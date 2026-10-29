"""Runtime activity routes (Task 12): authenticated by runtime credential and session only.

Task 9 fixed these paths, and Task 10's client calls them:
`POST /api/v1/runtime/activities/{poll,claim,renew,children,results,decline}` and
`/activities/{attempt_id}/values`. A runtime's reads of its own work live
separately under `/api/v1/runtime/activity-artifacts`. They serve the pinned
records of its delivery, and the declarations, input and pinned context of an
attempt it holds; never anything else.

E03's `FleetRequestBoundary` already applies to every `/api/v1/runtime/` path: the
opt-in switch, single authority headers, JSON bodies and the 64 KiB body bound
(the runtime transport's). Routes for results, declines and child requests refuse
an unknown credential or session before any lock; no route takes the installation
cursor (docs/superpowers/plans/2026-10-29-installation-capacity-lock.md).
"""

import json

from fastapi import APIRouter, Depends, Request, Response
from lotuspond_runtime.activity_protocol import (
    ActivityClaim,
    ActivityLease,
    ActivityResult,
    ChildActivityRequest,
    ChildActivityView,
)
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import CanonicalUUID, decode_fleet_json
from lotuspond_sdk.activity import ArtifactHandle, canonical_activity_json
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import select

from ..deps import DB
from ..fleet.authentication import token_digest
from ..fleet.runtime_routes import bearer, principal
from ..models import RuntimeCredential, RuntimeSession
from .activity_reads import ActivityReads
from .boundary import ExecutionRoute, services

router = APIRouter(prefix="/api/v1/runtime", route_class=ExecutionRoute)
# A delivery's wire form: its two documents of at most 64 KiB, JSON-escaped.
MAX_DELIVERY_BYTES = 131072


class PollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parent_attempt_id: CanonicalUUID | None = None


class ContextRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: CanonicalUUID
    version: int = Field(ge=1, le=2147483647)


def _json(value) -> Response:
    content = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return Response(content.encode("utf-8"), media_type="application/json")


def _bytes(content: bytes) -> Response:
    return Response(content, media_type="application/json")


async def _decoded(request: Request):
    return decode_fleet_json(await request.body())


async def _raw(request: Request) -> bytes:
    return await request.body()


BODY, RAW = Depends(_decoded), Depends(_raw)


def _presented(request, db) -> None:
    """Refuse an unknown credential or session before any lock (Task 12 review).

    A plain look-up takes no row lock, so unauthenticated traffic never queues on
    a tenant's workspace row; the locked authentication after it still decides.
    """
    secret = request.headers.get("x-lotuspond-runtime-session")
    if secret is None:
        raise FleetError("unauthenticated")
    credential = db.scalar(
        select(RuntimeCredential).where(
            RuntimeCredential.token_digest == token_digest(bearer(request), "lpr_"),
            RuntimeCredential.status == "active",
        )
    )
    if credential is None or not db.scalar(
        select(RuntimeSession.id).where(
            RuntimeSession.instance_id == credential.instance_id,
            RuntimeSession.token_digest == token_digest(secret, "lps_"),
            RuntimeSession.status == "active",
        )
    ):
        raise FleetError("unauthenticated")


def _runtime(request, db, *, presented=False):
    configured = services(request)
    if presented:
        _presented(request, db)
    return configured, principal(request, db, require_session=True)


def _reads(configured) -> ActivityReads:
    return ActivityReads(configured.activities, configured.context)


@router.post("/activities/poll")
def poll(request: Request, raw=BODY, db=DB):
    configured, current = _runtime(request, db)
    body = PollRequest.model_validate(raw)
    delivery = configured.activities.poll_activity(db, current, body.parent_attempt_id)
    if delivery is None:
        return _bytes(b"null")
    content = json.dumps(delivery.model_dump(), separators=(",", ":"), sort_keys=True).encode()
    if len(content) > MAX_DELIVERY_BYTES:
        raise FleetError("input_limit")  # unreachable under Task 1's document bounds
    return _bytes(content)


@router.post("/activities/claim")
def claim(request: Request, raw=BODY, db=DB):
    configured, current = _runtime(request, db)
    body = ActivityClaim.model_validate(raw)
    lease = configured.activities.claim_activity(
        db, current, body.delivery_id, body.delivery_sha256
    )
    return _json(lease.model_dump(mode="json"))


@router.post("/activities/renew")
def renew(request: Request, raw=BODY, db=DB):
    configured, current = _runtime(request, db)
    body = ActivityLease.model_validate(raw)
    return _json(configured.leases.renew_activity(db, current, body).model_dump(mode="json"))


@router.post("/activities/children")
def children(request: Request, raw=BODY, db=DB):
    configured, current = _runtime(request, db, presented=True)
    body = ChildActivityRequest.model_validate(raw)
    attempt_id = configured.activities.request_child_activity(db, current, body)
    return _json(ChildActivityView(attempt_id=attempt_id).model_dump(mode="json"))


@router.post("/activities/results")
def results(request: Request, raw=BODY, db=DB):
    configured, current = _runtime(request, db, presented=True)
    body = ActivityResult.model_validate(raw)
    configured.activities.record_activity_result(db, current, body)
    return _json({})


@router.post("/activities/decline")
def decline(request: Request, raw=BODY, db=DB):
    configured, current = _runtime(request, db, presented=True)
    body = ActivityClaim.model_validate(raw)
    configured.leases.decline_activity(db, current, body.delivery_id, body.delivery_sha256)
    return _json({})


@router.post("/activities/{attempt_id}/values")
def values(attempt_id: str, request: Request, content=RAW, db=DB):
    attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
    configured, current = _runtime(request, db)
    handle = configured.activities.store_activity_value(db, current, attempt_id, content)
    return _json(handle.model_dump(mode="json"))


@router.post("/activity-artifacts/records")
def records(request: Request, raw=BODY, db=DB):
    configured, current = _runtime(request, db)
    body = ActivityClaim.model_validate(raw)
    pinned = _reads(configured).records(db, current, body.delivery_id, body.delivery_sha256)
    return _json(
        {
            "envelope": canonical_activity_json(pinned.envelope).decode("utf-8"),
            "connection": (
                None if pinned.connection is None else pinned.connection.model_dump(mode="json")
            ),
            "dependency": pinned.dependency.model_dump(mode="json"),
            "deadline_ms": pinned.deadline_ms,
            "input_schema": pinned.input_schema,
            "output_schema": pinned.output_schema,
            "request_sha256": pinned.request_sha256,
        }
    )


@router.post("/activity-artifacts/agent")
def agent(request: Request, raw=BODY, db=DB):
    configured, current = _runtime(request, db)
    body = ActivityClaim.model_validate(raw)
    return _json(_reads(configured).agent(db, current, body.delivery_id, body.delivery_sha256))


@router.post("/activity-artifacts/{attempt_id}/input")
def attempt_input(attempt_id: str, request: Request, raw=BODY, db=DB):
    attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
    configured, current = _runtime(request, db)
    handle = ArtifactHandle.model_validate(raw)
    return _bytes(_reads(configured).input(db, current, attempt_id, handle))


@router.post("/activity-artifacts/{attempt_id}/context")
def attempt_context(attempt_id: str, request: Request, raw=BODY, db=DB):
    attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
    configured, current = _runtime(request, db)
    body = ContextRead.model_validate(raw)
    return _bytes(_reads(configured).context(db, current, attempt_id, body.source_id, body.version))
