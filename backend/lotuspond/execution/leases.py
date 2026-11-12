"""Activity lease renewal and the sweeper's lapse and expiry transitions (E04).

A lease is at most 60 seconds and never outlives the attempt's deadline. Expiry
records lost authority: it never makes an attempt reclaimable. A lapsed lease
leaves the attempt uncertain and the execution in `reconciliation_required` with
its liability kept; an unclaimed delivery that expires, or a reserved attempt whose
reserved active time ran out before dispatch, fails its attempt, which only a new
ordinal can retry. Task 11's sweeper calls the three sweeper transitions.

Lock order: see `activity_records`. The sweeper paths take no runtime identity and
enter at the workspace: expiry then locks the environment capacity row, because it
returns a compute slot; a lapse changes no counter and skips it.
"""

from hashlib import sha256

from lotuspond_runtime.activity_protocol import MAX_LEASE_MS, ActivityLease
from lotuspond_runtime.errors import FleetError
from sqlalchemy import select

from .. import authority_gate
from ..fleet.authority import RecoverySnapshot, database_now_ms
from ..models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionLease,
    ExecutionReconciliationObservation,
    ExecutionRequest,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    Workspace,
)
from .activity_records import lease_revision, lease_view, reserved_until, scope_of
from .authorization import lock, record, scoped, share
from .dispatch import activity_service, failure_content
from .errors import ExecutionError
from .orchestrator import TERMINAL, mark_runnable, may_run
from .progression import record_event

# One row per declined delivery: makes an equal repeated decline a no-op.
DECLINE_PREFIX = "activity.decline:"


class LeaseService:
    # A runtime's declines per minute: even a broken or hostile runtime cannot fail
    # work much faster than unclaimed deliveries expiring would.
    DECLINES_PER_MINUTE = 6

    def __init__(self, activities):
        if activities is None:
            raise ValueError("Activity service required")
        self.activities = activities
        self.budgets = activities.budgets

    def _epoch(self, epoch):
        """Fenced before any lock: the recovery authority is never called under rows."""
        if epoch is not None:
            if type(epoch) is not int or epoch < 0:
                raise ExecutionError("recovery_unavailable")
            return epoch
        try:
            snapshot = self.budgets._call(
                "recovery", "current", (self.budgets.installation_id,), "recovery_unavailable"
            )
            if (
                type(snapshot) is not RecoverySnapshot
                or not snapshot.active
                or snapshot.installation_id != self.budgets.installation_id
            ):
                raise ValueError
            return snapshot.epoch
        except Exception:
            raise ExecutionError("recovery_unavailable") from None

    def _event(self, db, execution, epoch, code, attempt_id, detail=None, origin="sweeper"):
        a = self.activities
        record_event(
            db,
            a.artifacts,
            a.policy,
            a.installation_id,
            execution,
            epoch,
            code,
            path=None,
            attempt_id=attempt_id,
            detail=detail,
            origin=origin,
        )

    @activity_service
    def renew_activity(self, db, principal, lease: ActivityLease) -> ActivityLease:
        """Extend a live lease under current identity, authority, budget and deadline."""
        a = self.activities
        lease = ActivityLease.model_validate(lease)
        rows = a._locked(db, principal, lease.attempt_id)
        execution, attempt = rows["execution"], rows["attempt"]
        now = a._finish(db, rows["locked"])
        row, delivery = a._held(rows, attempt, principal, now)
        if (
            lease.generation != delivery.generation
            or lease.lease_revision > lease_revision(row)
            or attempt.state != "claimed"
        ):
            raise ExecutionError("conflict")
        # A pause or a cancellation is reported by refusing renewal: the runtime then
        # stops the provider and reports `cancelled` with what it observed.
        if not may_run(execution, rows["step"].occurrence_path) or execution.state in TERMINAL:
            raise ExecutionError("forbidden")
        if rows["parent"] is not None:
            # A child runs only inside its parent's live lease.
            a._held(rows, rows["parent"], principal, now)
        reservation = rows["reservations"].get(attempt.id)
        if reservation is None or reservation.compute_stopped_at_ms is not None:
            raise ExecutionError("conflict")
        records = a.records.load(db, attempt.id)
        grant, tenant, recovery = self.budgets._grants(db, scope_of(execution))
        self.budgets._final(db, rows["actor"], rows["revision"], grant, tenant, recovery)
        if recovery.epoch != delivery.recovery_epoch:
            raise ExecutionError("recovery_unavailable")
        a._verify(delivery, a._trust(scope_of(execution), now))
        # Authority and provider lookups may have outlasted the lease itself.
        now = a._finish(db, rows["locked"])
        if now >= row.expires_at_ms:
            raise ExecutionError("conflict")
        if rows["parent"] is not None:
            a._held(rows, rows["parent"], principal, now)
        # Never past the attempt's deadline or its remaining reserved active time.
        until = reserved_until(reservation)
        if until <= now:
            raise ExecutionError("budget_exceeded")
        expires = min(now + MAX_LEASE_MS, records.deadline_ms, until)
        if expires <= now:
            raise ExecutionError("forbidden")
        if now > row.renewed_at_ms:
            row.renewed_at_ms = now
            row.expires_at_ms = max(row.expires_at_ms, expires)
            db.flush()
        return lease_view(row, delivery, records.deadline_ms)

    def _rows(self, db, scope, attempt_id, *, counters):
        hint = record(db, ExecutionAttempt, scope, attempt_id)
        authority_gate.workspace(db, scope.workspace_id)  # before any row lock
        share(db, select(Workspace).where(Workspace.id == scope.workspace_id))
        if counters:
            lock(db, scoped(ExecutionCapacity, scope))
        execution = record(db, Execution, scope, hint.execution_id, locked=True)
        record(db, ExecutionStepInstance, scope, hint.step_instance_id, locked=True)
        attempt = record(db, ExecutionAttempt, scope, attempt_id, locked=True)
        reservation = lock(
            db,
            scoped(ExecutionUsageReservation, scope).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            ),
        )
        delivery = lock(
            db,
            scoped(ExecutionActivityDelivery, scope).where(
                ExecutionActivityDelivery.attempt_id == attempt_id
            ),
        )
        lease = lock(
            db, scoped(ExecutionLease, scope).where(ExecutionLease.attempt_id == attempt_id)
        )
        return execution, attempt, reservation, delivery, lease

    @activity_service
    def lapse_lease(self, db, lease_id: str, *, epoch=None) -> bool:
        """A lease past its expiry: the attempt is uncertain until reconciled.

        The liability stays reserved, and the execution is not marked runnable:
        only reconciliation can decide what happened. True when it changed.
        """
        epoch = self._epoch(epoch)
        hint = db.get(ExecutionLease, lease_id)
        if hint is None:
            raise ExecutionError("not_found")
        scope = scope_of(hint)
        execution, attempt, reservation, delivery, lease = self._rows(
            db, scope, hint.attempt_id, counters=False
        )
        now = database_now_ms(db)
        if lease is None or lease.status != "active" or now < lease.expires_at_ms:
            return False
        lease.status = "uncertain"
        if attempt.state == "claimed":
            attempt.state = "uncertain"
        if reservation is not None and reservation.state == "reserved":
            reservation.state = "uncertain"  # unknown usage: reserved, never zero
        db.flush()
        self._event(db, execution, epoch, "attempt_uncertain", attempt.id)
        if execution.state not in TERMINAL and execution.state != "reconciliation_required":
            execution.state = "reconciliation_required"
            execution.reason = "uncertain"
            db.flush()
            self._event(db, execution, epoch, "reconciliation_required", attempt.id)
        return True

    @activity_service
    def expire_delivery(self, db, delivery_id: str, *, epoch=None) -> bool:
        """An unclaimed delivery past its expiry fails its attempt and frees its slot.

        Its reservation is released at zero: a runtime invokes nothing before a
        durable claim, so an unclaimed delivery never started. The attempt never gets
        a second delivery; a retry is a new ordinal decided by progression. A delivery
        signed under an older epoch is left to the restore fence (`recovery`): after
        a restore, "unclaimed" in a restored row proves nothing. True when it changed.
        """
        epoch = self._epoch(epoch)
        hint = db.get(ExecutionActivityDelivery, delivery_id)
        if hint is None:
            raise ExecutionError("not_found")
        scope = scope_of(hint)
        execution, attempt, reservation, delivery, _ = self._rows(
            db, scope, hint.attempt_id, counters=True
        )
        now = database_now_ms(db)
        if (
            delivery.status != "pending"
            or now < delivery.expires_at_ms
            or delivery.recovery_epoch != epoch
        ):
            return False
        delivery.status = "conflict"
        db.flush()
        if attempt.state != "dispatched":
            return True
        self._fail_unstarted(
            db, scope, execution, attempt, reservation, "expired:" + delivery.id, now, epoch
        )
        return True

    def _fail_unstarted(
        self,
        db,
        scope,
        execution,
        attempt,
        reservation,
        identifier,
        now,
        epoch,
        *,
        code="timeout_before_dispatch",
        detail="timeout_before_dispatch",
        source="sweeper",
    ):
        """Fail an attempt that never started; compute stop, no charge, due again.

        No runtime ever claimed it, so its reservation is released at zero once the
        stop is confirmed (owner decision, 2026-10-26), as a cancelled one's is. The
        caller holds the workspace, capacity, execution and attempt rows.
        """
        a = self.activities
        handle = a.artifacts._put_internal(db, scope, failure_content(code, now), a.policy)
        attempt.result_artifact_id = handle.id
        attempt.result_artifact_version = handle.version
        attempt.result_sha256 = handle.sha256
        attempt.state = "failed"
        db.flush()
        if reservation is not None and reservation.compute_stopped_at_ms is None:
            observation = ExecutionReconciliationObservation(
                **scope.model_dump(),
                execution_id=execution.id,
                attempt_id=attempt.id,
                source_id=source,
                observation_id=identifier,
                digest=sha256(identifier.encode()).hexdigest(),
                code="compute_stopped",
                observed_at_ms=now,
            )
            db.add(observation)
            db.flush()
            self.budgets.confirm_stopped(db, attempt.id, observation.id)
            self.budgets.release_unstarted(db, attempt.id, epoch)
        self._event(db, execution, epoch, "attempt_failed", attempt.id, detail, origin=source)
        if execution.state not in TERMINAL:
            mark_runnable(db, execution, now)

    @activity_service
    def decline_activity(self, db, principal, delivery_id: str, digest: str) -> None:
        """A runtime refuses a delivery it cannot verify, so it stops heading its polls.

        Owner-approved addition beyond E04 (see the plan). Only the session the
        delivery was dispatched to may decline it, by its exact stored digest, while
        it is pending and unclaimed: an altered or forged envelope declines nothing,
        and a claimed attempt is reported or reconciled, never declined. The attempt
        fails exactly as if its delivery had expired now (`timeout_before_dispatch`,
        compute stop confirmed, reservation released at zero, due again; only the
        event detail says `declined`), so the step's retry classes apply unchanged.
        Churn stays bounded without a charge: the step's `max_attempts` (at most 10
        root attempts), this allowance and the runtime's three declines in a row.
        An equal repeat changes nothing. Like a result it grants nothing, so the
        initiator's current `execute` permission is not required; the runtime's
        current identity, session and recovery epoch are. A runtime may decline at
        most `DECLINES_PER_MINUTE` deliveries a minute. Like a result, it takes no
        installation cursor.
        """
        a = self.activities
        identity = principal.identity
        a.fleet.limit("activity-decline:" + identity.instance_id, self.DECLINES_PER_MINUTE)
        hint = record(db, ExecutionActivityDelivery, identity.scope, delivery_id)
        rows = a._locked(db, principal, hint.attempt_id, counters=True, authority=False)
        execution, attempt = rows["execution"], rows["attempt"]
        delivery = rows["deliveries"].get(attempt.id)
        if (
            delivery is None
            or delivery.id != hint.id
            or delivery.instance_id != identity.instance_id
            or delivery.generation != identity.generation
            or delivery.pool_id != identity.pool_id
        ):
            raise FleetError("unauthenticated")
        if delivery.session_id != principal.session_id or delivery.digest != digest:
            raise ExecutionError("conflict")
        now = a._finish(db, rows["locked"])
        scope = scope_of(execution)
        key = DECLINE_PREFIX + delivery.id
        if db.scalar(scoped(ExecutionRequest, scope).where(ExecutionRequest.request_key == key)):
            return  # an equal repeated decline changes nothing
        if (
            delivery.status != "pending"
            or attempt.state != "dispatched"
            or rows["leases"].get(attempt.id) is not None
        ):
            raise ExecutionError("conflict")
        delivery.status = "conflict"
        db.add(
            ExecutionRequest(
                **scope.model_dump(), execution_id=execution.id, request_key=key, digest=digest
            )
        )
        db.flush()
        self._fail_unstarted(
            db,
            scope,
            execution,
            attempt,
            rows["reservations"].get(attempt.id),
            "declined:" + delivery.id,
            now,
            identity.recovery_epoch,
            code="timeout_before_dispatch",
            detail="declined",
            source="activity",
        )

    @activity_service
    def expire_reservation(self, db, attempt_id: str, *, epoch=None) -> bool:
        """A reserved attempt whose reserved active time ran out before any dispatch.

        Dispatch refuses it (`budget_exceeded`), so without this it would hold its
        compute slot until the execution deadline. It never started: it fails with
        `timeout_before_dispatch`, its compute stop is confirmed and its reservation
        released at zero, and progression may retry with a new ordinal. Task 11's
        sweeper calls this.
        """
        epoch = self._epoch(epoch)
        hint = db.get(ExecutionAttempt, attempt_id)
        if hint is None:
            raise ExecutionError("not_found")
        scope = scope_of(hint)
        execution, attempt, reservation, delivery, _ = self._rows(db, scope, hint.id, counters=True)
        now = database_now_ms(db)
        if (
            attempt.state != "reserved"
            # A restored execution's rows prove nothing about dispatch since the
            # backup: it is held instead (recovery.py).
            or execution.recovery_epoch != epoch
            or delivery is not None
            or reservation is None
            or reservation.compute_stopped_at_ms is not None
            or reserved_until(reservation) > now
        ):
            return False
        self._fail_unstarted(
            db, scope, execution, attempt, reservation, "spent:" + attempt.id, now, epoch
        )
        return True
