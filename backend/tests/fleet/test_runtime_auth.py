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
