from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from lotuspond.models import AuditEvent, LoginSession, now
from lotuspond.security import digest


def login(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "a-valid-test-password"},
    )
    assert response.status_code == 200


def test_session_inventory_and_revocation_are_account_scoped(app, client, account, db_factory):
    with TestClient(app, headers={"origin": "http://testserver"}) as other_device:
        login(other_device)
        inventory = client.get("/api/v1/auth/sessions")
        assert inventory.status_code == 200
        assert "token" not in inventory.text
        rows = inventory.json()["items"]
        assert len(rows) == 2
        assert sum(row["current"] for row in rows) == 1
        target = next(row["id"] for row in rows if not row["current"])
        with TestClient(app, headers={"origin": "http://testserver"}) as stranger:
            stranger.post(
                "/api/v1/auth/register",
                json={
                    "email": "stranger@example.com",
                    "name": "Other",
                    "password": "another-test-password",
                },
            )
            assert (
                stranger.request("DELETE", f"/api/v1/auth/sessions/{target}", json={}).status_code
                == 404
            )
        assert client.request("DELETE", f"/api/v1/auth/sessions/{target}", json={}).json() == {
            "ok": True,
            "signed_out": False,
        }
        assert other_device.get("/api/v1/me").status_code == 401
        assert client.get("/api/v1/me").status_code == 200
    with db_factory() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "account.session_revoked"))
        assert event.resource_id == target


def test_revoking_current_session_clears_cookie(client, account):
    session = client.get("/api/v1/auth/sessions").json()["items"][0]
    response = client.request("DELETE", f"/api/v1/auth/sessions/{session['id']}", json={})
    assert response.json() == {"ok": True, "signed_out": True}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/v1/me").status_code == 401


def test_password_change_requires_old_password_rotates_and_revokes(
    app, client, account, db_factory
):
    with TestClient(app, headers={"origin": "http://testserver"}) as other_device:
        login(other_device)
        original = client.cookies.get("lotuspond_session")
        rejected = client.post(
            "/api/v1/auth/password",
            json={"current_password": "wrong-secret", "new_password": "new-valid-test-password"},
        )
        assert rejected.status_code == 401
        assert "wrong-secret" not in rejected.text
        assert other_device.get("/api/v1/me").status_code == 200
        assert (
            client.post(
                "/api/v1/auth/password",
                json={
                    "current_password": "a-valid-test-password",
                    "new_password": "a-valid-test-password",
                },
            ).status_code
            == 422
        )
        response = client.post(
            "/api/v1/auth/password",
            json={
                "current_password": "a-valid-test-password",
                "new_password": "new-valid-test-password",
            },
        )
        assert response.status_code == 200
        assert client.cookies.get("lotuspond_session") != original
        assert client.get("/api/v1/me").status_code == 200
        assert other_device.get("/api/v1/me").status_code == 401
        assert (
            other_device.post(
                "/api/v1/auth/login",
                json={"email": "owner@example.com", "password": "a-valid-test-password"},
            ).status_code
            == 401
        )
        assert (
            other_device.post(
                "/api/v1/auth/login",
                json={"email": "owner@example.com", "password": "new-valid-test-password"},
            ).status_code
            == 200
        )
    with db_factory() as db:
        assert (
            db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(original)))
            is None
        )
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "account.password_changed"))


def test_session_creation_prunes_expired_and_bounds_active_sessions(client, account, db_factory):
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    old = now() - timedelta(hours=2)
    with db_factory.begin() as db:
        for index in range(24):
            db.add(
                LoginSession(
                    user_id=user_id,
                    token_hash=digest(f"old-{index}"),
                    created_at=old + timedelta(seconds=index),
                    expires_at=now() + timedelta(hours=1),
                )
            )
        db.add(
            LoginSession(
                user_id=user_id,
                token_hash=digest("expired"),
                expires_at=now() - timedelta(seconds=1),
            )
        )
    login(client)
    assert len(client.get("/api/v1/auth/sessions").json()["items"]) == 20
    with db_factory() as db:
        assert (
            db.scalar(select(LoginSession).where(LoginSession.token_hash == digest("expired")))
            is None
        )
        assert (
            db.scalar(select(LoginSession).where(LoginSession.token_hash == digest("old-0")))
            is None
        )
