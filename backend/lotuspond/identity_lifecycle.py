from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from . import schemas as s
from .deps import DB, audit, require
from .mail import encrypt_payload, enqueue_mail, enqueue_security_notice
from .models import (
    IdentityChallenge,
    Invitation,
    LoginSession,
    Membership,
    MfaEnrollment,
    MfaLoginChallenge,
    OfflineEnrollment,
    User,
    Workspace,
    new_id,
    now,
)
from .security import (
    Principal,
    digest,
    fail,
    new_token,
    password_hasher,
    require_admin,
    session_user,
    utc,
    verify_password,
)

router = APIRouter(prefix="/api/v1")
TOKEN_LIFETIME = timedelta(minutes=30)


def _personal_workspace(db: Session, user: User) -> Workspace | None:
    return db.scalar(
        select(Workspace).where(Workspace.owner_id == user.id, Workspace.kind == "personal")
    )


def _audit_account(db: Session, user: User, action: str, resource_id: str):
    personal = _personal_workspace(db, user)
    if personal:
        audit(db, Principal(user.id, personal.id, "owner", user.id), action, resource_id)


def _active_challenge(challenge: IdentityChallenge | None, kind: str) -> bool:
    return bool(
        challenge
        and challenge.kind == kind
        and challenge.consumed_at is None
        and challenge.revoked_at is None
        and utc(challenge.expires_at) > now()
    )


def _password_recovery_eligible(db: Session, user: User) -> bool:
    """Keep mailbox recovery from upgrading a stored offline identity claim.

    Offline enrollment was retired (ADR-0014), but its rows stay. Publicly
    registered accounts retain the existing recovery behavior. A stored
    offline-origin account recovers only if it finished enrollment and
    independently verified its mailbox, as before.
    """
    offline_origin = db.scalar(
        select(exists().where(OfflineEnrollment.user_id == user.id))
    )
    if not offline_origin:
        return True
    if not user.email_verified:
        return False
    return bool(
        db.scalar(
            select(
                exists().where(
                    OfflineEnrollment.user_id == user.id,
                    OfflineEnrollment.consumed_at.is_not(None),
                    OfflineEnrollment.revoked_at.is_(None),
                )
            )
        )
    )


def _active_invitation(invitation: Invitation | None) -> bool:
    return bool(
        invitation
        and invitation.accepted_at is None
        and invitation.revoked_at is None
        and utc(invitation.expires_at) > now()
    )


def validate_registration_invitation(
    db: Session, token: str | None, email: str
) -> Invitation | None:
    if not token:
        return None
    hint = db.scalar(select(Invitation).where(Invitation.token_hash == digest(token)))
    if not hint:
        return None
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == hint.workspace_id).with_for_update()
    )
    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.id == hint.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not _active_invitation(invitation) or invitation.email != email:
        return None
    if not workspace or workspace.kind != "organization" or workspace.status != "active":
        return None
    return invitation


def _revoke_challenges(db: Session, user_id: str, kind: str, *, except_id: str | None = None):
    rows = db.scalars(
        select(IdentityChallenge)
        .where(
            IdentityChallenge.user_id == user_id,
            IdentityChallenge.kind == kind,
            IdentityChallenge.consumed_at.is_(None),
            IdentityChallenge.revoked_at.is_(None),
        )
        .with_for_update()
    )
    current = now()
    for row in rows:
        if row.id != except_id:
            row.revoked_at = current


@router.post("/auth/verification/request", status_code=202)
def request_verification(request: Request, db: Session = DB):
    user = session_user(db, request)
    request.app.state.limiter.check(f"verification-request:{user.id}", 5, 3600)
    user = db.scalar(select(User).where(User.id == user.id).with_for_update())
    session_user(db, request)
    if user.email_verified:
        return {"status": "accepted"}
    settings = request.app.state.settings
    if not settings.mail_enabled:
        fail(503, "mail_unavailable", "Mailbox delivery is not configured.")
    _revoke_challenges(db, user.id, "email_verification")
    raw = new_token()
    challenge = IdentityChallenge(
        id=new_id(),
        user_id=user.id,
        kind="email_verification",
        token_hash=digest(raw),
        expires_at=now() + TOKEN_LIFETIME,
    )
    db.add(challenge)
    personal = _personal_workspace(db, user)
    enqueue_mail(
        db,
        settings,
        kind="email_verification",
        recipient=user.email,
        subject="Verify your Lotuspond mailbox",
        body=f"Verify your mailbox: {settings.origin}/#verify-email?token={raw}",
        actor_id=user.id,
        workspace_id=personal.id if personal else None,
        challenge_id=challenge.id,
    )
    _audit_account(db, user, "account.verification_requested", challenge.id)
    return {"status": "accepted"}


@router.post("/auth/verification/confirm")
def confirm_verification(body: s.TokenConfirm, request: Request, db: Session = DB):
    token_hash = digest(body.token)
    request.app.state.limiter.check(f"verification-confirm-ip:{request.client.host}", 40, 900)
    request.app.state.limiter.check(f"verification-confirm:{token_hash}", 10, 900)
    challenge = db.scalar(
        select(IdentityChallenge).where(IdentityChallenge.token_hash == token_hash)
    )
    if not challenge:
        fail(400, "invalid_token", "The verification link is invalid or expired.")
    user = db.scalar(select(User).where(User.id == challenge.user_id).with_for_update())
    challenge = db.scalar(
        select(IdentityChallenge)
        .where(IdentityChallenge.id == challenge.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not user or not _active_challenge(challenge, "email_verification"):
        fail(400, "invalid_token", "The verification link is invalid or expired.")
    user.email_verified = True
    challenge.consumed_at = now()
    _revoke_challenges(db, user.id, "email_verification", except_id=challenge.id)
    _audit_account(db, user, "account.email_verified", user.id)
    return {"ok": True}


@router.post("/auth/password/reset/request", status_code=202)
def request_password_reset(body: s.PasswordResetRequest, request: Request, db: Session = DB):
    email = str(body.email).lower()
    request.app.state.limiter.check(f"password-reset-account:{email}", 5, 3600)
    request.app.state.limiter.check(f"password-reset-ip:{request.client.host}", 20, 3600)
    settings = request.app.state.settings
    raw = new_token()
    link = f"{settings.origin}/#reset-password?token={raw}"
    # Do equivalent token/message encryption work even when no eligible account
    # exists. The response remains identical and never confirms delivery.
    if settings.mail_enabled:
        encrypt_payload(
            settings,
            new_id(),
            {"to": email, "subject": "Reset your Lotuspond password", "body": f"Reset: {link}"},
        )
    user = db.scalar(
        select(User).where(User.email == email, User.disabled_at.is_(None)).with_for_update()
    )
    if user and settings.mail_enabled and _password_recovery_eligible(db, user):
        _revoke_challenges(db, user.id, "password_reset")
        challenge = IdentityChallenge(
            id=new_id(),
            user_id=user.id,
            kind="password_reset",
            token_hash=digest(raw),
            expires_at=now() + TOKEN_LIFETIME,
        )
        db.add(challenge)
        personal = _personal_workspace(db, user)
        enqueue_mail(
            db,
            settings,
            kind="password_reset",
            recipient=user.email,
            subject="Reset your Lotuspond password",
            body=f"Reset your password: {link}",
            actor_id=user.id,
            workspace_id=personal.id if personal else None,
            challenge_id=challenge.id,
        )
        _audit_account(db, user, "account.password_reset_requested", challenge.id)
    elif settings.mail_enabled:
        # Keep the dominant token/message work comparable for ineligible addresses.
        encrypt_payload(
            settings,
            new_id(),
            {"to": email, "subject": "Reset your Lotuspond password", "body": f"Reset: {link}"},
        )
    return {"status": "accepted"}


@router.post("/auth/password/reset/confirm")
def confirm_password_reset(body: s.PasswordResetConfirm, request: Request, db: Session = DB):
    token_hash = digest(body.token)
    request.app.state.limiter.check(f"password-reset-confirm-ip:{request.client.host}", 40, 900)
    request.app.state.limiter.check(f"password-reset-confirm:{token_hash}", 10, 900)
    challenge = db.scalar(
        select(IdentityChallenge).where(IdentityChallenge.token_hash == token_hash)
    )
    if not challenge:
        fail(400, "invalid_token", "The password reset link is invalid or expired.")
    user = db.scalar(select(User).where(User.id == challenge.user_id).with_for_update())
    challenge = db.scalar(
        select(IdentityChallenge)
        .where(IdentityChallenge.id == challenge.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not user
        or user.disabled_at
        or not _active_challenge(challenge, "password_reset")
        or not _password_recovery_eligible(db, user)
    ):
        fail(400, "invalid_token", "The password reset link is invalid or expired.")
    if verify_password(body.new_password, user.password_hash):
        fail(422, "unchanged_password", "Choose a different password.")
    user.password_hash = password_hasher.hash(body.new_password)
    challenge.consumed_at = now()
    _revoke_challenges(db, user.id, "password_reset", except_id=challenge.id)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.execute(delete(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user.id))
    db.execute(delete(MfaEnrollment).where(MfaEnrollment.user_id == user.id))
    _audit_account(db, user, "account.password_reset", user.id)
    personal = _personal_workspace(db, user)
    enqueue_security_notice(
        db,
        request.app.state.settings,
        user=user,
        change="password_reset",
        occurred_at=now(),
        workspace_id=personal.id if personal else None,
    )
    return {"ok": True}


def _invitation_status(invitation: Invitation) -> str:
    if invitation.accepted_at:
        return "accepted"
    if invitation.revoked_at:
        return "revoked"
    if utc(invitation.expires_at) <= now():
        return "expired"
    return "pending"


def _invitation_json(invitation: Invitation) -> dict:
    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "status": _invitation_status(invitation),
        "expires_at": utc(invitation.expires_at).isoformat(),
        "created_at": utc(invitation.created_at).isoformat(),
        "created_by": invitation.created_by,
    }


@router.get("/invitations")
def invitations(p: Principal = Depends(require()), db: Session = DB):
    require_admin(p)
    return {
        "items": [
            _invitation_json(row)
            for row in db.scalars(
                select(Invitation)
                .where(Invitation.workspace_id == p.workspace_id)
                .order_by(Invitation.created_at.desc(), Invitation.id.desc())
                .limit(100)
            )
        ]
    }


@router.post("/invitations", status_code=201)
def create_invitation(
    body: s.InvitationInput,
    request: Request,
    p: Principal = Depends(require("write")),
    db: Session = DB,
):
    require_admin(p)
    settings = request.app.state.settings
    if not settings.mail_enabled:
        fail(503, "mail_unavailable", "Mailbox delivery is not configured.")
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == p.workspace_id).with_for_update()
    )
    if workspace.kind != "organization":
        fail(409, "organization_required", "Invitations require an organization workspace.")
    if workspace.status != "active":
        fail(409, "workspace_suspended", "The organization is suspended.")
    email = str(body.email).lower()
    request.app.state.limiter.check(f"invitation-workspace:{workspace.id}", 50, 3600)
    request.app.state.limiter.check(f"invitation-recipient:{email}", 10, 86400)
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and db.get(Membership, (workspace.id, existing_user.id)):
        fail(409, "already_member", "This account is already an organization member.")
    for prior in db.scalars(
        select(Invitation)
        .where(
            Invitation.workspace_id == workspace.id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .with_for_update()
    ):
        prior.revoked_at = now()
    raw = new_token()
    invitation = Invitation(
        id=new_id(),
        workspace_id=workspace.id,
        email=email,
        role=body.role,
        token_hash=digest(raw),
        created_by=p.user_id,
        expires_at=now() + timedelta(days=body.days),
    )
    db.add(invitation)
    enqueue_mail(
        db,
        settings,
        kind="invitation",
        recipient=email,
        subject=f"Invitation to {workspace.name} on Lotuspond",
        body=f"Accept your invitation: {settings.origin}/#accept-invitation?token={raw}",
        actor_id=p.user_id,
        workspace_id=workspace.id,
        invitation_id=invitation.id,
    )
    audit(db, p, "invitation.created", invitation.id, {"role": body.role})
    db.flush()
    return _invitation_json(invitation)


@router.post("/invitations/{identifier}/revoke")
def revoke_invitation(
    identifier: s.Identifier,
    p: Principal = Depends(require("write")),
    db: Session = DB,
):
    require_admin(p)
    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.id == identifier, Invitation.workspace_id == p.workspace_id)
        .with_for_update()
    )
    if not invitation or not _active_invitation(invitation):
        fail(404, "not_found", "Pending invitation not found.")
    invitation.revoked_at = now()
    audit(db, p, "invitation.revoked", invitation.id)
    return {"ok": True}


@router.post("/invitations/accept")
def accept_invitation(body: s.TokenConfirm, request: Request, db: Session = DB):
    user = session_user(db, request)
    request.app.state.limiter.check(f"invitation-accept:{user.id}", 20, 900)
    request.app.state.limiter.check(f"invitation-accept-ip:{request.client.host}", 40, 900)
    token_hash = digest(body.token)
    user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session_user(db, request)
    invitation_hint = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash))
    if not invitation_hint:
        fail(400, "invalid_token", "The invitation is invalid or expired.")
    workspace = db.scalar(
        select(Workspace)
        .where(Workspace.id == invitation_hint.workspace_id)
        .with_for_update()
    )
    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.id == invitation_hint.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not user.email_verified:
        fail(403, "identity_unverified", "Verify your mailbox before accepting invitations.")
    if not _active_invitation(invitation) or invitation.email != user.email:
        fail(400, "invalid_token", "The invitation is invalid or expired.")
    if not workspace or workspace.kind != "organization":
        fail(409, "organization_required", "The invitation organization is unavailable.")
    if workspace.status != "active":
        fail(409, "workspace_suspended", "The invitation organization is suspended.")
    if db.get(Membership, (workspace.id, user.id)):
        fail(409, "already_member", "This account is already an organization member.")
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role=invitation.role))
    invitation.accepted_at = now()
    audit(
        db,
        Principal(user.id, workspace.id, invitation.role, user.id),
        "invitation.accepted",
        invitation.id,
    )
    return {"ok": True, "workspace_id": workspace.id, "role": invitation.role}
