from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Row:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Scoped(Row):
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)


def scoped(*constraints):
    return (UniqueConstraint("workspace_id", "id"), *constraints)


def within(table: str, field: str):
    return ForeignKeyConstraint(["workspace_id", field], [f"{table}.workspace_id", f"{table}.id"])


class WorkflowPackageVersion(Scoped, Base):
    """Validated immutable content; publication grants no execution permission."""

    __tablename__ = "workflow_package_versions"
    project_id: Mapped[str] = mapped_column(String(36))
    package_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(100))
    schema_version: Mapped[str] = mapped_column(String(100))
    validator_revision: Mapped[str] = mapped_column(String(100))
    canonicalization: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    canonical_content: Mapped[bytes] = mapped_column(LargeBinary)
    content_bytes: Mapped[int] = mapped_column(Integer)
    publisher_id: Mapped[str] = mapped_column(String(36))
    publisher_kind: Mapped[str] = mapped_column(String(20))
    __table_args__ = scoped(
        within("projects", "project_id"),
        UniqueConstraint(
            "workspace_id",
            "project_id",
            "package_id",
            "version",
            name="package_version_identity_unique",
        ),
        CheckConstraint("length(sha256) = 64", name="package_digest_length_check"),
        CheckConstraint(
            "content_bytes > 0 AND content_bytes = length(canonical_content)",
            name="package_content_length_check",
        ),
        CheckConstraint(
            "publisher_kind IN ('user','api_key')", name="package_publisher_kind_check"
        ),
        Index("package_project_page_idx", "workspace_id", "project_id", "created_at", "id"),
    )


class PackagePublicationRequest(Scoped, Base):
    __tablename__ = "package_publication_requests"
    request_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    package_version_id: Mapped[str] = mapped_column(String(36))
    __table_args__ = scoped(
        within("workflow_package_versions", "package_version_id"),
        UniqueConstraint("workspace_id", "request_key", name="package_request_key_unique"),
        CheckConstraint(
            "length(request_key) BETWEEN 1 AND 128", name="package_request_key_length_check"
        ),
        CheckConstraint("length(request_hash) = 64", name="package_request_hash_length_check"),
    )


class User(Row, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(Text)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("email = lower(email)"),)


class Workspace(Row, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="personal")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    plan: Mapped[str] = mapped_column(String(20), default="developer")
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    require_mfa: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint("kind IN ('personal','organization')"),
        CheckConstraint("status IN ('active','suspended')"),
        CheckConstraint("monthly_budget_usd IS NULL OR monthly_budget_usd >= 0"),
        CheckConstraint(
            "kind = 'organization' OR require_mfa = false",
            name="workspaces_require_mfa_scope_check",
        ),
        # The subscription ladder is a static product definition; a stored plan can
        # never be a tier the server does not know how to enforce.
        CheckConstraint(
            "plan IN ('developer','team','enterprise')",
            name="workspaces_plan_check",
        ),
    )


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    __table_args__ = (CheckConstraint("role IN ('owner','admin','editor','viewer')"),)


class LoginSession(Row, Base):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mfa_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_factor_id: Mapped[str | None] = mapped_column(String(36))
    mfa_factor_version: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "mfa_factor_id", "mfa_factor_version"],
            ["mfa_factors.user_id", "mfa_factors.id", "mfa_factors.version"],
            name="sessions_mfa_factor_fk",
        ),
        CheckConstraint(
            "(mfa_verified_at IS NULL AND mfa_factor_id IS NULL "
            "AND mfa_factor_version IS NULL) OR "
            "(mfa_verified_at IS NOT NULL AND mfa_factor_id IS NOT NULL "
            "AND mfa_factor_version IS NOT NULL)",
            name="sessions_mfa_binding_check",
        ),
        CheckConstraint(
            "mfa_verified_at IS NULL OR mfa_verified_at <= created_at",
            name="sessions_mfa_verified_time_check",
        ),
        CheckConstraint(
            "mfa_factor_version IS NULL OR mfa_factor_version >= 1",
            name="sessions_mfa_version_check",
        ),
        Index(
            "sessions_mfa_factor_idx", "user_id", "mfa_factor_id", "mfa_factor_version"
        ),
    )


class ApiKey(Scoped, Base):
    __tablename__ = "api_keys"
    name: Mapped[str] = mapped_column(String(160))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list[str]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class IdentityChallenge(Row, Base):
    __tablename__ = "identity_challenges"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("kind IN ('email_verification','password_reset')"),
    )


class MfaFactor(Row, Base):
    __tablename__ = "mfa_factors"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_totp_counter: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        UniqueConstraint("user_id", "id", "version", name="mfa_factors_user_binding_unique"),
        CheckConstraint("length(secret_ciphertext) > 0", name="mfa_factors_secret_check"),
        CheckConstraint("version >= 1", name="mfa_factors_version_check"),
        CheckConstraint(
            "last_totp_counter IS NULL OR last_totp_counter >= 0",
            name="mfa_factors_counter_check",
        ),
    )


class MfaEnrollment(Row, Base):
    __tablename__ = "mfa_enrollments"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    factor_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (
        CheckConstraint("length(secret_ciphertext) > 0", name="mfa_enrollments_secret_check"),
        CheckConstraint("factor_version >= 1", name="mfa_enrollments_version_check"),
        CheckConstraint("expires_at > created_at", name="mfa_enrollments_expiry_check"),
    )


class MfaLoginChallenge(Row, Base):
    __tablename__ = "mfa_login_challenges"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    factor_id: Mapped[str] = mapped_column(String(36))
    factor_version: Mapped[int] = mapped_column(Integer)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "factor_id", "factor_version"],
            ["mfa_factors.user_id", "mfa_factors.id", "mfa_factors.version"],
            name="mfa_login_challenges_factor_fk",
        ),
        CheckConstraint("factor_version >= 1", name="mfa_login_challenges_version_check"),
        CheckConstraint(
            "length(token_hash) = 64", name="mfa_login_challenges_token_hash_check"
        ),
        CheckConstraint("attempts >= 0 AND attempts <= 8", name="mfa_login_challenges_attempts_check"),
        CheckConstraint("expires_at > created_at", name="mfa_login_challenges_expiry_check"),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="mfa_login_challenges_consumed_time_check",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="mfa_login_challenges_revoked_time_check",
        ),
        CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="mfa_login_challenges_state_check",
        ),
        Index(
            "mfa_login_challenges_factor_idx", "user_id", "factor_id", "factor_version"
        ),
    )


class MfaRecoveryCode(Row, Base):
    __tablename__ = "mfa_recovery_codes"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    factor_id: Mapped[str] = mapped_column(String(36))
    factor_version: Mapped[int] = mapped_column(Integer)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "factor_id", "factor_version"],
            ["mfa_factors.user_id", "mfa_factors.id", "mfa_factors.version"],
            name="mfa_recovery_codes_factor_fk",
        ),
        CheckConstraint("factor_version >= 1", name="mfa_recovery_codes_version_check"),
        CheckConstraint("length(code_hash) = 64", name="mfa_recovery_codes_hash_check"),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="mfa_recovery_codes_consumed_time_check",
        ),
        Index("mfa_recovery_codes_factor_idx", "user_id", "factor_id", "factor_version"),
    )


class OfflineIdentityAttestation(Row, Base):
    __tablename__ = "offline_identity_attestations"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(40))
    source_reference: Mapped[str] = mapped_column(String(160))
    operator_reference: Mapped[str] = mapped_column(String(160))
    attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("user_id", "id"),
        CheckConstraint(
            "source IN ('in_person','government_credential','enterprise_record')"
        ),
        CheckConstraint("expires_at IS NULL OR expires_at > attested_at"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= attested_at"),
    )


class OfflineEnrollment(Row, Base):
    __tablename__ = "offline_enrollments"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    attestation_id: Mapped[str] = mapped_column(String(36))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "attestation_id"],
            ["offline_identity_attestations.user_id", "offline_identity_attestations.id"],
        ),
        CheckConstraint("consumed_at IS NULL OR consumed_at >= created_at"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at"),
        CheckConstraint("NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)"),
    )


class Invitation(Scoped, Base):
    __tablename__ = "invitations"
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scoped(CheckConstraint("role IN ('admin','editor','viewer')"))


class MailOutbox(Row, Base):
    __tablename__ = "mail_outbox"
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    identity_challenge_id: Mapped[str | None] = mapped_column(
        ForeignKey("identity_challenges.id"), index=True
    )
    invitation_id: Mapped[str | None] = mapped_column(ForeignKey("invitations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    recipient_hash: Mapped[str] = mapped_column(String(64))
    body_ciphertext: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (
        CheckConstraint(
            "kind IN ('email_verification','password_reset','invitation','security_notice')",
            name="mail_outbox_kind_check",
        ),
        CheckConstraint("status IN ('queued','sending','sent','failed','cancelled')"),
        CheckConstraint("attempts >= 0 AND attempts <= 5"),
        # A security notice carries no link, so it references nothing; every other kind
        # references exactly one expiring record.
        CheckConstraint(
            "(kind = 'security_notice' AND identity_challenge_id IS NULL "
            "AND invitation_id IS NULL) OR "
            "(kind <> 'security_notice' AND ("
            "(identity_challenge_id IS NOT NULL AND invitation_id IS NULL) OR "
            "(identity_challenge_id IS NULL AND invitation_id IS NOT NULL)))",
            name="mail_outbox_reference_check",
        ),
    )


class Project(Scoped, Base):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = scoped()


class Environment(Scoped, Base):
    __tablename__ = "environments"
    project_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20))
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = scoped(
        within("projects", "project_id"),
        CheckConstraint("kind IN ('local','sandbox','production')"),
    )


class Scenario(Scoped, Base):
    __tablename__ = "scenarios"
    project_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    modality: Mapped[str] = mapped_column(String(20), default="text")
    input: Mapped[dict] = mapped_column(JSON)
    expected: Mapped[dict] = mapped_column(JSON)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = scoped(
        within("projects", "project_id"),
        CheckConstraint("modality IN ('text','voice','tool','multimodal')"),
    )


class Runner(Scoped, Base):
    __tablename__ = "runners"
    name: Mapped[str] = mapped_column(String(160))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scoped()


class Run(Scoped, Base):
    __tablename__ = "runs"
    project_id: Mapped[str] = mapped_column(String(36))
    environment_id: Mapped[str | None] = mapped_column(String(36))
    runner_id: Mapped[str | None] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    revision: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    scenario_snapshot: Mapped[list[dict]] = mapped_column(JSON)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scoped(
        within("projects", "project_id"),
        within("environments", "environment_id"),
        within("runners", "runner_id"),
        CheckConstraint("status IN ('queued','running','completed','failed','cancelled')"),
        Index("runs_workspace_status", "workspace_id", "status"),
    )


class RunResult(Scoped, Base):
    __tablename__ = "run_results"
    run_id: Mapped[str] = mapped_column(String(36))
    scenario_id: Mapped[str] = mapped_column(String(36))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    output: Mapped[dict] = mapped_column(JSON)
    latency_ms: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = scoped(
        within("runs", "run_id"),
        within("scenarios", "scenario_id"),
        UniqueConstraint("run_id", "scenario_id"),
        CheckConstraint("latency_ms >= 0 AND cost_usd >= 0"),
    )


class Policy(Scoped, Base):
    __tablename__ = "policies"
    name: Mapped[str] = mapped_column(String(160))
    minimum_pass_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=1)
    maximum_regression: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    maximum_p95_ms: Mapped[int | None] = mapped_column(Integer)
    maximum_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    approval_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    __table_args__ = scoped(
        CheckConstraint("minimum_pass_rate BETWEEN 0 AND 1 AND maximum_regression BETWEEN 0 AND 1")
    )


class Release(Scoped, Base):
    __tablename__ = "releases"
    project_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    revision: Mapped[str] = mapped_column(String(160))
    candidate_run_id: Mapped[str] = mapped_column(String(36))
    baseline_run_id: Mapped[str | None] = mapped_column(String(36))
    policy_id: Mapped[str] = mapped_column(String(36))
    __table_args__ = scoped(
        within("projects", "project_id"),
        within("runs", "candidate_run_id"),
        within("runs", "baseline_run_id"),
        within("policies", "policy_id"),
    )


class Approval(Scoped, Base):
    __tablename__ = "approvals"
    release_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scoped(
        within("releases", "release_id"),
        CheckConstraint("decided_by IS NULL OR decided_by <> requested_by"),
        CheckConstraint(
            "(status='pending' AND decided_by IS NULL AND decided_at IS NULL) OR (status IN ('approved','rejected') AND decided_by IS NOT NULL AND decided_at IS NOT NULL)"
        ),
    )


class ReleaseDecision(Scoped, Base):
    __tablename__ = "release_decisions"
    release_id: Mapped[str] = mapped_column(String(36))
    verdict: Mapped[str] = mapped_column(String(30))
    reasons: Mapped[list[str]] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON)
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = scoped(
        within("releases", "release_id"),
        CheckConstraint("verdict IN ('pass','block','approval_required')"),
    )


class Trace(Scoped, Base):
    __tablename__ = "traces"
    project_id: Mapped[str | None] = mapped_column(String(36))
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    cost_measurement: Mapped[str] = mapped_column(String(20))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = scoped(
        within("projects", "project_id"),
        UniqueConstraint("workspace_id", "external_id"),
        CheckConstraint("status IN ('ok','error') AND latency_ms >= 0 AND cost_usd >= 0"),
        CheckConstraint(
            "(cost_measurement='reported' AND cost_usd IS NOT NULL) OR "
            "(cost_measurement='unavailable' AND cost_usd IS NULL) OR "
            "cost_measurement='unknown'",
            name="traces_cost_measurement_check",
        ),
    )


class GatewayUsage(Scoped, Base):
    """Authoritative gateway spend ledger, separate from client-submitted trace costs."""

    __tablename__ = "gateway_usage"
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(30, 6))
    charged_usd: Mapped[Decimal] = mapped_column(Numeric(30, 6))
    actual_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger)
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scoped(
        CheckConstraint("status IN ('reserved','settled','uncertain')"),
        CheckConstraint("reserved_usd >= 0 AND charged_usd >= 0"),
        CheckConstraint("actual_usd IS NULL OR actual_usd >= 0"),
        CheckConstraint("prompt_tokens IS NULL OR prompt_tokens >= 0"),
        CheckConstraint("completion_tokens IS NULL OR completion_tokens >= 0"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0"),
        Index("gateway_usage_workspace_created", "workspace_id", "created_at"),
    )


class Incident(Scoped, Base):
    __tablename__ = "incidents"
    project_id: Mapped[str | None] = mapped_column(String(36))
    trace_id: Mapped[str | None] = mapped_column(String(36))
    voice_session_id: Mapped[str | None] = mapped_column(String(36))
    assessment_id: Mapped[str | None] = mapped_column(String(36))
    monitor_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(160))
    severity: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(40), default="manual")
    source: Mapped[str] = mapped_column(String(40), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="open")
    resolution_reason: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scoped(
        within("projects", "project_id"),
        within("traces", "trace_id"),
        within("voice_sessions", "voice_session_id"),
        within("assessments", "assessment_id"),
        within("monitors", "monitor_id"),
        UniqueConstraint("workspace_id", "assessment_id"),
        CheckConstraint("severity IN ('low','medium','high','critical')"),
        CheckConstraint("category IN ('manual','assessment_failed','assessment_unavailable')"),
        CheckConstraint("source IN ('manual','monitor','trace_assessment','voice_assessment')"),
        CheckConstraint("status IN ('open','resolved')"),
        CheckConstraint(
            "(status='open' AND resolved_at IS NULL) OR "
            "(status='resolved' AND resolved_at IS NOT NULL)"
        ),
    )


class IncidentEvent(Scoped, Base):
    __tablename__ = "incident_events"
    incident_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = scoped(
        within("incidents", "incident_id"),
        CheckConstraint("action IN ('created','resolved','reopened')"),
    )


class AlertDelivery(Scoped, Base):
    __tablename__ = "alert_deliveries"
    incident_id: Mapped[str] = mapped_column(String(36))
    incident_event_id: Mapped[str] = mapped_column(String(36))
    destination: Mapped[str] = mapped_column(String(80))
    event: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = scoped(
        within("incidents", "incident_id"),
        within("incident_events", "incident_event_id"),
        UniqueConstraint("workspace_id", "incident_event_id", "destination"),
        CheckConstraint("event IN ('incident.opened','incident.resolved','incident.reopened')"),
        CheckConstraint("status IN ('queued','sending','delivered','failed','cancelled')"),
        CheckConstraint("attempts >= 0 AND attempts <= 5"),
    )


class Integration(Scoped, Base):
    __tablename__ = "integrations"
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(30))
    endpoint: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="unverified")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = scoped()


class Receipt(Scoped, Base):
    __tablename__ = "receipts"
    release_id: Mapped[str] = mapped_column(String(36))
    external_id: Mapped[str] = mapped_column(String(160))
    outcome: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[dict] = mapped_column(JSON)
    __table_args__ = scoped(
        within("releases", "release_id"),
        UniqueConstraint("workspace_id", "external_id"),
        CheckConstraint("outcome IN ('success','failure','unknown')"),
    )


class AuditEvent(Row, Base):
    __tablename__ = "audit_events"
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(160))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class VoiceSession(Scoped, Base):
    __tablename__ = "voice_sessions"
    project_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="open")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scoped(
        within("projects", "project_id"), CheckConstraint("status IN ('open','closed')")
    )


class VoiceSegment(Scoped, Base):
    __tablename__ = "voice_segments"
    session_id: Mapped[str] = mapped_column(String(36))
    sequence: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    is_final: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = scoped(
        within("voice_sessions", "session_id"),
        UniqueConstraint("session_id", "sequence", "revision"),
        CheckConstraint("sequence >= 0 AND revision >= 0 AND start_ms >= 0 AND end_ms >= start_ms"),
    )


class Assessment(Scoped, Base):
    __tablename__ = "assessments"
    project_id: Mapped[str] = mapped_column(String(36))
    voice_session_id: Mapped[str | None] = mapped_column(String(36))
    trace_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    score: Mapped[float | None] = mapped_column(Numeric(6, 5))
    criteria: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = scoped(
        within("projects", "project_id"),
        within("voice_sessions", "voice_session_id"),
        within("traces", "trace_id"),
        CheckConstraint("status IN ('passed','failed','unknown')"),
    )


class Monitor(Scoped, Base):
    __tablename__ = "monitors"
    project_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    criteria: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sample_basis_points: Mapped[int] = mapped_column(Integer, default=10000)
    __table_args__ = scoped(
        within("projects", "project_id"), CheckConstraint("sample_basis_points BETWEEN 0 AND 10000")
    )


class EvaluationJob(Scoped, Base):
    __tablename__ = "evaluation_jobs"
    project_id: Mapped[str] = mapped_column(String(36))
    monitor_id: Mapped[str | None] = mapped_column(String(36))
    trace_id: Mapped[str | None] = mapped_column(String(36))
    voice_session_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    criteria: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = scoped(
        within("projects", "project_id"),
        within("monitors", "monitor_id"),
        within("traces", "trace_id"),
        within("voice_sessions", "voice_session_id"),
        UniqueConstraint("monitor_id", "trace_id"),
    )


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def serialize(row, *, exclude: set[str] | None = None) -> dict[str, Any]:
    hidden = {
        "password_hash",
        "token_hash",
        "lease_token_hash",
        "body_ciphertext",
        "secret_ciphertext",
        "code_hash",
    } | (exclude or set())
    result = {}
    for column in row.__table__.columns:
        if column.name in hidden:
            continue
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            value = value.replace(tzinfo=value.tzinfo or UTC).isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        result[column.name] = value
    return result
