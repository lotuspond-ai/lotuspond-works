"""Pure action identity and supplied-context consistency; never authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, TypeVar

from pydantic import Field

from ..contract import Identifier, Version
from ._canonical import jcs_bytes, sha256_hex
from ._validation import StrictModel, report_bytes, validate_model
from .errors import ErrorCode, ExecutableError
from .input import WIRE_LIMITS, project_json
from .package import Digest

if TYPE_CHECKING:
    from .wire import RuntimeCommand

Record = TypeVar("Record", bound=StrictModel)

OpaqueId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
]
NonnegativeInt = Annotated[int, Field(ge=0, le=9007199254740991)]
INTENT_LIFETIME_MS = 2592000000
COMMAND_LIFETIME_MS = 300000


class WireRecord(StrictModel):
    format_version: Literal[1] = 1


class ActionBinding(WireRecord):
    record_type: Literal["action_binding"] = "action_binding"
    installation_id: OpaqueId
    recovery_epoch: NonnegativeInt
    workspace_id: OpaqueId
    project_id: OpaqueId
    environment_id: OpaqueId
    execution_id: OpaqueId
    step_id: Identifier
    action_id: OpaqueId
    package_sha256: Digest
    configuration_sha256: Digest
    plan_sha256: Digest
    connector_name: Identifier
    connector_version: Version
    connector_artifact_sha256: Digest
    operation: Identifier
    target_sha256: Digest
    input_sha256: Digest
    policy_sha256: Digest
    approval_mode: Literal["none", "human"]
    independent_approval: bool
    requester_id: OpaqueId
    issued_at_ms: NonnegativeInt
    expires_at_ms: NonnegativeInt


class ActionIntent(WireRecord):
    record_type: Literal["action_intent"] = "action_intent"
    binding: ActionBinding
    sha256: Digest


class HumanDecision(WireRecord):
    record_type: Literal["human_decision"] = "human_decision"
    decision_id: OpaqueId
    intent_sha256: Digest
    decision: Literal["allow", "deny"]
    actor_id: OpaqueId
    actor_kind: Literal["human", "service", "worker", "model"]
    policy_sha256: Digest
    issued_at_ms: NonnegativeInt
    expires_at_ms: NonnegativeInt


class BindingContext(StrictModel):
    installation_id: OpaqueId
    recovery_epoch: NonnegativeInt
    workspace_id: OpaqueId
    project_id: OpaqueId
    environment_id: OpaqueId
    execution_id: OpaqueId
    step_id: Identifier
    action_id: OpaqueId
    package_sha256: Digest
    configuration_sha256: Digest
    plan_sha256: Digest
    policy_sha256: Digest
    worker_id: OpaqueId
    fencing_generation: NonnegativeInt
    requester_id: OpaqueId
    now_ms: NonnegativeInt


BindingCode = Literal[
    "binding_valid",
    "context_mismatch",
    "stale_epoch",
    "stale_generation",
    "wrong_recipient",
    "not_yet_valid",
    "expired",
    "intent_mismatch",
    "approval_required",
    "approval_unexpected",
    "approval_denied",
    "invalid_approver",
    "independence_required",
]


class BindingReport(StrictModel):
    valid: bool
    code: BindingCode
    signature_status: Literal["not_checked"] = "not_checked"
    assurance: Literal["synthetic-contract-evaluation"] = "synthetic-contract-evaluation"
    authority_granted: Literal[False] = False


ACTION_MODELS = (ActionBinding, ActionIntent, HumanDecision, BindingContext, BindingReport)


def _normalized(raw: StrictModel, models: tuple) -> object:
    return project_json(raw, model_types=models, limits=WIRE_LIMITS)


def _bytes(raw: StrictModel, models: tuple) -> bytes:
    return jcs_bytes(_normalized(raw, models), max_bytes=WIRE_LIMITS.max_bytes)


def _interval(issued_at_ms: int, expires_at_ms: int, maximum: int = INTENT_LIFETIME_MS) -> None:
    if not 0 < expires_at_ms - issued_at_ms <= maximum:
        raise ExecutableError("invalid_wire")


def _action_domain(record: StrictModel) -> None:
    """Run only after bounded known-model projection and shape validation."""
    if type(record) in (ActionBinding, HumanDecision):
        _interval(record.issued_at_ms, record.expires_at_ms)
    elif type(record) is ActionIntent:
        _action_domain(record.binding)
        if record.sha256 != sha256_hex(_bytes(record.binding, ACTION_MODELS)):
            raise ExecutableError("invalid_wire")


def _admit(
    raw: object, model: type[Record], models: tuple, code: ErrorCode = "invalid_wire"
) -> Record:
    try:
        return validate_model(
            raw, model_type=model, model_types=models, code=code, limits=WIRE_LIMITS
        )
    except ExecutableError as error:
        if error.code == "invalid_json_value":
            raise ExecutableError(code) from None
        raise


def _action(raw: object, model: type[Record]) -> Record:
    result = _admit(raw, model, ACTION_MODELS)
    _action_domain(result)
    return result


def validate_action_binding(raw: object) -> ActionBinding:
    return _action(raw, ActionBinding)


def validate_action_intent(raw: object) -> ActionIntent:
    """Recompute the embedded binding digest, including on mutated instances."""
    return _action(raw, ActionIntent)


def validate_human_decision(raw: object) -> HumanDecision:
    return _action(raw, HumanDecision)


def canonical_binding_json(raw: object) -> bytes:
    return _bytes(validate_action_binding(raw), ACTION_MODELS)


def build_action_intent(binding: object) -> ActionIntent:
    validated = validate_action_binding(binding)
    return validate_action_intent(
        ActionIntent(binding=validated, sha256=sha256_hex(_bytes(validated, ACTION_MODELS)))
    )


def intent_digest(raw: object) -> str:
    """Return a validated ActionIntent's binding hash, not its wrapper hash."""
    return validate_action_intent(raw).sha256


def canonical_decision_json(raw: object) -> bytes:
    return _bytes(validate_human_decision(raw), ACTION_MODELS)


def decision_digest(raw: object) -> str:
    """Hash the complete normalized decision, including its actor and metadata."""
    return sha256_hex(canonical_decision_json(raw))


def validate_binding_context(raw: object) -> BindingContext:
    return _admit(raw, BindingContext, ACTION_MODELS, "invalid_context")


def validate_binding_report(raw: object) -> BindingReport:
    report = _admit(raw, BindingReport, ACTION_MODELS)
    if report.valid != (report.code == "binding_valid"):
        raise ExecutableError("invalid_wire")
    return report


def binding_report_json(raw: object) -> bytes:
    """Bounded ASCII report with newline, safe for ordinary output."""
    return report_bytes(
        validate_binding_report(raw),
        model_type=BindingReport,
        model_types=ACTION_MODELS,
        code="invalid_wire",
    )


_SCOPE = (
    "installation_id",
    "workspace_id",
    "project_id",
    "environment_id",
    "execution_id",
    "action_id",
)
_BINDING_EXPECTED = (
    *_SCOPE,
    "step_id",
    "package_sha256",
    "configuration_sha256",
    "plan_sha256",
    "policy_sha256",
    "requester_id",
)


def _report(code: BindingCode) -> BindingReport:
    return validate_binding_report(BindingReport(valid=code == "binding_valid", code=code))


def _scope_code(
    binding: ActionBinding,
    decision: HumanDecision | None,
    context: BindingContext,
    command: RuntimeCommand | None = None,
) -> BindingCode | None:
    if any(getattr(binding, key) != getattr(context, key) for key in _BINDING_EXPECTED):
        return "context_mismatch"
    if command is not None and any(
        getattr(command, key) != getattr(context, key) for key in _SCOPE
    ):
        return "context_mismatch"
    if decision is not None and decision.policy_sha256 != context.policy_sha256:
        return "context_mismatch"
    if binding.recovery_epoch != context.recovery_epoch or (
        command is not None and command.recovery_epoch != context.recovery_epoch
    ):
        return "stale_epoch"
    if command is not None:
        if command.fencing_generation != context.fencing_generation:
            return "stale_generation"
        if command.worker_id != context.worker_id:
            return "wrong_recipient"
    return None


def _time_code(records: tuple, now: int) -> BindingCode | None:
    for record in records:
        if record is not None:
            if now < record.issued_at_ms:
                return "not_yet_valid"
            if now >= record.expires_at_ms:
                return "expired"
    return None


def _decision_code(intent: ActionIntent, decision: HumanDecision | None) -> BindingCode:
    binding = intent.binding
    if binding.approval_mode == "none":
        return "approval_unexpected" if decision is not None else "binding_valid"
    if decision is None:
        return "approval_required"
    if (
        decision.intent_sha256 != intent.sha256
        or decision.expires_at_ms > binding.expires_at_ms
        or decision.issued_at_ms < binding.issued_at_ms
    ):
        return "intent_mismatch"
    if decision.decision != "allow":
        return "approval_denied"
    if decision.actor_kind != "human":
        return "invalid_approver"
    if binding.independent_approval and decision.actor_id == binding.requester_id:
        return "independence_required"
    return "binding_valid"


def check_decision(intent: object, decision: object, context: object) -> BindingReport:
    validated_intent = validate_action_intent(intent)
    validated_decision = validate_human_decision(decision)
    expected = validate_binding_context(context)
    code = (
        _scope_code(validated_intent.binding, validated_decision, expected)
        or _time_code((validated_intent.binding, validated_decision), expected.now_ms)
        or _decision_code(validated_intent, validated_decision)
    )
    return _report(code)


def check_command_binding(
    command: object, intent: object, decision: object | None, context: object
) -> BindingReport:
    # Local import keeps wire's payload models free to embed the action records.
    from .wire import validate_runtime_command

    cmd = validate_runtime_command(command)
    bound = validate_action_intent(intent)
    approved = None if decision is None else validate_human_decision(decision)
    expected = validate_binding_context(context)
    code = _scope_code(bound.binding, approved, expected, cmd) or _time_code(
        (bound.binding, cmd, approved), expected.now_ms
    )
    if code is not None:
        return _report(code)
    if cmd.intent_sha256 != bound.sha256:
        return _report("intent_mismatch")
    if bound.binding.approval_mode == "none" and cmd.decision_sha256 is not None:
        return _report("approval_unexpected")
    if bound.binding.approval_mode == "none":
        return _report(_decision_code(bound, approved))
    if approved is not None and cmd.decision_sha256 != decision_digest(approved):
        return _report("intent_mismatch")
    return _report(_decision_code(bound, approved))
