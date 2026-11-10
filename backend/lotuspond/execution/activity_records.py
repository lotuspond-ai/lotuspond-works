"""Pinned activity records, lease identity and shared guards for runtime activities.

Everything here is reconstructed from persisted rows: the verified program closure,
the deployment revision's canonical configuration and the exact connection version.
Nothing comes from a request body. Callers hold the locks; these helpers only read.

Lock order for every activity service (a subsequence of Task 5's order): installation
cursor (only when capacity counters change), workspace and E03 fleet rows (pool,
connections, instance, sessions, credential), environment capacity, deployment and
revision, execution, step, attempts, reservations, delivery, lease.
"""

import json
from dataclasses import dataclass
from datetime import UTC
from hashlib import sha256
from typing import Protocol

from lotuspond_runtime.activity_protocol import ActivityLease, PinnedActivityRecords, tool_name
from lotuspond_runtime.protocol import ConnectionConfig, Scope
from lotuspond_sdk.activity import (
    ActivityEnvelope,
    ActivitySignature,
    ArtifactHandle,
    canonical_activity_json,
    validate_activity,
)
from lotuspond_sdk.executable.package import AgentStep, ArtifactRef, ConnectorReadStep

from ..fleet.management import stored_config
from ..models import (
    ConnectionVersion,
    ExecutablePublication,
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionDeploymentRevision,
    ExecutionRequest,
    ExecutionStepInstance,
)
from ..security import Principal
from .authorization import authorize_execution, record, scoped
from .contracts import DeploymentConfig, canonical_value
from .errors import ExecutionError
from .program import ProgramError, load_program, resolve_occurrence

LEASE_CONTEXT = b"lotuspond-activity-lease-v1\0"
CHILD_CONTEXT = b"lotuspond-activity-child-v1\0"
VALUE_PREFIX = "activity.value:"
RESULT_PREFIX = "activity.result:"
# Root attempts use ordinals 1..10 (RetryPolicy.max_attempts <= 10). Child attempts
# share the root's step instance and take ordinals above that range.
CHILD_ORDINAL_BASE = 10


class ActivitySigner(Protocol):
    """Separately provisioned activity signing; never publisher or action keys."""

    def sign(self, envelope: ActivityEnvelope) -> ActivitySignature: ...


class UnavailableActivitySigner:
    def sign(self, envelope):
        raise ExecutionError("trust_unavailable")


@dataclass(frozen=True)
class Declaration:
    """One activity an agent attempt may request: its model or one declared read tool."""

    kind: str
    name: str
    dependency: ArtifactRef
    operation: str | None
    input_schema: dict
    output_schema: dict
    timeout_ms: int


@dataclass(frozen=True)
class ActivityPins:
    """Every persisted pin of one attempt, before a recipient or delivery exists."""

    scope: Scope
    execution_id: str
    step_instance_id: str
    attempt_id: str
    parent_attempt_id: str | None
    deployment_revision_id: str
    package_sha256: str
    plan_sha256: str
    configuration_sha256: str
    input: ArtifactHandle
    kind: str
    dependency: ArtifactRef
    operation: str | None
    connection: ConnectionConfig | None
    input_schema: dict
    output_schema: dict
    timeout_ms: int
    # For a root agent attempt: what its children may request.
    declarations: tuple[Declaration, ...] = ()


def scope_of(row) -> Scope:
    return Scope(
        workspace_id=row.workspace_id, project_id=row.project_id, environment_id=row.environment_id
    )


def _schema(schema, definitions):
    return {
        "schema": schema.model_dump(mode="json"),
        "definitions": [item.model_dump(mode="json") for item in definitions],
    }


def _read_schemas(document, operation):
    definitions = [item.model_dump(mode="json") for item in document.schemas]
    return (
        {
            "target": operation.target_schema.model_dump(mode="json"),
            "input": operation.input_schema.model_dump(mode="json"),
            "definitions": definitions,
        },
        _schema(operation.output_schema, document.schemas),
    )


def _operation(document, operation_id):
    operation = next((op for op in document.definition.operations if op.id == operation_id), None)
    if operation is None or operation.effect != "read":
        raise ExecutionError("dependency_unavailable")
    return operation


def _model(program, ref):
    """The agent step's pinned model reference and model document."""
    package, step = ref.package, ref.step
    if step.model_ref is not None:
        declared = next(d.ref for d in package.models if d.id == step.model_ref)
        return declared, program.catalog[declared.sha256], None
    declared = next(d.ref for d in package.agents if d.id == step.agent_ref)
    agent = program.catalog[declared.sha256]
    model_ref = agent.definition.model_ref
    return model_ref, program.catalog[model_ref.sha256], agent


def declarations(program, ref) -> tuple[Declaration, ...]:
    """The model and the read tools a root agent attempt may request, each named once."""
    package, step = ref.package, ref.step
    model_ref, model, agent = _model(program, ref)
    found = [
        Declaration(
            kind="model",
            name=model_ref.name,
            dependency=model_ref,
            operation=None,
            # A model round's request and reply are bounded JSON checked by the
            # runtime's model adapter; only the root attempt's output is typed here.
            input_schema={"schema": None, "definitions": []},
            output_schema={"schema": None, "definitions": []},
            timeout_ms=step.limits.timeout_seconds * 1000,
        )
    ]
    tools = []
    for tool in step.read_tools:
        declared = next(d.ref for d in package.connectors if d.id == tool.connector_ref)
        tools.append((declared, tool))
    if agent is not None:
        tools.extend((tool.connector_ref, tool) for tool in agent.definition.read_tools)
    seen = set()
    for declared, tool in tools:
        key = (declared.sha256, tool.operation)
        if key in seen:
            continue
        seen.add(key)
        name = tool_name(declared.name, tool.operation)
        document = program.catalog.get(declared.sha256)
        if name is None or document is None:
            continue  # an unnameable tool can never be requested
        input_schema, output_schema = _read_schemas(document, _operation(document, tool.operation))
        found.append(
            Declaration(
                kind="connector_read",
                name=name,
                dependency=declared,
                operation=tool.operation,
                input_schema=input_schema,
                output_schema=output_schema,
                timeout_ms=min(tool.retry.timeout_seconds, step.limits.timeout_seconds) * 1000,
            )
        )
    names = [item.name for item in found if item.kind == "connector_read"]
    # Two declarations under one name are ambiguous: neither can be requested.
    return tuple(item for item in found if item.kind == "model" or names.count(item.name) == 1)


def child_digest(request_id, kind, name, arguments) -> str:
    content = {
        "request_id": request_id,
        "kind": kind,
        "declaration_name": name,
        "arguments": ArtifactHandle.model_validate(arguments).model_dump(),
    }
    return sha256(CHILD_CONTEXT + canonical_value(content)).hexdigest()


def connection_for(db, scope, revision, dependency, operation) -> ConnectionConfig:
    """The deployment's exact connection version for a connector dependency."""
    configuration = DeploymentConfig.model_validate_json(revision.canonical_configuration)
    wanted = canonical_value(dependency.model_dump())
    binding = next(
        (b for b in configuration.bindings if canonical_value(b.dependency.model_dump()) == wanted),
        None,
    )
    if binding is None or binding.connection_id is None:
        raise ExecutionError("dependency_unavailable")
    version = db.scalar(
        scoped(ConnectionVersion, scope).where(
            ConnectionVersion.connection_id == binding.connection_id,
            ConnectionVersion.version == binding.connection_version,
        )
    )
    if version is None:
        raise ExecutionError("dependency_unavailable")
    config = stored_config(db, scope, version)
    if operation not in config.operations:
        raise ExecutionError("dependency_unavailable")
    return config


class ActivityRecordSource:
    """Reconstructs `PinnedActivityRecords` from persisted rows only."""

    def pins(self, db, execution, step, attempt) -> ActivityPins:
        scope = scope_of(execution)
        revision = record(db, ExecutionDeploymentRevision, scope, execution.deployment_revision_id)
        publication = record(db, ExecutablePublication, scope, revision.publication_id)
        try:
            program = load_program(db, scope, execution)
            ref = resolve_occurrence(program, step.occurrence_path)
        except ProgramError:
            raise ExecutionError("dependency_unavailable") from None
        if ref.selector is not None or ref.step_id != step.node_id:
            raise ExecutionError("dependency_unavailable")
        common = dict(
            scope=scope,
            execution_id=execution.id,
            step_instance_id=step.id,
            attempt_id=attempt.id,
            parent_attempt_id=attempt.parent_attempt_id,
            deployment_revision_id=revision.id,
            package_sha256=publication.package_sha256,
            plan_sha256=publication.plan_sha256,
            configuration_sha256=revision.configuration_sha256,
            input=ArtifactHandle(
                id=attempt.input_artifact_id,
                version=attempt.input_artifact_version,
                sha256=attempt.input_sha256,
            ),
        )
        node = ref.step
        if attempt.parent_attempt_id is not None:
            if not isinstance(node, AgentStep):
                raise ExecutionError("dependency_unavailable")
            parent = record(db, ExecutionAttempt, scope, attempt.parent_attempt_id)
            if parent.parent_attempt_id is not None or parent.step_instance_id != step.id:
                raise ExecutionError("dependency_unavailable")
            match = [
                item
                for item in declarations(program, ref)
                if child_digest(attempt.child_request_id, item.kind, item.name, common["input"])
                == attempt.child_request_digest
            ]
            if len(match) != 1:
                raise ExecutionError("dependency_unavailable")
            item = match[0]
            connection = (
                connection_for(db, scope, revision, item.dependency, item.operation)
                if item.kind == "connector_read"
                else None
            )
            return ActivityPins(
                **common,
                kind=item.kind,
                dependency=item.dependency,
                operation=item.operation,
                connection=connection,
                input_schema=item.input_schema,
                output_schema=item.output_schema,
                timeout_ms=item.timeout_ms,
            )
        if isinstance(node, ConnectorReadStep):
            declared = next(d.ref for d in ref.package.connectors if d.id == node.connector_ref)
            document = program.catalog[declared.sha256]
            input_schema, output_schema = _read_schemas(
                document, _operation(document, node.operation)
            )
            return ActivityPins(
                **common,
                kind="connector_read",
                dependency=declared,
                operation=node.operation,
                connection=connection_for(db, scope, revision, declared, node.operation),
                input_schema=input_schema,
                output_schema=output_schema,
                timeout_ms=node.retry.timeout_seconds * 1000,
            )
        if isinstance(node, AgentStep):
            model_ref, model, agent = _model(program, ref)
            source = agent if agent is not None else model
            return ActivityPins(
                **common,
                kind="model",
                dependency=model_ref,
                operation=None,
                connection=None,
                input_schema=_schema(source.definition.input_schema, source.schemas),
                output_schema=_schema(node.output_schema, ref.package.schemas),
                timeout_ms=min(node.retry.timeout_seconds, node.limits.timeout_seconds) * 1000,
                declarations=declarations(program, ref),
            )
        raise ExecutionError("unsupported_step")

    def deadline(self, db, execution, pins, issued_at_ms) -> int:
        """The attempt's absolute deadline; a child never outlives its parent."""
        deadline = min(execution.deadline_ms, issued_at_ms + pins.timeout_ms)
        if pins.parent_attempt_id is not None:
            parent = self.load(db, pins.parent_attempt_id)
            deadline = min(deadline, parent.deadline_ms)
        return deadline

    def envelope(self, pins, *, installation_id, recovery_epoch, recipient, delivery):
        """The exact envelope for these pins, recipient and delivery identity."""
        return validate_activity(
            dict(
                schema_version="lotuspond.runtime-activity.v1",
                installation_id=installation_id,
                recovery_epoch=recovery_epoch,
                **pins.scope.model_dump(),
                execution_id=pins.execution_id,
                step_instance_id=pins.step_instance_id,
                attempt_id=pins.attempt_id,
                deployment_revision_id=pins.deployment_revision_id,
                package_sha256=pins.package_sha256,
                plan_sha256=pins.plan_sha256,
                configuration_sha256=pins.configuration_sha256,
                input=pins.input.model_dump(),
                kind=pins.kind,
                dependency=pins.dependency.model_dump(),
                operation=pins.operation,
                pool_id=recipient["pool_id"],
                instance_id=recipient["instance_id"],
                generation=recipient["generation"],
                delivery_id=delivery["id"],
                replay_id=delivery["replay_id"],
                issued_at_ms=delivery["issued_at_ms"],
                expires_at_ms=delivery["expires_at_ms"],
            )
        )

    def load(self, db, attempt_id: str) -> PinnedActivityRecords:
        """Records of a delivered attempt; its stored envelope must equal a fresh rebuild."""
        attempt = db.get(ExecutionAttempt, attempt_id)
        if attempt is None:
            raise ExecutionError("not_found")
        scope = scope_of(attempt)
        execution = record(db, Execution, scope, attempt.execution_id)
        step = record(db, ExecutionStepInstance, scope, attempt.step_instance_id)
        delivery = db.scalar(
            scoped(ExecutionActivityDelivery, scope).where(
                ExecutionActivityDelivery.attempt_id == attempt.id
            )
        )
        if delivery is None:
            raise ExecutionError("not_found")
        pins = self.pins(db, execution, step, attempt)
        expected = self.envelope(
            pins,
            installation_id=delivery.installation_id,
            recovery_epoch=delivery.recovery_epoch,
            recipient=dict(
                pool_id=delivery.pool_id,
                instance_id=delivery.instance_id,
                generation=delivery.generation,
            ),
            delivery=dict(
                id=delivery.id,
                replay_id=delivery.replay_id,
                issued_at_ms=delivery.issued_at_ms,
                expires_at_ms=delivery.expires_at_ms,
            ),
        )
        deadline = self.deadline(db, execution, pins, delivery.issued_at_ms)
        canonical = canonical_activity_json(expected)
        if (
            bytes(delivery.canonical_envelope) != canonical
            or delivery.digest != sha256(canonical).hexdigest()
            or expected.expires_at_ms > deadline
        ):
            raise ExecutionError("conflict")
        return PinnedActivityRecords(
            envelope=expected,
            connection=pins.connection,
            dependency=pins.dependency,
            deadline_ms=deadline,
            input_schema=pins.input_schema,
            output_schema=pins.output_schema,
            request_sha256=attempt.input_sha256,
        )


def reserved_until(reservation) -> int:
    """When the attempt's reserved active time runs out (Task 5's liability start).

    A lease never runs past it, so concurrent attempts cannot exceed the active
    budget that admission and reservation checked.
    """
    created_at = reservation.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return int(created_at.timestamp() * 1000) + reservation.active_ms


def lease_token(delivery, session_id) -> str:
    """Binds a lease to its delivery and the claiming runtime session; never a secret."""
    content = {
        "attempt_id": delivery.attempt_id,
        "delivery_id": delivery.id,
        "delivery_sha256": delivery.digest,
        "session_id": session_id,
        "generation": delivery.generation,
    }
    return sha256(LEASE_CONTEXT + canonical_value(content)).hexdigest()


def lease_revision(row) -> int:
    """Derived from stored times: 1 at claim, larger after every later renewal."""
    return row.renewed_at_ms - row.claimed_at_ms + 1


def lease_view(row, delivery, deadline_ms) -> ActivityLease:
    return ActivityLease(
        attempt_id=row.attempt_id,
        generation=delivery.generation,
        lease_revision=lease_revision(row),
        expires_at_ms=row.expires_at_ms,
        deadline_ms=deadline_ms,
    )


def initiator(db, execution):
    """The execution's initiating principal, reauthorized now; raises when revoked."""
    principal = Principal(
        execution.initiating_api_key_id or execution.initiating_user_id,
        execution.workspace_id,
        "service" if execution.initiating_api_key_id else "member",
        None if execution.initiating_api_key_id else execution.initiating_user_id,
    )
    return authorize_execution(db, principal, scope_of(execution), "execute")


def registered_values(db, scope, attempt_id, artifact_ids):
    """Which of these artifacts the attempt's lease holder stored for this attempt."""
    keys = [VALUE_PREFIX + identifier for identifier in artifact_ids]
    rows = db.scalars(scoped(ExecutionRequest, scope).where(ExecutionRequest.request_key.in_(keys)))
    return {
        row.request_key[len(VALUE_PREFIX) :]
        for row in rows
        if row.digest == value_digest(attempt_id, row.request_key[len(VALUE_PREFIX) :])
    }


def value_digest(attempt_id, artifact_id) -> str:
    return sha256(
        canonical_value({"attempt_id": attempt_id, "artifact_id": artifact_id})
    ).hexdigest()


def signature_text(signature) -> str:
    return json.dumps(
        ActivitySignature.model_validate(signature).model_dump(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
