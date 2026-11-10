import re
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import schemas as s
from .deps import DB, audit, require
from .identity_lifecycle import validate_registration_invitation
from .identity_status import enrollment_pending, identity_verification_source, identity_verified
from .mail import enqueue_security_notice
from .mfa import begin_login_challenge, mfa_status
from .models import (
    ApiKey,
    Approval,
    AuditEvent,
    IdentityChallenge,
    LoginSession,
    Membership,
    MfaEnrollment,
    MfaFactor,
    MfaLoginChallenge,
    Project,
    Run,
    User,
    Workspace,
    new_id,
    now,
    serialize,
)
from .security import (
    Principal,
    authenticator_enabled,
    current_session,
    digest,
    fail,
    issue_session,
    new_token,
    password_hasher,
    platform_admin,
    require_admin,
    session_user,
    verify_password,
)

router = APIRouter(prefix="/api/v1")

# The retired subscription plan and gateway budget keep their columns so stored
# data survives, but no response reports them as if they still applied.
RETIRED_WORKSPACE_FIELDS = {"plan", "monthly_budget_usd"}


def _workspace(workspace: Workspace) -> dict:
    return serialize(workspace, exclude=RETIRED_WORKSPACE_FIELDS)


def create_workspace(db: Session, user: User, name: str, kind: str) -> Workspace:
    identifier = new_id()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "workspace"
    workspace = Workspace(
        id=identifier, name=name[:160], kind=kind, owner_id=user.id, slug=f"{slug}-{identifier[:8]}"
    )
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    return workspace


@router.post("/auth/register", status_code=201)
def register(body: s.Register, request: Request, response: Response, db: Session = DB):
    email = str(body.email).lower()
    request.app.state.limiter.check(f"register:{request.client.host}", 10, 900)
    invitation = validate_registration_invitation(db, body.invitation_token, email)
    if body.invitation_token and not invitation:
        fail(403, "registration_closed", "Account registration is currently invitation-only.")
    if not request.app.state.settings.registration_enabled and not invitation:
        fail(403, "registration_closed", "Account registration is currently invitation-only.")
    if request.app.state.settings.env == "production" and body.account_type == "organization":
        fail(
            403,
            "identity_unverified",
            "Create an individual account and verify its mailbox before creating an organization.",
        )
    user = User(
        id=new_id(),
        name=body.name,
        email=email,
        password_hash=password_hasher.hash(body.password),
    )
    db.add(user)
    db.flush()
    personal = create_workspace(db, user, f"{body.name}'s workspace", "personal")
    selected = personal
    if body.account_type == "organization":
        selected = create_workspace(db, user, body.workspace, "organization")
    issue_session(db, request, response, user)
    audit(db, Principal(user.id, selected.id, "owner", user.id), "account.created", user.id)
    db.flush()
    return {"workspaceId": selected.id, "account_type": body.account_type}


@router.post("/auth/login")
def login(body: s.Login, request: Request, response: Response, db: Session = DB):
    request.app.state.limiter.check(f"login:{request.client.host}", 30, 900)
    request.app.state.limiter.check(f"login-account:{str(body.email).lower()}", 15, 900)
    user = db.scalar(select(User).where(User.email == str(body.email).lower()).with_for_update())
    valid = verify_password(body.password, user.password_hash) if user else False
    if not user:
        password_hasher.hash(body.password)
    if not valid or user.disabled_at or enrollment_pending(db, user.id):
        fail(401, "invalid_credentials", "Email or password is incorrect.")
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = password_hasher.hash(body.password)
    challenge = begin_login_challenge(db, request, response, user)
    if challenge:
        response.status_code = 202
        return challenge
    issue_session(db, request, response, user)
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = DB):
    db.execute(
        delete(LoginSession).where(
            LoginSession.token_hash == digest(request.cookies.get("lotuspond_session", ""))
        )
    )
    response.delete_cookie("lotuspond_session", path="/")
    return {"ok": True}


def audit_account(db: Session, user: User, action: str, resource_id: str):
    personal = personal_workspace(db, user)
    if personal:
        audit(db, Principal(user.id, personal.id, "owner", user.id), action, resource_id)


def personal_workspace(db: Session, user: User) -> Workspace | None:
    return db.scalar(
        select(Workspace).where(Workspace.owner_id == user.id, Workspace.kind == "personal")
    )


@router.get("/auth/sessions")
def sessions(request: Request, db: Session = DB):
    user = session_user(db, request)
    token_hash = digest(request.cookies.get("lotuspond_session", ""))
    return {
        "items": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "current": row.token_hash == token_hash,
            }
            for row in db.scalars(
                select(LoginSession)
                .where(LoginSession.user_id == user.id, LoginSession.expires_at > now())
                .order_by(LoginSession.created_at.desc(), LoginSession.id.desc())
                .limit(100)
            )
        ]
    }


@router.delete("/auth/sessions/{identifier}")
def revoke_session(
    identifier: s.Identifier, request: Request, response: Response, db: Session = DB
):
    user = session_user(db, request)
    session = db.scalar(
        select(LoginSession)
        .where(LoginSession.id == identifier, LoginSession.user_id == user.id)
        .with_for_update()
    )
    if not session:
        fail(404, "not_found", "Session not found.")
    current = session.token_hash == digest(request.cookies.get("lotuspond_session", ""))
    db.delete(session)
    audit_account(db, user, "account.session_revoked", identifier)
    if current:
        response.delete_cookie("lotuspond_session", path="/")
    return {"ok": True, "signed_out": current}


@router.post("/auth/password")
def change_password(body: s.PasswordChange, request: Request, response: Response, db: Session = DB):
    user = session_user(db, request)
    request.app.state.limiter.check(f"password-change:{user.id}", 5, 900)
    user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session = current_session(db, request, lock=True)
    # Recheck the session after acquiring the user lock: another password change
    # may have revoked the cookie while this request was waiting.
    session_user(db, request)
    if not verify_password(body.current_password, user.password_hash):
        fail(401, "invalid_credentials", "The current password is incorrect.")
    if body.current_password == body.new_password:
        fail(422, "unchanged_password", "Choose a different password.")
    user.password_hash = password_hasher.hash(body.new_password)
    for challenge in db.scalars(
        select(IdentityChallenge)
        .where(
            IdentityChallenge.user_id == user.id,
            IdentityChallenge.kind == "password_reset",
            IdentityChallenge.consumed_at.is_(None),
            IdentityChallenge.revoked_at.is_(None),
        )
        .with_for_update()
    ):
        challenge.revoked_at = now()
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.execute(delete(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user.id))
    db.execute(delete(MfaEnrollment).where(MfaEnrollment.user_id == user.id))
    issue_session(
        db,
        request,
        response,
        user,
        mfa_verified_at=session.mfa_verified_at,
        mfa_factor_id=session.mfa_factor_id,
        mfa_factor_version=session.mfa_factor_version,
    )
    audit_account(db, user, "account.password_changed", user.id)
    personal = personal_workspace(db, user)
    enqueue_security_notice(
        db,
        request.app.state.settings,
        user=user,
        change="password_changed",
        occurred_at=now(),
        workspace_id=personal.id if personal else None,
    )
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = DB):
    user = session_user(db, request)
    rows = db.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at)
    ).all()
    verified_source = identity_verification_source(db, user)
    if request.app.state.settings.env == "production" and not verified_source:
        rows = [(workspace, role) for workspace, role in rows if workspace.kind == "personal"]
    return {
        "user": {
            **serialize(user),
            "identity_verified": verified_source is not None,
            "identity_verification_source": verified_source,
        },
        "workspaces": [{**_workspace(workspace), "role": role} for workspace, role in rows],
        "capabilities": {
            "mail_enabled": request.app.state.settings.mail_enabled,
            "registration_enabled": request.app.state.settings.registration_enabled,
            "offline_enrollment_enabled": request.app.state.settings.network_mode == "airgap",
        },
        "mfa": mfa_status(db, request, user),
    }


@router.post("/organizations", status_code=201)
def create_organization(body: s.Organization, request: Request, db: Session = DB):
    user = session_user(db, request)
    if request.app.state.settings.env == "production" and not identity_verified(db, user):
        fail(403, "identity_unverified", "A verified identity is required to create organizations.")
    request.app.state.limiter.check(f"organizations:{user.id}", 10, 3600)
    workspace = create_workspace(db, user, body.name, "organization")
    audit(
        db, Principal(user.id, workspace.id, "owner", user.id), "organization.created", workspace.id
    )
    db.flush()
    return _workspace(workspace)


@router.get("/members")
def members(p: Principal = Depends(require()), db: Session = DB):
    require_admin(p)
    rows = db.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == p.workspace_id)
        .order_by(User.name)
    ).all()
    return {
        "items": [
            {"id": user.id, "name": user.name, "email": user.email, "role": role}
            for user, role in rows
        ]
    }


@router.post("/members", status_code=201)
def add_member(
    body: s.MemberInput,
    request: Request,
    p: Principal = Depends(require("write")),
    db: Session = DB,
):
    require_admin(p)
    workspace = db.get(Workspace, p.workspace_id)
    if workspace.kind != "organization":
        fail(
            409, "organization_required", "Create an organization to collaborate with other people."
        )
    user = db.scalar(
        select(User).where(User.email == str(body.email).lower(), User.disabled_at.is_(None))
    )
    if not user or enrollment_pending(db, user.id):
        fail(
            400,
            "account_required",
            "The person must create a Lotuspond account before being added.",
        )
    if request.app.state.settings.env == "production" and not identity_verified(db, user):
        fail(
            409,
            "identity_unverified",
            "Verify this person's identity before granting organization access.",
        )
    db.add(Membership(workspace_id=p.workspace_id, user_id=user.id, role=body.role))
    audit(db, p, "members.added", user.id)
    db.flush()
    return {"ok": True}


@router.get("/api-keys")
def keys(p: Principal = Depends(require()), db: Session = DB):
    require_admin(p)
    return {
        "items": [
            serialize(row)
            for row in db.scalars(
                select(ApiKey)
                .where(ApiKey.workspace_id == p.workspace_id)
                .order_by(ApiKey.created_at.desc())
            )
        ]
    }


@router.post("/api-keys", status_code=201)
def create_key(body: s.KeyInput, p: Principal = Depends(require("write")), db: Session = DB):
    require_admin(p)
    raw = f"lp_{new_token()}"
    key = ApiKey(
        id=new_id(),
        workspace_id=p.workspace_id,
        name=body.name,
        scopes=sorted(set(body.scopes)),
        token_hash=digest(raw),
        prefix=raw[:11],
        created_by_user_id=p.user_id,
        expires_at=now() + timedelta(days=body.days),
    )
    db.add(key)
    audit(db, p, "api_keys.created", key.id)
    db.flush()
    return {"id": key.id, "key": raw, "message": "Save this key now. It will not be shown again."}


@router.post("/api-keys/{identifier}/revoke")
def revoke_key(
    identifier: s.Identifier, p: Principal = Depends(require("write")), db: Session = DB
):
    require_admin(p)
    key = db.scalar(
        select(ApiKey)
        .where(
            ApiKey.id == identifier,
            ApiKey.workspace_id == p.workspace_id,
            ApiKey.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if not key:
        fail(404, "not_found", "Active API key not found.")
    key.revoked_at = now()
    audit(db, p, "api_keys.revoked", key.id)
    return {"ok": True}


def _members_without_authenticator(db: Session, workspace_id: str) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Membership)
        .outerjoin(MfaFactor, MfaFactor.user_id == Membership.user_id)
        .where(Membership.workspace_id == workspace_id, MfaFactor.id.is_(None))
    )


@router.get("/settings")
def settings(request: Request, p: Principal = Depends(require()), db: Session = DB):
    workspace = db.get(Workspace, p.workspace_id)
    policy = {
        "require_mfa": workspace.require_mfa,
        "available": request.app.state.settings.mfa_encryption_key is not None,
        "manageable": workspace.kind == "organization",
        "required_by_installation": request.app.state.settings.require_mfa,
    }
    if p.user_id and p.role in {"owner", "admin"}:
        policy["members_without_authenticator"] = _members_without_authenticator(
            db, workspace.id
        )
    return {
        **_workspace(workspace),
        "authenticator_policy": policy,
    }


@router.post("/settings/authenticator-policy")
def update_authenticator_policy(
    body: s.AuthenticatorPolicyInput,
    request: Request,
    p: Principal = Depends(require("write")),
    db: Session = DB,
):
    require_admin(p)
    if not request.app.state.settings.mfa_encryption_key:
        fail(503, "mfa_unavailable", "Multi-factor authentication is unavailable.")
    # Lock the actor and then the workspace so a concurrent factor removal cannot slip between
    # the check below and the stored requirement.
    db.scalar(
        select(User)
        .where(User.id == p.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    workspace = db.scalar(
        select(Workspace)
        .where(Workspace.id == p.workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if workspace.kind != "organization":
        fail(409, "policy_unavailable", "Only organizations can require an authenticator.")
    # The acting administrator must already hold a factor, so the policy cannot lock out its author.
    if not authenticator_enabled(db, p.user_id, lock=True):
        fail(403, "mfa_required", "Enable your own authenticator before changing this policy.")
    if workspace.require_mfa == body.require_mfa:
        return {
            "ok": True,
            "require_mfa": workspace.require_mfa,
            "members_without_authenticator": _members_without_authenticator(db, workspace.id),
        }
    workspace.require_mfa = body.require_mfa
    remaining = _members_without_authenticator(db, workspace.id)
    audit(
        db,
        p,
        "settings.authenticator_policy_changed",
        workspace.id,
        {"require_mfa": body.require_mfa, "members_without_authenticator": remaining},
    )
    return {"ok": True, "require_mfa": body.require_mfa, "members_without_authenticator": remaining}


@router.patch("/settings")
def update_settings(
    body: s.WorkspaceInput, p: Principal = Depends(require("write")), db: Session = DB
):
    require_admin(p)
    workspace = db.get(Workspace, p.workspace_id)
    workspace.name = body.name
    audit(db, p, "settings.updated", workspace.id)
    return {"ok": True}


@router.get("/admin/overview")
def admin_overview(request: Request, db: Session = DB):
    platform_admin(db, request)
    return {
        "users": db.scalar(select(func.count()).select_from(User)),
        "workspaces": db.scalar(select(func.count()).select_from(Workspace)),
        "projects": db.scalar(select(func.count()).select_from(Project)),
        "runs": db.scalar(select(func.count()).select_from(Run)),
        "pending_approvals": db.scalar(
            select(func.count()).select_from(Approval).where(Approval.status == "pending")
        ),
    }


@router.get("/admin/workspaces")
def admin_workspaces(request: Request, db: Session = DB, limit: int = 100):
    platform_admin(db, request)
    return {
        "items": [
            _workspace(row)
            for row in db.scalars(
                select(Workspace)
                .order_by(Workspace.created_at.desc())
                .limit(min(max(limit, 1), 100))
            )
        ]
    }


@router.get("/admin/users")
def admin_users(request: Request, db: Session = DB, limit: int = 100):
    platform_admin(db, request)
    return {
        "items": [
            serialize(row)
            for row in db.scalars(
                select(User).order_by(User.created_at.desc()).limit(min(max(limit, 1), 100))
            )
        ]
    }


@router.patch("/admin/workspaces/{identifier}")
def admin_workspace_status(
    identifier: s.Identifier, body: s.WorkspaceStatus, request: Request, db: Session = DB
):
    user = platform_admin(db, request)
    workspace = db.scalar(select(Workspace).where(Workspace.id == identifier).with_for_update())
    if not workspace:
        fail(404, "not_found", "Workspace not found.")
    previous = workspace.status
    workspace.status = body.status
    db.add(
        AuditEvent(
            workspace_id=workspace.id,
            actor_id=user.id,
            action="admin.workspace_status",
            resource_id=workspace.id,
            details={"from": previous, "to": body.status, "reason": body.reason},
        )
    )
    return {"ok": True, "status": workspace.status}
