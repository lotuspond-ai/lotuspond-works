"""HTTP boundary of the execution APIs (Task 12): wiring, limits, errors and DTO helpers.

Execution routes are part of the opt-in fleet API, so they answer 404 while
`fleet_enabled` is off, as E03's routes do. Services are constructed outside the
app and injected only in test mode. Without them (production today) a route that
needs one refuses with 503 `authority_unavailable`, while database-only reads
still answer. Task 14 supplies the production providers.

Errors are closed codes, `{"error": code, "message": code}` with no-store; caller,
provider and database text never escapes.
"""

import re
from dataclasses import dataclass, fields

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import OpaqueId, Scope
from lotuspond_sdk.executable.input import JsonLimits, decode_executable_bytes, project_json
from pydantic import TypeAdapter, ValidationError

from ..models import AuditEvent
from ..security import authenticate
from .authorization import authorize_execution, replayed
from .errors import ExecutionError

# Customer execution paths under a project environment.
CUSTOMER_PATH = re.compile(
    r"/api/v1/projects/[^/]+/environments/[^/]+/"
    r"(?:executable-packages|deployments|executions|context-sources|artifacts)(?:/|$)"
)
MAX_BODY_BYTES = 1048576
MAX_HEADER_BYTES = 32768
BODY_LIMITS = JsonLimits(MAX_BODY_BYTES, 32, 100000)
SINGLE_HEADERS = (
    b"authorization",
    b"cookie",
    b"x-lotuspond-workspace",
    b"idempotency-key",
    b"content-type",
    b"content-length",
    b"content-encoding",
)
STATUS = {
    "invalid_request": 400,
    "unsupported_step": 400,
    "unsupported_policy": 400,
    "command_rejected": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "budget_exceeded": 409,
    "input_limit": 413,
    "capacity_unavailable": 429,
    "rate_limited": 429,
}
# The key scope a session or key presents for each operation; `authorize_execution`
# then decides under current locks (keys have none for manage, human forms or
# reconciliation, so those always refuse a key).
KEY_SCOPE = {
    "read": "read",
    "publish": "write",
    "manage": "write",
    "execute": "execute",
    "control": "execute",
    "signal": "execution_signal",
    "human_form": "write",
    "reconcile": "write",
}


def status_of(code: str) -> int:
    return STATUS.get(code, 503)


def error_response(code: str) -> JSONResponse:
    return JSONResponse(
        {"error": code, "message": code},
        status_code=status_of(code),
        headers={"Cache-Control": "no-store"},
    )


@dataclass(frozen=True)
class ExecutionServices:
    """Constructed execution services the API calls; none is built from settings."""

    artifacts: object
    artifact_policy: object
    budgets: object
    dependencies: object
    admission: object
    inbox: object
    controls: object
    activities: object
    leases: object
    reconciliation: object
    context: object

    def __post_init__(self):
        if any(getattr(self, item.name) is None for item in fields(self)):
            raise ValueError("Every execution service is required")


class ExecutionRequestBoundary:
    """Raw-header and streaming budgets for customer execution paths, before decoding."""

    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not CUSTOMER_PATH.match(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def refuse(code):
            await error_response(code)(scope, receive, send)

        if not self.settings.fleet_enabled:
            await refuse("not_found")
            return
        headers = {}
        for name, value in scope["headers"]:
            headers.setdefault(name.lower(), []).append(value)
        if sum(len(k) + len(v) for k, v in scope["headers"]) > MAX_HEADER_BYTES:
            await refuse("input_limit")
            return
        if any(len(headers.get(name, [])) > 1 for name in SINGLE_HEADERS):
            await refuse("invalid_request")
            return
        # Runtime credentials authenticate only the runtime routes.
        if (
            b"x-lotuspond-runtime-session" in headers
            or b"," in headers.get(b"authorization", [b""])[0]
        ):
            await refuse("unauthenticated")
            return
        if headers.get(b"content-encoding", [b"identity"])[0].lower() != b"identity":
            await refuse("invalid_request")
            return
        mutation = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        if mutation and (
            headers.get(b"content-type", [b""])[0].split(b";")[0].strip().lower()
            != b"application/json"
        ):
            await refuse("invalid_request")
            return
        try:
            length = int(headers.get(b"content-length", [b"0"])[0])
        except ValueError:
            await refuse("invalid_request")
            return
        if length < 0:
            await refuse("invalid_request")
            return
        if length > MAX_BODY_BYTES:
            await refuse("input_limit")
            return
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > MAX_BODY_BYTES:
                await refuse("input_limit")
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


class ExecutionRoute(APIRoute):
    """Closed errors for every execution route, customer or runtime."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except (ExecutionError, FleetError) as error:
                raise HTTPException(
                    status_of(error.code),
                    {"error": error.code, "message": error.code},
                    headers={"Cache-Control": "no-store"},
                ) from None
            except ValidationError:
                raise HTTPException(
                    400,
                    {"error": "invalid_request", "message": "invalid_request"},
                    headers={"Cache-Control": "no-store"},
                ) from None

        return handle


def services(request: Request) -> ExecutionServices:
    configured = request.app.state.execution_services
    if configured is None:
        raise ExecutionError("authority_unavailable")
    return configured


def json_body(content: bytes) -> object:
    """Strictly decoded JSON (no duplicate keys or nonfinite numbers), bounded."""
    try:
        return project_json(decode_executable_bytes(content), limits=BODY_LIMITS)
    except Exception:
        raise ExecutionError("invalid_request") from None


def request_key(request: Request) -> str:
    try:
        return TypeAdapter(OpaqueId).validate_python(
            request.headers.get("idempotency-key"), strict=True
        )
    except Exception:
        raise ExecutionError("invalid_request") from None


def page(request: Request, cursor: str = "after_id") -> tuple[int, str | None]:
    """Keyset paging: `limit` 1..100 (default 50) and one cursor; nothing else."""
    allowed = {"limit", cursor}
    if any(
        name not in allowed or len(request.query_params.getlist(name)) != 1
        for name in request.query_params
    ):
        raise ExecutionError("invalid_request")
    limit = request.query_params.get("limit", "50")
    if re.fullmatch(r"[1-9][0-9]{0,2}", limit) is None or int(limit) > 100:
        raise ExecutionError("invalid_request")
    return int(limit), request.query_params.get(cursor)


def no_query(request: Request) -> None:
    if request.query_params:
        raise ExecutionError("invalid_request")


def scope_for(principal, project_id: str, environment_id: str) -> Scope:
    """The path's environment in the authenticated workspace, never one named by a header.

    `authorize_execution` then refuses (`not_found`) an environment outside it.
    """
    try:
        return Scope(
            workspace_id=principal.workspace_id,
            project_id=project_id,
            environment_id=environment_id,
        )
    except ValidationError:
        raise ExecutionError("not_found") from None


def _closed(error: HTTPException):
    """A shared authentication or quota refusal as a closed code; outages stay outages."""
    if error.status_code == 401:
        return FleetError("unauthenticated")  # a fleet code; execution errors have none
    if error.status_code == 429:
        return FleetError("rate_limited")
    if error.status_code >= 500:
        return ExecutionError("authority_unavailable")
    return ExecutionError("forbidden")


def actor(db, request: Request, project_id: str, environment_id: str, operation: str):
    """Authenticate the session or key, then pre-check the operation without locks.

    The check holds no lock and opens no second connection: every service
    reauthorizes under its own lock order (the installation cursor and provider
    lookups before any account row), and a read is authorized lock-free anyway.
    Reads and writes have their own per-workspace allowance.
    """
    try:
        principal = authenticate(db, request, KEY_SCOPE[operation])
        kind = "read" if operation == "read" else "write"
        request.app.state.limiter.check(
            f"workspace:{principal.workspace_id}:execution-{kind}", 1000
        )
    except HTTPException as error:
        raise _closed(error) from None
    scope = scope_for(principal, project_id, environment_id)
    return authorize_execution(db, principal, scope, operation, locked=False)


def audit(db, actor, name: str, resource_id: str | None, request_id=None, **details) -> None:
    """Fixed identifiers and states only; values, payloads and text are never recorded.

    A mutation records its request's identifier (its idempotency key, control
    request or signal ID). An equal retry, which the service answered by replaying
    the first answer (`mark_replay`), is not audited again; nothing is looked up.
    """
    if replayed(db):
        return
    if request_id is not None:
        details["request_id"] = request_id
    db.add(
        AuditEvent(
            workspace_id=actor.scope.workspace_id,
            actor_id=actor.api_key_id or actor.user_id,
            action=name,
            resource_id=resource_id,
            details={
                "project_id": actor.scope.project_id,
                "environment_id": actor.scope.environment_id,
                **details,
            },
        )
    )
    db.flush()
