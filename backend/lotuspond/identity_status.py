from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .models import OfflineEnrollment, User


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
    """Only a verified mailbox verifies an identity (ADR-0014).

    A stored, unfinished offline enrollment still blocks the account; an operator
    attestation no longer counts as verification.
    """
    if user.disabled_at is not None or enrollment_pending(db, user.id):
        return None
    return "mailbox" if user.email_verified else None


def identity_verified(db: Session, user: User) -> bool:
    return identity_verification_source(db, user) is not None
