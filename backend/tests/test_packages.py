"""Stored workflow-package v1 publications: read-only history after ADR-0013 step 3.

Publishing v1 is retired (docs/superpowers/plans/2026-11-15-retire-workflow-package-v1.md).
Versions already stored stay readable, metadata and exact canonical bytes, within the
workspace, by `read` authority, and nothing is validated or rewritten on read.
"""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lotuspond_sdk.contract import canonical_package_json, validate_package
from sqlalchemy import event, select

from lotuspond import models
from lotuspond.models import User, now

MANIFEST = Path(__file__).resolve().parents[2] / "contracts/examples/reviewed-action.v1.json"


@pytest.fixture
def manifest():
    return json.loads(MANIFEST.read_text())


@pytest.fixture
def project(client, account):
    response = client.post("/api/v1/projects", json={"name": "Registry history"})
    assert response.status_code == 201
    return response.json()["id"]


def stored(db_factory, workspace, project, manifest, version="1.0.0", created_at=None):
    """A v1 version as the retired publication route stored it."""
    manifest = {**manifest, "version": version}
    content = canonical_package_json(validate_package(manifest))
    with db_factory.begin() as db:
        user = db.scalar(select(User))
        row = models.WorkflowPackageVersion(
            workspace_id=workspace,
            project_id=project,
            package_id=manifest["package_id"],
            version=version,
            schema_version=manifest["schema_version"],
            validator_revision="lotuspond.workflow-package.validator.v1.1",
            canonicalization="lotuspond.workflow-package.canonical-json.v1",
            sha256=hashlib.sha256(content).hexdigest(),
            canonical_content=content,
            content_bytes=len(content),
            publisher_id=user.id,
            publisher_kind="user",
            **({"created_at": created_at} if created_at else {}),
        )
        db.add(row)
        db.flush()
        return row.id, content


def test_publishing_v1_is_retired(client, project, manifest):
    response = client.post(f"/api/v1/projects/{project}/packages", json=manifest)
    assert response.status_code == 405
    assert client.get("/api/v1/package-contracts").status_code == 404


def test_stored_versions_stay_readable_byte_for_byte(
    client, account, project, manifest, db_factory
):
    package_id, content = stored(db_factory, account, project, manifest)
    item = client.get(f"/api/v1/packages/{package_id}").json()
    assert item["sha256"] == hashlib.sha256(content).hexdigest()
    assert item["content_bytes"] == len(content)
    assert item["publisher_kind"] == "user"
    assert "canonical_content" not in item
    body = client.get(f"/api/v1/packages/{package_id}/content")
    assert body.content == content
    assert body.headers["x-package-sha256"] == item["sha256"]
    assert body.headers["etag"] == '"' + item["sha256"] + '"'
    assert body.headers["cache-control"] == "no-store"
    listing = client.get("/api/v1/packages", params={"project_id": project}).json()
    assert listing == {"items": [item], "next": None}
    # Metadata never carries the manifest's private text.
    assert manifest["name"] not in json.dumps(listing)


def test_stored_versions_are_scoped_to_their_workspace(
    client, account, project, manifest, app, db_factory
):
    package_id, _ = stored(db_factory, account, project, manifest)
    with TestClient(app, headers={"origin": "http://testserver"}) as foreign:
        registered = foreign.post(
            "/api/v1/auth/register",
            json={
                "email": "foreign@example.com",
                "name": "Foreign",
                "password": "a-valid-test-password",
            },
        )
        foreign.headers["x-lotuspond-workspace"] = registered.json()["workspaceId"]
        with db_factory.begin() as db:
            db.scalar(
                select(User).where(User.email == "foreign@example.com")
            ).is_platform_admin = True
        assert foreign.get("/api/v1/packages", params={"project_id": project}).status_code == 404
        assert foreign.get(f"/api/v1/packages/{package_id}").status_code == 404
        assert foreign.get(f"/api/v1/packages/{package_id}/content").status_code == 404


@pytest.mark.parametrize("scopes,status", [(["read"], 200), (["write"], 403)])
def test_reading_history_needs_read(
    client, account, project, manifest, app, db_factory, scopes, status
):
    stored(db_factory, account, project, manifest)
    key = client.post("/api/v1/api-keys", json={"name": "History", "scopes": scopes}).json()["key"]
    with TestClient(app, headers={"authorization": f"Bearer {key}"}) as caller:
        response = caller.get("/api/v1/packages", params={"project_id": project})
        assert response.status_code == status


def test_history_pages_are_stable_and_bound_to_their_project(
    client, account, project, manifest, db_factory
):
    moment = now()
    ids = [
        stored(db_factory, account, project, manifest, version, created_at=moment)[0]
        for version in ["1.0.0", "1.1.0", "1.2.0"]
    ]
    page = client.get("/api/v1/packages", params={"project_id": project, "limit": 1}).json()
    seen = [page["items"][0]["id"]]
    other = client.post("/api/v1/projects", json={"name": "Cursor binding"}).json()["id"]
    assert (
        client.get(
            "/api/v1/packages", params={"project_id": other, "cursor": page["next"]}
        ).status_code
        == 422
    )
    while page["next"]:
        page = client.get(
            "/api/v1/packages", params={"project_id": project, "limit": 1, "cursor": page["next"]}
        ).json()
        seen.extend(item["id"] for item in page["items"])
    assert seen == sorted(ids, reverse=True)
    for parameters in [{"limit": 101}, {"limit": 0}, {"cursor": "bad"}, {"cursor": "x" * 1025}]:
        assert (
            client.get("/api/v1/packages", params={"project_id": project, **parameters}).status_code
            == 422
        )
    assert client.get("/api/v1/packages").status_code == 422


@pytest.mark.parametrize("view", ["list", "metadata"])
def test_metadata_reads_do_not_fetch_canonical_content(
    client, account, project, manifest, engine, db_factory, view
):
    package_id, content = stored(db_factory, account, project, manifest)
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT ") and "FROM workflow_package_versions" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = (
            client.get("/api/v1/packages", params={"project_id": project})
            if view == "list"
            else client.get(f"/api/v1/packages/{package_id}")
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert len(statements) == 1, "Metadata must not lazy-load content in a second query"
    assert "canonical_content" not in statements[0].partition("FROM")[0]
    assert client.get(f"/api/v1/packages/{package_id}/content").content == content
