"""E05 action contracts: closed vocabularies, strict DTOs and pure binding derivation.

Nothing here grants authority, touches a database, reads the environment, opens a file
or socket, starts a thread or loads a provider. Canonical bytes and digests come only
from the SDK (`lotuspond_sdk.executable`); this module never canonicalizes itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lotuspond_runtime.protocol import Digest, OpaqueId, SafeInt
from lotuspond_sdk.activity import ArtifactHandle, UsageObservation
from lotuspond_sdk.executable.actions import (
    COMMAND_LIFETIME_MS,
    ActionBinding,
    ActionIntent,
    BindingContext,
    build_action_intent,
)
from lotuspond_sdk.executable.wire import ActionReceipt, RuntimeCommand, validate_runtime_command
from pydantic import JsonValue

from .contracts import Contract
from .errors import ExecutionError

INTERPRETER_V2 = "lotuspond.execution-interpreter.v2"

ASSIGNMENT_ROLES: dict[str, frozenset[str]] = {
    "workspace_owners": frozenset({"owner"}),
    "workspace_admins": frozenset({"owner", "admin"}),
    "workspace_editors": frozenset({"owner", "admin", "editor"}),
}
ESCALATIONS = frozenset({"after_half_timeout"})

TASK_CLAIM_MS = 900_000
ACTION_LEASE_MS = 60_000
ACTION_RENEW_MS = 20_000
MAX_READBACKS = 5
if COMMAND_LIFETIME_MS != 300_000:  # the plan's frozen command lifetime
    raise ImportError("unexpected SDK command lifetime")

AUTHORIZATION_STATES = (
    "proposed",
    "awaiting_review",
    "approved",
    "denied",
    "review_expired",
    "expired",
    "withdrawn",
    "superseded",
)
EFFECT_STATES = ("not_dispatched", "dispatching", "accepted", "observed", "unknown", "absent")
OUTCOME_STATES = ("unknown", "verified", "not_verified")
TASK_KINDS = ("review", "form")
TASK_STATES = ("open", "decided", "submitted", "expired", "cancelled")
COMMAND_STATES = ("admitted", "claimed", "stopped", "uncertain", "expired")
OBSERVATION_RESULTS = ("effect_present", "effect_absent", "unknown")
OBSERVATION_SOURCES = ("readback", "restore_readback", "historical", "resolution")
RESOLUTION_KINDS = ("readback", "compute_stopped", "effect_present", "close_unknown")
FAILURE_CODES = (
    "approval_denied",
    "review_expired",
    "approval_expired",
    "approval_revoked",
    "binding_refused",
    "not_applied",
    "outcome_not_verified",
    "outcome_unknown",
    "recovery_epoch_changed",
)
EXECUTION_REASONS = ("review", "action_uncertain", "containment", *FAILURE_CODES)
# execution_control event codes that schema v16 adds (Task 2).
CONTROL_CODES = (
    "task_opened",
    "task_claimed",
    "task_released",
    "task_escalated",
    "task_expired",
    "task_decided",
    "task_submitted",
    "intent_held",
    "intent_superseded",
    "action_resolved",
)

Authorization = Literal[
    "proposed",
    "awaiting_review",
    "approved",
    "denied",
    "review_expired",
    "expired",
    "withdrawn",
    "superseded",
]
Effect = Literal["not_dispatched", "dispatching", "accepted", "observed", "unknown", "absent"]
Outcome = Literal["unknown", "verified", "not_verified"]
TaskKind = Literal["review", "form"]
TaskState = Literal["open", "decided", "submitted", "expired", "cancelled"]
ResolutionKind = Literal["readback", "compute_stopped", "effect_present", "close_unknown"]


def assignment_roles(assignment: str) -> frozenset[str]:
    """The membership roles a closed assignment group admits; anything else is refused."""
    try:
        return ASSIGNMENT_ROLES[assignment]
    except (KeyError, TypeError):
        raise ExecutionError("unsupported_policy") from None


def check_escalation(escalation: str | None) -> None:
    if escalation is not None and escalation not in ESCALATIONS:
        raise ExecutionError("unsupported_policy")


# --- Strict DTOs -------------------------------------------------------------------


class HumanTaskView(Contract):
    id: OpaqueId
    execution_id: OpaqueId
    kind: TaskKind
    state: TaskState
    deadline_ms: SafeInt
    escalated: bool
    escalate_at_ms: SafeInt | None
    claim: Literal["none", "caller", "other"]
    claim_expires_at_ms: SafeInt | None
    intent_sha256: Digest | None


class ProposalView(Contract):
    intent_sha256: Digest
    connector_name: str
    connector_version: str
    operation: str
    policy_name: str
    policy_version: str
    approval_mode: Literal["none", "human"]
    independent_approval: bool
    requester_id: OpaqueId
    issued_at_ms: SafeInt
    expires_at_ms: SafeInt
    target: JsonValue
    input: JsonValue


class FormView(Contract):
    """What a form task asks: its declared data schema and the package's named schemas
    its `ref`s resolve against. Package content only, never an execution's values."""

    form_schema: JsonValue
    definitions: list[JsonValue]


class DecisionRequest(Contract):
    decision_id: OpaqueId
    intent_sha256: Digest
    decision: Literal["allow", "deny"]


class SubmissionRequest(Contract):
    submission_id: OpaqueId
    payload: JsonValue


class ActionView(Contract):
    action_id: OpaqueId
    step_id: str
    occurrence_path: str
    intent_sha256: Digest
    authorization: Authorization
    effect: Effect
    outcome: Outcome
    task_id: OpaqueId | None
    receipt_status: str | None
    evidence: tuple[ArtifactHandle, ...]


class ResolutionRequest(Contract):
    resolution_id: OpaqueId
    kind: ResolutionKind
    evidence: ArtifactHandle | None


# Runtime-shared shapes. The runtime defines the same shapes in
# `lotuspond_runtime.action_protocol` and never imports this module.


class ActionLease(Contract):
    attempt_id: OpaqueId
    command_id: OpaqueId
    # The dispatch ordinal: a receipt's `attempt` must equal it.
    ordinal: SafeInt
    lease_revision: SafeInt
    expires_at_ms: SafeInt
    deadline_ms: SafeInt


class ActionReport(Contract):
    attempt_id: OpaqueId
    lease_revision: SafeInt
    receipt: ActionReceipt
    response: ArtifactHandle | None
    usage: UsageObservation


class ActionStop(Contract):
    attempt_id: OpaqueId
    lease_revision: SafeInt
    code: Literal["not_started", "stopped_unknown"]
    usage: UsageObservation


# --- Derivation --------------------------------------------------------------------


@dataclass(frozen=True)
class BindingFields:
    """Exactly the sourced values of the specification's binding table."""

    installation_id: str
    recovery_epoch: int
    workspace_id: str
    project_id: str
    environment_id: str
    execution_id: str
    step_id: str
    action_id: str
    package_sha256: str
    plan_sha256: str
    configuration_sha256: str
    connector_name: str
    connector_version: str
    connector_artifact_sha256: str
    operation: str
    target_sha256: str
    input_sha256: str
    policy_sha256: str
    approval_mode: str
    independent_approval: bool
    requester_id: str
    issued_at_ms: int
    expiry_seconds: int
    execution_deadline_ms: int


def derive_binding(fields: BindingFields) -> ActionBinding:
    values = {k: v for k, v in vars(fields).items()}
    expiry_seconds = values.pop("expiry_seconds")
    deadline = values.pop("execution_deadline_ms")
    values["expires_at_ms"] = min(fields.issued_at_ms + expiry_seconds * 1000, deadline)
    return ActionBinding(**values)


def derive_intent(fields: BindingFields) -> ActionIntent:
    return build_action_intent(derive_binding(fields))


def idempotency_key(intent: ActionIntent) -> str:
    return intent.binding.action_id


def _context(binding: ActionBinding, worker_id: str, generation: int, now_ms: int):
    return BindingContext(
        installation_id=binding.installation_id,
        recovery_epoch=binding.recovery_epoch,
        workspace_id=binding.workspace_id,
        project_id=binding.project_id,
        environment_id=binding.environment_id,
        execution_id=binding.execution_id,
        step_id=binding.step_id,
        action_id=binding.action_id,
        package_sha256=binding.package_sha256,
        configuration_sha256=binding.configuration_sha256,
        plan_sha256=binding.plan_sha256,
        policy_sha256=binding.policy_sha256,
        worker_id=worker_id,
        fencing_generation=generation,
        requester_id=binding.requester_id,
        now_ms=now_ms,
    )


def decision_context(intent: ActionIntent, now_ms: int) -> BindingContext:
    """The context a decision is checked against; no command, so no recipient."""
    return _context(intent.binding, "control-plane", 0, now_ms)


@dataclass(frozen=True)
class Recipient:
    worker_id: str
    fencing_generation: int


def command_context(intent: ActionIntent, recipient: Recipient, now_ms: int) -> BindingContext:
    return _context(intent.binding, recipient.worker_id, recipient.fencing_generation, now_ms)


def derive_command(
    intent: ActionIntent,
    decision_sha256: str | None,
    recipient: Recipient,
    command_id: str,
    replay_id: str,
    now_ms: int,
    attempt_deadline_ms: int,
) -> RuntimeCommand:
    expires_at_ms = min(now_ms + COMMAND_LIFETIME_MS, attempt_deadline_ms)
    if expires_at_ms <= now_ms:
        raise ExecutionError("conflict")
    binding = intent.binding
    return validate_runtime_command(
        RuntimeCommand(
            command_id=command_id,
            replay_id=replay_id,
            intent_sha256=intent.sha256,
            decision_sha256=decision_sha256,
            installation_id=binding.installation_id,
            recovery_epoch=binding.recovery_epoch,
            workspace_id=binding.workspace_id,
            project_id=binding.project_id,
            environment_id=binding.environment_id,
            execution_id=binding.execution_id,
            action_id=binding.action_id,
            worker_id=recipient.worker_id,
            fencing_generation=recipient.fencing_generation,
            issued_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
        )
    )
