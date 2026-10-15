"""Owner/admin management; the machine identity boundary is separate."""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    ConnectionConfig,
    ConnectionList,
    ConnectionVersionList,
    EmptyManagementRequest,
    ManagementNameRequest,
    ManagementPageRequest,
    ManagementStatusRequest,
    OpaqueId,
    PoolList,
    PoolPolicy,
    PoolPolicyList,
    SecretReferenceList,
    SecretRegistrationRequest,
    decode_fleet_json,
)
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..deps import DB, require
from ..models import (
    Connection,
    ConnectionVersion,
    RuntimePool,
    RuntimePoolPolicy,
    SecretReference,
    Workspace,
)
from ..security import Principal, authenticate, require_admin
from .management import (
    ConnectionService,
    PoolService,
    checked_scope,
    connection_view,
    policy_view,
    pool_view,
    record,
    scoped,
    secret_view,
    stored_config,
    stored_policy,
    version_view,
)

MANAGEMENT_PATH = re.compile(
    r"/api/v1/projects/[^/]+/environments/[^/]+/"
    r"(?:connections|secret-references|runtime-pools|runtime-instances)(?:/|$)"
)
ERROR_STATUS = {
    "invalid_request": 400,
    "command_rejected": 400,
    "input_limit": 413,
    "unauthenticated": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "rate_limited": 429,
}


class FleetRequestBoundary:
    """Apply fleet-only raw-header and streaming budgets before generic decoding."""

    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        runtime = scope["type"] == "http" and scope["path"].startswith("/api/v1/runtime/")
        if scope["type"] != "http" or not (runtime or MANAGEMENT_PATH.match(scope["path"])):
            await self.app(scope, receive, send)
            return

        async def error(code):
            await JSONResponse(
                {"error": code, "message": code},
                status_code=ERROR_STATUS.get(code, 503),
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        if not self.settings.fleet_enabled:
            await error("not_found")
            return
        if runtime and scope["path"] == "/api/v1/runtime/enroll":
            from .authentication import token_digest

            dependencies = scope["app"].state.fleet_dependencies
            try:
                peer = scope.get("client")
                await run_in_threadpool(
                    dependencies.limit, "exchange:peer:" + (peer[0] if peer else "unknown"), 20
                )
                # Charge recognizable proof before decoding or rejecting bodies.
                auth_headers = [v for k, v in scope["headers"] if k.lower() == b"authorization"]
                if len(auth_headers) == 1 and len(auth_headers[0]) == 54:
                    try:
                        header = auth_headers[0].decode("ascii")
                        digest = token_digest(
                            header[7:] if header.startswith("Bearer ") else "", "lpe_"
                        )
                    except (UnicodeError, FleetError):
                        pass
                    else:
                        await run_in_threadpool(dependencies.limit, "exchange:token:" + digest, 5)
            except FleetError as failure:
                await error(failure.code)
                return
        headers = {}
        for name, value in scope["headers"]:
            headers.setdefault(name.lower(), []).append(value)
        for name in (
            b"authorization",
            b"x-lotuspond-workspace",
            b"x-lotuspond-runtime-session",
            b"idempotency-key",
            b"content-type",
            b"content-encoding",
            b"content-length",
        ):
            if len(headers.get(name, [])) > 1:
                await error("invalid_request")
                return
        if sum(len(k) + len(v) for k, v in scope["headers"]) > 32768:
            await error("input_limit")
            return
        authorization = headers.get(b"authorization", [b""])[0]
        if (
            b"," in authorization
            or (not runtime and b"x-lotuspond-runtime-session" in headers)
            or b"," in headers.get(b"x-lotuspond-runtime-session", [b""])[0]
        ):
            await error("unauthenticated")
            return
        if headers.get(b"content-encoding", [b"identity"])[0].lower() != b"identity":
            await error("invalid_request")
            return
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and (
            headers.get(b"content-type", [b""])[0].split(b";")[0].strip().lower()
            != b"application/json"
        ):
            await error("invalid_request")
            return
        try:
            length = int(headers.get(b"content-length", [b"0"])[0])
        except ValueError:
            await error("invalid_request")
            return
        if length < 0:
            await error("invalid_request")
            return
        if length > 65536:
            await error("input_limit")
            return
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > 65536:
                await error("input_limit")
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)


class FleetRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except FleetError as error:
                raise HTTPException(
                    ERROR_STATUS.get(error.code, 503), {"error": error.code, "message": error.code}
                ) from None
            except ValidationError:
                raise HTTPException(
                    400, {"error": "invalid_request", "message": "invalid_request"}
                ) from None

        return handle


router = APIRouter(
    prefix="/api/v1/projects/{project_id}/environments/{environment_id}", route_class=FleetRoute
)


def require_fleet_admin(
    request: Request, db: Session = DB, principal: Principal = Depends(require("write"))
) -> Principal:
    require_admin(principal)
    # Authenticate again after the shared parent lock: a request-key replay does
    # not bypass membership, workspace, verification or MFA changes while waiting.
    db.scalar(select(Workspace).where(Workspace.id == principal.workspace_id).with_for_update())
    db.expire_all()
    principal = authenticate(db, request, "write")
    require_admin(principal)
    return principal


ADMIN = Depends(require_fleet_admin)


def service(request, db, principal, cls):
    catalog = request.app.state.fleet_dependencies.catalog
    if catalog.test_only and request.app.state.settings.env != "test":
        raise FleetError("authority_unavailable")
    return cls(db, catalog=catalog, actor_id=principal.actor_id)


async def body(request: Request):
    return decode_fleet_json(await request.body())


BODY = Depends(body)


def request_key(request):
    return TypeAdapter(OpaqueId).validate_python(
        request.headers.get("idempotency-key"), strict=True
    )


def page_request(request):
    allowed = {"limit", "after_id"}
    if any(
        key not in allowed or len(request.query_params.getlist(key)) != 1
        for key in request.query_params
    ):
        raise FleetError("invalid_request")
    limit = request.query_params.get("limit", "50")
    if re.fullmatch(r"[1-9][0-9]{0,2}", limit) is None:
        raise FleetError("invalid_request")
    return ManagementPageRequest(limit=int(limit), after_id=request.query_params.get("after_id"))


def inventory(db, scope, model, page, view, wrapper, *predicates):
    query = scoped(model, scope).where(*predicates)
    if page.after_id is not None:
        # Scope the cursor to this collection/parent; foreign IDs reveal nothing.
        cursor = db.scalar(scoped(model, scope).where(model.id == page.after_id, *predicates))
        if cursor is None:
            raise FleetError("not_found")
        query = query.where(model.id > page.after_id)
    rows = list(db.scalars(query.order_by(model.id).limit(page.limit + 1)))
    items = [view(row) for row in rows[: page.limit]]
    return wrapper(
        items=items, next_after_id=rows[page.limit - 1].id if len(rows) > page.limit else None
    )


@router.post("/connections", status_code=201)
def create_connection(
    project_id: str,
    environment_id: str,
    request: Request,
    raw=BODY,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    item = ManagementNameRequest.model_validate(raw)
    return service(request, db, principal, ConnectionService).create(
        scope, item.name, request_key(request)
    )


@router.get("/connections")
def list_connections(
    project_id: str,
    environment_id: str,
    request: Request,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    return inventory(
        db,
        scope,
        Connection,
        page_request(request),
        lambda row: connection_view(db, scope, row),
        ConnectionList,
    )


@router.get("/connections/{connection_id}")
def get_connection(
    project_id: str,
    environment_id: str,
    connection_id: str,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    return connection_view(db, scope, record(db, Connection, scope, connection_id))


@router.post("/connections/{connection_id}/status")
def connection_status(
    project_id: str,
    environment_id: str,
    connection_id: str,
    request: Request,
    raw=BODY,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    item = ManagementStatusRequest.model_validate(raw)
    return service(request, db, principal, ConnectionService).set_status(
        scope, connection_id, item.status, request_key(request)
    )


@router.post("/connections/{connection_id}/versions", status_code=201)
def publish_connection(
    project_id: str,
    environment_id: str,
    connection_id: str,
    request: Request,
    raw=BODY,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    return service(request, db, principal, ConnectionService).publish(
        scope, connection_id, ConnectionConfig.model_validate(raw), request_key(request)
    )


@router.get("/connections/{connection_id}/versions")
def list_connection_versions(
    project_id: str,
    environment_id: str,
    connection_id: str,
    request: Request,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    record(db, Connection, scope, connection_id)

    def view(row):
        stored_config(db, scope, row)
        return version_view(scope, row)

    return inventory(
        db,
        scope,
        ConnectionVersion,
        page_request(request),
        view,
        ConnectionVersionList,
        ConnectionVersion.connection_id == connection_id,
    )


@router.get("/connections/{connection_id}/versions/{version}")
def get_connection_version(
    project_id: str,
    environment_id: str,
    connection_id: str,
    version: str,
    request: Request,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    if re.fullmatch(r"[1-9][0-9]{0,9}", version) is None or int(version) > 2147483647:
        raise FleetError("invalid_request")
    return service(request, db, principal, ConnectionService).detail(
        scope, connection_id, int(version)
    )


@router.post("/runtime-pools", status_code=201)
def create_pool(
    project_id: str,
    environment_id: str,
    request: Request,
    raw=BODY,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    item = ManagementNameRequest.model_validate(raw)
    return service(request, db, principal, PoolService).create(
        scope, item.name, request_key(request)
    )


@router.get("/runtime-pools")
def list_pools(
    project_id: str,
    environment_id: str,
    request: Request,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    return inventory(
        db,
        scope,
        RuntimePool,
        page_request(request),
        lambda row: pool_view(db, scope, row),
        PoolList,
    )


@router.get("/runtime-pools/{pool_id}")
def get_pool(
    project_id: str,
    environment_id: str,
    pool_id: str,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    return pool_view(db, scope, record(db, RuntimePool, scope, pool_id))


@router.post("/runtime-pools/{pool_id}/status")
def pool_status(
    project_id: str,
    environment_id: str,
    pool_id: str,
    request: Request,
    raw=BODY,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    item = ManagementStatusRequest.model_validate(raw)
    return service(request, db, principal, PoolService).set_status(
        scope, pool_id, item.status, request_key(request)
    )


@router.post("/runtime-pools/{pool_id}/policies", status_code=201)
def publish_policy(
    project_id: str,
    environment_id: str,
    pool_id: str,
    request: Request,
    raw=BODY,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    return service(request, db, principal, PoolService).publish(
        scope, pool_id, PoolPolicy.model_validate(raw), request_key(request)
    )


@router.get("/runtime-pools/{pool_id}/policies")
def list_pool_policies(
    project_id: str,
    environment_id: str,
    pool_id: str,
    request: Request,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    record(db, RuntimePool, scope, pool_id)

    def view(row):
        stored_policy(db, scope, row)
        return policy_view(scope, row)

    return inventory(
        db,
        scope,
        RuntimePoolPolicy,
        page_request(request),
        view,
        PoolPolicyList,
        RuntimePoolPolicy.pool_id == pool_id,
    )


@router.post("/secret-references", status_code=201)
def register_secret(
    project_id: str,
    environment_id: str,
    request: Request,
    raw=BODY,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    item = SecretRegistrationRequest.model_validate(raw)
    return service(request, db, principal, PoolService).register_secret(
        scope, item.binding_id, item.version, request_key(request)
    )


@router.get("/secret-references")
def list_secrets(
    project_id: str,
    environment_id: str,
    request: Request,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    return inventory(
        db,
        scope,
        SecretReference,
        page_request(request),
        lambda row: secret_view(scope, row),
        SecretReferenceList,
    )


@router.post("/secret-references/{reference_id}/revoke")
def revoke_secret(
    project_id: str,
    environment_id: str,
    reference_id: str,
    request: Request,
    raw=BODY,
    db: Session = DB,
    principal: Principal = ADMIN,
):
    scope = checked_scope(db, principal, project_id, environment_id)
    EmptyManagementRequest.model_validate(raw)
    return service(request, db, principal, PoolService).revoke_secret(
        scope, reference_id, request_key(request)
    )
