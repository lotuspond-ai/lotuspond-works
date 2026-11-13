"""Runtime action routes (E05 Task 11): runtime credential and session only.

`POST /api/v1/runtime/actions/{poll,records,claim,renew,receipts,stops}` and
`/actions/{attempt_id}/values`, the paths runtime 0.3.0's `ActionClient` calls and
its control transport allows (session only). The plan named the receipt and stop
paths in the singular; the shipped client's plural paths are kept.

E03's `FleetRequestBoundary` applies to every `/api/v1/runtime/` path: the opt-in
switch, single authority headers, JSON bodies and the 64 KiB body bound. Each route
refuses an unknown credential or session by plain reads before any lock, then hands
the runtime it names (`runtime_hint`, which grants nothing) to the dispatcher. The
dispatcher takes the fair gates first and authenticates under E03's locks, before
any execution row, and locks environment capacity where a count changes. No route
takes the installation cursor, as E04's result routes do after finding 11.
"""

import json

from fastapi import APIRouter, Depends, Request, Response
from lotuspond_runtime.commands import delivery_wire
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    CanonicalUUID,
    Digest,
    EmptyManagementRequest,
    decode_fleet_json,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import select

from ..deps import DB
from ..fleet.authentication import runtime_hint, token_digest
from ..fleet.authority import database_now_ms
from ..fleet.runtime_routes import bearer
from ..models import RuntimeCredential, RuntimeSession
from .action_contracts import ActionLease, ActionReport, ActionStop
from .boundary import ExecutionRoute, action_services

router = APIRouter(prefix="/api/v1/runtime", route_class=ExecutionRoute)
# A delivery's wire form: its two documents of at most 64 KiB, JSON-escaped.
MAX_DELIVERY_BYTES = 131072


class ActionClaim(BaseModel):
    """Names one admitted command by its exact canonical digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    command_id: CanonicalUUID
    command_sha256: Digest


async def _decoded(request: Request):
    return decode_fleet_json(await request.body())


async def _raw(request: Request) -> bytes:
    return await request.body()


BODY, RAW = Depends(_decoded), Depends(_raw)


def _json(value) -> Response:
    content = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return Response(content.encode("utf-8"), media_type="application/json")


def _presented(request, db) -> None:
    """Refuse an unknown, ended or expired credential or session by plain reads,
    before any lock (E04's `_presented`, with database-time expiry). A poll with
    nothing to hand out authenticates no further, so an ended session learns it
    here, rather than by polling `null`."""
    secret = request.headers.get("x-lotuspond-runtime-session")
    if secret is None:
        raise FleetError("unauthenticated")
    try:
        token, session = token_digest(bearer(request), "lpr_"), token_digest(secret, "lps_")
    except FleetError:
        raise
    except Exception:
        raise FleetError("unauthenticated") from None
    now = database_now_ms(db)
    credential = db.scalar(
        select(RuntimeCredential).where(
            RuntimeCredential.token_digest == token,
            RuntimeCredential.status == "active",
            RuntimeCredential.expires_at_ms > now,
        )
    )
    if credential is None or not db.scalar(
        select(RuntimeSession.id).where(
            RuntimeSession.instance_id == credential.instance_id,
            RuntimeSession.token_digest == session,
            RuntimeSession.status == "active",
            RuntimeSession.expires_at_ms > now,
        )
    ):
        raise FleetError("unauthenticated")


def _runtime(request, db):
    """The runtime the request names and the dispatcher, before any lock.

    Authentication first, so an unauthenticated caller learns nothing about what
    this installation composes."""
    if request.query_params:
        raise FleetError("invalid_request")
    _presented(request, db)
    hint = runtime_hint(db, bearer(request), request.headers.get("x-lotuspond-runtime-session"))
    if hint is None or hint.session_id is None:
        raise FleetError("unauthenticated")
    return action_services(request).dispatcher, hint


@router.post("/actions/poll")
def poll_actions(request: Request, raw=BODY, db=DB):
    dispatcher, current = _runtime(request, db)
    EmptyManagementRequest.model_validate(raw)
    delivery = dispatcher.poll(db, current)
    if delivery is None:
        return Response(b"null", media_type="application/json")
    content = delivery_wire(delivery)
    if len(content) > MAX_DELIVERY_BYTES:
        raise FleetError("input_limit")  # unreachable under the command's document bounds
    return Response(content, media_type="application/json")


@router.post("/actions/records")
def action_records(request: Request, raw=BODY, db=DB):
    from lotuspond_runtime.action_protocol import encode_records

    dispatcher, current = _runtime(request, db)
    body = ActionClaim.model_validate(raw)
    records = dispatcher.records(db, current, body.command_id, body.command_sha256)
    return _json(encode_records(records))


@router.post("/actions/claim")
def claim_action(request: Request, raw=BODY, db=DB):
    dispatcher, current = _runtime(request, db)
    body = ActionClaim.model_validate(raw)
    lease = dispatcher.claim(db, current, body.command_id, body.command_sha256)
    return _json(lease.model_dump(mode="json"))


@router.post("/actions/renew")
def renew_action(request: Request, raw=BODY, db=DB):
    dispatcher, current = _runtime(request, db)
    lease = dispatcher.renew(db, current, ActionLease.model_validate(raw))
    return _json(lease.model_dump(mode="json"))


@router.post("/actions/receipts")
def record_action_receipt(request: Request, raw=BODY, db=DB):
    dispatcher, current = _runtime(request, db)
    dispatcher.record_receipt(db, current, ActionReport.model_validate(raw))
    return _json({})


@router.post("/actions/stops")
def record_action_stop(request: Request, raw=BODY, db=DB):
    dispatcher, current = _runtime(request, db)
    dispatcher.record_stop(db, current, ActionStop.model_validate(raw))
    return _json({})


@router.post("/actions/{attempt_id}/values")
def store_action_value(attempt_id: str, request: Request, content=RAW, db=DB):
    attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
    dispatcher, current = _runtime(request, db)
    handle = dispatcher.store_value(db, current, attempt_id, content)
    return _json(handle.model_dump(mode="json"))
