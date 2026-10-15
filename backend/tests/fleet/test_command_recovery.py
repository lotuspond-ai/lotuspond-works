import pytest
from lotuspond_runtime.errors import FleetError

from tests.fleet.command_fixtures import admit, principal  # noqa: F401


def test_restored_database_cannot_restore_authority(command_env):
    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
        old = principal(db, c)
    c.env.store["epoch"] = 8
    with c.env.db.begin() as db, pytest.raises(FleetError, match="authority_unavailable"):
        c.service.claim(db, old, first.command_id, first.command_sha256)


def test_lost_trust_and_offline_revocation_deny(command_env):
    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    c.trust.available = False
    with c.env.db.begin() as db, pytest.raises(FleetError):
        c.service.poll(db, principal(db, c))
    c.trust.available = True
    c.trust.keys = c.trust.keys.model_copy(
        update={"keys": [c.trust.keys.keys[0].model_copy(update={"state": "revoked"})]}
    )
    with c.env.db.begin() as db, pytest.raises(FleetError):
        c.service.claim(db, principal(db, c), first.command_id, first.command_sha256)


def test_restart_retains_replay_and_current_grants(command_env):
    from lotuspond.fleet.commands import CommandService

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
        current = principal(db, c)
        c.service.claim(db, current, first.command_id, first.command_sha256)
    restarted = CommandService(
        c.env.dependencies.recovery, c.dispatch, c.source, c.trust, dependencies=c.env.dependencies
    )
    with c.env.db.begin() as db, pytest.raises(FleetError, match="conflict"):
        restarted.claim(db, principal(db, c), first.command_id, first.command_sha256)
    # An independent grant fence survives the application-service restart.
    c.dispatch.allowed = False
    with c.env.db.begin() as db, pytest.raises(FleetError, match="forbidden"):
        restarted.claim(db, principal(db, c), first.command_id, first.command_sha256)


def test_current_key_transition_and_revocation(command_env):
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from lotuspond_sdk.trust import TrustSet

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    new = Ed25519PrivateKey.generate()
    public = new.public_key().public_bytes_raw()
    old = c.trust.keys.keys[0]
    replacement = dict(
        key_id=hashlib.sha256(public).hexdigest(), public_key=public.hex(), state="allowed"
    )
    overlap_started = c.env.clock.now
    c.trust.revision = 2
    c.trust.keys = TrustSet(format_version=1, keys=[old, replacement])
    # Current revision2 explicitly retains original revision1 issuance proof.
    with c.env.db.begin() as db:
        assert c.service.poll(db, principal(db, c)).trust_revision == 1
        assert admit(db, c) == first
    c.env.clock.now += 1  # Deterministic oldest-first queue order.
    c.case.key = new
    c.case.command = c.case.command.model_copy(
        update={"command_id": "new-command", "replay_id": "new-replay"}
    )
    with c.env.db.begin() as db:
        assert admit(db, c).status == "admitted"
    c.env.clock.now += 19999
    assert 0 < c.env.clock.now - overlap_started <= 300000
    # Explicit provider overlap is 20000ms; retire the old key now.
    c.trust.revision = 3
    c.trust.keys = TrustSet(
        format_version=1, keys=[old.model_copy(update={"state": "revoked"}), replacement]
    )
    with c.env.db.begin() as db, pytest.raises(FleetError):
        c.service.claim(db, principal(db, c), first.command_id, first.command_sha256)
    with c.env.db.begin() as db, pytest.raises(FleetError):
        c.service.poll(db, principal(db, c))
    # A currently allowed new-key record remains claimable by its exact ID.
    with c.env.db.begin() as db:
        from lotuspond_sdk.executable.wire import wire_digest

        assert (
            c.service.claim(db, principal(db, c), "new-command", wire_digest(c.case.command)).status
            == "claimed"
        )


def test_api_restart_with_restored_state_fails_current_epoch(command_env):
    from fastapi.testclient import TestClient

    from lotuspond.app import create_app
    from lotuspond.fleet.commands import CommandDependencies
    from tests.fleet.identity_fixtures import auth, enrollment

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    issued = enrollment(c.env)
    assert issued.status_code == 201, issued.text
    # Reopen the same persisted application state. Only the independent authority
    # changes; a database restore cannot roll it back with credential/session rows.
    c.env.store["epoch"] = 8
    restarted = create_app(
        c.env.app.state.settings,
        engine=c.env.db.kw["bind"],
        fleet_dependencies=c.env.dependencies,
        command_dependencies=CommandDependencies(c.dispatch, c.source, c.trust),
    )
    with TestClient(restarted) as client:
        headers = auth(c.credential, c.session["secret"])
        for path, body in [
            ("/commands/poll", {}),
            ("/session", {}),
            (f"/commands/{first.command_id}/claim", {"command_sha256": first.command_sha256}),
        ]:
            assert (
                client.post("/api/v1/runtime" + path, headers=headers, json=body).status_code == 503
            )
        assert (
            client.post(
                "/api/v1/runtime/enroll",
                headers={"Authorization": "Bearer " + issued.json()["token"]},
                json={"platform": "linux", "connectors": []},
            ).status_code
            == 503
        )


def test_database_disconnect_never_uses_cached_delivery(command_env):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    from tests.fleet.identity_fixtures import auth

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    engine = c.env.db.kw["bind"]

    def disconnect(connection, cursor, statement, parameters, context, many):
        if "runtime_command_admissions" in statement:
            raise OperationalError("synthetic disconnect", None, Exception("unavailable"))

    event.listen(engine, "before_cursor_execute", disconnect)
    try:
        response = c.env.client.post(
            "/api/v1/runtime/commands/poll",
            headers=auth(c.credential, c.session["secret"]),
            json={},
        )
        assert response.status_code == 503
        assert first.command_sha256 not in response.text
    finally:
        event.remove(engine, "before_cursor_execute", disconnect)
    # No delivery cached across the failed connection or later grant withdrawal.
    c.dispatch.allowed = False
    with c.env.db.begin() as db, pytest.raises(FleetError, match="forbidden"):
        c.service.claim(db, principal(db, c), first.command_id, first.command_sha256)


def test_fresh_epoch_and_default_dispatch_do_not_restore_grants(command_env):
    from lotuspond.fleet.commands import CommandService, DenyBusinessDispatchAuthority

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    # Even with matching independent epoch again, missing future grants deny.
    c.env.store["active"] = False
    with c.env.db.begin() as db, pytest.raises(FleetError):
        c.service.poll(db, principal(db, c))
    c.env.store["active"] = True
    denied = CommandService(
        c.env.dependencies.recovery,
        DenyBusinessDispatchAuthority(),
        c.source,
        c.trust,
        dependencies=c.env.dependencies,
    )
    with c.env.db.begin() as db, pytest.raises(FleetError, match="forbidden"):
        denied.claim(db, principal(db, c), first.command_id, first.command_sha256)


def test_advanced_epoch_with_new_identity_still_requires_business_grants(command_env):
    from lotuspond_runtime.protocol import RuntimeIdentity

    from lotuspond.fleet.commands import CommandService, DenyBusinessDispatchAuthority
    from tests.fleet.identity_fixtures import acquire, enrolled
    from tests.fleet.test_management import post

    c = command_env
    response = post(
        c.env.client,
        f"{c.env.prefix}/runtime-instances/{c.case.identity.instance_id}/revoke",
        {},
        "revoke-old",
    )
    assert response.status_code == 200, response.text
    c.env.store["epoch"] = 8
    credential = enrolled(c.env)
    session = acquire(c.env, credential)
    assert session.status_code == 201, session.text
    identity = RuntimeIdentity.model_validate(session.json()["identity"])
    assert identity.recovery_epoch == 8
    case = c.helpers.command_case(
        identity=identity, now=c.env.clock.now, connection_id=c.case.connection_id
    )
    pins = c.helpers.records(case)

    class Source:
        def load(self, db, command):
            assert command == case.command
            return pins

    class Trust:
        def current(self, installation_id):
            return c.helpers.snapshot(case)

    denied = CommandService(
        c.env.dependencies.recovery,
        DenyBusinessDispatchAuthority(),
        Source(),
        Trust(),
        dependencies=c.env.dependencies,
    )
    delivery = c.helpers.delivery(case)
    with c.env.db.begin() as db, pytest.raises(FleetError, match="forbidden"):
        denied.admit(db, identity, delivery.command_bytes, delivery.signature_bytes)
