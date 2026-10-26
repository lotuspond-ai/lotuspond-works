"""Customer execution routes (Task 12): production executions, never evaluation runs.

Every route is under `/api/v1/projects/{project_id}/environments/{environment_id}`
in the authenticated workspace. A session or `lp_` key is authenticated first and
the operation authorized in its own short transaction; each service then
reauthorizes under its own lock order (cursor and provider lookups first), so a
route never holds a lock a service must take earlier. Database-only reads need no
provider and no worker, so an orchestrator outage never blocks evidence.

Responses are explicit DTOs, never ORM rows. Mutations require an
`Idempotency-Key` except signals, controls and reconciliation, whose identity is
their own (`signal_id`, `request_id`, or the state they change). Each mutation is
audited with fixed identifiers and states only.
"""

from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from lotuspond_runtime.protocol import CanonicalUUID, OpaqueId, SafeInt
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.contract import Identifier
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ..deps import DB
from ..models import (
    ExecutablePublication,
    Execution,
    ExecutionArtifact,
    ExecutionContextSource,
    ExecutionDeployment,
    ExecutionEvent,
    ExecutionStepInstance,
    ExecutionWait,
    new_id,
)
from .admission import execution_view
from .authorization import reauthorize, record, scoped
from .boundary import (
    ExecutionRoute,
    actor,
    audit,
    json_body,
    no_query,
    page,
    request_key,
    services,
)
from .contracts import (
    AdmissionRequest,
    Contract,
    ControlRequest,
    DeploymentRequest,
    PublicationRequest,
    SignalRequest,
    canonical_value,
)
from .deployments import _view as deployment_view
from .deployments import create_deployment, set_deployment_status
from .errors import ExecutionError
from .projections import project_execution
from .publication import mutation_replay, publication_view, publish_v2, remember

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/environments/{environment_id}",
    route_class=ExecutionRoute,
)


async def _body(request: Request):
    return json_body(await request.body())


BODY = Depends(_body)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StatusRequest(_Strict):
    status: Literal["enabled", "disabled", "revoked"]


class EmptyRequest(_Strict):
    pass


class ContextVersionRequest(_Strict):
    content: object
    citation: object
    classification: Identifier
    observed_at_ms: SafeInt
    expires_at_ms: SafeInt
    retain_until_ms: SafeInt
    access_policy_id: OpaqueId
    key_id: OpaqueId


class ContextNextVersion(ContextVersionRequest):
    version: int = Field(ge=2, le=2147483647)


class ContextView(Contract):
    source_id: CanonicalUUID
    version: int
    value: ArtifactHandle
    citation: ArtifactHandle


def _id(value: str) -> str:
    try:
        return TypeAdapter(CanonicalUUID).validate_python(value, strict=True)
    except Exception:
        raise ExecutionError("not_found") from None


def _inventory(db, scope, model, request, view, *predicates):
    """One keyset page of a scoped collection: `items` and `next_after_id`, no counts."""
    limit, after = page(request)
    query = scoped(model, scope).where(*predicates)
    if after is not None:
        after = _id(after)
        if db.scalar(query.where(model.id == after)) is None:
            raise ExecutionError("not_found")  # another collection's ID reveals nothing
        query = query.where(model.id > after)
    rows = list(db.scalars(query.order_by(model.id).limit(limit + 1)))
    return {
        "items": [view(row) for row in rows[:limit]],
        "next_after_id": rows[limit - 1].id if len(rows) > limit else None,
    }


def _json(view) -> dict:
    return view.model_dump(mode="json")


# ------------------------------------------------------------------ packages


@router.post("/executable-packages", status_code=201, tags=["executable packages"])
def publish_executable_package(
    project_id: str, environment_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "publish")
    key = request_key(request)
    view = publish_v2(
        db,
        current,
        PublicationRequest.model_validate(raw),
        key,
        dependencies=services(request).dependencies,
    )
    audit(db, current, "executions.packages.published", view.id, key)
    return _json(view)


@router.get("/executable-packages", tags=["executable packages"])
def list_executable_packages(project_id: str, environment_id: str, request: Request, db=DB):
    current = actor(db, request, project_id, environment_id, "read")
    return _inventory(
        db,
        current.scope,
        ExecutablePublication,
        request,
        lambda row: _json(publication_view(row)),
    )


@router.get("/executable-packages/{publication_id}", tags=["executable packages"])
def get_executable_package(
    project_id: str, environment_id: str, publication_id: str, request: Request, db=DB
):
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    row = record(db, ExecutablePublication, current.scope, _id(publication_id))
    return _json(publication_view(row))


@router.get("/executable-packages/{publication_id}/content", tags=["executable packages"])
def get_executable_package_content(
    project_id: str, environment_id: str, publication_id: str, request: Request, db=DB
):
    """The exact canonical bytes that were admitted; their digest is checked first."""
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    row = record(db, ExecutablePublication, current.scope, _id(publication_id))
    content = bytes(row.canonical_package)
    if sha256(content).hexdigest() != row.package_sha256:
        raise ExecutionError("artifact_corrupt")
    return Response(content, media_type="application/json")


# ------------------------------------------------------------------ deployments


@router.post("/deployments", status_code=201, tags=["deployments"])
def create_execution_deployment(
    project_id: str, environment_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "manage")
    key = request_key(request)
    view = create_deployment(
        db,
        current,
        DeploymentRequest.model_validate(raw),
        key,
        dependencies=services(request).dependencies,
    )
    audit(db, current, "executions.deployments.created", view.id, key, revision_id=view.revision_id)
    return _json(view)


@router.get("/deployments", tags=["deployments"])
def list_execution_deployments(project_id: str, environment_id: str, request: Request, db=DB):
    current = actor(db, request, project_id, environment_id, "read")
    return _inventory(
        db,
        current.scope,
        ExecutionDeployment,
        request,
        lambda row: _json(deployment_view(row, None)),
    )


@router.get("/deployments/{deployment_id}", tags=["deployments"])
def get_execution_deployment(
    project_id: str, environment_id: str, deployment_id: str, request: Request, db=DB
):
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    return _json(
        deployment_view(record(db, ExecutionDeployment, current.scope, _id(deployment_id)), None)
    )


def _status(request, db, project_id, environment_id, deployment_id, revision_id, raw):
    current = actor(db, request, project_id, environment_id, "manage")
    status = StatusRequest.model_validate(raw).status
    deployment_id = _id(deployment_id)
    revision_id = None if revision_id is None else _id(revision_id)
    key = request_key(request)
    view = set_deployment_status(
        db,
        current,
        deployment_id,
        revision_id,
        status,
        key,
        dependencies=services(request).dependencies
        if status == "enabled"
        else _withdrawal(request),
    )
    audit(
        db,
        current,
        "executions.deployments.status",
        deployment_id,
        key,
        revision_id=revision_id,
        status=status,
    )
    return _json(view)


def _withdrawal(request):
    """Withdrawing authority needs no provider, so it works without configured services."""
    configured = request.app.state.execution_services
    return None if configured is None else configured.dependencies


@router.post("/deployments/{deployment_id}/status", tags=["deployments"])
def set_execution_deployment_status(
    project_id: str, environment_id: str, deployment_id: str, request: Request, raw=BODY, db=DB
):
    return _status(request, db, project_id, environment_id, deployment_id, None, raw)


@router.post("/deployments/{deployment_id}/revisions/{revision_id}/status", tags=["deployments"])
def set_execution_deployment_revision_status(
    project_id: str,
    environment_id: str,
    deployment_id: str,
    revision_id: str,
    request: Request,
    raw=BODY,
    db=DB,
):
    return _status(request, db, project_id, environment_id, deployment_id, revision_id, raw)


# ------------------------------------------------------------------ executions


@router.post("/executions", status_code=201, tags=["production executions"])
def start_production_execution(
    project_id: str, environment_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "execute")
    key = request_key(request)
    view = services(request).admission.admit_execution(
        db, current, AdmissionRequest.model_validate(raw), key
    )
    audit(db, current, "executions.started", view.id, key, state=view.state)
    return _json(view)


@router.get("/executions", tags=["production executions"])
def list_production_executions(project_id: str, environment_id: str, request: Request, db=DB):
    current = actor(db, request, project_id, environment_id, "read")
    return _inventory(db, current.scope, Execution, request, lambda row: _json(execution_view(row)))


@router.get("/executions/{execution_id}", tags=["production executions"])
def get_production_execution(
    project_id: str, environment_id: str, execution_id: str, request: Request, db=DB
):
    """Observed and requested state, separately: a request is never shown as observed."""
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    return _json(project_execution(db, current, _id(execution_id)))


@router.get("/executions/{execution_id}/events", tags=["production executions"])
def list_production_execution_events(
    project_id: str, environment_id: str, execution_id: str, request: Request, db=DB
):
    """Event metadata in sequence order; contents stay encrypted and are not served."""
    current = actor(db, request, project_id, environment_id, "read")
    execution = record(db, Execution, current.scope, _id(execution_id))
    limit, after = page(request, "after_sequence")
    query = scoped(ExecutionEvent, current.scope).where(ExecutionEvent.execution_id == execution.id)
    if after is not None:
        if not after.isdigit() or len(after) > 16:
            raise ExecutionError("invalid_request")
        query = query.where(ExecutionEvent.sequence > int(after))
    rows = list(db.scalars(query.order_by(ExecutionEvent.sequence).limit(limit + 1)))
    return {
        "items": [
            {
                "sequence": row.sequence,
                "family": row.family,
                "code": row.code,
                "occurred_at_ms": row.occurred_at_ms,
                "attempt_id": row.attempt_id,
                "application_state": row.application_state,
            }
            for row in rows[:limit]
        ],
        "next_after_sequence": rows[limit - 1].sequence if len(rows) > limit else None,
    }


@router.get("/executions/{execution_id}/waits", tags=["production execution waits"])
def list_production_execution_waits(
    project_id: str, environment_id: str, execution_id: str, request: Request, db=DB
):
    current = actor(db, request, project_id, environment_id, "read")
    execution = record(db, Execution, current.scope, _id(execution_id))
    waits = _inventory(
        db,
        current.scope,
        ExecutionWait,
        request,
        lambda row: row,
        ExecutionWait.execution_id == execution.id,
    )
    # Only this page's step instances, never every path of a large iteration.
    paths = dict(
        db.execute(
            scoped(ExecutionStepInstance, current.scope)
            .where(
                ExecutionStepInstance.execution_id == execution.id,
                ExecutionStepInstance.id.in_({row.step_instance_id for row in waits["items"]}),
            )
            .with_only_columns(ExecutionStepInstance.id, ExecutionStepInstance.occurrence_path)
        ).all()
    )
    waits["items"] = [
        {
            "id": row.id,
            "occurrence_path": paths.get(row.step_instance_id),
            "state": row.state,
            "deadline_ms": row.deadline_ms,
        }
        for row in waits["items"]
    ]
    return waits


@router.post("/executions/{execution_id}/signals", tags=["production execution waits"])
def signal_production_execution_wait(
    project_id: str, environment_id: str, execution_id: str, request: Request, raw=BODY, db=DB
):
    """An external signal or a human form; a form is a person's decision, never a key's."""
    current = actor(db, request, project_id, environment_id, "signal")
    body = SignalRequest.model_validate(raw)
    receipt = services(request).inbox.accept_signal(db, current, _id(execution_id), body)
    audit(
        db, current, "executions.signalled", body.wait_id, body.signal_id, execution_id=execution_id
    )
    return _json(receipt)


@router.post("/executions/{execution_id}/control", tags=["production executions"])
def control_production_execution(
    project_id: str, environment_id: str, execution_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "control")
    body = ControlRequest.model_validate(raw)
    view = services(request).controls.request_control(db, current, _id(execution_id), body)
    audit(
        db,
        current,
        "executions.controlled",
        execution_id,
        body.request_id,
        action=body.action,
        requested_state=view.requested_state,
    )
    return _json(view)


@router.post("/executions/{execution_id}/reconciliation", tags=["production executions"])
def reconcile_production_execution(
    project_id: str, environment_id: str, execution_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "reconcile")
    EmptyRequest.model_validate(raw)
    view = services(request).controls.reconcile_execution(db, current, _id(execution_id))
    audit(db, current, "executions.reconciled", execution_id, state=view.state)
    return _json(view)


# ------------------------------------------------------------------ context and artifacts


def _register(request, db, current, source_id, version, body, operation):
    """`source_id` is None for a new source: it is chosen only after a replay check, so
    an equal retry under its key returns the first registration."""
    content = {"source_id": source_id, "version": version, **body.model_dump(mode="json")}
    key = request_key(request)
    # Under the account locks first, as `register` takes them, so two equal requests
    # serialize and the second replays the first instead of conflicting.
    current = reauthorize(db, current, "manage")
    digest, replay = mutation_replay(db, current, operation, key, content, ContextView)
    if replay is not None:
        return replay
    source_id = source_id or new_id()
    registered = services(request).context.register(
        db,
        current,
        source_id=source_id,
        version=version,
        content=canonical_value(body.content),
        citation=canonical_value(body.citation),
        classification=body.classification,
        observed_at_ms=body.observed_at_ms,
        expires_at_ms=body.expires_at_ms,
        retain_until_ms=body.retain_until_ms,
        access_policy_id=body.access_policy_id,
        key_id=body.key_id,
    )
    view = ContextView(
        source_id=source_id,
        version=version,
        value=registered.value,
        citation=registered.citation,
    )
    audit(db, current, "executions.context.registered", source_id, key, version=version)
    return remember(db, current, operation, key, digest, view)


@router.post("/context-sources", status_code=201, tags=["context sources"])
def register_context_source(
    project_id: str, environment_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "manage")
    body = ContextVersionRequest.model_validate(raw)
    return _json(_register(request, db, current, None, 1, body, "execution.context.register"))


@router.post("/context-sources/{source_id}/versions", status_code=201, tags=["context sources"])
def register_context_source_version(
    project_id: str, environment_id: str, source_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "manage")
    body = ContextNextVersion.model_validate(raw)
    source_id = _id(source_id)
    record(db, ExecutionContextSource, current.scope, source_id)
    fields = ContextVersionRequest(**body.model_dump(exclude={"version"}))
    return _json(
        _register(
            request, db, current, source_id, body.version, fields, "execution.context.version"
        )
    )


@router.post("/context-sources/{source_id}/revoke", tags=["context sources"])
def revoke_context_source(
    project_id: str, environment_id: str, source_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "manage")
    EmptyRequest.model_validate(raw)
    key = request_key(request)
    source_id = _id(source_id)
    services(request).context.set_status(db, current, source_id, "revoked")
    audit(db, current, "executions.context.revoked", source_id, key)
    return {"source_id": source_id, "status": "revoked"}


@router.get("/artifacts/{artifact_id}/versions/{version}", tags=["execution artifacts"])
def get_execution_artifact(
    project_id: str, environment_id: str, artifact_id: str, version: str, request: Request, db=DB
):
    """One artifact version, decrypted under current access; never a bearer capability."""
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    if not version.isdigit() or len(version) > 10 or not 1 <= int(version) <= 2147483647:
        raise ExecutionError("not_found")
    row = record(db, ExecutionArtifact, current.scope, _id(artifact_id))
    if row.version != int(version):
        raise ExecutionError("not_found")
    handle = ArtifactHandle(id=row.id, version=row.version, sha256=row.sha256)
    return Response(
        services(request).artifacts.get(db, current, handle), media_type="application/json"
    )
