"""Settle uncertain writes by read-back, before any resend (E05 Task 8).

An uncertain write never gets a blind replacement. Progression schedules a
read-back instead (`CreateReadback`): an E04 connector-read attempt of the write's
reconciliation read operation, called with the intent's exact target and input, on
the dispatch step's `<path>#reconcile` record. Read-back n of command attempt k has
ordinal `100k + n` (`budgets.READBACK_STRIDE`), so each read-back names the write it
settles. Its reservation shares the slot the write retains while that write's
compute is not confirmed stopped (`budgets._readback_parent`), so read-backs never
wait for new capacity; the sweeper reserves them (`reserve_readback`).

`ReadbackService.record` applies the catalog's `effect_rules` and `absence_rules` to
the result and writes one `ExecutionActionObservation`. It changes only the
observation and the intent's effect column; progression decides what follows:

- effect present: the step succeeds, nothing is resent;
- qualified absence (absence rules hold, and the operation declares `target_key` or
  the write's compute stop is confirmed): one resend under the same intent, decision
  and idempotency key when the operation declares `target_key` and the dispatch
  step's retry policy has attempts left, else the step fails `not_applied`;
- unqualified absence, or `MAX_READBACKS` unknown results: the execution becomes
  `reconciliation_required` (reason `action_uncertain`) until an owner or admin
  resolves the action (`ResolutionService`).

A confirmed stop is the write's reservation `compute_stopped_at_ms`: set by a live
receipt or stop, a historical report (`action_dispatch`), or a `compute_stopped`
resolution. A lapse alone confirms nothing: the write may still be in flight.

Evidence an owner may cite is registered per intent as `ExecutionRequest` rows
(`action-evidence:<intent>:<artifact>:<version>`, digest = the artifact digest):
read-back results and historical receipts. A resolution never turns missing
evidence into an observed effect, and never resends.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from lotuspond_runtime.protocol import CanonicalUUID, Scope
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.package import (
    ActionDispatchStep,
    ActionIntentStep,
    DecisionStep,
    IterationStep,
    ParallelStep,
    SubworkflowStep,
)
from lotuspond_sdk.executable.wire import ReconciliationObservedPayload
from pydantic import TypeAdapter
from sqlalchemy import and_, func, or_, select

from .. import authority_gate
from ..fleet.authority import database_now_ms
from ..models import (
    Execution,
    ExecutionActionCommand,
    ExecutionActionIntent,
    ExecutionActionObservation,
    ExecutionActionReceipt,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionHumanTask,
    ExecutionReconciliationObservation,
    ExecutionRequest,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    Workspace,
)
from .action_contracts import ActionView, ResolutionRequest
from .action_rules import ABSENT, PRESENT, UNKNOWN, settle
from .artifacts import ArtifactPolicy
from .authorization import (
    authorize_execution,
    lock,
    mark_replay,
    reauthorize,
    record,
    scoped,
    share,
)
from .budgets import (
    DISPATCH_SUFFIX,
    READBACK_STRIDE,
    READBACK_SUFFIX,
    BudgetDemand,
    _readback_parent,
)
from .contracts import canonical_value
from .deployments import execution_gates, gates
from .errors import ExecutionError
from .expressions import ExpressionError, ValueEnvironment, typed_schema
from .orchestrator import TERMINAL, mark_runnable
from .program import ProgramError, load_program, region_body, resolve_occurrence

EVIDENCE_PREFIX = "action-evidence:"
RECONCILIATION_RULE = "reconciliation"
RESOLUTION_VERSION = "1"
# Resolutions that ask for another bounded read-back (the second also confirms the
# write's compute stop first), and those that settle the step.
REREAD = frozenset({"readback", "compute_stopped"})
FINAL = frozenset({"effect_present", "close_unknown"})


def _scope(row) -> Scope:
    return Scope(
        workspace_id=row.workspace_id, project_id=row.project_id, environment_id=row.environment_id
    )


def _steps(body):
    for step in body.steps:
        yield step
        if isinstance(step, DecisionStep):
            yield from _steps(step.then_body)
            yield from _steps(step.else_body)
        elif isinstance(step, IterationStep):
            yield from _steps(step.body)
        elif isinstance(step, ParallelStep):
            for branch in step.branches:
                yield from _steps(branch.body)


@dataclass(frozen=True)
class WriteSpec:
    """A dispatch step's write and its reconciliation read, from the pinned catalog."""

    dispatch: ActionDispatchStep
    intent_step: ActionIntentStep
    connector: object  # the declared `ArtifactRef`
    document: object  # the connector's catalog document
    write: object  # `ConnectorOperation`, effect write, read-back reconciliation
    read: object  # `ConnectorOperation`, effect read

    @property
    def target_key(self) -> bool:
        return self.write.idempotency == "target_key"

    @property
    def definitions(self) -> list:
        return [item.model_dump(mode="json") for item in self.document.schemas]

    @property
    def rule_version(self) -> str:
        return f"{self.connector.name}/{self.connector.version}/{self.write.id}"


def write_spec(program, ref) -> WriteSpec:
    """The write a dispatch occurrence (`ref`) performs; `ProgramError` when unpinned."""
    node = ref.step
    if not isinstance(node, ActionDispatchStep):
        raise ProgramError("occurrence_unresolved")
    source = node.intent.source
    intent_step = next(
        (
            s
            for s in _steps(ref.package.body)
            if s.id == getattr(source, "id", None) and isinstance(s, ActionIntentStep)
        ),
        None,
    )
    if intent_step is None:
        raise ProgramError("integrity")
    connector = next(
        (d.ref for d in ref.package.connectors if d.id == intent_step.connector_ref), None
    )
    document = None if connector is None else program.catalog.get(connector.sha256)
    if document is None:
        raise ProgramError("integrity")
    operations = {op.id: op for op in document.definition.operations}
    write = operations.get(intent_step.operation)
    if write is None or write.effect != "write" or write.reconciliation.mode != "read_back":
        raise ProgramError("integrity")
    read = operations.get(write.reconciliation.read_operation)
    if read is None or read.effect != "read":
        raise ProgramError("integrity")
    return WriteSpec(node, intent_step, connector, document, write, read)


def dispatch_ref(program, path):
    """The dispatch step occurrence behind a `#dispatch` or `#reconcile` record path."""
    for suffix in (DISPATCH_SUFFIX, READBACK_SUFFIX):
        if path.endswith(suffix):
            ref = resolve_occurrence(program, path[: -len(suffix)])
            if ref.selector is not None or not isinstance(ref.step, ActionDispatchStep):
                raise ProgramError("occurrence_unresolved")
            return ref
    raise ProgramError("occurrence_unresolved")


# ---------------------------------------------------------------- evidence


def _evidence_key(intent_id, handle) -> str:
    return f"{EVIDENCE_PREFIX}{intent_id}:{handle.id}:{handle.version}"


def register_evidence(db, scope, execution_id, intent_id, handle: ArtifactHandle) -> None:
    """Record an artifact as evidence of this intent's effect (idempotent)."""
    key = _evidence_key(intent_id, handle)
    existing = db.scalar(scoped(ExecutionRequest, scope).where(ExecutionRequest.request_key == key))
    if existing is not None:
        if existing.digest != handle.sha256 or existing.execution_id != execution_id:
            raise ExecutionError("conflict")
        return
    db.add(
        ExecutionRequest(
            **scope.model_dump(),
            execution_id=execution_id,
            request_key=key,
            digest=handle.sha256,
        )
    )
    db.flush()


def recorded_evidence(db, scope, intent_id) -> tuple[ArtifactHandle, ...]:
    prefix = f"{EVIDENCE_PREFIX}{intent_id}:"
    rows = db.scalars(
        scoped(ExecutionRequest, scope)
        .where(ExecutionRequest.request_key.startswith(prefix, autoescape=True))
        .order_by(ExecutionRequest.request_key)
        .limit(64)
    )
    found = []
    for row in rows:
        artifact_id, _, version = row.request_key[len(prefix) :].rpartition(":")
        found.append(ArtifactHandle(id=artifact_id, version=int(version), sha256=row.digest))
    return tuple(found)


def _resolution_key(request) -> str:
    """A resolution's observation ID: bounded, whatever its 128-character ID."""
    return "resolution:" + sha256(request.resolution_id.encode()).hexdigest()


def _evidence_ref(handle, source_kind):
    return {
        "artifact_id": handle.id,
        "sha256": handle.sha256,
        "media_type": "application/json",
        "classification": "internal",
        "source_kind": source_kind,
    }


def _payload(intent, result, rule_id, rule_version, evidence, qualified) -> tuple[bytes, dict]:
    payload = ReconciliationObservedPayload(
        intent_sha256=intent.intent_sha256,
        observation=result,
        rule_id=rule_id,
        rule_version=rule_version,
        evidence_refs=evidence,
        qualified_absence=qualified,
    )
    value = payload.model_dump(mode="json")
    return canonical_value(value), value


def _write_attempt(db, scope, execution, step, ordinal):
    """The `#dispatch` record's attempt `ordinal` beside a `#reconcile` record."""
    sibling = db.scalar(
        scoped(ExecutionStepInstance, scope).where(
            ExecutionStepInstance.execution_id == execution.id,
            ExecutionStepInstance.occurrence_path
            == step.occurrence_path[: -len(READBACK_SUFFIX)] + DISPATCH_SUFFIX,
        )
    )
    if sibling is None or sibling.node_id != step.node_id:
        raise ProgramError("integrity")
    attempt = db.scalar(
        scoped(ExecutionAttempt, scope).where(
            ExecutionAttempt.execution_id == execution.id,
            ExecutionAttempt.step_instance_id == sibling.id,
            ExecutionAttempt.parent_attempt_id.is_(None),
            ExecutionAttempt.ordinal == ordinal,
        )
    )
    if attempt is None:
        raise ProgramError("integrity")
    return attempt


def _intent_of(db, artifacts, scope, attempt, keys=None):
    """The intent a command attempt dispatches, from its own input record."""
    content = artifacts._get_internal(
        db,
        scope,
        ArtifactHandle(
            id=attempt.input_artifact_id,
            version=attempt.input_artifact_version,
            sha256=attempt.input_sha256,
        ),
        keys=keys,
    )
    value = json.loads(content)
    if type(value) is not dict or set(value) != {"intent_id", "decision_id"}:
        raise ProgramError("integrity")
    return value["intent_id"]


def _intent_step_path(program, region, step_id):
    """Occurrence path of intent step `step_id` as region `region` sees it: its own
    step, else the nearest enclosing region's (as `_outer` reads outer values), never
    across a subworkflow call."""
    segments = region.split("/") if region else []
    for depth in range(len(segments), -1, -1):
        candidate = "/".join(segments[:depth])
        if any(step.id == step_id for step in region_body(program, candidate)[2].steps):
            return (candidate + "/" if candidate else "") + step_id
        if depth:
            owner = resolve_occurrence(program, "/".join(segments[:depth]))
            if isinstance(owner.step, SubworkflowStep) and owner.selector == "call":
                break
    raise ProgramError("integrity")


def _restored_intent(db, scope, execution, program, step):
    """The intent a `#reconcile` record's dispatch step names, restored and never
    dispatched in these rows (read-back ordinal 0)."""
    ref = dispatch_ref(program, step.occurrence_path)
    source = getattr(ref.step.intent, "source", None)
    if getattr(source, "kind", None) != "step" or ref.step.intent.pointer != "":
        raise ProgramError("integrity")
    base = step.occurrence_path[: -len(READBACK_SUFFIX)]
    path = _intent_step_path(program, base.rpartition("/")[0], source.id)
    intent = db.scalar(
        scoped(ExecutionActionIntent, scope)
        .join(
            ExecutionStepInstance,
            ExecutionStepInstance.id == ExecutionActionIntent.step_instance_id,
        )
        .where(
            ExecutionActionIntent.execution_id == execution.id,
            ExecutionStepInstance.occurrence_path == path,
        )
        .with_for_update(of=ExecutionActionIntent)
    )
    if (
        intent is None
        or execution.recovery_epoch is None
        or intent.recovery_epoch >= execution.recovery_epoch
    ):
        raise ProgramError("integrity")
    return intent


def stop_confirmed(db, scope, attempt_id) -> bool:
    reservation = db.scalar(
        scoped(ExecutionUsageReservation, scope).where(
            ExecutionUsageReservation.attempt_id == attempt_id
        )
    )
    return reservation is not None and reservation.compute_stopped_at_ms is not None


# ---------------------------------------------------------------- read-backs


class ReadbackService:
    """Creates read-back attempts and records what they observed (pure of providers)."""

    def __init__(self, *, artifacts, artifact_policy, installation_id):
        if artifacts is None:
            raise ValueError("Artifacts are required")
        self.artifacts = artifacts
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        self.installation_id = installation_id

    def schedule(self, db, execution, intent, *, path, node_id, ordinal, reason, epoch, keys=None):
        """Create read-back attempt `ordinal` of `path` (`<dispatch>#reconcile`).

        Its input is the intent's exact target and input (owner decision 18). The
        caller holds the execution row; no reservation is taken here (the sweeper
        shares the write's retained slot, or the scheduler reserves one).
        """
        from .actions import load_intent
        from .progression import record_event

        scope = _scope(execution)
        if not path.endswith(READBACK_SUFFIX) or intent.execution_id != execution.id:
            raise ProgramError("integrity")
        verified = load_intent(
            db,
            scope,
            intent.id,
            artifacts=self.artifacts,
            keys=keys,
            installation_id=self.installation_id,
        )
        instance = db.scalar(
            scoped(ExecutionStepInstance, scope).where(
                ExecutionStepInstance.execution_id == execution.id,
                ExecutionStepInstance.occurrence_path == path,
            )
        )
        if instance is None:
            instance = ExecutionStepInstance(
                **scope.model_dump(),
                execution_id=execution.id,
                occurrence_path=path,
                node_id=node_id,
            )
            db.add(instance)
            db.flush()
        elif instance.node_id != node_id:
            raise ProgramError("integrity")
        existing = db.scalar(
            scoped(ExecutionAttempt, scope).where(
                ExecutionAttempt.step_instance_id == instance.id,
                ExecutionAttempt.ordinal == ordinal,
            )
        )
        if existing is not None:
            return None  # a repeated transition creates nothing
        handle = self.artifacts._put_internal(
            db,
            scope,
            canonical_value({"target": verified.target, "input": verified.input}),
            self.policy,
            keys=keys,
        )
        attempt = ExecutionAttempt(
            **scope.model_dump(),
            execution_id=execution.id,
            step_instance_id=instance.id,
            ordinal=ordinal,
            input_artifact_id=handle.id,
            input_artifact_version=handle.version,
            input_sha256=handle.sha256,
            state="reserved",
        )
        db.add(attempt)
        db.flush()
        record_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "attempt_reserved",
            path=path,
            attempt_id=attempt.id,
            detail=reason,
            keys=keys,
        )
        return attempt

    def record(self, db, execution, epoch, attempt_id, *, program=None, keys=None):
        """Apply the write's rules to a finished read-back: one observation, and the
        intent's effect column. Nothing else changes; progression decides what follows."""
        from .actions import load_intent, record_sdk_event

        scope = _scope(execution)
        attempt = record(db, ExecutionAttempt, scope, attempt_id, locked=True)
        step = record(db, ExecutionStepInstance, scope, attempt.step_instance_id)
        if attempt.execution_id != execution.id or attempt.state not in (
            "succeeded",
            "failed",
            "cancelled",
        ):
            raise ProgramError("integrity")
        observation_id = "readback:" + attempt.id
        program = program or load_program(db, scope, execution)
        if attempt.ordinal // READBACK_STRIDE == 0:
            # Task 10: a restored intent these rows never dispatched; no write to name.
            write_attempt, intent = None, _restored_intent(db, scope, execution, program, step)
        else:
            write_attempt = _write_attempt(
                db, scope, execution, step, attempt.ordinal // READBACK_STRIDE
            )
            intent_id = _intent_of(db, self.artifacts, scope, write_attempt, keys)
            intent = record(db, ExecutionActionIntent, scope, intent_id, locked=True)
        existing = db.scalar(
            scoped(ExecutionActionObservation, scope).where(
                ExecutionActionObservation.intent_id == intent.id,
                ExecutionActionObservation.observation_id == observation_id,
            )
        )
        if existing is not None:
            return existing
        spec = write_spec(program, dispatch_ref(program, step.occurrence_path))
        result, evidence, handle = UNKNOWN, [], None
        if attempt.state == "succeeded" and attempt.result_artifact_id is not None:
            handle = ArtifactHandle(
                id=attempt.result_artifact_id,
                version=attempt.result_artifact_version,
                sha256=attempt.result_sha256,
            )
            evidence = [_evidence_ref(handle, "target_readback")]
            try:
                value = json.loads(self.artifacts._get_internal(db, scope, handle, keys=keys))
                definitions = spec.definitions
                readback = typed_schema(
                    value, spec.read.output_schema.model_dump(mode="json"), definitions
                )
                verified = load_intent(
                    db,
                    scope,
                    intent.id,
                    artifacts=self.artifacts,
                    keys=keys,
                    program=program,
                    installation_id=self.installation_id,
                )
                expected = ValueEnvironment(
                    input=typed_schema(
                        verified.input, spec.write.input_schema.model_dump(mode="json"), definitions
                    )
                )
                reconciliation = spec.write.reconciliation
                result = settle(
                    reconciliation.effect_rules,
                    reconciliation.absence_rules,
                    readback,
                    expected,
                    definitions,
                )
            except (ExpressionError, ValueError):
                result = UNKNOWN  # an unreadable or ill-typed result settles nothing
        # A late receipt reporting the effect contradicts an absence (the target's read
        # model may lag): never qualified, so an owner decides, citing that receipt.
        reported = (
            None
            if write_attempt is None
            else db.scalar(
                scoped(ExecutionActionReceipt, scope).where(
                    ExecutionActionReceipt.attempt_id == write_attempt.id,
                    ExecutionActionReceipt.status.in_(("accepted", "observed_effect")),
                )
            )
        )
        # No write in these rows: none can still run once its owner released the
        # execution with every runtime journal reconciled (E04's restore hold).
        qualified = (
            result == ABSENT
            and reported is None
            and (
                spec.target_key
                or write_attempt is None
                or stop_confirmed(db, scope, write_attempt.id)
            )
        )
        content, payload = _payload(
            intent, result, RECONCILIATION_RULE, spec.rule_version, evidence, qualified
        )
        row = ExecutionActionObservation(
            **scope.model_dump(),
            execution_id=execution.id,
            intent_id=intent.id,
            attempt_id=attempt.id,
            observation_id=observation_id,
            payload_bytes=content,
            payload_sha256=sha256(content).hexdigest(),
            result=result,
            qualified=qualified,
            rule_id=RECONCILIATION_RULE,
            rule_version=spec.rule_version,
            source="readback",
            actor_user_id=None,
        )
        db.add(row)
        intent.effect_state = {PRESENT: "observed", ABSENT: "absent"}.get(result, "unknown")
        db.flush()
        if handle is not None:
            register_evidence(db, scope, execution.id, intent.id, handle)
        record_sdk_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "reconciliation_observed",
            payload,
            attempt_id=attempt.id,
            keys=keys,
        )
        return row


def readback_candidates(limit):
    """Read-backs awaiting a reservation whose write still retains its slot (plain
    read); `reserve_readback` rechecks under locks."""
    from sqlalchemy.orm import aliased

    write = aliased(ExecutionAttempt)
    write_step = aliased(ExecutionStepInstance)
    held = aliased(ExecutionUsageReservation)
    # Only read-backs whose write still retains its slot: one whose write is stopped
    # is the scheduler's, and must never fill this pass's batch (no starvation).
    retains = (
        select(write.id)
        .join(write_step, write_step.id == write.step_instance_id)
        .join(held, held.attempt_id == write.id)
        .where(
            write.execution_id == ExecutionAttempt.execution_id,
            write.parent_attempt_id.is_(None),
            write.state == "uncertain",
            write.ordinal == ExecutionAttempt.ordinal // READBACK_STRIDE,
            write_step.node_id == ExecutionStepInstance.node_id,
            write_step.occurrence_path
            == func.left(
                ExecutionStepInstance.occurrence_path,
                func.length(ExecutionStepInstance.occurrence_path) - len(READBACK_SUFFIX),
            )
            + DISPATCH_SUFFIX,
            held.compute_stopped_at_ms.is_(None),
        )
        .exists()
    )
    return (
        select(ExecutionAttempt.id)
        .join(ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id)
        .join(Execution, Execution.id == ExecutionAttempt.execution_id)
        .where(
            ExecutionStepInstance.occurrence_path.like("%" + READBACK_SUFFIX),
            ExecutionAttempt.state == "reserved",
            Execution.state == "running",
            ~select(ExecutionUsageReservation.id)
            .where(ExecutionUsageReservation.attempt_id == ExecutionAttempt.id)
            .exists(),
            retains,
        )
        .order_by(ExecutionAttempt.id)
        .limit(limit)
    )


def reserve_readback(db, budgets, demand_provider, attempt_id) -> bool:
    """Reserve one read-back against its write's retained slot (sweeper item).

    Its own transaction, in the scheduler's lock order: the fair gates, the
    workspace, the initiator's authority, then `reserve_attempt(shared=True)`, which
    takes the capacity row before the execution and never the installation cursor.
    """
    from ..security import Principal

    hint = db.get(ExecutionAttempt, attempt_id)
    if hint is None:
        return False
    scope = _scope(hint)
    execution = record(db, Execution, scope, hint.execution_id)
    step = record(db, ExecutionStepInstance, scope, hint.step_instance_id)
    parent = _readback_parent(db, scope, step, hint)
    if parent is None or stop_confirmed(db, scope, parent):
        return False  # no retained slot to share: the scheduler reserves its own
    authority_gate.workspace(db, scope.workspace_id)
    gates(db, scope, execution.deployment_revision_id, users=[execution.initiating_user_id])
    share(db, select(Workspace).where(Workspace.id == scope.workspace_id))
    principal = Principal(
        execution.initiating_api_key_id or execution.initiating_user_id,
        scope.workspace_id,
        "service" if execution.initiating_api_key_id else "member",
        None if execution.initiating_api_key_id else execution.initiating_user_id,
    )
    actor = authorize_execution(db, principal, scope, "execute")
    try:
        demand = BudgetDemand.model_validate(demand_provider.demand(attempt_id))
    except Exception:
        raise ExecutionError("price_unavailable") from None
    budgets.reserve_attempt(db, actor, attempt_id, demand, shared=True)
    return True


# ---------------------------------------------------------------- resolution


def action_view(db, scope, intent) -> ActionView:
    instance = record(db, ExecutionStepInstance, scope, intent.step_instance_id)
    task = db.scalar(
        scoped(ExecutionHumanTask, scope).where(ExecutionHumanTask.intent_id == intent.id)
    )
    receipt = db.scalar(
        scoped(ExecutionActionReceipt, scope)
        .where(ExecutionActionReceipt.intent_id == intent.id)
        .order_by(ExecutionActionReceipt.created_at.desc(), ExecutionActionReceipt.id)
        .limit(1)
    )
    return ActionView(
        action_id=intent.action_id,
        step_id=instance.node_id,
        occurrence_path=instance.occurrence_path,
        intent_sha256=intent.intent_sha256,
        authorization=intent.authorization_state,
        effect=intent.effect_state,
        outcome=intent.outcome_state,
        task_id=None if task is None else task.id,
        receipt_status=None if receipt is None else receipt.status,
        evidence=recorded_evidence(db, scope, intent.id),
    )


class ResolutionService:
    """An owner's or admin's resolution of an action that awaits one (spec, "Read-back").

    Only an action whose execution is `reconciliation_required` with reason
    `action_uncertain` (an unqualified absence, or read-backs that stayed unknown)
    can be resolved, under the current recovery epoch, by a member session with the
    `reconcile` permission. Each resolution is an immutable observation (source
    `resolution`) keyed by its `resolution_id`; an equal retry replays it.
    """

    def __init__(self, *, artifacts, artifact_policy, budgets, recovery, installation_id):
        if any(x is None for x in (artifacts, budgets, recovery)):
            raise ValueError("Artifacts, budgets and recovery are required")
        self.artifacts, self.budgets, self.recovery = artifacts, budgets, recovery
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        self.installation_id = installation_id
        self.readbacks = ReadbackService(
            artifacts=artifacts, artifact_policy=artifact_policy, installation_id=installation_id
        )

    def _epoch(self, db):
        try:
            epoch = self.recovery.current(db)
            if type(epoch) is not int or epoch < 0:
                raise ValueError
            return epoch
        except Exception:
            raise ExecutionError("recovery_unavailable") from None

    def resolve(self, db, actor, execution_id, action_id, request: ResolutionRequest) -> ActionView:
        from .actions import record_sdk_event
        from .progression import record_event

        request = ResolutionRequest.model_validate(request)
        execution_id = TypeAdapter(CanonicalUUID).validate_python(execution_id, strict=True)
        epoch = self._epoch(db)
        # The fair gates first, before any row lock; then current authority.
        execution_gates(db, actor.scope, execution_id, "reconcile", [actor.user_id])
        actor = reauthorize(db, actor, "reconcile")
        if actor.api_key_id is not None or actor.user_id is None:
            raise ExecutionError("forbidden")  # an owner's or admin's session only
        scope = actor.scope
        # The workspace, then capacity, before the execution (Task 5's order): a
        # confirmed stop changes the capacity count.
        authority_gate.workspace(db, scope.workspace_id)
        share(db, select(Workspace).where(Workspace.id == scope.workspace_id))
        lock(db, scoped(ExecutionCapacity, scope))
        execution = record(db, Execution, scope, execution_id, locked=True)
        intent = db.scalar(
            scoped(ExecutionActionIntent, scope)
            .where(
                ExecutionActionIntent.execution_id == execution.id,
                ExecutionActionIntent.action_id == action_id,
            )
            .with_for_update()
        )
        if intent is None:
            raise ExecutionError("not_found")
        existing = db.scalar(
            scoped(ExecutionActionObservation, scope).where(
                ExecutionActionObservation.intent_id == intent.id,
                ExecutionActionObservation.observation_id == _resolution_key(request),
            )
        )
        if existing is not None:
            cited = [
                (ref["artifact_id"], ref["sha256"])
                for ref in json.loads(existing.payload_bytes)["evidence_refs"]
            ]
            wanted = (
                [] if request.evidence is None else [(request.evidence.id, request.evidence.sha256)]
            )
            if (
                existing.rule_id != "resolution-" + request.kind
                or existing.actor_user_id != actor.user_id
                or cited != wanted
            ):
                raise ExecutionError("conflict")
            mark_replay(db)
            return action_view(db, scope, intent)
        if execution.state in TERMINAL:
            raise ExecutionError("conflict")
        # Held after a restore (Task 10): only a write's confirmed stop, which its
        # owner's release needs. Released: an older-epoch intent is resolved like any.
        held = execution.recovery_epoch is None
        if (held and request.kind != "compute_stopped") or (
            not held and execution.recovery_epoch != epoch
        ):
            raise ExecutionError("conflict")
        awaiting = (
            not held
            and execution.state == "reconciliation_required"
            and execution.reason == "action_uncertain"
        )
        # Containment only: a write read-back already settled, whose lapse left its
        # compute stop unconfirmed, keeps the execution from ending; an owner may
        # confirm that stop, and nothing else, while the execution runs. Any closed
        # write of this intent qualifies, a superseded one included.
        containment = not awaiting
        if containment:
            if request.kind != "compute_stopped":
                raise ExecutionError("conflict")
            write_attempt, path, node_id, reads = (
                self._unstopped(db, scope, execution, intent, held=held),
                None,
                None,
                [],
            )
        else:
            write_attempt, path, node_id, reads = self._awaiting(db, scope, execution, intent)
        now = database_now_ms(db)
        evidence, result, created = [], UNKNOWN, None
        if request.kind == "effect_present":
            cited = request.evidence
            if cited is None or cited not in recorded_evidence(db, scope, intent.id):
                raise ExecutionError("conflict")  # only evidence already recorded here
            evidence, result = [_evidence_ref(cited, "target_readback")], PRESENT
        elif request.evidence is not None:
            raise ExecutionError("invalid_request")
        if request.kind == "compute_stopped" and not stop_confirmed(db, scope, write_attempt.id):
            stop = ExecutionReconciliationObservation(
                **scope.model_dump(),
                execution_id=execution.id,
                attempt_id=write_attempt.id,
                source_id="resolution",
                observation_id=_resolution_key(request),
                digest=sha256(
                    canonical_value({"resolution": request.resolution_id, "user": actor.user_id})
                ).hexdigest(),
                code="compute_stopped",
                observed_at_ms=now,
            )
            db.add(stop)
            db.flush()
            self.budgets.confirm_stopped(db, write_attempt.id, stop.id)
        if request.kind in REREAD and not containment:
            created = self.readbacks.schedule(
                db,
                execution,
                intent,
                path=path,
                node_id=node_id,
                ordinal=(reads[-1].ordinal + 1)
                if reads
                else write_attempt.ordinal * READBACK_STRIDE + 1,
                reason="resolution",
                epoch=epoch,
            )
            if created is None or created.ordinal >= (write_attempt.ordinal + 1) * READBACK_STRIDE:
                raise ExecutionError("conflict")
        spec_version = RESOLUTION_VERSION
        content, payload = _payload(
            intent, result, "resolution-" + request.kind, spec_version, evidence, False
        )
        db.add(
            ExecutionActionObservation(
                **scope.model_dump(),
                execution_id=execution.id,
                intent_id=intent.id,
                # A re-read names the read-back it asked for; a final resolution the write.
                attempt_id=created.id if created is not None else write_attempt.id,
                observation_id=_resolution_key(request),
                payload_bytes=content,
                payload_sha256=sha256(content).hexdigest(),
                result=result,
                qualified=False,
                rule_id="resolution-" + request.kind,
                rule_version=spec_version,
                source="resolution",
                actor_user_id=actor.user_id,
            )
        )
        if request.kind == "effect_present":
            intent.effect_state = "observed"
        elif request.kind == "close_unknown":
            intent.effect_state = "unknown"
        if awaiting:
            # The execution resumes; progression decides what this resolution allows.
            execution.state = "running"
        db.flush()
        record_sdk_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "reconciliation_observed",
            payload,
            attempt_id=write_attempt.id,
        )
        record_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "action_resolved",
            attempt_id=write_attempt.id,
            detail=request.kind,
            origin="control",
        )
        mark_runnable(db, execution, now)
        return action_view(db, scope, intent)

    def _unstopped(self, db, scope, execution, intent, *, held=False):
        """A write of this intent whose compute stop is unconfirmed: one closed after
        an uncertain command, or (held after a restore) one the hold left uncertain."""
        closed = and_(
            ExecutionActionCommand.status == "uncertain",
            ExecutionAttempt.state.in_(("succeeded", "failed")),
        )
        writes = list(
            db.scalars(
                scoped(ExecutionAttempt, scope)
                .join(
                    ExecutionActionCommand,
                    ExecutionActionCommand.attempt_id == ExecutionAttempt.id,
                )
                .where(
                    ExecutionAttempt.execution_id == execution.id,
                    ExecutionActionCommand.intent_id == intent.id,
                    or_(closed, ExecutionAttempt.state == "uncertain") if held else closed,
                )
                .order_by(ExecutionAttempt.ordinal)
            )
        )
        for write in writes:
            if not stop_confirmed(db, scope, write.id):
                return record(db, ExecutionAttempt, scope, write.id, locked=True)
        raise ExecutionError("conflict")

    @staticmethod
    def _command(db, scope, write_attempt):
        command = db.scalar(
            scoped(ExecutionActionCommand, scope).where(
                ExecutionActionCommand.attempt_id == write_attempt.id
            )
        )
        if command is None:
            raise ExecutionError("conflict")
        return command

    def _awaiting(self, db, scope, execution, intent, *, uncertain=True):
        """The intent's latest write (uncertain, when an owner is awaited) and its
        read-backs, none of them in flight."""
        attempt_ids = select(ExecutionActionCommand.attempt_id).where(
            *(getattr(ExecutionActionCommand, k) == v for k, v in scope.model_dump().items()),
            ExecutionActionCommand.intent_id == intent.id,
        )
        latest = db.scalar(
            scoped(ExecutionAttempt, scope)
            .where(
                ExecutionAttempt.execution_id == execution.id, ExecutionAttempt.id.in_(attempt_ids)
            )
            .order_by(ExecutionAttempt.ordinal.desc())
            .limit(1)
        )
        if latest is None:
            raise ExecutionError("conflict")
        write_attempt = record(db, ExecutionAttempt, scope, latest.id, locked=True)
        dispatch = record(db, ExecutionStepInstance, scope, write_attempt.step_instance_id)
        later = db.scalar(
            scoped(ExecutionAttempt, scope).where(
                ExecutionAttempt.step_instance_id == dispatch.id,
                ExecutionAttempt.parent_attempt_id.is_(None),
                ExecutionAttempt.ordinal > write_attempt.ordinal,
            )
        )
        if (uncertain and write_attempt.state != "uncertain") or later is not None:
            raise ExecutionError("conflict")
        path = dispatch.occurrence_path[: -len(DISPATCH_SUFFIX)] + READBACK_SUFFIX
        reconcile = db.scalar(
            scoped(ExecutionStepInstance, scope).where(
                ExecutionStepInstance.execution_id == execution.id,
                ExecutionStepInstance.occurrence_path == path,
            )
        )
        reads = []
        if reconcile is not None:
            low = write_attempt.ordinal * READBACK_STRIDE
            reads = list(
                db.scalars(
                    scoped(ExecutionAttempt, scope)
                    .where(
                        ExecutionAttempt.step_instance_id == reconcile.id,
                        ExecutionAttempt.ordinal > low,
                        ExecutionAttempt.ordinal < low + READBACK_STRIDE,
                    )
                    .order_by(ExecutionAttempt.ordinal)
                    .with_for_update()
                )
            )
        if any(a.state not in ("succeeded", "failed", "cancelled") for a in reads):
            raise ExecutionError("conflict")  # a read-back is still in flight
        return write_attempt, path, dispatch.node_id, reads
