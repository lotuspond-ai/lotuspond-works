"""Human task routes (E05 Task 11), under E04 Task 12's conventions.

Every route is under `/api/v1/projects/{project_id}/environments/{environment_id}`.
A session or key is authenticated and the operation pre-checked without locks
(`actor`); the task service then reauthorizes under its own lock order. Claims,
decisions and submissions are a person's: they need a human session, and a key,
whatever its scopes, is refused before any lock (`KEY_SCOPE["review"]`). Browser
origin, content type and MFA are E04's (`RequestBoundary`, `session_user`).

Claim and release are idempotent by state; a decision and a submission carry their
own identity, so none needs an `Idempotency-Key`. A task whose wait is not yet
retained is absent from the listing, and its other routes answer as Task 5 sets out
(E04 finding 2): `not_found` for reads, `conflict` for mutations.

Each mutation, and each proposal read, writes an `AuditEvent` with fixed identifiers
only. An equal retry the service replayed is not audited again.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..deps import DB
from .action_contracts import DecisionRequest, SubmissionRequest
from .boundary import ExecutionRoute, action_services, actor, audit, no_query, page
from .errors import ExecutionError
from .routes import BODY, EmptyRequest, _id, _json

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/environments/{environment_id}",
    route_class=ExecutionRoute,
)
TAGS = ["human tasks"]


def _person(db, request, project_id, environment_id, operation):
    """A person's session for this operation, pre-checked without locks."""
    current = actor(db, request, project_id, environment_id, operation)
    if current.api_key_id is not None or current.user_id is None:
        raise ExecutionError("forbidden")
    return current


@router.get("/human-tasks", tags=TAGS)
def list_human_tasks(project_id: str, environment_id: str, request: Request, db=DB):
    """Open tasks of this environment, once their waits are retained; keyset pages."""
    limit, after = page(request)
    current = actor(db, request, project_id, environment_id, "read")
    after = None if after is None else _id(after)
    listed = action_services(request).tasks.list(db, current, after_id=after, limit=limit)
    return {
        "items": [_json(item) for item in listed["items"]],
        "next_after_id": listed["next_after_id"],
    }


@router.get("/human-tasks/{task_id}", tags=TAGS)
def get_human_task(project_id: str, environment_id: str, task_id: str, request: Request, db=DB):
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    return _json(action_services(request).tasks.get(db, current, _id(task_id)))


@router.get("/human-tasks/{task_id}/proposal", tags=TAGS)
def get_human_task_proposal(
    project_id: str, environment_id: str, task_id: str, request: Request, db=DB
):
    """The proposal's values: never cached, and audited without them."""
    no_query(request)
    current = _person(db, request, project_id, environment_id, "read")
    task_id = _id(task_id)
    services = action_services(request)
    proposal = services.tasks.proposal(db, current, task_id)
    execution_id = services.tasks.get(db, current, task_id).execution_id
    audit(db, current, "executions.tasks.proposal_read", task_id, execution_id=execution_id)
    return JSONResponse(_json(proposal), headers={"Cache-Control": "no-store"})


@router.get("/human-tasks/{task_id}/form", tags=TAGS)
def get_human_task_form(
    project_id: str, environment_id: str, task_id: str, request: Request, db=DB
):
    """What a form task asks: its declared schema, so a person can answer it."""
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    return _json(action_services(request).tasks.form(db, current, _id(task_id)))


def _mutation(request, db, project_id, environment_id, task_id):
    current = _person(db, request, project_id, environment_id, "review")
    return current, _id(task_id), action_services(request).tasks


@router.post("/human-tasks/{task_id}/claim", tags=TAGS)
def claim_human_task(
    project_id: str, environment_id: str, task_id: str, request: Request, raw=BODY, db=DB
):
    current, task_id, tasks = _mutation(request, db, project_id, environment_id, task_id)
    EmptyRequest.model_validate(raw)
    view = tasks.claim(db, current, task_id)
    audit(db, current, "executions.tasks.claimed", task_id, execution_id=view.execution_id)
    return _json(view)


@router.post("/human-tasks/{task_id}/release", tags=TAGS)
def release_human_task(
    project_id: str, environment_id: str, task_id: str, request: Request, raw=BODY, db=DB
):
    current, task_id, tasks = _mutation(request, db, project_id, environment_id, task_id)
    EmptyRequest.model_validate(raw)
    view = tasks.release(db, current, task_id)
    audit(db, current, "executions.tasks.released", task_id, execution_id=view.execution_id)
    return _json(view)


@router.post("/human-tasks/{task_id}/decision", tags=TAGS)
def decide_human_task(
    project_id: str, environment_id: str, task_id: str, request: Request, raw=BODY, db=DB
):
    current, task_id, tasks = _mutation(request, db, project_id, environment_id, task_id)
    body = DecisionRequest.model_validate(raw)
    view = tasks.decide(db, current, task_id, body)
    audit(
        db,
        current,
        "executions.tasks.decided",
        task_id,
        body.decision_id,
        execution_id=view.execution_id,
        decision=body.decision,
    )
    return _json(view)


@router.post("/human-tasks/{task_id}/submission", tags=TAGS)
def submit_human_task(
    project_id: str, environment_id: str, task_id: str, request: Request, raw=BODY, db=DB
):
    current, task_id, tasks = _mutation(request, db, project_id, environment_id, task_id)
    body = SubmissionRequest.model_validate(raw)
    view = tasks.submit(db, current, task_id, body)
    audit(
        db,
        current,
        "executions.tasks.submitted",
        task_id,
        body.submission_id,
        execution_id=view.execution_id,
    )
    return _json(view)
