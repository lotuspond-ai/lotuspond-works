"""E05 Task 6: receipts, stops, command expiry and shared read-back reservations."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.models import (
    ExecutionActionCommand,
    ExecutionActionIntent,
    ExecutionAttempt,
    ExecutionUsageReservation,
    RuntimeCommandAdmission,
)

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..control_fixtures import until
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import dispatch_attempt, intent_row, receipt_for, report
from .test_tasks import tasks as tasks  # noqa: F401


def test_receipt_requires_live_lease_and_matching_intent(actions):
    h = actions
    execution_id = h.reviewed("receipt")
    lease = h.claim(h.poll())
    with pytest.raises(ExecutionError, match="conflict"):
        report(h, receipt_for(h, execution_id, lease, intent_sha256="0" * 64))
    with pytest.raises(ExecutionError, match="conflict"):
        report(h, receipt_for(h, execution_id, lease, action_id="other-action"))
    report(h, receipt_for(h, execution_id, lease))
    report(h, receipt_for(h, execution_id, lease))  # an equal repeat changes nothing
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, lease.attempt_id)
        assert attempt.state == "succeeded"
        intent = db.get(ExecutionActionIntent, intent_row(h, execution_id).id)
        assert intent.effect_state == "accepted"
        command = db.scalar(select(ExecutionActionCommand))
        assert command.status == "stopped"
        assert db.get(RuntimeCommandAdmission, command.admission_id).status == "terminal"


def test_unknown_receipt_leaves_the_effect_unknown(actions):
    h = actions
    execution_id = h.reviewed("unknown")
    lease = h.claim(h.poll())
    report(h, receipt_for(h, execution_id, lease, status="unknown"))
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, lease.attempt_id).state == "uncertain"
        intent = db.get(ExecutionActionIntent, intent_row(h, execution_id).id)
        assert intent.effect_state == "unknown"


def test_unclaimed_command_expiry_writes_nothing(actions):
    h = actions
    execution_id = h.reviewed("expire", active_ms=3000)
    delivery = h.poll()
    assert delivery is not None
    with Session(h.engine) as db:
        command = db.scalar(select(ExecutionActionCommand))
        expires = db.get(RuntimeCommandAdmission, command.admission_id).command_expires_at_ms
    until(h, expires)
    assert h.sweep().expired_action_commands == 1
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, dispatch_attempt(h, execution_id))
        assert attempt.state == "failed"
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == attempt.id
            )
        )
        assert reservation.state == "released"  # zero usage: nothing ran
        assert db.scalar(select(ExecutionActionCommand)).status == "expired"


def test_uncertain_parent_shared_reservation(actions):
    """Task 8's read-backs reserve against an uncertain write's retained slot."""
    from ..activity_fixtures import Demand

    h = actions
    execution_id = h.reviewed("shared", active_ms=4000)
    lease = h.claim(h.poll())
    until(h, lease.expires_at_ms)
    h.sweep()
    with Session(h.engine) as db, db.begin():
        parent = db.get(ExecutionAttempt, lease.attempt_id)
        assert parent.state == "uncertain"
        child = ExecutionAttempt(
            workspace_id=parent.workspace_id,
            project_id=parent.project_id,
            environment_id=parent.environment_id,
            execution_id=parent.execution_id,
            step_instance_id=parent.step_instance_id,
            ordinal=2,
            parent_attempt_id=parent.id,
            child_request_id="reconcile:1",
            child_request_digest="a" * 64,
            input_artifact_id=parent.input_artifact_id,
            input_artifact_version=parent.input_artifact_version,
            input_sha256=parent.input_sha256,
            state="reserved",
        )
        db.add(child)
        db.flush()
        child_id = child.id
    with Session(h.engine) as db, db.begin():
        h.budgets.reserve_attempt(db, h.actor, child_id, Demand(1000).demand(child_id), shared=True)
    with Session(h.engine) as db:
        assert db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == child_id
            )
        )
    del execution_id
