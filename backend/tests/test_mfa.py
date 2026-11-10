import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from lotuspond.mfa import _totp_at
from lotuspond.models import (
    IdentityChallenge,
    LoginSession,
    MfaEnrollment,
    MfaFactor,
    MfaLoginChallenge,
    MfaRecoveryCode,
    User,
    now,
)
from lotuspond.security import digest


def enable_mfa(app):
    app.state.settings.mfa_encryption_key = SecretStr(
        base64.urlsafe_b64encode(b"m" * 32).decode()
    )


def current_totp(secret: str) -> str:
    padding = "=" * (-len(secret) % 8)
    decoded = base64.b32decode(secret + padding)
    return _totp_at(decoded, int(now().timestamp()))


def enroll(client, app):
    enable_mfa(app)
    started = client.post(
        "/api/v1/auth/mfa/enrollment/start",
        json={"current_password": "a-valid-test-password"},
    )
    assert started.status_code == 200, started.text
    assert started.headers["cache-control"] == "no-store"
    payload = started.json()
    assert payload["otpauth_uri"].startswith("otpauth://totp/")
    confirmed = client.post(
        "/api/v1/auth/mfa/enrollment/confirm",
        json={"code": current_totp(payload["secret"])},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.headers["cache-control"] == "no-store"
    return payload, confirmed.json()["recovery_codes"]


def test_rfc6238_sha1_six_digit_vectors():
    secret = b"12345678901234567890"
    expected = {
        59: "287082",
        1111111109: "081804",
        1111111111: "050471",
        1234567890: "005924",
        2000000000: "279037",
        20000000000: "353130",
    }
    assert {timestamp: _totp_at(secret, timestamp) for timestamp in expected} == expected


def test_enrollment_start_rotates_sessions_and_binds_confirmation(
    app, client, account
):
    enable_mfa(app)
    with TestClient(app, headers={"origin": "http://testserver"}) as old_session:
        assert old_session.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "a-valid-test-password"},
        ).status_code == 200
        started = client.post(
            "/api/v1/auth/mfa/enrollment/start",
            json={"current_password": "a-valid-test-password"},
        )
        code = current_totp(started.json()["secret"])
        assert old_session.post(
            "/api/v1/auth/mfa/enrollment/confirm", json={"code": code}
        ).status_code == 401
        assert client.post(
            "/api/v1/auth/mfa/enrollment/confirm", json={"code": code}
        ).status_code == 200


def test_mfa_enrollment_login_recovery_and_session_binding(
    app, client, account, db_factory
):
    original_cookie = client.cookies.get("lotuspond_session")
    _, codes = enroll(client, app)
    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(code.startswith("lpr_") for code in codes)
    assert client.cookies.get("lotuspond_session") != original_cookie
    status = client.get("/api/v1/me").json()["mfa"]
    assert status == {
        "available": True,
        "enabled": True,
        "session_verified": True,
        "recovery_codes_remaining": 10,
        "required_for_privileged": False,
    }

    with TestClient(app, headers={"origin": "http://testserver"}) as pending_login:
        pending = pending_login.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "a-valid-test-password"},
        )
        assert pending.status_code == 202
        pending_token = pending.json()["challenge_token"]

        verified_cookie = client.cookies.get("lotuspond_session")
        with db_factory() as db:
            before = db.scalar(select(LoginSession))
            verified_at = before.mfa_verified_at
        changed = client.post(
            "/api/v1/auth/password",
            json={
                "current_password": "a-valid-test-password",
                "new_password": "a-new-valid-test-password",
            },
        )
        assert changed.status_code == 200, changed.text
        assert client.cookies.get("lotuspond_session") != verified_cookie
        with db_factory() as db:
            after = db.scalar(select(LoginSession))
            assert after.mfa_verified_at == verified_at
            assert db.scalar(select(MfaLoginChallenge)) is None
        assert pending_login.post(
            "/api/v1/auth/mfa/challenge/complete",
            json={"challenge_token": pending_token, "code": codes[0]},
        ).json()["error"] == "invalid_mfa_challenge"

    assert client.post("/api/v1/auth/logout", json={}).status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "a-new-valid-test-password"},
    )
    assert login.status_code == 202, login.text
    assert login.json()["mfa_required"] is True
    assert client.get("/api/v1/me").status_code == 401
    completed = client.post(
        "/api/v1/auth/mfa/challenge/complete",
        json={"challenge_token": login.json()["challenge_token"], "code": codes[0]},
    )
    assert completed.json() == {"ok": True}
    assert client.get("/api/v1/me").json()["mfa"]["recovery_codes_remaining"] == 9
    replay = client.post(
        "/api/v1/auth/mfa/challenge/complete",
        json={"challenge_token": login.json()["challenge_token"], "code": codes[1]},
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_mfa_challenge"


def test_mfa_challenge_attempt_cap_persists_and_missing_key_never_falls_back(
    app, client, account, db_factory
):
    _, codes = enroll(client, app)
    client.post("/api/v1/auth/logout", json={})
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "a-valid-test-password"},
    )
    token = login.json()["challenge_token"]
    for _ in range(8):
        denied = client.post(
            "/api/v1/auth/mfa/challenge/complete",
            json={"challenge_token": token, "code": "000000"},
        )
        assert denied.status_code == 400
    assert client.post(
        "/api/v1/auth/mfa/challenge/complete",
        json={"challenge_token": token, "code": codes[0]},
    ).json()["error"] == "invalid_mfa_challenge"
    with db_factory() as db:
        challenge = db.scalar(select(MfaLoginChallenge))
        assert challenge.attempts == 8
        assert challenge.revoked_at is not None

    app.state.settings.mfa_encryption_key = None
    unavailable = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "a-valid-test-password"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "mfa_unavailable"
    assert client.get("/api/v1/me").status_code == 401


def test_mfa_management_has_per_account_attempt_limit(app, client, account):
    enroll(client, app)
    for _ in range(10):
        denied = client.post(
            "/api/v1/auth/mfa/recovery-codes/regenerate",
            json={
                "current_password": "a-valid-test-password",
                "code": "lpr_invalid",
            },
        )
        assert denied.status_code == 400
        assert denied.json()["error"] == "invalid_mfa_code"
    limited = client.post(
        "/api/v1/auth/mfa/disable",
        json={
            "current_password": "a-valid-test-password",
            "code": "lpr_invalid",
        },
    )
    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"


def test_recovery_regeneration_disable_and_production_operator_enforcement(
    app, client, account, db_factory
):
    _, codes = enroll(client, app)
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    regenerated = client.post(
        "/api/v1/auth/mfa/recovery-codes/regenerate",
        json={"current_password": "a-valid-test-password", "code": codes[0]},
    )
    assert regenerated.status_code == 200, regenerated.text
    replacement = regenerated.json()["recovery_codes"]
    assert len(replacement) == 10
    assert set(replacement).isdisjoint(codes)

    with db_factory.begin() as db:
        user = db.get(User, user_id)
        user.is_platform_admin = True
        user.email_verified = True
    app.state.settings.env = "production"
    assert client.get("/api/v1/admin/overview").status_code == 200
    denied = client.post(
        "/api/v1/auth/mfa/disable",
        json={"current_password": "a-valid-test-password", "code": replacement[0]},
    )
    assert denied.status_code == 403
    assert denied.json()["error"] == "mfa_required"

    app.state.settings.env = "test"
    disabled = client.post(
        "/api/v1/auth/mfa/disable",
        json={"current_password": "a-valid-test-password", "code": replacement[1]},
    )
    assert disabled.json() == {"ok": True}
    assert client.get("/api/v1/me").json()["mfa"]["enabled"] is False
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "a-valid-test-password"},
    )
    assert login.status_code == 200


def test_production_platform_admin_without_factor_is_denied_but_can_enroll(
    app, client, account, db_factory
):
    enable_mfa(app)
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    with db_factory.begin() as db:
        user = db.get(User, user_id)
        user.is_platform_admin = True
        user.email_verified = True
    app.state.settings.env = "production"
    denied = client.get("/api/v1/admin/overview")
    assert denied.status_code == 403
    assert denied.json()["error"] == "mfa_required"
    assert client.post(
        "/api/v1/auth/mfa/enrollment/start",
        json={"current_password": "a-valid-test-password"},
    ).status_code == 200


def test_password_reset_revokes_pending_mfa_enrollment(app, db_factory):
    enable_mfa(app)
    with TestClient(app, headers={"origin": "http://testserver"}) as browser:
        registered = browser.post(
            "/api/v1/auth/register",
            json={
                "email": "pending-mfa@example.com",
                "name": "Pending MFA",
                "password": "a-valid-test-password",
            },
        )
        assert registered.status_code == 201
        assert browser.post(
            "/api/v1/auth/mfa/enrollment/start",
            json={"current_password": "a-valid-test-password"},
        ).status_code == 200
        user_id = browser.get("/api/v1/me").json()["user"]["id"]
        raw = "reset-token-with-enough-entropy-for-test"
        with db_factory.begin() as db:
            db.add(
                IdentityChallenge(
                    user_id=user_id,
                    kind="password_reset",
                    token_hash=digest(raw),
                    expires_at=now() + timedelta(minutes=5),
                )
            )
        reset = browser.post(
            "/api/v1/auth/password/reset/confirm",
            json={"token": raw, "new_password": "a-new-valid-test-password"},
        )
        assert reset.status_code == 200, reset.text
        assert browser.get("/api/v1/me").status_code == 401
    with db_factory() as db:
        assert db.scalar(
            select(MfaEnrollment).where(MfaEnrollment.user_id == user_id)
        ) is None


def test_password_reset_revokes_existing_mfa_login_challenge(
    app, client, account, db_factory
):
    _, _codes = enroll(client, app)
    with TestClient(app, headers={"origin": "http://testserver"}) as login_browser:
        login = login_browser.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "a-valid-test-password"},
        )
        challenge_token = login.json()["challenge_token"]
        user_id = client.get("/api/v1/me").json()["user"]["id"]
        raw = "factor-reset-token-with-enough-entropy"
        with db_factory.begin() as db:
            db.add(
                IdentityChallenge(
                    user_id=user_id,
                    kind="password_reset",
                    token_hash=digest(raw),
                    expires_at=now() + timedelta(minutes=5),
                )
            )
        assert client.post(
            "/api/v1/auth/password/reset/confirm",
            json={"token": raw, "new_password": "a-new-valid-test-password"},
        ).status_code == 200
        assert login_browser.post(
            "/api/v1/auth/mfa/challenge/complete",
            json={"challenge_token": challenge_token, "code": _codes[0]},
        ).json()["error"] == "invalid_mfa_challenge"
    with db_factory() as db:
        assert db.scalar(
            select(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user_id)
        ) is None
        assert db.scalar(select(MfaFactor).where(MfaFactor.user_id == user_id)) is not None


def test_postgres_concurrent_mfa_challenge_replay_has_one_winner(
    app, client, account, db_factory, engine
):
    if engine.dialect.name != "postgresql":
        pytest.skip("MFA replay serialization requires isolated PostgreSQL")
    _, codes = enroll(client, app)
    client.post("/api/v1/auth/logout", json={})
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "a-valid-test-password"},
    )
    token = login.json()["challenge_token"]
    barrier = Barrier(2)

    def complete():
        with TestClient(app, headers={"origin": "http://testserver"}) as browser:
            barrier.wait()
            response = browser.post(
                "/api/v1/auth/mfa/challenge/complete",
                json={"challenge_token": token, "code": codes[0]},
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: complete(), range(2)))
    assert sorted(status for status, _ in results) == [200, 400]
    assert next(body for status, body in results if status == 400)["error"] == (
        "invalid_mfa_challenge"
    )
    with db_factory() as db:
        assert db.scalar(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.code_hash == digest(codes[0])
            )
        ).consumed_at is not None
