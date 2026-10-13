"""Durable signed webhook delivery for metadata-only incident alerts."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta
from time import time

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .config import AlertWebhook, Settings
from .models import AlertDelivery, Workspace, now
from .security import canonical, digest, new_token, utc

MAX_ATTEMPTS = 5
MAX_ALERT_BYTES = 32 * 1024


@dataclass(frozen=True)
class AlertClaim:
    id: str
    workspace_id: str
    lease_token: str
    destination: str
    event: str
    payload: dict


def _clear_lease(delivery: AlertDelivery) -> None:
    delivery.lease_expires_at = None
    delivery.lease_token_hash = None


def claim_alert(
    db: Session,
    *,
    lease_seconds: int = 60,
) -> AlertClaim | None:
    current = now()
    exhausted = db.scalar(
        select(AlertDelivery)
        .where(
            AlertDelivery.status == "sending",
            AlertDelivery.attempts >= MAX_ATTEMPTS,
            AlertDelivery.lease_expires_at < current,
        )
        .order_by(AlertDelivery.created_at, AlertDelivery.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if exhausted:
        exhausted.status = "failed"
        exhausted.last_error_code = "attempts_exhausted"
        _clear_lease(exhausted)
        db.flush()
        return None
    claimable = or_(
        and_(AlertDelivery.status == "queued", AlertDelivery.available_at <= current),
        and_(
            AlertDelivery.status == "sending",
            AlertDelivery.lease_expires_at < current,
        ),
    )
    delivery = db.scalar(
        select(AlertDelivery)
        .join(Workspace, Workspace.id == AlertDelivery.workspace_id)
        .where(
            Workspace.status == "active",
            AlertDelivery.attempts < MAX_ATTEMPTS,
            claimable,
        )
        .order_by(AlertDelivery.created_at, AlertDelivery.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if delivery is None:
        return None
    token = new_token()
    delivery.status = "sending"
    delivery.attempts += 1
    delivery.lease_token_hash = digest(token)
    delivery.lease_expires_at = current + timedelta(seconds=lease_seconds)
    db.flush()
    return AlertClaim(
        id=delivery.id,
        workspace_id=delivery.workspace_id,
        lease_token=token,
        destination=delivery.destination,
        event=delivery.event,
        payload=delivery.payload,
    )


def _locked_delivery(db: Session, claim: AlertClaim) -> AlertDelivery | None:
    current = now()
    return db.scalar(
        select(AlertDelivery)
        .where(
            AlertDelivery.id == claim.id,
            AlertDelivery.workspace_id == claim.workspace_id,
            AlertDelivery.status == "sending",
            AlertDelivery.lease_token_hash == digest(claim.lease_token),
            AlertDelivery.lease_expires_at >= current,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _complete(db: Session, claim: AlertClaim) -> bool:
    delivery = _locked_delivery(db, claim)
    if not delivery:
        return False
    delivery.status = "delivered"
    delivery.delivered_at = now()
    delivery.last_error_code = None
    _clear_lease(delivery)
    return True


def _fail(db: Session, claim: AlertClaim, code: str, *, terminal: bool = False) -> bool:
    delivery = _locked_delivery(db, claim)
    if not delivery:
        return False
    terminal = terminal or delivery.attempts >= MAX_ATTEMPTS
    delivery.status = "failed" if terminal else "queued"
    delivery.last_error_code = code
    delivery.available_at = now() + timedelta(seconds=min(900, 15 * 2**delivery.attempts))
    _clear_lease(delivery)
    return False


def _destination(settings: Settings, name: str) -> AlertWebhook | None:
    return next(
        (item for item in settings.alert_webhooks if item.enabled and item.name == name),
        None,
    )


def _signed_headers(destination: AlertWebhook, claim: AlertClaim, body: bytes) -> dict[str, str]:
    timestamp = str(int(time()))
    signed = timestamp.encode("ascii") + b"." + body
    signature = hmac.new(
        destination.secret.get_secret_value().encode(), signed, hashlib.sha256
    ).hexdigest()
    return {
        "content-type": "application/json",
        "user-agent": "Lotuspond-Alert-Worker/1",
        "x-lotuspond-delivery": claim.id,
        "x-lotuspond-event": claim.event,
        "x-lotuspond-timestamp": timestamp,
        "x-lotuspond-signature": f"v1={signature}",
    }


async def deliver_alert(
    sessions,
    settings: Settings,
    claim: AlertClaim,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    with sessions.begin() as db:
        delivery = db.scalar(
            select(AlertDelivery)
            .where(AlertDelivery.id == claim.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            not delivery
            or delivery.status != "sending"
            or delivery.lease_token_hash != digest(claim.lease_token)
            or not delivery.lease_expires_at
            or utc(delivery.lease_expires_at) < now()
        ):
            return False
        workspace = db.get(Workspace, delivery.workspace_id)
        if not workspace or workspace.status != "active" or settings.network_mode == "airgap":
            delivery.status = "cancelled"
            delivery.last_error_code = (
                "workspace_suspended" if workspace and workspace.status != "active" else "network_disabled"
            )
            _clear_lease(delivery)
            return False
    destination = _destination(settings, claim.destination)
    if destination is None:
        with sessions.begin() as db:
            return _fail(db, claim, "destination_unavailable", terminal=True)
    body = canonical(claim.payload).encode()
    if len(body) > MAX_ALERT_BYTES:
        with sessions.begin() as db:
            return _fail(db, claim, "payload_invalid", terminal=True)
    headers = _signed_headers(destination, claim, body)
    try:
        async with asyncio.timeout(settings.alert_timeout_seconds):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.alert_timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client:
                async with client.stream(
                    "POST", destination.url, headers=headers, content=body
                ) as response:
                    status = response.status_code
    except (TimeoutError, httpx.TimeoutException):
        with sessions.begin() as db:
            return _fail(db, claim, "timeout")
    except httpx.RequestError:
        with sessions.begin() as db:
            return _fail(db, claim, "connection_error")
    if 200 <= status < 300:
        with sessions.begin() as db:
            return _complete(db, claim)
    retryable = status in {408, 425, 429} or status >= 500
    code = "redirect_refused" if 300 <= status < 400 else f"http_{status}"
    with sessions.begin() as db:
        return _fail(db, claim, code, terminal=not retryable)


async def run_once(
    sessions,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool | None:
    if settings.network_mode == "airgap" or not any(
        item.enabled for item in settings.alert_webhooks
    ):
        return None
    with sessions.begin() as db:
        claim = claim_alert(db)
    if claim is None:
        return None
    return await deliver_alert(sessions, settings, claim, transport=transport)
