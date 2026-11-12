"""E05 Task 11: human task routes follow assignment, sessions and current authority."""

import json

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
from .dispatch_fixtures import intent_row, task_id
from .test_resolution import resolving as resolving  # noqa: F401
from .test_tasks import tasks as tasks  # noqa: F401

EVERY_SCOPE = ["execute", "execution_signal", "read", "run", "write"]


def get(h, path, headers):
    return h.client.get(h.M + path, headers=headers)


def post(h, path, body, headers):
    return h.client.post(h.M + path, json=body, headers=headers)


def under_review(h, key_):
    execution_id = h.reviewed(key_, decide=False)
    return execution_id, task_id(h, execution_id)


def decision(h, execution_id, decision_id="d-1", choice="allow"):
    return {
        "decision_id": decision_id,
        "intent_sha256": intent_row(h, execution_id).intent_sha256,
        "decision": choice,
    }


def audits(h, action, resource_id=None):
    with Session(h.engine) as db:
        query = select(AuditEvent).where(AuditEvent.action == action)
        if resource_id is not None:
            query = query.where(AuditEvent.resource_id == resource_id)
        return list(db.scalars(query))


def test_route_role_matrix(action_api):
    """Owners, admins and editors act per assignment (here: owners and admins);
    viewers read metadata and proposals but never claim."""
    h = action_api
    execution_id, task = under_review(h, "matrix")
    sessions = {role: session_for(h, role)[1] for role in ("admin", "editor", "viewer")}
    sessions["owner"] = h.owner
    for role, headers in sessions.items():
        listed = get(h, "/human-tasks", headers)
        assert listed.status_code == 200, (role, listed.text)
        assert [item["id"] for item in listed.json()["items"]] == [task]
        assert get(h, f"/human-tasks/{task}", headers).json()["state"] == "open"
        proposal = get(h, f"/human-tasks/{task}/proposal", headers)
        assert proposal.status_code == 200, (role, proposal.text)
        assert proposal.json()["intent_sha256"] == intent_row(h, execution_id).intent_sha256
    # The assignment admits owners and admins: an editor and a viewer never claim.
    for role in ("editor", "viewer"):
        refused = post(h, f"/human-tasks/{task}/claim", {}, sessions[role])
        assert refused.status_code == 403, (role, refused.text)
    # Nor decide or release; a viewer is refused every mutation by role, and an
    # editor's submission to a review is the wrong kind of task.
    submission = {"submission_id": "s-1", "payload": {"approved": True}}
    for role, path, body, status in (
        ("editor", "decision", decision(h, execution_id), 403),
        ("editor", "release", {}, 200),  # nobody holds it: nothing changes
        ("editor", "submission", submission, 409),
        ("viewer", "decision", decision(h, execution_id), 403),
        ("viewer", "release", {}, 403),
        ("viewer", "submission", submission, 403),
    ):
        refused = post(h, f"/human-tasks/{task}/{path}", body, sessions[role])
        assert refused.status_code == status, (role, path, refused.text)
    claimed = post(h, f"/human-tasks/{task}/claim", {}, sessions["admin"])
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["claim"] == "caller"
    assert get(h, f"/human-tasks/{task}", h.owner).json()["claim"] == "other"
    # Another holder's live claim conflicts; an owner may release it.
    assert post(h, f"/human-tasks/{task}/claim", {}, h.owner).status_code == 409
    released = post(h, f"/human-tasks/{task}/release", {}, h.owner)
    assert released.status_code == 200 and released.json()["claim"] == "none"
    # The owner started the execution: independence leaves the decision to the admin.
    assert post(h, f"/human-tasks/{task}/claim", {}, sessions["admin"]).status_code == 200
    decided = post(h, f"/human-tasks/{task}/decision", decision(h, execution_id), sessions["admin"])
    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "decided"
    # Each mutation is audited with fixed identifiers only.
    assert [row.action for row in audits(h, "executions.tasks.claimed", task)] == [
        "executions.tasks.claimed"
    ] * 2
    [decided_row] = audits(h, "executions.tasks.decided", task)
    assert decided_row.details["request_id"] == "d-1"
    assert decided_row.details["decision"] == "allow"
    # An unknown task is not found, and paging is keyset only.
    assert (
        get(h, "/human-tasks/" + "0" * 8 + "-0000-4000-8000-" + "0" * 12, h.owner).status_code
        == 404
    )
    assert get(h, "/human-tasks?limit=0", h.owner).status_code == 400
    assert get(h, "/human-tasks?offset=1", h.owner).status_code == 400


def test_keys_never_claim_decide_submit_or_resolve(action_api):
    h = action_api
    execution_id, task = under_review(h, "keyed")
    every = key(h, EVERY_SCOPE)
    assert get(h, "/human-tasks", every).status_code == 200  # a read key may list
    # Proposal values are a person's to read: a key never receives them.
    assert get(h, f"/human-tasks/{task}/proposal", every).status_code == 403
    key_claim_response = post(h, f"/human-tasks/{task}/claim", {}, every)
    assert key_claim_response.status_code == 403
    key_decision_response = post(
        h, f"/human-tasks/{task}/decision", decision(h, execution_id), every
    )
    assert key_decision_response.status_code == 403
    submission = {"submission_id": "s-1", "payload": {"approved": True}}
    assert post(h, f"/human-tasks/{task}/submission", submission, every).status_code == 403
    assert post(h, f"/human-tasks/{task}/release", {}, every).status_code == 403
    action_id = intent_row(h, execution_id).action_id
    resolution = {"resolution_id": "r-1", "kind": "readback", "evidence": None}
    response = post(
        h, f"/executions/{execution_id}/actions/{action_id}/resolution", resolution, every
    )
    assert response.status_code == 403
    # Nothing was claimed, decided or audited for any key.
    assert get(h, f"/human-tasks/{task}", h.owner).json()["claim"] == "none"
    assert not audits(h, "executions.tasks.claimed") and not audits(h, "executions.tasks.decided")


def test_proposal_is_no_store_and_audited_without_values(action_api):
    h = action_api
    _, task = under_review(h, "proposal")
    proposal_response = get(h, f"/human-tasks/{task}/proposal", h.owner)
    assert proposal_response.status_code == 200, proposal_response.text
    assert proposal_response.headers["cache-control"] == "no-store"
    # The fixture's proposal: target R-100, input in EUR. They are the private values.
    body = proposal_response.json()
    assert body["target"] == {"record_id": "R-100"}
    private_sentinel = "R-100"
    [row] = audits(h, "executions.tasks.proposal_read", task)
    proposal_audit_row = json.dumps(row.details) + str(row.resource_id)
    assert private_sentinel not in proposal_audit_row
    assert "EUR" not in proposal_audit_row
    # Task metadata carries no values either.
    assert private_sentinel not in get(h, f"/human-tasks/{task}", h.owner).text


def test_replay_is_not_audited_twice(action_api):
    h = action_api
    execution_id, task = under_review(h, "replayed")
    _, admin = session_for(h, "admin")  # independent of the owner, who started it
    assert post(h, f"/human-tasks/{task}/claim", {}, admin).status_code == 200
    for _ in range(2):  # an equal retry answers the first decision and is audited once
        response = post(h, f"/human-tasks/{task}/decision", decision(h, execution_id), admin)
        assert response.status_code == 200, response.text
    other = post(h, f"/human-tasks/{task}/decision", decision(h, execution_id, "d-2"), admin)
    assert other.status_code == 409
    assert len(audits(h, "executions.tasks.decided", task)) == 1
    # Releasing a task nobody holds changes nothing, so it is not audited.
    _, second = under_review(h, "unheld")
    for _ in range(2):
        assert post(h, f"/human-tasks/{second}/release", {}, h.owner).status_code == 200
    assert audits(h, "executions.tasks.released", second) == []


def test_openapi_names(action_api):
    h = action_api
    schema = h.client.app.openapi()
    prefix = "/api/v1/projects/{project_id}/environments/{environment_id}"
    tasks_ops = [
        operation["operationId"]
        for path, methods in schema["paths"].items()
        if path.startswith(prefix + "/human-tasks")
        for operation in methods.values()
    ]
    action_ops = [
        operation["operationId"]
        for path, methods in schema["paths"].items()
        if path.startswith(prefix + "/executions/{execution_id}/actions")
        for operation in methods.values()
    ]
    assert len(tasks_ops) == 7 and all("human_task" in name for name in tasks_ops)
    assert len(action_ops) == 3
    assert all("production_execution_action" in name for name in action_ops)
    # Nothing names an evaluation run.
    assert not any("run" in name.split("_") for name in tasks_ops + action_ops)
