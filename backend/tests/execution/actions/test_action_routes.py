"""E05 Task 11: action projections and resolutions over the customer API."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.models import AuditEvent

from ..activity_fixtures import activity as activity  # noqa: F401
from ..api_fixtures import key
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .action_api_fixtures import action_api as action_api  # noqa: F401
from .action_api_fixtures import session_for
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import intent_row
from .readback_fixtures import commands, execution, pending_readback, readbacks
from .test_resolution import resolving as resolving  # noqa: F401
from .test_resolution import unknown_five_times
from .test_tasks import tasks as tasks  # noqa: F401


def get(h, path, headers):
    return h.client.get(h.M + path, headers=headers)


def post(h, path, body, headers):
    return h.client.post(h.M + path, json=body, headers=headers)


def test_actions_are_listed_and_read_as_metadata(action_api):
    h = action_api
    execution_id = h.reviewed("listed")
    action_id = intent_row(h, execution_id).action_id
    _, viewer = session_for(h, "viewer")
    for headers in (h.owner, viewer, key(h, ["read"])):
        page = get(h, f"/executions/{execution_id}/actions", headers)
        assert page.status_code == 200, page.text
        [item] = page.json()["items"]
        assert page.json()["next_after_id"] is None
        assert (item["action_id"], item["authorization"], item["outcome"]) == (
            action_id,
            "approved",
            "unknown",
        )
        one = get(h, f"/executions/{execution_id}/actions/{action_id}", headers)
        assert one.status_code == 200 and one.json() == item
        assert "R-100" not in one.text and "EUR" not in one.text  # no proposal values
    missing = "00000000-0000-4000-8000-000000000000"
    assert get(h, f"/executions/{execution_id}/actions/{missing}", h.owner).status_code == 404
    assert get(h, f"/executions/{missing}/actions", h.owner).status_code == 404
    assert get(h, f"/executions/{execution_id}/actions?limit=101", h.owner).status_code == 400
    assert get(h, f"/executions/{execution_id}/actions?state=x", h.owner).status_code == 400


def test_resolution_needs_an_owner_session_and_is_audited_once(action_api):
    h = action_api
    execution_id, _ = unknown_five_times(h, "resolved")
    action_id = intent_row(h, execution_id).action_id
    path = f"/executions/{execution_id}/actions/{action_id}/resolution"
    body = {"resolution_id": "r-route", "kind": "readback", "evidence": None}
    for role in ("editor", "viewer"):
        assert post(h, path, body, session_for(h, role)[1]).status_code == 403, role
    assert post(h, path, body, key(h, ["execute", "read", "write"])).status_code == 403
    for _ in range(2):  # an equal retry replays the first resolution
        response = post(h, path, body, h.owner)
        assert response.status_code == 200, response.text
        assert response.json()["action_id"] == action_id
    with Session(h.engine) as db:
        rows = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "executions.actions.resolved",
                    AuditEvent.resource_id == action_id,
                )
            )
        )
    assert len(rows) == 1
    assert (rows[0].details["request_id"], rows[0].details["kind"]) == ("r-route", "readback")
    assert rows[0].details["execution_id"] == execution_id
    # One more bounded read-back, never a resend.
    assert execution(h, execution_id).state == "running"
    assert pending_readback(h, execution_id) is not None and len(readbacks(h, execution_id)) == 6
    assert len(commands(h, execution_id)) == 1
    # A conflicting resolution under the same identifier is refused.
    conflicting = {**body, "kind": "close_unknown"}
    assert post(h, path, conflicting, h.owner).status_code == 409


def test_another_environment_finds_nothing(action_api):
    h = action_api
    execution_id = h.reviewed("scoped", decide=False)
    action_id = intent_row(h, execution_id).action_id
    other = h.M.replace(h.scope.environment_id, "00000000-0000-4000-8000-000000000000")
    task = h.client.get(h.M + "/human-tasks", headers=h.owner).json()["items"][0]["id"]
    for path in (
        f"/executions/{execution_id}/actions",
        f"/executions/{execution_id}/actions/{action_id}",
        f"/human-tasks/{task}",
        f"/human-tasks/{task}/proposal",
    ):
        assert h.client.get(other + path, headers=h.owner).status_code == 404, path


def test_without_action_services_the_routes_answer_unavailable(action_api):
    """Production composes interpreter v1 only: its action routes refuse with 503,
    and an unauthenticated runtime still learns nothing before a 401."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from lotuspond.app import create_app

    from ..api_fixtures import api_settings

    h = action_api
    services = replace(h.services, actions=None)
    application = create_app(
        api_settings(), engine=h.engine, fleet_dependencies=h.fleet, execution_services=services
    )
    with TestClient(application, headers={"origin": "http://testserver"}) as client:
        for path in ("/human-tasks", "/executions/00000000-0000-4000-8000-000000000000/actions"):
            response = client.get(h.M + path, headers=h.owner)
            assert (response.status_code, response.json()["error"]) == (
                503,
                "authority_unavailable",
            ), path
        bare = {"content-type": "application/json"}
        assert (
            client.post("/api/v1/runtime/actions/poll", content=b"{}", headers=bare).status_code
            == 401
        )
        wire = {**h.wire, "content-type": "application/json"}
        response = client.post("/api/v1/runtime/actions/poll", content=b"{}", headers=wire)
        assert response.status_code == 503
