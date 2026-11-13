"""E05 action intents: immutable, derived only from persisted records (Task 4).

An `action_intent` step creates one intent per occurrence, inside the orchestrator
transaction that holds the execution row and evaluated the step's mappings. Its
target and input are stored as workspace-policy execution artifacts; its SDK
`ActionBinding` comes from the execution, its pinned publication and deployment
revision, the verified program and the bound connection version, never from a
request. Every later read recomputes the binding bytes, the intent digest and both
artifact digests; a mismatch is `ProgramError("integrity")`, which E04 fences.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from lotuspond_runtime.errors import FleetError
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.actions import (
    ActionIntent,
    build_action_intent,
    canonical_binding_json,
)
from lotuspond_sdk.executable.package import ActionIntentStep
from sqlalchemy import select

from ..fleet.authority import database_now_ms
from ..fleet.management import stored_config
from ..models import (
    ConnectionVersion,
    DeploymentBinding,
    ExecutableDependency,
    ExecutablePublication,
    ExecutionActionIntent,
    ExecutionDeploymentRevision,
    ExecutionEvent,
    new_id,
)
from .action_contracts import BindingFields, derive_binding
from .authorization import record, scoped
from .contracts import canonical_value
from .errors import ExecutionError
from .program import ProgramError

HANDLE_KIND = "action_intent"


class BindingRefused(Exception):
    """The pinned records cannot bind this intent (for example a connection mismatch)."""


def intent_handle(intent_id: str) -> dict:
    """The step's opaque output; the compiler forbids mappings to read inside it."""
    return {"handle_kind": HANDLE_KIND, "id": intent_id}


@dataclass(frozen=True)
class VerifiedIntent:
    row: ExecutionActionIntent
    intent: ActionIntent
    target: object
    input: object


def _scope(execution):
    from lotuspond_runtime.protocol import Scope

    return Scope(
        workspace_id=execution.workspace_id,
        project_id=execution.project_id,
        environment_id=execution.environment_id,
    )


def _connection_pin(db, scope, revision, connector_sha256):
    """The bound connection version for a connector dependency, and its connector pin."""
    link = db.scalar(
        scoped(ExecutableDependency, scope).where(
            ExecutableDependency.publication_id == revision.publication_id,
            ExecutableDependency.dependency_sha256 == connector_sha256,
        )
    )
    if link is None:
        raise BindingRefused
    binding = db.scalar(
        scoped(DeploymentBinding, scope).where(
            DeploymentBinding.deployment_revision_id == revision.id,
            DeploymentBinding.dependency_id == link.id,
        )
    )
    if binding is None or binding.connection_id is None:
        raise BindingRefused
    version = db.scalar(
        scoped(ConnectionVersion, scope).where(
            ConnectionVersion.connection_id == binding.connection_id,
            ConnectionVersion.version == binding.connection_version,
        )
    )
    if version is None:
        raise BindingRefused
    try:
        config = stored_config(db, scope, version)
    except (FleetError, ValueError):
        # Altered bytes, digest or links refuse; infrastructure errors stay retryable.
        raise BindingRefused from None
    return binding.connection_id, binding.connection_version, config


class IntentService:
    """Creates intents under the caller's execution row lock; evaluates nothing."""

    def __init__(self, *, artifacts, artifact_policy, installation_id, keys=None):
        self.artifacts, self.policy = artifacts, artifact_policy
        self.installation_id = installation_id
        self.keys = keys

    def _put(self, db, execution, value):
        return self.artifacts._put_internal(
            db, _scope(execution), canonical_value(value), self.policy, keys=self.keys
        )

    def create(
        self,
        db,
        execution,
        step_instance,
        step: ActionIntentStep,
        program,
        package,
        target,
        input,
        *,
        epoch,
    ) -> tuple[ExecutionActionIntent, ActionIntent]:
        """Returns the row and its SDK intent. Beyond the plan's signature it takes the
        step, program and package progression already holds, so nothing is re-derived."""
        scope = _scope(execution)
        revision = record(db, ExecutionDeploymentRevision, scope, execution.deployment_revision_id)
        publication = record(db, ExecutablePublication, scope, revision.publication_id)
        connector = program.document(package, "connectors", step.connector_ref)
        declaration = next(d for d in package.connectors if d.id == step.connector_ref)
        policy = next(d for d in package.policies if d.id == step.policy_ref)
        connection_id, connection_version, config = _connection_pin(
            db, scope, revision, declaration.ref.sha256
        )
        pin = config.connector
        if (
            pin.name != connector.name
            or pin.version != connector.version
            or pin.artifact_sha256 != connector.definition.artifact_sha256
            or step.operation not in config.operations
        ):
            raise BindingRefused
        issued = database_now_ms(db)
        if min(issued + step.expiry_seconds * 1000, execution.deadline_ms) <= issued:
            raise BindingRefused  # no time left before the execution deadline
        target_handle = self._put(db, execution, target)
        input_handle = self._put(db, execution, input)
        # The binding's value digests equal the artifact digests (SHA-256 of canonical JSON).
        for handle, value in ((target_handle, target), (input_handle, input)):
            if handle.sha256 != sha256(canonical_value(value)).hexdigest():
                raise ProgramError("integrity")
        binding = derive_binding(
            BindingFields(
                installation_id=self.installation_id,
                recovery_epoch=epoch,
                workspace_id=execution.workspace_id,
                project_id=execution.project_id,
                environment_id=execution.environment_id,
                execution_id=execution.id,
                step_id=step.id,
                action_id=new_id(),
                package_sha256=publication.package_sha256,
                plan_sha256=publication.plan_sha256,
                configuration_sha256=revision.configuration_sha256,
                connector_name=connector.name,
                connector_version=connector.version,
                connector_artifact_sha256=connector.definition.artifact_sha256,
                operation=step.operation,
                target_sha256=target_handle.sha256,
                input_sha256=input_handle.sha256,
                policy_sha256=policy.ref.sha256,
                approval_mode=step.approval,
                independent_approval=step.independent_review,
                requester_id=execution.initiating_user_id,
                issued_at_ms=issued,
                expiry_seconds=step.expiry_seconds,
                execution_deadline_ms=execution.deadline_ms,
            )
        )
        intent = build_action_intent(binding)
        row = ExecutionActionIntent(
            **scope.model_dump(),
            execution_id=execution.id,
            step_instance_id=step_instance.id,
            action_id=binding.action_id,
            binding_bytes=canonical_binding_json(binding),
            intent_sha256=intent.sha256,
            recovery_epoch=epoch,
            connection_id=connection_id,
            connection_version=connection_version,
            target_artifact_id=target_handle.id,
            target_artifact_version=target_handle.version,
            target_sha256=target_handle.sha256,
            input_artifact_id=input_handle.id,
            input_artifact_version=input_handle.version,
            input_sha256=input_handle.sha256,
            approval_mode=step.approval,
            independent_approval=step.independent_review,
            requester_user_id=execution.initiating_user_id,
            issued_at_ms=binding.issued_at_ms,
            expires_at_ms=binding.expires_at_ms,
            authorization_state="proposed" if step.approval == "none" else "awaiting_review",
            effect_state="not_dispatched",
            outcome_state="unknown",
        )
        db.add(row)
        db.flush()
        return row, intent

    def record_created(self, db, execution, epoch, intent, attempt_id):
        """Append the SDK `intent_created` event with the exact intent in its encrypted content."""
        record_sdk_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "intent_created",
            {"intent": json.loads(intent.model_dump_json())},
            attempt_id=attempt_id,
            keys=self.keys,
        )


def record_sdk_event(
    db,
    artifacts,
    policy,
    installation_id,
    execution,
    epoch,
    kind,
    payload,
    *,
    attempt_id=None,
    keys=None,
):
    """An SDK runtime event in the execution's one gap-free sequence, its record encrypted.

    The row holds the kind and references only; the record (intent, decision, ...)
    is in the encrypted event artifact.
    """
    sequence = execution.next_event_sequence
    content = {
        "format": "lotuspond.execution-event.v1",
        "execution_id": execution.id,
        "sequence": sequence,
        "kind": kind,
        "payload": payload,
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
            family="sdk_runtime_event",
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
            code=kind,
            occurred_at_ms=database_now_ms(db),
            application_state="applied",
            gap_slot=None,
        )
    )
    execution.next_event_sequence = sequence + 1
    db.flush()


def intent_for(db, execution, step_instance_id):
    return db.scalar(
        scoped(ExecutionActionIntent, _scope(execution)).where(
            ExecutionActionIntent.execution_id == execution.id,
            ExecutionActionIntent.step_instance_id == step_instance_id,
        )
    )


def _pinned(db, scope, row, *, program=None, installation_id=None):
    """The intent's binding against the records it was derived from (spec, "Action intents")."""
    from lotuspond_sdk.executable.package import ActionIntentStep

    from ..models import Execution, ExecutionStepInstance
    from .program import load_program, resolve_occurrence

    binding = json.loads(row.binding_bytes)
    execution = record(db, Execution, scope, row.execution_id)
    revision = record(db, ExecutionDeploymentRevision, scope, execution.deployment_revision_id)
    publication = record(db, ExecutablePublication, scope, revision.publication_id)
    instance = record(db, ExecutionStepInstance, scope, row.step_instance_id)
    program = program or load_program(db, scope, execution)
    ref = resolve_occurrence(program, instance.occurrence_path)
    step = ref.step
    if not isinstance(step, ActionIntentStep):
        raise ValueError
    connector = program.document(ref.package, "connectors", step.connector_ref)
    policy = next(d for d in ref.package.policies if d.id == step.policy_ref)
    expected = {
        "execution_id": execution.id,
        "requester_id": execution.initiating_user_id,
        "step_id": step.id,
        "package_sha256": publication.package_sha256,
        "plan_sha256": publication.plan_sha256,
        "configuration_sha256": revision.configuration_sha256,
        "connector_name": connector.name,
        "connector_version": connector.version,
        "connector_artifact_sha256": connector.definition.artifact_sha256,
        "operation": step.operation,
        "policy_sha256": policy.ref.sha256,
        "approval_mode": step.approval,
        "independent_approval": step.independent_review,
    }
    if instance.node_id != step.id or instance.execution_id != execution.id:
        raise ValueError
    if any(binding.get(key) != value for key, value in expected.items()):
        raise ValueError
    if installation_id is not None and binding.get("installation_id") != installation_id:
        raise ValueError
    if binding.get("expires_at_ms", 0) > execution.deadline_ms:
        raise ValueError


def _verified(db, scope, row, *, artifacts, keys=None, program=None, installation_id=None):
    try:
        binding = json.loads(row.binding_bytes)
        intent = build_action_intent(binding)
        values = []
        for prefix in ("target", "input"):
            handle = ArtifactHandle(
                id=getattr(row, f"{prefix}_artifact_id"),
                version=getattr(row, f"{prefix}_artifact_version"),
                sha256=getattr(row, f"{prefix}_sha256"),
            )
            content = artifacts._get_internal(db, scope, handle, keys=keys)
            if sha256(content).hexdigest() != handle.sha256:
                raise ValueError
            values.append(json.loads(content))
        b = intent.binding
        if (
            canonical_binding_json(b) != row.binding_bytes
            or intent.sha256 != row.intent_sha256
            or b.target_sha256 != row.target_sha256
            or b.input_sha256 != row.input_sha256
            or b.action_id != row.action_id
            or b.execution_id != row.execution_id
            or (b.workspace_id, b.project_id, b.environment_id)
            != (row.workspace_id, row.project_id, row.environment_id)
            or b.recovery_epoch != row.recovery_epoch
            or b.approval_mode != row.approval_mode
            or b.independent_approval != row.independent_approval
            or b.requester_id != row.requester_user_id
            or (b.issued_at_ms, b.expires_at_ms) != (row.issued_at_ms, row.expires_at_ms)
        ):
            raise ValueError
        _pinned(db, scope, row, program=program, installation_id=installation_id)
    except ExecutionError as error:
        if error.code in {"key_unavailable", "authority_unavailable"}:
            raise  # infrastructure: retryable, never an integrity verdict
        raise ProgramError("integrity") from None
    except ProgramError:
        raise
    except Exception:
        raise ProgramError("integrity") from None
    return VerifiedIntent(row, intent, values[0], values[1])


def load_intent(
    db, scope, intent_id, *, artifacts, keys=None, program=None, installation_id=None
) -> VerifiedIntent:
    """Recompute the binding bytes, the intent digest, both artifact digests, and every
    bound field against the pinned execution, publication, revision, program and step."""
    row = record(db, ExecutionActionIntent, scope, intent_id)
    return _verified(
        db,
        scope,
        row,
        artifacts=artifacts,
        keys=keys,
        program=program,
        installation_id=installation_id,
    )


def verify_intents(db, execution, *, artifacts, keys=None, program=None, installation_id=None):
    """Every intent of this execution still verifies (each later read, Task 4); one query."""
    scope = _scope(execution)
    rows = db.scalars(
        select(ExecutionActionIntent)
        .where(
            ExecutionActionIntent.workspace_id == execution.workspace_id,
            ExecutionActionIntent.project_id == execution.project_id,
            ExecutionActionIntent.environment_id == execution.environment_id,
            ExecutionActionIntent.execution_id == execution.id,
        )
        .order_by(ExecutionActionIntent.id)
    )
    for row in rows:
        _verified(
            db,
            scope,
            row,
            artifacts=artifacts,
            keys=keys,
            program=program,
            installation_id=installation_id,
        )
