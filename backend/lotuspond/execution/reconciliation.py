"""Separately authorized reconciliation of historical activity evidence (E04).

Only an owner or admin, under current recovery authority, can close an uncertain
attempt. The evidence is immutable and checked against the persisted delivery.
It can settle known usage and confirm that compute stopped. It never issues work
and never turns unverifiable evidence into success: the attempt ends `failed`
with `uncertain`, and progression decides what follows. E03 `record_status` is
not involved.

Lock order: the route has authorized the actor (workspace and account rows held),
so the installation cursor is taken with NOWAIT (see `dispatch`), then capacity,
execution, step, attempt, reservation, delivery and lease.
"""

from hashlib import sha256

from lotuspond_runtime.activity_protocol import HistoricalObservation
from lotuspond_sdk.activity import ArtifactHandle
from sqlalchemy import select

from ..fleet.authority import RecoverySnapshot, database_now_ms
from ..models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionLease,
    ExecutionReconciliationObservation,
    ExecutionStepInstance,
    ExecutionUsageReservation,
)
from .authorization import lock, reauthorize, record, scoped
from .contracts import canonical_value
from .dispatch import (
    activity_service,
    close_children,
    cursor_nowait,
    failure_content,
    keep_interrupted,
)
from .errors import ExecutionError
from .orchestrator import TERMINAL, mark_runnable
from .progression import INTERRUPTED, record_event

SOURCE = "historical"


class ReconciliationService:
    def __init__(self, activities):
        if activities is None:
            raise ValueError("Activity service required")
        self.activities = activities
        self.budgets = activities.budgets

    def _recovery(self):
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
            return snapshot
        except Exception:
            raise ExecutionError("recovery_unavailable") from None

    @activity_service
    def reconcile_activity(self, db, actor, observation: HistoricalObservation) -> None:
        observation = HistoricalObservation.model_validate(observation)
        # Current recovery authority, fenced before the cursor or any execution row:
        # a restored or fenced installation cannot close historical attempts.
        recovery = self._recovery()
        actor = reauthorize(db, actor, "reconcile")
        scope = actor.scope
        cursor_nowait(db, self.budgets.installation_id)
        hint = record(db, ExecutionAttempt, scope, observation.attempt_id)
        lock(db, scoped(ExecutionCapacity, scope))
        execution = record(db, Execution, scope, hint.execution_id, locked=True)
        record(db, ExecutionStepInstance, scope, hint.step_instance_id, locked=True)
        attempt = record(db, ExecutionAttempt, scope, hint.id, locked=True)
        reservation = lock(
            db,
            scoped(ExecutionUsageReservation, scope).where(
                ExecutionUsageReservation.attempt_id == attempt.id
            ),
        )
        delivery = lock(
            db,
            scoped(ExecutionActivityDelivery, scope).where(
                ExecutionActivityDelivery.attempt_id == attempt.id
            ),
        )
        lock(db, scoped(ExecutionLease, scope).where(ExecutionLease.attempt_id == attempt.id))
        digest = sha256(canonical_value(observation.model_dump(mode="json"))).hexdigest()
        existing = db.scalar(
            scoped(ExecutionReconciliationObservation, scope).where(
                ExecutionReconciliationObservation.attempt_id == attempt.id,
                ExecutionReconciliationObservation.source_id == SOURCE,
                ExecutionReconciliationObservation.observation_id == observation.observation_id,
            )
        )
        if existing is not None:
            if existing.digest != digest:
                raise ExecutionError("conflict")
            return  # immutable: an equal observation changes nothing
        if (
            delivery is None
            or delivery.digest != observation.delivery_sha256
            or delivery.recovery_epoch > recovery.epoch
        ):
            raise ExecutionError("conflict")
        if attempt.state != "uncertain" or reservation is None:
            raise ExecutionError("conflict")
        now = database_now_ms(db)
        if observation.usage.completed_at_ms > now:
            raise ExecutionError("invalid_request")
        if observation.result is not None:
            # Evidence must name an artifact of this scope; it is never adopted.
            handle = ArtifactHandle.model_validate(observation.result)
            row = db.scalar(
                scoped(ExecutionArtifact, scope).where(ExecutionArtifact.id == handle.id)
            )
            if row is None or (row.version, row.sha256) != (handle.version, handle.sha256):
                raise ExecutionError("conflict")
        # A root's unstarted children can never run now. A child that may still run
        # (claimed, or uncertain and unstopped) is reconciled first: refused here.
        closed = (
            close_children(db, self.budgets, scope, attempt, now)
            if attempt.parent_attempt_id is None
            else []
        )
        evidence = ExecutionReconciliationObservation(
            **scope.model_dump(),
            execution_id=execution.id,
            attempt_id=attempt.id,
            source_id=SOURCE,
            observation_id=observation.observation_id,
            digest=digest,
            code="compute_stopped",
            observed_at_ms=now,
        )
        db.add(evidence)
        db.flush()
        # Known usage settles; unknown usage stays reserved as uncertain liability.
        # The usage observation ID is derived, so any valid evidence ID fits.
        self.budgets.settle_attempt(
            db,
            attempt.id,
            SOURCE + ":" + sha256(observation.observation_id.encode()).hexdigest(),
            observation.usage,
        )
        if reservation.compute_stopped_at_ms is None:
            self.budgets.confirm_stopped(db, attempt.id, evidence.id)
        a = self.activities
        if keep_interrupted(a.artifacts, db, scope, attempt):
            ended = ("cancelled", attempt.id, INTERRUPTED)  # a pause stopped it (Task 11)
        else:
            content = failure_content("uncertain", now)
            handle = a.artifacts._put_internal(db, scope, content, a.policy)
            attempt.result_artifact_id = handle.id
            attempt.result_artifact_version = handle.version
            attempt.result_sha256 = handle.sha256
            attempt.state = "failed"
            db.flush()
            ended = ("attempt_failed", attempt.id, "uncertain")
        events = [("reconciled", attempt.id, {"actor_user_id": actor.user_id}), ended]
        events += [("cancelled", child, "parent_ended") for child in closed]
        for code, attempt_id, detail in events:
            record_event(
                db,
                a.artifacts,
                a.policy,
                a.installation_id,
                execution,
                recovery.epoch,
                code,
                path=None,
                attempt_id=attempt_id,
                detail=detail,
                origin="reconciliation",
            )
        remaining = db.scalar(
            select(ExecutionAttempt.id)
            .where(
                ExecutionAttempt.workspace_id == scope.workspace_id,
                ExecutionAttempt.execution_id == execution.id,
                ExecutionAttempt.state == "uncertain",
            )
            .limit(1)
        )
        if remaining is None and execution.state == "reconciliation_required":
            # Back to what was requested; progression and controls take it from there.
            from .control import resumed_state

            execution.state, execution.reason = resumed_state(db, execution), None
            db.flush()
        if execution.state not in TERMINAL and remaining is None:
            # Progression decides what the failed attempt means for its step.
            mark_runnable(db, execution, now)
