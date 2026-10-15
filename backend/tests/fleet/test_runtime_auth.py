"""Machine and human authentication are separate token boundaries."""

import pytest

from tests.fleet.identity_fixtures import (
    acquire,
    auth,
    enrolled,
    enrollment,
)


@pytest.mark.parametrize("kind", ["enrollment", "credential", "session", "human", "unknown"])
def test_token_route_matrix(identity_env, kind):
    env = identity_env
    credential = enrolled(env)
    session = acquire(env, credential).json()
    tokens = {
        "enrollment": enrollment(env).json()["token"],
        "credential": credential["token"],
        "session": session["secret"],
        "human": "lp_" + "a" * 43,
        "unknown": "lpr_" + "a" * 43,
    }
    headers = {"Authorization": "Bearer " + tokens[kind]}
    assert env.client.get("/api/v1/runtime/self", headers=headers).status_code == (
        200 if kind == "credential" else 401
    )
    assert env.client.get(f"{env.prefix}/connections", headers=headers).status_code == 401
    assert env.client.get("/api/v1/projects", headers=headers).status_code == 401


def test_runtime_boundary(identity_env):
    env = identity_env
    credential = enrolled(env)
    headers = auth(credential)
    for malformed in [
        [("Authorization", "Bearer " + credential["token"])] * 2,
        {"Authorization": "Bearer " + credential["token"] + ",x"},
        dict(headers, **{"X-Lotuspond-Runtime-Session": "x,y"}),
    ]:
        assert env.client.get("/api/v1/runtime/self", headers=malformed).status_code in {400, 401}
    assert (
        env.client.post(
            "/api/v1/runtime/session",
            content=b'{"x":1,"x":2}',
            headers=dict(headers, **{"Content-Type": "application/json"}),
        ).status_code
        == 400
    )
    assert (
        env.client.post(
            "/api/v1/runtime/session",
            content=b"{}",
            headers=dict(
                headers, **{"Content-Type": "application/json", "Content-Encoding": "gzip"}
            ),
        ).status_code
        == 400
    )


def test_exchange_limits_include_invalid_bodies_and_direct_peer(identity_env):
    env = identity_env
    token = enrollment(env).json()["token"]
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    for n in range(5):
        response = env.client.post(
            "/api/v1/runtime/enroll",
            content=b'{"x":1,"x":2}',
            headers=dict(headers, **{"X-Forwarded-For": f"192.0.2.{n}"}),
        )
        assert response.status_code == 400
    assert (
        env.client.post(
            "/api/v1/runtime/enroll", json={"platform": "linux", "connectors": []}, headers=headers
        ).status_code
        == 429
    )


def test_stream_budget_exact_bytes(identity_env):
    env = identity_env
    credential = enrolled(env)
    headers = dict(auth(credential), **{"Content-Type": "application/json"})
    for length, expected in [(65536, 201), (65537, 413)]:
        data = b"{}" + b" " * (length - 2)
        response = env.client.post(
            "/api/v1/runtime/session", content=iter([data[:20000], data[20000:]]), headers=headers
        )
        assert response.status_code == expected, response.text


def test_exchange_peer_limit_ignores_forwarded_headers(identity_env):
    env = identity_env
    for n in range(20):
        response = env.client.post(
            "/api/v1/runtime/enroll", json={}, headers={"X-Forwarded-For": f"192.0.2.{n}"}
        )
        assert response.status_code == 401
    response = env.client.post(
        "/api/v1/runtime/enroll", json={}, headers={"X-Forwarded-For": "198.51.100.1"}
    )
    assert response.status_code == 429


def test_fleet_distributed_limiter_failure_denies(identity_env):
    from dataclasses import replace

    from fastapi import HTTPException

    env = identity_env

    class UnavailableLimiter:
        def check(self, *args):
            raise HTTPException(503, {"error": "rate_store_unavailable"})

    env.app.state.fleet_dependencies = replace(env.dependencies, limiter=UnavailableLimiter())
    response = enrollment(env)
    assert response.status_code == 503
    assert response.json() == {"error": "authority_unavailable", "message": "authority_unavailable"}
    assert env.client.post("/api/v1/runtime/enroll", json={}).status_code == 503


def test_tokens_are_class_specific_and_never_logged_or_audited(identity_env, caplog):
    from sqlalchemy import select

    from lotuspond import models
    from lotuspond.fleet.authentication import new_token, token_digest

    env = identity_env
    issued = enrollment(env).json()
    from tests.fleet.identity_fixtures import exchange

    credential = exchange(env, issued["token"]).json()
    session = acquire(env, credential).json()
    for value in (issued["token"], credential["token"], session["secret"]):
        assert len(value) == 47
    for prefix in ("lpe_", "lpr_", "lps_"):
        assert len(token_digest(new_token(prefix), prefix)) == 64
    with env.db() as db:
        stored = repr([row.__dict__ for row in db.scalars(select(models.AuditEvent))])
        for value in (issued["token"], credential["token"], session["secret"]):
            assert value not in stored and value not in caplog.text
    for response in [
        env.client.get("/api/v1/runtime/self", headers=auth(credential)),
        env.client.get(f"{env.prefix}/runtime-instances"),
    ]:
        assert response.headers["cache-control"] == "no-store"
        assert "token_digest" not in response.text


def test_pending_and_revoked_credentials_never_authenticate(identity_env):
    from sqlalchemy import select

    from lotuspond import models
    from lotuspond.fleet.authentication import machine_fields, new_token, token_digest

    env = identity_env
    credential = enrolled(env)
    acquire(env, credential)
    pending_token = new_token("lpr_")
    with env.db.begin() as db:
        active = db.scalar(select(models.RuntimeCredential))
        session = db.scalar(select(models.RuntimeSession))
        pending = models.RuntimeCredential(
            **machine_fields(active),
            instance_id=active.instance_id,
            version=2,
            token_digest=token_digest(pending_token, "lpr_"),
            status="pending",
            expires_at_ms=env.clock.now + 900000,
            predecessor_id=active.id,
            session_id=session.id,
            generation=session.generation,
            prepared_policy_version=1,
            activation_deadline_ms=env.clock.now + 60000,
        )
        db.add(pending)
    assert (
        env.client.get(
            "/api/v1/runtime/self", headers={"Authorization": "Bearer " + pending_token}
        ).status_code
        == 401
    )
    with env.db.begin() as db:
        db.scalar(
            select(models.RuntimeCredential).where(models.RuntimeCredential.status == "active")
        ).status = "revoked"
    assert env.client.get("/api/v1/runtime/self", headers=auth(credential)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_bucket", ["peer", "token"])
async def test_exchange_quota_wait_preserves_event_loop_progress(blocked_bucket):
    import asyncio
    from threading import Event, Timer
    from types import SimpleNamespace

    import redis
    from fastapi.responses import JSONResponse

    from lotuspond.fleet.authentication import new_token
    from lotuspond.fleet.authority import FleetDependencies, UnavailableRecoveryAuthority
    from lotuspond.fleet.catalog import InstalledCatalog
    from lotuspond.fleet.management_routes import FleetRequestBoundary
    from lotuspond.security import RateLimiter

    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    calls, messages, human_messages = [], [], []
    blocking_call = 1 if blocked_bucket == "peer" else 2

    class RecordedLimiter(RateLimiter):
        def check(self, key, limit, seconds=60):
            calls.append((key, limit))
            return super().check(key, limit, seconds)

    class BlockingRedis:
        def eval(self, *args):
            if len(calls) == blocking_call:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(3), "Synthetic quota wait was not released"
                raise redis.exceptions.TimeoutError("synthetic private store failure")
            return 1

    limiter = RecordedLimiter(SimpleNamespace(redis_url=None, env="production"))
    limiter.client = BlockingRedis()
    deps = FleetDependencies(UnavailableRecoveryAuthority(), InstalledCatalog(), limiter=limiter)

    async def inner(scope, receive, send):
        assert scope["path"] == "/api/v1/health", (
            "Unavailable quota store reached enrollment handler"
        )
        await JSONResponse({"status": "ok"})(scope, receive, send)

    async def receive():
        raise AssertionError("Failed quota lookup consumed a request body")

    async def send(message):
        messages.append(message)

    async def send_human(message):
        human_messages.append(message)

    scope = {
        "type": "http",
        "path": "/api/v1/runtime/enroll",
        "method": "POST",
        "headers": [(b"authorization", ("Bearer " + new_token("lpe_")).encode("ascii"))],
        "client": ("127.0.0.1", 12345),
        "app": SimpleNamespace(state=SimpleNamespace(fleet_dependencies=deps)),
    }
    boundary = FleetRequestBoundary(inner, SimpleNamespace(fleet_enabled=True))
    # Independent cleanup releases even the old synchronously blocked loop.
    # Assertions never strand a worker or wait for an unreleased Redis stub.
    watchdog = Timer(1, release.set)
    watchdog.start()
    enrollment_task = asyncio.create_task(boundary(scope, receive, send))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        assert not release.is_set(), "Quota I/O blocked this event loop until watchdog release"
        await asyncio.wait_for(
            boundary(dict(scope, path="/api/v1/health"), receive, send_human), timeout=0.5
        )
        assert not release.is_set(), "Unrelated request could not finish while quota storage waited"
        assert human_messages[0]["status"] == 200
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join(timeout=2)
        await asyncio.wait_for(enrollment_task, timeout=3)
        deps.close()
    assert messages[0]["status"] == 503
    assert b"synthetic private" not in b"".join(item.get("body", b"") for item in messages)
    assert [limit for _, limit in calls] == ([20] if blocked_bucket == "peer" else [20, 5])
    assert calls[0][0].startswith("fleet:exchange:peer:")
    if blocked_bucket == "token":
        assert calls[1][0].startswith("fleet:exchange:token:")
