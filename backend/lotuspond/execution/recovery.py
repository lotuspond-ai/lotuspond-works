"""Recovery fence and restore handling (ADR-0011, Task 11).

The current recovery epoch comes only from the independent RecoveryAuthority,
through the bounded fleet lookup. No restored row supplies an epoch or a
revocation. Orchestration, sweeping and dispatch fence before taking any row lock
and refuse while the authority cannot answer.

After a database restore the authority's epoch is newer than the one a restored
delivery was signed under. Such an attempt, dispatched or claimed before the
restore, may have run after the backup without the restored rows knowing: it
becomes uncertain and is reconciled, never treated as never started. Its runtime
session and delivery are refused by the epoch checks of claims, renewals and
results.

Nothing in the restored rows shows what else ran after the backup: an attempt they
show reserved may have been delivered and run since, and an execution may have
progressed. So every non-terminal execution admitted under an older epoch is held
for reconciliation (owner decision, 2026-10-28) until an owner releases it, once the
runtime journals are reconciled (`ControlService.reconcile_execution`). No restored
attempt is delivered under the new epoch before that: scheduling, dispatch and
reservation expiry skip an execution whose `recovery_epoch` is not current.
"""

from ..fleet.authority import FleetDependencies
from ..models import (
    ExecutionActionIntent,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionLease,
    ExecutionStepInstance,
    ExecutionUsageReservation,
)
from .activity_records import scope_of
from .authorization import scoped
from .errors import ExecutionError, execution_service
from .orchestrator import TERMINAL, clear_runnable

# Attempt states a restored row may show while the runtime had already moved on.
IN_FLIGHT = frozenset({"dispatched", "claimed"})
# E05: an intent no observation or decision has settled (`execution_action_hold_idx`).
UNSETTLED_EFFECTS = ("not_dispatched", "dispatching", "accepted", "unknown")
CLOSED_AUTHORIZATIONS = ("denied", "review_expired", "expired", "withdrawn", "superseded")


class RecoveryFence:
    """`current(db)`: the authority's epoch, or `authority_unavailable`."""

    def __init__(self, fleet: FleetDependencies):
        if type(fleet) is not FleetDependencies or fleet.installation_id is None:
            raise ValueError("Fleet dependencies with an installation are required")
        self.fleet = fleet
        # A synthetic authority never fences a production engine.
        self.test_only = bool(getattr(fleet.recovery, "test_only", False))

    def current(self, db) -> int:
        try:
            return self.fleet.current(self.fleet.installation_id).epoch
        except Exception:
            raise ExecutionError("authority_unavailable") from None


@execution_service
def fence_restored(leases, db, delivery_id: str, *, epoch: int) -> bool:
    """A delivery signed under an older epoch: its attempt becomes uncertain.

    Lock order as for a lapse (`LeaseService._rows`, no capacity): no counter changes,
    the reservation stays reserved as uncertain liability. True when it changed.
    """
    if type(epoch) is not int or epoch < 0:
        raise ExecutionError("authority_unavailable")
    hint = db.get(ExecutionActivityDelivery, delivery_id)
    if hint is None:
        raise ExecutionError("not_found")
    scope = scope_of(hint)
    execution, attempt, reservation, delivery, lease = leases._rows(
        db, scope, hint.attempt_id, counters=False
    )
    if (
        delivery is None
        or delivery.id != hint.id
        or delivery.recovery_epoch >= epoch
        or delivery.status not in ("pending", "leased")
    ):
        return False
    if attempt.state not in IN_FLIGHT:
        # A restore left the attempt and its delivery disagreeing. Its old-epoch
        # delivery is never claimable again, and is never selected again either.
        if delivery.status == "pending":
            delivery.status = "conflict"
            db.flush()
        return False
    if delivery.status == "pending":
        delivery.status = "conflict"  # never claimable under the new epoch
    if lease is not None and lease.status == "active":
        lease.status = "uncertain"
    attempt.state = "uncertain"
    if reservation is not None and reservation.state == "reserved":
        reservation.state = "uncertain"  # unknown usage: reserved, never zero
    db.flush()
    leases._event(db, execution, epoch, "attempt_uncertain", attempt.id, "restored")
    if execution.state not in TERMINAL and execution.state != "reconciliation_required":
        execution.state = "reconciliation_required"
        execution.reason = "uncertain"
        db.flush()
        leases._event(db, execution, epoch, "reconciliation_required", attempt.id, "restored")
    return True


def restored(execution, epoch: int) -> bool:
    """Admitted or released under an older recovery epoch, and not held yet."""
    return execution.recovery_epoch is not None and execution.recovery_epoch < epoch


def hold_restored(db, execution, epoch: int, event) -> bool:
    """Hold a restored execution for reconciliation; the caller holds its row.

    Every attempt of it that has not ended becomes uncertain, with its reservation
    (unknown usage: reserved, never zero), so nothing delivers, retires or releases it
    as never started:

    - one the restored rows show reserved and undelivered may have been delivered
      and run since the backup;
    - one in flight under the older epoch is fenced as `fence_restored` fences it
      (its pending delivery is never claimable again, its active lease uncertain), so
      the hold never depends on the sweeper reaching that delivery first.

    A started execution becomes `reconciliation_required`; one still `pending` keeps
    its state and admission slot and derives no work while held. `recovery_epoch` is
    cleared until an owner releases it. Lock order: the execution, then its attempts,
    their reservations, deliveries and leases, each by ID; no counter changes.
    `event(code, attempt_id, path=None)` appends an event (detail `restored`). True
    when held.
    """
    if execution.state in TERMINAL or execution.recovery_epoch is None:
        return False
    scope = scope_of(execution)
    attempts = db.scalars(
        scoped(ExecutionAttempt, scope)
        .where(
            ExecutionAttempt.execution_id == execution.id,
            ExecutionAttempt.state.in_(("reserved", *IN_FLIGHT)),
        )
        .order_by(ExecutionAttempt.id)
        .with_for_update()
    ).all()
    ids = [attempt.id for attempt in attempts]

    def rows(model, *where):
        return {
            row.attempt_id: row
            for row in db.scalars(
                scoped(model, scope)
                .where(model.attempt_id.in_(ids), *where)
                .order_by(model.id)
                .with_for_update()
            )
        }

    reservations = rows(ExecutionUsageReservation)
    deliveries = rows(ExecutionActivityDelivery)
    leases = rows(ExecutionLease, ExecutionLease.status == "active")
    for attempt in attempts:
        delivery = deliveries.get(attempt.id)
        if delivery is not None and delivery.recovery_epoch >= epoch:
            continue  # delivered under this epoch: not restored work
        if delivery is not None and delivery.status == "pending":
            delivery.status = "conflict"  # never claimable under the new epoch
        lease = leases.get(attempt.id)
        if lease is not None:
            lease.status = "uncertain"
        reservation = reservations.get(attempt.id)
        if reservation is not None and reservation.state == "reserved":
            reservation.state = "uncertain"
        attempt.state = "uncertain"
        db.flush()
        event("attempt_uncertain", attempt.id)
    hold_old_epoch_intents(db, execution, epoch=epoch, event=event)
    if execution.state not in ("pending", "reconciliation_required"):
        execution.state, execution.reason = "reconciliation_required", "uncertain"
    execution.recovery_epoch = None
    clear_runnable(db, execution)
    db.flush()
    event("reconciliation_required", None)
    return True


def hold_old_epoch_intents(db, execution, *, epoch: int, event) -> int:
    """Hold each unsettled intent of a restored execution with it (E05 decision 13).

    The caller holds the execution row and is holding it (`hold_restored`): this runs
    in that transaction, after its attempts. Every intent created under an older
    epoch whose effect is not settled and whose authorization is still open is held:
    one `intent_held` event each, by ID (`execution_action_hold_idx`), naming the
    intent by its intent step's occurrence path (the event has no intent column). Nothing is
    scheduled: no intent of a held execution is dispatched, read back or decided
    into a dispatch until its owner releases the execution, and then each is read
    back under the new epoch (`InterpreterV2`), never re-delivered. Returns the count.
    """
    intents = list(
        db.scalars(
            scoped(ExecutionActionIntent, scope_of(execution))
            .where(
                ExecutionActionIntent.execution_id == execution.id,
                ExecutionActionIntent.recovery_epoch < epoch,
                ExecutionActionIntent.effect_state.in_(UNSETTLED_EFFECTS),
                ExecutionActionIntent.authorization_state.notin_(CLOSED_AUTHORIZATIONS),
            )
            .order_by(ExecutionActionIntent.recovery_epoch, ExecutionActionIntent.id)
            .with_for_update()
        )
    )
    paths = {
        row.id: row.occurrence_path
        for row in db.scalars(
            scoped(ExecutionStepInstance, scope_of(execution)).where(
                ExecutionStepInstance.id.in_([intent.step_instance_id for intent in intents])
            )
        )
    }
    for intent in intents:
        event("intent_held", None, path=paths[intent.step_instance_id])
    return len(intents)
