"""Production execution action routes (E05 Task 11), under E04 Task 12's conventions.

The action projection is metadata: authorization, effect and outcome states,
digests and identifiers, never a proposal's values. Read without locks. A
resolution is an owner's or admin's, by session only; it carries its own
`resolution_id`, so it needs no `Idempotency-Key`, and an equal retry the service
replayed is not audited again.
"""

from fastapi import APIRouter, Request

from ..deps import DB
from .action_contracts import ResolutionRequest
from .boundary import ExecutionRoute, action_services, actor, audit, no_query, page
from .errors import ExecutionError
from .routes import BODY, _id, _json

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/environments/{environment_id}",
    route_class=ExecutionRoute,
)
TAGS = ["production execution actions"]


@router.get("/executions/{execution_id}/actions", tags=TAGS)
def list_production_execution_actions(
    project_id: str, environment_id: str, execution_id: str, request: Request, db=DB
):
    limit, after = page(request)
    current = actor(db, request, project_id, environment_id, "read")
    after = None if after is None else _id(after)
    listed = action_services(request).projection.list(
        db, current, _id(execution_id), after_id=after, limit=limit
    )
    return {"items": [_json(item) for item in listed["items"]], "next_after_id": listed["next"]}


@router.get("/executions/{execution_id}/actions/{action_id}", tags=TAGS)
def get_production_execution_action(
    project_id: str, environment_id: str, execution_id: str, action_id: str, request: Request, db=DB
):
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    view = action_services(request).projection.get(db, current, _id(execution_id), _id(action_id))
    return _json(view)


@router.post("/executions/{execution_id}/actions/{action_id}/resolution", tags=TAGS)
def resolve_production_execution_action(
    project_id: str,
    environment_id: str,
    execution_id: str,
    action_id: str,
    request: Request,
    raw=BODY,
    db=DB,
):
    current = actor(db, request, project_id, environment_id, "reconcile")
    if current.api_key_id is not None or current.user_id is None:
        raise ExecutionError("forbidden")  # a person's session only, before any lock
    body = ResolutionRequest.model_validate(raw)
    execution_id, action_id = _id(execution_id), _id(action_id)
    view = action_services(request).resolutions.resolve(db, current, execution_id, action_id, body)
    audit(
        db,
        current,
        "executions.actions.resolved",
        action_id,
        body.resolution_id,
        execution_id=execution_id,
        kind=body.kind,
    )
    return _json(view)
