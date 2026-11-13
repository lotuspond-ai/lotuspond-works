"""Pause, resume and cancel, and owner reconciliation of a fenced execution (Task 11).

A control request is persisted with its outbox row under the execution row lock, and
it sets the requested state there. Task 9's dispatch, claim and renewal check that
state under the same lock, so a committed cancellation or pause precedes any later
dispatch. The orchestrator applies it in its next transaction:

- pause: a running or waiting execution becomes `paused`; one still `pending` keeps
  its admission slot and stays `pending`. Dispatch and new waits stop. Root attempts
  that have not started (reserved, or delivered but never claimed) are released
  with their compute slots; claimed ones are stopped cooperatively (renewal is
  refused) and report what they observed. Each such attempt is interrupted: on
  resume its step gets a new attempt, never counted as a retry. Deadlines, budgets
  and wait deadlines keep running, and signals are still accepted;
- resume: back to `waiting` while a retained open wait exists, else `running`;
- cancel: reserved attempts and open waits are cancelled, claimed attempts are
  cancelled cooperatively (renewal is refused), and the execution is observed
  `cancelled` only once nothing it started may still run, or after reconciliation.

The requested state is never presented as the observed one. Cancellation is final.
A request equal to an earlier one with the same ID changes nothing, even after the
authority it needed is gone; a different one with that ID conflicts. Owners and
admins control any execution in scope, editors only their own. Resume needs the
requester's and the initiator's current authority, a deployment revision that may
still run, current grants, the remaining deadline and budget left.

Reconciliation (`reconcile_execution`) needs an owner or admin, current recovery
authority and a confirmed compute stop for every uncertain attempt. It fails those
attempts as `uncertain` (an interrupted one stays interrupted) and lets progression
decide: a retry with a new ordinal and a fresh reservation under the step's policy,
or failure. It never turns unverifiable data into success.

It also releases an execution held after a restore (`recovery.hold_restored`, owner
decision, 2026-10-28), `pending` or `reconciliation_required`, once its owner has
reconciled the runtime journals. An attempt the hold made uncertain, never
delivered in the restored rows and with no journal evidence, is one no runtime ran
since the backup: it is reserved again, and may be delivered under this epoch. One
delivered under the older epoch still needs its confirmed stop.
"""

from hashlib import sha256

from lotuspond_runtime.protocol import CanonicalUUID
from pydantic import TypeAdapter
from sqlalchemy import literal, or_, select

from .. import authority_gate
from ..fleet.authority import database_now_ms
from ..models import (
    Execution,
    ExecutionActionCommand,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionControlRequest,
    ExecutionDeploymentRevision,
    ExecutionEvent,
    ExecutionOutbox,
    ExecutionReconciliationObservation,
    ExecutionRequest,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    ExecutionWait,
    Workspace,
)
from .activity_records import initiator, scope_of
from .admission import execution_view
from .artifacts import ArtifactPolicy
from .authorization import lock, mark_replay, reauthorize, record, scoped, share
from .budgets import liability
from .contracts import ControlRequest, ExecutionView, canonical_value
from .deployments import execution_gates, load_deployment
from .dispatch import failure_content, keep_interrupted, mark_pause_stops
from .errors import ExecutionError, execution_service
from .orchestrator import TERMINAL, mark_runnable
from .progression import INTERRUPTED, record_event
from .recovery import restored

TARGET = {"pause": "paused", "resume": "running", "cancel": "cancelled"}


def retained_clause():
    """The one retention predicate (E04 finding 2): the wait's capacity record exists.

    Releasing a wait adds a separate `capacity.wait-release:` record and keeps this
    one, so a wait once listed stays listed.
    """
    return (
        select(ExecutionRequest.id)
        .where(
            ExecutionRequest.workspace_id == ExecutionWait.workspace_id,
            ExecutionRequest.project_id == ExecutionWait.project_id,
            ExecutionRequest.environment_id == ExecutionWait.environment_id,
            ExecutionRequest.request_key == literal("capacity.wait:") + ExecutionWait.id,
        )
        .exists()
    )


def wait_retained(db, wait) -> bool:
    return bool(
        db.scalar(
            select(ExecutionWait.id).where(ExecutionWait.id == wait.id).where(retained_clause())
        )
    )


def listed_wait_clause():
    """Waits a listing shows: every non-open wait, and an open one once retained."""
    return or_(ExecutionWait.state != "open", retained_clause())


def _retained_open_wait(db, execution) -> bool:
    retained = retained_clause()
    return bool(
        db.scalar(
            scoped(ExecutionWait, scope_of(execution))
            .where(ExecutionWait.execution_id == execution.id, ExecutionWait.state == "open")
            .where(retained)
            .limit(1)
        )
    )


def resumed_state(db, execution) -> str:
    """The observed state an execution returns to after a pause or a fence."""
    if execution.requested_state == "paused":
        return "paused"
    return "waiting" if _retained_open_wait(db, execution) else "running"


def _delivered(db, scope, attempt) -> bool:
    """Whether the rows hold any delivery of this attempt, under any epoch: an
    activity delivery, or (E05) an action command."""
    for model in (ExecutionActivityDelivery, ExecutionActionCommand):
        if db.scalar(scoped(model, scope).where(model.attempt_id == attempt.id).limit(1)):
            return True
    return False


def _write(db, scope, attempt) -> bool:
    """An action's command attempt (E05): only a read-back settles it, never a release."""
    step = record(db, ExecutionStepInstance, scope, attempt.step_instance_id)
    return attempt.parent_attempt_id is None and step.occurrence_path.endswith("#dispatch")


def observe_control(db, execution, event) -> bool:
    """Apply a requested pause or resume to the observed state; the row lock is held.

    `event(code)` appends the control event. Cancellation is applied by progression
    (cancel undispatched work, then the terminal state once contained). True when the
    observed state changed.
    """
    if execution.state in TERMINAL or execution.state == "reconciliation_required":
        return False
    if execution.requested_state == "paused" and execution.state in ("running", "waiting"):
        execution.state, execution.reason = "paused", "control"
        db.flush()
        event("paused")
        return True
    if execution.requested_state == "running" and execution.state == "paused":
        execution.state, execution.reason = resumed_state(db, execution), None
        db.flush()
        event("resumed")
        return True
    return False


class ControlService:
    def __init__(self, *, budgets, artifacts, artifact_policy, installation_id, recovery):
        if any(item is None for item in (budgets, artifacts, recovery)):
            raise ValueError("Budgets, artifacts and recovery fence are required")
        self.budgets, self.artifacts, self.recovery = budgets, artifacts, recovery
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        if self.policy.source_id is not None:
            raise ValueError("Controls require workspace response policy")
        self.installation_id = installation_id

    def _fence(self, db):
        try:
            epoch = self.recovery.current(db)
            if type(epoch) is not int or epoch < 0:
                raise ValueError
            return epoch
        except Exception:
            raise ExecutionError("authority_unavailable") from None

    def _event(self, db, execution, epoch, code, *, attempt_id=None, detail=None):
        record_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            code,
            attempt_id=attempt_id,
            detail=detail,
            origin="reconciliation",
        )

    @execution_service
    def request_control(self, db, actor, execution_id: str, request) -> ExecutionView:
        request = ControlRequest.model_validate(request)
        execution_id = TypeAdapter(CanonicalUUID).validate_python(execution_id, strict=True)
        resume = request.action == "resume"
        grants = refused = None
        if resume:
            # Provider lookups come before every lock. A refusal waits until an
            # equal repeat of a recorded request has been answered.
            try:
                grants = self.budgets._grants(db, actor.scope)
            except ExecutionError as error:
                refused = error
        # The fair gates first, before any row lock (`authority_gate`).
        execution_gates(db, actor.scope, execution_id, "control", [actor.user_id])
        actor = reauthorize(db, actor, "control")
        scope = actor.scope
        digest = sha256(canonical_value(request.model_dump(mode="json"))).hexdigest()
        if resume and refused is None:
            # The initiator's account and the deployment rows precede the execution
            # row (Task 5's order): resume needs the initiator's current authority
            # and a deployment revision that may still run.
            hint = record(db, Execution, scope, execution_id)
            if not self._recorded(db, scope, hint, request, digest):
                try:
                    load_deployment(
                        db,
                        initiator(db, hint),
                        hint.deployment_revision_id,
                        dependencies=self.budgets.deployments,
                    )
                except ExecutionError as error:
                    refused = error
        execution = record(db, Execution, scope, execution_id, locked=True)
        if actor.role not in ("owner", "admin") and execution.initiating_user_id != actor.user_id:
            raise ExecutionError("forbidden")
        if self._recorded(db, scope, execution, request, digest):
            mark_replay(db)
            return execution_view(execution)  # an equal repeat changes nothing
        target = TARGET[request.action]
        if execution.state in TERMINAL or (
            execution.requested_state == "cancelled" and target != "cancelled"
        ):
            raise ExecutionError("conflict")  # cancellation is final
        now = database_now_ms(db)
        if target == "running" and execution.requested_state == "paused":
            if refused is not None:
                raise refused
            if now >= execution.deadline_ms:
                raise ExecutionError("conflict")  # nothing left to resume into
            grant, tenant, _ = grants
            self.budgets._expiry(db, grant, tenant)  # after every lock
            self._remaining(db, scope, execution, grant, tenant)
        row = ExecutionControlRequest(
            **scope.model_dump(),
            execution_id=execution.id,
            request_id=request.request_id,
            action=request.action,
            digest=digest,
            initiating_user_id=actor.user_id,
            initiating_api_key_id=actor.api_key_id,
        )
        db.add(row)
        db.flush()
        if execution.requested_state != target:
            execution.requested_state = target
            db.add(
                ExecutionOutbox(
                    **scope.model_dump(),
                    execution_id=execution.id,
                    message_id=row.id,
                    kind="control",
                    digest=digest,
                    available_at_ms=now,
                )
            )
            db.flush()
            if target == "paused":
                mark_pause_stops(db, scope, execution)
            mark_runnable(db, execution, now)
        return execution_view(execution)

    @staticmethod
    def _recorded(db, scope, execution, request, digest) -> bool:
        """True for an equal repeat of a recorded request; another under its ID conflicts."""
        prior = db.scalar(
            scoped(ExecutionControlRequest, scope).where(
                ExecutionControlRequest.execution_id == execution.id,
                ExecutionControlRequest.request_id == request.request_id,
            )
        )
        if prior is not None and prior.digest != digest:
            raise ExecutionError("conflict")
        return prior is not None

    def _remaining(self, db, scope, execution, grant, tenant):
        """Resume needs budget left: refused (`budget_exceeded`) when a limit is spent.

        The same limits and liabilities a reservation checks; the next reservation
        still checks its own demand.
        """
        revision = record(db, ExecutionDeploymentRevision, scope, execution.deployment_revision_id)
        limits, _, _ = self.budgets._limits(db, scope, revision, grant, tenant)
        reservations = list(
            db.scalars(
                scoped(ExecutionUsageReservation, scope).where(
                    ExecutionUsageReservation.execution_id == execution.id
                )
            )
        )
        now = database_now_ms(db)
        for field, limit in (
            ("input_tokens", "max_input_tokens"),
            ("output_tokens", "max_output_tokens"),
            ("cost_microusd", "max_cost_microusd"),
            ("active_ms", "active_ms"),
        ):
            if sum(liability(row, field, now) for row in reservations) >= limits[limit]:
                raise ExecutionError("budget_exceeded")
        if len(reservations) >= limits["max_steps"]:
            raise ExecutionError("budget_exceeded")

    @execution_service
    def reconcile_execution(self, db, actor, execution_id: str) -> ExecutionView:
        execution_id = TypeAdapter(CanonicalUUID).validate_python(execution_id, strict=True)
        # Current recovery authority, fenced before any row lock.
        epoch = self._fence(db)
        actor = reauthorize(db, actor, "reconcile")
        scope = actor.scope
        # The workspace, then capacity, before the execution (Task 5's order): an
        # action write the release confirms stopped frees its compute slot.
        authority_gate.workspace(db, scope.workspace_id)
        share(db, select(Workspace).where(Workspace.id == scope.workspace_id))
        lock(db, scoped(ExecutionCapacity, scope))
        execution = record(db, Execution, scope, execution_id, locked=True)
        if restored(execution, epoch):
            raise ExecutionError("conflict")  # the restore hold comes first
        held = execution.recovery_epoch is None
        if execution.state != "reconciliation_required" and not (
            held and execution.state == "pending"
        ):
            raise ExecutionError("conflict")
        attempts = list(
            db.scalars(
                scoped(ExecutionAttempt, scope)
                .where(
                    ExecutionAttempt.execution_id == execution.id,
                    ExecutionAttempt.state == "uncertain",
                )
                .order_by(ExecutionAttempt.id)
                .with_for_update()
            )
        )
        released, writes = [], []
        for attempt in attempts:
            reservation = lock(
                db,
                scoped(ExecutionUsageReservation, scope).where(
                    ExecutionUsageReservation.attempt_id == attempt.id
                ),
            )
            stopped = db.scalar(
                scoped(ExecutionReconciliationObservation, scope).where(
                    ExecutionReconciliationObservation.attempt_id == attempt.id,
                    ExecutionReconciliationObservation.code == "compute_stopped",
                )
            )
            delivered = _delivered(db, scope, attempt)
            if _write(db, scope, attempt):
                # E05 decision 13: an action write is never reserved or delivered again,
                # nor failed here. It stays uncertain and progression reads it back
                # under the current epoch; one a runtime received must have stopped.
                if delivered and (
                    reservation is None
                    or reservation.compute_stopped_at_ms is None
                    or stopped is None
                ):
                    raise ExecutionError("conflict")
                writes.append((attempt, reservation, held and not delivered))
                continue
            if held and stopped is None and not delivered:
                released.append((attempt, reservation))  # in no runtime's journal
                continue
            if reservation is None or reservation.compute_stopped_at_ms is None or stopped is None:
                # Something may still run: an owner reconciles only after the stop.
                raise ExecutionError("conflict")
        now = database_now_ms(db)
        ended = []
        for attempt, reservation, undelivered in writes:
            if (
                undelivered
                and reservation is not None
                and reservation.compute_stopped_at_ms is None
            ):
                # In no runtime's journal and never commanded in these rows: nothing
                # runs it, so its slot is freed; its liability stays uncertain.
                observation = ExecutionReconciliationObservation(
                    **scope.model_dump(),
                    execution_id=execution.id,
                    attempt_id=attempt.id,
                    source_id="reconciliation",
                    observation_id="released-undelivered",
                    digest=sha256(b"released-undelivered:" + attempt.id.encode()).hexdigest(),
                    code="compute_stopped",
                    observed_at_ms=now,
                )
                db.add(observation)
                db.flush()
                self.budgets.confirm_stopped(db, attempt.id, observation.id)
        for attempt, reservation in released:
            attempt.state = "reserved"
            if reservation is not None and reservation.state == "uncertain":
                reservation.state = "reserved"
            ended.append(("attempt_reserved", attempt.id, "released"))
        returned = {attempt.id for attempt, _ in released} | {a.id for a, _, _ in writes}
        attempts = [attempt for attempt in attempts if attempt.id not in returned]
        for attempt in attempts:
            if keep_interrupted(self.artifacts, db, scope, attempt):
                # A pause stopped it: the step resumes with a new attempt.
                ended.append(("cancelled", attempt.id, INTERRUPTED))
                continue
            handle = self.artifacts._put_internal(
                db, scope, failure_content("uncertain", now), self.policy
            )
            attempt.result_artifact_id = handle.id
            attempt.result_artifact_version = handle.version
            attempt.result_sha256 = handle.sha256
            attempt.state = "failed"
            ended.append(("attempt_failed", attempt.id, "uncertain"))
        db.flush()
        # Buffered runtime events behind a gap are abandoned; their source continues
        # after the highest sequence seen.
        for event in db.scalars(
            scoped(ExecutionEvent, scope)
            .where(
                ExecutionEvent.execution_id == execution.id,
                ExecutionEvent.application_state == "pending",
            )
            .with_for_update()
        ):
            event.application_state, event.gap_slot = "conflict", None
        self._event(db, execution, epoch, "reconciled", detail={"actor_user_id": actor.user_id})
        for code, attempt_id, detail in ended:
            self._event(db, execution, epoch, code, attempt_id=attempt_id, detail=detail)
        if held:
            execution.recovery_epoch = epoch  # current again: the hold is released
        # Back to what was requested; progression and controls take it from there. A
        # held execution still `pending` keeps its admission slot and state.
        if execution.state == "reconciliation_required":
            execution.state, execution.reason = resumed_state(db, execution), None
        db.flush()
        mark_runnable(db, execution, now)
        return execution_view(execution)
