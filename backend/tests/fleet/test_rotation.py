"""Two-phase rotation state table, response loss, and runtime boundary."""

from dataclasses import replace

import pytest
from sqlalchemy import select

from lotuspond import models
from lotuspond.fleet.authentication import new_token
from tests.fleet.identity_fixtures import acquire, auth, enrolled
from tests.fleet.test_management import POLICY, post

ROOT = "/api/v1/runtime/credentials"


def request(env, operation, credential, secret=None):
    return env.client.post(f"{ROOT}/{operation}", headers=auth(credential, secret), json={})


def ready(env):
    credential = enrolled(env)
    session = acquire(env, credential).json()
    return credential, session["secret"]


def prepare(env, credential, secret):
    response = request(env, "prepare", credential, secret)
    assert response.status_code == 201, response.text
    assert "no-store" in response.headers["cache-control"].split(", ")
    return response.json()


def rows(env):
    with env.db() as db:
        return [
            (r.version, r.status, r.expires_at_ms, r.successor_id)
            for r in db.scalars(
                select(models.RuntimeCredential).order_by(models.RuntimeCredential.version)
            )
        ]


def test_prepare_activate_exact_lifetimes_digest_only(identity_env):
    env = identity_env
    credential, secret = ready(env)
    pending = prepare(env, credential, secret)
    assert pending["activation_deadline_ms"] - pending["server_time_ms"] == 60000
    assert rows(env)[0][2] == credential["state"]["credential_expires_at_ms"]
    with env.db() as db:
        candidate = db.scalar(
            select(models.RuntimeCredential).where(models.RuntimeCredential.version == 2)
        )
        assert pending["token"] not in repr(candidate.__dict__)
        assert len(candidate.token_digest) == 64
        assert candidate.session_id == db.scalar(select(models.RuntimeSession.id))
        assert candidate.generation == 1 and candidate.prepared_policy_version == 1
    env.clock.now += 1000
    activated = request(env, "activate", pending, secret)
    assert activated.status_code == 200, activated.text
    assert activated.json()["credential_expires_at_ms"] == env.clock.now + 900000
    assert activated.json()["identity"]["generation"] == 1
    assert [r[1] for r in rows(env)] == ["revoked", "active"]
    assert rows(env)[0][3] is None
    assert env.client.get("/api/v1/runtime/self", headers=auth(credential)).status_code == 401
    assert (
        env.client.post(
            "/api/v1/runtime/heartbeat", headers=auth(pending, secret), json={}
        ).status_code
        == 200
    )


def test_active_p_retry_preserves_q_and_expiry(identity_env):
    env = identity_env
    credential, secret = ready(env)
    pending = prepare(env, credential, secret)
    activated = request(env, "activate", pending, secret).json()
    q = prepare(env, pending, secret)
    before = rows(env)
    env.clock.now += 60000  # Process memory and the old lease are now gone.
    for _ in range(3):
        retry = request(env, "activate", pending)
        assert retry.status_code == 200, retry.text
        assert retry.json()["credential_expires_at_ms"] == activated["credential_expires_at_ms"]
        assert retry.json()["session_expires_at_ms"] is None
        assert rows(env) == before
    with env.db() as db:
        assert len(list(db.scalars(select(models.RuntimeSession)))) == 1
        assert (
            db.scalar(
                select(models.RuntimeCredential.version).where(
                    models.RuntimeCredential.status == "pending"
                )
            )
            == q["credential_version"]
        )


def test_prepare_response_loss_replacement_and_cancel_versions(identity_env):
    env = identity_env
    credential, secret = ready(env)
    p = prepare(env, credential, secret)
    replacement = prepare(env, credential, secret)
    assert replacement["credential_version"] == p["credential_version"] + 1
    assert replacement["token"] != p["token"]
    assert request(env, "activate", p, secret).status_code == 401
    assert request(env, "cancel", credential, secret).status_code == 200
    assert request(env, "cancel", credential, secret).status_code == 200
    assert request(env, "activate", replacement, secret).status_code == 401
    next_p = prepare(env, credential, secret)
    assert next_p["credential_version"] == replacement["credential_version"] + 1
    assert rows(env)[0][2] == credential["state"]["credential_expires_at_ms"]


@pytest.mark.parametrize("operation", ["prepare", "cancel"])
@pytest.mark.parametrize("proof", [None, "wrong", "expired", "generation"])
def test_predecessor_mutations_need_current_session(identity_env, operation, proof):
    env = identity_env
    credential, secret = ready(env)
    prepare(env, credential, secret)
    if proof == "wrong":
        secret = new_token("lps_")
    elif proof is None:
        secret = None
    elif proof == "expired":
        env.clock.now += 60000
    else:
        with env.db.begin() as db:
            db.scalar(select(models.RuntimeInstance)).generation += 1
    before = rows(env)
    assert request(env, operation, credential, secret).status_code == 401
    assert rows(env) == before


@pytest.mark.parametrize(
    "change",
    [
        "deadline",
        "c_expiry",
        "c_revoked",
        "p_revoked",
        "p_expiry",
        "session_expiry",
        "session_revoked",
        "generation",
        "pool",
        "workspace",
        "instance",
        "epoch",
        "policy",
        "missing_secret",
        "wrong_secret",
    ],
)
def test_pending_activation_requires_current_predecessor_registration_and_session(
    identity_env, change
):
    env = identity_env
    credential, secret = ready(env)
    session_deadline = env.clock.now + 60000
    if change == "session_expiry":
        env.clock.now += 1000  # Session expires before the later preparation deadline.
    pending = prepare(env, credential, secret)
    if change == "session_expiry":
        env.clock.now = session_deadline
        assert env.clock.now < pending["activation_deadline_ms"]
    elif change == "deadline":
        env.clock.now += 20000
        assert (
            env.client.post(
                "/api/v1/runtime/heartbeat", headers=auth(credential, secret), json={}
            ).status_code
            == 200
        )
        env.clock.now = pending["activation_deadline_ms"]  # Session is still live.
    elif change == "p_expiry":
        with env.db.begin() as db:
            db.scalar(
                select(models.RuntimeCredential).where(models.RuntimeCredential.version == 2)
            ).expires_at_ms = env.clock.now
    elif change == "c_expiry":
        with env.db.begin() as db:
            db.scalar(
                select(models.RuntimeCredential).where(models.RuntimeCredential.version == 1)
            ).expires_at_ms = env.clock.now
    elif change == "epoch":
        env.store["epoch"] = 8
    elif change == "policy":
        assert (
            post(
                env.client, f"{env.prefix}/runtime-pools/{env.pool}/policies", POLICY, "same-policy"
            ).status_code
            == 201
        )
    elif change == "missing_secret":
        secret = None
    elif change == "wrong_secret":
        secret = new_token("lps_")
    else:
        with env.db.begin() as db:
            if change in {"c_revoked", "p_revoked"}:
                db.scalar(
                    select(models.RuntimeCredential).where(
                        models.RuntimeCredential.version == (1 if change == "c_revoked" else 2)
                    )
                ).status = "revoked"
            elif change == "session_revoked":
                db.scalar(select(models.RuntimeSession)).status = "revoked"
            elif change == "generation":
                db.scalar(select(models.RuntimeInstance)).generation += 1
            elif change == "pool":
                db.get(models.RuntimePool, env.pool).status = "disabled"
            elif change == "workspace":
                db.get(models.Workspace, env.workspace).status = "suspended"
            else:
                db.scalar(select(models.RuntimeInstance)).status = "revoked"
    before = rows(env)
    assert request(env, "activate", pending, secret).status_code == (
        503 if change == "epoch" else 401
    )
    assert rows(env) == before


@pytest.mark.parametrize(
    "path", ["/self", "/heartbeat", "/session", "/credentials/prepare", "/credentials/cancel"]
)
def test_pending_token_has_activation_authority_only(identity_env, path):
    env = identity_env
    credential, secret = ready(env)
    pending = prepare(env, credential, secret)
    headers = auth(pending, secret)
    response = (
        env.client.get("/api/v1/runtime" + path, headers=headers)
        if path == "/self"
        else env.client.post("/api/v1/runtime" + path, headers=headers, json={})
    )
    assert response.status_code == 401


def test_takeover_invalidates_pending_and_preserves_monotonic_version(identity_env):
    env = identity_env
    credential, secret = ready(env)
    pending = prepare(env, credential, secret)
    env.clock.now += 60000
    new_session = acquire(env, credential).json()
    assert request(env, "activate", pending, secret).status_code == 401
    later = prepare(env, credential, new_session["secret"])
    assert later["credential_version"] == 3


@pytest.mark.parametrize("first", ["cancel", "activate"])
def test_cancel_activation_race_never_revokes_active_p(identity_env, first):
    env = identity_env
    credential, secret = ready(env)
    p = prepare(env, credential, secret)
    if first == "cancel":
        assert request(env, "cancel", credential, secret).status_code == 200
        assert request(env, "activate", p, secret).status_code == 401
        assert [r[1] for r in rows(env)] == ["active", "revoked"]
    else:
        assert request(env, "activate", p, secret).status_code == 200
        assert request(env, "cancel", credential, secret).status_code == 401
        assert [r[1] for r in rows(env)] == ["revoked", "active"]


def test_rotation_rate_is_one_shared_instance_bucket(identity_env):
    env = identity_env
    credential, secret = ready(env)
    p = prepare(env, credential, secret)  # 1
    assert request(env, "cancel", credential, secret).status_code == 200  # 2
    p = prepare(env, credential, secret)  # 3
    assert request(env, "activate", p, secret).status_code == 200  # 4
    for _ in range(6):
        assert request(env, "activate", p).status_code == 200
    for operation in ("prepare", "activate", "cancel"):
        assert request(env, operation, p, secret).status_code == 429


@pytest.mark.parametrize("operation", ["prepare", "activate", "cancel"])
def test_rotation_rechecks_after_quota_wait(identity_env, operation):
    env = identity_env
    credential, secret = ready(env)
    p = prepare(env, credential, secret)

    class Limiter:
        def check(self, key, limit):
            if key.startswith("fleet:rotation:"):
                env.clock.now += 60000

    env.app.state.fleet_dependencies = replace(env.dependencies, limiter=Limiter())
    before = rows(env)
    assert (
        request(env, operation, p if operation == "activate" else credential, secret).status_code
        == 401
    )
    assert rows(env) == before


def test_instance_revoke_invalidates_every_live_rotation_row(identity_env):
    env = identity_env
    credential, secret = ready(env)
    p = prepare(env, credential, secret)
    identifier = credential["state"]["identity"]["instance_id"]
    assert (
        post(
            env.client, f"{env.prefix}/runtime-instances/{identifier}/revoke", {}, "revoke"
        ).status_code
        == 200
    )
    assert [r[1] for r in rows(env)] == ["revoked", "revoked"]
    with env.db() as db:
        assert db.scalar(select(models.RuntimeSession.status)) == "revoked"
    assert request(env, "activate", p, secret).status_code == 401


def test_initial_credential_is_not_an_activation_retry(identity_env):
    env = identity_env
    credential, secret = ready(env)
    assert request(env, "activate", credential).status_code == 401


@pytest.mark.parametrize("operation", ["prepare", "activate", "cancel"])
@pytest.mark.parametrize("failure", ["audit", "storage"])
def test_rotation_storage_failure_rolls_back_all_authority(
    identity_env, monkeypatch, operation, failure
):
    from sqlalchemy.exc import OperationalError

    from lotuspond.fleet import rotation

    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    before = rows(env)
    with env.db() as db:
        before_audits = len(list(db.scalars(select(models.AuditEvent))))
    if failure == "audit":

        def fail(*args, **kwargs):
            raise OperationalError(None, None, Exception("synthetic private storage failure"))

        monkeypatch.setattr(rotation, "fleet_audit", fail)
    else:
        from sqlalchemy import event

        def fail(connection, cursor, statement, parameters, context, many):
            if statement.startswith("UPDATE runtime_credentials"):
                raise OperationalError(None, None, Exception("synthetic private storage failure"))

        event.listen(env.db.kw["bind"], "before_cursor_execute", fail)
    try:
        response = request(env, operation, p if operation == "activate" else c, secret)
        assert response.status_code == 503
        assert "synthetic private" not in response.text
    finally:
        if failure == "storage":
            event.remove(env.db.kw["bind"], "before_cursor_execute", fail)
    assert rows(env) == before
    with env.db() as db:
        assert len(list(db.scalars(select(models.AuditEvent)))) == before_audits


def test_rotation_audits_are_safe_and_metadata_retry_is_read_only(identity_env):
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    assert request(env, "cancel", c, secret).status_code == 200
    p = prepare(env, c, secret)
    assert request(env, "activate", p, secret).status_code == 200
    with env.db() as db:
        audits = list(
            db.scalars(
                select(models.AuditEvent).where(models.AuditEvent.action.like("fleet.credential.%"))
            )
        )
        assert sorted(a.action for a in audits) == [
            "fleet.credential.activated",
            "fleet.credential.cancelled",
            "fleet.credential.prepared",
            "fleet.credential.prepared",
        ]
        for audit in audits:
            assert set(audit.details) == {"project_id", "environment_id", "revision"}
            assert audit.actor_id == audit.resource_id == c["state"]["identity"]["instance_id"]
            assert p["token"] not in repr(audit.__dict__) and secret not in repr(audit.__dict__)
        before = len(list(db.scalars(select(models.AuditEvent))))
    assert request(env, "activate", p).status_code == 200
    assert request(env, "cancel", p, secret).status_code == 200  # no pending candidate
    with env.db() as db:
        assert len(list(db.scalars(select(models.AuditEvent)))) == before


@pytest.mark.parametrize("change", ["expired", "revoked", "instance", "pool", "workspace", "epoch"])
def test_active_p_metadata_retry_still_checks_current_authority(identity_env, change):
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    state = request(env, "activate", p, secret).json()
    if change == "expired":
        env.clock.now = state["credential_expires_at_ms"]
    elif change == "epoch":
        env.store["epoch"] = 8
    else:
        with env.db.begin() as db:
            if change == "revoked":
                db.scalar(
                    select(models.RuntimeCredential).where(models.RuntimeCredential.version == 2)
                ).status = "revoked"
            elif change == "workspace":
                db.get(models.Workspace, env.workspace).status = "suspended"
            elif change == "pool":
                db.get(models.RuntimePool, env.pool).status = "disabled"
            else:
                db.scalar(select(models.RuntimeInstance)).status = "revoked"
    before = rows(env)
    assert request(env, "activate", p).status_code == (503 if change == "epoch" else 401)
    assert rows(env) == before


def test_active_retry_does_not_rewrite_policy_history(identity_env):
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    initial = request(env, "activate", p, secret).json()
    assert (
        post(
            env.client, f"{env.prefix}/runtime-pools/{env.pool}/policies", POLICY, "equivalent"
        ).status_code
        == 201
    )
    assert request(env, "activate", p).json() == initial
    with env.db() as db:
        assert (
            db.scalar(
                select(models.RuntimeCredential.prepared_policy_version).where(
                    models.RuntimeCredential.version == 2
                )
            )
            == 1
        )


@pytest.mark.parametrize("operation", ["prepare", "cancel"])
@pytest.mark.parametrize("change", ["expired", "revoked"])
def test_predecessor_must_remain_active_and_unexpired(identity_env, operation, change):
    env = identity_env
    c, secret = ready(env)
    prepare(env, c, secret)
    with env.db.begin() as db:
        row = db.scalar(
            select(models.RuntimeCredential).where(models.RuntimeCredential.version == 1)
        )
        if change == "expired":
            row.expires_at_ms = env.clock.now
        else:
            row.status = "revoked"
    before = rows(env)
    assert request(env, operation, c, secret).status_code == 401
    assert rows(env) == before


@pytest.mark.parametrize("operation", ["prepare", "activate", "cancel"])
@pytest.mark.parametrize(
    "bad",
    [
        "query",
        "body",
        "encoding",
        "enrollment",
        "duplicate_auth",
        "duplicate_session",
        "comma",
        "workspace",
    ],
)
def test_rotation_route_boundary_rejects_ambiguous_or_wrong_proof(identity_env, operation, bad):
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    credential = p if operation == "activate" else c
    headers = auth(credential, secret)
    body = {}
    url = f"{ROOT}/{operation}"
    if bad == "query":
        url += "?token=not-an-authentication-channel"
    elif bad == "body":
        body = {"token": credential["token"]}
    elif bad == "encoding":
        headers["content-encoding"] = "gzip"
    elif bad == "enrollment":
        headers["Authorization"] = "Bearer " + new_token("lpe_")
    elif bad == "duplicate_auth":
        headers = list(headers.items()) + [("authorization", headers["Authorization"])]
    elif bad == "duplicate_session":
        headers = list(headers.items()) + [("x-lotuspond-runtime-session", secret)]
    elif bad == "comma":
        headers["Authorization"] += ", " + credential["token"]
    else:
        headers = list(headers.items()) + [("x-lotuspond-workspace", env.workspace)] * 2
    before = rows(env)
    response = env.client.post(url, json=body, headers=headers)
    assert response.status_code in {400, 401}
    assert rows(env) == before


def test_pending_is_rejected_by_shared_business_authentication(identity_env):
    from lotuspond_runtime.errors import FleetError

    from lotuspond.fleet.authentication import authenticate_runtime

    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    # Poll/claim belong to Task8 and use this same active-only boundary.
    with env.db() as db, pytest.raises(FleetError, match="unauthenticated"):
        authenticate_runtime(
            db,
            p["token"],
            session_secret=secret,
            require_session=True,
            dependencies=env.dependencies,
        )


def test_rotation_joins_caller_transaction_without_committing(identity_env):
    from lotuspond.fleet.authentication import authenticate_runtime
    from lotuspond.fleet.rotation import prepare_rotation

    env = identity_env
    c, secret = ready(env)
    before = rows(env)
    with env.db() as caller:
        principal = authenticate_runtime(
            caller,
            c["token"],
            session_secret=secret,
            require_session=True,
            dependencies=env.dependencies,
        )
        result = prepare_rotation(caller, principal, env.dependencies)
        assert result.credential_version == 2
        caller.rollback()
    assert rows(env) == before


@pytest.mark.parametrize("target", ["candidate", "successor", "policy", "session"])
def test_activation_refreshes_caller_cached_authority(identity_env, engine, target):
    from lotuspond_runtime.errors import FleetError

    from lotuspond.fleet.rotation import activate_rotation

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL cached-authority qualification required")
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    with env.db() as caller:
        candidate = caller.scalar(
            select(models.RuntimeCredential).where(models.RuntimeCredential.version == 2)
        )
        predecessor = caller.scalar(
            select(models.RuntimeCredential).where(models.RuntimeCredential.version == 1)
        )
        process = caller.scalar(select(models.RuntimeSession))
        pool = caller.get(models.RuntimePool, env.pool)
        if target == "candidate":
            prepare(env, c, secret)
            assert candidate.status == "pending"
        elif target == "successor":
            assert request(env, "cancel", c, secret).status_code == 200
            assert predecessor.successor_id == candidate.id
        elif target == "policy":
            assert (
                post(
                    env.client,
                    f"{env.prefix}/runtime-pools/{env.pool}/policies",
                    POLICY,
                    "equivalent",
                ).status_code
                == 201
            )
            assert pool.current_policy_version == 1
        else:
            with env.db.begin() as other:
                other.get(models.RuntimeSession, process.id).status = "revoked"
            assert process.status == "active"
        with pytest.raises(FleetError, match="unauthenticated"):
            activate_rotation(caller, p["token"], secret, env.dependencies)
        caller.rollback()


def test_active_retry_refreshes_cached_pending_candidate_and_preserves_later_q(
    identity_env, engine
):
    from lotuspond.fleet.rotation import activate_rotation

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL cached-replay qualification required")
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    with env.db() as caller:
        cached = caller.scalar(
            select(models.RuntimeCredential).where(models.RuntimeCredential.version == 2)
        )
        activated = request(env, "activate", p, secret).json()
        prepare(env, p, secret)
        assert cached.status == "pending"
        retry = activate_rotation(caller, p["token"], None, env.dependencies)
        assert retry.credential_expires_at_ms == activated["credential_expires_at_ms"]
        assert cached.status == "active"
        caller.commit()
    assert [r[1] for r in rows(env)] == ["revoked", "active", "pending"]


def test_rotation_refuses_exhausted_version_without_replacing_pending(identity_env):
    env = identity_env
    c, secret = ready(env)
    prepare(env, c, secret)
    with env.db.begin() as db:
        db.scalar(
            select(models.RuntimeCredential).where(models.RuntimeCredential.version == 1)
        ).version = 2147483647
    before = rows(env)
    assert request(env, "prepare", c, secret).status_code == 409
    assert rows(env) == before


@pytest.mark.parametrize("operation", ["prepare", "activate", "cancel"])
def test_rotation_clock_fence_never_grants_partial_success(identity_env, operation):
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    before = rows(env)
    env.clock.skew = 30001
    assert request(env, operation, p if operation == "activate" else c, secret).status_code == 503
    assert rows(env) == before
    with env.db() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(models.AuditEvent).where(
                            models.AuditEvent.action == "fleet.identity.clock_fence"
                        )
                    )
                )
            )
            == 1
        )


def test_prepare_policy_queries_keep_full_customer_scope(identity_env, engine):
    from sqlalchemy import event

    env = identity_env
    c, secret = ready(env)
    queries = []

    def observe(connection, cursor, statement, parameters, context, many):
        if "FROM runtime_pools" in statement:
            queries.append(statement.split("WHERE", 1)[1])

    event.listen(engine, "before_cursor_execute", observe)
    try:
        prepare(env, c, secret)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert queries
    assert all(
        all(
            f"runtime_pools.{field} =" in query
            for field in ("workspace_id", "project_id", "environment_id")
        )
        for query in queries
    ), "Every current policy query must include the full authenticated customer scope"
