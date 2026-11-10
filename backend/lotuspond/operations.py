"""MFA-protected, aggregate operational queue visibility."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .deps import DB
from .models import (
    IdentityChallenge,
    Invitation,
    MailOutbox,
    Workspace,
    now,
)
from .security import platform_admin, utc
from .worker_observation import WORKER_ROLES, read_observations

router = APIRouter(prefix="/api/v1/admin")
DELIVERY_MAX_ATTEMPTS = 5

STATUS_VALUES = {
    "mail": ("queued", "sending", "sent", "failed", "cancelled"),
}


def _status_counts(db: Session, model, queue: str) -> dict[str, int]:
    observed = dict(db.execute(select(model.status, func.count()).group_by(model.status)).all())
    return {status: int(observed.get(status, 0)) for status in STATUS_VALUES[queue]}


def _count(db: Session, model, condition) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(condition)) or 0)


def _mail_health(db: Session, observed_at, *, enabled: bool) -> dict:
    active_challenge = and_(
        MailOutbox.identity_challenge_id.is_not(None),
        IdentityChallenge.id.is_not(None),
        IdentityChallenge.consumed_at.is_(None),
        IdentityChallenge.revoked_at.is_(None),
        IdentityChallenge.expires_at > observed_at,
    )
    active_invitation = and_(
        MailOutbox.invitation_id.is_not(None),
        Invitation.id.is_not(None),
        Invitation.accepted_at.is_(None),
        Invitation.revoked_at.is_(None),
        Invitation.expires_at > observed_at,
        Workspace.kind == "organization",
        Workspace.status == "active",
    )
    active_reference = or_(active_challenge, active_invitation)
    claimable = or_(
        and_(MailOutbox.status == "queued", MailOutbox.available_at <= observed_at),
        and_(
            MailOutbox.status == "sending",
            MailOutbox.lease_expires_at.is_not(None),
            MailOutbox.lease_expires_at < observed_at,
        ),
    )
    eligible = and_(
        MailOutbox.attempts < DELIVERY_MAX_ATTEMPTS, active_reference, claimable
    )
    delayed = and_(
        MailOutbox.status == "queued",
        MailOutbox.attempts < DELIVERY_MAX_ATTEMPTS,
        MailOutbox.available_at > observed_at,
        active_reference,
    )
    suspended = and_(
        MailOutbox.status.in_(("queued", "sending")),
        MailOutbox.attempts < DELIVERY_MAX_ATTEMPTS,
        MailOutbox.invitation_id.is_not(None),
        Invitation.accepted_at.is_(None),
        Invitation.revoked_at.is_(None),
        Invitation.expires_at > observed_at,
        Workspace.status == "suspended",
    )
    expired = and_(
        MailOutbox.status == "sending",
        MailOutbox.lease_expires_at.is_not(None),
        MailOutbox.lease_expires_at < observed_at,
    )

    def query(expression):
        return (
            select(func.count())
            .select_from(MailOutbox)
            .outerjoin(
                IdentityChallenge,
                IdentityChallenge.id == MailOutbox.identity_challenge_id,
            )
            .outerjoin(Invitation, Invitation.id == MailOutbox.invitation_id)
            .outerjoin(Workspace, Workspace.id == Invitation.workspace_id)
            .where(expression)
        )

    oldest = db.scalar(
        select(func.min(MailOutbox.created_at))
        .outerjoin(
            IdentityChallenge,
            IdentityChallenge.id == MailOutbox.identity_challenge_id,
        )
        .outerjoin(Invitation, Invitation.id == MailOutbox.invitation_id)
        .outerjoin(Workspace, Workspace.id == Invitation.workspace_id)
        .where(eligible)
    )
    return {
        "enabled": enabled,
        "status_counts": _status_counts(db, MailOutbox, "mail"),
        "eligible_now": int(db.scalar(query(eligible)) or 0),
        "suspended": int(db.scalar(query(suspended)) or 0),
        "delayed": int(db.scalar(query(delayed)) or 0),
        "expired_leases": _count(db, MailOutbox, expired),
        "oldest_eligible_age_seconds": (
            max(0, int((observed_at - utc(oldest)).total_seconds())) if oldest else None
        ),
    }


@router.get("/operations")
def operations(request: Request, db: Session = DB):
    platform_admin(db, request)
    observed_at = now()
    settings = request.app.state.settings
    enabled = {"mail": settings.mail_enabled}
    redis_dependency, observations = read_observations(
        request.app.state.limiter.client,
        configured=settings.redis_url is not None,
        enabled=enabled,
        observed_at=observed_at,
    )
    workers = {
        role: {"role": role, "enabled": enabled[role], "observation": observations[role]}
        for role in WORKER_ROLES
    }
    return {
        "observed_at": observed_at.isoformat(),
        "dependencies": {
            "database": {"state": "ok", "error_code": None},
            "redis": redis_dependency,
        },
        "workers": workers,
        "queues": {
            "mail": _mail_health(db, observed_at, enabled=enabled["mail"]),
        },
    }
