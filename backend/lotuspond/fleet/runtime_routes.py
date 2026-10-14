"""Private one-time responses and runtime-only authentication boundary."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    EmptyManagementRequest,
    EnrollmentList,
    InstanceList,
    RuntimeObservation,
    encode_acquired_session,
    encode_enrollment_issued,
    encode_issued_credential,
    encode_prepared_credential,
)

from ..deps import DB
from ..models import RuntimeEnrollment, RuntimeInstance, RuntimePool
from ..security import Principal
from .authentication import authenticate_runtime
from .identity import (
    IdentityManagement,
    acquire_session,
    enrollment_view,
    exchange_enrollment,
    heartbeat,
    instance_view,
    issue_enrollment,
    self_state,
)
from .management import checked_scope, record
from .management_routes import (
    ADMIN,
    BODY,
    FleetRoute,
    inventory,
    page_request,
    request_key,
    service,
)
from .management_routes import (
    router as management_router,
)
from .rotation import activate_rotation, cancel_rotation, prepare_rotation

router = APIRouter(prefix="/api/v1/runtime", route_class=FleetRoute)


def dependencies(request):
    result = request.app.state.fleet_dependencies
    if result.catalog.test_only and request.app.state.settings.env != "test":
        raise FleetError("authority_unavailable")
    return result


def bearer(request):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise FleetError("unauthenticated")
    return header[7:]


def principal(request, db, *, require_session=False):
    if request.query_params:
        raise FleetError("invalid_request")
    return authenticate_runtime(
        db,
        bearer(request),
        session_secret=request.headers.get("x-lotuspond-runtime-session"),
        require_session=require_session,
        dependencies=dependencies(request),
    )


def one_time(value):
    return JSONResponse(value, status_code=201, headers={"Cache-Control": "no-store"})


@router.post("/enroll")
def enroll(request: Request, raw=BODY, db=DB):
    deps = dependencies(request)
    token = bearer(request)
    if request.headers.get("x-lotuspond-runtime-session") is not None or request.query_params:
        raise FleetError("unauthenticated")
    result = exchange_enrollment(db, token, RuntimeObservation.model_validate(raw), deps)
    return one_time(encode_issued_credential(result))


@router.get("/self")
def self_route(request: Request, db=DB):
    return self_state(db, principal(request, db), dependencies(request))


@router.post("/session")
def session_route(request: Request, raw=BODY, db=DB):
    EmptyManagementRequest.model_validate(raw)
    result = acquire_session(db, principal(request, db), dependencies(request))
    return one_time(encode_acquired_session(result))


@router.post("/heartbeat")
def heartbeat_route(request: Request, raw=BODY, db=DB):
    EmptyManagementRequest.model_validate(raw)
    return heartbeat(db, principal(request, db, require_session=True), dependencies(request))


@router.post("/credentials/prepare")
def prepare_route(request: Request, raw=BODY, db=DB):
    EmptyManagementRequest.model_validate(raw)
    result = prepare_rotation(
        db, principal(request, db, require_session=True), dependencies(request)
    )
    return one_time(encode_prepared_credential(result))


@router.post("/credentials/activate")
def activate_route(request: Request, raw=BODY, db=DB):
    EmptyManagementRequest.model_validate(raw)
    if request.query_params:
        raise FleetError("invalid_request")
    return activate_rotation(
        db,
        bearer(request),
        request.headers.get("x-lotuspond-runtime-session"),
        dependencies(request),
    )


@router.post("/credentials/cancel")
def cancel_route(request: Request, raw=BODY, db=DB):
    EmptyManagementRequest.model_validate(raw)
    return cancel_rotation(db, principal(request, db, require_session=True), dependencies(request))


@management_router.post("/runtime-pools/{pool_id}/enrollments")
def issue(
    project_id: str,
    environment_id: str,
    pool_id: str,
    request: Request,
    raw=BODY,
    db=DB,
    human: Principal = ADMIN,
):
    scope = checked_scope(db, human, project_id, environment_id)
    result = issue_enrollment(
        db, human, scope, pool_id, RuntimeObservation.model_validate(raw), dependencies(request)
    )
    return one_time(encode_enrollment_issued(result))


@management_router.get("/runtime-pools/{pool_id}/enrollments")
def enrollments(
    project_id: str,
    environment_id: str,
    pool_id: str,
    request: Request,
    db=DB,
    human: Principal = ADMIN,
):
    scope = checked_scope(db, human, project_id, environment_id)
    record(db, RuntimePool, scope, pool_id)
    return inventory(
        db,
        scope,
        RuntimeEnrollment,
        page_request(request),
        enrollment_view,
        EnrollmentList,
        RuntimeEnrollment.pool_id == pool_id,
    )


@management_router.post("/runtime-pools/{pool_id}/enrollments/{enrollment_id}/revoke")
def revoke_enrollment(
    project_id: str,
    environment_id: str,
    pool_id: str,
    enrollment_id: str,
    request: Request,
    raw=BODY,
    db=DB,
    human: Principal = ADMIN,
):
    scope = checked_scope(db, human, project_id, environment_id)
    EmptyManagementRequest.model_validate(raw)
    return service(request, db, human, IdentityManagement).revoke_enrollment(
        scope, pool_id, enrollment_id, request_key(request)
    )


@management_router.get("/runtime-instances")
def instances(
    project_id: str, environment_id: str, request: Request, db=DB, human: Principal = ADMIN
):
    scope = checked_scope(db, human, project_id, environment_id)
    return inventory(
        db,
        scope,
        RuntimeInstance,
        page_request(request),
        lambda row: instance_view(db, row),
        InstanceList,
    )


@management_router.get("/runtime-instances/{instance_id}")
def instance(
    project_id: str, environment_id: str, instance_id: str, db=DB, human: Principal = ADMIN
):
    scope = checked_scope(db, human, project_id, environment_id)
    return instance_view(db, record(db, RuntimeInstance, scope, instance_id))


@management_router.post("/runtime-instances/{instance_id}/revoke")
def revoke_instance(
    project_id: str,
    environment_id: str,
    instance_id: str,
    request: Request,
    raw=BODY,
    db=DB,
    human: Principal = ADMIN,
):
    scope = checked_scope(db, human, project_id, environment_id)
    EmptyManagementRequest.model_validate(raw)
    return service(request, db, human, IdentityManagement).revoke_instance(
        scope, instance_id, request_key(request)
    )


@router.post("/commands/poll")
def poll_route(request: Request, raw=BODY, db=DB):
    from fastapi.responses import Response
    from lotuspond_runtime.commands import delivery_wire

    EmptyManagementRequest.model_validate(raw)
    current = principal(request, db, require_session=True)
    delivery = request.app.state.command_service.poll(db, current)
    return Response(
        delivery_wire(delivery) if delivery is not None else b"null",
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/commands/{command_id}/claim")
def claim_route(command_id: str, request: Request, raw=BODY, db=DB):
    from lotuspond_runtime.protocol import CommandClaimRequest

    body = CommandClaimRequest.model_validate(raw)
    return request.app.state.command_service.claim(
        db, principal(request, db, require_session=True), command_id, body.command_sha256
    )
