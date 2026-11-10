"""Task 12: customer execution routes follow roles, key scopes and current authority."""

from lotuspond_sdk.executable.signing import sign_executable_package
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.models import (
    AuditEvent,
    ExecutableDependency,
    ExecutionDeploymentRevision,
    Membership,
    User,
    new_id,
)

from .activity_fixtures import activity as activity
from .api_fixtures import api as api
from .api_fixtures import key, login, member
from .control_fixtures import controlled as controlled
from .control_fixtures import until
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_deployments import deployment_request
from .test_progression import flows as flows
from .test_publication import example
from .test_publication import harness as harness
from .test_sweeper import wait_of


def package_body(h, n=0):
    raw = example("minimal")
    raw["package_id"] = f"route-minimal-{n}"
    return {
        "package": raw,
        "dependency_ids": [],
        "signature": sign_executable_package(raw, h.key).model_dump(mode="json"),
    }


def post(h, path, body, headers, idempotency=None):
    headers = dict(headers)
    if idempotency is not None:
        headers["idempotency-key"] = idempotency
    return h.client.post(h.M + path, json=body, headers=headers)


def get(h, path, headers):
    return h.client.get(h.M + path, headers=headers)


def deployment_of(h, revision_id):
    with Session(h.engine) as db:
        return db.get(ExecutionDeploymentRevision, revision_id).deployment_id


def start_body(revision_id):
    return {"deployment_revision_id": revision_id, "input": None}


def test_route_role_scope_matrix(api):
    h = api
    admin, editor, viewer = (member(h, role) for role in ("admin", "editor", "viewer"))
    sessions = {
        "owner": h.owner,
        "admin": login(h, admin),
        "editor": login(h, editor),
        "viewer": login(h, viewer),
    }
    revision = h.deploy("waits", "waits.policy")
    # Packages: owners, admins and editors publish (Task 3's publisher rule); viewers never.
    published = {}
    for n, (role, headers) in enumerate(sessions.items()):
        response = post(h, "/executable-packages", package_body(h, n), headers, f"p-{role}")
        assert response.status_code == (403 if role == "viewer" else 201), (role, response.text)
        if response.status_code == 201:
            published[role] = response.json()["id"]
    # Deployment publication and activation: owners and admins only.
    request = deployment_request(h, published["owner"]).model_dump(mode="json")
    for role, headers in sessions.items():
        response = post(h, "/deployments", request, headers, f"d-{role}")
        assert response.status_code == (201 if role in ("owner", "admin") else 403), role
    deployment = deployment_of(h, revision)
    for role, headers in sessions.items():
        response = post(
            h, f"/deployments/{deployment}/status", {"status": "enabled"}, headers, f"s-{role}"
        )
        assert response.status_code == (200 if role in ("owner", "admin") else 403), role
    # Executions: editors and above start enabled work; viewers only read.
    started = {}
    for role, headers in sessions.items():
        response = post(h, "/executions", start_body(revision), headers, f"x-{role}")
        assert response.status_code == (403 if role == "viewer" else 201), role
        if response.status_code == 201:
            started[role] = response.json()["id"]
    for headers in sessions.values():
        assert get(h, f"/executions/{started['editor']}", headers).status_code == 200
        assert get(h, "/executions", headers).status_code == 200

    def control(execution_id, headers, request_id):
        body = {"request_id": request_id, "action": "pause"}
        return post(h, f"/executions/{execution_id}/control", body, headers)

    # Editors control their own executions; owners and admins any in scope.
    assert control(started["owner"], sessions["editor"], "e-1").status_code == 403
    assert control(started["editor"], sessions["editor"], "e-2").status_code == 200
    assert control(started["editor"], sessions["admin"], "a-1").status_code == 200
    assert control(started["owner"], sessions["viewer"], "v-1").status_code == 403
    # Reconciliation: owners and admins (here refused only because nothing is fenced).
    for role, headers in sessions.items():
        response = post(h, f"/executions/{started['owner']}/reconciliation", {}, headers)
        assert response.status_code == (409 if role in ("owner", "admin") else 403), role
    # API keys need the explicit scope; the evaluation `run` scope grants nothing here.
    evaluation_run_scope_response = post(
        h, "/executions", start_body(revision), key(h, ["read", "run", "write"]), "k-run"
    )
    assert evaluation_run_scope_response.status_code == 403
    assert post(h, "/executions", start_body(revision), key(h, ["execute"]), "k-x").status_code == (
        201
    )
    reader = key(h, ["read"])
    assert get(h, "/executions", reader).status_code == 200
    assert post(h, "/executions", start_body(revision), reader, "k-read").status_code == 403
    writer = key(h, ["write"])
    assert post(h, "/executable-packages", package_body(h, 9), writer, "k-w").status_code == 201
    # A key never manages or reconciles, whatever its scopes.
    everything = key(h, ["execute", "execution_signal", "read", "run", "write"])
    assert post(h, "/deployments", request, everything, "k-d").status_code == 403
    response = post(h, f"/executions/{started['owner']}/reconciliation", {}, everything)
    assert response.status_code == 403
    # A key carries its creator's current role.
    editor_key = key(h, ["execute"], editor)
    assert control(started["owner"], editor_key, "k-e1").status_code == 403
    assert control(started["editor"], editor_key, "k-e2").status_code == 200
    with Session(h.engine) as db, db.begin():
        db.get(Membership, (h.scope.workspace_id, editor)).role = "viewer"
    assert post(h, "/executions", start_body(revision), editor_key, "k-gone").status_code == 403
    # Platform administration never supplies implicit customer access.
    operator = new_id()
    with Session(h.engine) as db, db.begin():
        db.add(
            User(
                id=operator,
                email=f"{operator}@example.com",
                name="operator",
                password_hash="x",
                is_platform_admin=True,
            )
        )
    assert get(h, "/executions", login(h, operator)).status_code == 403
    # Each mutation is audited with fixed identifiers only.
    with Session(h.engine) as db:
        actions = set(
            db.scalars(
                select(AuditEvent.action).where(AuditEvent.workspace_id == h.scope.workspace_id)
            )
        )
    assert {
        "executions.packages.published",
        "executions.deployments.created",
        "executions.deployments.status",
        "executions.started",
        "executions.controlled",
    } <= actions


def test_runtime_credentials_cannot_enter_customer_routes(api):
    h = api
    runtime_token = {"authorization": h.runtime["authorization"]}
    # A runtime token or session is not a customer credential.
    assert get(h, "/executions", runtime_token).status_code == 401
    assert get(h, "/executions", h.runtime).status_code == 401
    assert get(h, "/executions", {**h.owner, **h.runtime}).status_code == 401
    session = {"x-lotuspond-runtime-session": h.runtime["x-lotuspond-runtime-session"]}
    assert get(h, "/executions", {**h.owner, **session}).status_code == 401
    # And customer credentials never enter the runtime routes.
    for headers in (h.owner, key(h, ["execute", "read", "write"])):
        response = h.client.post("/api/v1/runtime/activities/poll", json={}, headers=headers)
        assert response.status_code == 401
    # A runtime credential without its current session cannot poll either.
    response = h.client.post("/api/v1/runtime/activities/poll", json={}, headers=runtime_token)
    assert response.status_code == 401


def test_human_form_rejects_api_key(api):
    h = api
    # The callback keeps its hour: a 3 s expiry lapsed before the signal on a slow host.
    execution_id = h.start("waits_fast_timer", None, "waits.policy")
    h.run()
    until(h, wait_of(h, execution_id, "pause").deadline_ms)
    h.sweep()
    h.run()
    callback = wait_of(h, execution_id, "callback")
    # An external signal may come with an `execution_signal` key.
    signal = {"signal_id": "callback", "wait_id": callback.id, "payload": 7}
    signaller = key(h, ["execution_signal"])
    for _ in range(2):  # an equal retry returns the same receipt and is audited once
        response = post(h, f"/executions/{execution_id}/signals", signal, signaller)
        assert response.status_code == 200, response.text
    with Session(h.engine) as db:
        actions = db.scalars(select(AuditEvent.action).where(AuditEvent.resource_id == callback.id))
        assert list(actions) == ["executions.signalled"]
    h.run()
    review = wait_of(h, execution_id, "review")
    form = {"signal_id": "form", "wait_id": review.id, "payload": {"approved": True}}
    # A human form is a person's decision: never a key's, whatever its scopes.
    every_scope = key(h, ["execute", "execution_signal", "read", "run", "write"])
    human_form_key_response = post(h, f"/executions/{execution_id}/signals", form, every_scope)
    assert human_form_key_response.status_code == 403
    assert post(h, f"/executions/{execution_id}/signals", form, h.owner).status_code == 200
    h.run()
    detail = get(h, f"/executions/{execution_id}", h.owner).json()
    assert detail["state"] == "succeeded"


def test_orchestrator_outage_keeps_evidence_available(api, monkeypatch):
    h = api
    revision = h.deploy("waits", "waits.policy")
    execution_id = post(h, "/executions", start_body(revision), h.owner, "start").json()["id"]
    h.run()
    response = post(h, "/executable-packages", package_body(h), h.owner, "route-publish")
    assert response.status_code == 201, response.text
    published = response.json()["id"]
    # No orchestrator or sweeper runs from here on, and every provider is out:
    # recovery authority, entitlement and publisher trust.
    h.fleet_recovery.available = False

    class Lost:
        def current(self, *args):
            raise RuntimeError("PRIVATE_PROVIDER_OUTAGE")

    h.budgets.entitlement = Lost()
    monkeypatch.setattr(h.authority, "current", Lost().current)
    evidence_during_orchestrator_outage = get(h, f"/executions/{execution_id}", h.owner)
    assert evidence_during_orchestrator_outage.status_code == 200
    for path in (
        "/executions",
        f"/executions/{execution_id}/events",
        f"/executions/{execution_id}/waits",
        "/deployments",
        f"/deployments/{deployment_of(h, revision)}",
        "/executable-packages",
        f"/executable-packages/{published}",
        f"/executable-packages/{published}/content",
    ):
        assert get(h, path, h.owner).status_code == 200, path
    # New work needs the providers; withdrawing authority never does.
    assert post(h, "/executions", start_body(revision), h.owner, "later").status_code == 503
    response = post(
        h,
        f"/deployments/{deployment_of(h, revision)}/status",
        {"status": "disabled"},
        h.owner,
        "withdraw",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"


def test_package_content_and_inventory_are_exact_and_scoped(api):
    h = api
    first = post(h, "/executable-packages", package_body(h, 1), h.owner, "one")
    assert first.status_code == 201
    # An equal retry under the same key returns the same publication.
    again = post(h, "/executable-packages", package_body(h, 1), h.owner, "one")
    assert again.status_code in (200, 201) and again.json() == first.json()
    publication = first.json()
    content = get(h, f"/executable-packages/{publication['id']}/content", h.owner)
    from hashlib import sha256

    assert sha256(content.content).hexdigest() == publication["package_sha256"]
    with Session(h.engine) as db:
        assert (
            db.scalar(
                select(ExecutableDependency).where(
                    ExecutableDependency.publication_id == publication["id"]
                )
            )
            is None
        )


def context_body(h, **changes):
    from lotuspond.fleet.authority import database_now_ms

    with Session(h.engine) as db:
        now = database_now_ms(db)
    return {
        "content": {"fact": "synthetic"},
        "citation": None,
        "classification": "synthetic",
        "observed_at_ms": now,
        "expires_at_ms": now + 3_600_000,
        "retain_until_ms": now + 3_600_000,
        "access_policy_id": "fixture-access",
        "key_id": "fixture",
        **changes,
    }


def test_context_sources_and_artifacts_follow_manage_and_read(api):
    h = api
    editor, viewer = member(h, "editor"), member(h, "viewer")
    body = context_body(h)
    # Owners and admins register context; editors and keys never.
    assert post(h, "/context-sources", body, login(h, editor), "c-e").status_code == 403
    assert post(h, "/context-sources", body, key(h, ["write", "execute"]), "c-k").status_code == 403
    first = post(h, "/context-sources", body, h.owner, "c-1")
    assert first.status_code == 201, first.text
    source = first.json()
    # An equal retry under its key returns the same registration.
    assert post(h, "/context-sources", body, h.owner, "c-1").json() == source
    version = post(
        h,
        f"/context-sources/{source['source_id']}/versions",
        {**body, "version": 2},
        h.owner,
        "c-2",
    )
    assert version.status_code == 201 and version.json()["version"] == 2
    # Artifacts are read under current access, by any reader; nothing else is served.
    value = source["value"]
    path = f"/artifacts/{value['id']}/versions/{value['version']}"
    assert get(h, path, login(h, viewer)).json() == {"fact": "synthetic"}
    assert get(h, f"/artifacts/{value['id']}/versions/2", h.owner).status_code == 404
    h.access.allowed = False
    assert get(h, path, h.owner).status_code == 403
    h.access.allowed = True
    revoked = post(h, f"/context-sources/{source['source_id']}/revoke", {}, h.owner, "c-r")
    assert revoked.json() == {"source_id": source["source_id"], "status": "revoked"}
    assert get(h, path, h.owner).status_code == 403


def test_events_and_waits_are_metadata_in_order(api):
    h = api
    revision = h.deploy("waits", "waits.policy")
    execution_id = post(h, "/executions", start_body(revision), h.owner, "w-1").json()["id"]
    h.run()
    events = get(h, f"/executions/{execution_id}/events?limit=1", h.owner).json()
    assert [item["sequence"] for item in events["items"]] == [1]
    assert events["items"][0]["code"] == "started" and events["next_after_sequence"] == 1
    rest = get(h, f"/executions/{execution_id}/events?after_sequence=1", h.owner).json()
    sequences = [item["sequence"] for item in rest["items"]]
    assert sequences == sorted(sequences) and sequences[0] == 2
    # Codes and references only: no event content is served.
    assert set(rest["items"][0]) == {
        "sequence",
        "family",
        "code",
        "occurred_at_ms",
        "attempt_id",
        "application_state",
    }
    waits = get(h, f"/executions/{execution_id}/waits", h.owner).json()
    (pause,) = waits["items"]
    assert (pause["occurrence_path"], pause["state"]) == ("pause", "open")
    assert pause["deadline_ms"] == wait_of(h, execution_id, "pause").deadline_ms


def test_keys_with_the_new_execution_scopes_are_issued_and_honoured(api):
    h = api
    revision = h.deploy("waits", "waits.policy")
    response = h.client.post(
        "/api/v1/api-keys",
        json={"name": "executor", "scopes": ["execute", "execution_signal", "read"]},
        headers=h.owner,
    )
    assert response.status_code == 201, response.text
    issued = {"authorization": f"Bearer {response.json()['key']}"}
    assert post(h, "/executions", start_body(revision), issued, "issued").status_code == 201


def test_openapi_names_production_executions_and_waits(api):
    h = api
    schema = h.client.app.openapi()
    prefix = "/api/v1/projects/{project_id}/environments/{environment_id}"
    operations = [
        operation
        for path, methods in schema["paths"].items()
        if path.startswith(prefix + "/executions")
        for operation in methods.values()
    ]
    assert operations and all("production" in operation["operationId"] for operation in operations)
    assert any("wait" in operation["operationId"] for operation in operations)
    # Nothing under the execution API is named for evaluation runs.
    assert not any(
        "run" in segment.split("_")
        for path, methods in schema["paths"].items()
        if path.startswith(prefix + "/executions")
        for operation in methods.values()
        for segment in [operation["operationId"]]
    )
