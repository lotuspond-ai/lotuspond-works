"""Pure bounded event checks over caller-supplied, unauthenticated snapshots.

No inbox, authority, clock, provider read-back or execution is implemented here.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeVar

from pydantic import Field

from ..contract import Identifier, ReferenceRevision
from ._validation import StrictModel, report_bytes, validate_model
from .actions import (
    ActionIntent,
    BindingCode,
    BindingContext,
    HumanDecision,
    NonnegativeInt,
    OpaqueId,
    check_command_binding,
    check_decision,
    decision_digest,
    intent_digest,
    validate_action_intent,
    validate_binding_context,
    validate_human_decision,
)
from .errors import ExecutableError
from .input import WIRE_LIMITS
from .package import Digest, RetryPolicy
from .wire import (
    ACTION_EVENT_KINDS,
    WIRE_MODELS,
    ActionReceipt,
    RuntimeEvent,
    WaitStartedPayload,
    canonical_event_payload_json,
    validate_action_receipt,
    validate_runtime_event,
    wire_digest,
)


class ContractState(StrictModel):
    phase: Literal[
        "new", "running", "waiting", "reconciliation_required", "cancelled", "failed", "completed"
    ]
    action_state: Literal["not_dispatched", "dispatching", "accepted", "observed", "unknown"]
    outcome: Literal["unknown", "not_verified", "verified"]


class KnownEvent(StrictModel):
    event_id: OpaqueId
    sha256: Digest


EffectCount = Annotated[int, Field(ge=0, le=10000)]


class CompletionSummary(StrictModel):
    required_effects: EffectCount
    verified_effects: EffectCount
    not_verified_effects: EffectCount
    unknown_effects: EffectCount
    unfinished_steps: EffectCount


class EventContext(StrictModel):
    installation_id: OpaqueId
    workspace_id: OpaqueId
    project_id: OpaqueId
    environment_id: OpaqueId
    execution_id: OpaqueId
    recovery_epoch: NonnegativeInt
    source_id: OpaqueId
    source_generation: NonnegativeInt
    now_ms: NonnegativeInt
    prior_source_sequence: NonnegativeInt
    package_sha256: Digest
    configuration_sha256: Digest
    plan_sha256: Digest
    active_step: bool
    step_id: Identifier | None = None
    action_id: OpaqueId | None = None
    attempt: NonnegativeInt | None = None
    known_event: KnownEvent | None = None
    binding_context: BindingContext | None = None
    intent: ActionIntent | None = None
    decision: HumanDecision | None = None
    receipt: ActionReceipt | None = None
    wait: WaitStartedPayload | None = None
    retry_policy: RetryPolicy | None = None
    completion: CompletionSummary | None = None
    target_operation_ref: ReferenceRevision | None = None
    reconciliation_rule_id: Identifier | None = None
    reconciliation_rule_version: ReferenceRevision | None = None
    verification_rule_id: Identifier | None = None
    verification_rule_version: ReferenceRevision | None = None


TransitionCode = Literal[
    "applied",
    "duplicate",
    "replay_conflict",
    "sequence_gap",
    "stale_sequence",
    "context_mismatch",
    "illegal_transition",
    "wait_mismatch",
    "wait_not_ready",
    "wait_expired",
    "binding_refused",
    "reconciliation_required",
    "evidence_missing",
    "verification_missing",
    "completion_incomplete",
    "terminal_state",
    "retry_exhausted",
]


class TransitionReport(StrictModel):
    disposition: Literal["applied", "duplicate", "refused", "reconciliation_required"]
    code: TransitionCode
    state: ContractState
    accepted_source_sequence: NonnegativeInt
    structurally_retryable: bool
    binding_code: BindingCode | None = None
    assurance: Literal["synthetic-contract-evaluation"] = "synthetic-contract-evaluation"
    authority_granted: Literal[False] = False


TRANSITION_MODELS = (
    ContractState,
    KnownEvent,
    CompletionSummary,
    EventContext,
    TransitionReport,
    RetryPolicy,
    *WIRE_MODELS,
)
Model = TypeVar("Model", bound=StrictModel)
_SCOPE = ("installation_id", "workspace_id", "project_id", "environment_id", "execution_id")
_HASHES = ("package_sha256", "configuration_sha256", "plan_sha256")
_TERMINAL = ("cancelled", "failed", "completed")


def _admit(raw: object, model: type[Model]) -> Model:
    try:
        return validate_model(
            raw,
            model_type=model,
            model_types=TRANSITION_MODELS,
            code="invalid_context",
            limits=WIRE_LIMITS,
        )
    except ExecutableError as error:
        if error.code == "invalid_json_value":
            raise ExecutableError("invalid_context") from None
        raise


def validate_contract_state(raw: object) -> ContractState:
    result = _admit(raw, ContractState)
    if result.phase == "new" and (
        result.action_state != "not_dispatched" or result.outcome != "unknown"
    ):
        raise ExecutableError("invalid_context")
    if result.phase == "completed" and (
        result.action_state in ("dispatching", "unknown")
        or result.outcome == "not_verified"
        or (result.action_state in ("accepted", "observed") and result.outcome != "verified")
    ):
        raise ExecutableError("invalid_context")
    if result.outcome == "verified" and result.action_state in ("dispatching", "unknown"):
        raise ExecutableError("invalid_context")
    return result


def validate_known_event(raw: object) -> KnownEvent:
    return _admit(raw, KnownEvent)


def validate_completion_summary(raw: object) -> CompletionSummary:
    result = _admit(raw, CompletionSummary)
    if result.required_effects != (
        result.verified_effects + result.not_verified_effects + result.unknown_effects
    ):
        raise ExecutableError("invalid_context")
    return result


def validate_event_context(raw: object) -> EventContext:
    result = _admit(raw, EventContext)
    if result.binding_context is not None:
        binding = validate_binding_context(result.binding_context)
        if any(
            getattr(binding, key) != getattr(result, key)
            for key in (*_SCOPE, *_HASHES, "recovery_epoch", "now_ms", "step_id", "action_id")
        ):
            raise ExecutableError("invalid_context")
    if result.intent is not None:
        validate_action_intent(result.intent)
    if result.decision is not None:
        validate_human_decision(result.decision)
    if result.receipt is not None:
        validate_action_receipt(result.receipt)
    if result.wait is not None:
        canonical_event_payload_json("wait_started", result.wait)
    if result.completion is not None:
        validate_completion_summary(result.completion)
    if result.retry_policy is not None:
        codes = result.retry_policy.retry_codes
        if len(codes) != len(set(codes)):
            raise ExecutableError("invalid_context")
    for id_field, version_field in (
        (result.reconciliation_rule_id, result.reconciliation_rule_version),
        (result.verification_rule_id, result.verification_rule_version),
    ):
        if (id_field is None) != (version_field is None):
            raise ExecutableError("invalid_context")
    return result


def validate_transition_report(raw: object) -> TransitionReport:
    result = _admit(raw, TransitionReport)
    validate_contract_state(result.state)
    expected = (
        "applied"
        if result.code == "applied"
        else "duplicate"
        if result.code == "duplicate"
        else "reconciliation_required"
        if result.code in ("sequence_gap", "reconciliation_required")
        else "refused"
    )
    if (
        result.disposition != expected
        or (
            result.binding_code is not None
            and (result.code != "binding_refused" or result.binding_code == "binding_valid")
        )
        or (
            result.structurally_retryable
            and (
                result.disposition != "applied"
                or result.state.phase != "running"
                or result.state.action_state != "not_dispatched"
                or result.state.outcome != "unknown"
            )
        )
    ):
        raise ExecutableError("invalid_context")
    return result


def transition_report_json(raw: object) -> bytes:
    """Revalidate and emit deterministic, bounded ASCII JSON plus newline."""
    return report_bytes(
        validate_transition_report(raw),
        model_type=TransitionReport,
        model_types=TRANSITION_MODELS,
        code="invalid_context",
    )


def event_digest(raw: object) -> str:
    """Full admitted event identity, including header, sequence and observation time."""
    return wire_digest(validate_runtime_event(raw))


def _changed(state: ContractState, **changes: object) -> ContractState:
    return validate_contract_state(state.model_copy(update=changes))


def _result(
    state: ContractState,
    event: RuntimeEvent,
    context: EventContext,
    code: TransitionCode,
    *,
    next_state: ContractState | None = None,
    retry: bool = False,
    binding_code: BindingCode | None = None,
) -> TransitionReport:
    disposition = (
        "applied"
        if code == "applied"
        else "duplicate"
        if code == "duplicate"
        else "reconciliation_required"
        if code in ("sequence_gap", "reconciliation_required")
        else "refused"
    )
    return validate_transition_report(
        TransitionReport(
            disposition=disposition,
            code=code,
            state=state if next_state is None else next_state,
            accepted_source_sequence=(
                event.source_sequence if disposition == "applied" else context.prior_source_sequence
            ),
            structurally_retryable=retry,
            binding_code=binding_code,
        )
    )


def _uncertain(state: ContractState) -> ContractState:
    # Preserve positive evidence, including verification, even on contradictions.
    return _changed(
        state,
        phase="reconciliation_required",
        action_state=state.action_state
        if state.action_state in ("accepted", "observed") or state.outcome == "verified"
        else "unknown",
    )


def _intent_code(context: EventContext, *, current: bool) -> BindingCode | None:
    intent, expected = context.intent, context.binding_context
    if intent is None or expected is None:
        return "context_mismatch"
    binding = intent.binding
    if any(
        getattr(binding, key) != getattr(expected, key)
        for key in (*_SCOPE, *_HASHES, "step_id", "action_id", "policy_sha256", "requester_id")
    ):
        return "context_mismatch"
    if binding.recovery_epoch != expected.recovery_epoch:
        return "stale_epoch"
    if current:
        if context.now_ms < binding.issued_at_ms:
            return "not_yet_valid"
        if context.now_ms >= binding.expires_at_ms:
            return "expired"
    return None


def _retry_available(context: EventContext, code: str | None, *, action: bool = True) -> bool:
    policy = context.retry_policy
    return (
        policy is not None
        and context.attempt is not None
        and context.attempt + 1 < policy.max_attempts
        and (code is None or code in policy.retry_codes)
        and ((not action and context.intent is None) or _intent_code(context, current=True) is None)
    )


def _receipt_matches(receipt: ActionReceipt, context: EventContext) -> bool:
    return (
        context.intent is not None
        and receipt.intent_sha256 == intent_digest(context.intent)
        and receipt.action_id == context.action_id
        and receipt.attempt == context.attempt
        and context.target_operation_ref is not None
        and receipt.target_operation_ref == context.target_operation_ref
    )


def check_event_transition(state: object, event: object, context: object) -> TransitionReport:
    """Check supplied declarations in fence/replay/sequence/current-state order.

    Attempts are zero-based. Only applied events advance the source sequence.
    All context records are admitted even when the selected event does not use them.
    """
    s = validate_contract_state(state)
    e = validate_runtime_event(event)
    c = validate_event_context(context)
    p, kind = e.payload, e.event_kind

    def result(code, **kwargs):
        return _result(s, e, c, code, **kwargs)

    if any(
        getattr(e, key) != getattr(c, key)
        for key in (*_SCOPE, "recovery_epoch", "source_id", "source_generation")
    ):
        return result("context_mismatch")
    if c.known_event is not None:
        if c.known_event.event_id != e.event_id:
            return result("context_mismatch")
        return result("duplicate" if c.known_event.sha256 == event_digest(e) else "replay_conflict")
    if e.source_sequence > c.prior_source_sequence + 1:
        # Fencing and replay precede this; terminal states cannot be resurrected.
        return result(
            "sequence_gap",
            next_state=(
                s if s.phase in _TERMINAL else _changed(s, phase="reconciliation_required")
            ),
        )
    if e.source_sequence <= c.prior_source_sequence:
        return result("stale_sequence")
    if e.step_id is not None and (
        e.step_id != c.step_id or e.attempt != c.attempt or not c.active_step
    ):
        return result("context_mismatch" if c.active_step else "illegal_transition")
    if e.action_id is not None and e.action_id != c.action_id:
        return result("context_mismatch")
    if s.phase in _TERMINAL:
        return result("terminal_state")
    if kind in ("cancellation_requested", "execution_failed"):
        return result(
            "applied",
            next_state=_changed(
                s, phase="cancelled" if kind == "cancellation_requested" else "failed"
            ),
        )
    # A supplied receipt is a fact about the relevant current action, not an
    # unrelated historical record. Check it after replay/sequence/stop guards,
    # before any fresh progression can claim no dispatch or complete execution.
    if c.receipt is not None:
        code = _intent_code(c, current=False)
        if code is not None:
            return result("binding_refused", binding_code=code)
        if not _receipt_matches(c.receipt, c):
            return result("binding_refused", binding_code="intent_mismatch")
        if c.receipt.status == "observed_effect" and s.action_state == "not_dispatched":
            return result("illegal_transition")
    if kind == "admission":
        if s.phase != "new":
            return result("illegal_transition")
        if any(getattr(p, key) != getattr(c, key) for key in _HASHES):
            return result("context_mismatch")
        return result("applied", next_state=_changed(s, phase="running"))
    if kind in ("signal_received", "timer_elapsed", "wait_started"):
        expected_phase = "running" if kind == "wait_started" else "waiting"
        if s.phase != expected_phase:
            return result("illegal_transition")
        wait = c.wait
        if wait is None or p.wait_id != wait.wait_id:
            return result("wait_mismatch")
        if kind == "wait_started":
            if canonical_event_payload_json(kind, p) != canonical_event_payload_json(kind, wait):
                return result("wait_mismatch")
        elif kind == "signal_received":
            if wait.kind == "timer" or p.correlation_sha256 != wait.correlation_sha256:
                return result("wait_mismatch")
        elif wait.kind != "timer":
            return result("wait_mismatch")
        if c.now_ms >= wait.expires_at_ms:
            return result("wait_expired")
        if kind != "wait_started" and c.now_ms < wait.not_before_ms:
            return result("wait_not_ready")
        return result(
            "applied",
            next_state=_changed(s, phase="waiting" if kind == "wait_started" else "running"),
        )
    if kind in ("step_started", "step_result", "step_failed"):
        if s.phase != "running":
            return result("illegal_transition")
        if kind == "step_started" and (
            c.retry_policy is None or e.attempt >= c.retry_policy.max_attempts
        ):
            return result("retry_exhausted")
        # Result is a step declaration, never execution completion or effect verification.
        return result(
            "applied",
            retry=(
                kind == "step_failed"
                and s.action_state == "not_dispatched"
                and s.outcome == "unknown"
                and _retry_available(c, p.code, action=False)
            ),
        )
    if kind == "execution_completed":
        if s.phase != "running" or s.action_state in ("unknown", "dispatching"):
            return result("illegal_transition")
        summary = c.completion
        if summary is None or summary.unfinished_steps:
            return result("completion_incomplete")
        if (
            summary.unknown_effects
            or summary.not_verified_effects
            or s.outcome == "not_verified"
            or (s.outcome == "verified" and summary.required_effects == 0)
        ):
            return result("verification_missing")
        if s.action_state in ("accepted", "observed") and (
            s.outcome != "verified" or summary.required_effects == 0
        ):
            return result("verification_missing")
        return result(
            "applied",
            next_state=_changed(
                s, phase="completed", outcome="verified" if summary.required_effects else "unknown"
            ),
        )
    if kind in ACTION_EVENT_KINDS:
        if s.phase not in ("running", "reconciliation_required"):
            return result("illegal_transition")
        if kind in ("intent_created", "decision_recorded", "action_dispatched") and (
            s.phase != "running" or s.action_state != "not_dispatched" or s.outcome != "unknown"
        ):
            return result("illegal_transition")
        code = _intent_code(c, current=kind == "intent_created")
        if code is not None:
            return result("binding_refused", binding_code=code)
        if kind == "intent_created":
            if intent_digest(p.intent) != intent_digest(c.intent):
                return result("binding_refused", binding_code="intent_mismatch")
            return result("applied")
        if kind == "decision_recorded":
            if c.decision is not None and decision_digest(c.decision) != decision_digest(
                p.decision
            ):
                return result("binding_refused", binding_code="intent_mismatch")
            binding = check_decision(c.intent, p.decision, c.binding_context)
            if not binding.valid:
                return result("binding_refused", binding_code=binding.code)
            return result("applied")
        if kind == "action_dispatched":
            if c.retry_policy is None or c.attempt >= c.retry_policy.max_attempts:
                return result("retry_exhausted")
            binding = check_command_binding(p.command, c.intent, c.decision, c.binding_context)
            if not binding.valid:
                return result("binding_refused", binding_code=binding.code)
            return result(
                "applied", next_state=_changed(s, action_state="dispatching", outcome="unknown")
            )
        if kind == "action_receipt":
            if not _receipt_matches(p.receipt, c) or (
                c.receipt is not None and wire_digest(c.receipt) != wire_digest(p.receipt)
            ):
                return result("binding_refused", binding_code="intent_mismatch")
            if s.action_state == "not_dispatched":
                return result("illegal_transition")
            if p.receipt.status == "unknown":
                return result("reconciliation_required", next_state=_uncertain(s))
            if p.receipt.status == "observed_effect" and not p.receipt.evidence_refs:
                return result("evidence_missing")
            return result(
                "applied",
                next_state=_changed(
                    s,
                    action_state="observed"
                    if p.receipt.status == "observed_effect" or s.action_state == "observed"
                    else "accepted",
                ),
            )
        if kind == "dispatch_timeout":
            if p.intent_sha256 != intent_digest(c.intent):
                return result("binding_refused", binding_code="intent_mismatch")
            if p.may_have_dispatched or s.action_state != "not_dispatched":
                return result("reconciliation_required", next_state=_uncertain(s))
            if s.phase != "running":
                return result("illegal_transition")
            return result(
                "applied",
                retry=(s.outcome == "unknown" and _retry_available(c, "timeout_before_dispatch")),
            )
        if kind == "reconciliation_observed":
            if p.intent_sha256 != intent_digest(c.intent) or (
                p.rule_id != c.reconciliation_rule_id
                or p.rule_version != c.reconciliation_rule_version
            ):
                return result("binding_refused", binding_code="intent_mismatch")
            if (
                c.target_operation_ref is None
                or (s.action_state == "accepted" and c.receipt is None)
                or (c.receipt is not None and not _receipt_matches(c.receipt, c))
            ):
                return result("binding_refused", binding_code="context_mismatch")
            if s.action_state == "not_dispatched":
                return result("illegal_transition")
            if p.observation == "unknown":
                return result("reconciliation_required", next_state=_uncertain(s))
            if not p.evidence_refs:
                return result("evidence_missing")
            if p.observation == "effect_present":
                return result(
                    "applied", next_state=_changed(s, phase="running", action_state="observed")
                )
            if (
                not p.qualified_absence
                or s.action_state == "observed"
                or s.outcome == "verified"
                or (c.receipt is not None and c.receipt.status == "observed_effect")
            ):
                return result("reconciliation_required", next_state=_uncertain(s))
            return result(
                "applied",
                next_state=_changed(
                    s, phase="running", action_state="not_dispatched", outcome="unknown"
                ),
                retry=_retry_available(c, None),
            )
        if kind == "outcome_recorded":
            outcome = p.outcome
            if (
                c.receipt is None
                or not _receipt_matches(c.receipt, c)
                or (
                    outcome.intent_sha256 != intent_digest(c.intent)
                    or outcome.receipt_id != c.receipt.receipt_id
                    or outcome.rule_id != c.verification_rule_id
                    or outcome.rule_version != c.verification_rule_version
                    or outcome.observed_at_ms > c.now_ms
                )
            ):
                return result("binding_refused", binding_code="intent_mismatch")
            if s.action_state == "not_dispatched":
                return result("illegal_transition")
            if s.outcome == "verified" and outcome.state != "verified":
                return result("reconciliation_required", next_state=_uncertain(s))
            if outcome.state == "verified":
                return result(
                    "applied",
                    next_state=_changed(
                        s, phase="running", action_state="observed", outcome="verified"
                    ),
                )
            if outcome.state == "unknown":
                return result("reconciliation_required", next_state=_uncertain(s))
            return result("applied", next_state=_changed(s, outcome="not_verified"))
    return result("illegal_transition")
