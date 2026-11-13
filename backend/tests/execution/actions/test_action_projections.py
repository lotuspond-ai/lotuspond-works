"""E05 Task 10: action projections. Read without locks; success is never inferred."""

import json

import pytest
from sqlalchemy.orm import Session

from lotuspond.execution.action_projections import ActionProjection
from lotuspond.execution.errors import ExecutionError

from ..activity_fixtures import activity as activity  # noqa: F401
from ..api_fixtures import member
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import intent_row
from .readback_fixtures import Target, execution, read
from .test_action_controls import write_at
from .test_tasks import actor_for
from .test_tasks import tasks as tasks  # noqa: F401
from .test_verification import pending_read, verification_package


def views(h, execution_id, actor=None, **page):
    with Session(h.engine) as db:
        return ActionProjection().list(db, actor or h.actor, execution_id, **page)


def view(h, execution_id, action_id, actor=None):
    with Session(h.engine) as db:
        return ActionProjection().get(db, actor or h.actor, execution_id, action_id)


def test_projection_never_infers_success(actions):
    from .readback_fixtures import report

    h = actions
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("projected", package, documents, active_ms=20000)
    action_id = intent_row(h, execution_id).action_id
    [before] = views(h, execution_id)["items"]
    assert (before.authorization, before.effect, before.outcome) == (
        "approved",
        "not_dispatched",
        "unknown",
    )
    delivery = h.poll()
    lease = h.claim(delivery)
    write_at(h, target, delivery)
    report(h, lease, "accepted")
    h.run2()
    accepted = view(h, execution_id, action_id)
    # A receipt is not an outcome: unknown until verified, whatever the runtime said.
    assert (accepted.effect, accepted.outcome, accepted.receipt_status) == (
        "accepted",
        "unknown",
        "accepted",
    )
    assert execution(h, execution_id).state != "succeeded"
    read(h, pending_read(h, execution_id).id, {"present": True})
    h.run2()
    verified = view(h, execution_id, action_id)
    assert (verified.effect, verified.outcome) == ("observed", "verified")
    assert verified.evidence  # the verified outcome cites its read
    # The view carries digests and identifiers only, never proposal values.
    dumped = json.dumps(verified.model_dump(mode="json"))
    assert "R-100" not in dumped and "EUR" not in dumped
    # Paging; an unknown action is not found; a viewer may read the views.
    page = views(h, execution_id, limit=1)
    assert [v.action_id for v in page["items"]] == [action_id] and page["next"] is None
    with pytest.raises(ExecutionError) as missing:
        view(h, execution_id, "00000000-0000-4000-8000-000000000000")
    assert missing.value.code == "not_found"
    viewer = actor_for(h, member(h, "viewer"), "viewer")
    assert [v.action_id for v in views(h, execution_id, viewer)["items"]] == [action_id]
    with pytest.raises(ExecutionError):
        views(h, execution_id, limit=0)
