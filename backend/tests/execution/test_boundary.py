"""Task 12: header, body and pagination boundaries of the execution APIs."""

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy.orm import Session

from lotuspond.execution.boundary import MAX_BODY_BYTES, ExecutionRequestBoundary
from lotuspond.models import Environment, new_id

from .activity_fixtures import activity as activity
from .api_fixtures import api as api
from .api_fixtures import api_settings
from .control_fixtures import controlled as controlled
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_progression import flows as flows
from .test_publication import harness as harness
from .test_routes import get, post, start_body


def error(response):
    return response.status_code, response.json()["error"]


def test_headers_body_and_pagination_boundaries(api):
    h = api
    revision = h.deploy("waits", "waits.policy")
    started = [
        post(h, "/executions", start_body(revision), h.owner, f"s-{n}").json()["id"]
        for n in range(3)
    ]
    execution_detail = get(h, f"/executions/{started[0]}", h.owner)
    assert execution_detail.status_code == 200
    assert execution_detail.headers["cache-control"] == "no-store"
    # Duplicated authority or framing headers are refused before any decoding.
    for name in ("x-lotuspond-workspace", "authorization", "idempotency-key", "cookie"):
        value = h.owner.get(name, "Bearer lp_x" if name == "authorization" else "x")
        headers = [(k, v) for k, v in h.owner.items() if k != name] + [(name, value)] * 2
        response = h.client.get(h.M + "/executions", headers=headers)
        assert error(response) == (400, "invalid_request"), name
    # A runtime session header never accompanies a customer request.
    response = get(h, "/executions", {**h.owner, "x-lotuspond-runtime-session": "lps_x"})
    assert error(response) == (401, "unauthenticated")
    # Bodies: JSON only, uncompressed, at most 1 MiB; refused with fixed codes.
    response = h.client.post(
        h.M + "/executions",
        content=b"deployment_revision_id=x",
        headers={**h.owner, "content-type": "text/plain", "idempotency-key": "plain"},
    )
    assert error(response) == (400, "invalid_request")
    huge = json.dumps({"deployment_revision_id": revision, "input": "x" * 1048576}).encode()
    response = h.client.post(
        h.M + "/executions",
        content=huge,
        headers={**h.owner, "content-type": "application/json", "idempotency-key": "huge"},
    )
    assert error(response) == (413, "input_limit")
    response = h.client.post(
        h.M + "/executions",
        content=b'{"deployment_revision_id": "a", "deployment_revision_id": "b", "input": null}',
        headers={**h.owner, "content-type": "application/json", "idempotency-key": "dup"},
    )
    assert error(response) == (400, "invalid_request")  # duplicate keys
    # A mutation needs its idempotency key; an equal retry returns the same execution.
    assert error(post(h, "/executions", start_body(revision), h.owner)) == (400, "invalid_request")
    again = post(h, "/executions", start_body(revision), h.owner, "s-0")
    assert again.status_code in (200, 201) and again.json()["id"] == started[0]
    other = post(h, "/executions", {"deployment_revision_id": revision, "input": 1}, h.owner, "s-0")
    assert error(other) == (409, "conflict")
    # Keyset pages: limit 1..100 (default 50), one cursor, nothing else; no counts.
    for query in ("limit=0", "limit=101", "limit=x", "limit=1&limit=2", "offset=1", "after_id="):
        response = h.client.get(h.M + "/executions?" + query, headers=h.owner)
        assert error(response) in ((400, "invalid_request"), (404, "not_found")), query
    first = h.client.get(h.M + "/executions?limit=2", headers=h.owner).json()
    assert len(first["items"]) == 2 and first["next_after_id"] is not None
    assert set(first) == {"items", "next_after_id"}
    rest = h.client.get(
        h.M + f"/executions?limit=2&after_id={first['next_after_id']}", headers=h.owner
    ).json()
    assert rest["next_after_id"] is None
    seen = [item["id"] for item in first["items"] + rest["items"]]
    assert sorted(seen) == sorted(started) and len(set(seen)) == 3
    unknown = h.client.get(h.M + f"/executions?after_id={new_id()}", headers=h.owner)
    assert error(unknown) == (404, "not_found")
    # Cross-scope: another environment's path never reaches this execution.
    elsewhere = new_id()
    with Session(h.engine) as db, db.begin():
        db.add(
            Environment(
                id=elsewhere,
                workspace_id=h.scope.workspace_id,
                project_id=h.scope.project_id,
                name="elsewhere",
                kind="local",
            )
        )
    path = f"/api/v1/projects/{h.scope.project_id}/environments/{elsewhere}/executions/{started[0]}"
    assert error(h.client.get(path, headers=h.owner)) == (404, "not_found")
    path = f"/api/v1/projects/{h.scope.project_id}/environments/{new_id()}/executions"
    assert error(h.client.get(path, headers=h.owner)) == (404, "not_found")
    assert error(get(h, "/executions/not-a-uuid", h.owner)) == (404, "not_found")
    # Errors are no-store too.
    assert unknown.headers["cache-control"] == "no-store"


def test_the_execution_api_is_opt_in(controlled):
    from fastapi.testclient import TestClient

    from lotuspond.app import create_app

    h = controlled
    application = create_app(
        api_settings(fleet_enabled=False, fleet_installation_id=None), engine=h.engine
    )
    with TestClient(application, headers={"origin": "http://testserver"}) as client:
        path = f"/api/v1/projects/{h.scope.project_id}/environments/{h.scope.environment_id}"
        assert client.get(path + "/executions").status_code == 404
        assert client.post("/api/v1/runtime/activities/poll", json={}).status_code == 404


def test_without_providers_only_database_reads_answer(controlled):
    from fastapi.testclient import TestClient

    from lotuspond.app import create_app

    from .api_fixtures import login

    h = controlled
    revision = h.deploy("waits", "waits.policy")
    execution_id = h.start("waits", None, "waits.policy")
    # Production defaults: no execution services are configured.
    application = create_app(api_settings(), engine=h.engine, fleet_dependencies=h.fleet)
    with TestClient(application, headers={"origin": "http://testserver"}) as client:
        owner = login(h, h.actor.user_id)
        base = f"/api/v1/projects/{h.scope.project_id}/environments/{h.scope.environment_id}"
        assert client.get(base + f"/executions/{execution_id}", headers=owner).status_code == 200
        response = client.post(
            base + "/executions",
            json=start_body(revision),
            headers={**owner, "idempotency-key": "nothing"},
        )
        assert error(response) == (503, "authority_unavailable")


def test_execution_services_are_injected_only_in_test_mode(api):
    import pytest

    from lotuspond.app import create_app

    h = api
    with pytest.raises(ValueError):
        create_app(api_settings(env="development"), engine=h.engine, execution_services=h.services)


def test_body_bounds_hold_before_and_while_reading():
    """Both bounds, apart: a declared length is refused unread; a stream, as it grows."""
    reached = []

    async def application(scope, receive, send):
        reached.append(scope["path"])

    def call(headers, messages):
        sent, pending = [], list(messages)

        async def receive():
            assert pending, "the body was read past its bound"
            return pending.pop(0)

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/projects/p/environments/e/executions",
            "headers": [(b"content-type", b"application/json"), *headers],
        }
        boundary = ExecutionRequestBoundary(application, SimpleNamespace(fleet_enabled=True))
        asyncio.run(boundary(scope, receive, send))
        return sent[0]["status"] if sent else None

    declared = [(b"content-length", str(MAX_BODY_BYTES + 1).encode())]
    assert call(declared, []) == 413  # nothing is read
    chunk = {"type": "http.request", "body": b"x" * 65536, "more_body": True}
    stream = [chunk] * (MAX_BODY_BYTES // 65536) + [{**chunk, "body": b"x", "more_body": False}]
    assert call([], stream) == 413  # no declared length: refused as the stream grows
    assert call([], stream[:-1] + [{**chunk, "body": b"", "more_body": False}]) is None
    assert reached == ["/api/v1/projects/p/environments/e/executions"]


def test_quota_and_outage_refusals_keep_closed_codes(api, monkeypatch):
    from fastapi import HTTPException

    h = api
    seen = []

    def refusing(status):
        def check(key, limit, seconds=60):
            seen.append(key)
            raise HTTPException(status, "quota text never escapes")

        return check

    limiter = h.client.app.state.limiter
    monkeypatch.setattr(limiter, "check", refusing(429))
    assert error(get(h, "/executions", h.owner)) == (429, "rate_limited")
    assert error(post(h, "/executions", {}, h.owner, "q-1")) == (429, "rate_limited")
    # Reads and writes spend separate allowances, so polling never starves writes.
    assert [key.rsplit(":", 1)[1] for key in seen] == ["execution-read", "execution-write"]
    # A quota store outage is an outage, never a permission refusal.
    monkeypatch.setattr(limiter, "check", refusing(503))
    assert error(get(h, "/executions", h.owner)) == (503, "authority_unavailable")


def test_an_equal_retry_is_audited_once(api):
    from sqlalchemy import event, func, select

    from lotuspond.models import AuditEvent

    from .test_routes import context_body, package_body

    h = api
    statements = []

    def seen(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    revision = h.deploy("waits", "waits.policy")
    event.listen(h.engine, "before_cursor_execute", seen)
    try:
        started = [post(h, "/executions", start_body(revision), h.owner, "a-1") for _ in range(2)]
    finally:
        event.remove(h.engine, "before_cursor_execute", seen)
    # The service says it replayed; the audit trail is never searched for it.
    assert not [s for s in statements if "FROM audit_events" in s]
    execution_id = started[0].json()["id"]
    assert started[1].json()["id"] == execution_id
    control = {"request_id": "pause-1", "action": "pause"}
    for _ in range(2):
        response = post(h, f"/executions/{execution_id}/control", control, h.owner)
        assert response.status_code == 200, response.text
    published = [post(h, "/executable-packages", package_body(h, 7), h.owner, "a-p") for _ in "ab"]
    package_id = published[0].json()["id"]
    source_id = post(h, "/context-sources", context_body(h), h.owner, "a-c").json()["source_id"]
    for key in ("a-r1", "a-r2"):  # revoking a revoked source changes nothing
        response = post(h, f"/context-sources/{source_id}/revoke", {}, h.owner, key)
        assert response.status_code == 200, response.text

    def audited(action, resource_id):
        with Session(h.engine) as db:
            return db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == action, AuditEvent.resource_id == resource_id)
            )

    assert audited("executions.started", execution_id) == 1
    assert audited("executions.controlled", execution_id) == 1
    assert audited("executions.packages.published", package_id) == 1
    assert audited("executions.context.revoked", source_id) == 1
    # Another request is another event.
    post(h, f"/executions/{execution_id}/control", {**control, "request_id": "pause-2"}, h.owner)
    assert audited("executions.controlled", execution_id) == 2
