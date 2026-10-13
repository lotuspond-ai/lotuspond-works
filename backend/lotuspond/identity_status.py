from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .models import OfflineEnrollment, OfflineIdentityAttestation, User, now


def enrollment_pending(db: Session, user_id: str) -> bool:
    provisioned = db.scalar(
        select(exists().where(OfflineEnrollment.user_id == user_id))
    )
    completed = db.scalar(
        select(
            exists().where(
                OfflineEnrollment.user_id == user_id,
                OfflineEnrollment.consumed_at.is_not(None),
                OfflineEnrollment.revoked_at.is_(None),
            )
        )
    )
    return bool(provisioned and not completed)


def identity_verification_source(db: Session, user: User) -> str | None:
    if user.disabled_at is not None or enrollment_pending(db, user.id):
        return None
    if user.email_verified:
        return "mailbox"
    current = now()
    active = db.scalar(
        select(OfflineIdentityAttestation.id)
        .join(
            OfflineEnrollment,
            (
                OfflineEnrollment.user_id == OfflineIdentityAttestation.user_id
            )
            & (OfflineEnrollment.attestation_id == OfflineIdentityAttestation.id),
        )
        .where(
            OfflineIdentityAttestation.user_id == user.id,
            OfflineIdentityAttestation.attested_at <= current,
            OfflineIdentityAttestation.revoked_at.is_(None),
            (
                OfflineIdentityAttestation.expires_at.is_(None)
                | (OfflineIdentityAttestation.expires_at > current)
            ),
            OfflineEnrollment.consumed_at.is_not(None),
            OfflineEnrollment.revoked_at.is_(None),
        )
        .limit(1)
    )
    return "operator_attested" if active else None


def identity_verified(db: Session, user: User) -> bool:
    return identity_verification_source(db, user) is not None
