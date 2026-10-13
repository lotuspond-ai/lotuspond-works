"""MFA-protected, aggregate operational queue visibility."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .deps import DB
from .models import (
    AlertDelivery,
    Assessment,
    EvaluationJob,
    IdentityChallenge,
    Invitation,
    MailOutbox,
    Workspace,
    now,
)
from .security import platform_admin, utc
from .worker_observation import WORKER_ROLES, read_observations

router = APIRouter(prefix="/api/v1/admin")
EVALUATION_MAX_ATTEMPTS = 3
DELIVERY_MAX_ATTEMPTS = 5
ASSESSMENT_WINDOW = timedelta(hours=24)

STATUS_VALUES = {
    "evaluation": ("queued", "running", "completed", "failed", "cancelled"),
    "mail": ("queued", "sending", "sent", "failed", "cancelled"),
    "alert": ("queued", "sending", "delivered", "failed", "cancelled"),
}


def _status_counts(db: Session, model, queue: str) -> dict[str, int]:
    observed = dict(db.execute(select(model.status, func.count()).group_by(model.status)).all())
    return {status: int(observed.get(status, 0)) for status in STATUS_VALUES[queue]}


def _count(db: Session, model, condition) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(condition)) or 0)


def _evaluation_health(db: Session, observed_at, *, enabled: bool) -> dict:
    pending = EvaluationJob.status.in_(("queued", "running"))
    expired = and_(
        EvaluationJob.status == "running",
        EvaluationJob.lease_expires_at.is_not(None),
        EvaluationJob.lease_expires_at < observed_at,
    )
    eligible = and_(
        Workspace.status == "active",
        EvaluationJob.attempts < EVALUATION_MAX_ATTEMPTS,
        or_(EvaluationJob.status == "queued", expired),
    )
    suspended = and_(Workspace.status == "suspended", pending)
    eligible_query = (
        select(func.count())
        .select_from(EvaluationJob)
        .join(Workspace, Workspace.id == EvaluationJob.workspace_id)
        .where(eligible)
    )
    oldest_query = (
        select(func.min(EvaluationJob.created_at))
        .join(Workspace, Workspace.id == EvaluationJob.workspace_id)
        .where(eligible)
    )
    oldest = db.scalar(oldest_query)
    return {
        "enabled": enabled,
        "status_counts": _status_counts(db, EvaluationJob, "evaluation"),
        "eligible_now": int(db.scalar(eligible_query) or 0),
        "suspended": int(
            db.scalar(
                select(func.count())
                .select_from(EvaluationJob)
                .join(Workspace, Workspace.id == EvaluationJob.workspace_id)
                .where(suspended)
            )
            or 0
        ),
        "delayed": 0,
        "expired_leases": _count(db, EvaluationJob, expired),
        "oldest_eligible_age_seconds": (
            max(0, int((observed_at - utc(oldest)).total_seconds())) if oldest else None
        ),
    }


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


def _alert_health(db: Session, observed_at, *, enabled: bool) -> dict:
    pending = AlertDelivery.status.in_(("queued", "sending"))
    expired = and_(
        AlertDelivery.status == "sending",
        AlertDelivery.lease_expires_at.is_not(None),
        AlertDelivery.lease_expires_at < observed_at,
    )
    eligible = and_(
        Workspace.status == "active",
        AlertDelivery.attempts < DELIVERY_MAX_ATTEMPTS,
        or_(
            and_(AlertDelivery.status == "queued", AlertDelivery.available_at <= observed_at),
            expired,
        ),
    )
    suspended = and_(Workspace.status == "suspended", pending)
    delayed = and_(
        Workspace.status == "active",
        AlertDelivery.status == "queued",
        AlertDelivery.attempts < DELIVERY_MAX_ATTEMPTS,
        AlertDelivery.available_at > observed_at,
    )

    def query(expression):
        return (
            select(func.count())
            .select_from(AlertDelivery)
            .join(Workspace, Workspace.id == AlertDelivery.workspace_id)
            .where(expression)
        )

    oldest = db.scalar(
        select(func.min(AlertDelivery.created_at))
        .join(Workspace, Workspace.id == AlertDelivery.workspace_id)
        .where(eligible)
    )
    return {
        "enabled": enabled,
        "status_counts": _status_counts(db, AlertDelivery, "alert"),
        "eligible_now": int(db.scalar(query(eligible)) or 0),
        "suspended": int(db.scalar(query(suspended)) or 0),
        "delayed": int(db.scalar(query(delayed)) or 0),
        "expired_leases": _count(db, AlertDelivery, expired),
        "oldest_eligible_age_seconds": (
            max(0, int((observed_at - utc(oldest)).total_seconds())) if oldest else None
        ),
    }


@router.get("/operations")
def operations(request: Request, db: Session = DB):
    platform_admin(db, request)
    observed_at = now()
    start_at = observed_at - ASSESSMENT_WINDOW
    settings = request.app.state.settings
    enabled = {
        "evaluation": True,
        "mail": settings.mail_enabled,
        "alert": any(item.enabled for item in settings.alert_webhooks),
    }
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
    outcomes = dict(
        db.execute(
            select(Assessment.status, func.count())
            .where(
                Assessment.created_at >= start_at,
                Assessment.created_at < observed_at,
                Assessment.status.in_(("failed", "unknown")),
            )
            .group_by(Assessment.status)
        ).all()
    )
    return {
        "observed_at": observed_at.isoformat(),
        "window": {
            "start_at": start_at.isoformat(),
            "end_at": observed_at.isoformat(),
            "duration_seconds": int(ASSESSMENT_WINDOW.total_seconds()),
        },
        "dependencies": {
            "database": {"state": "ok", "error_code": None},
            "redis": redis_dependency,
        },
        "workers": workers,
        "queues": {
            "evaluation": _evaluation_health(db, observed_at, enabled=True),
            "mail": _mail_health(db, observed_at, enabled=enabled["mail"]),
            "alert": _alert_health(db, observed_at, enabled=enabled["alert"]),
        },
        "assessments_last_24h": {
            "failed": int(outcomes.get("failed", 0)),
            "unknown": int(outcomes.get("unknown", 0)),
        },
    }
