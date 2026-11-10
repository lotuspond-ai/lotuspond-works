"""The administrator-authorized reset for a member who lost every authenticator factor.

Organization-wide and installation-wide authenticator requirements were retired (ADR-0014);
this reset stays, because it is how a locked-out member gets back in.
"""

import base64

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from lotuspond.mfa import _totp_at
from lotuspond.models import AuditEvent, MfaFactor, MfaRecoveryCode, User, now

MEMBER = "member@example.com"
PASSWORD = "a-valid-test-password"


def enable_mfa(app):
    app.state.settings.mfa_encryption_key = SecretStr(base64.urlsafe_b64encode(b"m" * 32).decode())


def enroll(client, app):
    """Complete an authenticator enrollment for whoever holds this client's session."""
    enable_mfa(app)
    started = client.post(
        "/api/v1/auth/mfa/enrollment/start",
        json={"current_password": "a-valid-test-password"},
    )
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    decoded = base64.b32decode(secret + "=" * (-len(secret) % 8))
    confirmed = client.post(
        "/api/v1/auth/mfa/enrollment/confirm",
        json={"code": _totp_at(decoded, int(now().timestamp()))},
    )
    assert confirmed.status_code == 200, confirmed.text


def register(client, email=MEMBER, name="Member"):
    created = client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": name, "password": "a-valid-test-password"},
    )
    assert created.status_code == 201, created.text
    return created.json()["workspaceId"]


def invite(client, email=MEMBER, role="editor"):
    added = client.post("/api/v1/members", json={"email": email, "role": role})
    assert added.status_code == 201, added.text


def reset_member(client, member_id, password=PASSWORD, reason="lost authenticator and codes"):
    return client.post(
        f"/api/v1/auth/mfa/members/{member_id}/reset",
        json={"current_password": password, "reason": reason},
    )


def test_an_organization_administrator_can_clear_a_lost_authenticator(
    client, account, app, db_factory
):
    enroll(client, app)
    with TestClient(app, headers={"origin": "http://testserver"}) as member:
        register(member)
        invite(client)
        member.headers["x-lotuspond-workspace"] = account
        enroll(member, app)
        member_id = member.get("/api/v1/me").json()["user"]["id"]
        assert member.get("/api/v1/overview").status_code == 200

        done = reset_member(client, member_id)
        assert done.status_code == 200, done.text
        assert done.json()["member_id"] == member_id
        # Clearing the factor also ends every session it protected.
        assert member.get("/api/v1/me").status_code == 401

    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(MfaFactor).where(
            MfaFactor.user_id == member_id
        )) == 0
        assert db.scalar(select(func.count()).select_from(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == member_id
        )) == 0
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "members.authenticator_reset")
        )
        assert event.resource_id == member_id
        assert event.details["reason"] == "lost authenticator and codes"

    # The member recovers on their own: password sign-in, then a fresh enrolment.
    with TestClient(app, headers={"origin": "http://testserver"}) as recovered:
        assert recovered.post(
            "/api/v1/auth/login",
            json={"email": MEMBER, "password": PASSWORD},
        ).status_code == 200
        recovered.headers["x-lotuspond-workspace"] = account
        enroll(recovered, app)
        assert recovered.get("/api/v1/overview").status_code == 200


def test_the_reset_needs_a_different_administrator_and_the_right_password(
    client, account, app
):
    enroll(client, app)
    owner_id = client.get("/api/v1/me").json()["user"]["id"]
    with TestClient(app, headers={"origin": "http://testserver"}) as member:
        register(member)
        invite(client, role="admin")
        member.headers["x-lotuspond-workspace"] = account
        enroll(member, app)
        member_id = member.get("/api/v1/me").json()["user"]["id"]

        # An administrator cannot clear their own factor this way.
        own = reset_member(client, owner_id)
        assert own.status_code == 403
        assert own.json()["error"] == "different_administrator_required"

        wrong = reset_member(client, member_id, password="not-the-right-password")
        assert wrong.status_code == 401
        assert wrong.json()["error"] == "invalid_credentials"


def test_the_reset_is_refused_outside_an_organization_and_for_operators(
    client, account, app, db_factory
):
    enroll(client, app)
    with TestClient(app, headers={"origin": "http://testserver"}) as member:
        personal = register(member)
        invite(client)
        member.headers["x-lotuspond-workspace"] = account
        enroll(member, app)
        member_id = member.get("/api/v1/me").json()["user"]["id"]

        # A member with no factor has nothing to clear.
        stranger = register(member, email="stranger@example.com", name="Stranger")
        assert stranger

        with db_factory.begin() as db:
            db.get(User, member_id).is_platform_admin = True
        operator = reset_member(client, member_id)
        assert operator.status_code == 403
        assert operator.json()["error"] == "forbidden"
        with db_factory.begin() as db:
            db.get(User, member_id).is_platform_admin = False

    # The same call in a personal workspace has no organization to authorize it.
    personal_client = client
    personal_client.headers["x-lotuspond-workspace"] = personal
    refused = reset_member(personal_client, member_id)
    assert refused.status_code in {403, 409}


def test_a_member_without_a_factor_or_outside_the_workspace_is_refused(client, account, app):
    enroll(client, app)
    with TestClient(app, headers={"origin": "http://testserver"}) as member:
        register(member)
        invite(client)
        member_id = member.get("/api/v1/me").json()["user"]["id"]
        nothing = reset_member(client, member_id)
        assert nothing.status_code == 409
        assert nothing.json()["error"] == "mfa_not_enabled"

    with TestClient(app, headers={"origin": "http://testserver"}) as outsider:
        register(outsider, email="outsider@example.com", name="Outsider")
        outsider_id = outsider.get("/api/v1/me").json()["user"]["id"]
    assert reset_member(client, outsider_id).status_code == 404
