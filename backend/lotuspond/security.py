import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import time

import redis
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings
from .identity_status import enrollment_pending, identity_verified
from .models import ApiKey, LoginSession, Membership, MfaFactor, User, Workspace, now

password_hasher = PasswordHasher()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def new_token() -> str:
    return secrets.token_urlsafe(32)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def fail(status: int, code: str, message: str):
    raise HTTPException(status, {"error": code, "message": message})


_CREDENTIAL_KEY = re.compile(r"password|secret|authorization|cookie|api.?key", re.I)
_TOKEN_KEY = re.compile(r"token", re.I)
_TOKEN_COUNT_KEY = re.compile(r"tokens$", re.I)


def _count(value) -> bool:
    # OTLP carries 64-bit integers as decimal strings, so a count may arrive as one.
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, str) and 0 < len(value) <= 20 and value.isascii() and value.isdigit()


def _sensitive(key: str, value) -> bool:
    if _CREDENTIAL_KEY.search(key):
        return True
    # A usage count such as gen_ai.usage.input_tokens is evidence, not a credential; any
    # other key that mentions a token is treated as one.
    return bool(_TOKEN_KEY.search(key)) and not (_TOKEN_COUNT_KEY.search(key) and _count(value))


def redact(value):
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _sensitive(key, item) else redact(item)
            for key, item in value.items()
        }
    return value


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC)


def current_session(db: Session, request: Request, *, lock: bool = False) -> LoginSession | None:
    query = select(LoginSession).where(
        LoginSession.token_hash == digest(request.cookies.get("lotuspond_session", "")),
        LoginSession.expires_at > now(),
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def issue_session(
    db: Session,
    request: Request,
    response: Response,
    user: User,
    *,
    mfa_verified_at: datetime | None = None,
    mfa_factor_id: str | None = None,
    mfa_factor_version: int | None = None,
):
    binding = (mfa_verified_at, mfa_factor_id, mfa_factor_version)
    if any(value is None for value in binding) and any(value is not None for value in binding):
        raise RuntimeError("Incomplete MFA session binding")
    db.execute(
        delete(LoginSession).where(
            LoginSession.user_id == user.id, LoginSession.expires_at <= now()
        )
    )
    older_ids = list(
        db.scalars(
            select(LoginSession.id)
            .where(LoginSession.user_id == user.id)
            .order_by(LoginSession.created_at.desc(), LoginSession.id.desc())
            .offset(19)
        )
    )
    if older_ids:
        db.execute(delete(LoginSession).where(LoginSession.id.in_(older_ids)))
    settings = request.app.state.settings
    token = new_token()
    db.add(
        LoginSession(
            user_id=user.id,
            token_hash=digest(token),
            expires_at=now() + timedelta(hours=settings.session_hours),
            mfa_verified_at=mfa_verified_at,
            mfa_factor_id=mfa_factor_id,
            mfa_factor_version=mfa_factor_version,
        )
    )
    response.set_cookie(
        "lotuspond_session",
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.env == "production",
        samesite="strict",
        path="/",
    )


def authenticator_enabled(db: Session, user_id: str, *, lock: bool = False) -> bool:
    query = select(MfaFactor).where(MfaFactor.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query) is not None


def _require_workspace_authenticator(request: Request, db: Session, workspace: Workspace, user_id):
    """An installation or organization requirement holds for every human authority it covers.

    Account pages and enrollment never pass through here, so a person kept out by either
    requirement can always still add the authenticator that lets them back in.
    """
    settings = request.app.state.settings
    installation_wide = settings.require_mfa
    organization_wide = workspace.kind == "organization" and workspace.require_mfa
    if not installation_wide and not organization_wide:
        return
    if not settings.mfa_encryption_key:
        fail(503, "mfa_unavailable", "Multi-factor authentication is unavailable.")
    if not user_id or not authenticator_enabled(db, user_id):
        fail(
            403,
            "mfa_required",
            "This installation requires an authenticator for workspace access."
            if installation_wide
            else "This organization requires an authenticator for access.",
        )


@dataclass(frozen=True)
class Principal:
    actor_id: str
    workspace_id: str
    role: str
    user_id: str | None = None


def session_user(db: Session, request: Request) -> User:
    session = current_session(db, request)
    user = db.scalar(
        select(User)
        .where(
            User.id == (session.user_id if session else ""),
            User.disabled_at.is_(None),
        )
    )
    if not user or enrollment_pending(db, user.id):
        fail(401, "unauthenticated", "Sign in to continue.")
    factor = db.scalar(select(MfaFactor).where(MfaFactor.user_id == user.id))
    if factor and (
        not request.app.state.settings.mfa_encryption_key
        or not session
        or session.mfa_verified_at is None
        or session.mfa_factor_id != factor.id
        or session.mfa_factor_version != factor.version
    ):
        fail(401, "unauthenticated", "Sign in to continue.")
    return user


def authenticate(db: Session, request: Request, scope: str = "read") -> Principal:
    authorization = request.headers.get("authorization")
    if authorization:
        if not authorization.startswith("Bearer lp_"):
            fail(401, "unauthenticated", "A valid Lotuspond API key is required.")
        key = db.scalar(
            select(ApiKey).where(
                ApiKey.token_hash == digest(authorization[7:]),
                ApiKey.revoked_at.is_(None),
                ApiKey.expires_at > datetime.now(UTC),
            )
        )
        if not key:
            fail(401, "unauthenticated", "The API key is invalid or expired.")
        if scope not in key.scopes:
            fail(403, "forbidden", f"The API key requires the {scope} scope.")
        workspace = db.get(Workspace, key.workspace_id)
        if not workspace or workspace.status != "active":
            fail(403, "workspace_suspended", "Workspace access is suspended.")
        creator = db.get(User, key.created_by_user_id) if key.created_by_user_id else None
        if key.created_by_user_id and (
            not creator or creator.disabled_at or enrollment_pending(db, creator.id)
        ):
            fail(401, "unauthenticated", "The API key owner is not active.")
        if request.app.state.settings.env == "production" and workspace.kind == "organization":
            if not creator or not identity_verified(db, creator):
                fail(403, "identity_unverified", "A verified account must authorize this API key.")
        # A scoped key carries its creator's authority, so the policy follows that person.
        _require_workspace_authenticator(request, db, workspace, creator.id if creator else None)
        return Principal(key.id, key.workspace_id, "service")
    user = session_user(db, request)
    workspace_id = request.headers.get("x-lotuspond-workspace", "")
    member = db.get(Membership, (workspace_id, user.id))
    workspace = db.get(Workspace, workspace_id)
    if not member or not workspace:
        fail(403, "forbidden", "This account does not have access to the workspace.")
    if workspace.status != "active":
        fail(403, "workspace_suspended", "Workspace access is suspended.")
    if (
        request.app.state.settings.env == "production"
        and workspace.kind == "organization"
        and not identity_verified(db, user)
    ):
        fail(403, "identity_unverified", "A verified identity is required for organization access.")
    _require_workspace_authenticator(request, db, workspace, user.id)
    if scope != "read" and member.role == "viewer":
        fail(403, "forbidden", "Your role has read-only access.")
    return Principal(user.id, workspace_id, member.role, user.id)


def require_admin(principal: Principal):
    if not principal.user_id or principal.role not in {"owner", "admin"}:
        fail(403, "forbidden", "A workspace administrator must perform this action.")


def platform_admin(db: Session, request: Request) -> User:
    user = session_user(db, request)
    if request.app.state.settings.env == "production" and not identity_verified(db, user):
        fail(403, "identity_unverified", "A verified identity is required for operator access.")
    if not user.is_platform_admin:
        fail(403, "forbidden", "Platform operator access is required.")
    if request.app.state.settings.env == "production":
        if not request.app.state.settings.mfa_encryption_key:
            fail(503, "mfa_unavailable", "Multi-factor authentication is unavailable.")
        factor = db.scalar(select(MfaFactor).where(MfaFactor.user_id == user.id))
        session = current_session(db, request)
        if (
            not factor
            or not session
            or session.mfa_verified_at is None
            or session.mfa_factor_id != factor.id
            or session.mfa_factor_version != factor.version
        ):
            fail(403, "mfa_required", "Multi-factor authentication is required.")
    return user


class RateLimiter:
    def __init__(self, settings: Settings):
        self.client = (
            redis.Redis.from_url(
                settings.redis_url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
            )
            if settings.redis_url
            else None
        )
        self.production = settings.env == "production"
        self.entries: dict[str, tuple[int, float]] = {}
        self.lock = Lock()

    def check(self, key: str, limit: int, seconds: int = 60):
        if self.client:
            try:
                count = self.client.eval(
                    "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
                    1,
                    f"lotuspond:rate:{digest(key)}",
                    seconds,
                )
            except redis.RedisError:
                fail(503, "rate_store_unavailable", "Request quota enforcement is unavailable.")
        else:
            if self.production:
                fail(503, "rate_store_unavailable", "Request quota enforcement is unavailable.")
            with self.lock:
                current = time()
                self.entries = {
                    key: item for key, item in self.entries.items() if item[1] > current
                }
                count, expiry = self.entries.get(key, (0, current + seconds))
                count += 1
                if len(self.entries) >= 10000:
                    fail(429, "rate_limited", "Request capacity is temporarily exhausted.")
                self.entries[key] = (count, expiry)
        if count > limit:
            fail(429, "rate_limited", "Request limit reached. Try again later.")
