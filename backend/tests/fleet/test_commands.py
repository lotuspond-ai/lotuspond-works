"""Admission is caller-transactional and current authority precedes replay."""

import importlib

import pytest
from lotuspond_runtime.errors import FleetError


def test_default_hooks_deny():
    commands = importlib.import_module("lotuspond.fleet.commands")
    with pytest.raises(FleetError):
        commands.DenyPinnedRecordSource().load(None, None)
    with pytest.raises(FleetError):
        commands.DenyBusinessDispatchAuthority().authorize(None, "a" * 64, None)
    with pytest.raises(FleetError):
        commands.UnavailableCommandTrustAuthority().current("test-installation")


def test_command_dependencies_mismatch_denies():
    commands = importlib.import_module("lotuspond.fleet.commands")
    from lotuspond.fleet.authority import FleetDependencies, UnavailableRecoveryAuthority
    from lotuspond.fleet.catalog import InstalledCatalog

    recovery = UnavailableRecoveryAuthority()
    with pytest.raises(FleetError):
        commands.CommandService(
            recovery,
            commands.DenyBusinessDispatchAuthority(),
            commands.DenyPinnedRecordSource(),
            commands.UnavailableCommandTrustAuthority(),
            dependencies=FleetDependencies(UnavailableRecoveryAuthority(), InstalledCatalog()),
        )


from tests.fleet.command_fixtures import admit, principal  # noqa: E402,F401


def test_caller_transaction_rolls_back_delivery(command_env):
    from sqlalchemy import func, select

    from lotuspond.models import RuntimeCommandAdmission

    c = command_env
    with c.env.db() as db:
        assert admit(db, c).status == "admitted"
        db.rollback()
    with c.env.db() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeCommandAdmission)) == 0


def test_replay_auth_precedes_lookup(command_env):
    from lotuspond.fleet.authentication import RuntimePrincipal

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
        old = principal(db, c)
        assert c.service.claim(db, old, first.command_id, first.command_sha256).status == "claimed"
    c.env.clock.now += 60000
    with c.env.db.begin() as db, pytest.raises(FleetError, match="unauthenticated"):
        stale = RuntimePrincipal(old.identity, old.credential_id, old.session_id)
        c.service.claim(db, stale, first.command_id, first.command_sha256)


def test_replay_requires_current_business_authority(command_env):
    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    c.dispatch.allowed = False
    with c.env.db.begin() as db, pytest.raises(FleetError, match="forbidden"):
        admit(db, c)
    with c.env.db.begin() as db, pytest.raises(FleetError, match="forbidden"):
        c.service.claim(db, principal(db, c), first.command_id, first.command_sha256)


@pytest.mark.parametrize(
    "field,value",
    [
        ("connection_id", "00000000-0000-0000-0000-000000000001"),
        ("connection_version", 2),
        ("actual_input", b"changed"),
        ("actual_target", b"/other"),
    ],
)
def test_actual_binding_matrix(command_env, field, value):
    from dataclasses import replace

    c = command_env
    c.source.load = lambda db, cmd: replace(c.pinned, **{field: value})
    with c.env.db.begin() as db, pytest.raises(FleetError):
        admit(db, c)


def test_changed_persisted_configuration_denied(command_env):
    from dataclasses import replace

    c = command_env
    changed = c.pinned.configuration.model_copy(
        update={
            "data_handling": c.pinned.configuration.data_handling.model_copy(
                update={"timeout_ms": 1}
            )
        }
    )
    c.source.load = lambda db, cmd: replace(c.pinned, configuration=changed)
    with c.env.db.begin() as db, pytest.raises(FleetError, match="command_rejected"):
        admit(db, c)


@pytest.mark.parametrize("waiter", ["records", "trust", "dispatch"])
@pytest.mark.parametrize("expired", ["credential", "session", "command", "epoch"])
def test_authority_and_actual_time_after_dependency_wait(command_env, waiter, expired):
    from sqlalchemy import select

    from lotuspond import models

    c = command_env

    def advance():
        if expired == "epoch":
            c.env.store["epoch"] = 8
        elif expired == "command":
            c.env.clock.now = c.case.command.expires_at_ms
        else:
            with c.env.db() as db:
                model = (
                    models.RuntimeCredential if expired == "credential" else models.RuntimeSession
                )
                row = db.scalar(
                    select(model).where(
                        model.instance_id == c.case.identity.instance_id, model.status == "active"
                    )
                )
                c.env.clock.now = row.expires_at_ms

    if waiter == "records":
        c.source.load = lambda db, cmd: (advance(), c.pinned)[1]
    else:
        getattr(c, waiter).callback = advance
    with c.env.db.begin() as db, pytest.raises(FleetError):
        admit(db, c)


def test_terminal_and_uncertain_never_reclaim(command_env):
    from sqlalchemy import select

    from lotuspond import models

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
        current = principal(db, c)
        c.service.claim(db, current, first.command_id, first.command_sha256)
        terminal = c.service.record_status(db, current, first.command_id, "terminal")
        assert terminal.status == "terminal"
        row = db.scalar(select(models.RuntimeCommandAdmission))
        assert row.retain_until_ms >= row.terminal_at_ms + 86400000
        assert row.retain_until_ms >= row.command_expires_at_ms + 30000
    with c.env.db.begin() as db, pytest.raises(FleetError, match="conflict"):
        c.service.claim(db, principal(db, c), first.command_id, first.command_sha256)


def test_production_fixture_selection_refused(settings, engine):
    from lotuspond.app import create_app
    from lotuspond.fleet.commands import (
        CommandDependencies,
        DenyBusinessDispatchAuthority,
        DenyPinnedRecordSource,
        UnavailableCommandTrustAuthority,
    )

    settings.env = "production"
    with pytest.raises(ValueError, match="test mode"):
        create_app(
            settings,
            engine=engine,
            command_dependencies=CommandDependencies(
                DenyBusinessDispatchAuthority(),
                DenyPinnedRecordSource(),
                UnavailableCommandTrustAuthority(),
            ),
        )


def test_no_public_sign_submit_dispatch_routes(command_env):
    c = command_env
    for suffix in ("submit", "sign", "dispatch", "invoke"):
        assert c.env.client.post("/api/v1/runtime/commands/" + suffix, json={}).status_code in (
            404,
            405,
        )


def test_signer_checks_provisioned_key_before_signing(tmp_path, monkeypatch):
    import time

    from cryptography.hazmat.primitives import serialization
    from lotuspond_runtime.protocol import CommandTrustSnapshot
    from lotuspond_sdk.trust import TrustSet

    from lotuspond.fleet import signing
    from tests.fleet.command_fixtures import helpers

    case = helpers.command_case(now=time.time_ns() // 1000000)
    key = tmp_path / "operator.pem"
    key.write_bytes(
        case.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key.chmod(0o600)
    calls = []
    real = signing.sign_runtime_command
    monkeypatch.setattr(
        signing, "sign_runtime_command", lambda *args: (calls.append(True), real(*args))[1]
    )
    signer = signing.CommandSigner(key)
    with pytest.raises(FleetError):
        signer.sign(
            case.command,
            CommandTrustSnapshot(1, TrustSet(format_version=1, keys=[]), case.now + 60000),
        )
    assert calls == []


@pytest.mark.parametrize("change", ["version", "binding", "revoked", "missing"])
def test_current_secret_reference_matrix(command_env, change):
    from dataclasses import replace

    from lotuspond_runtime.protocol import (
        ConnectionConfig,
        ConnectorPin,
        ResolvedSecretBinding,
        SecretPin,
    )
    from sqlalchemy import select

    from lotuspond import models
    from lotuspond.fleet.catalog import InstalledCatalog
    from tests.fleet.test_management import CONFIG, PIN, post

    c = command_env
    deps = replace(
        c.env.dependencies,
        catalog=InstalledCatalog.fixture(
            ConnectorPin(**PIN), test_mode=True, secret_bindings=frozenset({("fixture-binding", 1)})
        ),
    )
    c.env.app.state.fleet_dependencies = deps
    c.service.dependencies = deps
    response = post(
        c.env.client,
        f"{c.env.prefix}/secret-references",
        {"binding_id": "fixture-binding", "version": 1},
        "secret",
    )
    assert response.status_code == 201, response.text
    reference = response.json()["id"]
    config = ConnectionConfig.model_validate(
        {**CONFIG, "secrets": [{"reference_id": reference, "version": 1}]}
    )
    response = post(
        c.env.client,
        f"{c.env.prefix}/connections/{c.case.connection_id}/versions",
        config.model_dump(mode="json"),
        "secret-config",
    )
    assert response.status_code == 201, response.text
    pin = ResolvedSecretBinding(reference, 1, "fixture-binding")
    if change == "version":
        config = config.model_copy(
            update={"secrets": (SecretPin(reference_id=reference, version=2),)}
        )
        pin = ResolvedSecretBinding(reference, 2, "fixture-binding")
    elif change == "binding":
        pin = ResolvedSecretBinding(reference, 1, "other-binding")
    elif change == "revoked":
        with c.env.db.begin() as db:
            db.scalar(
                select(models.SecretReference).where(models.SecretReference.id == reference)
            ).status = "revoked"
    pins = replace(
        c.pinned,
        connection_version=2,
        configuration=config,
        secret_bindings=() if change == "missing" else (pin,),
    )
    c.source.load = lambda db, cmd: pins
    # Grant fixture recognizes its own exact records; current DB checks still deny.
    c.dispatch.authorize = lambda *args: None
    with c.env.db.begin() as db, pytest.raises(FleetError):
        admit(db, c)


def test_uncertain_external_send_is_not_reversed_by_revoke(command_env):
    from tests.fleet.test_management import post

    c = command_env
    sent = []  # Test-only effect sink, deliberately absent from product dependencies.
    with c.env.db.begin() as db:
        first = admit(db, c)
        current = principal(db, c)
        c.service.claim(db, current, first.command_id, first.command_sha256)
        sent.append(b"synthetic external send")
        assert (
            c.service.record_status(db, current, first.command_id, "uncertain").status
            == "uncertain"
        )
    response = post(
        c.env.client,
        f"{c.env.prefix}/runtime-instances/{c.case.identity.instance_id}/revoke",
        {},
        "revoke",
    )
    assert response.status_code == 200, response.text
    assert sent == [b"synthetic external send"]
    with c.env.db.begin() as db, pytest.raises(FleetError):
        c.service.claim(db, current, first.command_id, first.command_sha256)
    from sqlalchemy import select

    from lotuspond.models import RuntimeCommandAdmission

    with c.env.db() as db:
        row = db.scalar(select(RuntimeCommandAdmission))
        assert row.status == "uncertain"
        assert row.terminal_at_ms is None


def test_api_authentication_precedes_replay_results(command_env):
    from tests.fleet.identity_fixtures import auth

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    url = f"/api/v1/runtime/commands/{first.command_id}/claim"
    assert (
        c.env.client.post(
            url, headers=auth(c.credential), json={"command_sha256": first.command_sha256}
        ).status_code
        == 401
    )
    # Runtime scope is token-derived; an untrusted hint cannot select a tenant.
    # A principal whose actual scope was changed must fail before replay queries.
    from sqlalchemy import event

    from lotuspond.fleet.authentication import RuntimePrincipal

    with c.env.db.begin() as db:
        valid = principal(db, c)
    scope = valid.identity.scope.model_copy(
        update={"project_id": "00000000-0000-0000-0000-000000000001"}
    )
    wrong = RuntimePrincipal(
        valid.identity.model_copy(update={"scope": scope}), valid.credential_id, valid.session_id
    )
    statements = []

    def observe(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    engine = c.env.db.kw["bind"]
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with c.env.db.begin() as db, pytest.raises(FleetError):
            c.service.claim(db, wrong, first.command_id, first.command_sha256)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert not any("runtime_command_admissions" in statement for statement in statements)
    c.env.clock.now = c.session["expires_at_ms"]
    assert (
        c.env.client.post(
            url,
            headers=auth(c.credential, c.session["secret"]),
            json={"command_sha256": first.command_sha256},
        ).status_code
        == 401
    )


@pytest.mark.parametrize("changed", ["unavailable", "revoked"])
def test_current_trust_after_dispatch_wait(command_env, changed):
    c = command_env

    def withdraw():
        if changed == "unavailable":
            c.trust.available = False
        else:
            c.trust.keys = c.trust.keys.model_copy(
                update={"keys": [c.trust.keys.keys[0].model_copy(update={"state": "revoked"})]}
            )

    c.dispatch.callback = withdraw
    with c.env.db.begin() as db, pytest.raises(FleetError):
        admit(db, c)


@pytest.mark.parametrize(
    "field,value",
    [
        ("command_digest", "f" * 64),
        ("replay_id", "other-replay"),
        ("command_expires_at_ms", 9007199254740000),
    ],
)
def test_stored_admission_metadata_matches_verified_bytes(command_env, field, value):
    from sqlalchemy import select

    from lotuspond.models import RuntimeCommandAdmission

    c = command_env
    with c.env.db.begin() as db:
        admit(db, c)
    with c.env.db.begin() as db:
        row = db.scalar(select(RuntimeCommandAdmission))
        setattr(row, field, value)
    with c.env.db.begin() as db, pytest.raises(FleetError):
        c.service.poll(db, principal(db, c))


def test_unconfigured_command_service_denies_real_recipient(command_env):
    from lotuspond.fleet.commands import CommandService

    c = command_env
    service = CommandService(c.env.dependencies.recovery, c.dispatch, c.source, c.trust)
    value = c.helpers.delivery(c.case)
    with c.env.db.begin() as db, pytest.raises(FleetError):
        service.admit(db, c.case.identity, value.command_bytes, value.signature_bytes)


@pytest.mark.parametrize("removed", ["connection", "connector"])
def test_current_pool_policy_independently_revokes_admission(command_env, removed):
    from tests.fleet.test_management import POLICY, post

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    policy = {**POLICY, "connection_ids": [c.case.connection_id]}
    policy["connection_ids" if removed == "connection" else "connectors"] = []
    if removed == "connector":
        # Publication correctly refuses a connection whose connector is absent.
        # Withdraw its connection too; observation ceases to fit current policy.
        policy["connection_ids"] = []
    response = post(
        c.env.client, f"{c.env.prefix}/runtime-pools/{c.env.pool}/policies", policy, "withdraw"
    )
    assert response.status_code == 201, response.text
    with c.env.db.begin() as db, pytest.raises(FleetError):
        c.service.claim(db, principal(db, c), first.command_id, first.command_sha256)


def test_route_rejected_content_differs_from_unavailable_trust(command_env):
    import json

    from sqlalchemy import select

    from lotuspond.models import RuntimeCommandAdmission
    from tests.fleet.identity_fixtures import auth

    c = command_env
    with c.env.db.begin() as db:
        first = admit(db, c)
    path = f"/api/v1/runtime/commands/{first.command_id}/claim"
    headers = auth(c.credential, c.session["secret"])
    body = {"command_sha256": first.command_sha256}
    c.trust.available = False
    unavailable = c.env.client.post(path, headers=headers, json=body)
    assert unavailable.status_code == 503
    c.trust.available = True
    with c.env.db.begin() as db:
        row = db.scalar(select(RuntimeCommandAdmission))
        envelope = json.loads(row.signature_bytes)
        envelope["signature"] = "0" * 128
        row.signature_bytes = json.dumps(envelope).encode()
    rejected = c.env.client.post(path, headers=headers, json=body)
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "command_rejected"
    assert first.command_sha256 not in rejected.text + unavailable.text
