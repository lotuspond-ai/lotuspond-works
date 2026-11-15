"""Case routes (E06, second part), under E04 Task 12's conventions.

A case's key is customer data held encrypted, so every route needs the artifact store
of the composed execution services (503 without them). Creating a case and linking an
execution need `execute`; ownership and resolution are a person's, by session, with
`review` (a key is refused before any lock). Each mutation is audited with identifiers
only; an equal retry the service replayed is not audited again.
"""

from fastapi import APIRouter, Request

from ..deps import DB
from .boundary import ExecutionRoute, actor, audit, no_query, page, services
from .cases import CaseService
from .errors import ExecutionError
from .routes import BODY, _id

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/environments/{environment_id}",
    route_class=ExecutionRoute,
)
TAGS = ["production cases"]


def _cases(request) -> CaseService:
    configured = services(request)
    return CaseService(artifacts=configured.artifacts, artifact_policy=configured.artifact_policy)


def _person(db, request, project_id, environment_id):
    current = actor(db, request, project_id, environment_id, "review")
    if current.api_key_id is not None or current.user_id is None:
        raise ExecutionError("forbidden")
    return current


@router.post("/cases", tags=TAGS)
def create_case(project_id: str, environment_id: str, request: Request, raw=BODY, db=DB):
    """The case named by this business key, created if it does not exist yet."""
    current = actor(db, request, project_id, environment_id, "execute")
    view = _cases(request).create(db, current, raw)
    audit(db, current, "executions.cases.created", view["id"])
    return view


@router.get("/cases", tags=TAGS)
def list_cases(project_id: str, environment_id: str, request: Request, db=DB):
    limit, after = page(request)
    current = actor(db, request, project_id, environment_id, "read")
    after = None if after is None else _id(after)
    return _cases(request).list(db, current, after_id=after, limit=limit)


@router.get("/cases/{case_id}", tags=TAGS)
def get_case(project_id: str, environment_id: str, case_id: str, request: Request, db=DB):
    no_query(request)
    current = actor(db, request, project_id, environment_id, "read")
    return _cases(request).get(db, current, _id(case_id))


@router.post("/cases/{case_id}/executions", tags=TAGS)
def link_case_execution(
    project_id: str, environment_id: str, case_id: str, request: Request, raw=BODY, db=DB
):
    current = actor(db, request, project_id, environment_id, "execute")
    view = _cases(request).link(db, current, _id(case_id), raw)
    audit(
        db,
        current,
        "executions.cases.linked",
        view["id"],
        execution_id=raw.get("execution_id") if isinstance(raw, dict) else None,
    )
    return view


@router.post("/cases/{case_id}/owner", tags=TAGS)
def change_case_owner(
    project_id: str, environment_id: str, case_id: str, request: Request, raw=BODY, db=DB
):
    current = _person(db, request, project_id, environment_id)
    view = _cases(request).owner(db, current, _id(case_id), raw)
    audit(
        db,
        current,
        "executions.cases.owner",
        view["id"],
        raw.get("request_id"),
        action=raw.get("action"),
    )
    return view


@router.post("/cases/{case_id}/resolution", tags=TAGS)
def resolve_case(
    project_id: str, environment_id: str, case_id: str, request: Request, raw=BODY, db=DB
):
    current = _person(db, request, project_id, environment_id)
    view = _cases(request).resolve(db, current, _id(case_id), raw)
    audit(
        db,
        current,
        "executions.cases.resolved",
        view["id"],
        raw.get("request_id"),
        kind=raw.get("kind"),
    )
    return view
