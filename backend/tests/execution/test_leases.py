"""Task 9: lease renewal bounds, unclaimed-delivery expiry and lapsed leases.

The `activity_read` fixture's attempt timeout is 2 s, so expiry happens in real
database time; no deadline, lease or expiry column is ever edited.
"""

import json
from time import sleep

import pytest
from lotuspond_runtime.errors import FleetError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionEvent,
    ExecutionLease,
    ExecutionUsageReservation,
    RuntimeInstance,
)

from .activity_fixtures import activity as activity
from .activity_fixtures import attempt_at
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_progression import flows as flows
from .test_publication import harness as harness


def renew(h, lease, principal=None):
    with Session(h.engine) as db, db.begin():
        return h.leases.renew_activity(db, principal or h.principal_runtime, lease)


def charged(db, reservation):
    """Every liability the reservation still counts, now."""
    from lotuspond.execution.budgets import liability

    now = database_now_ms(db)
    return {
        liability(reservation, field, now)
        for field in ("input_tokens", "output_tokens", "cost_microusd", "active_ms")
    }


def until(h, at_ms):
    """Wait in real time until database time passes `at_ms`."""
    while True:
        with Session(h.engine) as db:
            if database_now_ms(db) > at_ms:
                return
        sleep(0.05)


def test_lease_renewal_cannot_extend_attempt_deadline(activity):
    h = activity
    _, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    with Session(h.engine) as db:
        server_now_ms = database_now_ms(db)
    assert lease.expires_at_ms - server_now_ms <= 60000
    assert lease.expires_at_ms <= lease.deadline_ms
    renewed = renew(h, lease)
    # Renewal keeps the attempt's own deadline (2 s timeout), never 60 s beyond it.
    assert renewed.deadline_ms == lease.deadline_ms
    assert renewed.expires_at_ms <= renewed.deadline_ms
    assert renewed.lease_revision >= lease.lease_revision
    until(h, lease.expires_at_ms)
    # Expiry + 1: a lease past its expiry can never be renewed.
    with pytest.raises(ExecutionError, match="conflict"):
        renew(h, renewed)


@pytest.mark.parametrize("change", ["cancelled", "revoked"])
def test_revoked_identity_and_cancellation_cannot_renew(activity, change):
    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    assert renew(h, lease).attempt_id == attempt_id  # positive control
    with Session(h.engine) as db, db.begin():
        if change == "cancelled":
            db.scalar(
                select(Execution).where(Execution.id == execution_id).with_for_update()
            ).requested_state = "cancelled"
        else:
            db.get(RuntimeInstance, h.principal_runtime.identity.instance_id).status = "revoked"
    # Cancellation is reported by refusing renewal; the runtime then stops.
    error = (
        (ExecutionError, "forbidden") if change == "cancelled" else (FleetError, "unauthenticated")
    )
    with pytest.raises(error[0], match=error[1]):
        renew(h, lease)


def test_unclaimed_delivery_expiry_allows_only_a_new_ordinal(activity):
    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    delivery = h.dispatch(attempt_id)
    envelope = json.loads(delivery.envelope)
    with Session(h.engine) as db, db.begin():
        # Not yet expired: the sweeper's call changes nothing.
        h.leases.expire_delivery(db, envelope["delivery_id"])
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "dispatched"
    until(h, envelope["expires_at_ms"])
    with Session(h.engine) as db, db.begin():
        h.leases.expire_delivery(db, envelope["delivery_id"])
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, attempt_id)
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            )
        )
        failure = json.loads(
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
        assert attempt.state == "failed" and failure["failure"] == "timeout_before_dispatch"
        assert db.get(ExecutionActivityDelivery, envelope["delivery_id"]).status == "conflict"
        # Never claimed, so never started: compute stopped, slot returned, and the
        # reservation released at zero, active time too (owner decision, 2026-10-26).
        assert (reservation.state, reservation.observed_cost_microusd) == ("released", None)
        assert reservation.compute_stopped_at_ms is not None
        assert charged(db, reservation) == {0}
        assert db.scalar(select(ExecutionCapacity.active)) == 0
        assert db.get(Execution, execution_id).runnable_at_ms is not None
        codes = set(
            db.scalars(
                select(ExecutionEvent.code).where(ExecutionEvent.execution_id == execution_id)
            )
        )
        assert {"attempt_dispatched", "attempt_failed"} <= codes
    # The failed attempt never gets a second delivery or a claim.
    with pytest.raises(ExecutionError, match="conflict"):
        h.dispatch(attempt_id)
    with pytest.raises(ExecutionError, match="conflict"):
        h.claim(delivery)
    # Progression retries it, and only as a new ordinal.
    h.drive()
    retry = attempt_at(h, execution_id, "read", ordinal=2)
    assert retry is not None and retry != attempt_id
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, retry).state == "reserved"
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExecutionActivityDelivery)
                .where(ExecutionActivityDelivery.attempt_id == attempt_id)
            )
            == 1
        )


def test_lapsed_lease_requires_reconciliation(activity):
    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    with Session(h.engine) as db:
        lease_id = db.scalar(select(ExecutionLease.id))
    with Session(h.engine) as db, db.begin():
        h.leases.lapse_lease(db, lease_id)  # still live: nothing changes
    with Session(h.engine) as db:
        assert db.get(ExecutionLease, lease_id).status == "active"
    until(h, lease.expires_at_ms)
    with Session(h.engine) as db, db.begin():
        h.leases.lapse_lease(db, lease_id)
    with Session(h.engine) as db, db.begin():
        h.leases.lapse_lease(db, lease_id)  # repeats are no-ops
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        reservation = db.scalar(select(ExecutionUsageReservation))
        assert db.get(ExecutionLease, lease_id).status == "uncertain"
        assert db.get(ExecutionAttempt, attempt_id).state == "uncertain"
        # The liability stays reserved and the compute slot stays held.
        assert reservation.state == "uncertain" and reservation.compute_stopped_at_ms is None
        assert db.scalar(select(ExecutionCapacity.active)) == 1
        assert (execution.state, execution.reason) == ("reconciliation_required", "uncertain")
        assert execution.runnable_at_ms is None
    # An old result cannot resurrect the activity: it stays uncertain.
    with pytest.raises(ExecutionError, match="conflict"):
        h.result(attempt_id, lease, value=None)
    with pytest.raises(ExecutionError, match="conflict"):
        renew(h, lease)
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "uncertain"
        assert db.get(Execution, execution_id).runnable_at_ms is None


def test_lease_never_outlives_the_reserved_active_time(activity):
    from lotuspond.execution.activity_records import reserved_until

    h = activity
    # 1.5 s of reserved active time, inside the 2 s attempt deadline.
    _, attempt_id = h.ready("activity_read", active_ms=1500)
    lease = h.claim(h.dispatch(attempt_id))
    with Session(h.engine) as db:
        budget_end = reserved_until(
            db.scalar(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.attempt_id == attempt_id
                )
            )
        )
    assert lease.expires_at_ms == budget_end < lease.deadline_ms
    # Renewal cannot extend it past the remaining budget.
    assert renew(h, lease).expires_at_ms == budget_end
    until(h, budget_end)
    with pytest.raises(ExecutionError):
        renew(h, lease)


def test_a_lease_is_at_most_sixty_seconds_even_with_a_longer_deadline(activity):
    h = activity
    # A 120 s step timeout and 90 s of reserved active time: the 60 s cap binds.
    _, attempt_id = h.ready("activity_slow", active_ms=90000)
    lease = h.claim(h.dispatch(attempt_id))
    with Session(h.engine) as db:
        now = database_now_ms(db)
    assert lease.deadline_ms - now > 60000
    assert 59000 < lease.expires_at_ms - now <= 60000
    assert renew(h, lease).expires_at_ms - now <= 60000 + 1000


def test_spent_active_time_refuses_dispatch_and_the_sweeper_retires_it(activity):
    from lotuspond.execution.activity_records import reserved_until

    h = activity
    execution_id, attempt_id = h.ready("activity_read", active_ms=1000)
    with Session(h.engine) as db:
        spent = reserved_until(
            db.scalar(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.attempt_id == attempt_id
                )
            )
        )
    with Session(h.engine) as db, db.begin():
        h.leases.expire_reservation(db, attempt_id)  # not yet spent: nothing changes
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "reserved"
    until(h, spent)
    with pytest.raises(ExecutionError, match="budget_exceeded"):
        h.dispatch(attempt_id)
    # Stand-in for Task 11's sweeper: the spent, never-started attempt is retired.
    with Session(h.engine) as db, db.begin():
        h.leases.expire_reservation(db, attempt_id)
    with Session(h.engine) as db:
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            )
        )
        assert db.get(ExecutionAttempt, attempt_id).state == "failed"
        # Its reserved active time ran out unused: nothing ran, so nothing is charged.
        assert (reservation.state, reservation.observed_cost_microusd) == ("released", None)
        assert reservation.compute_stopped_at_ms is not None
        assert charged(db, reservation) == {0}
        assert db.scalar(select(ExecutionCapacity.active)) == 0  # the slot is returned
        assert db.get(Execution, execution_id).runnable_at_ms is not None
    h.drive()
    retry = attempt_at(h, execution_id, "read", ordinal=2)
    with Session(h.engine) as db:
        assert retry is not None and db.get(ExecutionAttempt, retry).state == "reserved"


def test_only_a_stopped_never_claimed_attempt_is_released(activity):
    """The refund's guard: a confirmed stop, no lease ever, and a cancelled or failed end."""
    from lotuspond.models import ExecutionReconciliationObservation

    h = activity
    _, reserved = h.ready("activity_read", capacity=3)
    _, claimed = h.ready("activity_read", capacity=3)
    lease = h.claim(h.dispatch(claimed))
    h.result(claimed, lease, code="unavailable")

    def release(attempt_id):
        with Session(h.engine) as db, db.begin():
            h.budgets.release_unstarted(db, attempt_id, h.principal_runtime.identity.recovery_epoch)

    # Claimed, so it may have run: it stays charged as observed, failed or not.
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, claimed).state == "failed"
    with pytest.raises(ExecutionError, match="conflict"):
        release(claimed)
    # Never claimed but not stopped: its reservation still holds the compute slot,
    # whatever the attempt's state says.
    for state in ("reserved", "failed"):
        with Session(h.engine) as db, db.begin():
            db.get(ExecutionAttempt, reserved).state = state
        with pytest.raises(ExecutionError, match="conflict"):
            release(reserved)
    with Session(h.engine) as db, db.begin():
        attempt = db.get(ExecutionAttempt, reserved)
        observation = ExecutionReconciliationObservation(
            workspace_id=attempt.workspace_id,
            project_id=attempt.project_id,
            environment_id=attempt.environment_id,
            execution_id=attempt.execution_id,
            attempt_id=reserved,
            source_id="test",
            observation_id="stopped",
            digest="0" * 64,
            code="compute_stopped",
            observed_at_ms=database_now_ms(db),
        )
        db.add(observation)
        db.flush()
        h.budgets.confirm_stopped(db, reserved, observation.id)
    # Stopped and never claimed, but neither cancelled nor failed: an uncertain or
    # still reserved attempt keeps its liability (a restore's "unclaimed" proves nothing).
    for state in ("reserved", "uncertain"):
        with Session(h.engine) as db, db.begin():
            db.get(ExecutionAttempt, reserved).state = state
        with pytest.raises(ExecutionError, match="conflict"):
            release(reserved)

    # Only a `reserved` reservation changes: an uncertain one keeps its liability.
    def reservation_state(db, state=None):
        row = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == reserved
            )
        )
        if state is not None:
            row.state = state
        return row.state

    with Session(h.engine) as db, db.begin():
        db.get(ExecutionAttempt, reserved).state = "failed"
        reservation_state(db, "uncertain")
    release(reserved)
    with Session(h.engine) as db, db.begin():
        assert reservation_state(db) == "uncertain"  # left as it is
        reservation_state(db, "reserved")
    release(reserved)
    release(reserved)  # a repeat changes nothing
    with Session(h.engine) as db:
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == reserved
            )
        )
        assert reservation.state == "released" and charged(db, reservation) == {0}


def test_an_expiry_takes_no_installation_cursor(activity):
    """Finding 11: the sweeper's expiry returns its slot without the cursor."""
    from lotuspond.execution.budgets import installation_active

    from .cursor_checks import without_cursor

    h = activity
    _, attempt_id = h.ready("activity_read")
    envelope = json.loads(h.dispatch(attempt_id).envelope)
    until(h, envelope["expires_at_ms"])

    def expire():
        with Session(h.engine) as db, db.begin():
            return h.leases.expire_delivery(db, envelope["delivery_id"])

    assert without_cursor(h.engine, expire) is True
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "failed"
        assert installation_active(db) == 0
