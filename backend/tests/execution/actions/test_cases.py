"""E06, second part: cases, their executions, exception ownership and resolution."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import DecisionRequest
from lotuspond.execution.cases import key_digest
from lotuspond.models import AuditEvent, ExecutionCase

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

MISSING = "00000000-0000-4000-8000-000000000000"


def call(h, method, path, headers, body=None):
    return h.client.request(method, h.M + path, json=body, headers=headers)


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def denied(h, name):
    """An execution whose review was denied: it failed and wrote nothing (an exception)."""
    execution_id = h.reviewed(name, decide=False)
    reviewer = h.reviewer()
    task = task_id(h, execution_id)
    h.call("claim", reviewer, task)
    h.call(
        "decide",
        reviewer,
        task,
        DecisionRequest(
            decision_id="d-" + name,
            intent_sha256=intent_row(h, execution_id).intent_sha256,
            decision="deny",
        ),
    )
    h.run2()
    return execution_id


def test_a_case_is_named_by_its_key_which_is_stored_encrypted(action_api):
    h = action_api
    created = ok(call(h, "POST", "/cases", h.owner, {"key": "PO-1042"}))
    assert created["key"] == "PO-1042" and created["outcome"] == "empty"
    assert created["key_sha256"] == key_digest("PO-1042")
    assert created["needs_person"] is False and created["owner"] is None
    # The same key names the same case; another key another.
    assert ok(call(h, "POST", "/cases", h.owner, {"key": "PO-1042"}))["id"] == created["id"]
    other = ok(call(h, "POST", "/cases", h.owner, {"key": "PO-7"}))
    assert other["id"] != created["id"]
    # The key is never kept in plain text: not on the case row, not in any artifact.
    with Session(h.engine) as db:
        row = db.get(ExecutionCase, created["id"])
        assert "PO-1042" not in repr({c.name: getattr(row, c.name) for c in row.__table__.c})
        stored = db.execute(text("SELECT ciphertext FROM execution_artifacts")).scalars().all()
        assert all(b"PO-1042" not in value for value in stored)
        audits = db.scalars(
            select(AuditEvent.resource_id).where(AuditEvent.action == "executions.cases.created")
        ).all()
    # Created twice and once more: the equal retry is not audited again.
    assert sorted(audits) == sorted([created["id"], other["id"]])
    listed = ok(call(h, "GET", "/cases", h.owner))
    assert {item["id"] for item in listed["items"]} == {created["id"], other["id"]}
    for bad in ({}, {"key": ""}, {"key": "x" * 201}, {"key": "a", "extra": 1}):
        assert call(h, "POST", "/cases", h.owner, bad).status_code == 400, bad


def test_executions_join_one_case_and_the_latest_sets_its_outcome(action_api):
    h = action_api
    first = h.reviewed("case-first")
    case = ok(call(h, "POST", "/cases", h.owner, {"key": "INV-1"}))
    linked = ok(
        call(h, "POST", f"/cases/{case['id']}/executions", h.owner, {"execution_id": first})
    )
    assert linked["execution_count"] == 1
    assert [row["id"] for row in linked["executions"]] == [first]
    assert linked["outcome"] == linked["executions"][0]["outcome"]
    # Linking again is a no-op; another case cannot take it.
    again = call(h, "POST", f"/cases/{case['id']}/executions", h.owner, {"execution_id": first})
    assert ok(again)["execution_count"] == 1
    other = ok(call(h, "POST", "/cases", h.owner, {"key": "INV-2"}))
    taken = call(h, "POST", f"/cases/{other['id']}/executions", h.owner, {"execution_id": first})
    assert taken.status_code == 409
    missing = call(h, "POST", f"/cases/{case['id']}/executions", h.owner, {"execution_id": MISSING})
    assert missing.status_code == 404
    # A second execution is the latest.
    second = h.reviewed("case-second", decide=False)
    view = ok(call(h, "POST", f"/cases/{case['id']}/executions", h.owner, {"execution_id": second}))
    assert [row["id"] for row in view["executions"]][0] == second
    assert view["outcome"] == "in_progress"


def test_a_person_owns_an_exception_and_resolves_it(action_api):
    h = action_api
    failed = denied(h, "case-denied")
    case = ok(call(h, "POST", "/cases", h.owner, {"key": "PO-9"}))
    path = f"/cases/{case['id']}"
    view = ok(call(h, "POST", f"{path}/executions", h.owner, {"execution_id": failed}))
    assert view["executions"][0]["exception"] is True and view["needs_person"] is True
    _, editor = session_for(h, "editor")
    _, viewer = session_for(h, "viewer")
    # Only the owner may resolve; a key or a viewer may not even take it.
    resolve = {"request_id": "resolve-1", "kind": "corrected_by_hand"}
    assert call(h, "POST", f"{path}/resolution", h.owner, resolve).status_code == 403
    assert (
        call(
            h, "POST", f"{path}/owner", key(h, ["write"]), {"request_id": "k", "action": "take"}
        ).status_code
        == 403
    )
    assert (
        call(h, "POST", f"{path}/owner", viewer, {"request_id": "v", "action": "take"}).status_code
        == 403
    )
    taken = ok(call(h, "POST", f"{path}/owner", editor, {"request_id": "take-1", "action": "take"}))
    assert taken["owner"]["user_id"] and taken["needs_person"] is True
    # Taken by someone else: refused; the same request again is replayed.
    assert (
        call(
            h, "POST", f"{path}/owner", h.owner, {"request_id": "take-2", "action": "take"}
        ).status_code
        == 409
    )
    assert (
        ok(call(h, "POST", f"{path}/owner", editor, {"request_id": "take-1", "action": "take"}))[
            "owner"
        ]
        == taken["owner"]
    )
    assert call(h, "POST", f"{path}/resolution", h.owner, resolve).status_code == 403
    for bad in (
        {"request_id": "r", "kind": "fixed it myself"},
        {"request_id": "r", "kind": "corrected_by_hand", "note": "x"},
    ):
        assert call(h, "POST", f"{path}/resolution", editor, bad).status_code == 400
    resolved = ok(call(h, "POST", f"{path}/resolution", editor, resolve))
    assert resolved["needs_person"] is False
    assert resolved["resolution"]["kind"] == "corrected_by_hand"
    assert resolved["resolution"]["resolved_by"]["user_id"] == taken["owner"]["user_id"]
    # An equal retry is replayed; the same request with another kind conflicts.
    assert (
        ok(call(h, "POST", f"{path}/resolution", editor, resolve))["resolution"]
        == resolved["resolution"]
    )
    changed = {**resolve, "kind": "no_action_needed"}
    assert call(h, "POST", f"{path}/resolution", editor, changed).status_code == 409
    # Nothing left to resolve.
    later = {"request_id": "resolve-2", "kind": "no_action_needed"}
    assert call(h, "POST", f"{path}/resolution", editor, later).status_code == 409
    # A later exception reopens the case.
    again = denied(h, "case-denied-again")
    reopened = ok(call(h, "POST", f"{path}/executions", h.owner, {"execution_id": again}))
    assert reopened["needs_person"] is True
    # An admin may release anyone's ownership.
    released = ok(
        call(h, "POST", f"{path}/owner", h.owner, {"request_id": "release-1", "action": "release"})
    )
    assert released["owner"] is None
    with Session(h.engine) as db:
        actions = db.scalars(
            select(AuditEvent.action).where(AuditEvent.resource_id == case["id"])
        ).all()
    assert actions.count("executions.cases.resolved") == 1
    assert actions.count("executions.cases.owner") == 2


def test_cases_are_scoped_and_read_as_executions(action_api):
    h = action_api
    case = ok(call(h, "POST", "/cases", h.owner, {"key": "SCOPED-1"}))
    _, viewer = session_for(h, "viewer")
    for headers in (h.owner, viewer, key(h, ["read"])):
        assert ok(call(h, "GET", f"/cases/{case['id']}", headers))["key"] == "SCOPED-1"
    assert call(h, "POST", "/cases", viewer, {"key": "nope"}).status_code == 403
    assert call(h, "GET", f"/cases/{MISSING}", h.owner).status_code == 404
    assert call(h, "GET", f"/cases/{case['id']}?x=1", h.owner).status_code == 400
    other = h.M.replace(h.scope.environment_id, MISSING)
    assert h.client.get(other + f"/cases/{case['id']}", headers=h.owner).status_code == 404


def test_without_execution_services_cases_answer_unavailable(action_api):
    from fastapi.testclient import TestClient

    from lotuspond.app import create_app

    from ..api_fixtures import api_settings

    h = action_api
    application = create_app(api_settings(), engine=h.engine, fleet_dependencies=h.fleet)
    with TestClient(application, headers={"origin": "http://testserver"}) as client:
        response = client.get(h.M + "/cases", headers=h.owner)
        assert (response.status_code, response.json()["error"]) == (503, "authority_unavailable")
