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
"""

from ..fleet.authority import FleetDependencies
from ..models import ExecutionActivityDelivery
from .activity_records import scope_of
from .errors import ExecutionError, execution_service
from .orchestrator import TERMINAL

# Attempt states a restored row may show while the runtime had already moved on.
IN_FLIGHT = frozenset({"dispatched", "claimed"})


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

    Lock order as for a lapse (`LeaseService._rows`, no cursor): no counter changes,
    the reservation stays reserved as uncertain liability. True when it changed.
    """
    if type(epoch) is not int or epoch < 0:
        raise ExecutionError("authority_unavailable")
    hint = db.get(ExecutionActivityDelivery, delivery_id)
    if hint is None:
        raise ExecutionError("not_found")
    scope = scope_of(hint)
    execution, attempt, reservation, delivery, lease = leases._rows(
        db, scope, hint.attempt_id, cursor=False
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
