"""Execution projections and runtime-reported events (ADR-0011, Task 11).

Projections are read models over committed rows. `ExecutionView` carries the
observed state and, separately, the requested one: a requested pause or
cancellation is never presented as observed.

Orchestrator events are gap-free by construction (`record_event`). Runtime-reported
events (the SDK's `RuntimeEvent`, validated as admitted) keep their own event ID,
digest and source sequence:

- the event that follows its source's last applied sequence is applied, then every
  buffered one it unblocks, in order;
- one beyond that is buffered as pending, in one of at most 100 gap slots per
  execution;
- an equal repeat changes nothing.

Only a runtime holding the execution's live work under the current epoch (a pending
delivery or an active lease for it) reports. A conflicting digest for an event ID,
another event at a sequence already taken, a stale sequence, or a gap beyond the
bound fences the execution for reconciliation with a fixed reason. A late event
behind a gap that reconciliation abandoned is refused without fencing again. A runtime event never completes, fails or otherwise moves an
execution: completion is never inferred from one. Action-review and outcome event
kinds belong to E05/E06 and are refused. No E04 component reports a supplemental
control event; such events would use their own versioned closed model, never a
change to SDK event bytes.
"""

from hashlib import sha256

from lotuspond_runtime.protocol import Scope
from lotuspond_sdk.executable.wire import (
    ACTION_EVENT_KINDS,
    canonical_wire_json,
    validate_runtime_event,
)
from sqlalchemy import func, or_, select

from ..fleet.authority import database_now_ms
from ..models import Execution, ExecutionActivityDelivery, ExecutionEvent, ExecutionLease
from .admission import execution_view
from .artifacts import ArtifactPolicy
from .authorization import reauthorize, record, scoped
from .contracts import ExecutionView
from .errors import ExecutionError, execution_service
from .orchestrator import TERMINAL
from .progression import record_event

FAMILY = "sdk_runtime_event"
GAP_SLOTS = 100
# A runtime clock this far ahead of database time is not believed.
CLOCK_SKEW_MS = 30000


class EventProjection:
    def __init__(self, *, artifacts, artifact_policy, installation_id, recovery):
        if artifacts is None or recovery is None:
            raise ValueError("Artifacts and recovery fence are required")
        self.artifacts, self.recovery = artifacts, recovery
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        if self.policy.source_id is not None:
            raise ValueError("Events require workspace response policy")
        self.installation_id = installation_id

    def _fence(self, db):
        try:
            epoch = self.recovery.current(db)
            if type(epoch) is not int or epoch < 0:
                raise ValueError
            return epoch
        except Exception:
            raise ExecutionError("authority_unavailable") from None

    def _rows(self, db, execution, source_id, generation):
        return scoped(ExecutionEvent, _scope(execution)).where(
            ExecutionEvent.execution_id == execution.id,
            ExecutionEvent.family == FAMILY,
            ExecutionEvent.source_id == source_id,
            ExecutionEvent.source_generation == generation,
        )

    def _abandoned(self, db, execution, event, prior):
        """Whether reconciliation abandoned buffered events above this missing one."""
        rows = self._rows(db, execution, event.source_id, event.source_generation)
        return (
            db.scalar(
                rows.where(
                    ExecutionEvent.application_state == "conflict",
                    ExecutionEvent.source_sequence > event.source_sequence,
                    ExecutionEvent.source_sequence <= prior,
                ).limit(1)
            )
            is not None
        )

    def _fence_execution(self, db, execution, epoch, reason):
        """Stop the execution for reconciliation; the event itself is not stored."""
        if execution.state not in TERMINAL and execution.state != "reconciliation_required":
            execution.state, execution.reason = "reconciliation_required", "uncertain"
            db.flush()
            record_event(
                db,
                self.artifacts,
                self.policy,
                self.installation_id,
                execution,
                epoch,
                "reconciliation_required",
                detail=reason,
                origin="runtime_event",
            )
        return execution_view(execution)

    def _store(self, db, execution, epoch, event, content, digest, *, slot):
        handle = self.artifacts._put_internal(db, _scope(execution), content, self.policy)
        if handle.sha256 != digest:
            raise ExecutionError("conflict")
        sequence = execution.next_event_sequence
        db.add(
            ExecutionEvent(
                **_scope(execution).model_dump(),
                execution_id=execution.id,
                attempt_id=None,
                sequence=sequence,
                family=FAMILY,
                format_version=1,
                installation_id=self.installation_id,
                recovery_epoch=epoch,
                source_id=event.source_id,
                source_generation=event.source_generation,
                source_sequence=event.source_sequence,
                event_id=event.event_id,
                event_artifact_id=handle.id,
                event_artifact_version=handle.version,
                digest=digest,
                code=event.event_kind,
                occurred_at_ms=event.observed_at_ms,
                application_state="pending" if slot else "applied",
                gap_slot=slot,
            )
        )
        execution.next_event_sequence = sequence + 1
        db.flush()

    @execution_service
    def apply_event(self, db, source_identity: str, event) -> ExecutionView:
        event = validate_runtime_event(event)
        if event.event_kind in ACTION_EVENT_KINDS:
            raise ExecutionError("unsupported_step")
        if source_identity != event.source_id:
            raise ExecutionError("forbidden")
        # Current recovery, fenced before any row lock; an older epoch is refused.
        epoch = self._fence(db)
        if event.installation_id != self.installation_id or event.recovery_epoch != epoch:
            raise ExecutionError("forbidden")
        scope = Scope(
            workspace_id=event.workspace_id,
            project_id=event.project_id,
            environment_id=event.environment_id,
        )
        execution = record(db, Execution, scope, event.execution_id, locked=True)
        # Only a runtime holding this execution's live work reports on it: a pending
        # delivery or an active lease, signed for it under the current epoch.
        delivery = ExecutionActivityDelivery
        live = or_(
            delivery.status == "pending",
            select(ExecutionLease.id)
            .where(ExecutionLease.delivery_id == delivery.id, ExecutionLease.status == "active")
            .exists(),
        )
        if not db.scalar(
            scoped(delivery, scope)
            .where(
                delivery.execution_id == execution.id,
                delivery.instance_id == source_identity,
                delivery.generation == event.source_generation,
                delivery.recovery_epoch == epoch,
                live,
            )
            .limit(1)
        ):
            raise ExecutionError("forbidden")
        if event.observed_at_ms > database_now_ms(db) + CLOCK_SKEW_MS:
            raise ExecutionError("invalid_request")
        content = canonical_wire_json(event)
        digest = sha256(content).hexdigest()
        rows = self._rows(db, execution, event.source_id, event.source_generation)
        known = db.scalar(
            scoped(ExecutionEvent, scope).where(
                ExecutionEvent.execution_id == execution.id,
                ExecutionEvent.source_id == event.source_id,
                ExecutionEvent.event_id == event.event_id,
            )
        )
        if known is not None:
            if known.digest == digest and known.source_sequence == event.source_sequence:
                return execution_view(execution)  # an equal repeat changes nothing
            return self._fence_execution(db, execution, epoch, "event_conflict")
        if execution.state in TERMINAL:
            raise ExecutionError("conflict")
        taken = db.scalar(rows.where(ExecutionEvent.source_sequence == event.source_sequence))
        prior = (
            db.scalar(
                select(func.max(ExecutionEvent.source_sequence)).where(
                    ExecutionEvent.workspace_id == execution.workspace_id,
                    ExecutionEvent.execution_id == execution.id,
                    ExecutionEvent.family == FAMILY,
                    ExecutionEvent.source_id == event.source_id,
                    ExecutionEvent.source_generation == event.source_generation,
                    ExecutionEvent.application_state != "pending",
                )
            )
            or 0
        )
        if (
            taken is None
            and event.source_sequence <= prior
            and self._abandoned(db, execution, event, prior)
        ):
            # Behind a gap that reconciliation abandoned: late, refused, never refenced.
            raise ExecutionError("conflict")
        if taken is not None or event.source_sequence <= prior:
            return self._fence_execution(db, execution, epoch, "event_conflict")
        if event.source_sequence > prior + 1:
            used = set(
                db.scalars(
                    select(ExecutionEvent.gap_slot).where(
                        ExecutionEvent.workspace_id == execution.workspace_id,
                        ExecutionEvent.execution_id == execution.id,
                        ExecutionEvent.gap_slot.is_not(None),
                    )
                )
            )
            free = next((s for s in range(1, GAP_SLOTS + 1) if s not in used), None)
            if free is None:
                return self._fence_execution(db, execution, epoch, "event_gap")
            self._store(db, execution, epoch, event, content, digest, slot=free)
            return execution_view(execution)
        self._store(db, execution, epoch, event, content, digest, slot=None)
        # Apply what this event unblocks, in source order.
        following = prior + 2
        for row in db.scalars(
            rows.where(ExecutionEvent.application_state == "pending")
            .order_by(ExecutionEvent.source_sequence)
            .with_for_update()
        ):
            if row.source_sequence != following:
                break
            row.application_state, row.gap_slot = "applied", None
            following += 1
        db.flush()
        return execution_view(execution)


def _scope(execution) -> Scope:
    return Scope(
        workspace_id=execution.workspace_id,
        project_id=execution.project_id,
        environment_id=execution.environment_id,
    )


@execution_service
def project_execution(db, actor, execution_id: str) -> ExecutionView:
    """The execution as committed: observed state and requested state, separately."""
    actor = reauthorize(db, actor, "read")
    return execution_view(record(db, Execution, actor.scope, execution_id))
