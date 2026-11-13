from __future__ import annotations

import base64
import binascii
import secrets
from datetime import timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor import InvalidToken
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import authority_gate
from . import schemas as s
from .deps import DB, audit, require
from .identity_status import enrollment_pending
from .mail import enqueue_security_notice
from .models import (
    LoginSession,
    Membership,
    MfaEnrollment,
    MfaFactor,
    MfaLoginChallenge,
    MfaRecoveryCode,
    User,
    Workspace,
    new_id,
    now,
)
from .security import (
    Principal,
    current_session,
    digest,
    fail,
    issue_session,
    new_token,
    require_admin,
    session_user,
    utc,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth/mfa")
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
ENROLLMENT_LIFETIME = timedelta(minutes=10)
CHALLENGE_LIFETIME = timedelta(minutes=5)
MAX_CHALLENGE_ATTEMPTS = 8
RECOVERY_CODE_COUNT = 10


def _key(settings) -> bytes:
    configured = settings.mfa_encryption_key
    if not configured:
        fail(503, "mfa_unavailable", "Multi-factor authentication is unavailable.")
    try:
        key = base64.b64decode(configured.get_secret_value(), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        fail(503, "mfa_unavailable", "Multi-factor authentication is unavailable.")
    if len(key) != 32:
        fail(503, "mfa_unavailable", "Multi-factor authentication is unavailable.")
    return key


def _aad(purpose: str, user_id: str, record_id: str, version: int) -> bytes:
    return f"lotuspond:mfa:{purpose}:{user_id}:{record_id}:{version}".encode()


def _encrypt_secret(
    settings, purpose: str, user_id: str, record_id: str, version: int, secret: bytes
) -> str:
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_key(settings)).encrypt(
        nonce, secret, _aad(purpose, user_id, record_id, version)
    )
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def _decrypt_secret(
    settings, purpose: str, user_id: str, record_id: str, version: int, value: str
) -> bytes:
    try:
        payload = base64.b64decode(value, altchars=b"-_", validate=True)
        if len(payload) < 29:
            raise ValueError("short ciphertext")
        return AESGCM(_key(settings)).decrypt(
            payload[:12], payload[12:], _aad(purpose, user_id, record_id, version)
        )
    except Exception as error:
        if getattr(error, "status_code", None) == 503:
            raise
        fail(503, "mfa_unavailable", "Multi-factor authentication is unavailable.")


def _totp(secret: bytes) -> TOTP:
    return TOTP(secret, TOTP_DIGITS, SHA1(), TOTP_PERIOD_SECONDS)


def _totp_at(secret: bytes, timestamp: int) -> str:
    return _totp(secret).generate(timestamp).decode()


def _matching_counter(secret: bytes, code: str, timestamp: int) -> int | None:
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    current = timestamp // TOTP_PERIOD_SECONDS
    verifier = _totp(secret)
    for counter in (current - 1, current, current + 1):
        if counter < 0:
            continue
        try:
            verifier.verify(code.encode(), counter * TOTP_PERIOD_SECONDS)
            return counter
        except InvalidToken:
            continue
    return None


def _factor(db: Session, user_id: str, *, lock: bool = False) -> MfaFactor | None:
    query = select(MfaFactor).where(MfaFactor.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


def _personal_workspace(db: Session, user: User) -> Workspace | None:
    return db.scalar(
        select(Workspace).where(Workspace.owner_id == user.id, Workspace.kind == "personal")
    )


def _audit_account(db: Session, user: User, action: str, resource_id: str):
    personal = _personal_workspace(db, user)
    if personal:
        audit(db, Principal(user.id, personal.id, "owner", user.id), action, resource_id)


def _notify(request: Request, db: Session, user: User, change: str, occurred_at):
    """Queue the out-of-band notice; a disabled mailbox never blocks the change."""
    personal = _personal_workspace(db, user)
    enqueue_security_notice(
        db,
        request.app.state.settings,
        user=user,
        change=change,
        occurred_at=occurred_at,
        workspace_id=personal.id if personal else None,
    )


def _revoke_login_challenges(db: Session, user_id: str):
    current = now()
    rows = list(
        db.scalars(
            select(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user_id).with_for_update()
        )
    )
    for row in rows:
        if (
            row.consumed_at is not None
            or row.revoked_at is not None
            or utc(row.expires_at) <= current
        ):
            db.delete(row)
        else:
            row.revoked_at = current


def begin_login_challenge(
    db: Session, request: Request, response: Response, user: User
) -> dict | None:
    factor = _factor(db, user.id, lock=True)
    if not factor:
        return None
    _key(request.app.state.settings)
    _revoke_login_challenges(db, user.id)
    raw = f"lpm_{new_token()}"
    challenge = MfaLoginChallenge(
        user_id=user.id,
        factor_id=factor.id,
        factor_version=factor.version,
        token_hash=digest(raw),
        expires_at=now() + CHALLENGE_LIFETIME,
    )
    db.add(challenge)
    response.delete_cookie("lotuspond_session", path="/")
    return {
        "mfa_required": True,
        "challenge_token": raw,
        "expires_at": challenge.expires_at,
    }


def session_mfa_verified(db: Session, request: Request, factor: MfaFactor | None) -> bool:
    session = current_session(db, request)
    return bool(
        factor
        and session
        and session.mfa_verified_at is not None
        and session.mfa_factor_id == factor.id
        and session.mfa_factor_version == factor.version
    )


def _verify_factor_code(db: Session, settings, factor: MfaFactor, code: str) -> bool:
    _key(settings)
    if len(code) == TOTP_DIGITS and code.isdigit():
        secret = _decrypt_secret(
            settings,
            "factor",
            factor.user_id,
            factor.id,
            factor.version,
            factor.secret_ciphertext,
        )
        timestamp = int(now().timestamp())
        counter = _matching_counter(secret, code, timestamp)
        if counter is None or (
            factor.last_totp_counter is not None and counter <= factor.last_totp_counter
        ):
            return False
        factor.last_totp_counter = counter
        return True
    recovery = db.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == factor.user_id,
            MfaRecoveryCode.factor_id == factor.id,
            MfaRecoveryCode.factor_version == factor.version,
            MfaRecoveryCode.code_hash == digest(code),
            MfaRecoveryCode.consumed_at.is_(None),
        )
        .with_for_update()
    )
    if not recovery:
        return False
    recovery.consumed_at = now()
    return True


def _new_recovery_codes(db: Session, factor: MfaFactor) -> list[str]:
    raw_codes = [f"lpr_{secrets.token_urlsafe(18)}" for _ in range(RECOVERY_CODE_COUNT)]
    for raw in raw_codes:
        db.add(
            MfaRecoveryCode(
                user_id=factor.user_id,
                factor_id=factor.id,
                factor_version=factor.version,
                code_hash=digest(raw),
            )
        )
    return raw_codes


@router.post("/challenge/complete")
def complete_challenge(
    body: s.MfaChallengeConfirm, request: Request, response: Response, db: Session = DB
):
    token_hash = digest(body.challenge_token)
    request.app.state.limiter.check(f"mfa-challenge-ip:{request.client.host}", 40, 900)
    request.app.state.limiter.check(f"mfa-challenge:{token_hash}", 12, 900)
    hint = db.scalar(select(MfaLoginChallenge).where(MfaLoginChallenge.token_hash == token_hash))
    if not hint:
        fail(400, "invalid_mfa_challenge", "The sign-in challenge is invalid or expired.")
    # The user's fair gate first, exclusive, before any row lock (`authority_gate`).
    authority_gate.users(db, [hint.user_id], exclusive=True)
    user = db.scalar(
        select(User)
        .where(User.id == hint.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    challenge = db.scalar(
        select(MfaLoginChallenge)
        .where(MfaLoginChallenge.id == hint.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    factor = _factor(db, hint.user_id, lock=True)
    active = bool(
        user
        and not user.disabled_at
        and not enrollment_pending(db, user.id)
        and factor
        and challenge
        and challenge.consumed_at is None
        and challenge.revoked_at is None
        and utc(challenge.expires_at) > now()
        and challenge.attempts < MAX_CHALLENGE_ATTEMPTS
        and challenge.factor_id == factor.id
        and challenge.factor_version == factor.version
    )
    if not active:
        fail(400, "invalid_mfa_challenge", "The sign-in challenge is invalid or expired.")
    if not _verify_factor_code(db, request.app.state.settings, factor, body.code):
        challenge.attempts += 1
        if challenge.attempts >= MAX_CHALLENGE_ATTEMPTS:
            challenge.revoked_at = now()
        db.commit()  # Persist the database attempt cap before returning an error.
        fail(400, "invalid_mfa_code", "The authentication code is invalid.")
    challenge.consumed_at = now()
    verified_at = now()
    issue_session(
        db,
        request,
        response,
        user,
        mfa_verified_at=verified_at,
        mfa_factor_id=factor.id,
        mfa_factor_version=factor.version,
    )
    _audit_account(db, user, "account.mfa_login", factor.id)
    return {"ok": True}


@router.post("/enrollment/start")
def start_enrollment(
    body: s.MfaEnrollmentStart, request: Request, response: Response, db: Session = DB
):
    user = session_user(db, request)
    request.app.state.limiter.check(f"mfa-enrollment:{user.id}", 8, 900)
    _key(request.app.state.settings)
    # The user's fair gate first, exclusive, before any row lock (`authority_gate`).
    authority_gate.users(db, [user.id], exclusive=True)
    user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session_user(db, request)
    if not verify_password(body.current_password, user.password_hash):
        fail(401, "invalid_credentials", "The current password is incorrect.")
    if _factor(db, user.id, lock=True):
        fail(409, "mfa_already_enabled", "Multi-factor authentication is already enabled.")
    existing = db.scalar(
        select(MfaEnrollment)
        .where(MfaEnrollment.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing:
        db.delete(existing)
        db.flush()
    enrollment_id = new_id()
    version = 1
    secret = secrets.token_bytes(20)
    enrollment = MfaEnrollment(
        id=enrollment_id,
        user_id=user.id,
        secret_ciphertext=_encrypt_secret(
            request.app.state.settings,
            "enrollment",
            user.id,
            enrollment_id,
            version,
            secret,
        ),
        factor_version=version,
        expires_at=now() + ENROLLMENT_LIFETIME,
    )
    db.add(enrollment)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    issue_session(db, request, response, user)
    encoded = base64.b32encode(secret).decode().rstrip("=")
    uri = _totp(secret).get_provisioning_uri(user.email, "LotusPond Works")
    _audit_account(db, user, "account.mfa_enrollment_started", enrollment.id)
    return {"secret": encoded, "otpauth_uri": uri, "expires_at": enrollment.expires_at}


@router.post("/enrollment/confirm")
def confirm_enrollment(
    body: s.MfaEnrollmentConfirm, request: Request, response: Response, db: Session = DB
):
    user = session_user(db, request)
    request.app.state.limiter.check(f"mfa-enrollment-confirm:{user.id}", 12, 900)
    _key(request.app.state.settings)
    # The user's fair gate first, exclusive, before any row lock (`authority_gate`).
    authority_gate.users(db, [user.id], exclusive=True)
    user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session_user(db, request)
    if _factor(db, user.id, lock=True):
        fail(409, "mfa_already_enabled", "Multi-factor authentication is already enabled.")
    enrollment = db.scalar(
        select(MfaEnrollment)
        .where(MfaEnrollment.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not enrollment or utc(enrollment.expires_at) <= now():
        fail(409, "mfa_enrollment_unavailable", "Start multi-factor enrollment again.")
    secret = _decrypt_secret(
        request.app.state.settings,
        "enrollment",
        user.id,
        enrollment.id,
        enrollment.factor_version,
        enrollment.secret_ciphertext,
    )
    counter = _matching_counter(secret, body.code, int(now().timestamp()))
    if counter is None:
        fail(400, "invalid_mfa_code", "The authentication code is invalid.")
    factor = MfaFactor(
        user_id=user.id,
        secret_ciphertext="pending",
        version=enrollment.factor_version,
        enabled_at=now(),
        last_totp_counter=counter,
    )
    db.add(factor)
    db.flush()
    factor.secret_ciphertext = _encrypt_secret(
        request.app.state.settings,
        "factor",
        user.id,
        factor.id,
        factor.version,
        secret,
    )
    codes = _new_recovery_codes(db, factor)
    db.delete(enrollment)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    _revoke_login_challenges(db, user.id)
    verified_at = now()
    issue_session(
        db,
        request,
        response,
        user,
        mfa_verified_at=verified_at,
        mfa_factor_id=factor.id,
        mfa_factor_version=factor.version,
    )
    _audit_account(db, user, "account.mfa_enabled", factor.id)
    _notify(request, db, user, "mfa_enabled", verified_at)
    return {
        "ok": True,
        "recovery_codes": codes,
        "message": "Save these recovery codes now. They will not be shown again.",
    }


def _management_context(
    body: s.MfaManagement, request: Request, db: Session
) -> tuple[User, MfaFactor]:
    user = session_user(db, request)
    request.app.state.limiter.check(f"mfa-management:{user.id}", 10, 900)
    # The user's fair gate first, exclusive, before any row lock (`authority_gate`).
    authority_gate.users(db, [user.id], exclusive=True)
    user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session_user(db, request)
    if not verify_password(body.current_password, user.password_hash):
        fail(401, "invalid_credentials", "The current password is incorrect.")
    factor = _factor(db, user.id, lock=True)
    if not factor:
        fail(409, "mfa_not_enabled", "Multi-factor authentication is not enabled.")
    if not _verify_factor_code(db, request.app.state.settings, factor, body.code):
        fail(400, "invalid_mfa_code", "The authentication code is invalid.")
    return user, factor


@router.post("/recovery-codes/regenerate")
def regenerate_recovery_codes(
    body: s.MfaManagement, request: Request, response: Response, db: Session = DB
):
    request.app.state.limiter.check(f"mfa-management-ip:{request.client.host}", 30, 900)
    user, factor = _management_context(body, request, db)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.execute(delete(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user.id))
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    codes = _new_recovery_codes(db, factor)
    verified_at = now()
    issue_session(
        db,
        request,
        response,
        user,
        mfa_verified_at=verified_at,
        mfa_factor_id=factor.id,
        mfa_factor_version=factor.version,
    )
    _audit_account(db, user, "account.mfa_recovery_regenerated", factor.id)
    _notify(request, db, user, "mfa_recovery_regenerated", verified_at)
    return {
        "ok": True,
        "recovery_codes": codes,
        "message": "Save these recovery codes now. They will not be shown again.",
    }


@router.post("/disable")
def disable_mfa(body: s.MfaManagement, request: Request, response: Response, db: Session = DB):
    request.app.state.limiter.check(f"mfa-management-ip:{request.client.host}", 30, 900)
    user, factor = _management_context(body, request, db)
    if request.app.state.settings.env == "production" and user.is_platform_admin:
        fail(
            403, "mfa_required", "Platform operators must keep multi-factor authentication enabled."
        )
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.execute(delete(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user.id))
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    db.flush()
    db.delete(factor)
    db.flush()
    issue_session(db, request, response, user)
    _audit_account(db, user, "account.mfa_disabled", user.id)
    _notify(request, db, user, "mfa_disabled", now())
    return {"ok": True}


@router.post("/members/{identifier}/reset")
def reset_member_authenticator(
    identifier: s.Identifier,
    body: s.AuthenticatorReset,
    request: Request,
    p: Principal = Depends(require("write")),
    db: Session = DB,
):
    """Let an organization administrator clear a member's lost authenticator.

    This is the assisted path for someone who lost both their authenticator and every
    recovery code. It only removes; it never issues a factor, a session or a password,
    so the administrator cannot use it to take over the account. The member re-enrols
    themselves afterwards with their own password.
    """
    require_admin(p)
    request.app.state.limiter.check(f"mfa-admin-reset:{p.user_id}", 10, 900)
    request.app.state.limiter.check(f"mfa-admin-reset-ip:{request.client.host}", 30, 900)
    workspace = db.get(Workspace, p.workspace_id)
    if not workspace or workspace.kind != "organization":
        fail(
            409,
            "organization_required",
            "Only an organization administrator can reset another member's authenticator.",
        )
    # The fair gates of both users first, exclusive, in key order (`authority_gate`).
    authority_gate.users(db, [p.user_id, identifier], exclusive=True)
    actor = db.scalar(
        select(User)
        .where(User.id == p.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not verify_password(body.current_password, actor.password_hash):
        fail(401, "invalid_credentials", "The current password is incorrect.")
    if identifier == p.user_id:
        # A different person must authorize it, the same rule release approvals use.
        fail(
            403,
            "different_administrator_required",
            "Use your own authenticator settings to change your own factor.",
        )
    if not db.get(Membership, (p.workspace_id, identifier)):
        fail(404, "not_found", "That person is not a member of this organization.")
    member = db.scalar(
        select(User)
        .where(User.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not member:
        fail(404, "not_found", "That person is not a member of this organization.")
    if member.is_platform_admin:
        # An organization administrator must not be able to clear a factor that
        # protects the whole installation.
        fail(
            403,
            "forbidden",
            "A platform operator's authenticator cannot be reset from a workspace.",
        )
    factor = _factor(db, member.id, lock=True)
    if not factor:
        fail(409, "mfa_not_enabled", "That member has no authenticator to reset.")
    db.execute(delete(LoginSession).where(LoginSession.user_id == member.id))
    db.execute(delete(MfaLoginChallenge).where(MfaLoginChallenge.user_id == member.id))
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == member.id))
    db.execute(delete(MfaEnrollment).where(MfaEnrollment.user_id == member.id))
    db.flush()
    db.delete(factor)
    db.flush()
    audit(db, p, "members.authenticator_reset", member.id, {"reason": body.reason})
    _notify(request, db, member, "mfa_admin_reset", now())
    return {
        "ok": True,
        "member_id": member.id,
        "message": "The member signs in with their password and enrols a new authenticator.",
    }


def mfa_status(db: Session, request: Request, user: User) -> dict:
    factor = _factor(db, user.id)
    remaining = 0
    if factor:
        remaining = db.scalar(
            select(func.count())
            .select_from(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.factor_id == factor.id,
                MfaRecoveryCode.factor_version == factor.version,
                MfaRecoveryCode.consumed_at.is_(None),
            )
        )
    return {
        "available": request.app.state.settings.mfa_encryption_key is not None,
        "enabled": factor is not None,
        "session_verified": session_mfa_verified(db, request, factor),
        "recovery_codes_remaining": remaining,
        "required_for_privileged": (
            request.app.state.settings.env == "production" and user.is_platform_admin
        ),
    }
