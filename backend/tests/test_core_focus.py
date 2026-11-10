"""ADR-0014: features outside the core loop refuse requests; stored data keeps its meaning."""

import pytest
from fastapi.testclient import TestClient

from lotuspond.config import Settings
from lotuspond.models import Policy, Workspace


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("POST", "/api/v1/auth/offline-enrollment/inspect", 405),
        ("POST", "/api/v1/auth/offline-enrollment/complete", 405),
        ("POST", "/api/v1/settings/authenticator-policy", 405),
        ("POST", "/api/v1/authorize", 405),
        ("GET", "/api/v1/admin/operations", 404),
    ],
)
def test_retired_routes_refuse_requests(client, account, method, path, status):
    assert client.request(method, path, json={}).status_code == status


def test_settings_and_workspaces_no_longer_report_an_authenticator_requirement(
    client, account, db_factory
):
    with db_factory.begin() as db:
        db.get(Workspace, account).require_mfa = True
    settings = client.get("/api/v1/settings").json()
    assert "authenticator_policy" not in settings and "require_mfa" not in settings
    workspaces = client.get("/api/v1/me").json()["workspaces"]
    assert all("require_mfa" not in workspace for workspace in workspaces)
    assert "required_by_installation" not in client.get("/api/v1/me").json()["mfa"]


def test_a_stored_organization_requirement_no_longer_holds_members(
    app, client, account, db_factory
):
    """The organization-wide requirement was retired; its stored flag authorizes nothing."""
    with db_factory.begin() as db:
        db.get(Workspace, account).require_mfa = True
    with TestClient(app, headers={"origin": "http://testserver"}) as member:
        assert (
            member.post(
                "/api/v1/auth/register",
                json={
                    "email": "member@example.com",
                    "name": "Member",
                    "password": "a-valid-test-password",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/v1/members", json={"email": "member@example.com", "role": "editor"}
            ).status_code
            == 201
        )
        member.headers["x-lotuspond-workspace"] = account
        assert member.get("/api/v1/projects").status_code == 200


def test_the_installation_requirement_setting_is_ignored(monkeypatch):
    monkeypatch.setenv("LOTUSPOND_REQUIRE_MFA", "true")
    monkeypatch.setenv("LOTUSPOND_NETWORK_MODE", "airgap")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "require_mfa")
    assert not hasattr(settings, "network_mode")


def test_new_policies_cannot_declare_tool_lists(client, account, db_factory):
    refused = client.post(
        "/api/v1/policies", json={"name": "Tools", "blocked_tools": ["remove_all"]}
    )
    assert refused.status_code == 422
    created = client.post("/api/v1/policies", json={"name": "Release limits"})
    assert created.status_code == 201, created.text
    with db_factory() as db:
        policy = db.get(Policy, created.json()["id"])
        assert policy.blocked_tools == [] and policy.approval_tools == []
