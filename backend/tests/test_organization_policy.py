import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from lotuspond.config import Settings
from lotuspond.mfa import _totp_at
from lotuspond.models import AuditEvent, MfaFactor, MfaRecoveryCode, User, Workspace, now

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


def set_policy(client, required: bool):
    return client.post("/api/v1/settings/authenticator-policy", json={"require_mfa": required})


def test_policy_requires_the_acting_administrator_to_hold_a_factor(client, account, app):
    enable_mfa(app)
    refused = set_policy(client, True)
    assert refused.status_code == 403
    assert refused.json()["error"] == "mfa_required"
    assert client.get("/api/v1/settings").json()["require_mfa"] is False

    enroll(client, app)
    accepted = set_policy(client, True)
    assert accepted.status_code == 200
    assert accepted.json() == {
        "ok": True,
        "require_mfa": True,
        "members_without_authenticator": 0,
    }
    settings = client.get("/api/v1/settings").json()
    assert settings["require_mfa"] is True
    assert settings["authenticator_policy"] == {
        "require_mfa": True,
        "available": True,
        "manageable": True,
        "required_by_installation": False,
        "members_without_authenticator": 0,
    }


def test_policy_refuses_members_without_an_authenticator_until_they_enroll(client, account, app):
    enroll(client, app)
    with TestClient(app, headers={"origin": "http://testserver"}) as member:
        register(member)
        invite(client)
        member.headers["x-lotuspond-workspace"] = account
        assert member.get("/api/v1/overview").status_code == 200

        assert set_policy(client, True).json()["members_without_authenticator"] == 1
        blocked = member.get("/api/v1/overview")
        assert blocked.status_code == 403
        assert blocked.json()["error"] == "mfa_required"
        # The member keeps a usable session for their own account and for enrollment.
        assert member.get("/api/v1/me").status_code == 200

        enroll(member, app)
        assert member.get("/api/v1/overview").status_code == 200
    assert (
        client.get("/api/v1/settings").json()["authenticator_policy"][
            "members_without_authenticator"
        ]
        == 0
    )


def test_policy_follows_the_key_creator_and_releases_when_withdrawn(client, account, app):
    enroll(client, app)
    with TestClient(app, headers={"origin": "http://testserver"}) as member:
        register(member)
        invite(client, role="admin")
        member.headers["x-lotuspond-workspace"] = account
        created = member.post("/api/v1/api-keys", json={"name": "Member key", "scopes": ["read"]})
        assert created.status_code == 201, created.text
        token = created.json()["key"]

    with TestClient(app, headers={"origin": "http://testserver"}) as service:
        service.headers["x-lotuspond-workspace"] = account
        service.headers["authorization"] = f"Bearer {token}"
        assert service.get("/api/v1/overview").status_code == 200
        assert set_policy(client, True).status_code == 200
        refused = service.get("/api/v1/overview")
        assert refused.status_code == 403
        assert refused.json()["error"] == "mfa_required"
        assert set_policy(client, False).status_code == 200
        assert service.get("/api/v1/overview").status_code == 200


def test_only_an_organization_can_require_an_authenticator(client, app):
    client.headers["x-lotuspond-workspace"] = register(
        client, email="solo@example.com", name="Solo"
    )
    enroll(client, app)
    refused = set_policy(client, True)
    assert refused.status_code == 409
    assert refused.json()["error"] == "policy_unavailable"
    policy = client.get("/api/v1/settings").json()["authenticator_policy"]
    assert policy["manageable"] is False
    assert policy["require_mfa"] is False


def test_the_policy_cannot_be_set_without_a_configured_authenticator_key(client, account, app):
    app.state.settings.mfa_encryption_key = None
    unavailable = set_policy(client, True)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "mfa_unavailable"
    assert client.get("/api/v1/settings").json()["authenticator_policy"] == {
        "require_mfa": False,
        "available": False,
        "manageable": True,
        "required_by_installation": False,
        "members_without_authenticator": 1,
    }


def test_a_required_organization_closes_when_the_authenticator_key_is_unavailable(
    client, account, app, db_factory
):
    enroll(client, app)
    with TestClient(app, headers={"origin": "http://testserver"}) as member:
        register(member)
        invite(client)
        member.headers["x-lotuspond-workspace"] = account
        assert set_policy(client, True).status_code == 200

        app.state.settings.mfa_encryption_key = None
        # The member holds no factor, so the policy itself cannot be evaluated.
        unavailable = member.get("/api/v1/overview")
        assert unavailable.status_code == 503
        assert unavailable.json()["error"] == "mfa_unavailable"
        # An enrolled account cannot fall back to a password-only session either.
        assert client.get("/api/v1/overview").status_code == 401
    with db_factory() as db:
        assert db.get(Workspace, account).require_mfa is True


def test_policy_changes_are_audited_and_repeat_requests_are_inert(client, account, app, db_factory):
    enroll(client, app)
    assert set_policy(client, True).status_code == 200
    assert set_policy(client, True).json()["require_mfa"] is True
    assert set_policy(client, False).status_code == 200
    with db_factory() as db:
        recorded = list(
            db.scalars(
                select(AuditEvent.details)
                .where(AuditEvent.action == "settings.authenticator_policy_changed")
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
    assert [item["require_mfa"] for item in recorded] == [True, False]
    assert all(item["members_without_authenticator"] == 0 for item in recorded)


def test_a_personal_workspace_cannot_store_the_policy(client, account, db_factory):
    with db_factory() as db:
        personal = db.scalar(select(Workspace).where(Workspace.kind == "personal"))
        assert personal is not None
        personal.require_mfa = True
        try:
            db.commit()
            raise AssertionError("A personal workspace must not accept the policy")
        except Exception as error:
            assert "require_mfa" in str(error).lower() or "check" in str(error).lower()
            db.rollback()


def test_postgres_concurrent_enablement_records_one_change(client, account, app, engine, db_factory):
    if engine.dialect.name != "postgresql":
        pytest.skip("Row-lock contention requires the isolated PostgreSQL test database")
    enroll(client, app)
    cookie = client.cookies.get("lotuspond_session")
    barrier = Barrier(2)

    def enable():
        with TestClient(app, headers={"origin": "http://testserver"}) as caller:
            caller.cookies.set("lotuspond_session", cookie)
            caller.headers["x-lotuspond-workspace"] = account
            barrier.wait(timeout=5)
            return set_policy(caller, True).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = [future.result(timeout=15) for future in [pool.submit(enable), pool.submit(enable)]]
    assert statuses == [200, 200]
    with db_factory() as db:
        assert db.get(Workspace, account).require_mfa is True
        # The second request observes the committed policy and stays inert.
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "settings.authenticator_policy_changed")
            )
            == 1
        )


def test_an_installation_requirement_covers_personal_workspaces_too(client, app, db_factory):
    enable_mfa(app)
    app.state.settings.require_mfa = True
    personal = register(client, email="required@example.com", name="Required")
    client.headers["x-lotuspond-workspace"] = personal
    blocked = client.get("/api/v1/overview")
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "mfa_required"
    assert "installation" in blocked.json()["message"]
    # The account keeps exactly the access it needs to satisfy the requirement.
    account = client.get("/api/v1/me")
    assert account.status_code == 200
    assert account.json()["mfa"]["required_by_installation"] is True

    enroll(client, app)
    assert client.get("/api/v1/overview").status_code == 200
    assert client.get("/api/v1/settings").json()["authenticator_policy"][
        "required_by_installation"
    ] is True


def test_an_installation_requirement_without_a_key_closes_workspaces(client, account, app):
    app.state.settings.mfa_encryption_key = None
    app.state.settings.require_mfa = True
    unavailable = client.get("/api/v1/overview")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "mfa_unavailable"


def test_an_installation_requirement_covers_keys_through_their_creator(client, account, app):
    created = client.post("/api/v1/api-keys", json={"name": "Reader", "scopes": ["read"]})
    assert created.status_code == 201, created.text
    token = created.json()["key"]
    with TestClient(app, headers={"origin": "http://testserver"}) as service:
        service.headers["x-lotuspond-workspace"] = account
        service.headers["authorization"] = f"Bearer {token}"
        assert service.get("/api/v1/overview").status_code == 200
        enable_mfa(app)
        app.state.settings.require_mfa = True
        refused = service.get("/api/v1/overview")
        assert refused.status_code == 403
        assert refused.json()["error"] == "mfa_required"


def test_an_installation_requirement_needs_a_configured_key():
    key = base64.urlsafe_b64encode(b"m" * 32).decode()
    with pytest.raises(ValueError, match="authenticator requirement"):
        Settings(env="test", require_mfa=True, mfa_encryption_key=None)
    assert Settings(env="test", require_mfa=True, mfa_encryption_key=key).require_mfa is True


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
