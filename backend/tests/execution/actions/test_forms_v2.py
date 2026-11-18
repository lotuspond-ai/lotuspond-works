"""E05 Task 5: v2 forms use task records and rules; v1 forms keep E04's signal route."""

from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import SubmissionRequest
from lotuspond.execution.contracts import SignalRequest
from lotuspond.execution.errors import ExecutionError
from lotuspond.models import Execution, ExecutionHumanTask, ExecutionWait

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .test_admission import doc
from .test_tasks import code_of, state_of  # noqa: F401
from .test_tasks import tasks as tasks

FORM_SCHEMA = {"kind": "string", "max_length": 64}


def form_package(policy):
    package = doc("reviewed_action")
    package["package_id"] = "e05-form"
    package["connectors"] = []
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    document = doc(policy)
    package["policies"] = [
        {
            "id": "clerks",
            "ref": {
                "kind": "policy",
                "name": document["name"],
                "version": "1.0.0",
                "sha256": catalog_document_digest(document),
            },
        }
    ]
    package["body"]["steps"] = [
        {
            "kind": "human_task",
            "id": "form",
            "assignment_policy_ref": "clerks",
            "timeout_seconds": 600,
            "mode": "form",
            "form_schema": FORM_SCHEMA,
        }
    ]
    return package, [document]


def form_task(h, execution_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionHumanTask).where(ExecutionHumanTask.execution_id == execution_id)
        )


def test_v2_form_submission_uses_task_rules(tasks):
    h = tasks
    package, documents = form_package("owners")  # an E05 assignment: a v2 form
    execution_id = h.start2(package, "form-v2", documents)
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).interpreter_id.endswith(".v2")
    task = form_task(h, execution_id)
    assert task is not None and task.kind == "form" and task.assignment == "workspace_owners"
    submission = SubmissionRequest(submission_id="s-1", payload="approved by owner")
    # No claim: refused. An admin is not in the owners group: refused.
    assert code_of(h.call, "submit", h.actor, task.id, submission) == "conflict"
    admin = h.reviewer("admin")
    assert code_of(h.call, "claim", admin, task.id) == "forbidden"
    # The plain signal route is closed to a v2 form.
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
        h.inbox.accept_signal(
            db,
            h.actor,
            execution_id,
            SignalRequest(signal_id="direct", wait_id=task.wait_id, payload="x"),
        )
    assert error.value.code == "forbidden"
    # Claimed by an owner: submitted through the typed, encrypted inbox path.
    h.call("claim", h.actor, task.id)
    assert h.call("submit", h.actor, task.id, submission).state == "submitted"
    assert h.call("submit", h.actor, task.id, submission).state == "submitted"  # replay
    other = SubmissionRequest(submission_id="s-2", payload="another")
    assert code_of(h.call, "submit", h.actor, task.id, other) == "conflict"
    h.run2()
    assert state_of(h, execution_id)[0] == "succeeded"


def test_v1_form_signals_unchanged(tasks):
    h = tasks
    package, documents = form_package("reviewers")  # workspace admins: E04's form
    execution_id = h.start2(deepcopy(package), "form-v1", documents)
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).interpreter_id.endswith(".v1")
        wait_ids = list(
            db.scalars(select(ExecutionWait.id).where(ExecutionWait.execution_id == execution_id))
        )
    assert form_task(h, execution_id) is None  # v1 forms have no task record
    with Session(h.engine) as db, db.begin():
        receipt = h.inbox.accept_signal(
            db,
            h.actor,
            execution_id,
            SignalRequest(signal_id="v1", wait_id=wait_ids[0], payload="signed off"),
        )
    assert receipt.status == "pending"
    h.run2()
    assert state_of(h, execution_id)[0] == "succeeded"


def test_a_form_task_says_what_it_asks(tasks):
    """The console renders a form from its declared schema: the pinned package's, read
    by anyone who may read the execution, and refused for a review."""
    h = tasks
    package, documents = form_package("owners")
    execution_id = h.start2(package, "form-schema", documents)
    task = form_task(h, execution_id)
    view = h.call("form", h.actor, task.id)
    assert view.form_schema == FORM_SCHEMA
    assert view.definitions == []
    viewer = h.reviewer("viewer")
    assert h.call("form", viewer, task.id).form_schema == FORM_SCHEMA
    from .dispatch_fixtures import task_id
    from .test_tasks import review_package

    review, review_documents = review_package()
    reviewed = h.start2(review, "form-of-review", review_documents)
    assert code_of(h.call, "form", h.actor, task_id(h, reviewed)) == "not_found"
