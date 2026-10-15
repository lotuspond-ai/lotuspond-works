"""Bounded runtime wire declarations with no transport, authentication or execution."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..contract import Identifier, ReferenceRevision
from ._canonical import sha256_hex
from ._validation import StrictModel
from .actions import (
    ACTION_MODELS,
    COMMAND_LIFETIME_MS,
    ActionBinding,
    ActionIntent,
    HumanDecision,
    NonnegativeInt,
    OpaqueId,
    Record,
    WireRecord,
    _action_domain,
    _admit,
    _bytes,
    _interval,
)
from .errors import ExecutableError
from .input import WIRE_LIMITS, project_json
from .package import Digest

FailureCode = Literal[
    "unavailable",
    "rate_limited",
    "timeout_before_dispatch",
    "invalid_output",
    "cancelled",
    "unknown",
]


class EvidenceRef(StrictModel):
    artifact_id: OpaqueId
    sha256: Digest
    media_type: Annotated[str, Field(min_length=1, max_length=128)]
    classification: Literal["public", "internal", "confidential", "restricted"]
    source_kind: Literal["target_readback", "signed_receipt", "deterministic_check"]


class RuntimeCommand(WireRecord):
    record_type: Literal["runtime_command"] = "runtime_command"
    command_id: OpaqueId
    replay_id: OpaqueId
    intent_sha256: Digest
    decision_sha256: Digest | None = None
    installation_id: OpaqueId
    recovery_epoch: NonnegativeInt
    workspace_id: OpaqueId
    project_id: OpaqueId
    environment_id: OpaqueId
    execution_id: OpaqueId
    action_id: OpaqueId
    worker_id: OpaqueId
    fencing_generation: NonnegativeInt
    issued_at_ms: NonnegativeInt
    expires_at_ms: NonnegativeInt


class ActionReceipt(WireRecord):
    record_type: Literal["action_receipt"] = "action_receipt"
    receipt_id: OpaqueId
    action_id: OpaqueId
    intent_sha256: Digest
    attempt: NonnegativeInt
    target_operation_ref: ReferenceRevision
    status: Literal["accepted", "observed_effect", "unknown"]
    evidence_refs: list[EvidenceRef] = Field(max_length=64)


class OutcomeRecord(WireRecord):
    record_type: Literal["outcome_record"] = "outcome_record"
    outcome_id: OpaqueId
    action_id: OpaqueId
    intent_sha256: Digest
    receipt_id: OpaqueId
    state: Literal["verified", "not_verified", "unknown"]
    rule_id: Identifier
    rule_version: ReferenceRevision
    evidence_refs: list[EvidenceRef] = Field(max_length=64)
    observed_at_ms: NonnegativeInt


class AdmissionPayload(StrictModel):
    package_sha256: Digest
    configuration_sha256: Digest
    plan_sha256: Digest
    recovery_epoch: NonnegativeInt


class StepStartedPayload(StrictModel):
    input_sha256: Digest


class StepResultPayload(StrictModel):
    output_sha256: Digest


class StepFailedPayload(StrictModel):
    code: FailureCode


class WaitStartedPayload(StrictModel):
    wait_id: OpaqueId
    kind: Literal["timer", "external", "human"]
    correlation_sha256: Digest
    not_before_ms: NonnegativeInt
    expires_at_ms: NonnegativeInt


class SignalReceivedPayload(StrictModel):
    wait_id: OpaqueId
    correlation_sha256: Digest
    signal_sha256: Digest


class TimerElapsedPayload(StrictModel):
    wait_id: OpaqueId


class IntentCreatedPayload(StrictModel):
    intent: ActionIntent


class DecisionRecordedPayload(StrictModel):
    decision: HumanDecision


class ActionDispatchedPayload(StrictModel):
    command: RuntimeCommand


class ActionReceiptPayload(StrictModel):
    receipt: ActionReceipt


class DispatchTimeoutPayload(StrictModel):
    intent_sha256: Digest
    may_have_dispatched: bool


class ReconciliationObservedPayload(StrictModel):
    intent_sha256: Digest
    observation: Literal["effect_present", "effect_absent", "unknown"]
    rule_id: Identifier
    rule_version: ReferenceRevision
    evidence_refs: list[EvidenceRef] = Field(max_length=64)
    qualified_absence: bool


class OutcomeRecordedPayload(StrictModel):
    outcome: OutcomeRecord


class ExecutionCompletedPayload(StrictModel):
    output_sha256: Digest


class CancellationRequestedPayload(StrictModel):
    reason_code: Literal["owner_requested", "policy_revoked", "recovery_fence"]


class ExecutionFailedPayload(StrictModel):
    code: FailureCode


EventKind = Literal[
    "admission",
    "step_started",
    "step_result",
    "step_failed",
    "wait_started",
    "signal_received",
    "timer_elapsed",
    "intent_created",
    "decision_recorded",
    "action_dispatched",
    "action_receipt",
    "dispatch_timeout",
    "reconciliation_observed",
    "outcome_recorded",
    "execution_completed",
    "cancellation_requested",
    "execution_failed",
]
EventPayload = (
    AdmissionPayload
    | StepStartedPayload
    | StepResultPayload
    | StepFailedPayload
    | WaitStartedPayload
    | SignalReceivedPayload
    | TimerElapsedPayload
    | IntentCreatedPayload
    | DecisionRecordedPayload
    | ActionDispatchedPayload
    | ActionReceiptPayload
    | DispatchTimeoutPayload
    | ReconciliationObservedPayload
    | OutcomeRecordedPayload
    | ExecutionCompletedPayload
    | CancellationRequestedPayload
    | ExecutionFailedPayload
)
EVENT_PAYLOAD_MODELS = {
    "admission": AdmissionPayload,
    "step_started": StepStartedPayload,
    "step_result": StepResultPayload,
    "step_failed": StepFailedPayload,
    "wait_started": WaitStartedPayload,
    "signal_received": SignalReceivedPayload,
    "timer_elapsed": TimerElapsedPayload,
    "intent_created": IntentCreatedPayload,
    "decision_recorded": DecisionRecordedPayload,
    "action_dispatched": ActionDispatchedPayload,
    "action_receipt": ActionReceiptPayload,
    "dispatch_timeout": DispatchTimeoutPayload,
    "reconciliation_observed": ReconciliationObservedPayload,
    "outcome_recorded": OutcomeRecordedPayload,
    "execution_completed": ExecutionCompletedPayload,
    "cancellation_requested": CancellationRequestedPayload,
    "execution_failed": ExecutionFailedPayload,
}
STEP_EVENT_KINDS = frozenset(
    {
        "step_started",
        "step_result",
        "step_failed",
        "wait_started",
        "signal_received",
        "timer_elapsed",
    }
)
ACTION_EVENT_KINDS = frozenset(
    {
        "intent_created",
        "decision_recorded",
        "action_dispatched",
        "action_receipt",
        "dispatch_timeout",
        "reconciliation_observed",
        "outcome_recorded",
    }
)


class RuntimeEvent(WireRecord):
    record_type: Literal["runtime_event"] = "runtime_event"
    event_id: OpaqueId
    installation_id: OpaqueId
    recovery_epoch: NonnegativeInt
    workspace_id: OpaqueId
    project_id: OpaqueId
    environment_id: OpaqueId
    execution_id: OpaqueId
    step_id: Identifier | None = None
    action_id: OpaqueId | None = None
    attempt: NonnegativeInt | None = None
    source_id: OpaqueId
    source_generation: NonnegativeInt
    source_sequence: NonnegativeInt
    event_kind: EventKind
    observed_at_ms: NonnegativeInt
    payload: EventPayload
    payload_sha256: Digest

    @model_validator(mode="before")
    @classmethod
    def select_payload(cls, raw: object) -> object:
        if type(raw) is dict:
            kind = raw.get("event_kind")
            if type(kind) is str and kind in EVENT_PAYLOAD_MODELS:
                raw = dict(raw)
                raw["payload"] = EVENT_PAYLOAD_MODELS[kind].model_validate(raw.get("payload"))
        return raw


WIRE_RECORD_MODELS = (
    ActionBinding,
    ActionIntent,
    HumanDecision,
    RuntimeCommand,
    RuntimeEvent,
    ActionReceipt,
    OutcomeRecord,
)
WIRE_MODELS = (
    *ACTION_MODELS,
    EvidenceRef,
    RuntimeCommand,
    RuntimeEvent,
    ActionReceipt,
    OutcomeRecord,
    *EVENT_PAYLOAD_MODELS.values(),
)
_RECORD_TYPES = {model.model_fields["record_type"].default: model for model in WIRE_RECORD_MODELS}


def _wire_domain(record: StrictModel) -> None:
    _action_domain(record)
    if type(record) is RuntimeCommand:
        _interval(record.issued_at_ms, record.expires_at_ms, COMMAND_LIFETIME_MS)
    elif type(record) is OutcomeRecord:
        if record.state == "verified" and not record.evidence_refs:
            raise ExecutableError("invalid_wire")
    elif type(record) is RuntimeEvent:
        _event_domain(record)


def _event_domain(event: RuntimeEvent) -> None:
    kind = event.event_kind
    payload = event.payload
    if type(payload) is not EVENT_PAYLOAD_MODELS[kind]:
        raise ExecutableError("invalid_wire")
    step = kind in STEP_EVENT_KINDS or kind in ACTION_EVENT_KINDS
    action = kind in ACTION_EVENT_KINDS
    if (
        (event.step_id is not None) != step
        or (event.attempt is not None) != step
        or (event.action_id is not None) != action
    ):
        raise ExecutableError("invalid_wire")
    nested = None
    if kind == "admission" and payload.recovery_epoch != event.recovery_epoch:
        raise ExecutableError("invalid_wire")
    _payload_domain(payload)
    if kind == "intent_created":
        nested = payload.intent.binding
    elif kind == "action_dispatched":
        nested = payload.command
    elif kind == "action_receipt":
        nested = payload.receipt
    elif kind == "outcome_recorded":
        nested = payload.outcome
    if nested is not None:
        for name in (
            "installation_id",
            "recovery_epoch",
            "workspace_id",
            "project_id",
            "environment_id",
            "execution_id",
            "step_id",
            "action_id",
            "attempt",
        ):
            if hasattr(nested, name) and getattr(nested, name) != getattr(event, name):
                raise ExecutableError("invalid_wire")
    if event.payload_sha256 != sha256_hex(_bytes(payload, WIRE_MODELS)):
        raise ExecutableError("invalid_wire")


def _validate(raw: object, model: type[Record]) -> Record:
    result = _admit(raw, model, WIRE_MODELS)
    _wire_domain(result)
    return result


def validate_wire(raw: object) -> WireRecord:
    """Admit a complete tagged record; recheck nested intent and payload digests."""
    try:
        projected = project_json(raw, model_types=WIRE_MODELS, limits=WIRE_LIMITS)
    except ExecutableError as error:
        if error.code == "invalid_json_value":
            raise ExecutableError("invalid_wire") from None
        raise
    if type(projected) is not dict or type(projected.get("record_type")) is not str:
        raise ExecutableError("invalid_wire")
    model = _RECORD_TYPES.get(projected["record_type"])
    if model is None:
        raise ExecutableError("invalid_wire")
    return _validate(projected, model)


def validate_runtime_command(raw: object) -> RuntimeCommand:
    return _validate(raw, RuntimeCommand)


def validate_runtime_event(raw: object) -> RuntimeEvent:
    return _validate(raw, RuntimeEvent)


def validate_action_receipt(raw: object) -> ActionReceipt:
    return _validate(raw, ActionReceipt)


def validate_outcome_record(raw: object) -> OutcomeRecord:
    return _validate(raw, OutcomeRecord)


def canonical_wire_json(raw: object) -> bytes:
    """Full normalized record JCS, after complete admission; at most 65,536 bytes."""
    return _bytes(validate_wire(raw), WIRE_MODELS)


def wire_digest(raw: object) -> str:
    """Hash the complete admitted record; distinct from the intent's binding digest."""
    return sha256_hex(canonical_wire_json(raw))


def _payload_domain(payload: StrictModel) -> None:
    if type(payload) is WaitStartedPayload and payload.not_before_ms >= payload.expires_at_ms:
        raise ExecutableError("invalid_wire")
    if type(payload) is ReconciliationObservedPayload and payload.qualified_absence:
        if payload.observation != "effect_absent" or not payload.evidence_refs:
            raise ExecutableError("invalid_wire")
    for wrapper, field in (
        (IntentCreatedPayload, "intent"),
        (DecisionRecordedPayload, "decision"),
        (ActionDispatchedPayload, "command"),
        (ActionReceiptPayload, "receipt"),
        (OutcomeRecordedPayload, "outcome"),
    ):
        if type(payload) is wrapper:
            _wire_domain(getattr(payload, field))


def canonical_event_payload_json(event_kind: object, payload: object) -> bytes:
    """Admit a kind-specific payload, including nested record/digest validation.

    Outer event references are checked only by complete event admission.
    """
    if type(event_kind) is not str or event_kind not in EVENT_PAYLOAD_MODELS:
        raise ExecutableError("invalid_wire")
    result = _admit(payload, EVENT_PAYLOAD_MODELS[event_kind], WIRE_MODELS)
    _payload_domain(result)
    return _bytes(result, WIRE_MODELS)


def event_payload_digest(event_kind: object, payload: object) -> str:
    return sha256_hex(canonical_event_payload_json(event_kind, payload))
