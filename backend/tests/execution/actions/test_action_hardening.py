"""E05 Task 6, after review: the lease, receipt, stop and recipient cases in full."""

import json
from copy import deepcopy

import pytest
from lotuspond_runtime.errors import FleetError
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import ActionStop
from lotuspond.execution.errors import ExecutionError
from lotuspond.models import (
    Execution,
    ExecutionActionCommand,
    ExecutionActionIntent,
    ExecutionAttempt,
    ExecutionDeploymentRevision,
    ExecutionEvent,
    ExecutionUsageReservation,
    Membership,
    RuntimeCommandAdmission,
)

from ..activity_fixtures import activity as activity  # noqa: F401
from ..activity_fixtures import runtime
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..control_fixtures import until
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import intent_row, receipt_for, report
from .test_tasks import tasks as tasks  # noqa: F401


def renew(h, lease, principal=None):
    with Session(h.engine) as db, db.begin():
        return h.dispatcher.renew(db, principal or h.runtime, lease)


def code_of(callable_, *args):
    with pytest.raises((ExecutionError, FleetError)) as error:
        callable_(*args)
    return error.value.code


def test_renewal_after_twenty_seconds_extends_and_bumps_the_revision(actions):
    h = actions
    h.reviewed("renew-20", active_ms=95000)  # a deadline beyond the first lease
    lease = h.claim(h.poll())
    assert renew(h, lease) == lease  # within 20 s: unchanged
    with Session(h.engine) as db:
        renewed_at = db.scalar(select(ExecutionActionCommand)).renewed_at_ms
    until(h, renewed_at + 20000)
    later = renew(h, lease)
    assert later.lease_revision == 2 and later.expires_at_ms > lease.expires_at_ms
    assert later.expires_at_ms <= later.deadline_ms


def test_another_session_cannot_claim_renew_or_report(actions):
    h = actions
    execution_id = h.reviewed("recipient")
    delivery = h.poll()
    other = runtime(h)
    assert code_of(h.claim, delivery, other) in {"not_found", "conflict"}
    lease = h.claim(delivery)
    assert code_of(renew, h, lease, other) in {"not_found", "conflict"}
    assert code_of(report, h, receipt_for(h, execution_id, lease), other) in {
        "not_found",
        "conflict",
    }
    # Another session's poll gets nothing of this command.
    with Session(h.engine) as db, db.begin():
        assert h.dispatcher.poll(db, other) is None


def test_a_claim_after_the_reviewer_lost_the_role_is_refused(actions):
    h = actions
    execution_id = h.reviewed("claim-demoted")
    delivery = h.poll()
    from lotuspond.models import ExecutionActionDecision

    with Session(h.engine) as db, db.begin():
        decision = db.scalar(
            select(ExecutionActionDecision).where(
                ExecutionActionDecision.execution_id == execution_id
            )
        )
        db.scalar(
            select(Membership).where(Membership.user_id == decision.actor_user_id)
        ).role = "viewer"
    assert code_of(h.claim, delivery) == "forbidden"
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionActionCommand)).status == "admitted"


def test_a_claim_after_the_revision_was_disabled_is_refused(actions):
    h = actions
    execution_id = h.reviewed("claim-disabled")
    delivery = h.poll()
    with Session(h.engine) as db, db.begin():
        execution = db.get(Execution, execution_id)
        db.get(ExecutionDeploymentRevision, execution.deployment_revision_id).status = "disabled"
    # E03 reports the record source's refusal as `authority_unavailable`: it denies.
    assert code_of(h.claim, delivery) in {
        "deployment_unavailable",
        "forbidden",
        "authority_unavailable",
    }
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionActionCommand)).status == "admitted"


def test_an_expired_intent_is_never_dispatched(actions):
    from .test_admission import doc
    from .test_tasks import review_package

    h = actions
    package, documents = review_package()
    package["body"]["steps"] = doc("reviewed_action")["body"]["steps"]
    package["body"]["steps"][0]["expiry_seconds"] = 5
    execution_id = h.reviewed("intent-expiry", package=package, documents=documents)
    until(h, intent_row(h, execution_id).expires_at_ms)
    assert h.poll() is None
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionActionCommand)) is None


def test_receipts_need_a_registered_response_and_one_digest(actions):
    h = actions
    execution_id = h.reviewed("response")
    lease = h.claim(h.poll())
    receipt = receipt_for(h, execution_id, lease)
    with Session(h.engine) as db, db.begin():
        stored = h.dispatcher.store_value(db, h.runtime, lease.attempt_id, b'{"present":true}')
    unregistered = receipt.model_copy(
        update={
            "response": stored.model_copy(update={"id": "00000000-0000-4000-8000-00000000abcd"})
        }
    )
    assert code_of(report, h, unregistered) in {"forbidden", "not_found"}
    report(h, receipt.model_copy(update={"response": stored}))
    changed = deepcopy(receipt.receipt.model_dump())
    changed["status"] = "observed_effect"
    conflicting = receipt.model_copy(update={"receipt": changed})
    assert code_of(report, h, conflicting) == "conflict"


def test_a_late_receipt_after_a_lapse_changes_nothing(actions):
    """Task 8: a late receipt is historical evidence only. It changes no lease, attempt
    or execution state; it may settle known usage and confirm the compute stop."""
    from lotuspond.models import ExecutionActionCommand, ExecutionActionReceipt

    h = actions
    execution_id = h.reviewed("late", active_ms=4000)
    lease = h.claim(h.poll())
    until(h, lease.expires_at_ms)
    assert h.sweep().lapsed_action_leases == 1
    report(h, receipt_for(h, execution_id, lease))
    assert code_of(renew, h, lease) == "conflict"
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, lease.attempt_id).state == "uncertain"
        command = db.scalar(select(ExecutionActionCommand))
        assert (command.status, command.lease_revision) == ("uncertain", lease.lease_revision)
        assert db.scalar(select(ExecutionActionReceipt)).source == "historical"
        # Its known usage settled the write and confirmed its stop.
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == lease.attempt_id
            )
        )
        assert reservation.compute_stopped_at_ms is not None
        # No owner fence: the execution keeps running, and read-back decides.
        assert db.get(Execution, execution_id).state == "running"


def test_a_stop_before_starting_needs_a_read_back(actions):
    h = actions
    execution_id = h.reviewed("stop")
    lease = h.claim(h.poll())
    with Session(h.engine) as db, db.begin():
        h.dispatcher.record_stop(
            db,
            h.runtime,
            ActionStop(
                attempt_id=lease.attempt_id,
                lease_revision=lease.lease_revision,
                code="not_started",
                usage={
                    "input_tokens": None,
                    "output_tokens": None,
                    "cost_microusd": None,
                    "completed_at_ms": 0,
                },
            ),
        )
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, lease.attempt_id).state == "uncertain"
        intent = db.get(ExecutionActionIntent, intent_row(h, execution_id).id)
        assert intent.effect_state == "unknown"
        assert db.get(Execution, execution_id).reason == "action_uncertain"


def test_commands_and_events_carry_no_target_or_input_values(actions):
    h = actions
    execution_id = h.reviewed("values")
    delivery = h.poll()
    lease = h.claim(delivery)
    report(h, receipt_for(h, execution_id, lease))
    for sentinel in (b"R-100", b"EUR", b"75000"):
        assert sentinel not in delivery.command_bytes
    with Session(h.engine) as db:
        admission = db.scalar(select(RuntimeCommandAdmission))
        assert b"R-100" not in bytes(admission.command_bytes)
        for event in db.scalars(select(ExecutionEvent)):
            row = json.dumps(
                {c.name: str(getattr(event, c.name)) for c in ExecutionEvent.__table__.columns}
            )
            assert "R-100" not in row and "EUR" not in row
