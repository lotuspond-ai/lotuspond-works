"""Owner-approved addition beyond E04: a runtime declines a delivery it cannot verify.

Without it a pending delivery the runtime refuses heads every poll of its session
until it expires (up to 5 minutes). Declining needs the session the delivery was
dispatched to and its exact digest, and works only while it is pending and
unclaimed; the attempt fails as never started and only a new ordinal retries it.
"""

import json
from hashlib import sha256

import pytest
from lotuspond_runtime.errors import FleetError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionEvent,
    ExecutionUsageReservation,
)

from .activity_fixtures import FLOW, Demand, attempt_at, runtime
from .activity_fixtures import activity as activity
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_dispatch import poll, race, released, request_child
from .test_leases import charged, until
from .test_progression import flows as flows
from .test_publication import harness as harness


def decline(h, delivery, principal=None, digest=None):
    envelope = json.loads(delivery.envelope)
    with Session(h.engine) as db, db.begin():
        h.activities.lock_cursor(db)
        return h.leases.decline_activity(
            db,
            principal or h.principal_runtime,
            envelope["delivery_id"],
            digest or sha256(delivery.envelope.encode()).hexdigest(),
        )


def failure(h, db, attempt):
    return json.loads(
        h.store._get_internal(
            db,
            h.scope,
            {
                "id": attempt.result_artifact_id,
                "version": attempt.result_artifact_version,
                "sha256": attempt.result_sha256,
            },
        )
    )


def test_a_declined_delivery_stops_heading_its_sessions_polls(activity):
    h = activity
    execution_id, stuck = h.ready("activity_read", capacity=2)
    _, served = h.ready("activity_read", capacity=2)
    head = poll(h)
    assert json.loads(head.envelope)["attempt_id"] == stuck
    # Unclaimed, it heads every poll of this session until it expires.
    assert poll(h).envelope == head.envelope
    assert decline(h, head) is None
    assert json.loads(poll(h).envelope)["attempt_id"] == served
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, stuck)
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(ExecutionUsageReservation.attempt_id == stuck)
        )
        # Exactly an expiry, now: never started, compute stopped, released at zero.
        assert attempt.state == "failed"
        assert failure(h, db, attempt)["failure"] == "timeout_before_dispatch"
        assert db.get(
            ExecutionActivityDelivery, json.loads(head.envelope)["delivery_id"]
        ).status == ("conflict")
        assert (reservation.state, reservation.observed_cost_microusd) == ("released", None)
        assert reservation.compute_stopped_at_ms is not None
        assert charged(db, reservation) == {0}
        assert db.scalar(select(ExecutionCapacity.active)) == 1  # only the served one
        assert db.get(Execution, execution_id).runnable_at_ms is not None
        assert "attempt_failed" in set(
            db.scalars(
                select(ExecutionEvent.code).where(
                    ExecutionEvent.execution_id == execution_id,
                    ExecutionEvent.attempt_id == stuck,
                )
            )
        )
    # An equal repeat changes nothing; the declined delivery can never be claimed.
    assert decline(h, head) is None
    with pytest.raises(ExecutionError, match="conflict"):
        h.claim(head)
    # Progression retries it, and only as a new ordinal with a new delivery.
    h.drive()
    retry = attempt_at(h, execution_id, "read", ordinal=2)
    assert retry is not None and retry != stuck
    with Session(h.engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExecutionActivityDelivery)
                .where(ExecutionActivityDelivery.attempt_id == stuck)
            )
            == 1
        )


def test_only_its_session_and_exact_digest_may_decline(activity):
    h = activity
    _, attempt_id = h.ready("activity_read")
    delivery = h.dispatch(attempt_id)
    other = runtime(h)  # another runtime instance in the same pool
    with pytest.raises(FleetError, match="unauthenticated"):
        decline(h, delivery, principal=other)
    with pytest.raises(ExecutionError, match="conflict"):
        decline(h, delivery, digest="0" * 64)  # an altered or forged envelope
    with pytest.raises(ExecutionError, match="not_found"):
        with Session(h.engine) as db, db.begin():
            h.leases.decline_activity(
                db, h.principal_runtime, "00000000-0000-4000-8000-000000000099", "0" * 64
            )
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "dispatched"  # unchanged
    # A claimed attempt is reported or reconciled, never declined.
    lease = h.claim(delivery)
    with pytest.raises(ExecutionError, match="conflict"):
        decline(h, delivery)
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "claimed"
    h.result(attempt_id, lease, value=None)


def test_a_declined_child_fails_and_its_parent_can_still_report(activity):
    from lotuspond_runtime.activity_protocol import tool_name

    h = activity
    model = json.loads((FLOW / "agent_tools.model.json").read_text())
    _, parent = h.ready("agent_tools", model=model, path="think")
    parent_lease = h.claim(h.dispatch(parent))
    arguments = h.store_value(parent, {"target": None, "input": None})
    child = request_child(
        h, parent, "tool-1", "connector_read", tool_name("connector-fixture", "read"), arguments
    )
    with Session(h.engine) as db, db.begin():
        delivery = h.activities.poll_activity(db, h.principal_runtime, parent_attempt_id=parent)
    assert json.loads(delivery.envelope)["attempt_id"] == child
    decline(h, delivery)
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, child)
        assert attempt.state == "failed"
        assert failure(h, db, attempt)["failure"] == "timeout_before_dispatch"
        assert db.get(ExecutionAttempt, parent).state == "claimed"  # the parent runs on
        assert released(db, child)  # never claimed: nothing of it is charged
    # Nothing of the parent's is still running: its own result is accepted.
    h.result(parent, parent_lease, value=None)
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, parent).state == "succeeded"
        parent_reservation = db.scalar(
            select(ExecutionUsageReservation).where(ExecutionUsageReservation.attempt_id == parent)
        )
        # The claimed parent may have run: it stays charged as observed.
        assert parent_reservation.state == "settled"
        assert charged(db, parent_reservation) != {0}


def failures(h, execution_id, attempt_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(ExecutionEvent)
            .where(
                ExecutionEvent.execution_id == execution_id,
                ExecutionEvent.attempt_id == attempt_id,
                ExecutionEvent.code == "attempt_failed",
            )
        )


def test_decline_and_expiry_end_an_attempt_once_in_either_order(activity):
    h = activity
    first_execution, first = h.ready("activity_read", capacity=2)
    second_execution, second = h.ready("activity_read", capacity=2)
    expired, declined = h.dispatch(first), h.dispatch(second)
    # Declined first: the sweeper's later expiry changes nothing.
    decline(h, declined)
    until(h, max(json.loads(d.envelope)["expires_at_ms"] for d in (expired, declined)))
    with Session(h.engine) as db, db.begin():
        h.leases.expire_delivery(db, json.loads(declined.envelope)["delivery_id"])
        h.leases.expire_delivery(db, json.loads(expired.envelope)["delivery_id"])
    # Expired first: a later decline is refused.
    with pytest.raises(ExecutionError, match="conflict"):
        decline(h, expired)
    assert decline(h, declined) is None  # its own repeat is still a no-op
    assert failures(h, second_execution, second) == 1
    assert failures(h, first_execution, first) == 1
    with Session(h.engine) as db:
        # Either way it ended once, released at zero, with its slot returned once.
        assert released(db, first) and released(db, second)
        assert db.scalar(select(ExecutionCapacity.active)) == 0


def test_a_decline_racing_a_claim_has_one_winner(activity):
    h = activity
    _, attempt_id = h.ready("activity_read")
    delivery = h.dispatch(attempt_id)
    claimed, declined = race(lambda: h.claim(delivery), lambda: decline(h, delivery))
    with Session(h.engine) as db:
        state = db.get(ExecutionAttempt, attempt_id).state
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            )
        )
        # Exactly one wins: claimed with a live lease and its reservation held, or
        # failed as never started and released at zero.
        if state == "claimed":
            assert not isinstance(claimed, Exception) and isinstance(declined, ExecutionError)
            assert reservation.state == "reserved"
        else:
            assert state == "failed" and declined is None and isinstance(claimed, ExecutionError)
            assert released(db, attempt_id)


def test_a_runtime_declines_at_most_its_allowance_per_minute(activity, monkeypatch):
    from lotuspond.execution.leases import LeaseService

    monkeypatch.setattr(LeaseService, "DECLINES_PER_MINUTE", 1)
    h = activity
    _, first = h.ready("activity_read", capacity=2)
    _, second = h.ready("activity_read", capacity=2)
    decline(h, h.dispatch(first))
    with pytest.raises(FleetError, match="rate_limited"):
        decline(h, h.dispatch(second))
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, second).state == "dispatched"  # nothing changed


def test_refunded_declines_still_end_at_the_steps_attempts(activity):
    """Declines charge nothing, so the step's attempts, not the budget, end the churn.

    This pins one execution's bound. Across executions a declining runtime is bounded
    by `DECLINES_PER_MINUTE` (`test_a_runtime_declines_at_most_its_allowance_per_minute`)
    and by the runtime's three declines in a row (runtime `test_activity_worker.py`).
    """
    from lotuspond.execution.leases import LeaseService

    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    for ordinal in (1, 2, 3):  # the step's max_attempts
        if ordinal > 1:
            attempt_id = attempt_at(h, execution_id, "read", ordinal=ordinal)
            with Session(h.engine) as db, db.begin():  # as the scheduler would
                h.budgets.reserve_attempt(db, h.actor, attempt_id, Demand(10000).demand(attempt_id))
        decline(h, h.dispatch(attempt_id))
        h.drive()
    assert attempt_at(h, execution_id, "read", ordinal=4) is None
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "failed"
        rows = list(
            db.scalars(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.execution_id == execution_id
                )
            )
        )
        assert [row.state for row in rows] == ["released"] * 3
        assert set().union(*(charged(db, row) for row in rows)) == {0}
        assert db.scalar(select(ExecutionCapacity.active)) == 0
    assert LeaseService.DECLINES_PER_MINUTE == 6
