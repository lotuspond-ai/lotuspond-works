from fastapi.testclient import TestClient
from sqlalchemy import select

from lotuspond.models import AuditEvent, Membership, User


def test_individual_and_deliberate_organization(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "individual@example.com",
            "name": "Developer",
            "password": "a-valid-test-password",
        },
    )
    assert response.status_code == 201
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    me = client.get("/api/v1/me").json()
    assert [w["kind"] for w in me["workspaces"]] == ["personal"]
    assert me["user"]["is_platform_admin"] is False
    assert "password_hash" not in me["user"]
    organization = client.post("/api/v1/organizations", json={"name": "My organization"})
    assert organization.status_code == 201
    assert len(client.get("/api/v1/me").json()["workspaces"]) == 2


def test_csrf_and_validation_do_not_echo_secrets(client, account):
    secret = "private-secret-test-string"
    response = client.post("/api/v1/auth/register", json={"password": secret})
    assert response.status_code == 422
    assert secret not in response.text
    assert (
        client.post(
            "/api/v1/projects", json={"name": "X"}, headers={"origin": "https://attacker.example"}
        ).status_code
        == 403
    )
    del client.headers["origin"]
    assert client.post("/api/v1/projects", json={"name": "X"}).status_code == 403


def test_tenant_and_key_scopes(app, client, account):
    project = client.post("/api/v1/projects", json={"name": "Private"}).json()
    key = client.post("/api/v1/api-keys", json={"name": "Reader", "scopes": ["read"]}).json()["key"]
    with TestClient(app, headers={"authorization": f"Bearer {key}"}) as service:
        assert service.get("/api/v1/projects").json()["items"][0]["id"] == project["id"]
        assert service.post("/api/v1/projects", json={"name": "Forbidden"}).status_code == 403
        assert (
            service.post(
                "/api/v1/api-keys", json={"name": "Escalation", "scopes": ["write"]}
            ).status_code
            == 403
        )
    with TestClient(app, headers={"origin": "http://testserver"}) as stranger:
        signup = stranger.post(
            "/api/v1/auth/register",
            json={
                "email": "other@example.com",
                "name": "Other",
                "password": "a-valid-test-password",
            },
        ).json()
        stranger.headers["x-lotuspond-workspace"] = account
        assert stranger.get("/api/v1/projects").status_code == 403
        stranger.headers["x-lotuspond-workspace"] = signup["workspaceId"]
        assert (
            stranger.post(
                "/api/v1/scenarios",
                json={
                    "project_id": project["id"],
                    "name": "Forbidden",
                    "input": {},
                    "expected": {},
                },
            ).status_code
            == 404
        )


def test_platform_operator_boundary_and_audit(client, account, db_factory):
    assert client.get("/api/v1/admin/overview").status_code == 403
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    with db_factory.begin() as db:
        db.get(User, user_id).is_platform_admin = True
    assert client.get("/api/v1/admin/overview").status_code == 200
    response = client.patch(
        f"/api/v1/admin/workspaces/{account}",
        json={"status": "suspended", "reason": "Local integration verification"},
    )
    assert response.status_code == 200
    assert client.get("/api/v1/projects").status_code == 403
    assert (
        client.patch(
            f"/api/v1/admin/workspaces/{account}",
            json={"status": "active", "reason": "Local verification completed"},
        ).status_code
        == 200
    )
    with db_factory() as db:
        events = list(
            db.scalars(select(AuditEvent).where(AuditEvent.action == "admin.workspace_status"))
        )
        assert len(events) == 2
        assert events[0].details["from"] == "active"


def test_personal_members_and_viewer_writes(client, account, db_factory):
    me = client.get("/api/v1/me").json()
    personal = next(w for w in me["workspaces"] if w["kind"] == "personal")
    assert (
        client.post(
            "/api/v1/members",
            headers={"x-lotuspond-workspace": personal["id"]},
            json={"email": "test@example.com", "role": "editor"},
        ).status_code
        == 409
    )
    with db_factory.begin() as db:
        db.get(Membership, (account, me["user"]["id"])).role = "viewer"
    assert client.get("/api/v1/projects").status_code == 200
    assert client.post("/api/v1/projects", json={"name": "Forbidden"}).status_code == 403


def test_logout_revokes_session(client, account):
    assert client.post("/api/v1/auth/logout", json={}).status_code == 200
    assert client.get("/api/v1/me").status_code == 401


def test_long_name_remains_valid_for_personal_workspace(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "long@example.com", "name": "a" * 160, "password": "a-valid-test-password"},
    )
    assert response.status_code == 201, response.text
    me = client.get("/api/v1/me").json()
    assert len(me["user"]["name"]) == 160
    assert len(me["workspaces"][0]["name"]) <= 160


def test_production_membership_requires_mailbox_verification(client, account, app):
    with TestClient(app, headers={"origin": "http://testserver"}) as other:
        assert (
            other.post(
                "/api/v1/auth/register",
                json={
                    "email": "unverified@example.com",
                    "name": "Unverified",
                    "password": "a-valid-test-password",
                },
            ).status_code
            == 201
        )
    # Exercise the production identity gate using the isolated test rate-store and transport.
    with app.state.sessions.begin() as db:
        db.scalar(select(User).where(User.email == "owner@example.com")).email_verified = True
    app.state.settings.env = "production"
    response = client.post(
        "/api/v1/members", json={"email": "unverified@example.com", "role": "admin"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "identity_unverified"
    app.state.settings.env = "test"


def test_production_unverified_signup_cannot_create_or_enumerate_organizations(app):
    app.state.settings.env = "production"
    with TestClient(app, headers={"origin": "http://testserver"}) as caller:
        blocked = caller.post(
            "/api/v1/auth/register",
            json={
                "email": "blocked-organization@example.com",
                "name": "Blocked",
                "password": "a-valid-test-password",
                "account_type": "organization",
                "workspace": "Blocked organization",
            },
        )
        assert blocked.status_code == 403
        assert blocked.json()["error"] == "identity_unverified"
    app.state.settings.env = "test"
    with TestClient(app, headers={"origin": "http://testserver"}) as existing:
        assert existing.post(
            "/api/v1/auth/register",
            json={
                "email": "preexisting-owner@example.com",
                "name": "Preexisting",
                "password": "a-valid-test-password",
                "account_type": "organization",
                "workspace": "Existing organization",
            },
        ).status_code == 201
        app.state.settings.env = "production"
        me = existing.get("/api/v1/me")
        assert me.status_code == 200
        assert [workspace["kind"] for workspace in me.json()["workspaces"]] == ["personal"]
    app.state.settings.env = "test"
