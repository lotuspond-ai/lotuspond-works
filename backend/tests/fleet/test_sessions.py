"""A copied credential cannot preempt a live process lease."""

import pytest
from sqlalchemy import select

from lotuspond import authority_gate, models
from tests.fleet.identity_fixtures import acquire, auth, enrolled, race_runtime


def test_copied_credential_cannot_own_live_session(identity_env):
    env = identity_env
    credential = enrolled(env)
    acquired = acquire(env, credential)
    assert acquired.status_code == 201, acquired.text
    session = acquired.json()
    assert session["expires_at_ms"] - session["server_time_ms"] == 60000
    assert acquire(env, credential).status_code == 409
    assert (
        env.client.post("/api/v1/runtime/heartbeat", headers=auth(credential), json={}).status_code
        == 401
    )
    assert (
        env.client.post(
            "/api/v1/runtime/heartbeat", headers=auth(credential, session["secret"]), json={}
        ).status_code
        == 200
    )


def test_lost_acquisition_waits_expiry(identity_env):
    env = identity_env
    credential = enrolled(env)
    original = acquire(env, credential).json()
    assert acquire(env, credential).status_code == 409
    env.clock.now = original["expires_at_ms"]
    takeover = acquire(env, credential)
    assert takeover.status_code == 201, takeover.text
    assert takeover.json()["identity"]["generation"] == original["identity"]["generation"] + 1
    assert (
        env.client.post(
            "/api/v1/runtime/heartbeat", headers=auth(credential, original["secret"]), json={}
        ).status_code
        == 401
    )
    with env.db() as db:
        assert sorted(db.scalars(select(models.RuntimeSession.status))) == ["active", "revoked"]


def test_heartbeat_half_open_and_fixed_extension(identity_env):
    env = identity_env
    credential = enrolled(env)
    session = acquire(env, credential).json()
    env.clock.now += 20000
    state = env.client.post(
        "/api/v1/runtime/heartbeat", headers=auth(credential, session["secret"]), json={}
    )
    assert state.status_code == 200, state.text
    assert state.json()["session_expires_at_ms"] == env.clock.now + 60000
    env.clock.now += 60000
    assert (
        env.client.post(
            "/api/v1/runtime/heartbeat", headers=auth(credential, session["secret"]), json={}
        ).status_code
        == 401
    )


def test_session_single_winner_after_expiry(identity_env, engine):

    import pytest

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL session qualification required")
    env = identity_env
    credential = enrolled(env)
    original = acquire(env, credential).json()
    env.clock.now = original["expires_at_ms"]
    results = race_runtime(env, "/api/v1/runtime/session", [auth(credential)] * 2, [{}, {}])
    assert sorted(r.status_code for r in results) == [201, 409]
    acquired = next(r for r in results if r.status_code == 201).json()
    assert acquired["identity"]["generation"] == original["identity"]["generation"] + 1


def test_current_parent_and_observation_changes(identity_env):
    from tests.fleet.test_management import POLICY, post

    env = identity_env
    credential = enrolled(env)
    assert (
        post(
            env.client,
            f"{env.prefix}/runtime-pools/{env.pool}/policies",
            dict(POLICY, connectors=[]),
            "narrow",
        ).status_code
        == 201
    )
    assert env.client.get("/api/v1/runtime/self", headers=auth(credential)).status_code == 401


def test_enrolling_membership_removal_does_not_revoke_runtime(identity_env):
    env = identity_env
    credential = enrolled(env)
    with env.db.begin() as db:
        db.delete(
            db.scalar(
                select(models.Membership).where(models.Membership.workspace_id == env.workspace)
            )
        )
    assert env.client.get("/api/v1/runtime/self", headers=auth(credential)).status_code == 200


def test_takeover_invalidates_pending_generation(identity_env):
    from lotuspond.fleet.authentication import machine_fields

    env = identity_env
    credential = enrolled(env)
    first = acquire(env, credential).json()
    with env.db.begin() as db:
        active = db.scalar(select(models.RuntimeCredential))
        session = db.scalar(select(models.RuntimeSession))
        pending = models.RuntimeCredential(
            **machine_fields(active),
            instance_id=active.instance_id,
            version=2,
            token_digest="f" * 64,
            status="pending",
            expires_at_ms=env.clock.now + 900000,
            predecessor_id=active.id,
            session_id=session.id,
            generation=session.generation,
            prepared_policy_version=1,
            activation_deadline_ms=env.clock.now + 60000,
        )
        db.add(pending)
    env.clock.now = first["expires_at_ms"]
    assert acquire(env, credential).status_code == 201
    with env.db() as db:
        assert (
            db.scalar(
                select(models.RuntimeCredential).where(models.RuntimeCredential.version == 2)
            ).status
            == "revoked"
        )


def test_active_credential_historical_session_metadata_does_not_block_recovery(identity_env):
    env = identity_env
    credential = enrolled(env)
    session = acquire(env, credential).json()
    with env.db.begin() as db:
        active = db.scalar(select(models.RuntimeCredential))
        historical = db.scalar(select(models.RuntimeSession))
        active.session_id, active.generation = historical.id, historical.generation
    env.clock.now = session["expires_at_ms"]
    assert env.client.get("/api/v1/runtime/self", headers=auth(credential)).status_code == 200
    assert acquire(env, credential).status_code == 201


@pytest.mark.parametrize(
    "target", ["workspace", "pool", "instance", "credential", "session", "policy"]
)
def test_preloaded_mutable_runtime_authority_refreshes(identity_env, engine, target):
    from lotuspond_runtime.errors import FleetError

    from lotuspond.fleet.authentication import authenticate_runtime
    from tests.fleet.test_management import POLICY, post

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL authority refresh qualification required")
    env = identity_env
    credential = enrolled(env)
    acquired = acquire(env, credential).json()
    identifier = credential["state"]["identity"]["instance_id"]
    with env.db() as caller:
        if target == "policy":
            cached = caller.get(models.RuntimePool, env.pool)
            assert cached.current_policy_version == 1
            assert (
                post(
                    env.client,
                    f"{env.prefix}/runtime-pools/{env.pool}/policies",
                    dict(POLICY, connectors=[]),
                    "narrow",
                ).status_code
                == 201
            )
            assert cached.current_policy_version == 1
        else:
            model, key = {
                "workspace": (models.Workspace, env.workspace),
                "pool": (models.RuntimePool, env.pool),
                "instance": (models.RuntimeInstance, identifier),
                "credential": (models.RuntimeCredential, None),
                "session": (models.RuntimeSession, None),
            }[target]
            cached = caller.get(model, key) if key else caller.scalar(select(model))
            assert cached.status == "active"
            with env.db.begin() as other:
                other.get(model, cached.id).status = (
                    "suspended" if target == "workspace" else "revoked"
                )
            assert cached.status == "active"
        with pytest.raises(FleetError, match="unauthenticated"):
            authenticate_runtime(
                caller,
                credential["token"],
                session_secret=acquired["secret"],
                require_session=True,
                dependencies=env.dependencies,
            )
        assert (
            cached.current_policy_version == 2 if target == "policy" else cached.status != "active"
        )


@pytest.mark.parametrize("expiry", ["credential", "session"])
def test_expiry_is_rechecked_after_blocked_lock(identity_env, engine, expiry):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient
    from sqlalchemy import event

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL expiry-after-wait qualification required")
    env = identity_env
    credential = enrolled(env)
    session = acquire(env, credential).json()
    reached = Event()

    def before_cursor(connection, cursor, statement, parameters, context, many):
        if "pg_advisory_xact_lock" in statement:  # its first lock: the fair gate
            reached.set()

    def request():
        return TestClient(env.app).post(
            "/api/v1/runtime/heartbeat", json={}, headers=auth(credential, session["secret"])
        )

    with ThreadPoolExecutor(max_workers=1) as workers:
        with env.db.begin() as holder:
            # An authority change holds the parent: its gate first, exclusive.
            authority_gate.workspace(holder, env.workspace, exclusive=True)
            holder.scalar(
                select(models.Workspace)
                .where(models.Workspace.id == env.workspace)
                .with_for_update()
            )
            event.listen(engine, "before_cursor_execute", before_cursor)
            try:
                future = workers.submit(request)
                assert reached.wait(5), "Request did not reach the held lock"
                env.clock.now = (
                    credential["state"]["credential_expires_at_ms"]
                    if expiry == "credential"
                    else session["expires_at_ms"]
                )
            finally:
                event.remove(engine, "before_cursor_execute", before_cursor)
        assert future.result(timeout=10).status_code == 401


def test_heartbeat_and_session_rates(identity_env):
    env = identity_env
    credential = enrolled(env)
    session = acquire(env, credential).json()
    for _ in range(9):
        assert acquire(env, credential).status_code == 409
    assert acquire(env, credential).status_code == 429
    for _ in range(10):
        assert (
            env.client.post(
                "/api/v1/runtime/heartbeat", json={}, headers=auth(credential, session["secret"])
            ).status_code
            == 200
        )
    assert (
        env.client.post(
            "/api/v1/runtime/heartbeat", json={}, headers=auth(credential, session["secret"])
        ).status_code
        == 429
    )


@pytest.mark.parametrize("operation", ["session", "heartbeat"])
def test_expiry_is_rechecked_after_quota_wait(identity_env, operation):
    from dataclasses import replace

    env = identity_env
    credential = enrolled(env)
    session = acquire(env, credential).json() if operation == "heartbeat" else None

    class DelayedLimiter:
        def check(self, *args):
            env.clock.now = (
                session["expires_at_ms"]
                if session
                else credential["state"]["credential_expires_at_ms"]
            )

    env.app.state.fleet_dependencies = replace(env.dependencies, limiter=DelayedLimiter())
    response = env.client.post(
        f"/api/v1/runtime/{operation}",
        json={},
        headers=auth(credential, session["secret"] if session else None),
    )
    assert response.status_code == 401


@pytest.mark.parametrize("operation", ["self", "session", "heartbeat"])
def test_runtime_boundary_locks_sessions_before_credentials(identity_env, engine, operation):
    from sqlalchemy import event

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL shared lock-order qualification required")
    env = identity_env
    credential = enrolled(env)
    session = acquire(env, credential).json() if operation == "heartbeat" else None
    ordered = []

    def before_cursor(connection, cursor, statement, parameters, context, many):
        if "FOR UPDATE" in statement:
            for name in ("runtime_instances", "runtime_sessions", "runtime_credentials"):
                if f"FROM {name} " in statement or f"FROM {name}\n" in statement:
                    ordered.append(name)

    event.listen(engine, "before_cursor_execute", before_cursor)
    try:
        headers = auth(credential, session["secret"] if session else None)
        response = (
            env.client.get("/api/v1/runtime/self", headers=headers)
            if operation == "self"
            else env.client.post(f"/api/v1/runtime/{operation}", json={}, headers=headers)
        )
        assert response.status_code in {200, 201}
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor)
    assert (
        ordered.index("runtime_instances")
        < ordered.index("runtime_sessions")
        < ordered.index("runtime_credentials")
    )


@pytest.mark.parametrize("after_wait", ["expired", "valid", "epoch_changed"])
def test_takeover_rechecks_authority_after_pending_credential_wait(
    identity_env, engine, after_wait
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from lotuspond.fleet.authentication import machine_fields, new_token, token_digest

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL pending-credential wait qualification required")
    env = identity_env
    credential = enrolled(env)
    original = acquire(env, credential).json()
    with env.db.begin() as db:
        active = db.scalar(select(models.RuntimeCredential))
        process = db.scalar(select(models.RuntimeSession))
        pending = models.RuntimeCredential(
            **machine_fields(active),
            instance_id=active.instance_id,
            version=2,
            token_digest=token_digest(new_token("lpr_"), "lpr_"),
            status="pending",
            expires_at_ms=env.clock.now + 900000,
            predecessor_id=active.id,
            session_id=process.id,
            generation=process.generation,
            prepared_policy_version=1,
            activation_deadline_ms=env.clock.now + 60000,
        )
        db.add(pending)
        db.flush()
        pending_id, process_id = pending.id, process.id
        instance_id, active_id = active.instance_id, active.id
        initial_seen = db.get(models.RuntimeInstance, instance_id).last_seen_at_ms
    env.clock.now = original["expires_at_ms"]
    reached = Event()

    def before_cursor(connection, cursor, statement, parameters, context, many):
        if (
            "FROM runtime_credentials" in statement
            and "FOR UPDATE" in statement
            and isinstance(parameters, dict)
            and "pending" in parameters.values()
        ):
            reached.set()

    def request():
        return TestClient(env.app).post(
            "/api/v1/runtime/session", json={}, headers=auth(credential)
        )

    with ThreadPoolExecutor(max_workers=1) as workers:
        with env.db.begin() as holder:
            holder.scalar(
                select(models.RuntimeCredential)
                .where(models.RuntimeCredential.id == pending_id)
                .with_for_update()
            )
            event.listen(engine, "before_cursor_execute", before_cursor)
            try:
                future = workers.submit(request)
                assert reached.wait(5), "Takeover never reached its final credential lock"
                if after_wait == "expired":
                    env.clock.now = credential["state"]["credential_expires_at_ms"]
                else:
                    env.clock.now += 5000
                    if after_wait == "epoch_changed":
                        env.store["epoch"] = 8
            finally:
                event.remove(engine, "before_cursor_execute", before_cursor)
        response = future.result(timeout=10)
    assert response.status_code == {"expired": 401, "valid": 201, "epoch_changed": 503}[after_wait]
    with env.db() as db:
        instance = db.get(models.RuntimeInstance, instance_id)
        assert db.get(models.RuntimeCredential, active_id).status == "active"
        if after_wait == "valid":
            state = response.json()
            assert state["server_time_ms"] == env.clock.now
            assert state["expires_at_ms"] == env.clock.now + 60000
            assert instance.generation == original["identity"]["generation"] + 1
            assert db.get(models.RuntimeSession, process_id).status == "revoked"
            assert db.get(models.RuntimeCredential, pending_id).status == "revoked"
            assert len(list(db.scalars(select(models.RuntimeSession)))) == 2
        else:
            assert instance.generation == original["identity"]["generation"]
            assert instance.last_seen_at_ms == initial_seen
            assert db.get(models.RuntimeSession, process_id).status == "active"
            assert db.get(models.RuntimeCredential, pending_id).status == "pending"
            assert len(list(db.scalars(select(models.RuntimeSession)))) == 1
