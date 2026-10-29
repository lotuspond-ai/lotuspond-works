"""Task 11: runtime events keep identity and order; none of them completes an execution."""

import json
from hashlib import sha256
from threading import Barrier, Thread

import pytest
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.wire import canonical_event_payload_json
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.projections import EventProjection
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import Execution, ExecutionEvent

from .activity_fixtures import activity as activity
from .control_fixtures import controlled as controlled
from .control_fixtures import own_fence
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_progression import flows as flows
from .test_publication import harness as harness

PAYLOADS = {
    "execution_completed": {"output_sha256": "a" * 64},
    "cancellation_requested": {"reason_code": "owner_requested"},
}


def runtime_event(
    h, execution_id, source, sequence, *, kind="cancellation_requested", at=None, **changes
):
    payload = PAYLOADS[kind]
    if at is None:
        with Session(h.engine) as db:
            at = database_now_ms(db)
    event = {
        "record_type": "runtime_event",
        "format_version": 1,
        "event_id": f"event-{sequence}",
        "installation_id": "test-installation",
        "recovery_epoch": 1,
        **h.scope.model_dump(),
        "execution_id": execution_id,
        "step_id": None,
        "action_id": None,
        "attempt": None,
        "source_id": source,
        "source_generation": 1,
        "source_sequence": sequence,
        "event_kind": kind,
        "observed_at_ms": at,
        "payload": payload,
        "payload_sha256": sha256(canonical_event_payload_json(kind, payload)).hexdigest(),
    }
    event.update(changes)
    return event


def runtime_rows(h, execution_id):
    with Session(h.engine) as db:
        return list(
            db.scalars(
                select(ExecutionEvent)
                .where(
                    ExecutionEvent.execution_id == execution_id,
                    ExecutionEvent.family == "sdk_runtime_event",
                )
                .order_by(ExecutionEvent.source_sequence)
            )
        )


def reported(h, capacity=1):
    """A runtime this execution's work was delivered to, and a way to report as it."""
    execution_id, attempt_id = h.ready("activity_read", capacity=capacity)
    h.dispatch(attempt_id)
    source = h.principal_runtime.identity.instance_id
    with Session(h.engine) as db:
        at = database_now_ms(db)  # one observation time, so repeats are byte-equal

    def apply(sequence, projection=None, **changes):
        event = runtime_event(h, execution_id, source, sequence, at=at, **changes)
        with Session(h.engine) as db, db.begin():
            return (projection or h.events).apply_event(db, source, event)

    return execution_id, source, apply


def last_event(h, execution_id):
    """The latest event's code and recorded detail."""
    with Session(h.engine) as db:
        row = db.scalar(
            select(ExecutionEvent)
            .where(ExecutionEvent.execution_id == execution_id)
            .order_by(ExecutionEvent.sequence.desc())
            .limit(1)
        )
        content = h.store._get_internal(
            db,
            h.scope,
            ArtifactHandle(
                id=row.event_artifact_id,
                version=row.event_artifact_version,
                sha256=row.digest,
            ),
        )
        return row.code, json.loads(content)["detail"]


def test_event_gap_and_digest_conflict_do_not_complete(controlled):
    h = controlled
    execution_id, source, apply = reported(h)
    assert apply(1).state == "running"
    # 3 before 2: held behind the gap, and nothing completes.
    after_gap = apply(3, kind="execution_completed")
    assert after_gap.state != "succeeded"
    pending = [r for r in runtime_rows(h, execution_id) if r.application_state == "pending"]
    assert [(r.source_sequence, r.gap_slot) for r in pending] == [(3, 1)]
    # 2 arrives: 2 and 3 apply in source order. Completion is still never inferred.
    assert apply(2).state == "running"
    rows = runtime_rows(h, execution_id)
    assert [(r.source_sequence, r.application_state, r.gap_slot) for r in rows] == [
        (1, "applied", None),
        (2, "applied", None),
        (3, "applied", None),
    ]
    # An equal repeat changes nothing.
    with Session(h.engine) as db:
        before = db.scalar(select(func.count()).select_from(ExecutionEvent))
    assert apply(2).state == "running"
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionEvent)) == before
    # Refused outright: another source, an older epoch, an action-review kind.
    with Session(h.engine) as db, db.begin():
        with pytest.raises(ExecutionError, match="forbidden"):
            h.events.apply_event(db, "stranger", runtime_event(h, execution_id, "stranger", 1))
        with pytest.raises(ExecutionError, match="forbidden"):
            h.events.apply_event(
                db, source, runtime_event(h, execution_id, source, 4, recovery_epoch=0)
            )
    # A different event under a known ID fences the execution for reconciliation.
    fenced = apply(4, event_id="event-2")
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        assert (fenced.state, execution.reason) == ("reconciliation_required", "uncertain")
        last = db.scalar(
            select(ExecutionEvent)
            .where(ExecutionEvent.execution_id == execution_id)
            .order_by(ExecutionEvent.sequence.desc())
            .limit(1)
        )
        assert (last.family, last.code) == ("execution_control", "reconciliation_required")
    assert [r.source_sequence for r in runtime_rows(h, execution_id)] == [1, 2, 3]


def test_gaps_beyond_the_bound_fence_and_reconciliation_resumes(controlled):
    h = controlled
    execution_id, source, apply = reported(h)
    apply(1)
    for sequence in range(3, 103):  # 100 events held behind the missing 2
        assert apply(sequence).state == "running"
    assert apply(103).state == "reconciliation_required"  # a gap beyond the bound
    assert execution_reason(h, execution_id) == "uncertain"
    assert last_event(h, execution_id) == ("reconciliation_required", "event_gap")
    pending = [r for r in runtime_rows(h, execution_id) if r.application_state == "pending"]
    assert len(pending) == 100
    with Session(h.engine) as db, db.begin():
        view = h.controls.reconcile_execution(db, h.actor, execution_id)
    assert view.state == "running"
    rows = runtime_rows(h, execution_id)
    assert {r.application_state for r in rows if r.source_sequence > 2} == {"conflict"}
    # The source continues after the highest sequence it was seen to send.
    assert apply(103).state == "running"
    assert runtime_rows(h, execution_id)[-1].application_state == "applied"
    # The missing event arriving late is refused, and never fences again.
    with pytest.raises(ExecutionError, match="conflict"):
        apply(2)
    assert execution_reason(h, execution_id) is None


def execution_reason(h, execution_id):
    with Session(h.engine) as db:
        return db.get(Execution, execution_id).reason


def test_another_event_at_a_taken_sequence_fences_with_a_fixed_reason(controlled):
    h = controlled
    execution_id, _, apply = reported(h, capacity=2)
    apply(1)
    apply(2)
    # A new event ID at a sequence already applied: a conflicting source sequence.
    assert apply(2, event_id="event-2-again").state == "reconciliation_required"
    assert execution_reason(h, execution_id) == "uncertain"
    assert last_event(h, execution_id) == ("reconciliation_required", "event_conflict")
    other_id, _, other = reported(h, capacity=2)
    other(1)
    other(2)
    # Likewise a stale one, below the last applied sequence.
    assert other(1, event_id="event-1-again").state == "reconciliation_required"
    assert last_event(h, other_id) == ("reconciliation_required", "event_conflict")
    assert [r.event_id for r in runtime_rows(h, other_id)] == ["event-1", "event-2"]


def test_only_a_runtime_holding_live_work_reports(controlled):
    h = controlled
    execution_id, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    source = h.principal_runtime.identity.instance_id

    def apply(sequence, **changes):
        event = runtime_event(h, execution_id, source, sequence, **changes)
        with Session(h.engine) as db, db.begin():
            return h.events.apply_event(db, source, event)

    assert apply(1).state == "running"  # under its active lease
    with pytest.raises(ExecutionError, match="forbidden"):
        apply(2, source_generation=2)  # another generation of that instance holds nothing
    h.result(attempt_id, lease)
    # Its work is over: it can no longer report, let alone fence the execution.
    with pytest.raises(ExecutionError, match="forbidden"):
        apply(2, event_id="event-1")
    assert execution_reason(h, execution_id) != "uncertain"


def test_concurrent_events_apply_in_source_order(controlled):
    h = controlled
    execution_id, _, apply = reported(h)
    apply(1)
    projections = [
        EventProjection(
            artifacts=h.store,
            artifact_policy=h.policy,
            installation_id="test-installation",
            recovery=own_fence(h),
        )
        for _ in range(2)
    ]
    start, errors = Barrier(2), []

    def send(sequence, projection):
        start.wait(10)
        try:
            apply(sequence, projection=projection)
        except Exception as error:
            errors.append(error)

    threads = [Thread(target=send, args=(n, p)) for n, p in zip((3, 2), projections)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    # The execution row serializes them: whichever came first, both end applied.
    assert [(r.source_sequence, r.application_state) for r in runtime_rows(h, execution_id)] == [
        (1, "applied"),
        (2, "applied"),
        (3, "applied"),
    ]
