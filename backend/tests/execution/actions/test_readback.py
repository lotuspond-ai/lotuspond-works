"""E05 Task 8: uncertain writes settle by read-back before any resend.

Real PostgreSQL and real database time; writes and idempotency keys are counted at
the fake target, never from report counters.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.sweeper import ExecutionSweeper
from lotuspond.models import (
    ExecutionActionCommand,
    ExecutionActionIntent,
    ExecutionActionReceipt,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionRequest,
)

from ..activity_fixtures import Demand
from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..control_fixtures import until
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import intent_row
from .readback_fixtures import (
    Target,
    commands,
    dispatch_outcome,
    execution,
    observations,
    observe,
    pending_readback,
    read,
    read_input,
    readback_package,
    readbacks,
    report,
    reservation,
    reserve_readback,
    send,
)
from .test_tasks import tasks as tasks  # noqa: F401


def effect(h, execution_id):
    with Session(h.engine) as db:
        return db.get(ExecutionActionIntent, intent_row(h, execution_id).id).effect_state


def test_effect_present_stops_any_resend(actions):
    h, target = actions, Target()
    package, documents = readback_package(max_attempts=3)
    execution_id = h.reviewed("present", package, documents)
    send(h, target, "lose_response")  # applied at the target; the answer is lost
    h.run2()
    attempt = pending_readback(h, execution_id)
    # The read-back reads exactly the intent's target and input.
    assert read_input(h, attempt.id) == {
        "target": {"record_id": "R-100"},
        "input": {"amount_minor": 75000, "currency": "EUR"},
    }
    # Its write's compute is stopped (the `unknown` receipt), so there is no slot to
    # share: the sweeper's read-back pass leaves it to the scheduler.
    from lotuspond.execution.action_reconciliation import readback_candidates

    with Session(h.engine) as db:
        assert attempt.id not in db.scalars(readback_candidates(100)).all()
    observe(h, execution_id, target)
    lost_response_commands = len(commands(h, execution_id))
    lost_response_writes = target.writes
    assert (lost_response_commands, lost_response_writes) == (1, 1)
    [seen] = observations(h, execution_id)
    assert (seen.result, seen.source, seen.qualified) == ("effect_present", "readback", False)
    assert effect(h, execution_id) == "observed"
    state, value = dispatch_outcome(h, execution_id)
    with Session(h.engine) as db:
        receipt = db.scalar(select(ExecutionActionReceipt))
    assert state == "succeeded" and value == {"handle_kind": "action_receipt", "id": receipt.id}
    # Nothing is resent, however long it runs: retries were left, but none is due.
    h.run2()
    assert h.poll() is None and len(commands(h, execution_id)) == 1
    assert execution(h, execution_id).state == "waiting"  # on to the next step's timer


def test_qualified_absence_resends_once_with_target_key(actions):
    h, target = actions, Target()
    package, documents = readback_package(idempotency="target_key", max_attempts=2)
    execution_id = h.reviewed("resend", package, documents, active_ms=20000)
    send(h, target, "lose_request")  # never reached the target
    h.run2()
    observe(h, execution_id, target)
    [seen] = observations(h, execution_id)
    assert (seen.result, seen.qualified) == ("effect_absent", True)
    assert effect(h, execution_id) == "absent"
    # The resend: same intent, same decision, a new command and the same key.
    with Session(h.engine) as db:
        second = db.scalar(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.execution_id == execution_id, ExecutionAttempt.ordinal == 2)
            .where(ExecutionAttempt.parent_attempt_id.is_(None))
            .where(ExecutionAttempt.state == "reserved")
        )
    assert second is not None
    from .dispatch_fixtures import reserve

    reserve(h, second.id, 20000)
    send(h, target, "applied")
    h.run2()
    lost_request_commands = len(commands(h, execution_id))
    lost_request_writes = target.writes
    lost_request_idempotency_keys = target.keys
    assert (lost_request_commands, lost_request_writes) == (2, 1)
    assert len(set(lost_request_idempotency_keys)) == 1
    assert lost_request_idempotency_keys[0] == intent_row(h, execution_id).action_id
    first, again = sorted(commands(h, execution_id), key=lambda c: c.created_at)
    assert first.command_id != again.command_id
    assert first.intent_id == again.intent_id and first.decision_row_id == again.decision_row_id
    assert dispatch_outcome(h, execution_id)[0] == "succeeded"
    assert effect(h, execution_id) == "accepted"
    # Once: a second qualified absence with no retries left would fail, never resend.
    h.run2()
    assert h.poll() is None and len(commands(h, execution_id)) == 2


def test_absence_without_target_key_is_not_applied(actions):
    h, target = actions, Target()
    package, documents = readback_package(idempotency="none", max_attempts=3)
    execution_id = h.reviewed("absent", package, documents)
    send(h, target, "lose_request")  # its `unknown` receipt confirms the stop
    h.run2()
    observe(h, execution_id, target)
    [seen] = observations(h, execution_id)
    assert (seen.result, seen.qualified) == ("effect_absent", True)
    assert (len(commands(h, execution_id)), target.writes) == (1, 0)
    state, value = dispatch_outcome(h, execution_id)
    assert state == "failed" and value["failure"] == "not_applied"
    assert execution(h, execution_id).state == "failed"
    assert execution(h, execution_id).reason == "not_applied"


def test_unqualified_absence_waits_for_an_owner(actions):
    h, target = actions, Target()
    package, documents = readback_package(idempotency="none", max_attempts=3)
    execution_id = h.reviewed("unqualified", package, documents, active_ms=4000)
    lease = send(h, target, "lapse")
    target.records.clear()  # positive control below: absent at the target
    target.writes = 0
    until(h, lease.expires_at_ms)
    assert h.sweep().lapsed_action_leases == 1
    h.run2()
    attempt = pending_readback(h, execution_id)
    # A lapse confirms no stop: the write keeps its slot, and the read-back shares it.
    assert reservation(h, lease.attempt_id).compute_stopped_at_ms is None
    sweeper = ExecutionSweeper(leases=h.leases, recovery=h.fence, readbacks=(h.budgets, Demand()))
    assert h.sweep(sweeper=sweeper).reserved_readbacks == 1
    read(h, attempt.id, {"present": False}, reserve=False)
    h.run2()
    [seen] = observations(h, execution_id)
    assert (seen.result, seen.qualified) == ("effect_absent", False)
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("reconciliation_required", "action_uncertain")
    assert dispatch_outcome(h, execution_id) is None  # nothing decided without an owner
    h.run2()
    assert len(commands(h, execution_id)) == 1 and len(readbacks(h, execution_id)) == 1


def test_unknown_is_bounded_then_needs_reconciliation(actions):
    h, target = actions, Target()
    package, documents = readback_package(max_attempts=3)
    execution_id = h.reviewed("unknown", package, documents, active_ms=20000)
    send(h, target, "lose_response")
    h.run2()
    for count in range(1, 6):
        attempt = pending_readback(h, execution_id)
        # An unreadable answer settles nothing: unknown, never present or absent.
        read(h, attempt.id, {"elsewhere": True}, code="succeeded")
        h.run2()
        assert len(observations(h, execution_id)) == count
        if count < 5:
            assert execution(h, execution_id).state == "running"
    unknown_execution = execution(h, execution_id)
    assert unknown_execution.state == "reconciliation_required"
    assert unknown_execution.reason == "action_uncertain"
    assert {o.result for o in observations(h, execution_id)} == {"unknown"}
    assert len(readbacks(h, execution_id)) == 5 and len(commands(h, execution_id)) == 1
    assert effect(h, execution_id) == "unknown"


def test_failed_readback_is_unknown(actions):
    h, target = actions, Target()
    package, documents = readback_package()
    execution_id = h.reviewed("failed-read", package, documents)
    send(h, target, "lose_response")
    h.run2()
    attempt = pending_readback(h, execution_id)
    read(h, attempt.id, None, code="unavailable")
    h.run2()
    [seen] = observations(h, execution_id)
    assert seen.result == "unknown" and seen.attempt_id == attempt.id
    assert len(readbacks(h, execution_id)) == 2  # the next bounded read-back
    observe(h, execution_id, target)
    assert dispatch_outcome(h, execution_id)[0] == "succeeded"


def test_late_receipt_is_historical_only(actions):
    h, target = actions, Target()
    package, documents = readback_package()
    execution_id = h.reviewed("late", package, documents, active_ms=4000)
    lease = send(h, target, "lapse")
    until(h, lease.expires_at_ms)
    # The lease is over and the sweeper has not reached it: the report lapses it
    # first, then is recorded as historical evidence only.
    report(h, lease, "accepted")
    with Session(h.engine) as db:
        receipt = db.scalar(select(ExecutionActionReceipt))
        command = db.scalar(select(ExecutionActionCommand))
        attempt = db.get(ExecutionAttempt, lease.attempt_id)
    assert receipt.source == "historical" and receipt.status == "accepted"
    assert (command.status, command.lease_revision) == ("uncertain", lease.lease_revision)
    assert command.lease_expires_at_ms == lease.expires_at_ms
    assert attempt.state == "uncertain" and effect(h, execution_id) == "unknown"
    # It settled known usage and confirmed the stop, and is evidence for an owner.
    assert reservation(h, lease.attempt_id).compute_stopped_at_ms is not None
    with Session(h.engine) as db:
        evidence = db.scalar(
            select(func.count())
            .select_from(ExecutionRequest)
            .where(ExecutionRequest.request_key.like("action-evidence:%"))
        )
    assert evidence == 1
    # An equal repeat changes nothing; a renewal or another receipt is refused.
    report(h, lease, "accepted")
    with pytest.raises(ExecutionError):
        with Session(h.engine) as db, db.begin():
            h.dispatcher.renew(db, h.runtime, lease)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionActionReceipt)) == 1
    # Read-back still decides the effect.
    h.run2()
    observe(h, execution_id, target)
    assert dispatch_outcome(h, execution_id)[0] == "succeeded"
    assert (len(commands(h, execution_id)), target.writes) == (1, 1)


def test_another_runtime_cannot_send_a_late_report(actions):
    from ..activity_fixtures import runtime

    h, target = actions, Target()
    package, documents = readback_package()
    execution_id = h.reviewed("late-other", package, documents, active_ms=4000)
    lease = send(h, target, "lapse")
    until(h, lease.expires_at_ms)
    other = runtime(h)
    with pytest.raises(ExecutionError):
        report(h, lease, "accepted", principal=other)
    report(h, lease, "accepted")  # positive control: the claiming runtime may
    del execution_id


def test_readback_never_waits_for_new_capacity(actions):
    """Every environment slot is held by an uncertain write, and read-backs still run.

    This harness's grant allows one environment slot (`active_capacity=1`), so one
    lapsed write fills the environment; the plan's five-slot case is the same rule.
    """
    h, target = actions, Target()
    package, documents = readback_package()
    execution_id = h.reviewed("full", package, documents, active_ms=6000)
    lease = send(h, target, "lapse")
    until(h, lease.expires_at_ms)
    assert h.sweep().lapsed_action_leases == 1
    h.run2()
    attempt = pending_readback(h, execution_id)
    with Session(h.engine) as db:
        held = db.scalar(select(ExecutionCapacity)).active
    assert held == 1  # the lapsed write still holds the environment's only slot
    # Positive control: a read-back taking a slot of its own would wait.
    with pytest.raises(ExecutionError) as full:
        reserve_readback(h, attempt.id)
    assert full.value.code == "capacity_unavailable"
    from lotuspond.execution.action_reconciliation import readback_candidates

    with Session(h.engine) as db:
        assert db.scalars(readback_candidates(100)).all() == [attempt.id]
    sweeper = ExecutionSweeper(leases=h.leases, recovery=h.fence, readbacks=(h.budgets, Demand()))
    assert h.sweep(sweeper=sweeper).reserved_readbacks == 1
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionCapacity)).active == held  # no new slot taken
        shared = db.scalar(
            select(func.count())
            .select_from(ExecutionRequest)
            .where(ExecutionRequest.request_key == "capacity.shared:" + attempt.id)
        )
    assert shared == 1
    read(h, attempt.id, {"present": True}, reserve=False)
    h.run2()
    assert dispatch_outcome(h, execution_id)[0] == "succeeded"
    assert (target.writes, len(commands(h, execution_id))) == (1, 1)
    # Its write's stop is still unconfirmed, so the slot stays held (containment).
    assert reservation(h, lease.attempt_id).compute_stopped_at_ms is None
