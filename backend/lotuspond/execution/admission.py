"""Atomic execution admission. All writes belong to the caller's transaction.

Acquire the configured installation cursor before authenticating a transport actor
in the same transaction. Actor objects convey identity, never reusable authority.
Service instances/providers are long-lived, explicitly provisioned dependencies.
"""

import json
from hashlib import sha256

from lotuspond_runtime.protocol import OpaqueId
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.input import project_json
from lotuspond_sdk.executable.types import validate_value
from lotuspond_sdk.executable.validation import validate_executable_package
from pydantic import TypeAdapter

from ..fleet.authority import database_now_ms
from ..models import (
    ExecutablePublication,
    Execution,
    ExecutionDeployment,
    ExecutionOutbox,
    ExecutionRequest,
    new_id,
)
from .artifacts import ArtifactPolicy
from .assurance import check_assurance_current
from .authorization import lock, reauthorize, record, scoped
from .contracts import AdmissionRequest, ExecutionView, canonical_value
from .deployments import _configuration, _pool, _stored_configuration, load_deployment
from .errors import ExecutionError, execution_service
from .publication import current_snapshot, load_publication


def request_identity(key):
    key = TypeAdapter(OpaqueId).validate_python(key, strict=True)
    return (
        "admission:"
        + sha256(b"lotuspond.execution.admission-key.v1\x00" + key.encode("ascii")).hexdigest()
    )


def execution_view(row):
    result = (
        None
        if row.result_artifact_id is None
        else ArtifactHandle(
            id=row.result_artifact_id, version=row.result_artifact_version, sha256=row.result_sha256
        )
    )
    return ExecutionView(
        id=row.id,
        state=row.state,
        requested_state=row.requested_state,
        reason=row.reason,
        admitted_at_ms=row.admitted_at_ms,
        deadline_ms=row.deadline_ms,
        result=result,
        next_event_sequence=row.next_event_sequence,
    )


def _current_deployment(db, actor, revision_id, *, dependencies, operation):
    """Final current-use check on the already locked immutable parent set.

    Revalidate sources and captured assurance after the last publisher/provider
    wait. This returns verified source data only inside the caller's transaction;
    its output never supplies a later activity/worker authority token.
    """
    _, revision, evidence = load_deployment(
        db,
        actor,
        revision_id,
        operation=operation,
        dependencies=dependencies,
        include_assurance=True,
    )
    snapshot = dependencies.current(db, actor.scope)
    _, package, links, rows, sources = load_publication(
        db, actor.scope, revision.publication_id, snapshot
    )
    configuration = _stored_configuration(db, actor.scope, revision, links)
    pool = _pool(db, actor.scope, revision.pool_id)
    _configuration(db, actor.scope, configuration, package, links, rows, sources, pool, snapshot)
    reauthorize(db, actor, operation)
    current_snapshot(db, snapshot)
    check_assurance_current(db, evidence, package, snapshot, sources)
    return package, sources


class AdmissionService:
    def __init__(self, *, budgets, artifacts, artifact_policy, interpreter_id):
        self.budgets, self.artifacts = budgets, artifacts
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        # Supplied execution input is a new workspace response, not a source ACL override.
        if self.policy.source_id is not None:
            raise ValueError("Admission requires workspace response policy")
        self.interpreter_id = TypeAdapter(OpaqueId).validate_python(interpreter_id, strict=True)

    @execution_service
    def admit_execution(
        self, db, actor, request: AdmissionRequest, request_key: str
    ) -> ExecutionView:
        self.budgets._cursor(db)  # first lock, before current customer authority
        actor = reauthorize(db, actor, "execute")
        request = AdmissionRequest.model_validate(request)
        # Frozen DTOs may still contain caller-owned mutable JSON containers.
        # Copy before provider calls without normalizing numbers: only the declared
        # schema may select a numeric domain. Identity and storage share this copy.
        request = AdmissionRequest(
            deployment_revision_id=request.deployment_revision_id,
            input=project_json(request.input),
        )
        key = request_identity(request_key)
        digest = sha256(
            canonical_value({"operation": "admission", **request.model_dump(mode="json")})
        ).hexdigest()
        _, capacity, revision, _ = self.budgets._parents(db, actor, request.deployment_revision_id)
        existing = lock(
            db, scoped(ExecutionRequest, actor.scope).where(ExecutionRequest.request_key == key)
        )
        if existing is not None:
            # Current actor and deployment authorization precede conflict disclosure.
            package, _ = _current_deployment(
                db, actor, revision.id, dependencies=self.budgets.deployments, operation="execute"
            )
            if existing.digest != digest:
                raise ExecutionError("conflict")
            # JCS numeric identities can coincide across int/float types. Equal
            # bytes are not proof that the caller supplied the declared domain.
            validate_value(request.input, package.input_schema, package.schemas)
            row = record(db, Execution, actor.scope, existing.execution_id, locked=True)
            if row.admission_digest != digest or row.deployment_revision_id != revision.id:
                raise ExecutionError("conflict")
            return execution_view(row)
        deployment = record(
            db, ExecutionDeployment, actor.scope, revision.deployment_id, locked=True
        )
        if deployment.active_revision_id != revision.id:
            raise ExecutionError("deployment_unavailable")
        publication = record(db, ExecutablePublication, actor.scope, revision.publication_id)
        package = validate_executable_package(json.loads(publication.canonical_package))
        validate_value(request.input, package.input_schema, package.schemas)
        admitted_at = database_now_ms(db)
        grant, tenant, recovery = self.budgets._grants(db, actor.scope)
        limits, _, _ = self.budgets._limits(db, actor.scope, revision, grant, tenant)
        deadline = admitted_at + limits["elapsed_seconds"] * 1000
        handle = self.artifacts.put(db, actor, canonical_value(request.input), self.policy)
        self.budgets.reserve_admission(db, actor, revision.id)
        # Consume only this transaction's just-reserved value under the same row lock.
        sequence = capacity.next_admission_sequence - 1
        row = Execution(
            **actor.scope.model_dump(),
            id=new_id(),
            deployment_revision_id=revision.id,
            interpreter_id=self.interpreter_id,
            engine_identity=new_id(),
            input_artifact_id=handle.id,
            input_artifact_version=handle.version,
            input_sha256=handle.sha256,
            initiating_user_id=actor.user_id,
            initiating_api_key_id=actor.api_key_id,
            admitted_at_ms=admitted_at,
            deadline_ms=deadline,
            admission_sequence=sequence,
            admission_digest=digest,
            state="pending",
            requested_state="running",
            reason=None,
            next_event_sequence=1,
        )
        db.add(row)
        db.flush()
        db.add(
            ExecutionRequest(
                **actor.scope.model_dump(), execution_id=row.id, request_key=key, digest=digest
            )
        )
        db.add(
            ExecutionOutbox(
                **actor.scope.model_dump(),
                execution_id=row.id,
                message_id=row.id,
                kind="engine_start",
                digest=digest,
                available_at_ms=admitted_at,
            )
        )
        db.flush()
        # Later key/reservation/provider waits must not extend any authority/deadline.
        self.budgets._final(db, actor, revision, grant, tenant, recovery, row)
        self.artifacts._current_time(db, self.policy, None)
        if deployment.active_revision_id != revision.id:
            raise ExecutionError("deployment_unavailable")
        return execution_view(row)


@execution_service
def admit_execution(db, actor, request, request_key, *, service=None):
    """Composition entrypoint. Unconfigured production admission always denies."""
    if service is None:
        raise ExecutionError("authority_unavailable")
    return service.admit_execution(db, actor, request, request_key)
