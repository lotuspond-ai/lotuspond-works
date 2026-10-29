"""Task 9: activity transitions racing each other, the sweeper and E03 revocation.

Each race runs real concurrent transactions against PostgreSQL. A holder transaction
keeps the execution row so both racers queue on real locks, and is released at the
boundary that decides the outcome (an expiry), in real database time. The assertion
is always the same shape: exactly one consistent outcome, and no deadlock.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from lotuspond_runtime.activity_protocol import ActivityResult
from lotuspond_runtime.errors import FleetError
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionLease,
    ExecutionUsageReservation,
    RuntimeInstance,
)

from .activity_fixtures import activity as activity
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_dispatch import race
from .test_leases import renew, until
from .test_progression import flows as flows
from .test_publication import harness as harness


def attempt(fn):
    try:
        return fn()
    except (ExecutionError, FleetError) as error:
        return error


@contextmanager
def held_until(h, execution_id, at_ms):
    """Hold the execution row; both racers queue behind it until `at_ms`."""
    with ThreadPoolExecutor(2) as pool, Session(h.engine) as holder:
        with holder.begin():
            holder.scalar(select(Execution).where(Execution.id == execution_id).with_for_update())
            yield pool
            until(h, at_ms)


def states(h, attempt_id):
    with Session(h.engine) as db:
        lease = db.scalar(select(ExecutionLease).where(ExecutionLease.attempt_id == attempt_id))
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            )
        )
        return (
            db.get(ExecutionAttempt, attempt_id).state,
            None if lease is None else lease.status,
            reservation,
            db.scalar(select(ExecutionCapacity.active)),
        )


def test_concurrent_claims_renewals_and_results_stay_consistent(activity):
    h = activity
    # 120 s timeout and 60 s of reserved active time: no expiry can interfere.
    _, attempt_id = h.ready("activity_slow", active_ms=60000)
    delivery = h.dispatch(attempt_id)
    first, second = race(lambda: h.claim(delivery), lambda: h.claim(delivery))
    assert first == second  # one lease, returned to both racing claims
    renewed, reported = race(
        lambda: renew(h, first), lambda: h.result(attempt_id, first, code="unavailable")
    )
    assert reported is None
    # Either renewed before the result, or refused because the lease had stopped.
    assert getattr(renewed, "code", None) in (None, "conflict")
    state, lease, _, active = states(h, attempt_id)
    assert (state, lease, active) == ("failed", "stopped", 0)


# Released well before the deciding boundary, right at it, or well after it: the
# first and last pin each outcome; the boundary run may land on either side.
RELEASES = {"before": -1200, "boundary": -20, "after": 300}


@pytest.mark.parametrize("release", sorted(RELEASES))
def test_claim_racing_delivery_expiry_ends_in_exactly_one_outcome(activity, release):
    h = activity
    execution_id, attempt_id = h.ready("activity_read")  # the delivery expires in 2 s
    delivery = h.dispatch(attempt_id)
    envelope = json.loads(delivery.envelope)

    def expire():
        with Session(h.engine) as db, db.begin():
            h.leases.expire_delivery(db, envelope["delivery_id"])

    with held_until(h, execution_id, envelope["expires_at_ms"] + RELEASES[release]) as pool:
        racers = [pool.submit(attempt, lambda: h.claim(delivery)), pool.submit(attempt, expire)]
    outcomes = [racer.result(timeout=30) for racer in racers]
    until(h, envelope["expires_at_ms"])
    expire()  # the sweeper runs again later, as it would
    state, lease, reservation, active = states(h, attempt_id)
    assert {"before": lease is not None, "after": lease is None}.get(release, True)
    if lease is not None:
        # The claim won before expiry: a live lease, never a release at zero.
        assert (state, lease) == ("claimed", "active") and reservation.state == "reserved"
        assert not isinstance(outcomes[0], Exception)  # the claim returned the lease
    else:
        # Expiry won: never claimed, so stopped, returned and released at zero.
        assert state == "failed"
        assert (reservation.state, reservation.observed_cost_microusd) == ("released", None)
        assert reservation.compute_stopped_at_ms is not None and active == 0
        assert isinstance(outcomes[0], ExecutionError) and outcomes[0].code == "conflict"
    with Session(h.engine) as db:
        delivery_row = db.get(ExecutionActivityDelivery, envelope["delivery_id"])
        assert delivery_row.status == ("leased" if lease else "conflict")


@pytest.mark.parametrize("release", sorted(RELEASES))
def test_result_racing_lease_lapse_ends_in_exactly_one_outcome(activity, release):
    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    handle = h.store_value(attempt_id, None)
    with Session(h.engine) as db:
        lease_id = db.scalar(select(ExecutionLease.id))

    def report():
        with Session(h.engine) as db, db.begin():
            h.activities.lock_cursor(db)
            h.activities.record_activity_result(
                db,
                h.principal_runtime,
                ActivityResult(
                    attempt_id=attempt_id,
                    lease_revision=lease.lease_revision,
                    result=handle,
                    code="succeeded",
                    usage=dict(
                        input_tokens=1,
                        output_tokens=1,
                        cost_microusd=1,
                        completed_at_ms=database_now_ms(db) - 1,
                    ),
                ),
            )

    def lapse():
        with Session(h.engine) as db, db.begin():
            h.leases.lapse_lease(db, lease_id)

    with held_until(h, execution_id, lease.expires_at_ms + RELEASES[release]) as pool:
        racers = [pool.submit(attempt, report), pool.submit(attempt, lapse)]
    outcomes = [racer.result(timeout=30) for racer in racers]
    until(h, lease.expires_at_ms)
    lapse()  # the sweeper runs again later
    state, lease_status, reservation, active = states(h, attempt_id)
    assert {"before": state == "succeeded", "after": state == "uncertain"}.get(release, True)
    if state == "succeeded":
        # The result won inside its lease: recorded, settled, stopped.
        assert lease_status == "stopped" and reservation.state == "settled" and active == 0
        assert outcomes[0] is None
    else:
        # The lapse won: uncertain, liability kept, and the late result refused.
        assert (state, lease_status) == ("uncertain", "uncertain")
        assert reservation.compute_stopped_at_ms is None and active == 1
        assert isinstance(outcomes[0], ExecutionError) and outcomes[0].code == "conflict"
        with Session(h.engine) as db:
            assert db.get(Execution, execution_id).state == "reconciliation_required"


def test_renewal_racing_e03_instance_revocation_never_outlives_it(activity):
    from lotuspond.fleet.catalog import InstalledCatalog
    from lotuspond.fleet.identity import IdentityManagement

    h = activity
    _, attempt_id = h.ready("activity_slow", active_ms=60000)
    lease = h.claim(h.dispatch(attempt_id))
    instance_id = h.principal_runtime.identity.instance_id

    def revoke():
        with Session(h.engine) as db, db.begin():
            IdentityManagement(
                db, catalog=InstalledCatalog(), actor_id=h.actor.user_id
            ).revoke_instance(h.scope, instance_id, "revoke-during-renewal")

    renewed, revoked = race(lambda: renew(h, lease), revoke)
    assert revoked is None  # revocation always completes
    # The renewal either finished before the revocation or was refused by it.
    assert getattr(renewed, "code", None) in (None, "unauthenticated")
    with Session(h.engine) as db:
        assert db.get(RuntimeInstance, instance_id).status == "revoked"
    # Never afterwards: a revoked identity cannot renew, whatever won the race.
    refused = attempt(lambda: renew(h, lease))
    assert isinstance(refused, FleetError) and refused.code == "unauthenticated"
