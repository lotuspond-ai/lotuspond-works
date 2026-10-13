from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .deps import DB
from .identity_status import enrollment_pending
from .models import (
    ApiKey,
    AuditEvent,
    LoginSession,
    Membership,
    MfaEnrollment,
    MfaFactor,
    MfaLoginChallenge,
    MfaRecoveryCode,
    OfflineEnrollment,
    OfflineIdentityAttestation,
    Project,
    User,
    Workspace,
    new_id,
    now,
)
from .security import digest, fail, new_token, password_hasher, utc

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
AttestationSource = Literal["in_person", "government_credential", "enterprise_record"]
ENROLLMENT_PREFIX = "lpe_"
EMAIL_ADAPTER = TypeAdapter(EmailStr)
NAME_ADAPTER = TypeAdapter(Name)


class EnrollmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: str = Field(min_length=40, max_length=200)


class EnrollmentComplete(EnrollmentInput):
    password: str = Field(min_length=12, max_length=256)


@dataclass(frozen=True)
class ProvisioningRequest:
    email: str
    name: str
    attestation_source: AttestationSource
    source_reference: str
    operator_reference: str
    expires_hours: int = 24
    organization_name: str | None = None
    project_names: tuple[str, ...] = ()
    platform_admin: bool = False


@dataclass(frozen=True)
class ProvisioningResult:
    user_id: str
    selected_workspace_id: str
    enrollment_id: str
    expires_at: object
    token: str


@dataclass(frozen=True)
class ReissueRequest:
    user_id: str
    email: str
    operator_reference: str
    expires_hours: int = 24


@dataclass(frozen=True)
class RevocationRequest:
    user_id: str
    email: str
    operator_reference: str
    reason: str


router = APIRouter(prefix="/api/v1/auth/offline-enrollment")


def _workspace(db: Session, user: User, name: str, kind: str) -> Workspace:
    identifier = new_id()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "workspace"
    workspace = Workspace(
        id=identifier,
        name=name[:160],
        kind=kind,
        owner_id=user.id,
        slug=f"{slug}-{identifier[:8]}",
    )
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    return workspace


def provision_identity(db: Session, request: ProvisioningRequest) -> ProvisioningResult:
    email = str(EMAIL_ADAPTER.validate_python(request.email)).lower()
    name = NAME_ADAPTER.validate_python(request.name)
    source_reference = NAME_ADAPTER.validate_python(request.source_reference)
    operator_reference = NAME_ADAPTER.validate_python(request.operator_reference)
    if not 1 <= request.expires_hours <= 72:
        raise ValueError("Enrollment expiry must be between 1 and 72 hours")
    if request.project_names and not request.organization_name:
        raise ValueError("Provisioned projects require an explicit organization")
    if len(request.project_names) > 50:
        raise ValueError("At most 50 projects may be provisioned")
    if db.scalar(select(User.id).where(User.email == email)):
        raise ValueError("The account already exists; existing users are never overwritten")

    user = User(
        id=new_id(),
        email=email,
        name=name,
        password_hash=password_hasher.hash(new_token()),
        is_platform_admin=request.platform_admin,
        email_verified=False,
    )
    db.add(user)
    db.flush()
    personal = _workspace(db, user, f"{name}'s workspace", "personal")
    selected = personal
    if request.organization_name:
        organization_name = NAME_ADAPTER.validate_python(request.organization_name)
        selected = _workspace(db, user, organization_name, "organization")
        seen: set[str] = set()
        for raw_project in request.project_names:
            project_name = NAME_ADAPTER.validate_python(raw_project)
            normalized = project_name.casefold()
            if normalized in seen:
                raise ValueError("Provisioned project names must be unique")
            seen.add(normalized)
            db.add(
                Project(
                    workspace_id=selected.id,
                    name=project_name,
                    description="Provisioned by the offline operator.",
                )
            )

    attestation = OfflineIdentityAttestation(
        id=new_id(),
        user_id=user.id,
        source=request.attestation_source,
        source_reference=source_reference,
        operator_reference=operator_reference,
        attested_at=now(),
    )
    raw_token = f"{ENROLLMENT_PREFIX}{new_token()}"
    expires_at = now() + timedelta(hours=request.expires_hours)
    enrollment = OfflineEnrollment(
        id=new_id(),
        user_id=user.id,
        attestation_id=attestation.id,
        token_hash=digest(raw_token),
        expires_at=expires_at,
    )
    db.add(attestation)
    db.flush()
    db.add(enrollment)
    db.add(
        AuditEvent(
            workspace_id=personal.id,
            actor_id=attestation.id,
            action="account.offline_provisioned",
            resource_id=user.id,
            details={
                "attestation_source": request.attestation_source,
                "source_reference": source_reference,
                "operator_reference": operator_reference,
                "organization_id": selected.id if selected.kind == "organization" else None,
                "platform_admin_requested": request.platform_admin,
            },
        )
    )
    db.flush()
    return ProvisioningResult(
        user_id=user.id,
        selected_workspace_id=selected.id,
        enrollment_id=enrollment.id,
        expires_at=expires_at,
        token=raw_token,
    )


def reissue_enrollment(db: Session, request: ReissueRequest) -> ProvisioningResult:
    if not 1 <= request.expires_hours <= 72:
        raise ValueError("Enrollment expiry must be between 1 and 72 hours")
    email = str(EMAIL_ADAPTER.validate_python(request.email)).lower()
    user_id = str(UUID(request.user_id))
    operator_reference = NAME_ADAPTER.validate_python(request.operator_reference)
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or user.email != email:
        raise ValueError("Exact pending user ID and matching email are required")
    if user.disabled_at is not None:
        raise ValueError("Disabled accounts cannot receive a new enrollment token")
    enrollments = list(
        db.scalars(
            select(OfflineEnrollment)
            .where(OfflineEnrollment.user_id == user.id)
            .with_for_update()
        )
    )
    if not enrollments or any(item.consumed_at is not None for item in enrollments):
        raise ValueError("Only an existing uncompleted offline enrollment can be reissued")
    attestation = db.scalar(
        select(OfflineIdentityAttestation)
        .where(
            OfflineIdentityAttestation.user_id == user.id,
            OfflineIdentityAttestation.revoked_at.is_(None),
        )
        .order_by(OfflineIdentityAttestation.attested_at.desc())
        .with_for_update()
        .limit(1)
    )
    if (
        not attestation
        or utc(attestation.attested_at) > now()
        or (
            attestation.expires_at is not None
            and utc(attestation.expires_at) <= now()
        )
    ):
        raise ValueError("The offline identity attestation is no longer active")
    revoked_at = now()
    for item in enrollments:
        if item.revoked_at is None:
            item.revoked_at = revoked_at
    raw_token = f"{ENROLLMENT_PREFIX}{new_token()}"
    expires_at = now() + timedelta(hours=request.expires_hours)
    enrollment = OfflineEnrollment(
        id=new_id(),
        user_id=user.id,
        attestation_id=attestation.id,
        token_hash=digest(raw_token),
        expires_at=expires_at,
    )
    db.add(enrollment)
    personal = db.scalar(
        select(Workspace)
        .where(Workspace.owner_id == user.id, Workspace.kind == "personal")
        .order_by(Workspace.created_at)
        .limit(1)
    )
    if not personal:
        raise ValueError("The pending account has no personal workspace")
    db.add(
        AuditEvent(
            workspace_id=personal.id,
            actor_id=attestation.id,
            action="account.offline_enrollment_reissued",
            resource_id=user.id,
            details={
                "attestation_id": attestation.id,
                "operator_reference": operator_reference,
                "revoked_enrollment_ids": [item.id for item in enrollments],
            },
        )
    )
    db.flush()
    return ProvisioningResult(
        user_id=user.id,
        selected_workspace_id=personal.id,
        enrollment_id=enrollment.id,
        expires_at=expires_at,
        token=raw_token,
    )


def revoke_identity(db: Session, request: RevocationRequest) -> dict[str, object]:
    email = str(EMAIL_ADAPTER.validate_python(request.email)).lower()
    user_id = str(UUID(request.user_id))
    operator_reference = NAME_ADAPTER.validate_python(request.operator_reference)
    reason = NAME_ADAPTER.validate_python(request.reason)
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or user.email != email:
        raise ValueError("Exact offline user ID and matching email are required")
    enrollments = list(
        db.scalars(
            select(OfflineEnrollment)
            .where(OfflineEnrollment.user_id == user.id)
            .order_by(OfflineEnrollment.id)
            .with_for_update()
        )
    )
    attestations = list(
        db.scalars(
            select(OfflineIdentityAttestation)
            .where(OfflineIdentityAttestation.user_id == user.id)
            .order_by(OfflineIdentityAttestation.id)
            .with_for_update()
        )
    )
    if not enrollments or not attestations:
        raise ValueError("The account was not provisioned through offline identity")
    revoked_at = now()
    revoked_attestations = 0
    revoked_enrollments = 0
    for item in attestations:
        if item.revoked_at is None:
            item.revoked_at = max(revoked_at, utc(item.attested_at))
            revoked_attestations += 1
    for item in enrollments:
        if item.consumed_at is None and item.revoked_at is None:
            item.revoked_at = revoked_at
            revoked_enrollments += 1
    user.disabled_at = revoked_at
    sessions = db.execute(delete(LoginSession).where(LoginSession.user_id == user.id)).rowcount
    factor = db.scalar(
        select(MfaFactor).where(MfaFactor.user_id == user.id).with_for_update()
    )
    db.execute(delete(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user.id))
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    db.execute(delete(MfaEnrollment).where(MfaEnrollment.user_id == user.id))
    if factor:
        db.delete(factor)
    keys = list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.created_by_user_id == user.id, ApiKey.revoked_at.is_(None))
            .with_for_update()
        )
    )
    for key in keys:
        key.revoked_at = revoked_at
    personal = db.scalar(
        select(Workspace)
        .where(Workspace.owner_id == user.id, Workspace.kind == "personal")
        .order_by(Workspace.created_at)
        .limit(1)
    )
    actor_id = attestations[-1].id
    db.add(
        AuditEvent(
            workspace_id=personal.id if personal else None,
            actor_id=actor_id,
            action="account.offline_identity_revoked",
            resource_id=user.id,
            details={
                "operator_reference": operator_reference,
                "reason": reason,
                "revoked_attestations": revoked_attestations,
                "revoked_pending_enrollments": revoked_enrollments,
                "revoked_sessions": sessions,
                "revoked_api_keys": len(keys),
            },
        )
    )
    db.flush()
    return {
        "user_id": user.id,
        "revoked_attestations": revoked_attestations,
        "revoked_pending_enrollments": revoked_enrollments,
        "revoked_sessions": sessions,
        "revoked_api_keys": len(keys),
    }


def _validate_enrollment(enrollment, attestation, user):
    if not enrollment:
        fail(400, "invalid_enrollment", "The enrollment token is invalid.")
    if enrollment.consumed_at is not None or enrollment.revoked_at is not None:
        fail(409, "enrollment_unavailable", "The enrollment token is no longer available.")
    if utc(enrollment.expires_at) <= now():
        fail(410, "enrollment_expired", "The enrollment token has expired.")
    if (
        not attestation
        or attestation.user_id != enrollment.user_id
        or utc(attestation.attested_at) > now()
        or attestation.revoked_at is not None
        or (
            attestation.expires_at is not None
            and utc(attestation.expires_at) <= now()
        )
    ):
        fail(409, "enrollment_unavailable", "The enrollment is no longer authorized.")
    if not user or user.disabled_at is not None:
        fail(409, "enrollment_unavailable", "The enrollment is no longer authorized.")
    return enrollment, attestation, user


def _load_enrollment(db: Session, raw_token: str):
    query = select(OfflineEnrollment).where(
        OfflineEnrollment.token_hash == digest(raw_token)
    )
    enrollment = db.scalar(query)
    if not enrollment:
        fail(400, "invalid_enrollment", "The enrollment token is invalid.")
    attestation = db.get(OfflineIdentityAttestation, enrollment.attestation_id)
    user = db.get(User, enrollment.user_id)
    return _validate_enrollment(enrollment, attestation, user)


def _locked_enrollment(db: Session, raw_token: str):
    token_hash = digest(raw_token)
    initial = db.scalar(
        select(OfflineEnrollment.user_id).where(
            OfflineEnrollment.token_hash == token_hash
        )
    )
    if not initial:
        fail(400, "invalid_enrollment", "The enrollment token is invalid.")
    user = db.scalar(select(User).where(User.id == initial).with_for_update())
    enrollments = list(
        db.scalars(
            select(OfflineEnrollment)
            .where(OfflineEnrollment.user_id == initial)
            .order_by(OfflineEnrollment.id)
            .with_for_update()
        )
    )
    enrollment = next((item for item in enrollments if item.token_hash == token_hash), None)
    if not enrollment:
        fail(400, "invalid_enrollment", "The enrollment token is invalid.")
    attestation = db.scalar(
        select(OfflineIdentityAttestation)
        .where(
            OfflineIdentityAttestation.user_id == initial,
            OfflineIdentityAttestation.id == enrollment.attestation_id,
        )
        .with_for_update()
    )
    return _validate_enrollment(enrollment, attestation, user)


def _rate_limit(request: Request, token: str) -> None:
    token_key = digest(token)
    request.app.state.limiter.check(f"offline-enrollment-host:{request.client.host}", 30, 900)
    request.app.state.limiter.check(f"offline-enrollment-token:{token_key}", 10, 900)


def _require_airgap(request: Request) -> None:
    settings = request.app.state.settings
    if settings.network_mode != "airgap" or settings.mail_enabled:
        fail(404, "not_found", "Offline enrollment is not available.")
    if request.headers.get("origin") != settings.origin:
        fail(403, "invalid_origin", "The request origin is not permitted.")


def _provisioned_context(db: Session, user: User) -> tuple[Workspace | None, list[Project]]:
    organization = db.scalar(
        select(Workspace)
        .where(Workspace.owner_id == user.id, Workspace.kind == "organization")
        .order_by(Workspace.created_at)
        .limit(1)
    )
    projects = (
        list(
            db.scalars(
                select(Project)
                .where(Project.workspace_id == organization.id)
                .order_by(Project.created_at)
            )
        )
        if organization
        else []
    )
    return organization, projects


@router.post("/inspect")
def inspect_enrollment(body: EnrollmentInput, request: Request, db: Session = DB):
    _require_airgap(request)
    _rate_limit(request, body.token)
    enrollment, _attestation, user = _load_enrollment(db, body.token)
    organization, projects = _provisioned_context(db, user)
    return {
        "email": user.email,
        "name": user.name,
        "expires_at": enrollment.expires_at,
        "organization": (
            {"id": organization.id, "name": organization.name} if organization else None
        ),
        "projects": [{"id": project.id, "name": project.name} for project in projects],
        "identity_verification_source": "operator_attested",
    }


@router.post("/complete")
def complete_enrollment(
    body: EnrollmentComplete,
    request: Request,
    response: Response,
    db: Session = DB,
):
    _require_airgap(request)
    _rate_limit(request, body.token)
    enrollment, _attestation, user = _locked_enrollment(db, body.token)
    if not user or not enrollment_pending(db, user.id):
        fail(409, "enrollment_unavailable", "The enrollment token is no longer available.")
    user.password_hash = password_hasher.hash(body.password)
    enrollment.consumed_at = now()
    for other in db.scalars(
        select(OfflineEnrollment).where(
            OfflineEnrollment.user_id == user.id,
            OfflineEnrollment.id != enrollment.id,
            OfflineEnrollment.consumed_at.is_(None),
            OfflineEnrollment.revoked_at.is_(None),
        )
    ):
        other.revoked_at = now()
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    issued_keys = list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.created_by_user_id == user.id, ApiKey.revoked_at.is_(None))
            .with_for_update()
        )
    )
    for key in issued_keys:
        key.revoked_at = now()
    organization, _projects = _provisioned_context(db, user)
    personal = db.scalar(
        select(Workspace)
        .where(Workspace.owner_id == user.id, Workspace.kind == "personal")
        .order_by(Workspace.created_at)
        .limit(1)
    )
    selected = organization or personal
    if not selected:
        fail(409, "enrollment_unavailable", "The provisioned workspace is unavailable.")
    response.delete_cookie("lotuspond_session", path="/")
    db.add(
        AuditEvent(
            workspace_id=selected.id,
            actor_id=user.id,
            action="account.offline_enrolled",
            resource_id=user.id,
            details={
                "attestation_id": enrollment.attestation_id,
                "pre_enrollment_sessions_revoked": True,
                "pre_enrollment_api_keys_revoked": len(issued_keys),
            },
        )
    )
    return {
        "ok": True,
        "workspaceId": selected.id,
        "email_verified": False,
        "identity_verified": True,
        "identity_verification_source": "operator_attested",
    }
