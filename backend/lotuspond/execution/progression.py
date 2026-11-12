"""Interpreter v1: deterministic row transitions over the pinned compiled program.

`next_actions` is a pure function of the program, one region occurrence's
committed rows and database time. It never reads a clock, randomness,
environment, network or provider. `advance_execution` runs under the execution
row lock taken by the orchestrator claim: it loads one region occurrence at a
time, applies at most `bound` transitions, appends gap-free orchestrator events
and never touches installation, capacity, pool, connection or reservation rows.
Capacity changes (wait quota, pending release and terminal state) run in
`apply_capacity`, a separate transaction in Task 5's lock order.

Persisted conventions shared with Tasks 9-11:
- A pure step, a composite and a region occurrence complete with one attempt
  whose input and result artifact hold the value; its failure is a `failed`
  attempt whose result artifact is `{"failure": code, "failed_at_ms": ms}`.
- An activity attempt (connector read, agent) starts `reserved` with its input
  artifact; Task 9 records success (result value) or the failure record above.
- A `cancelled` activity attempt whose result artifact is the failure record with
  `interrupted` was stopped by a pause (Task 11): its step gets a new attempt on
  resume, and it never counts toward `max_attempts`. A step still has at most 10
  root attempts in all; past that it fails `retries_exhausted`.
- Wait keys are `w:` + SHA-256 of the occurrence path.
"""

import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from hashlib import sha256

from lotuspond_runtime.protocol import Scope
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.package import (
    ActionDispatchStep,
    ActionIntentStep,
    AgentStep,
    ConnectorReadStep,
    DecisionStep,
    ExternalWaitStep,
    HumanTaskStep,
    IterationStep,
    ParallelStep,
    SubworkflowStep,
    TimerStep,
    TransformStep,
)
from lotuspond_sdk.executable.types import (
    ArrayExport,
    ExportField,
    HandleExport,
    ObjectExport,
    parse_export,
)
from sqlalchemy import String, any_, bindparam, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY

from .. import authority_gate
from ..fleet.authority import database_now_ms
from ..models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionEvent,
    ExecutionInbox,
    ExecutionLease,
    ExecutionReconciliationObservation,
    ExecutionRequest,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    ExecutionWait,
    Workspace,
)
from ..security import Principal
from .action_contracts import FAILURE_CODES as ACTION_FAILURES
from .action_contracts import INTERPRETER_V2
from .artifacts import ArtifactPolicy
from .authorization import authorize_execution, lock, record, scoped, share
from .contracts import canonical_value
from .deployments import gates
from .engine_contracts import INTERPRETER_V1, AdvanceOutcome
from .errors import ExecutionError
from .expressions import (
    MISSING,
    ExportScope,
    ExpressionError,
    ValueEnvironment,
    data_export,
    evaluate_mapping,
    mapping_export,
    plain,
    typed_export,
)
from .orchestrator import TERMINAL, clear_runnable, mark_runnable, past_deadline
from .program import (
    MAX_PATH,
    Program,
    ProgramError,
    child_path,
    load_program,
    region_body,
    resolve_occurrence,
)

logger = logging.getLogger("lotuspond.execution")

BOUND = 64
# Per-transaction caches of immutable decrypted values and artifact keys. They
# never outlive the transition or capacity transaction that filled them.
_SCRATCH: ContextVar = ContextVar("lotuspond_progression_scratch", default=None)
ACTIVE = frozenset({"reserved", "dispatched", "claimed"})
FAILURES = frozenset(
    {
        "unavailable_value",
        "type_mismatch",
        "integer_overflow",
        "division_by_zero",
        "inexact_division",
        "inexact_decimal",
        "decimal_overflow",
        "invalid_mapping",
        "value_too_large",
        "wait_expired",
        "cancelled",
        "retries_exhausted",
        "invalid_result",
        "deadline_exceeded",
        "occurrence_unresolved",
        "unsupported_step",
        "unsupported_policy",
        "unavailable",
        "rate_limited",
        "timeout_before_dispatch",
        "provider_failed",
        "uncertain",
        # E05 (interpreter v2).
        "binding_refused",
        "approval_denied",
        "review_expired",
        "approval_expired",
        "not_applied",
        "outcome_unknown",
    }
)


def _failure(code):
    return code if code in FAILURES else "invalid_result"


# The record of a `cancelled` activity attempt that a pause released before it
# started, or that stopped when its lease renewal was refused short of a
# cancellation (Task 11). It is never a step's outcome: on resume the step gets a
# new attempt, and an interrupted attempt never counts toward `max_attempts`.
INTERRUPTED = "interrupted"
# Root attempts of a step keep ordinals 1..10, interrupted ones included: an agent's
# children take the ordinals above (`activity_records.CHILD_ORDINAL_BASE`). That
# also bounds how often a pause can restart a step.
ROOT_ATTEMPTS = 10


# ---------------------------------------------------------------- pure state


@dataclass(frozen=True)
class AttemptRecord:
    id: str
    ordinal: int
    state: str
    value: object = MISSING
    failure: str | None = None
    failed_at_ms: int | None = None


@dataclass(frozen=True)
class WaitRecord:
    id: str
    state: str
    deadline_ms: int


@dataclass(frozen=True)
class SignalRecord:
    id: str
    signal_id: str
    digest: str
    available_at_ms: int
    expires_at_ms: int
    payload: object


@dataclass(frozen=True)
class TaskRecord:
    """E05 (v2): the human task behind a wait, and a recorded decision if any."""

    id: str
    kind: str
    state: str
    decision: str | None = None


@dataclass(frozen=True)
class ObservationRecord:
    """E05 (v2): one read-back observation or resolution of an action."""

    id: str
    attempt_id: str | None
    result: str
    qualified: bool
    source: str
    kind: str | None = None  # a resolution's kind


@dataclass(frozen=True)
class ActionRecord:
    """E05 (v2): what settles a dispatch step's uncertain writes (Task 8)."""

    intent_id: str
    effect: str
    target_key: bool
    observations: tuple = ()
    # Command attempts whose compute stop is confirmed, and each one's receipt row.
    stopped: frozenset = frozenset()
    receipts: dict = field(default_factory=dict)
    # When each finished read-back settled (its reservation's completion time).
    finished: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OccurrenceRecord:
    path: str
    attempts: tuple = ()
    wait: WaitRecord | None = None
    signals: tuple = ()
    task: TaskRecord | None = None

    @property
    def final(self):
        """The last attempt if it closed the occurrence; retries remain open."""
        return self.attempts[-1] if self.attempts else None


@dataclass(frozen=True)
class ProgressionState:
    execution_id: str
    region_path: str
    requested_state: str
    deadline_ms: int
    input: object
    item: object = MISSING
    index: int | None = None
    outer: dict = field(default_factory=dict)
    occurrences: dict = field(default_factory=dict)
    # E05 (v2): each dispatch step occurrence's `ActionRecord`, by its path.
    actions: dict = field(default_factory=dict)


# ---------------------------------------------------------------- pure actions


@dataclass(frozen=True)
class RecordPure:
    occurrence_path: str
    node_id: str
    value: object = None
    failure: str | None = None


@dataclass(frozen=True)
class CreateAttempt:
    occurrence_path: str
    node_id: str
    ordinal: int
    input: object


@dataclass(frozen=True)
class OpenWait:
    occurrence_path: str
    node_id: str
    deadline_ms: int


@dataclass(frozen=True)
class ConsumeSignal:
    occurrence_path: str
    wait_id: str
    inbox_id: str | None
    rejected: tuple = ()


@dataclass(frozen=True)
class CreateIntent:
    """E05 (v2): create this occurrence's action intent from evaluated values."""

    occurrence_path: str
    node_id: str
    target: object
    input: object


@dataclass(frozen=True)
class OpenTask:
    """E05 (v2): open a human task's wait and its task record."""

    occurrence_path: str
    node_id: str
    deadline_ms: int
    escalate_at_ms: int | None
    intent_id: str | None


@dataclass(frozen=True)
class ConsumeDecision:
    """E05 (v2): consume the one decision bound to a review task."""

    occurrence_path: str
    task_id: str


@dataclass(frozen=True)
class CreateDispatch:
    """E05 (v2): the command attempt of an approved (or unreviewed) intent."""

    occurrence_path: str  # `<dispatch step>#dispatch`
    node_id: str
    intent_id: str
    decision_id: str | None
    # A later ordinal resends after a qualified absence (Task 8), never otherwise.
    ordinal: int = 1


@dataclass(frozen=True)
class CreateReadback:
    """E05 (v2): read back an uncertain write before anything else (Task 8)."""

    occurrence_path: str  # `<dispatch step>#reconcile`
    node_id: str
    intent_id: str
    ordinal: int  # 100k + n: read-back n of command attempt k
    reason: str


@dataclass(frozen=True)
class RecordReadback:
    """E05 (v2): apply the write's rules to a finished read-back (Task 8)."""

    occurrence_path: str  # `<dispatch step>#reconcile`
    node_id: str
    attempt_id: str


@dataclass(frozen=True)
class SettleWrite:
    """E05 (v2): close an uncertain write as read-back or an owner settled it (Task 8)."""

    occurrence_path: str  # `<dispatch step>#dispatch`
    node_id: str
    attempt_id: str
    value: object = MISSING
    failure: str | None = None


@dataclass(frozen=True)
class RequireResolution:
    """E05 (v2): an uncertain write only an owner or admin can settle (Task 8)."""

    occurrence_path: str  # the dispatch step
    node_id: str


@dataclass(frozen=True)
class CancelUndispatched:
    prefix: str


@dataclass(frozen=True)
class Complete:
    value: object


@dataclass(frozen=True)
class Fail:
    code: str


Action = (
    RecordPure
    | CreateAttempt
    | OpenWait
    | ConsumeSignal
    | CancelUndispatched
    | Complete
    | Fail
    | CreateIntent
    | OpenTask
    | ConsumeDecision
    | CreateDispatch
    | CreateReadback
    | RecordReadback
    | SettleWrite
    | RequireResolution
)


@dataclass(frozen=True)
class Derivation:
    actions: tuple = ()
    regions: tuple = ()
    wake_at_ms: int | None = None


def wait_key(path):
    return "w:" + sha256(path.encode()).hexdigest()


def signal_digest(execution_id, signal_id, wait_id, payload):
    """Recomputes Task 6's acceptance digest from the decrypted payload."""
    return sha256(
        canonical_value(
            {
                "operation": "signal",
                "execution_id": execution_id,
                "signal_id": signal_id,
                "wait_id": wait_id,
                "payload": payload,
            }
        )
    ).hexdigest()


# ---------------------------------------------------------------- pure typing


def _body_export(body, definitions):
    return parse_export(body.result_schema, list(definitions))


def output_export(program, package, step, record=None):
    """The export type of a completed step occurrence's value."""
    definitions = package.schemas
    if isinstance(step, (TransformStep, AgentStep)):
        return data_export(step.output_schema, definitions)
    if isinstance(step, ConnectorReadStep):
        document = program.document(package, "connectors", step.connector_ref)
        operation = next(op for op in document.definition.operations if op.id == step.operation)
        return data_export(operation.output_schema, document.schemas)
    if isinstance(step, HumanTaskStep):
        if step.mode == "review":
            return HandleExport(kind="handle", handle_kind="human_decision")
        return data_export(step.form_schema, definitions)
    if isinstance(step, ExternalWaitStep):
        return data_export(step.signal_schema, definitions)
    if isinstance(step, TimerStep):
        return data_export({"kind": "null"})
    if isinstance(step, DecisionStep):
        branch = record if record in ("then", "else") else "then"
        return _body_export(step.then_body if branch == "then" else step.else_body, definitions)
    if isinstance(step, ParallelStep):
        return ObjectExport(
            kind="object",
            fields=[
                ExportField(name=b.id, schema=_body_export(b.body, definitions), required=True)
                for b in step.branches
            ],
        )
    if isinstance(step, IterationStep):
        return ArrayExport(
            kind="array", items=_body_export(step.body, definitions), max_items=step.max_items
        )
    if isinstance(step, SubworkflowStep):
        _, child = program.child(package, step.workflow_ref)
        return data_export(child.output_schema, child.schemas)
    if isinstance(step, ActionIntentStep):
        return HandleExport(kind="handle", handle_kind="action_intent")
    if isinstance(step, ActionDispatchStep):
        return HandleExport(kind="handle", handle_kind="action_receipt")
    raise ExpressionError("type_mismatch")


# ---------------------------------------------------------------- pure derivation


def _region_kind(program, region_path):
    """(composite step ref, selector) of a non-root region occurrence."""
    if region_path == "":
        return None, None
    ref = resolve_occurrence(program, region_path)
    return ref, ref.selector


def _decision_branch(record):
    if record is None or record.final is None or record.final.state != "succeeded":
        return None
    value = record.final.value
    return value.get("branch") if type(value) is dict else None


class _Region:
    """Pure evaluation of one region occurrence over its committed records."""

    def __init__(self, program, state, now_ms):
        self.program, self.state, self.now_ms = program, state, now_ms
        self.digest, self.package, self.body = region_body(program, state.region_path)
        self.values = dict(state.outer)
        self.types = {}

    def path(self, segment):
        try:
            return child_path(self.state.region_path, segment)
        except ProgramError:
            # A path beyond the stored bound fails this region deterministically.
            raise ExpressionError("occurrence_unresolved") from None

    def record(self, segment):
        return self.state.occurrences.get(self.path(segment))

    def env(self):
        return ValueEnvironment(
            input=self.state.input, steps=self.values, item=self.state.item, index=self.state.index
        )

    def evaluate(self, mapping):
        return evaluate_mapping(mapping, self.env(), self.package.schemas)

    def done(self, step, value):
        branch = None
        if isinstance(step, DecisionStep):
            branch = _decision_branch(self.record(step.id + "#select"))
        export = output_export(self.program, self.package, step, branch)
        self.values[step.id] = typed_export(plain(value), export)

    # Each step returns ("done", value) | ("failed", code) | ("pending", Derivation).
    def step(self, step):
        path = self.path(step.id)
        record = self.state.occurrences.get(path)
        final = record.final if record else None
        if final is not None and final.state == "succeeded" and not _is_activity(step):
            return "done", final.value
        if final is not None and final.state in ("failed", "cancelled") and not _is_activity(step):
            return "failed", final.failure or "cancelled"
        if isinstance(step, TransformStep):
            try:
                value = self.evaluate(step.mapping)
                export = data_export(step.output_schema, self.package.schemas)
                value = plain(typed_export(plain(value), export))
                return "pending", Derivation(actions=(RecordPure(path, step.id, value=value),))
            except ExpressionError as error:
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure=error.code),)
                )
        if _is_activity(step):
            return self.activity(step, path, record)
        if isinstance(step, (TimerStep, ExternalWaitStep, HumanTaskStep)):
            return self.wait(step, path, record)
        if isinstance(step, DecisionStep):
            return self.decision(step, path)
        if isinstance(step, ParallelStep):
            return self.parallel(step, path)
        if isinstance(step, IterationStep):
            return self.iteration(step, path)
        if isinstance(step, SubworkflowStep):
            return self.subworkflow(step, path)
        # Effect nodes are refused at deployment admission; never guess here.
        return "pending", Derivation(
            actions=(RecordPure(path, step.id, failure="unsupported_step"),)
        )

    def activity(self, step, path, record):
        attempts = record.attempts if record else ()
        if not attempts:
            try:
                value = self.activity_input(step)
            except ExpressionError as error:
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure=error.code),)
                )
            return "pending", Derivation(actions=(CreateAttempt(path, step.id, 1, value),))
        last = attempts[-1]
        if last.state == "succeeded":
            return "done", last.value
        if last.state in ACTIVE or last.state == "uncertain":
            return "pending", Derivation()
        if last.state == "cancelled" and last.failure == INTERRUPTED:
            # Interrupted by a pause: a new attempt, never counted as a retry.
            if last.ordinal >= ROOT_ATTEMPTS:
                return "failed", "retries_exhausted"
            try:
                value = self.activity_input(step)
            except ExpressionError as error:
                return "failed", error.code
            return "pending", Derivation(
                actions=(CreateAttempt(path, step.id, last.ordinal + 1, value),)
            )
        # Ordinals run 1, 2, ... with no gaps: the last one counts every attempt.
        spent = last.ordinal - sum(
            1 for a in attempts if a.state == "cancelled" and a.failure == INTERRUPTED
        )
        code = "cancelled" if last.state == "cancelled" else (last.failure or "provider_failed")
        retry = step.retry
        if last.state == "failed" and code in retry.retry_codes and spent < retry.max_attempts:
            if last.ordinal >= ROOT_ATTEMPTS:
                return "failed", "retries_exhausted"  # interruptions took the rest
            due = (last.failed_at_ms or 0) + retry.backoff_seconds * 1000
            if due > self.now_ms:
                return "pending", Derivation(wake_at_ms=due)
            try:
                value = self.activity_input(step)
            except ExpressionError as error:
                return "failed", error.code
            return "pending", Derivation(
                actions=(CreateAttempt(path, step.id, last.ordinal + 1, value),)
            )
        if last.state == "failed" and code in retry.retry_codes:
            return "failed", "retries_exhausted"
        return "failed", code

    def activity_input(self, step):
        if isinstance(step, ConnectorReadStep):
            return plain({"target": self.evaluate(step.target), "input": self.evaluate(step.input)})
        return plain(self.evaluate(step.input))

    def wait(self, step, path, record):
        if record is None or record.wait is None:
            if getattr(step, "escalation_policy_ref", None) is not None:
                # Escalation is refused at admission; it is never silently dropped.
                return "failed", "unsupported_policy"
            if isinstance(step, TimerStep):
                seconds = step.delay_seconds
            elif isinstance(step, ExternalWaitStep):
                try:
                    self.evaluate(step.correlation)
                except ExpressionError as error:
                    return "failed", error.code
                seconds = step.expiry_seconds
            else:
                if step.mode != "form":
                    return "failed", "unsupported_step"
                seconds = step.timeout_seconds
            deadline = min(self.now_ms + seconds * 1000, self.state.deadline_ms)
            return "pending", Derivation(actions=(OpenWait(path, step.id, deadline),))
        wait = record.wait
        if wait.state == "satisfied":
            if isinstance(step, TimerStep):
                return "pending", Derivation(actions=(RecordPure(path, step.id, value=None),))
            # Consumption records the payload attempt in the same transition.
            return "failed", "invalid_result"
        if wait.state == "cancelled":
            return "pending", Derivation(actions=(RecordPure(path, step.id, failure="cancelled"),))
        expired = RecordPure(path, step.id, failure="wait_expired")
        if isinstance(step, TimerStep) or not record.signals:
            return "pending", Derivation(actions=(expired,) if wait.state == "expired" else ())
        schema = step.signal_schema if isinstance(step, ExternalWaitStep) else step.form_schema
        export = data_export(schema, self.package.schemas)
        accepted, rejected = None, []
        for signal in sorted(record.signals, key=lambda s: (s.available_at_ms, s.id)):
            # Timeliness is judged by committed acceptance time, never by when a
            # worker happens to consume: a signal accepted in time still counts.
            valid = (
                accepted is None
                and signal.available_at_ms < min(signal.expires_at_ms, wait.deadline_ms)
                and signal.payload is not MISSING
                and signal.digest
                == signal_digest(
                    self.state.execution_id, signal.signal_id, wait.id, plain(signal.payload)
                )
            )
            if valid:
                try:
                    typed_export(plain(signal.payload), export)
                except ExpressionError:
                    valid = False
            if valid:
                accepted = signal.id
            else:
                rejected.append(signal.id)
        consume = ConsumeSignal(path, wait.id, accepted, tuple(sorted(rejected)))
        if accepted is None and wait.state == "expired":
            return "pending", Derivation(actions=((consume,) if rejected else ()) + (expired,))
        return "pending", Derivation(actions=(consume,))

    def decision(self, step, path):
        select = self.record(step.id + "#select")
        if select is None:
            try:
                condition = self.evaluate(step.condition)
            except ExpressionError as error:
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure=error.code),)
                )
            if type(condition) is not bool:
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure="type_mismatch"),)
                )
            branch = "then" if condition else "else"
            return "pending", Derivation(
                actions=(RecordPure(path + "#select", step.id, value={"branch": branch}),)
            )
        branch = _decision_branch(select)
        if branch is None:
            return "failed", "invalid_result"
        return "pending", Derivation(regions=(path + ":" + branch,))

    def parallel(self, step, path):
        results, failed, open_regions = {}, None, []
        for branch in step.branches:
            record = self.record(step.id + ":" + branch.id)
            final = record.final if record else None
            if final is not None and final.state == "succeeded":
                results[branch.id] = final.value
            elif final is not None:
                failed = failed or final.failure or "cancelled"
            else:
                open_regions.append(path + ":" + branch.id)
        if failed is not None:
            # The first failed branch stops undispatched siblings; kept results remain.
            return "pending", Derivation(
                actions=(CancelUndispatched(path + ":"), RecordPure(path, step.id, failure=failed))
            )
        if not open_regions:
            value = {b.id: results[b.id] for b in step.branches}
            return "pending", Derivation(actions=(RecordPure(path, step.id, value=value),))
        return "pending", Derivation(regions=tuple(open_regions))

    def iteration(self, step, path):
        items = self.record(step.id + "#items")
        if items is None:
            try:
                value = self.evaluate(step.input)
            except ExpressionError as error:
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure=error.code),)
                )
            if type(value) is not list or len(value) > step.max_items:
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure="type_mismatch"),)
                )
            # Pin input order and content identity before any item starts.
            return "pending", Derivation(
                actions=(RecordPure(path + "#items", step.id, value=plain(value)),)
            )
        if items.final is None or items.final.state != "succeeded":
            return "failed", "invalid_result"
        count = len(items.final.value)
        results, failed, open_regions = [], None, []
        for index in range(count):
            record = self.record(f"{step.id}:{index}")
            final = record.final if record else None
            if final is not None and final.state == "succeeded":
                results.append(final.value)
            elif final is not None:
                failed = failed or final.failure or "cancelled"
            else:
                open_regions.append(f"{path}:{index}")
        if failed is not None:
            return "pending", Derivation(
                actions=(CancelUndispatched(path + ":"), RecordPure(path, step.id, failure=failed))
            )
        if not open_regions:
            return "pending", Derivation(actions=(RecordPure(path, step.id, value=results),))
        # Declared concurrency: only the first incomplete items, in input order.
        return "pending", Derivation(regions=tuple(open_regions[: step.concurrency]))

    def subworkflow(self, step, path):
        pinned = self.record(step.id + "#input")
        if pinned is None:
            _, child = self.program.child(self.package, step.workflow_ref)
            try:
                value = self.evaluate(step.input)
                value = plain(
                    typed_export(plain(value), data_export(child.input_schema, child.schemas))
                )
            except ExpressionError as error:
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure=error.code),)
                )
            return "pending", Derivation(
                actions=(RecordPure(path + "#input", step.id, value=value),)
            )
        return "pending", Derivation(regions=(path + ":call",))

    def finish(self):
        """Region result, or the completion/failure action addressed to its owner."""
        try:
            value = self.evaluate(self.body.result)
            value = plain(typed_export(plain(value), _body_export(self.body, self.package.schemas)))
            if self.state.region_path == "":
                value = plain(
                    typed_export(
                        value, data_export(self.package.output_schema, self.package.schemas)
                    )
                )
        except ExpressionError as error:
            return self.fail(error.code)
        return self.complete(value)

    def owner(self):
        ref, selector = _region_kind(self.program, self.state.region_path)
        if ref is None:
            return None, None
        if isinstance(ref.step, (DecisionStep, SubworkflowStep)):
            # A single-region composite completes with its region's result.
            parent = self.state.region_path.rpartition(":")[0]
            return parent, ref.step_id
        return self.state.region_path, ref.step_id

    def complete(self, value):
        target, node = self.owner()
        if target is None:
            return Derivation(actions=(Complete(value),))
        ref, _ = _region_kind(self.program, self.state.region_path)
        if isinstance(ref.step, SubworkflowStep):
            _, child = self.program.child(ref.package, ref.step.workflow_ref)
            try:
                value = plain(typed_export(value, data_export(child.output_schema, child.schemas)))
            except ExpressionError as error:
                return self.fail(error.code)
        return Derivation(actions=(RecordPure(target, node, value=value),))

    def fail(self, code):
        target, node = self.owner()
        if target is None:
            return Derivation(actions=(CancelUndispatched(""), Fail(code)))
        return Derivation(actions=(RecordPure(target, node, failure=code),))

    def derive(self):
        state = self.state
        if state.region_path == "" and self.now_ms >= state.deadline_ms:
            return Derivation(actions=(CancelUndispatched(""), Fail("deadline_exceeded")))
        if state.region_path == "" and state.requested_state == "cancelled":
            # Undispatched work ends now; the terminal state waits for containment.
            return Derivation(actions=(CancelUndispatched(""), Fail("cancelled")))
        if state.requested_state != "running":
            # Pause stops new dispatch and waits; deadlines keep running.
            return Derivation()
        wake = None
        for step in self.body.steps:
            status, detail = self.step(step)
            if status == "done":
                try:
                    self.done(step, detail)
                except ExpressionError as error:
                    return self.fail(error.code)
                continue
            if status == "failed":
                return self.fail(_failure(detail))
            if detail.actions or detail.regions:
                return detail
            wake = detail.wake_at_ms
            return Derivation(wake_at_ms=wake)
        return self.finish()


class _RegionV2(_Region):
    """Interpreter v2: v1's semantics, plus the E05 steps implemented so far."""

    def step(self, step):
        if isinstance(step, ActionDispatchStep):
            return self.dispatch(step)
        if isinstance(step, HumanTaskStep):
            path = self.path(step.id)
            record = self.state.occurrences.get(path)
            final = record.final if record else None
            if final is not None and final.state == "succeeded":
                return "done", final.value
            if final is not None:
                return "failed", final.failure or "cancelled"
            return self.task(step, path, record)
        if isinstance(step, ActionIntentStep):
            path = self.path(step.id)
            record = self.state.occurrences.get(path)
            final = record.final if record else None
            if final is not None and final.state == "succeeded":
                return "done", final.value
            if final is not None:
                return "failed", final.failure or "cancelled"
            return self.intent(step, path)
        return super().step(step)

    def dispatch(self, step):
        """Create the command attempt; its receipt or failure then closes the step."""
        path = self.path(step.id)
        record = self.state.occurrences.get(path)
        final = record.final if record else None
        if final is not None and final.state == "succeeded":
            return "done", final.value
        if final is not None:
            return "failed", final.failure or "cancelled"
        attempts_path = self.path(step.id + "#dispatch")
        attempts = self.state.occurrences.get(attempts_path)
        attempts = attempts.attempts if attempts else ()
        if not attempts:
            try:
                intent = self.evaluate(step.intent)
                decision = self.evaluate(step.decision) if step.decision is not None else None
            except ExpressionError as error:
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure=error.code),)
                )
            if (
                type(intent) is not dict
                or intent.get("handle_kind") != "action_intent"
                or (
                    decision is not None
                    and (
                        type(decision) is not dict
                        or decision.get("handle_kind") != "human_decision"
                    )
                )
            ):
                return "pending", Derivation(
                    actions=(RecordPure(path, step.id, failure="type_mismatch"),)
                )
            return "pending", Derivation(
                actions=(
                    CreateDispatch(
                        attempts_path,
                        step.id,
                        intent["id"],
                        None if decision is None else decision["id"],
                    ),
                )
            )
        last = attempts[-1]
        if last.state == "succeeded":
            return "pending", Derivation(actions=(RecordPure(path, step.id, value=last.value),))
        if last.state == "failed" and last.failure == "not_applied":
            resend = self.resend(step, path, last)
            if resend is not None:
                return "pending", resend
        if last.state in ("failed", "cancelled"):
            return "pending", Derivation(
                actions=(RecordPure(path, step.id, failure=last.failure or "cancelled"),)
            )
        if last.state == "uncertain":
            return self.settle(step, path, last)
        # Reserved, dispatched or claimed: the runtime decides.
        return "pending", Derivation()

    def _reads(self, step, last):
        """Read-back attempts of write `last`: ordinals 100k + 1 .. 100k + 99."""
        from .budgets import READBACK_STRIDE

        record = self.state.occurrences.get(self.path(step.id + "#reconcile"))
        low = last.ordinal * READBACK_STRIDE
        return [
            a
            for a in (record.attempts if record else ())
            if low < a.ordinal < low + READBACK_STRIDE
        ]

    @staticmethod
    def _seen(action, attempt):
        return next(
            (
                o
                for o in action.observations
                if o.source == "readback" and o.attempt_id == attempt.id
            ),
            None,
        )

    def resend(self, step, path, last):
        """After a qualified absence closed write `last` (`not_applied`): one more command
        under the same intent, decision and key, only for a `target_key` operation with
        attempts left in the dispatch step's retry policy. None: no resend."""
        action = self.state.actions.get(path)
        if action is None or not action.target_key or last.ordinal >= step.retry.max_attempts:
            return None
        reads = self._reads(step, last)
        seen = self._seen(action, reads[-1]) if reads else None
        if seen is None or seen.result != "effect_absent" or not seen.qualified:
            return None
        try:
            decision = self.evaluate(step.decision) if step.decision is not None else None
        except ExpressionError as error:
            return Derivation(actions=(RecordPure(path, step.id, failure=error.code),))
        return Derivation(
            actions=(
                CreateDispatch(
                    self.path(step.id + "#dispatch"),
                    step.id,
                    action.intent_id,
                    None if decision is None else decision["id"],
                    last.ordinal + 1,
                ),
            )
        )

    def settle(self, step, path, last):
        """An uncertain write: read back, then follow what was observed (Task 8).

        Never a blind replacement. Effect present closes the write as succeeded; a
        qualified absence closes it `not_applied` (then `resend` decides); an
        unqualified absence, or `MAX_READBACKS` unknown results since the last
        re-read an owner asked for, waits for an owner.
        """
        from .action_contracts import MAX_READBACKS
        from .action_reconciliation import FINAL, REREAD
        from .budgets import READBACK_STRIDE

        action = self.state.actions.get(path)
        if action is None:
            return "pending", Derivation()
        writes = self.path(step.id + "#dispatch")
        reconcile_path = self.path(step.id + "#reconcile")
        low = last.ordinal * READBACK_STRIDE
        reads = self._reads(step, last)

        def present(observation):
            value = {
                "handle_kind": "action_receipt",
                "id": action.receipts.get(last.id, observation.id),
            }
            return "pending", Derivation(actions=(SettleWrite(writes, step.id, last.id, value),))

        resolutions = [o for o in action.observations if o.source == "resolution"]
        final = next((o for o in resolutions if o.kind in FINAL and o.attempt_id == last.id), None)
        if final is not None:
            if final.kind == "effect_present":
                return present(final)
            closed = SettleWrite(writes, step.id, last.id, failure="outcome_unknown")
            return "pending", Derivation(actions=(closed,))
        if not reads:
            return "pending", Derivation(
                actions=(
                    CreateReadback(reconcile_path, step.id, action.intent_id, low + 1, "uncertain"),
                )
            )
        latest = reads[-1]
        if latest.state in ACTIVE or latest.state == "uncertain":
            return "pending", Derivation()  # a read-back in flight, or fenced by E04
        seen = self._seen(action, latest)
        if seen is None:
            return "pending", Derivation(
                actions=(RecordReadback(reconcile_path, step.id, latest.id),)
            )
        if seen.result == "effect_present":
            return present(seen)
        if seen.result == "effect_absent" and seen.qualified:
            closed = SettleWrite(writes, step.id, last.id, failure="not_applied")
            return "pending", Derivation(actions=(closed,))
        if seen.result == "effect_absent":
            return "pending", Derivation(actions=(RequireResolution(path, step.id),))
        # Unknown: bounded read-backs under the step's back-off, then an owner.
        requested = {o.attempt_id for o in resolutions if o.kind in REREAD}
        start = max((i for i, a in enumerate(reads) if a.id in requested), default=0)
        if len(reads) - start >= MAX_READBACKS or latest.ordinal + 1 >= low + READBACK_STRIDE:
            return "pending", Derivation(actions=(RequireResolution(path, step.id),))
        settled = action.finished.get(latest.id) or latest.failed_at_ms or 0
        due = settled + step.retry.backoff_seconds * 1000
        if due > self.now_ms:
            return "pending", Derivation(wake_at_ms=due)
        return "pending", Derivation(
            actions=(
                CreateReadback(
                    reconcile_path, step.id, action.intent_id, latest.ordinal + 1, "unknown"
                ),
            )
        )

    def task(self, step, path, record):
        """A review or (v2) form task: open it, then consume its decision or form."""
        if record is None or record.wait is None:
            timeout_ms = step.timeout_seconds * 1000
            deadline = min(self.now_ms + timeout_ms, self.state.deadline_ms)
            escalate = None
            if step.escalation_policy_ref is not None:
                escalate = self.now_ms + timeout_ms // 2
                if escalate >= deadline:
                    escalate = None  # capped below half the timeout: never escalates
            intent_id = None
            if step.mode == "review":
                try:
                    handle = self.evaluate(step.intent)
                except ExpressionError as error:
                    return "pending", Derivation(
                        actions=(RecordPure(path, step.id, failure=error.code),)
                    )
                if (
                    type(handle) is not dict
                    or handle.get("handle_kind") != "action_intent"
                    or type(handle.get("id")) is not str
                ):
                    return "pending", Derivation(
                        actions=(RecordPure(path, step.id, failure="type_mismatch"),)
                    )
                intent_id = handle["id"]
            return "pending", Derivation(
                actions=(OpenTask(path, step.id, deadline, escalate, intent_id),)
            )
        if step.mode == "form":
            return self.wait(step, path, record)  # E04's typed, encrypted form path
        wait, task = record.wait, record.task
        if wait.state == "cancelled":
            return "pending", Derivation(actions=(RecordPure(path, step.id, failure="cancelled"),))
        if task is not None and task.state == "decided":
            return "pending", Derivation(actions=(ConsumeDecision(path, task.id),))
        if wait.state == "expired" or (task is not None and task.state == "expired"):
            return "pending", Derivation(
                actions=(RecordPure(path, step.id, failure="review_expired"),)
            )
        return "pending", Derivation()

    def intent(self, step, path):
        document = self.program.document(self.package, "connectors", step.connector_ref)
        operation = next(op for op in document.definition.operations if op.id == step.operation)
        try:
            target = plain(
                typed_export(
                    plain(self.evaluate(step.target)),
                    data_export(operation.target_schema, document.schemas),
                )
            )
            value = plain(
                typed_export(
                    plain(self.evaluate(step.input)),
                    data_export(operation.input_schema, document.schemas),
                )
            )
        except ExpressionError as error:
            return "pending", Derivation(actions=(RecordPure(path, step.id, failure=error.code),))
        return "pending", Derivation(actions=(CreateIntent(path, step.id, target, value),))


def _is_activity(step):
    return isinstance(step, (ConnectorReadStep, AgentStep))


def derive(program: Program, state: ProgressionState, now_ms: int, region=None) -> Derivation:
    region = region or _Region
    try:
        return region(program, state, now_ms).derive()
    except ProgramError:
        raise
    except ExpressionError as error:
        return region(program, state, now_ms).fail(error.code)


def next_actions(plan: Program, state: ProgressionState, now_ms: int) -> tuple:
    """Pure: the actions one region occurrence's committed rows admit at `now_ms`."""
    if type(now_ms) is not int or now_ms < 0:
        raise ExpressionError("invalid_mapping")
    return derive(plan, state, now_ms).actions


# ---------------------------------------------------------------- golden records


def derivation_json(derivation) -> bytes:
    """Canonical bytes of a whole derivation: actions, descended regions and wake-up."""
    return canonical_value(
        {
            "actions": json.loads(actions_json(derivation.actions)),
            "regions": list(derivation.regions),
            "wake_at_ms": derivation.wake_at_ms,
        }
    )


def actions_json(actions) -> bytes:
    """Canonical bytes of derived actions, for golden progression fixtures."""
    items = []
    for action in actions:
        fields = {"type": type(action).__name__}
        for name, value in action.__dict__.items():
            fields[name] = list(value) if type(value) is tuple else plain(value)
        items.append(fields)
    return canonical_value(items)


def state_from_json(program: Program, raw: dict) -> ProgressionState:
    """Rebuild a committed-row state; values are typed exactly as loading types them."""
    region = raw["region_path"]
    digest, package, _ = region_body(program, region)
    owner = _enclosing_input_owner(program, region)
    if owner is None:
        schema, definitions = program.root.input_schema, program.root.schemas
    else:
        ref = resolve_occurrence(program, owner)
        _, child = program.child(ref.package, ref.step.workflow_ref)
        schema, definitions = child.input_schema, child.schemas
    item = MISSING
    if "item" in raw:
        item = raw["item"]
        item_owner = _enclosing_item_owner(program, region)
        if item_owner is not None:
            try:
                item = typed_export(item, item_export(program, item_owner))
            except (ExpressionError, StopIteration, KeyError):
                raise ProgramError("integrity") from None

    def attempt(item):
        return AttemptRecord(
            item["id"],
            item["ordinal"],
            item["state"],
            item["value"] if "value" in item else MISSING,
            item.get("failure"),
            item.get("failed_at_ms"),
        )

    occurrences = {}
    for entry in raw["occurrences"]:
        wait = entry.get("wait")
        occurrences[entry["path"]] = OccurrenceRecord(
            entry["path"],
            tuple(attempt(a) for a in entry.get("attempts", [])),
            None if wait is None else WaitRecord(wait["id"], wait["state"], wait["deadline_ms"]),
            tuple(
                SignalRecord(
                    s["id"],
                    s["signal_id"],
                    s["digest"],
                    s["available_at_ms"],
                    s["expires_at_ms"],
                    s["payload"] if "payload" in s else MISSING,
                )
                for s in entry.get("signals", [])
            ),
            None if entry.get("task") is None else TaskRecord(**entry["task"]),
        )
    return ProgressionState(
        execution_id=raw["execution_id"],
        region_path=region,
        requested_state=raw["requested_state"],
        deadline_ms=raw["deadline_ms"],
        input=typed_export(raw["input"], data_export(schema, definitions)),
        item=item,
        index=raw.get("index"),
        outer=_typed_outer(program, package, raw.get("outer", {})),
        occurrences=occurrences,
    )


def _typed_outer(program, package, outer):
    """Type recorded outer values by their declaring step, failing closed."""
    steps = {step.id: step for step in _package_steps(package.body)}
    typed = {}
    for name, value in outer.items():
        try:
            typed[name] = typed_export(value, output_export(program, package, steps[name]))
        except (ExpressionError, StopIteration, KeyError):
            raise ProgramError("integrity") from None
    return typed


# ---------------------------------------------------------------- database layer


def _array(values):
    """One array parameter instead of one bind parameter per element."""
    return bindparam(None, sorted(values), type_=ARRAY(String), unique=True)


def _scope(execution):
    return Scope(
        workspace_id=execution.workspace_id,
        project_id=execution.project_id,
        environment_id=execution.environment_id,
    )


def _unclaimed():
    """No runtime ever claimed the attempt: no lease exists, so nothing of it started."""
    return ~(
        select(ExecutionLease.id)
        .where(
            ExecutionLease.workspace_id == ExecutionAttempt.workspace_id,
            ExecutionLease.project_id == ExecutionAttempt.project_id,
            ExecutionLease.environment_id == ExecutionAttempt.environment_id,
            ExecutionLease.execution_id == ExecutionAttempt.execution_id,
            ExecutionLease.attempt_id == ExecutionAttempt.id,
        )
        .exists()
    )


def _progressing(execution):
    """Whether new work may be derived: running is requested, and no fence holds it.

    Nor does the restore hold (`recovery.hold_restored`), which may leave a held
    execution `pending`.
    """
    return (
        execution.requested_state == "running"
        and execution.state != "reconciliation_required"
        and execution.recovery_epoch is not None
    )


def _scoped(model, execution):
    return select(model).where(
        model.workspace_id == execution.workspace_id,
        model.project_id == execution.project_id,
        model.environment_id == execution.environment_id,
        model.execution_id == execution.id,
    )


def _refs(mapping):
    """Step IDs a mapping references (bounded by the SDK's 64-node limit)."""
    found = set()

    def visit(node):
        kind = getattr(node, "kind", None)
        if kind == "ref" and node.source.kind == "step":
            found.add(node.source.id)
        elif kind == "object":
            for item in node.fields:
                visit(item.value)
        elif kind == "array":
            for item in node.items:
                visit(item)
        elif kind == "call":
            for item in node.args:
                visit(item)

    visit(mapping)
    return found


def _body_refs(body):
    found = set(_refs(body.result))
    for step in body.steps:
        for name in ("mapping", "condition", "input", "target", "correlation"):
            mapping = getattr(step, name, None)
            if mapping is not None and hasattr(mapping, "kind"):
                found |= _refs(mapping)
    return found


_WAIT_KINDS = frozenset({"timer", "external_wait", "human_task"})
# Codes with which `retain_wait` refuses for now. `conflict` covers a wait that closed,
# passed its deadline or shares the execution with running compute.
_RETENTION_REFUSALS = frozenset(
    {
        "capacity_unavailable",
        "forbidden",
        "conflict",
        "entitlement_unavailable",
        "authority_unavailable",
        "recovery_unavailable",
        "deployment_unavailable",
        "assurance_unavailable",
        "trust_unavailable",
        "source_unavailable",
        "dependency_unavailable",
    }
)


def _touches_capacity(program, action):
    """Whether an applied pure action can change wait quota or stoppable work."""
    if isinstance(action, (OpenWait, ConsumeSignal, CancelUndispatched)):
        return True
    if isinstance(action, RecordPure):
        try:
            return resolve_occurrence(program, action.occurrence_path).kind in _WAIT_KINDS
        except ProgramError:
            return True
    return False


def _enclosing_input_owner(program, region_path):
    """Path of the innermost subworkflow call whose package input a region sees."""
    segments = region_path.split("/") if region_path else []
    for depth in range(len(segments), 0, -1):
        path = "/".join(segments[:depth])
        ref = resolve_occurrence(program, path)
        if isinstance(ref.step, SubworkflowStep) and ref.selector == "call":
            return path.rpartition(":")[0]
    return None


def item_export(program, owner_path):
    """Export of one iteration item, inferred over the package's declared types."""
    ref = resolve_occurrence(program, owner_path)
    package = ref.package
    steps = {}
    for step in _package_steps(package.body):
        try:
            steps[step.id] = output_export(program, package, step)
        except (ExpressionError, StopIteration, KeyError):
            continue
    outer = _enclosing_item_owner(program, owner_path.rpartition("/")[0])
    scope = ExportScope(
        input=data_export(package.input_schema, package.schemas),
        steps=steps,
        item=None if outer is None else item_export(program, outer),
    )
    export = mapping_export(ref.step.input, scope, package.schemas)
    if export.kind == "array":
        return export.items
    if export.kind == "data" and export.schema.kind == "array":
        return data_export(export.schema.items)
    raise ExpressionError("type_mismatch")


def _enclosing_item_owner(program, region_path):
    """Path of the innermost iteration whose item scope covers a region, if any."""
    segments = region_path.split("/") if region_path else []
    for depth in range(len(segments), 0, -1):
        path = "/".join(segments[:depth])
        ref = resolve_occurrence(program, path)
        if isinstance(ref.step, SubworkflowStep) and ref.selector == "call":
            return None
        if isinstance(ref.step, IterationStep) and ref.selector not in (None, "#items"):
            return path.rpartition(":")[0]
    return None


def _package_steps(body):
    for step in body.steps:
        yield step
        if isinstance(step, DecisionStep):
            yield from _package_steps(step.then_body)
            yield from _package_steps(step.else_body)
        elif isinstance(step, IterationStep):
            yield from _package_steps(step.body)
        elif isinstance(step, ParallelStep):
            for branch in step.branches:
                yield from _package_steps(branch.body)


def record_event(
    db,
    artifacts,
    policy,
    installation_id,
    execution,
    epoch,
    code,
    *,
    path=None,
    attempt_id=None,
    detail=None,
    keys=None,
    origin="orchestrator",
):
    """Gap-free control event under the held execution row lock: codes and references only.

    The orchestrator and the activity services (Task 9) share this one sequence of
    control-plane events; `origin` records which of them caused the event.
    """
    sequence = execution.next_event_sequence
    content = {
        "format": "lotuspond.execution-event.v1",
        "execution_id": execution.id,
        "sequence": sequence,
        "code": code,
        "occurrence_path": path,
        "attempt_id": attempt_id,
        "detail": detail,
        "origin": origin,
    }
    handle = artifacts._put_internal(
        db, _scope(execution), canonical_value(content), policy, keys=keys
    )
    db.add(
        ExecutionEvent(
            **_scope(execution).model_dump(),
            execution_id=execution.id,
            attempt_id=attempt_id,
            sequence=sequence,
            family="execution_control",
            format_version=1,
            installation_id=installation_id,
            recovery_epoch=epoch,
            source_id="orchestrator",
            source_generation=1,
            source_sequence=sequence,
            event_id=f"orchestrator:{sequence}",
            event_artifact_id=handle.id,
            event_artifact_version=handle.version,
            digest=handle.sha256,
            code=code,
            occurred_at_ms=database_now_ms(db),
            application_state="applied",
            gap_slot=None,
        )
    )
    execution.next_event_sequence = sequence + 1
    db.flush()


class InterpreterV1:
    """Progression for `lotuspond.execution-interpreter.v1`; explicitly constructed."""

    interpreter_id = INTERPRETER_V1

    def __init__(
        self,
        *,
        artifacts,
        artifact_policy,
        budgets,
        recovery,
        installation_id,
        test_mode=False,
    ):
        if any(item is None for item in (artifacts, budgets, recovery)):
            raise ValueError("Artifacts, budgets and recovery fence are required")
        if not test_mode and any(
            getattr(item, "test_only", False) for item in (artifacts, budgets, recovery)
        ):
            raise ValueError("Synthetic providers require test mode")
        self.artifacts, self.budgets, self.recovery = artifacts, budgets, recovery
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        if self.policy.source_id is not None:
            raise ValueError("Progression requires workspace response policy")
        self.installation_id = installation_id

    def region_for(self, execution):
        """v1 derives every execution it claims with v1's rules."""
        return _Region

    # ---------------------------------------------------------- values

    def _value(self, db, execution, artifact_id, version, digest):
        scratch = _SCRATCH.get()
        cached = None if scratch is None else scratch["values"].get((artifact_id, version, digest))
        if cached is not None:
            return json.loads(cached)
        content = self.artifacts._get_internal(
            db,
            _scope(execution),
            ArtifactHandle(id=artifact_id, version=version, sha256=digest),
            keys=None if scratch is None else scratch["keys"],
        )
        if scratch is not None:
            scratch["values"][(artifact_id, version, digest)] = content
        return json.loads(content)

    def _attempt(self, db, execution, row, *, values):
        value, failure, failed_at = MISSING, None, None
        if row.result_artifact_id is not None and (values or row.state in ("failed", "cancelled")):
            content = self._value(
                db,
                execution,
                row.result_artifact_id,
                row.result_artifact_version,
                row.result_sha256,
            )
            if row.state == "failed":
                if type(content) is dict:
                    failure = _failure(content.get("failure"))
                    failed_at = content.get("failed_at_ms")
            elif row.state == "cancelled":
                # Only an interrupted record means anything for a cancelled attempt.
                if type(content) is dict and content.get("failure") == INTERRUPTED:
                    failure = INTERRUPTED
            else:
                value = content
        return AttemptRecord(row.id, row.ordinal, row.state, value, failure, failed_at)

    def _occurrences(self, db, execution, paths, *, values_for):
        """Rows of exactly these occurrence paths, read once.

        `values_for(path, states)` decides which results to decrypt, given the
        final attempt state of every loaded path.
        """
        if not paths:
            return {}
        instances = list(
            db.scalars(
                _scoped(ExecutionStepInstance, execution).where(
                    ExecutionStepInstance.occurrence_path == any_(_array(paths))
                )
            )
        )
        if not instances:
            return {}
        by_id = {row.id: row for row in instances}
        attempts, waits, signals = {}, {}, {}
        for row in db.scalars(
            _scoped(ExecutionAttempt, execution)
            .where(
                ExecutionAttempt.step_instance_id == any_(_array(by_id)),
                ExecutionAttempt.parent_attempt_id.is_(None),
            )
            .order_by(ExecutionAttempt.ordinal)
        ):
            path = by_id[row.step_instance_id].occurrence_path
            attempts.setdefault(path, []).append(row)
        for row in db.scalars(
            _scoped(ExecutionWait, execution).where(
                ExecutionWait.step_instance_id == any_(_array(by_id))
            )
        ):
            waits[by_id[row.step_instance_id].occurrence_path] = row
        # An expired wait still sees signals accepted in time (judged at acceptance).
        open_waits = {
            row.id: path for path, row in waits.items() if row.state in ("open", "expired")
        }
        if open_waits:
            for row in db.scalars(
                _scoped(ExecutionInbox, execution)
                .where(
                    ExecutionInbox.wait_id.in_(sorted(open_waits)),
                    ExecutionInbox.status == "pending",
                )
                .order_by(ExecutionInbox.available_at_ms, ExecutionInbox.id)
            ):
                try:
                    payload = self._value(
                        db,
                        execution,
                        row.payload_artifact_id,
                        row.payload_artifact_version,
                        row.payload_sha256,
                    )
                except ExecutionError as error:
                    # Only permanent content refusals reject a signal; an unavailable
                    # key or provider is retried with the whole transition.
                    if error.code not in ("artifact_corrupt", "not_found", "forbidden"):
                        raise
                    payload = MISSING
                signals.setdefault(open_waits[row.wait_id], []).append(
                    SignalRecord(
                        row.id,
                        row.signal_id,
                        row.digest,
                        row.available_at_ms,
                        row.expires_at_ms,
                        payload,
                    )
                )
        states = {path: rows[-1].state for path, rows in attempts.items()}
        result = {}
        for row in instances:
            path = row.occurrence_path
            wait = waits.get(path)
            wanted = values_for(path, states)
            result[path] = OccurrenceRecord(
                path,
                tuple(
                    self._attempt(db, execution, attempt, values=wanted)
                    for attempt in attempts.get(path, [])
                ),
                None if wait is None else WaitRecord(wait.id, wait.state, wait.deadline_ms),
                tuple(signals.get(path, [])),
            )
        return result

    def _context(self, db, execution, program, region_path):
        """Package input, iteration item and index for a region occurrence."""
        segments = region_path.split("/") if region_path else []
        input_value, input_owner = None, None
        item, index, item_owner = MISSING, None, None
        prefix = []
        for segment in segments:
            prefix.append(segment)
            path = "/".join(prefix)
            ref = resolve_occurrence(program, path)
            if isinstance(ref.step, SubworkflowStep) and ref.selector == "call":
                input_owner = path.rpartition(":")[0]
                item, index, item_owner = MISSING, None, None
            elif isinstance(ref.step, IterationStep) and ref.selector not in (None, "#items"):
                item_owner, index = path.rpartition(":")[0], int(ref.selector)
        if input_owner is None:
            raw = self._value(
                db,
                execution,
                execution.input_artifact_id,
                execution.input_artifact_version,
                execution.input_sha256,
            )
            input_value = typed_export(
                raw, data_export(program.root.input_schema, program.root.schemas)
            )
        else:
            ref = resolve_occurrence(program, input_owner)
            _, child = program.child(ref.package, ref.step.workflow_ref)
            pinned = self._occurrences(
                db, execution, {input_owner + "#input"}, values_for=lambda *_: True
            ).get(input_owner + "#input")
            if pinned is None or pinned.final is None or pinned.final.state != "succeeded":
                raise ProgramError("integrity")
            input_value = typed_export(
                pinned.final.value, data_export(child.input_schema, child.schemas)
            )
        if item_owner is not None:
            pinned = self._occurrences(
                db, execution, {item_owner + "#items"}, values_for=lambda *_: True
            ).get(item_owner + "#items")
            if pinned is None or pinned.final is None or pinned.final.state != "succeeded":
                raise ProgramError("integrity")
            values = pinned.final.value
            if type(values) is not list or index >= len(values):
                raise ProgramError("integrity")
            try:
                item = typed_export(values[index], item_export(program, item_owner))
            except (ExpressionError, StopIteration, KeyError):
                # Fail closed: an untyped decimal would compare as text.
                raise ProgramError("integrity") from None
        return input_value, item, index

    def _outer(self, db, execution, program, region_path, body):
        """Typed outputs of enclosing-region steps this region's mappings reference."""
        wanted = _body_refs(body) - {step.id for step in body.steps}
        if not wanted or region_path == "":
            return {}
        found, segments = {}, region_path.split("/")
        for depth in range(len(segments) - 1, -1, -1):
            region = "/".join(segments[:depth])
            owner = resolve_occurrence(program, "/".join(segments[: depth + 1]))
            if isinstance(owner.step, SubworkflowStep) and owner.selector == "call":
                break  # a child package never sees its caller's steps
            _, package, outer_body = region_body(program, region)
            for step in outer_body.steps:
                if step.id in wanted and step.id not in found:
                    path = child_path(region, step.id)
                    paths = {path}
                    if isinstance(step, DecisionStep):
                        paths.add(path + "#select")
                    records = self._occurrences(db, execution, paths, values_for=lambda *_: True)
                    record = records.get(path)
                    if record is None or record.final is None or record.final.state != "succeeded":
                        continue
                    branch = _decision_branch(records.get(path + "#select"))
                    export = output_export(program, package, step, branch)
                    found[step.id] = typed_export(record.final.value, export)
        return found

    def load_progression(self, db, execution, region_path, *, program=None):
        """Committed rows of exactly one region occurrence, plus the values it reads.

        Iteration members are read only up to the pinned item count, and member
        results are decrypted only to compose a fully completed composite.
        """
        program = program or load_program(db, _scope(execution), execution)
        _, _, body = region_body(program, region_path)
        paths, groups, iterations = set(), {}, set()

        def add(path):
            # A path beyond the stored bound cannot exist; derivation fails it.
            if len(path) <= MAX_PATH:
                paths.add(path)

        for step in body.steps:
            try:
                path = child_path(region_path, step.id)
            except ProgramError:
                continue
            add(path)
            if isinstance(step, DecisionStep):
                add(path + "#select")
            elif isinstance(step, SubworkflowStep):
                add(path + "#input")
            elif isinstance(step, ActionDispatchStep):
                add(path + "#dispatch")
                add(path + "#reconcile")
            elif isinstance(step, ParallelStep):
                groups[path] = [path + ":" + b.id for b in step.branches]
                for member in groups[path]:
                    add(member)
            elif isinstance(step, IterationStep):
                add(path + "#items")
                iterations.add(path)
        members = {m for group in groups.values() for m in group}
        occurrences = self._occurrences(
            db, execution, paths, values_for=lambda path, _: path not in members
        )
        found = set()
        for owner in sorted(iterations):
            items = occurrences.get(owner + "#items")
            final = items.final if items else None
            count = len(final.value) if final and type(final.value) is list else 0
            groups[owner] = [
                m for m in (f"{owner}:{k}" for k in range(count)) if len(m) <= MAX_PATH
            ]
            found.update(groups[owner])
        if found:
            occurrences.update(self._occurrences(db, execution, found, values_for=lambda *_: False))
        for group in groups.values():
            records = [occurrences.get(m) for m in group]
            if group and all(r and r.final and r.final.state == "succeeded" for r in records):
                occurrences.update(
                    self._occurrences(db, execution, set(group), values_for=lambda *_: True)
                )
        input_value, item, index = self._context(db, execution, program, region_path)
        return ProgressionState(
            execution_id=execution.id,
            region_path=region_path,
            requested_state=execution.requested_state,
            deadline_ms=execution.deadline_ms,
            input=input_value,
            item=item,
            index=index,
            outer=self._outer(db, execution, program, region_path, body),
            occurrences=occurrences,
        )

    # ---------------------------------------------------------- transitions

    @staticmethod
    def _keys():
        scratch = _SCRATCH.get()
        return None if scratch is None else scratch["keys"]

    def _put(self, db, execution, value):
        try:
            return self.artifacts._put_internal(
                db, _scope(execution), canonical_value(value), self.policy, keys=self._keys()
            )
        except ExecutionError as error:
            if error.code == "invalid_request":
                raise ExpressionError("value_too_large") from None
            raise

    def _event(self, db, execution, epoch, code, *, path=None, attempt_id=None, detail=None):
        record_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            code,
            path=path,
            attempt_id=attempt_id,
            detail=detail,
            keys=self._keys(),
        )

    def _instance(self, db, execution, path, node_id):
        row = db.scalar(
            _scoped(ExecutionStepInstance, execution).where(
                ExecutionStepInstance.occurrence_path == path
            )
        )
        if row is None:
            row = ExecutionStepInstance(
                **_scope(execution).model_dump(),
                execution_id=execution.id,
                occurrence_path=path,
                node_id=node_id,
            )
            db.add(row)
            db.flush()
        elif row.node_id != node_id:
            raise ProgramError("integrity")
        return row

    def _attempts(self, db, execution, instance):
        return list(
            db.scalars(
                _scoped(ExecutionAttempt, execution)
                .where(
                    ExecutionAttempt.step_instance_id == instance.id,
                    ExecutionAttempt.parent_attempt_id.is_(None),
                )
                .order_by(ExecutionAttempt.ordinal)
            )
        )

    def _record(self, db, execution, epoch, action):
        instance = self._instance(db, execution, action.occurrence_path, action.node_id)
        attempts = self._attempts(db, execution, instance)
        if attempts and attempts[-1].state in ("succeeded", "failed", "cancelled"):
            return False  # a repeated transition is a no-op
        if action.failure is None:
            try:
                handle = self._put(db, execution, action.value)
                state, code = "succeeded", "attempt_succeeded"
            except ExpressionError as error:
                action = RecordPure(action.occurrence_path, action.node_id, failure=error.code)
        if action.failure is not None:
            record = {"failure": _failure(action.failure), "failed_at_ms": database_now_ms(db)}
            handle = self._put(db, execution, record)
            state, code = "failed", "attempt_failed"
        attempt = ExecutionAttempt(
            **_scope(execution).model_dump(),
            execution_id=execution.id,
            step_instance_id=instance.id,
            ordinal=(attempts[-1].ordinal + 1) if attempts else 1,
            input_artifact_id=handle.id,
            input_artifact_version=handle.version,
            input_sha256=handle.sha256,
            result_artifact_id=handle.id,
            result_artifact_version=handle.version,
            result_sha256=handle.sha256,
            state=state,
        )
        db.add(attempt)
        db.flush()
        self._event(
            db,
            execution,
            epoch,
            code,
            path=action.occurrence_path,
            attempt_id=attempt.id,
            detail=action.failure and _failure(action.failure),
        )
        wait = db.scalar(
            _scoped(ExecutionWait, execution).where(ExecutionWait.step_instance_id == instance.id)
        )
        if wait is not None:
            self._reject_pending(db, execution, epoch, [wait.id], action.occurrence_path)
        return True

    def _reject_pending(self, db, execution, epoch, wait_ids, path):
        """Signals still pending for a closed wait can never be consumed: refuse them."""
        rows = list(
            db.scalars(
                _scoped(ExecutionInbox, execution)
                .where(
                    ExecutionInbox.wait_id == any_(_array(wait_ids)),
                    ExecutionInbox.status == "pending",
                )
                .order_by(ExecutionInbox.id)
                .with_for_update()
            )
        )
        for row in rows:
            row.status = "conflict"
            self._event(db, execution, epoch, "signal_conflict", path=path)
        db.flush()

    def _create_attempt(self, db, execution, epoch, action):
        instance = self._instance(db, execution, action.occurrence_path, action.node_id)
        attempts = self._attempts(db, execution, instance)
        if any(a.ordinal >= action.ordinal for a in attempts):
            return False
        try:
            handle = self._put(db, execution, action.input)
        except ExpressionError as error:
            # An input beyond the artifact bound fails the step instead of retrying.
            failed = RecordPure(action.occurrence_path, action.node_id, failure=error.code)
            return self._record(db, execution, epoch, failed)
        attempt = ExecutionAttempt(
            **_scope(execution).model_dump(),
            execution_id=execution.id,
            step_instance_id=instance.id,
            ordinal=action.ordinal,
            input_artifact_id=handle.id,
            input_artifact_version=handle.version,
            input_sha256=handle.sha256,
            state="reserved",
        )
        db.add(attempt)
        db.flush()
        if execution.state == "waiting":
            execution.state = "running"  # dispatchable again; no counter changes
        self._event(
            db,
            execution,
            epoch,
            "attempt_reserved",
            path=action.occurrence_path,
            attempt_id=attempt.id,
        )
        return True

    def _open_wait(self, db, execution, epoch, action):
        instance = self._instance(db, execution, action.occurrence_path, action.node_id)
        key = wait_key(action.occurrence_path)
        existing = db.scalar(_scoped(ExecutionWait, execution).where(ExecutionWait.wait_key == key))
        if existing is not None:
            return False
        db.add(
            ExecutionWait(
                **_scope(execution).model_dump(),
                execution_id=execution.id,
                step_instance_id=instance.id,
                wait_key=key,
                deadline_ms=action.deadline_ms,
                state="open",
            )
        )
        db.flush()
        self._event(db, execution, epoch, "waiting", path=action.occurrence_path)
        return True

    def _consume(self, db, execution, epoch, action):
        wait = db.scalar(
            _scoped(ExecutionWait, execution)
            .where(ExecutionWait.id == action.wait_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if wait is None or wait.state not in ("open", "expired"):
            return False
        changed = False
        ids = sorted({*action.rejected, *([action.inbox_id] if action.inbox_id else [])})
        rows = {
            row.id: row
            for row in db.scalars(
                _scoped(ExecutionInbox, execution)
                .where(ExecutionInbox.id.in_(ids), ExecutionInbox.wait_id == wait.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        for identifier in action.rejected:
            row = rows.get(identifier)
            if row is not None and row.status == "pending":
                row.status = "conflict"
                changed = True
                self._event(db, execution, epoch, "signal_conflict", path=action.occurrence_path)
        row = rows.get(action.inbox_id) if action.inbox_id else None
        # Only a signal accepted before its deadline may satisfy the wait, even when
        # the sweeper marked the wait expired before this transition ran.
        if (
            row is None
            or row.status != "pending"
            or row.available_at_ms >= min(row.expires_at_ms, wait.deadline_ms)
        ):
            db.flush()
            return changed
        row.status = "delivered"
        row.delivery_count += 1
        wait.state = "satisfied"
        instance = db.get(ExecutionStepInstance, wait.step_instance_id)
        attempt = ExecutionAttempt(
            **_scope(execution).model_dump(),
            execution_id=execution.id,
            step_instance_id=instance.id,
            ordinal=1,
            input_artifact_id=row.payload_artifact_id,
            input_artifact_version=row.payload_artifact_version,
            input_sha256=row.payload_sha256,
            result_artifact_id=row.payload_artifact_id,
            result_artifact_version=row.payload_artifact_version,
            result_sha256=row.payload_sha256,
            state="succeeded",
        )
        db.add(attempt)
        db.flush()
        self._event(
            db,
            execution,
            epoch,
            "signal_received",
            path=action.occurrence_path,
            attempt_id=attempt.id,
        )
        return True

    def _cancel(self, db, execution, epoch, action):
        """Cancel undispatched attempts and open waits under a region prefix.

        Attempts already holding a usage reservation are cancelled too; their
        capacity is released by the separate capacity transaction.
        """
        pattern = action.prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        instances = select(ExecutionStepInstance.id).where(
            ExecutionStepInstance.workspace_id == execution.workspace_id,
            ExecutionStepInstance.project_id == execution.project_id,
            ExecutionStepInstance.environment_id == execution.environment_id,
            ExecutionStepInstance.execution_id == execution.id,
            ExecutionStepInstance.occurrence_path.like(pattern, escape="\\"),
        )
        attempts = list(
            db.scalars(
                _scoped(ExecutionAttempt, execution)
                .where(
                    ExecutionAttempt.step_instance_id.in_(instances),
                    ExecutionAttempt.state == "reserved",
                )
                .order_by(ExecutionAttempt.id)
                .with_for_update()
            )
        )
        waits = list(
            db.scalars(
                _scoped(ExecutionWait, execution)
                .where(ExecutionWait.step_instance_id.in_(instances), ExecutionWait.state == "open")
                .order_by(ExecutionWait.id)
                .with_for_update()
            )
        )
        for row in attempts:
            row.state = "cancelled"
        for row in waits:
            row.state = "cancelled"
        db.flush()
        if waits:
            self._reject_pending(db, execution, epoch, [w.id for w in waits], action.prefix or None)
        if attempts or waits:
            self._event(
                db, execution, epoch, "cancelled", path=action.prefix or None, detail="undispatched"
            )
        return bool(attempts or waits)

    def _release_paused(self, db, execution, epoch, limit):
        """A pause releases the root attempts that have not started (Task 11).

        Reserved attempts, and dispatched ones no runtime has claimed (no lease), end
        `cancelled` with the interrupted record; a pending delivery can no longer be
        claimed. The capacity step confirms their stop and frees their reservations,
        so a long pause neither holds compute slots nor spends an attempt: resuming
        creates each anew. Claimed attempts stop when their renewal is refused.
        """
        attempts = list(
            db.scalars(
                _scoped(ExecutionAttempt, execution)
                .where(
                    ExecutionAttempt.execution_id == execution.id,
                    ExecutionAttempt.parent_attempt_id.is_(None),
                    ExecutionAttempt.state.in_(("reserved", "dispatched")),
                    _unclaimed(),
                )
                .order_by(ExecutionAttempt.id)
                .limit(limit)
                .with_for_update()
            )
        )
        if not attempts:
            return 0
        for delivery in db.scalars(
            _scoped(ExecutionActivityDelivery, execution)
            .where(ExecutionActivityDelivery.attempt_id.in_([a.id for a in attempts]))
            .order_by(ExecutionActivityDelivery.id)
            .with_for_update()
        ):
            if delivery.status == "pending":
                delivery.status = "conflict"
        record = {"failure": INTERRUPTED, "failed_at_ms": database_now_ms(db)}
        for attempt in attempts:
            handle = self._put(db, execution, record)
            attempt.result_artifact_id = handle.id
            attempt.result_artifact_version = handle.version
            attempt.result_sha256 = handle.sha256
            attempt.state = "cancelled"
        db.flush()
        for attempt in attempts:
            self._event(
                db, execution, epoch, "cancelled", attempt_id=attempt.id, detail=INTERRUPTED
            )
        return len(attempts)

    def _apply(self, db, execution, epoch, action) -> bool:
        """Apply one pure action; False when its effect already exists."""
        if isinstance(action, RecordPure):
            return self._record(db, execution, epoch, action)
        if isinstance(action, CreateAttempt):
            return self._create_attempt(db, execution, epoch, action)
        if isinstance(action, OpenWait):
            return self._open_wait(db, execution, epoch, action)
        if isinstance(action, ConsumeSignal):
            return self._consume(db, execution, epoch, action)
        if isinstance(action, CancelUndispatched):
            return self._cancel(db, execution, epoch, action)
        raise ProgramError("integrity")

    # ---------------------------------------------------------- orchestration

    def _round(self, db, execution, program, epoch, remaining):
        """One depth-first pass from the root over the frontier regions.

        Each region with actions applies them and is not descended into again in
        this pass (its parent sees the change in the next pass); independent
        frontier regions, such as concurrent iteration items, advance together.
        Returns (applied, terminal action or None, earliest wake-up, waits touched).
        """
        stack, wake, applied, terminal, touched = [""], None, 0, None, False
        while stack and applied < remaining:
            region = stack.pop()
            now = database_now_ms(db)
            if region == "" and past_deadline(execution, now):
                # Checked before any load, so even unreadable inputs end at the deadline.
                derivation = Derivation(actions=(CancelUndispatched(""), Fail("deadline_exceeded")))
            elif region == "" and execution.requested_state == "cancelled":
                # Likewise a cancellation ends work whatever the inputs hold.
                derivation = Derivation(actions=(CancelUndispatched(""), Fail("cancelled")))
            elif region == "" and not _progressing(execution):
                # Paused, or fenced for reconciliation: nothing new, and nothing loaded.
                derivation = Derivation()
            else:
                if program is None:
                    program = load_program(db, _scope(execution), execution)
                state = self.load_progression(db, execution, region, program=program)
                derivation = derive(program, state, now, self.region_for(execution))
            if derivation.wake_at_ms is not None:
                wake = derivation.wake_at_ms if wake is None else min(wake, derivation.wake_at_ms)
            pure = [a for a in derivation.actions if not isinstance(a, (Complete, Fail))]
            changed = 0
            for action in pure[: remaining - applied]:
                if self._apply(db, execution, epoch, action):
                    changed += 1
                    touched = touched or _touches_capacity(program, action)
            applied += changed
            if changed:
                continue
            final = next((a for a in derivation.actions if isinstance(a, (Complete, Fail))), None)
            if final is not None:
                terminal = final
                continue
            if pure:
                # Every action's effect already exists: the rows changed under us.
                raise ProgramError("integrity")
            stack.extend(reversed(derivation.regions))
        return applied, terminal, wake, touched

    def _epoch(self, db, epoch=None):
        """The fenced epoch from the engine, or this interpreter's own fence."""
        if epoch is not None:
            if type(epoch) is not int or epoch < 0:
                raise ExecutionError("authority_unavailable")
            return epoch
        try:
            epoch = self.recovery.current(db)
            if type(epoch) is not int or epoch < 0:
                raise ValueError
            return epoch
        except Exception:
            raise ExecutionError("authority_unavailable") from None

    @staticmethod
    def _capacity_pending(db, execution):
        """Indexed probes of committed state: could a capacity step change anything?

        An open wait without its `capacity.wait:` row (while running), a closed wait
        retained but not released, or a cancelled, never claimed attempt whose
        reservation is not yet stopped. Bounded passes skip the capacity step, so
        this reads state rather than trusting what the last pass applied.
        """

        def request(key):
            return (
                select(ExecutionRequest.id)
                .where(
                    ExecutionRequest.workspace_id == execution.workspace_id,
                    ExecutionRequest.project_id == execution.project_id,
                    ExecutionRequest.environment_id == execution.environment_id,
                    ExecutionRequest.request_key == literal(key) + ExecutionWait.id,
                )
                .exists()
            )

        def waits(*conditions):
            return (
                select(ExecutionWait.id)
                .where(
                    ExecutionWait.workspace_id == execution.workspace_id,
                    ExecutionWait.project_id == execution.project_id,
                    ExecutionWait.environment_id == execution.environment_id,
                    ExecutionWait.execution_id == execution.id,
                    *conditions,
                )
                .exists()
            )

        probes = [
            waits(
                ExecutionWait.state != "open",
                request("capacity.wait:"),
                ~request("capacity.wait-release:"),
            ),
            select(ExecutionAttempt.id)
            .join(
                ExecutionUsageReservation,
                ExecutionUsageReservation.attempt_id == ExecutionAttempt.id,
            )
            .where(
                ExecutionAttempt.workspace_id == execution.workspace_id,
                ExecutionAttempt.execution_id == execution.id,
                ExecutionAttempt.state == "cancelled",
                ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
                _unclaimed(),
            )
            .exists(),
        ]
        if execution.requested_state == "running":
            probes.append(waits(ExecutionWait.state == "open", ~request("capacity.wait:")))
        return bool(db.scalar(select(or_(*probes))))

    def _capacity_work(self, db, execution):
        """What an ordered capacity transaction could do now (execution-wide reads)."""
        inflight = db.scalar(
            select(func.count())
            .select_from(ExecutionAttempt)
            .where(
                ExecutionAttempt.workspace_id == execution.workspace_id,
                ExecutionAttempt.execution_id == execution.id,
                ExecutionAttempt.state.in_(("reserved", "dispatched", "claimed", "uncertain")),
            )
        )
        unstopped = (
            select(func.count())
            .select_from(ExecutionUsageReservation)
            .join(ExecutionAttempt, ExecutionAttempt.id == ExecutionUsageReservation.attempt_id)
            .where(
                ExecutionUsageReservation.workspace_id == execution.workspace_id,
                ExecutionUsageReservation.execution_id == execution.id,
                ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
            )
        )
        # Cancelled before any claim: the capacity step confirms that stop itself.
        stoppable = db.scalar(unstopped.where(ExecutionAttempt.state == "cancelled", _unclaimed()))
        running = db.scalar(unstopped) - stoppable
        retained = set(
            db.scalars(
                select(ExecutionRequest.request_key).where(
                    ExecutionRequest.workspace_id == execution.workspace_id,
                    ExecutionRequest.execution_id == execution.id,
                    ExecutionRequest.request_key.like("capacity.wait%"),
                )
            )
        )
        retain, release = [], []
        for wait in db.scalars(_scoped(ExecutionWait, execution).order_by(ExecutionWait.id)):
            held = "capacity.wait:" + wait.id in retained
            released = "capacity.wait-release:" + wait.id in retained
            # A paused or cancelling execution retains nothing; resuming marks it due.
            if wait.state == "open" and not held and execution.requested_state == "running":
                retain.append(wait.id)
            elif wait.state != "open" and held and not released:
                release.append(wait.id)
        contained = inflight == 0 and running == 0
        return (retain if contained and not stoppable else []), release, contained, stoppable

    def advance_execution(self, db, worker, execution, *, bound=BOUND, epoch=None):
        """Apply at most `bound` pure transitions to the locked execution."""
        token = _SCRATCH.set({"values": {}, "keys": {}})
        try:
            return self._advance_execution(db, worker, execution, bound=bound, epoch=epoch)
        finally:
            _SCRATCH.reset(token)

    def _advance_execution(self, db, worker, execution, *, bound, epoch):
        if type(bound) is not int or not 1 <= bound <= BOUND:
            raise ExecutionError("invalid_request")
        epoch = self._epoch(db, epoch)
        if execution.next_event_sequence == 1:
            self._event(db, execution, epoch, "started")
        from .recovery import hold_restored, restored

        if restored(execution, epoch):
            # Admitted before a restore: held until an owner releases it, and nothing
            # new is derived. A cancellation or its deadline ends it below only once
            # nothing it started may still run; the sweeper wakes it for its deadline
            # only after the release.
            hold_restored(
                db,
                execution,
                epoch,
                lambda code, attempt_id: self._event(
                    db, execution, epoch, code, attempt_id=attempt_id, detail="restored"
                ),
            )
        from .control import observe_control

        # A requested pause or resume becomes the observed state (Task 11).
        observed = observe_control(
            db, execution, lambda code: self._event(db, execution, epoch, code)
        )
        released = (
            self._release_paused(db, execution, epoch, bound) if execution.state == "paused" else 0
        )
        # The deadline, a cancellation, a pause and a reconciliation fence need only
        # the execution row: a program that cannot be loaded never holds them up.
        program = (
            load_program(db, _scope(execution), execution)
            if _progressing(execution) and not past_deadline(execution, database_now_ms(db))
            else None
        )
        transitions, wake, terminal = int(observed) + released, None, None
        touched = bool(released)
        while transitions < bound:
            applied, terminal, round_wake, round_touched = self._round(
                db, execution, program, epoch, bound - transitions
            )
            touched = touched or round_touched
            if round_wake is not None:
                wake = round_wake if wake is None else min(wake, round_wake)
            if applied == 0:
                break
            transitions += applied
        capacity = False
        more = transitions >= bound and terminal is None
        # A pass stopped by the bound continues at once; its capacity step waits for
        # the first pass that finishes, so a blocked wait never throttles progress.
        # Otherwise capacity work is read only when it can have changed: a terminal
        # action is next, nothing moved, this run touched waits or cancelled work, or
        # committed state shows pending work (perhaps left by an earlier bounded pass).
        if not more and (
            terminal is not None
            or transitions == 0
            or touched
            or self._capacity_pending(db, execution)
        ):
            retain, release, contained, stoppable = self._capacity_work(db, execution)
            capacity = bool(retain or release or stoppable or (terminal is not None and contained))
        # A capacity pass keeps its own wake-up (say a sibling's retry back-off): the
        # engine adds the capacity deferral and the earliest time wins. No wake-up is
        # later than the deadline, which ends the work wherever it stands.
        if wake is not None and not more:
            mark_runnable(db, execution, min(wake, execution.deadline_ms))
        db.flush()
        return AdvanceOutcome(
            execution_id=execution.id,
            transitions=transitions,
            state=execution.state,
            runnable=capacity or more,
            reason="capacity" if capacity else None,
        )

    def fence_execution(self, db, execution, code, *, epoch=None) -> bool:
        """Fence a started execution whose stored program or rows fail verification.

        `integrity` and `occurrence_unresolved` repeat on every try, so the engine
        fences instead of retrying until the deadline: `reconciliation_required`,
        reason `uncertain`, with the code as event detail. Progression then derives
        nothing new; cancellation and the deadline still end it. An execution still
        `pending` keeps its admission slot and is left to its deadline. True when
        the state changed.
        """
        epoch = self._epoch(db, epoch)
        if execution.state in TERMINAL or execution.state in ("pending", "reconciliation_required"):
            return False
        execution.state, execution.reason = "reconciliation_required", "uncertain"
        clear_runnable(db, execution)
        db.flush()
        self._event(db, execution, epoch, "reconciliation_required", detail=code)
        return True

    # ---------------------------------------------------------- capacity

    def _actor(self, db, execution):
        """The initiating principal's current authority, or None when revoked."""
        principal = Principal(
            execution.initiating_api_key_id or execution.initiating_user_id,
            execution.workspace_id,
            "service" if execution.initiating_api_key_id else "member",
            None if execution.initiating_api_key_id else execution.initiating_user_id,
        )
        try:
            with db.begin_nested():
                return authorize_execution(db, principal, _scope(execution), "execute")
        except ExecutionError:
            return None

    def _stop_cancelled(self, db, execution, epoch):
        """Confirm compute stop for cancelled attempts that no runtime ever claimed."""
        rows = db.execute(
            select(ExecutionAttempt.id)
            .join(
                ExecutionUsageReservation,
                ExecutionUsageReservation.attempt_id == ExecutionAttempt.id,
            )
            .where(
                ExecutionAttempt.workspace_id == execution.workspace_id,
                ExecutionAttempt.execution_id == execution.id,
                ExecutionAttempt.state == "cancelled",
                ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
                _unclaimed(),
            )
            .order_by(ExecutionAttempt.id)
        ).scalars()
        stopped = 0
        for attempt_id in list(rows):
            observation = ExecutionReconciliationObservation(
                **_scope(execution).model_dump(),
                execution_id=execution.id,
                attempt_id=attempt_id,
                source_id="orchestrator",
                observation_id="cancelled-before-dispatch",
                digest=sha256(b"cancelled-before-dispatch:" + attempt_id.encode()).hexdigest(),
                code="compute_stopped",
                observed_at_ms=database_now_ms(db),
            )
            db.add(observation)
            db.flush()
            self.budgets.confirm_stopped(db, attempt_id, observation.id)
            # Never claimed: released at zero (Task 11), unless delivered before a restore.
            self.budgets.release_unstarted(db, attempt_id, epoch)
            stopped += 1
        return stopped

    def _terminal(self, db, execution, program, epoch, action):
        if isinstance(action, Complete):
            try:
                handle = self._put(db, execution, action.value)
                execution.result_artifact_id = handle.id
                execution.result_artifact_version = handle.version
                execution.result_sha256 = handle.sha256
            except ExpressionError as error:
                action = Fail(error.code)
        self.budgets.release_pending(db, execution.id)
        clear_runnable(db, execution)
        if isinstance(action, Complete):
            execution.state, execution.reason = "succeeded", "completed"
            self._event(db, execution, epoch, "succeeded")
        elif action.code == "cancelled" and execution.requested_state == "cancelled":
            execution.state, execution.reason = "cancelled", "control"
            self._event(db, execution, epoch, "cancelled")
        else:
            code = _failure(action.code)
            execution.state = "failed"
            execution.reason = {
                "deadline_exceeded": "deadline_exceeded",
                "retries_exhausted": "provider_unavailable",
                "unavailable": "provider_unavailable",
                "rate_limited": "provider_unavailable",
                "timeout_before_dispatch": "provider_unavailable",
                "provider_failed": "provider_unavailable",
            }.get(code, code if code in ACTION_FAILURES else "invalid_result")
            self._event(db, execution, epoch, "failed", detail=code)
        db.flush()

    def apply_capacity(self, db, worker, execution_id, *, epoch=None):
        """One ordered capacity transaction: workspace, pool, capacity, then rows.

        Releases, stops and the terminal state never wait on a retention: each
        retention runs in its own savepoint, and a refusal leaves only that wait
        unretained. Idempotent: every change is keyed by Task 5 request rows or by
        states checked under the locks, so concurrent orchestrators cannot
        double-count.
        """
        token = _SCRATCH.set({"values": {}, "keys": {}})
        try:
            return self._apply_capacity(db, worker, execution_id, epoch)
        finally:
            _SCRATCH.reset(token)

    def _apply_capacity(self, db, worker, execution_id, epoch):
        hint = db.scalar(
            select(Execution)
            .where(Execution.id == execution_id)
            .execution_options(populate_existing=True)
        )
        if hint is None:
            raise ExecutionError("not_found")
        if hint.state in TERMINAL:
            return self._outcome(hint, 0)
        # The fair gates first, before any row lock and outside the savepoints below,
        # so each lock under them finds them held (`authority_gate`).
        gates(db, _scope(hint), hint.deployment_revision_id, users=[hint.initiating_user_id])
        epoch = self._epoch(db, epoch)
        retain, _, _, _ = self._capacity_work(db, hint)
        actor = None
        visible = db.scalar(scoped(ExecutionCapacity, _scope(hint)))
        if retain and (visible is None or visible.retained_waits >= visible.wait_limit):
            # Visibly at quota: no provider call. Zero changes make the engine back off.
            logger.warning("Execution wait retention deferred: capacity_unavailable")
            retain = []
        if retain:
            # Retention needs current authority and every Task 5 parent, all taken
            # before the execution row; without them only releases proceed.
            actor = self._actor(db, hint)
            if actor is not None:
                try:
                    with db.begin_nested():
                        self.budgets._parents(db, actor, hint.deployment_revision_id)
                except ExecutionError as error:
                    logger.warning("Execution wait retention deferred: %s", error.code)
                    actor = None
        scope = _scope(hint)
        authority_gate.workspace(db, hint.workspace_id)  # before any row lock
        share(db, select(Workspace).where(Workspace.id == hint.workspace_id))
        lock(db, scoped(ExecutionCapacity, scope))
        execution = record(db, Execution, scope, execution_id, locked=True)
        if execution.state in TERMINAL:
            return self._outcome(execution, 0)
        changes = 0
        program = None
        if past_deadline(execution, database_now_ms(db)):
            derivation = Derivation(actions=(CancelUndispatched(""), Fail("deadline_exceeded")))
        elif execution.requested_state == "cancelled":
            derivation = Derivation(actions=(CancelUndispatched(""), Fail("cancelled")))
        elif not _progressing(execution):
            derivation = Derivation()  # paused or fenced: no terminal action to find
        else:
            program = load_program(db, scope, execution)
            state = self.load_progression(db, execution, "", program=program)
            derivation = derive(program, state, database_now_ms(db))
        terminal = next((a for a in derivation.actions if isinstance(a, (Complete, Fail))), None)
        if terminal is not None:
            for action in derivation.actions:
                if isinstance(action, CancelUndispatched):
                    changes += self._cancel(db, execution, epoch, action)
        changes += self._stop_cancelled(db, execution, epoch)
        retain, release, contained, _ = self._capacity_work(db, execution)
        for wait_id in release:
            self.budgets.release_wait(db, wait_id)
            changes += 1
        if terminal is not None and contained:
            self._terminal(db, execution, program, epoch, terminal)
            return self._outcome(execution, changes + 1)
        if actor is not None:
            for wait_id in retain:
                try:
                    with db.begin_nested():
                        self.budgets.retain_wait(db, actor, wait_id)
                    changes += 1
                except ExecutionError as error:
                    # Quota, pause, authority or a closed race: this wait stays
                    # unretained for now. Anything else is not a refusal and aborts.
                    if error.code not in _RETENTION_REFUSALS:
                        raise
                    logger.warning("Execution wait retention deferred: %s", error.code)
        if execution.state == "waiting" and not db.scalar(
            _scoped(ExecutionWait, execution).where(ExecutionWait.state == "open").limit(1)
        ):
            execution.state = "running"  # nothing left to wait on; no counter changes
            db.flush()
        return self._outcome(execution, changes)

    @staticmethod
    def _outcome(execution, changes):
        return AdvanceOutcome(
            execution_id=execution.id,
            transitions=min(changes, 2147483647),
            state=execution.state,
            runnable=execution.runnable_at_ms is not None,
            reason=None,
        )


class InterpreterV2(InterpreterV1):
    """Progression for `lotuspond.execution-interpreter.v2` (E05), and v1 unchanged.

    One worker may claim both versions: each execution is derived with the rules of
    the version it pins, so a v1 execution never sees an E05 semantic.
    """

    interpreter_id = INTERPRETER_V2

    def region_for(self, execution):
        return _RegionV2 if execution.interpreter_id == INTERPRETER_V2 else _Region

    def load_progression(self, db, execution, region_path, *, program=None):
        if execution.interpreter_id == INTERPRETER_V2:
            from .actions import verify_intents

            scratch = _SCRATCH.get()
            if scratch is None or not scratch.get("intents_verified"):
                # Every later read recomputes each intent's digests (spec, "Action intents").
                verify_intents(
                    db,
                    execution,
                    artifacts=self.artifacts,
                    keys=self._keys(),
                    program=program,
                    installation_id=self.installation_id,
                )
                if scratch is not None:
                    scratch["intents_verified"] = True
        state = super().load_progression(db, execution, region_path, program=program)
        if execution.interpreter_id == INTERPRETER_V2:
            state = self._with_tasks(db, execution, state)
            state = self._with_actions(db, execution, state, program)
        return state

    def _apply(self, db, execution, epoch, action) -> bool:
        if isinstance(action, CreateIntent):
            return self._create_intent(db, execution, epoch, action)
        if isinstance(action, OpenTask):
            return self._open_task(db, execution, epoch, action)
        if isinstance(action, ConsumeDecision):
            return self._consume_decision(db, execution, epoch, action)
        if isinstance(action, CreateDispatch):
            return self._create_dispatch(db, execution, epoch, action)
        if isinstance(action, CreateReadback):
            return self._create_readback(db, execution, epoch, action)
        if isinstance(action, RecordReadback):
            return self._record_readback(db, execution, epoch, action)
        if isinstance(action, SettleWrite):
            return self._settle_write(db, execution, epoch, action)
        if isinstance(action, RequireResolution):
            return self._require_resolution(db, execution, epoch, action)
        return super()._apply(db, execution, epoch, action)

    def _settle_write(self, db, execution, epoch, action):
        """Close the uncertain write: succeeded with the receipt handle, or failed.

        Its reservation is untouched: a write whose compute stop is not confirmed
        (a lapse) keeps its slot and liability, so the execution still waits for
        that stop before it can end (containment)."""
        from ..models import ExecutionAttempt

        write = db.scalar(
            _scoped(ExecutionAttempt, execution)
            .where(ExecutionAttempt.id == action.attempt_id)
            .with_for_update()
        )
        instance = self._instance(db, execution, action.occurrence_path, action.node_id)
        if write is None or write.step_instance_id != instance.id:
            raise ProgramError("integrity")
        if write.state != "uncertain":
            return False
        if action.failure is None:
            handle = self._put(db, execution, action.value)
            write.state, code = "succeeded", "attempt_succeeded"
        else:
            content = {"failure": _failure(action.failure), "failed_at_ms": database_now_ms(db)}
            handle = self._put(db, execution, content)
            write.state, code = "failed", "attempt_failed"
        write.result_artifact_id = handle.id
        write.result_artifact_version = handle.version
        write.result_sha256 = handle.sha256
        if execution.reason == "action_uncertain":
            execution.reason = None
        db.flush()
        self._event(
            db,
            execution,
            epoch,
            code,
            path=action.occurrence_path,
            attempt_id=write.id,
            detail=action.failure and _failure(action.failure),
        )
        return True

    def _readbacks(self):
        from .action_reconciliation import ReadbackService

        return ReadbackService(
            artifacts=self.artifacts,
            artifact_policy=self.policy,
            installation_id=self.installation_id,
        )

    def _uncertain_write(self, db, execution, path, node_id, ordinal):
        """The `#dispatch` record's attempt `ordinal`, which must be its last and uncertain."""
        instance = self._instance(db, execution, path, node_id)
        attempts = self._attempts(db, execution, instance)
        if not attempts or attempts[-1].ordinal != ordinal or attempts[-1].state != "uncertain":
            return None
        return attempts[-1]

    def _create_readback(self, db, execution, epoch, action):
        """Task 8: a read-back of the uncertain write its ordinal names."""
        from ..models import ExecutionActionCommand, ExecutionActionIntent
        from .budgets import READBACK_STRIDE

        if execution.interpreter_id != INTERPRETER_V2 or not action.occurrence_path.endswith(
            "#reconcile"
        ):
            raise ProgramError("integrity")
        base = action.occurrence_path[: -len("#reconcile")]
        write = self._uncertain_write(
            db, execution, base + "#dispatch", action.node_id, action.ordinal // READBACK_STRIDE
        )
        if write is None:
            return False
        intent = db.scalar(
            _scoped(ExecutionActionIntent, execution)
            .where(
                ExecutionActionIntent.id == action.intent_id,
                ExecutionActionIntent.execution_id == execution.id,
            )
            .with_for_update()
        )
        command = db.scalar(
            _scoped(ExecutionActionCommand, execution).where(
                ExecutionActionCommand.attempt_id == write.id
            )
        )
        if intent is None or command is None or command.intent_id != intent.id:
            raise ProgramError("integrity")
        created = self._readbacks().schedule(
            db,
            execution,
            intent,
            path=action.occurrence_path,
            node_id=action.node_id,
            ordinal=action.ordinal,
            reason=action.reason,
            epoch=epoch,
            keys=self._keys(),
        )
        if created is not None and execution.state == "waiting":
            execution.state = "running"
        return created is not None

    def _record_readback(self, db, execution, epoch, action):
        from ..models import ExecutionActionObservation

        existing = db.scalar(
            _scoped(ExecutionActionObservation, execution).where(
                ExecutionActionObservation.attempt_id == action.attempt_id,
                ExecutionActionObservation.source == "readback",
            )
        )
        if existing is not None:
            return False
        self._readbacks().record(db, execution, epoch, action.attempt_id, keys=self._keys())
        return True

    def _require_resolution(self, db, execution, epoch, action):
        """Only an owner or admin settles it now (`ResolutionService`); nothing else runs."""
        if execution.state in TERMINAL or execution.state == "reconciliation_required":
            return False
        execution.state, execution.reason = "reconciliation_required", "action_uncertain"
        clear_runnable(db, execution)
        db.flush()
        self._event(
            db,
            execution,
            epoch,
            "reconciliation_required",
            path=action.occurrence_path,
            detail="action_uncertain",
        )
        return True

    def _with_actions(self, db, execution, state, program):
        """Each dispatch occurrence's intent, observations, receipts and confirmed stops."""
        from ..models import (
            ExecutionActionCommand,
            ExecutionActionIntent,
            ExecutionActionObservation,
            ExecutionActionReceipt,
        )
        from .action_reconciliation import dispatch_ref, write_spec

        writes = {
            path: record.attempts
            for path, record in state.occurrences.items()
            if path.endswith("#dispatch") and record.attempts
        }
        if not writes:
            return state
        ids = [a.id for attempts in writes.values() for a in attempts]
        commands = {
            row.attempt_id: row
            for row in db.scalars(
                _scoped(ExecutionActionCommand, execution).where(
                    ExecutionActionCommand.attempt_id.in_(ids)
                )
            )
        }
        reads = {
            path: state.occurrences[path[: -len("#dispatch")] + "#reconcile"].attempts
            for path in writes
            if path[: -len("#dispatch")] + "#reconcile" in state.occurrences
        }
        reservations = {
            row.attempt_id: row
            for row in db.scalars(
                _scoped(ExecutionUsageReservation, execution).where(
                    ExecutionUsageReservation.attempt_id.in_(
                        ids + [a.id for attempts in reads.values() for a in attempts]
                    )
                )
            )
        }
        actions = {}
        for path, attempts in writes.items():
            intent_ids = {commands[a.id].intent_id for a in attempts if a.id in commands}
            if not intent_ids:
                continue
            if len(intent_ids) != 1:
                raise ProgramError("integrity")
            intent = db.scalar(
                _scoped(ExecutionActionIntent, execution).where(
                    ExecutionActionIntent.id == next(iter(intent_ids))
                )
            )
            observations = tuple(
                ObservationRecord(
                    row.id,
                    row.attempt_id,
                    row.result,
                    row.qualified,
                    row.source,
                    row.rule_id.removeprefix("resolution-") if row.source == "resolution" else None,
                )
                for row in db.scalars(
                    _scoped(ExecutionActionObservation, execution)
                    .where(ExecutionActionObservation.intent_id == intent.id)
                    .order_by(ExecutionActionObservation.id)
                )
            )
            receipts = {
                row.attempt_id: row.id
                for row in db.scalars(
                    _scoped(ExecutionActionReceipt, execution)
                    .where(ExecutionActionReceipt.intent_id == intent.id)
                    # "historical" sorts first, so a live receipt names the write.
                    .order_by(ExecutionActionReceipt.source, ExecutionActionReceipt.id)
                )
            }
            program = program or load_program(db, _scope(execution), execution)
            target_key = write_spec(program, dispatch_ref(program, path)).target_key
            actions[path[: -len("#dispatch")]] = ActionRecord(
                intent_id=intent.id,
                effect=intent.effect_state,
                target_key=target_key,
                observations=observations,
                stopped=frozenset(
                    a.id
                    for a in attempts
                    if a.id in reservations and reservations[a.id].compute_stopped_at_ms is not None
                ),
                receipts=receipts,
                finished={
                    a.id: reservations[a.id].completed_at_ms
                    for a in reads.get(path, ())
                    if a.id in reservations and reservations[a.id].completed_at_ms is not None
                },
            )
        return replace(state, actions=actions)

    def _create_dispatch(self, db, execution, epoch, action):
        """The one command attempt of an intent, only once it may be dispatched.

        Approval alone is not enough: dispatch rechecks everything under fresh
        authority (Task 6). Here the intent must be this execution's, verified, not
        yet dispatched, and approved by the bound decision (or unreviewed by design).
        """
        from ..models import ExecutionActionDecision, ExecutionActionIntent
        from .actions import load_intent

        if execution.interpreter_id != INTERPRETER_V2:
            raise ProgramError("integrity")
        instance = self._instance(db, execution, action.occurrence_path, action.node_id)
        existing = self._attempts(db, execution, instance)
        if action.ordinal == 1 and existing:
            return False  # a repeated transition creates nothing
        if action.ordinal > 1 and (
            not existing
            or existing[-1].ordinal != action.ordinal - 1
            or existing[-1].state != "failed"
        ):
            return False
        intent = db.scalar(
            _scoped(ExecutionActionIntent, execution)
            .where(
                ExecutionActionIntent.id == action.intent_id,
                ExecutionActionIntent.execution_id == execution.id,
            )
            .with_for_update()
        )
        if intent is None:
            raise ProgramError("integrity")
        load_intent(
            db,
            _scope(execution),
            intent.id,
            artifacts=self.artifacts,
            keys=self._keys(),
            installation_id=self.installation_id,
        )
        step_path = action.occurrence_path.rpartition("#")[0]
        refused, decision = None, None
        if intent.approval_mode == "human":
            decision = (
                None
                if action.decision_id is None
                else db.scalar(
                    _scoped(ExecutionActionDecision, execution).where(
                        ExecutionActionDecision.id == action.decision_id,
                        ExecutionActionDecision.intent_id == intent.id,
                    )
                )
            )
            if decision is None or decision.decision != "allow":
                refused = "approval_denied"
            elif intent.authorization_state != "approved":
                refused = "binding_refused"
        elif intent.authorization_state != "proposed" or action.decision_id is not None:
            refused = "binding_refused"
        if refused is None and action.ordinal > 1:
            # Task 8: a resend only after a qualified absence of this very write.
            if intent.effect_state != "absent" or not self._qualified_absence(
                db, execution, intent, existing[-1]
            ):
                refused = "binding_refused"
            elif decision is not None and decision.expires_at_ms <= database_now_ms(db):
                refused = "approval_expired"  # the same decision, only while unexpired
        elif refused is None and intent.effect_state != "not_dispatched":
            refused = "binding_refused"
        if refused is None and intent.expires_at_ms <= database_now_ms(db):
            refused = "approval_expired"
        if refused is not None:
            return self._record(
                db, execution, epoch, RecordPure(step_path, action.node_id, failure=refused)
            )
        return self._create_attempt(
            db,
            execution,
            epoch,
            CreateAttempt(
                action.occurrence_path,
                action.node_id,
                action.ordinal,
                {"intent_id": intent.id, "decision_id": action.decision_id},
            ),
        )

    def _qualified_absence(self, db, execution, intent, write):
        """The last read-back of `write` observed a qualified absence."""
        from ..models import ExecutionActionObservation, ExecutionAttempt
        from .budgets import READBACK_STRIDE

        low = write.ordinal * READBACK_STRIDE
        latest = db.execute(
            select(ExecutionActionObservation.result, ExecutionActionObservation.qualified)
            .join(ExecutionAttempt, ExecutionAttempt.id == ExecutionActionObservation.attempt_id)
            .where(
                ExecutionActionObservation.workspace_id == execution.workspace_id,
                ExecutionActionObservation.intent_id == intent.id,
                ExecutionActionObservation.source == "readback",
                ExecutionAttempt.ordinal > low,
                ExecutionAttempt.ordinal < low + READBACK_STRIDE,
            )
            .order_by(ExecutionAttempt.ordinal.desc())
            .limit(1)
        ).first()
        return latest is not None and latest.result == "effect_absent" and latest.qualified

    def _with_tasks(self, db, execution, state):
        """Each wait's human task, if any, with its recorded decision."""
        from ..models import ExecutionActionDecision, ExecutionHumanTask

        waits = {r.wait.id: path for path, r in state.occurrences.items() if r.wait is not None}
        if not waits:
            return state
        rows = list(
            db.scalars(
                _scoped(ExecutionHumanTask, execution).where(
                    ExecutionHumanTask.execution_id == execution.id,
                    ExecutionHumanTask.wait_id.in_(list(waits)),
                )
            )
        )
        if not rows:
            return state
        decisions = dict(
            db.execute(
                select(ExecutionActionDecision.task_id, ExecutionActionDecision.decision).where(
                    ExecutionActionDecision.workspace_id == execution.workspace_id,
                    ExecutionActionDecision.project_id == execution.project_id,
                    ExecutionActionDecision.environment_id == execution.environment_id,
                    ExecutionActionDecision.task_id.in_([r.id for r in rows]),
                )
            ).all()
        )
        occurrences = dict(state.occurrences)
        for row in rows:
            path = waits[row.wait_id]
            occurrences[path] = replace(
                occurrences[path],
                task=TaskRecord(row.id, row.kind, row.state, decisions.get(row.id)),
            )
        return replace(state, occurrences=occurrences)

    def _open_task(self, db, execution, epoch, action):
        from ..models import ExecutionActionIntent, ExecutionHumanTask

        if execution.interpreter_id != INTERPRETER_V2:
            raise ProgramError("integrity")
        program = load_program(db, _scope(execution), execution)
        ref = resolve_occurrence(program, action.occurrence_path)
        step = ref.step
        if not isinstance(step, HumanTaskStep) or step.id != action.node_id:
            raise ProgramError("integrity")
        deadline = action.deadline_ms
        intent_id = None
        if step.mode == "review":
            intent = db.scalar(
                _scoped(ExecutionActionIntent, execution).where(
                    ExecutionActionIntent.id == action.intent_id,
                    ExecutionActionIntent.execution_id == execution.id,
                )
            )
            if intent is None:
                raise ProgramError("integrity")
            intent_id = intent.id
            # The earliest of the step's timeout, the intent's expiry and the deadline.
            deadline = min(deadline, intent.expires_at_ms)
        if not self._open_wait(
            db, execution, epoch, OpenWait(action.occurrence_path, action.node_id, deadline)
        ):
            return False
        wait = db.scalar(
            _scoped(ExecutionWait, execution).where(
                ExecutionWait.wait_key == wait_key(action.occurrence_path)
            )
        )
        policy = program.document(ref.package, "policies", step.assignment_policy_ref)
        escalate = action.escalate_at_ms
        escalation = None
        if step.escalation_policy_ref is not None and escalate is not None and escalate < deadline:
            escalation = program.document(
                ref.package, "policies", step.escalation_policy_ref
            ).definition.assignment
        else:
            escalate = None
        db.add(
            ExecutionHumanTask(
                **_scope(execution).model_dump(),
                execution_id=execution.id,
                step_instance_id=wait.step_instance_id,
                wait_id=wait.id,
                kind=step.mode,
                intent_id=intent_id,
                assignment=policy.definition.assignment,
                escalation_assignment=escalation,
                escalate_at_ms=escalate,
                deadline_ms=deadline,
                state="open",
            )
        )
        db.flush()
        self._event(db, execution, epoch, "task_opened", path=action.occurrence_path)
        return True

    def _consume_decision(self, db, execution, epoch, action):
        # Every task, decision and intent row is locked here, as everywhere in E05,
        # only while the execution row is held, which serializes them all.
        from ..models import ExecutionActionDecision, ExecutionActionIntent, ExecutionHumanTask

        task = db.scalar(
            _scoped(ExecutionHumanTask, execution)
            .where(
                ExecutionHumanTask.id == action.task_id,
                ExecutionHumanTask.execution_id == execution.id,
            )
            .with_for_update()
        )
        if task is None or task.state != "decided" or task.kind != "review":
            raise ProgramError("integrity")
        # Only the decision bound to this task, for this task's intent.
        decision = db.scalar(
            _scoped(ExecutionActionDecision, execution).where(
                ExecutionActionDecision.task_id == task.id
            )
        )
        if decision is None or decision.intent_id != task.intent_id:
            raise ProgramError("integrity")
        intent = db.scalar(
            _scoped(ExecutionActionIntent, execution)
            .where(ExecutionActionIntent.id == task.intent_id)
            .with_for_update()
        )
        instance = self._instance(db, execution, action.occurrence_path, task_step_node(db, task))
        attempts = self._attempts(db, execution, instance)
        if attempts and attempts[-1].state in ("succeeded", "failed", "cancelled"):
            return False
        wait = db.scalar(
            _scoped(ExecutionWait, execution)
            .where(ExecutionWait.id == task.wait_id)
            .with_for_update()
        )
        if wait is not None and wait.state == "open":
            wait.state = "satisfied"
        node = instance.node_id
        now = database_now_ms(db)
        if intent is None or intent.authorization_state != "awaiting_review":
            # Superseded, withdrawn or already closed (say by recovery) between the
            # decision and now: the decision authorizes nothing.
            return self._record(
                db,
                execution,
                epoch,
                RecordPure(action.occurrence_path, node, failure="binding_refused"),
            )
        if intent.expires_at_ms <= now:
            intent.authorization_state = "expired"
            db.flush()
            return self._record(
                db,
                execution,
                epoch,
                RecordPure(action.occurrence_path, node, failure="approval_expired"),
            )
        if decision.decision == "allow":
            intent.authorization_state = "approved"
            outcome = RecordPure(
                action.occurrence_path,
                node,
                value={"handle_kind": "human_decision", "id": decision.id},
            )
        else:
            intent.authorization_state = "denied"
            outcome = RecordPure(action.occurrence_path, node, failure="approval_denied")
        db.flush()
        return self._record(db, execution, epoch, outcome)

    def _create_intent(self, db, execution, epoch, action):
        from .actions import BindingRefused, IntentService, intent_for, intent_handle

        if execution.interpreter_id != INTERPRETER_V2:
            raise ProgramError("integrity")
        instance = self._instance(db, execution, action.occurrence_path, action.node_id)
        attempts = self._attempts(db, execution, instance)
        if intent_for(db, execution, instance.id) is not None or (
            attempts and attempts[-1].state in ("succeeded", "failed", "cancelled")
        ):
            return False  # a repeated transition creates nothing
        program = load_program(db, _scope(execution), execution)
        ref = resolve_occurrence(program, action.occurrence_path)
        if not isinstance(ref.step, ActionIntentStep) or ref.step.id != action.node_id:
            raise ProgramError("integrity")
        service = IntentService(
            artifacts=self.artifacts,
            artifact_policy=self.policy,
            installation_id=self.installation_id,
            keys=self._keys(),
        )
        try:
            row, intent = service.create(
                db,
                execution,
                instance,
                ref.step,
                program,
                ref.package,
                action.target,
                action.input,
                epoch=epoch,
            )
        except BindingRefused:
            return self._record(
                db,
                execution,
                epoch,
                RecordPure(action.occurrence_path, action.node_id, failure="binding_refused"),
            )
        self._record(
            db,
            execution,
            epoch,
            RecordPure(action.occurrence_path, action.node_id, value=intent_handle(row.id)),
        )
        attempt = self._attempts(db, execution, instance)[-1]
        service.record_created(db, execution, epoch, intent, attempt.id)
        return True


def task_step_node(db, task):
    """The authored step ID of a task's step instance."""
    return db.get(ExecutionStepInstance, task.step_instance_id).node_id
