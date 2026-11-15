"""Verified outcome routes (E06, first part), under E04 Task 12's conventions.

An execution's trace and an environment's outcome counts: read models over committed
rows, digests and identifiers only, readable by anyone who may read the execution. The
download of a trace is audited with identifiers only. None needs action services: an
interpreter v1 execution simply has no actions.
"""

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..deps import DB
from .boundary import ExecutionRoute, actor, audit, no_query
from .errors import ExecutionError
from .outcomes import OutcomeProjection, parse_since
from .routes import _id

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/environments/{environment_id}",
    route_class=ExecutionRoute,
)
TAGS = ["production execution outcomes"]
PROJECTION = OutcomeProjection()


@router.get("/executions/{execution_id}/trace", tags=TAGS)
def get_production_execution_trace(
    project_id: str, environment_id: str, execution_id: str, request: Request, db=DB
):
    """Input, version, each action's review, dispatch, read-back and outcome."""
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    return PROJECTION.trace(db, current, _id(execution_id))


@router.get("/executions/{execution_id}/trace/export", tags=TAGS)
def export_production_execution_trace(
    project_id: str, environment_id: str, execution_id: str, request: Request, db=DB
):
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    trace = PROJECTION.trace(db, current, _id(execution_id))
    audit(
        db,
        current,
        "executions.trace_exported",
        trace["execution"]["id"],
        trace_sha256=trace["trace_sha256"],
    )
    return JSONResponse(
        trace,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="lotuspond-trace-{trace["execution"]["id"]}.json"'
            ),
        },
    )


@router.get("/outcomes", tags=TAGS)
def summarize_production_outcomes(project_id: str, environment_id: str, request: Request, db=DB):
    """Executions admitted since `since_ms`, optionally of one deployment, by outcome."""
    names = set(request.query_params)
    if not {"since_ms"} <= names <= {"since_ms", "deployment_id"} or any(
        len(request.query_params.getlist(name)) != 1 for name in names
    ):
        raise ExecutionError("invalid_request")
    since = parse_since(request.query_params.get("since_ms"))
    deployment = request.query_params.get("deployment_id")
    if deployment is not None and not re.fullmatch(r"[0-9a-f-]{36}", deployment):
        raise ExecutionError("invalid_request")
    current = actor(db, request, project_id, environment_id, "read")
    return PROJECTION.summary(db, current, since_ms=since, deployment_id=deployment)
