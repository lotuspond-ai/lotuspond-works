"""Registry boundaries and immutable-content acceptance tests."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from lotuspond import models
from lotuspond.models import ApiKey, AuditEvent, Membership, User, Workspace, now
from lotuspond.package_contract import canonical_package_json, validate_package


@pytest.fixture
def manifest():
    return json.loads(
        (
            Path(__file__).resolve().parents[2] / "contracts/examples/reviewed-action.v1.json"
        ).read_text()
    )


@pytest.fixture
def project(client, account):
    response = client.post("/api/v1/projects", json={"name": "Registry test"})
    assert response.status_code == 201
    return response.json()["id"]


def publish(client, project, manifest, key=None):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(f"/api/v1/projects/{project}/packages", json=manifest, headers=headers)


def test_publication_exact_content_and_metadata(client, project, manifest, db_factory):
    response = publish(client, project, manifest, "first")
    assert response.status_code == 201, response.text
    item = response.json()
    expected = canonical_package_json(validate_package(manifest))
    assert item["sha256"] == hashlib.sha256(expected).hexdigest()
    assert item["content_bytes"] == len(expected)
    assert item["schema_version"] == manifest["schema_version"]
    assert item["validator_revision"] == "lotuspond.workflow-package.validator.v1.1"
    assert item["canonicalization"] == "lotuspond.workflow-package.canonical-json.v1"
    assert item["publisher_kind"] == "user"
    assert "canonical_content" not in item
    content = client.get(f"/api/v1/packages/{item['id']}/content")
    assert content.content == expected
    assert json.loads(content.content)["secret_refs"] == manifest["secret_refs"]
    assert content.headers["x-package-sha256"] == item["sha256"]
    assert content.headers["x-package-canonicalization"] == item["canonicalization"]
    assert content.headers["etag"] == '"' + item["sha256"] + '"'
    assert content.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/packages/{item['id']}").json() == item
    listing = client.get("/api/v1/packages", params={"project_id": project}).json()
    assert listing == {"items": [item], "next": None}
    with db_factory() as db:
        audits = list(
            db.scalars(select(AuditEvent).where(AuditEvent.action == "packages.published"))
        )
        assert len(audits) == 1
        assert set(audits[0].details) == {"project_id", "package_id", "version", "sha256"}
        assert audits[0].details["sha256"] == item["sha256"]
    for private in [
        manifest["name"],
        next(n["instructions"] for n in manifest["nodes"] if n["kind"] == "agent"),
    ]:
        assert private not in json.dumps(listing)
        assert private not in client.get("/api/v1/audit-events").text


def test_immutable_replay_keys_and_project_binding(client, project, manifest, db_factory):
    first = publish(client, project, manifest, "replay")
    assert first.status_code == 201
    item = first.json()
    for key in [None, "replay", "second"]:
        replay = publish(client, project, manifest, key)
        assert replay.status_code == 200
        assert replay.json() == item
    changed = copy.deepcopy(manifest)
    changed["name"] = "Changed private content"
    assert publish(client, project, changed).status_code == 409
    changed["version"] = "2.0.0"
    assert publish(client, project, changed, "replay").status_code == 409
    other = client.post("/api/v1/projects", json={"name": "Other"}).json()["id"]
    assert publish(client, other, manifest, "replay").status_code == 409
    assert client.get("/api/v1/packages", params={"project_id": other}).json()["items"] == []
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(models.WorkflowPackageVersion)) == 1
        assert db.scalar(select(func.count()).select_from(models.PackagePublicationRequest)) == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "packages.published")
            )
            == 1
        )


def test_reads_and_replays_preserve_admission_revision(client, project, manifest, monkeypatch):
    first = publish(client, project, manifest)
    assert first.status_code == 201
    from lotuspond import packages

    monkeypatch.setattr(packages, "VALIDATOR_REVISION", "future-validator")
    monkeypatch.setattr(packages, "CANONICALIZATION", "future-canonicalizer")
    monkeypatch.setattr(
        packages,
        "validate_package",
        lambda _: (_ for _ in ()).throw(AssertionError("read revalidated")),
    )
    item = first.json()
    assert client.get(f"/api/v1/packages/{item['id']}").json() == item
    assert (
        client.get(f"/api/v1/packages/{item['id']}/content").headers["x-package-canonicalization"]
        == item["canonicalization"]
    )
    monkeypatch.setattr(packages, "validate_package", validate_package)
    for key in [None, "new-replay-key", "new-replay-key"]:
        replay = publish(client, project, manifest, key)
        assert replay.status_code == 200
        assert replay.json() == item


def test_cross_tenant_operations_and_operator_no_bypass(client, project, manifest, app, db_factory):
    first = publish(client, project, manifest, "tenant-key")
    assert first.status_code == 201
    item = first.json()
    with TestClient(app, headers={"origin": "http://testserver"}) as foreign:
        registered = foreign.post(
            "/api/v1/auth/register",
            json={
                "email": "foreign@example.com",
                "name": "Foreign",
                "password": "a-valid-test-password",
            },
        )
        workspace = registered.json()["workspaceId"]
        foreign.headers["x-lotuspond-workspace"] = workspace
        with db_factory.begin() as db:
            db.scalar(
                select(User).where(User.email == "foreign@example.com")
            ).is_platform_admin = True
        assert publish(foreign, project, manifest).status_code == 404
        assert foreign.get("/api/v1/packages", params={"project_id": project}).status_code == 404
        assert foreign.get(f"/api/v1/packages/{item['id']}").status_code == 404
        assert foreign.get(f"/api/v1/packages/{item['id']}/content").status_code == 404
        local = foreign.post("/api/v1/projects", json={"name": "Local"}).json()["id"]
        assert publish(foreign, local, manifest, "tenant-key").status_code == 201
    forged = copy.deepcopy(manifest)
    forged["workspace_id"] = workspace
    assert publish(client, project, forged).status_code == 422


@pytest.mark.parametrize(
    "role,status", [("owner", 201), ("admin", 201), ("editor", 201), ("viewer", 403)]
)
def test_roles(client, account, project, manifest, db_factory, role, status):
    with db_factory.begin() as db:
        db.scalar(select(Membership).where(Membership.workspace_id == account)).role = role
    assert publish(client, project, manifest).status_code == status


@pytest.mark.parametrize(
    "scopes,post,get", [(["read"], 403, 200), (["write"], 201, 403), (["read", "write"], 201, 200)]
)
def test_exact_key_scopes(client, project, manifest, app, scopes, post, get):
    key = client.post("/api/v1/api-keys", json={"name": "Registry", "scopes": scopes}).json()["key"]
    with TestClient(
        app, headers={"authorization": f"Bearer {key}", "x-lotuspond-workspace": "forged"}
    ) as caller:
        response = publish(caller, project, manifest)
        assert response.status_code == post
        assert caller.get("/api/v1/packages", params={"project_id": project}).status_code == get
        if post == 201:
            assert response.json()["publisher_kind"] == "api_key"


@pytest.mark.parametrize(
    "state,status",
    [("suspended", 403), ("expired", 401), ("revoked", 401), ("disabled", 401), ("mfa", 403)],
)
def test_current_key_authority(client, account, project, manifest, app, db_factory, state, status):
    key = client.post(
        "/api/v1/api-keys", json={"name": "Authority", "scopes": ["read", "write"]}
    ).json()["key"]
    with db_factory.begin() as db:
        row = db.scalar(select(ApiKey).where(ApiKey.workspace_id == account))
        if state == "suspended":
            db.get(Workspace, account).status = "suspended"
        elif state == "expired":
            row.expires_at = now() - timedelta(seconds=1)
        elif state == "revoked":
            row.revoked_at = now()
        elif state == "disabled":
            db.get(User, row.created_by_user_id).disabled_at = now()
        else:
            import base64

            from pydantic import SecretStr

            app.state.settings.mfa_encryption_key = SecretStr(
                base64.urlsafe_b64encode(b"k" * 32).decode()
            )
            db.get(Workspace, account).require_mfa = True
    with TestClient(app, headers={"authorization": f"Bearer {key}"}) as caller:
        assert publish(caller, project, manifest).status_code == status
        assert caller.get("/api/v1/packages", params={"project_id": project}).status_code == status


@pytest.mark.parametrize(
    "data,code,status",
    [
        (b'{"private":1,"private":2}', "duplicate_key", 422),
        (b'{"private":NaN}', "nonfinite_number", 422),
        (b'{"private":1e999}', "nonfinite_number", 422),
        (b'{"private":"\\ud800"}', "invalid_unicode", 422),
        (b"\xff", "invalid_utf8", 422),
        (b"{", "invalid_json", 422),
        (b"[" * 40 + b"0" + b"]" * 40, "input_too_deep", 422),
        (b"x" * (1024 * 1024 + 1), "body_too_large", 413),
    ],
    ids=["duplicate", "nan", "infinity", "unicode", "utf8", "json", "depth", "size"],
)
def test_raw_admission(client, project, data, code, status):
    result = client.post(
        f"/api/v1/projects/{project}/packages",
        content=data,
        headers={"content-type": "application/json"},
    )
    assert result.status_code == status
    assert "private" not in result.text
    assert code in result.text


@pytest.mark.parametrize("change", ["schema", "extra", "graph", "reference"])
def test_full_validator_fail_closed(client, project, manifest, change):
    if change == "schema":
        manifest["schema_version"] = "lotuspond.workflow-package.v999"
    elif change == "extra":
        manifest["private-secret-key"] = "PRIVATE_VALUE"
    elif change == "graph":
        manifest["edges"][0]["target"] = "missing"
    else:
        next(n for n in manifest["nodes"] if n["kind"] == "agent")["model_ref"] = "missing"
    result = publish(client, project, manifest)
    assert result.status_code == 422
    assert "PRIVATE_VALUE" not in result.text
    assert "private-secret-key" not in result.text


@pytest.mark.parametrize("key", ["", "x" * 129, "space key", "nonascii-£"])
def test_bounded_request_key(client, project, manifest, key):
    # HTTP header bytes are latin-1, not arbitrary Unicode.
    headers = {b"idempotency-key": key.encode("latin-1")}
    assert (
        client.post(
            f"/api/v1/projects/{project}/packages", json=manifest, headers=headers
        ).status_code
        == 422
    )


def test_media_origin_encoding_and_no_mutations(client, project, manifest):
    path = f"/api/v1/projects/{project}/packages"
    for media in ["text/plain", "application/jsonp", "x-application/json"]:
        assert (
            client.post(
                path, content=json.dumps(manifest), headers={"content-type": media}
            ).status_code
            == 415
        )
    assert (
        client.post(path, json=manifest, headers={"origin": "https://foreign.example"}).status_code
        == 403
    )
    assert client.post(path, json=manifest, headers={"content-encoding": "gzip"}).status_code == 415
    result = publish(client, project, manifest)
    assert result.status_code == 201
    for method in ["put", "patch", "delete"]:
        assert (
            getattr(client, method)(
                f"/api/v1/packages/{result.json()['id']}",
                **(
                    {"json": {}}
                    if method != "delete"
                    else {"headers": {"content-type": "application/json"}}
                ),
            ).status_code
            == 405
        )


@pytest.mark.parametrize(
    ("configured_limit", "expected_limit"),
    [(1024, 1024), (1048576, 1048576), (2097152, 1048576)],
)
def test_discovered_publication_limit_matches_admission(
    client, project, app, configured_limit, expected_limit
):
    app.state.settings.max_request_bytes = configured_limit
    response = client.get("/api/v1/package-contracts")
    assert response.status_code == 200
    assert response.json()["contracts"][0]["max_package_bytes"] == expected_limit
    manifest = {
        "schema_version": "lotuspond.workflow-package.v1",
        "package_id": "limit-test",
        "version": "1.0.0",
        "name": "Publication limit",
        "entry_node": "start",
        "models": [],
        "connectors": [],
        "secret_refs": [],
        "nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
        "edges": [{"source": "start", "target": "end"}],
    }
    wire = json.dumps(manifest).encode().ljust(expected_limit, b" ")
    url = f"/api/v1/projects/{project}/packages"
    headers = {"content-type": "application/json"}
    accepted = client.post(url, content=wire, headers=headers)
    assert accepted.status_code == 201, accepted.text
    rejected = client.post(url, content=wire + b" ", headers=headers)
    assert rejected.status_code == 413


def test_contracts_and_stable_pagination(client, project, manifest, db_factory, account):
    contracts = client.get("/api/v1/package-contracts")
    assert contracts.status_code == 200
    assert (
        contracts.json()["contracts"][0]["validator_revision"]
        == "lotuspond.workflow-package.validator.v1.1"
    )
    ids = []
    for version in ["1.0.0", "1.1.0", "1.2.0"]:
        manifest["version"] = version
        result = publish(client, project, manifest)
        assert result.status_code == 201
        ids.append(result.json()["id"])
    with db_factory.begin() as db:
        db.query(models.WorkflowPackageVersion).update({"created_at": now()})
    page = client.get("/api/v1/packages", params={"project_id": project, "limit": 1}).json()
    seen = [page["items"][0]["id"]]
    cursor = page["next"]
    other = client.post("/api/v1/projects", json={"name": "Cursor binding"}).json()["id"]
    assert (
        client.get("/api/v1/packages", params={"project_id": other, "cursor": cursor}).status_code
        == 422
    )
    while page["next"]:
        page = client.get(
            "/api/v1/packages", params={"project_id": project, "limit": 1, "cursor": page["next"]}
        ).json()
        seen.extend(item["id"] for item in page["items"])
    assert seen == sorted(ids, reverse=True)
    for parameters in [{"limit": 101}, {"limit": 0}, {"cursor": "bad"}, {"cursor": "x" * 1025}, {}]:
        assert client.get(
            "/api/v1/packages", params={"project_id": project, **parameters}
        ).status_code == (200 if not parameters else 422)
    assert client.get("/api/v1/packages").status_code == 422


def test_direct_cross_workspace_result_pointer_rejected(
    client, account, project, manifest, db_factory, engine
):
    result = publish(client, project, manifest)
    assert result.status_code == 201
    with db_factory.begin() as db:
        owner = db.get(Workspace, account).owner_id
        foreign = Workspace(name="Foreign", slug="foreign-result", owner_id=owner)
        db.add(foreign)
        db.flush()
        foreign_id = foreign.id
    with pytest.raises(IntegrityError), db_factory.begin() as db:
        db.add(
            models.PackagePublicationRequest(
                workspace_id=foreign_id,
                request_key="foreign",
                request_hash="a" * 64,
                package_version_id=result.json()["id"],
            )
        )
    with pytest.raises(IntegrityError), db_factory.begin() as db:
        row = models.WorkflowPackageVersion(
            workspace_id=foreign_id,
            project_id=project,
            package_id="foreign",
            version="1.0.0",
            schema_version=manifest["schema_version"],
            validator_revision="test",
            canonicalization="test",
            sha256="a" * 64,
            canonical_content=b"{}",
            content_bytes=2,
            publisher_id=owner,
            publisher_kind="user",
        )
        db.add(row)


@pytest.mark.parametrize(
    "mode", ["identical", "different-key", "no-key", "content-conflict", "key-project-conflict"]
)
def test_postgres_concurrent_publication(
    app, engine, client, account, project, manifest, db_factory, mode
):
    if engine.dialect.name != "postgresql":
        pytest.skip("Publication contention requires isolated PostgreSQL")
    other = client.post("/api/v1/projects", json={"name": "Concurrent other"}).json()["id"]
    barrier = Barrier(2)
    cookie = client.cookies.get("lotuspond_session")

    def call(index):
        value = copy.deepcopy(manifest)
        if mode == "content-conflict" and index:
            value["name"] = "Competing immutable content"
        target = other if mode == "key-project-conflict" and index else project
        key = f"key-{index}" if mode == "different-key" else "one-key"
        if mode == "no-key":
            key = None
        with TestClient(
            app, headers={"origin": "http://testserver", "x-lotuspond-workspace": account}
        ) as caller:
            caller.cookies.set("lotuspond_session", cookie)
            barrier.wait(timeout=5)
            result = publish(caller, target, value, key)
            return result.status_code

    insert_barrier = Barrier(2)
    table = (
        "package_publication_requests"
        if mode == "key-project-conflict"
        else "workflow_package_versions"
    )
    attempts = []

    def contend(connection, cursor, statement, parameters, context, many):
        if statement.startswith(f"INSERT INTO {table} "):
            attempts.append(statement)
            insert_barrier.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", contend)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, [0, 1]))
    finally:
        event.remove(engine, "before_cursor_execute", contend)
    assert len(attempts) == 2
    assert sorted(results) == (
        [200, 201] if mode in ["identical", "different-key", "no-key"] else [201, 409]
    )
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(models.WorkflowPackageVersion)) == 1
        assert db.scalar(select(func.count()).select_from(models.PackagePublicationRequest)) == (
            0 if mode == "no-key" else 2 if mode == "different-key" else 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "packages.published")
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [None, b"1"])
async def test_stream_budget_before_buffering_with_missing_or_false_length(app, length):
    """Middleware must stop consuming oversized package chunks before routing."""
    headers = [(b"content-type", b"application/json")]
    if length is not None:
        headers.append((b"content-length", length))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/projects/11111111-1111-4111-8111-111111111111/packages",
        "headers": headers,
    }
    messages = iter(
        [
            {"type": "http.request", "body": b"x" * (512 * 1024), "more_body": True},
            {"type": "http.request", "body": b"x" * (512 * 1024 + 1), "more_body": True},
        ]
    )
    consumed = 0
    sent = []

    async def receive():
        nonlocal consumed
        consumed += 1
        return next(messages)

    async def send(message):
        sent.append(message)

    from lotuspond.app import RequestBoundary

    async def unreachable(*args):
        raise AssertionError("Oversized input reached router")

    await RequestBoundary(unreachable, app.state.settings)(scope, receive, send)
    assert consumed == 2
    assert sent[0]["status"] == 413


def test_reordered_and_defaulted_requests_replay(client, project, manifest):
    first = publish(client, project, manifest, "canonical")
    assert first.status_code == 201
    reverse = dict(reversed(list(manifest.items())))
    result = client.post(
        f"/api/v1/projects/{project}/packages",
        content=json.dumps(reverse, indent=4),
        headers={"content-type": "application/json; charset=utf-8", "idempotency-key": "canonical"},
    )
    assert result.status_code == 200
    assert result.json() == first.json()


def test_production_identity_and_session_authority(
    client, account, project, manifest, app, db_factory
):
    from lotuspond.models import LoginSession

    app.state.settings.env = "production"
    assert publish(client, project, manifest).status_code == 403
    with db_factory.begin() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        user.email_verified = True
    assert publish(client, project, manifest).status_code == 201
    with db_factory.begin() as db:
        db.get(Workspace, account).status = "suspended"
    assert publish(client, project, manifest).status_code == 403
    with db_factory.begin() as db:
        db.get(Workspace, account).status = "active"
        db.query(LoginSession).delete()
    assert publish(client, project, manifest).status_code == 401


def test_unverified_production_key_creator(client, account, project, manifest, app, db_factory):
    key = client.post(
        "/api/v1/api-keys", json={"name": "Unverified", "scopes": ["read", "write"]}
    ).json()["key"]
    app.state.settings.env = "production"
    with TestClient(app, headers={"authorization": f"Bearer {key}"}) as caller:
        assert publish(caller, project, manifest).status_code == 403
        with db_factory.begin() as db:
            db.scalar(select(User).where(User.email == "owner@example.com")).email_verified = True
        assert publish(caller, project, manifest).status_code == 201


def test_equal_digest_different_content_still_conflicts(client, project, manifest, monkeypatch):
    first = publish(client, project, manifest)
    assert first.status_code == 201
    from lotuspond import packages

    monkeypatch.setattr(packages, "package_digest", lambda _: first.json()["sha256"])
    manifest["name"] = "Different canonical bytes with forced digest collision"
    assert publish(client, project, manifest).status_code == 409
    content = client.get(f"/api/v1/packages/{first.json()['id']}/content")
    assert b"forced digest collision" not in content.content


@pytest.mark.parametrize("view", ["list", "metadata"])
def test_metadata_reads_do_not_fetch_canonical_content(client, project, manifest, engine, view):
    published = publish(client, project, manifest)
    assert published.status_code == 201
    item = published.json()
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT ") and "FROM workflow_package_versions" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = (
            client.get("/api/v1/packages", params={"project_id": project})
            if view == "list"
            else client.get(f"/api/v1/packages/{item['id']}")
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert response.json() == ({"items": [item], "next": None} if view == "list" else item)
    assert len(statements) == 1, "Metadata must not lazy-load content in a second query"
    assert "canonical_content" not in statements[0].partition("FROM")[0]
    # Content retrieval must still return the original exact bytes and integrity headers.
    content = client.get(f"/api/v1/packages/{item['id']}/content")
    assert content.content == canonical_package_json(validate_package(manifest))
    assert content.headers["x-package-sha256"] == item["sha256"]


async def raw_package_request(app, client, project, manifest, extra_headers):
    body = json.dumps(manifest).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/api/v1/projects/{project}/packages",
        "root_path": "",
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "headers": [
            (b"content-type", b"application/json"),
            (b"origin", b"http://testserver"),
            (b"x-lotuspond-workspace", client.headers["x-lotuspond-workspace"].encode()),
            (b"cookie", f"lotuspond_session={client.cookies.get('lotuspond_session')}".encode()),
            *extra_headers,
        ],
    }
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    try:
        await app(scope, receive, send)
    except UnicodeDecodeError:
        # The prior middleware sent 500 and re-raised while decoding a raw byte.
        pass
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    content = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, content


@pytest.mark.asyncio
async def test_raw_latin1_request_key_is_rejected_safely(app, client, project, manifest):
    status, body = await raw_package_request(
        app, client, project, manifest, [(b"idempotency-key", b"nonascii-\xa3")]
    )
    assert status == 422
    assert json.loads(body)["error"] == "invalid_idempotency_key"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values", [(b"gzip", b"identity"), (b"identity", b"gzip"), (b"identity", b"identity")]
)
async def test_duplicate_wire_content_encoding_rejected(app, client, project, manifest, values):
    status, body = await raw_package_request(
        app, client, project, manifest, [(b"content-encoding", value) for value in values]
    )
    assert status == 415
    assert json.loads(body)["error"] == "unsupported_encoding"
    assert client.get("/api/v1/packages", params={"project_id": project}).json()["items"] == []


def test_registry_normalized_limit_preserves_digest_and_replay(client, project, db_factory):
    from test_package_contract import boundary_package

    from lotuspond.package_contract import MAX_PACKAGE_BYTES

    raw = boundary_package()
    wire = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(wire) == MAX_PACKAGE_BYTES
    response = client.post(
        f"/api/v1/projects/{project}/packages",
        content=wire,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "input_too_large"
    raw["nodes"][1]["instructions"] = raw["nodes"][1]["instructions"][:-576]
    first = publish(client, project, raw, "normalized-boundary")
    assert first.status_code == 201
    content = client.get(f"/api/v1/packages/{first.json()['id']}/content")
    assert len(content.content) == MAX_PACKAGE_BYTES
    assert hashlib.sha256(content.content).hexdigest() == first.json()["sha256"]
    for connector in raw["connectors"]:
        connector["secret_ref"] = None
    assert (
        len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode())
        == MAX_PACKAGE_BYTES
    )
    # Compact bytes keep the wire bound distinct from default client JSON spacing.
    replay = client.post(
        f"/api/v1/projects/{project}/packages",
        content=json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode(),
        headers={"content-type": "application/json", "idempotency-key": "normalized-boundary"},
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(models.WorkflowPackageVersion)) == 1
