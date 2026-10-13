from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from lotuspond.models import ApiKey, now
from lotuspond.schema_version import SCHEMA_VERSION


def test_http_body_limit_and_readiness(client, account):
    response = client.post(
        "/api/v1/projects",
        content=b"x" * (2 * 1024 * 1024 + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert (
        client.post(
            "/api/v1/projects",
            content="name=hello",
            headers={"content-type": "application/x-www-form-urlencoded"},
        ).status_code
        == 415
    )
    assert client.get("/api/v1/ready").json()["schema_version"] == SCHEMA_VERSION
    assert client.get("/api/v1/no-such-api").status_code == 404
    changelog = client.get("/api/v1/changelog").json()["items"]
    assert any(item["id"] == "0.2.0" for item in changelog)
    assert len({item["id"] for item in changelog}) == len(changelog)
    assert all(item["title"] and item["date"] and item["description"] for item in changelog)


def test_key_expiry_revocation_and_workspace_header_ignored(client, account, db_factory, app):
    key = client.post("/api/v1/api-keys", json={"name": "Short lived", "scopes": ["read"]}).json()
    with TestClient(
        app,
        headers={
            "authorization": f"Bearer {key['key']}",
            "x-lotuspond-workspace": "not-the-workspace",
        },
    ) as service:
        assert service.get("/api/v1/projects").status_code == 200
        with db_factory.begin() as db:
            row = db.scalar(select(ApiKey).where(ApiKey.workspace_id == account))
            row.expires_at = now() - timedelta(seconds=1)
        assert service.get("/api/v1/projects").status_code == 401
    created = client.post("/api/v1/api-keys", json={"name": "Revoked", "scopes": ["read"]}).json()
    identifier = next(
        row["id"]
        for row in client.get("/api/v1/api-keys").json()["items"]
        if row["name"] == "Revoked"
    )
    assert client.post(f"/api/v1/api-keys/{identifier}/revoke", json={}).status_code == 200
    with TestClient(app, headers={"authorization": f"Bearer {created['key']}"}) as service:
        assert service.get("/api/v1/projects").status_code == 401
