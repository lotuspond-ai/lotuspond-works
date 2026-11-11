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
    text,
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
    # Retired with the trace-plan ladder and the model gateway (ADR-0012). The columns
    # keep existing values; nothing reads them and no response reports them.
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
        # Kept from the retired subscription ladder so the schema stays unchanged.
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
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="api_keys_execution_scope_unique"),
    )


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


# Offline identity enrollment is retired (ADR-0014). Stored attestations and enrollments
# stay; a stored unfinished enrollment still blocks its account, and an attestation no
# longer verifies an identity.
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
        UniqueConstraint("workspace_id", "project_id", "id", name="environments_fleet_scope_unique"),
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
        UniqueConstraint(
            "workspace_id", "project_id", "environment_id", "id", name="runs_execution_scope_unique"
        ),
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
    # Tool authorization was retired (ADR-0014); both lists stay stored and new policies keep them empty.
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
        UniqueConstraint(
            "workspace_id",
            "project_id",
            "id",
            "candidate_run_id",
            name="releases_execution_candidate_unique",
        ),
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


# Trace ingestion is retired (ADR-0013). Stored traces stay readable and exportable;
# no route writes them any more.
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
    """Spend ledger of the retired model gateway (ADR-0012), kept so its rows survive."""

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


# Incidents, their events and alert deliveries are retired (ADR-0013). Stored rows stay
# readable through the incident routes; nothing opens, changes or delivers them.
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


# Endpoint registration is retired (ADR-0013); stored rows are kept and no route reads them.
class Integration(Scoped, Base):
    __tablename__ = "integrations"
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(30))
    endpoint: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="unverified")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = scoped()


# Receipt submission is retired (ADR-0013). Stored receipts stay in release evidence exports.
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


# Voice ingestion is retired (ADR-0012). These tables keep stored sessions and the
# assessments and incidents that link to them; no route writes them any more.
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


# Monitors, trace assessments and evaluation jobs are retired (ADR-0013). Stored rows stay;
# trace and incident reads still return the assessments linked to them.
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
            # One text per instant: PostgreSQL returns the session TimeZone's offset,
            # and digested evidence must not depend on it. SQLite returns naive UTC.
            value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        result[column.name] = value
    return result


# Fleet records use full scope; workspace-only `within` is deliberately insufficient.
FLEET_SCOPE = ("workspace_id", "project_id", "environment_id")
FLEET_MACHINE = (
    *FLEET_SCOPE,
    "pool_id",
    "installation_id",
    "recovery_epoch",
    "instance_id",
)


class FleetScoped(Scoped):
    project_id: Mapped[str] = mapped_column(String(36))
    environment_id: Mapped[str] = mapped_column(String(36))


def fleet_fk(
    table: str, fields: tuple[str, ...], targets: tuple[str, ...] | None = None
):
    return ForeignKeyConstraint(
        fields, [f"{table}.{field}" for field in (targets or fields)]
    )


def fleet_scoped(*constraints):
    return (
        UniqueConstraint(*FLEET_SCOPE, "id"),
        fleet_fk("projects", ("workspace_id", "project_id"), ("workspace_id", "id")),
        fleet_fk("environments", FLEET_SCOPE, ("workspace_id", "project_id", "id")),
        *constraints,
    )


def fleet_parent(table: str, field: str):
    return fleet_fk(table, (*FLEET_SCOPE, field), (*FLEET_SCOPE, "id"))


def fleet_instance():
    return fleet_fk("runtime_instances", FLEET_MACHINE, (*FLEET_MACHINE[:-1], "id"))


def fleet_status(values: str):
    return CheckConstraint(f"status IN ({values})")


def fleet_positive(field: str):
    return CheckConstraint(f"{field} BETWEEN 1 AND 2147483647")


class Connection(FleetScoped, Base):
    __tablename__ = "connections"
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="active")
    __table_args__ = fleet_scoped(fleet_status("'active','disabled','revoked'"))


class ConnectionVersion(FleetScoped, Base):
    __tablename__ = "connection_versions"
    connection_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    canonical_config: Mapped[bytes] = mapped_column(LargeBinary)
    config_digest: Mapped[str] = mapped_column(String(64), index=True)
    __table_args__ = fleet_scoped(
        fleet_parent("connections", "connection_id"),
        Index("connection_versions_inventory_idx", *FLEET_SCOPE, "connection_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "connection_id", "version"),
        fleet_positive("version"),
        CheckConstraint("length(canonical_config) BETWEEN 1 AND 32768"),
        CheckConstraint("length(config_digest) = 64"),
    )


class SecretReference(FleetScoped, Base):
    __tablename__ = "secret_references"
    binding_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="active")
    __table_args__ = fleet_scoped(
        UniqueConstraint(*FLEET_SCOPE, "binding_id", "version"),
        UniqueConstraint(*FLEET_SCOPE, "id", "version"),
        fleet_positive("version"),
        fleet_status("'active','revoked'"),
    )


class ConnectionVersionSecret(FleetScoped, Base):
    __tablename__ = "connection_version_secrets"
    connection_id: Mapped[str] = mapped_column(String(36))
    connection_version: Mapped[int] = mapped_column(Integer)
    secret_reference_id: Mapped[str] = mapped_column(String(36))
    secret_version: Mapped[int] = mapped_column(Integer)
    __table_args__ = fleet_scoped(
        fleet_fk(
            "connection_versions",
            (*FLEET_SCOPE, "connection_id", "connection_version"),
            (*FLEET_SCOPE, "connection_id", "version"),
        ),
        fleet_fk(
            "secret_references",
            (*FLEET_SCOPE, "secret_reference_id", "secret_version"),
            (*FLEET_SCOPE, "id", "version"),
        ),
        UniqueConstraint(
            *FLEET_SCOPE,
            "connection_id",
            "connection_version",
            "secret_reference_id",
            "secret_version",
        ),
        fleet_positive("connection_version"),
        fleet_positive("secret_version"),
    )


class RuntimePool(FleetScoped, Base):
    __tablename__ = "runtime_pools"
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="active")
    current_policy_version: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = fleet_scoped(
        ForeignKeyConstraint(
            (*FLEET_SCOPE, "id", "current_policy_version"),
            [
                f"runtime_pool_policies.{field}"
                for field in (*FLEET_SCOPE, "pool_id", "version")
            ],
            name="runtime_pools_current_policy_fk",
            use_alter=True,
        ),
        fleet_status("'active','disabled','revoked'"),
        CheckConstraint(
            "current_policy_version IS NULL OR current_policy_version BETWEEN 1 AND 2147483647"
        ),
    )


class RuntimePoolPolicy(FleetScoped, Base):
    __tablename__ = "runtime_pool_policies"
    pool_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    canonical_policy: Mapped[bytes] = mapped_column(LargeBinary)
    policy_digest: Mapped[str] = mapped_column(String(64), index=True)
    __table_args__ = fleet_scoped(
        fleet_parent("runtime_pools", "pool_id"),
        Index("runtime_pool_policies_inventory_idx", *FLEET_SCOPE, "pool_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "pool_id", "version"),
        fleet_positive("version"),
        CheckConstraint("length(canonical_policy) BETWEEN 1 AND 32768"),
        CheckConstraint("length(policy_digest) = 64"),
    )


class RuntimePoolPolicyConnection(FleetScoped, Base):
    __tablename__ = "runtime_pool_policy_connections"
    pool_id: Mapped[str] = mapped_column(String(36))
    policy_version: Mapped[int] = mapped_column(Integer)
    connection_id: Mapped[str] = mapped_column(String(36))
    __table_args__ = fleet_scoped(
        fleet_fk(
            "runtime_pool_policies",
            (*FLEET_SCOPE, "pool_id", "policy_version"),
            (*FLEET_SCOPE, "pool_id", "version"),
        ),
        fleet_parent("connections", "connection_id"),
        UniqueConstraint(*FLEET_SCOPE, "pool_id", "policy_version", "connection_id"),
        fleet_positive("policy_version"),
    )


class FleetMachine(FleetScoped):
    pool_id: Mapped[str] = mapped_column(String(36))
    installation_id: Mapped[str] = mapped_column(String(128))
    recovery_epoch: Mapped[int] = mapped_column(BigInteger)


def fleet_machine(*constraints):
    return fleet_scoped(
        fleet_parent("runtime_pools", "pool_id"),
        CheckConstraint("recovery_epoch BETWEEN 0 AND 9007199254740991"),
        CheckConstraint("length(installation_id) BETWEEN 1 AND 128"),
        *constraints,
    )


class RuntimeInstance(FleetMachine, Base):
    __tablename__ = "runtime_instances"
    status: Mapped[str] = mapped_column(String(20), default="active")
    generation: Mapped[int] = mapped_column(BigInteger, default=0)
    observation_ceiling: Mapped[bytes] = mapped_column(LargeBinary)
    last_seen_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = fleet_machine(
        UniqueConstraint(*FLEET_MACHINE[:-1], "id"),
        Index(
            "runtime_instances_active_idx",
            *FLEET_SCOPE,
            "pool_id",
            "id",
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        fleet_status("'active','revoked'"),
        CheckConstraint("generation BETWEEN 0 AND 9007199254740991"),
        CheckConstraint("length(observation_ceiling) BETWEEN 1 AND 32768"),
        CheckConstraint(
            "last_seen_at_ms IS NULL OR last_seen_at_ms BETWEEN 0 AND 9007199254740991"
        ),
    )


class RuntimeEnrollment(FleetMachine, Base):
    __tablename__ = "runtime_enrollments"
    policy_version: Mapped[int] = mapped_column(Integer)
    observation_ceiling: Mapped[bytes] = mapped_column(LargeBinary)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="issued")
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    consumed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = fleet_machine(
        Index("runtime_enrollments_inventory_idx", *FLEET_SCOPE, "pool_id", "id"),
        Index(
            "runtime_enrollments_live_idx",
            *FLEET_SCOPE,
            "pool_id",
            "expires_at_ms",
            postgresql_where=text("status = 'issued'"),
            sqlite_where=text("status = 'issued'"),
        ),
        fleet_fk(
            "runtime_pool_policies",
            (*FLEET_SCOPE, "pool_id", "policy_version"),
            (*FLEET_SCOPE, "pool_id", "version"),
        ),
        fleet_positive("policy_version"),
        fleet_status("'issued','consumed','revoked'"),
        CheckConstraint("length(token_digest) = 64"),
        CheckConstraint("length(observation_ceiling) BETWEEN 1 AND 32768"),
        CheckConstraint("expires_at_ms BETWEEN 0 AND 9007199254740991"),
        CheckConstraint(
            "consumed_at_ms IS NULL OR consumed_at_ms BETWEEN 0 AND 9007199254740991"
        ),
    )


class RuntimeSession(FleetMachine, Base):
    __tablename__ = "runtime_sessions"
    instance_id: Mapped[str] = mapped_column(String(36))
    generation: Mapped[int] = mapped_column(BigInteger)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = fleet_machine(
        fleet_instance(),
        UniqueConstraint(*FLEET_MACHINE, "id", "generation"),
        UniqueConstraint(*FLEET_MACHINE, "generation"),
        CheckConstraint("generation BETWEEN 1 AND 9007199254740991"),
        CheckConstraint("expires_at_ms BETWEEN 0 AND 9007199254740991"),
        CheckConstraint("length(token_digest) = 64"),
        fleet_status("'active','revoked'"),
        Index(
            "runtime_one_active_session",
            *FLEET_MACHINE,
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )


class RuntimeCredential(FleetMachine, Base):
    __tablename__ = "runtime_credentials"
    instance_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20))
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    predecessor_id: Mapped[str | None] = mapped_column(String(36))
    successor_id: Mapped[str | None] = mapped_column(String(36))
    session_id: Mapped[str | None] = mapped_column(String(36))
    generation: Mapped[int | None] = mapped_column(BigInteger)
    prepared_policy_version: Mapped[int | None] = mapped_column(Integer)
    activation_deadline_ms: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = fleet_machine(
        fleet_instance(),
        UniqueConstraint(*FLEET_MACHINE, "id"),
        UniqueConstraint(*FLEET_MACHINE, "version"),
        fleet_fk(
            "runtime_credentials",
            (*FLEET_MACHINE, "predecessor_id"),
            (*FLEET_MACHINE, "id"),
        ),
        fleet_fk(
            "runtime_credentials",
            (*FLEET_MACHINE, "successor_id"),
            (*FLEET_MACHINE, "id"),
        ),
        fleet_fk(
            "runtime_sessions",
            (*FLEET_MACHINE, "session_id", "generation"),
            (*FLEET_MACHINE, "id", "generation"),
        ),
        fleet_fk(
            "runtime_pool_policies",
            (*FLEET_SCOPE, "pool_id", "prepared_policy_version"),
            (*FLEET_SCOPE, "pool_id", "version"),
        ),
        fleet_status("'pending','active','revoked'"),
        fleet_positive("version"),
        CheckConstraint("length(token_digest) = 64"),
        CheckConstraint("expires_at_ms BETWEEN 0 AND 9007199254740991"),
        CheckConstraint(
            "generation IS NULL OR generation BETWEEN 1 AND 9007199254740991"
        ),
        CheckConstraint(
            "activation_deadline_ms IS NULL OR activation_deadline_ms BETWEEN 0 AND 9007199254740991"
        ),
        CheckConstraint(
            "status <> 'pending' OR (predecessor_id IS NOT NULL AND session_id IS NOT NULL AND generation IS NOT NULL AND prepared_policy_version IS NOT NULL AND activation_deadline_ms IS NOT NULL)"
        ),
        CheckConstraint(
            "(session_id IS NULL AND generation IS NULL) OR (session_id IS NOT NULL AND generation IS NOT NULL)"
        ),
        CheckConstraint("predecessor_id IS NULL OR predecessor_id <> id"),
        CheckConstraint("successor_id IS NULL OR successor_id <> id"),
        Index(
            "runtime_one_active_credential",
            *FLEET_MACHINE,
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "runtime_one_pending_credential",
            *FLEET_MACHINE,
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )


class RuntimeCommandAdmission(FleetMachine, Base):
    __tablename__ = "runtime_command_admissions"
    instance_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str] = mapped_column(String(36))
    generation: Mapped[int] = mapped_column(BigInteger)
    command_id: Mapped[str] = mapped_column(String(128))
    replay_id: Mapped[str] = mapped_column(String(128))
    recipient: Mapped[str] = mapped_column(String(128))
    command_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    signature_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    command_digest: Mapped[str] = mapped_column(String(64), index=True)
    trust_revision: Mapped[int] = mapped_column(BigInteger)
    command_expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="admitted")
    admitted_at_ms: Mapped[int] = mapped_column(BigInteger)
    claimed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    terminal_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    retain_until_ms: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = fleet_machine(
        fleet_instance(),
        fleet_fk(
            "runtime_sessions",
            (*FLEET_MACHINE, "session_id", "generation"),
            (*FLEET_MACHINE, "id", "generation"),
        ),
        UniqueConstraint(
            "installation_id", "recovery_epoch", "workspace_id", "replay_id"
        ),
        UniqueConstraint(
            "installation_id", "recovery_epoch", "workspace_id", "command_id"
        ),
        fleet_status("'admitted','claimed','terminal','uncertain'"),
        CheckConstraint("generation BETWEEN 1 AND 9007199254740991"),
        CheckConstraint("trust_revision BETWEEN 1 AND 9007199254740991"),
        CheckConstraint("length(command_digest) = 64"),
        CheckConstraint("length(command_bytes) BETWEEN 1 AND 65536"),
        CheckConstraint("length(signature_bytes) BETWEEN 1 AND 65536"),
        CheckConstraint("command_expires_at_ms BETWEEN 0 AND 9007199254740991"),
        CheckConstraint("admitted_at_ms BETWEEN 0 AND 9007199254740991"),
        CheckConstraint(
            "claimed_at_ms IS NULL OR claimed_at_ms BETWEEN 0 AND 9007199254740991"
        ),
        CheckConstraint(
            "terminal_at_ms IS NULL OR terminal_at_ms BETWEEN 0 AND 9007199254740991"
        ),
        CheckConstraint(
            "retain_until_ms IS NULL OR retain_until_ms BETWEEN 0 AND 9007199254740991"
        ),
        CheckConstraint(
            "status <> 'terminal' OR (terminal_at_ms IS NOT NULL AND retain_until_ms IS NOT NULL AND retain_until_ms >= terminal_at_ms + 86400000 AND retain_until_ms >= command_expires_at_ms + 30000)"
        ),
    )


class FleetMutationRequest(FleetScoped, Base):
    __tablename__ = "fleet_mutation_requests"
    operation: Mapped[str] = mapped_column(String(128))
    request_key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    __table_args__ = fleet_scoped(
        UniqueConstraint(*FLEET_SCOPE, "operation", "request_key"),
        CheckConstraint("length(request_digest) = 64"),
        CheckConstraint("length(request_key) BETWEEN 1 AND 128"),
    )


# Durable execution records. Stored pins are evidence, never current authority.
# Reuse the fleet's full scope and its normalized connection-version/secret links.


def execution_safe(*fields):
    return tuple(CheckConstraint(f"{field} BETWEEN 0 AND 9007199254740991") for field in fields)


def execution_hashes(*fields):
    return tuple(CheckConstraint(f"length({field}) = 64") for field in fields)


def execution_pair(left, right):
    return CheckConstraint(f"({left} IS NULL) = ({right} IS NULL)")


def execution_parent(table, field, *extra):
    """Bind a child to the same execution, in addition to the complete tenant scope."""
    return fleet_fk(
        table,
        (*FLEET_SCOPE, "execution_id", field, *extra),
        (*FLEET_SCOPE, "execution_id", "id", *extra),
    )


def execution_artifact(prefix):
    return fleet_fk(
        "execution_artifacts",
        (*FLEET_SCOPE, f"{prefix}_artifact_id", f"{prefix}_artifact_version", f"{prefix}_sha256"),
        (*FLEET_SCOPE, "id", "version", "sha256"),
    )


# Closed execution reasons and control event codes. The v15 lists are frozen; v16
# (E05) adds supersets, which `lotuspond.execution.action_contracts` also defines.
EXECUTION_REASONS_V15 = (
    "capacity",
    "budget",
    "entitlement_unavailable",
    "authority_revoked",
    "deadline_exceeded",
    "signal",
    "control",
    "provider_unavailable",
    "uncertain",
    "completed",
    "invalid_result",
)
EXECUTION_REASONS = (
    *EXECUTION_REASONS_V15,
    "review",
    "action_uncertain",
    "containment",
    "approval_denied",
    "review_expired",
    "approval_expired",
    "approval_revoked",
    "binding_refused",
    "not_applied",
    "outcome_not_verified",
    "outcome_unknown",
    "recovery_epoch_changed",
)
EXECUTION_CONTROL_CODES_V15 = (
    "admitted",
    "started",
    "attempt_reserved",
    "attempt_dispatched",
    "attempt_claimed",
    "attempt_succeeded",
    "attempt_failed",
    "attempt_uncertain",
    "waiting",
    "signal_received",
    "signal_conflict",
    "paused",
    "resumed",
    "cancelled",
    "succeeded",
    "failed",
    "reconciliation_required",
    "capacity_refused",
    "budget_refused",
    "authority_revoked",
    "deadline_exceeded",
    "reconciled",
)
EXECUTION_CONTROL_CODES = (
    *EXECUTION_CONTROL_CODES_V15,
    "task_opened",
    "task_claimed",
    "task_released",
    "task_escalated",
    "task_expired",
    "task_decided",
    "task_submitted",
    "intent_held",
    "intent_superseded",
    "action_resolved",
)


def sql_values(values) -> str:
    return ",".join(f"'{value}'" for value in values)


EXECUTION_REASONS_SQL = sql_values(EXECUTION_REASONS)
EXECUTION_CONTROL_CODES_SQL = sql_values(EXECUTION_CONTROL_CODES)


class ExecutablePublication(FleetScoped, Base):
    """Admitted v2 package or immutable admitted catalog record; no v1 conversion."""

    __tablename__ = "executable_publications"
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(100))
    canonical_package: Mapped[bytes] = mapped_column(LargeBinary)
    canonical_plan: Mapped[bytes | None] = mapped_column(LargeBinary)
    canonical_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    package_sha256: Mapped[str] = mapped_column(String(64))
    plan_sha256: Mapped[str | None] = mapped_column(String(64))
    validator_id: Mapped[str] = mapped_column(String(128))
    compiler_id: Mapped[str | None] = mapped_column(String(128))
    publisher_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    publisher_api_key_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="active")
    __table_args__ = fleet_scoped(
        within("api_keys", "publisher_api_key_id"),
        UniqueConstraint(*FLEET_SCOPE, "kind", "name", "version"),
        UniqueConstraint(*FLEET_SCOPE, "id", "package_sha256"),
        fleet_status("'active','revoked'"),
        CheckConstraint(
            "kind IN ('workflow','connector','model','agent','evaluation','policy','knowledge','artifact')"
        ),
        CheckConstraint("length(canonical_package) BETWEEN 1 AND 1048576"),
        CheckConstraint("length(canonical_plan) BETWEEN 1 AND 1048576"),
        CheckConstraint("length(canonical_signature) BETWEEN 1 AND 65536"),
        CheckConstraint("(kind = 'workflow') = (canonical_signature IS NOT NULL)"),
        execution_pair("canonical_plan", "plan_sha256"),
        execution_pair("canonical_plan", "compiler_id"),
        CheckConstraint("(kind = 'workflow') = (canonical_plan IS NOT NULL)"),
        *execution_hashes("package_sha256", "plan_sha256"),
    )


class ExecutableDependency(FleetScoped, Base):
    __tablename__ = "executable_dependencies"
    publication_id: Mapped[str] = mapped_column(String(36))
    dependency_publication_id: Mapped[str] = mapped_column(String(36))
    dependency_sha256: Mapped[str] = mapped_column(String(64))
    canonical_dependency: Mapped[bytes] = mapped_column(LargeBinary)
    __table_args__ = fleet_scoped(
        fleet_parent("executable_publications", "publication_id"),
        fleet_fk(
            "executable_publications",
            (*FLEET_SCOPE, "dependency_publication_id", "dependency_sha256"),
            (*FLEET_SCOPE, "id", "package_sha256"),
        ),
        UniqueConstraint(*FLEET_SCOPE, "publication_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "publication_id", "dependency_publication_id"),
        CheckConstraint("length(canonical_dependency) BETWEEN 1 AND 65536"),
        *execution_hashes("dependency_sha256"),
    )


class ExecutionDeployment(FleetScoped, Base):
    __tablename__ = "execution_deployments"
    status: Mapped[str] = mapped_column(String(20), default="disabled")
    active_revision_id: Mapped[str | None] = mapped_column(String(36))
    __table_args__ = fleet_scoped(
        ForeignKeyConstraint(
            (*FLEET_SCOPE, "id", "active_revision_id"),
            [
                f"execution_deployment_revisions.{field}"
                for field in (*FLEET_SCOPE, "deployment_id", "id")
            ],
            name="execution_deployment_active_revision_fk",
            use_alter=True,
        ),
        fleet_status("'active','disabled','revoked'"),
    )


class ExecutionDeploymentRevision(FleetScoped, Base):
    __tablename__ = "execution_deployment_revisions"
    deployment_id: Mapped[str] = mapped_column(String(36))
    publication_id: Mapped[str] = mapped_column(String(36))
    pool_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    canonical_configuration: Mapped[bytes] = mapped_column(LargeBinary)
    configuration_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="disabled")
    __table_args__ = fleet_scoped(
        fleet_parent("execution_deployments", "deployment_id"),
        fleet_parent("executable_publications", "publication_id"),
        fleet_parent("runtime_pools", "pool_id"),
        UniqueConstraint(*FLEET_SCOPE, "deployment_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "id", "publication_id"),
        UniqueConstraint(*FLEET_SCOPE, "deployment_id", "revision"),
        fleet_positive("revision"),
        fleet_status("'active','disabled','revoked'"),
        CheckConstraint("length(canonical_configuration) BETWEEN 1 AND 1048576"),
        *execution_hashes("configuration_sha256"),
    )


class ExecutionContextSource(FleetScoped, Base):
    __tablename__ = "execution_context_sources"
    status: Mapped[str] = mapped_column(String(20), default="active")
    access_policy_id: Mapped[str] = mapped_column(String(128))
    __table_args__ = fleet_scoped(fleet_status("'active','revoked','deleted'"))


class ExecutionContextVersion(FleetScoped, Base):
    __tablename__ = "execution_context_versions"
    source_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(String(64))
    observed_at_ms: Mapped[int] = mapped_column(BigInteger)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    access_policy_id: Mapped[str] = mapped_column(String(128))
    content_artifact_id: Mapped[str | None] = mapped_column(String(36))
    content_artifact_version: Mapped[int | None] = mapped_column(Integer)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    citation_artifact_id: Mapped[str | None] = mapped_column(String(36))
    citation_artifact_version: Mapped[int | None] = mapped_column(Integer)
    citation_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = fleet_scoped(
        fleet_parent("execution_context_sources", "source_id"),
        UniqueConstraint(*FLEET_SCOPE, "source_id", "version"),
        fleet_positive("version"),
        *execution_safe("observed_at_ms", "expires_at_ms"),
        CheckConstraint("expires_at_ms >= observed_at_ms"),
        CheckConstraint("length(classification) BETWEEN 1 AND 64"),
        # Deferred solely to break source/artifact DDL cycles; writes are immediate.
        ForeignKeyConstraint(
            (
                *FLEET_SCOPE,
                "source_id",
                "version",
                "content_artifact_id",
                "content_artifact_version",
                "content_sha256",
            ),
            [
                f"execution_artifacts.{field}"
                for field in (
                    *FLEET_SCOPE,
                    "source_id",
                    "source_version",
                    "id",
                    "version",
                    "sha256",
                )
            ],
            name="execution_context_content_fk",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            (
                *FLEET_SCOPE,
                "source_id",
                "version",
                "citation_artifact_id",
                "citation_artifact_version",
                "citation_sha256",
            ),
            [
                f"execution_artifacts.{field}"
                for field in (
                    *FLEET_SCOPE,
                    "source_id",
                    "source_version",
                    "id",
                    "version",
                    "sha256",
                )
            ],
            name="execution_context_citation_fk",
            use_alter=True,
        ),
        execution_pair("content_artifact_id", "content_artifact_version"),
        execution_pair("content_artifact_id", "content_sha256"),
        execution_pair("citation_artifact_id", "citation_artifact_version"),
        execution_pair("citation_artifact_id", "citation_sha256"),
    )


class DeploymentBinding(FleetScoped, Base):
    """Pins a normalized admitted dependency and exact connection/source revisions.

    ConnectionVersionSecret already normalizes all secret pins for the immutable
    connection revision. Services must compare these links to canonical bytes.
    """

    __tablename__ = "deployment_bindings"
    deployment_revision_id: Mapped[str] = mapped_column(String(36))
    publication_id: Mapped[str] = mapped_column(String(36))
    dependency_id: Mapped[str] = mapped_column(String(36))
    connection_id: Mapped[str | None] = mapped_column(String(36))
    connection_version: Mapped[int | None] = mapped_column(Integer)
    source_id: Mapped[str | None] = mapped_column(String(36))
    source_version: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = fleet_scoped(
        fleet_fk(
            "execution_deployment_revisions",
            (*FLEET_SCOPE, "deployment_revision_id", "publication_id"),
            (*FLEET_SCOPE, "id", "publication_id"),
        ),
        fleet_fk(
            "executable_dependencies",
            (*FLEET_SCOPE, "publication_id", "dependency_id"),
            (*FLEET_SCOPE, "publication_id", "id"),
        ),
        fleet_fk(
            "connection_versions",
            (*FLEET_SCOPE, "connection_id", "connection_version"),
            (*FLEET_SCOPE, "connection_id", "version"),
        ),
        fleet_fk(
            "execution_context_versions",
            (*FLEET_SCOPE, "source_id", "source_version"),
            (*FLEET_SCOPE, "source_id", "version"),
        ),
        execution_pair("connection_id", "connection_version"),
        execution_pair("source_id", "source_version"),
        UniqueConstraint(*FLEET_SCOPE, "deployment_revision_id", "dependency_id"),
    )


class DeploymentAssuranceBinding(FleetScoped, Base):
    """Exact legacy release/candidate link, not package/config assurance by itself."""

    __tablename__ = "deployment_assurance_bindings"
    deployment_revision_id: Mapped[str] = mapped_column(String(36))
    release_id: Mapped[str] = mapped_column(String(36))
    candidate_run_id: Mapped[str] = mapped_column(String(36))
    __table_args__ = fleet_scoped(
        fleet_parent("execution_deployment_revisions", "deployment_revision_id"),
        fleet_fk(
            "releases",
            ("workspace_id", "project_id", "release_id", "candidate_run_id"),
            ("workspace_id", "project_id", "id", "candidate_run_id"),
        ),
        fleet_parent("runs", "candidate_run_id"),
        UniqueConstraint(*FLEET_SCOPE, "deployment_revision_id"),
    )


class ExecutionArtifact(FleetScoped, Base):
    __tablename__ = "execution_artifacts"
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    content_bytes: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(36))
    source_version: Mapped[int | None] = mapped_column(Integer)
    access_policy_id: Mapped[str] = mapped_column(String(128))
    key_id: Mapped[str] = mapped_column(String(128))
    retain_until_ms: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = fleet_scoped(
        UniqueConstraint(*FLEET_SCOPE, "id", "version", "sha256"),
        UniqueConstraint(*FLEET_SCOPE, "source_id", "source_version", "id", "version", "sha256"),
        fleet_fk(
            "execution_context_versions",
            (*FLEET_SCOPE, "source_id", "source_version"),
            (*FLEET_SCOPE, "source_id", "version"),
        ),
        execution_pair("source_id", "source_version"),
        fleet_positive("version"),
        CheckConstraint("content_bytes BETWEEN 1 AND 1048576"),
        CheckConstraint("length(ciphertext) = content_bytes + 16"),
        CheckConstraint("length(nonce) = 12"),
        CheckConstraint("length(classification) BETWEEN 1 AND 64"),
        *execution_hashes("sha256"),
        *execution_safe("retain_until_ms"),
        Index("execution_artifact_retention_idx", *FLEET_SCOPE, "retain_until_ms", "id"),
    )


class Execution(FleetScoped, Base):
    admission_sequence: Mapped[int] = mapped_column(BigInteger)
    __tablename__ = "executions"
    deployment_revision_id: Mapped[str] = mapped_column(String(36))
    interpreter_id: Mapped[str] = mapped_column(String(128))
    engine_identity: Mapped[str] = mapped_column(String(128), unique=True)
    input_artifact_id: Mapped[str] = mapped_column(String(36))
    input_artifact_version: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64))
    result_artifact_id: Mapped[str | None] = mapped_column(String(36))
    result_artifact_version: Mapped[int | None] = mapped_column(Integer)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    initiating_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    initiating_api_key_id: Mapped[str | None] = mapped_column(String(36))
    admitted_at_ms: Mapped[int] = mapped_column(BigInteger)
    deadline_ms: Mapped[int] = mapped_column(BigInteger)
    admission_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="pending")
    requested_state: Mapped[str] = mapped_column(String(20), default="running")
    reason: Mapped[str | None] = mapped_column(String(40))
    next_event_sequence: Mapped[int] = mapped_column(BigInteger, default=1)
    # Schema v15: orchestrator work due at or after this time; NULL when none or terminal.
    # Sorted last so a fresh schema matches the additive v15 column position.
    runnable_at_ms: Mapped[int | None] = mapped_column(BigInteger, sort_order=1000)
    # Schema v15: the recovery epoch its rows are current under, from admission or an
    # owner's release; NULL while it is held after a restore (owner decision,
    # 2026-10-28). Rows written before v15 read 0, as rows from before any restore.
    recovery_epoch: Mapped[int | None] = mapped_column(
        BigInteger, server_default=text("0"), sort_order=1001
    )
    __table_args__ = fleet_scoped(
        fleet_parent("execution_deployment_revisions", "deployment_revision_id"),
        within("api_keys", "initiating_api_key_id"),
        execution_artifact("input"),
        execution_artifact("result"),
        execution_pair("result_artifact_id", "result_artifact_version"),
        execution_pair("result_artifact_id", "result_sha256"),
        CheckConstraint(
            "state IN ('pending','running','waiting','paused','cancelled','succeeded','failed','reconciliation_required')"
        ),
        CheckConstraint("requested_state IN ('running','paused','cancelled')"),
        # Named as PostgreSQL named it in v15, so v16 can replace it with a superset.
        CheckConstraint(
            "reason IN (" + EXECUTION_REASONS_SQL + ")",
            name="executions_reason_check",
        ),
        CheckConstraint("deadline_ms > admitted_at_ms"),
        CheckConstraint("next_event_sequence BETWEEN 1 AND 9007199254740991"),
        *execution_safe("admitted_at_ms", "deadline_ms"),
        *execution_hashes("admission_digest"),
        CheckConstraint("admission_sequence BETWEEN 1 AND 9007199254740991"),
        UniqueConstraint(*FLEET_SCOPE, "admission_sequence"),
        Index("execution_queue_idx", *FLEET_SCOPE, "state", "admission_sequence", "id"),
        Index("execution_deadline_idx", "state", "deadline_ms", "id"),
        CheckConstraint(
            "runnable_at_ms BETWEEN 0 AND 9007199254740991",
            name="executions_runnable_at_ms_check",
        ),
        CheckConstraint(
            "runnable_at_ms IS NULL OR state NOT IN ('succeeded','failed','cancelled')",
            name="executions_runnable_terminal_check",
        ),
        Index(
            "execution_runnable_idx",
            "interpreter_id",
            "runnable_at_ms",
            "id",
            postgresql_where=text("runnable_at_ms IS NOT NULL"),
        ),
        CheckConstraint(
            "recovery_epoch BETWEEN 0 AND 9007199254740991",
            name="executions_recovery_epoch_check",
        ),
        Index(
            "execution_restored_idx",
            "recovery_epoch",
            "id",
            postgresql_where=text("state NOT IN ('succeeded','failed','cancelled')"),
        ),
    )


class ExecutionStepInstance(FleetScoped, Base):
    __tablename__ = "execution_step_instances"
    execution_id: Mapped[str] = mapped_column(String(36))
    occurrence_path: Mapped[str] = mapped_column(String(1024))
    node_id: Mapped[str] = mapped_column(String(128))
    __table_args__ = fleet_scoped(
        fleet_parent("executions", "execution_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "occurrence_path"),
        CheckConstraint("length(occurrence_path) BETWEEN 1 AND 1024"),
    )


class ExecutionAttempt(FleetScoped, Base):
    __tablename__ = "execution_attempts"
    execution_id: Mapped[str] = mapped_column(String(36))
    step_instance_id: Mapped[str] = mapped_column(String(36))
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_attempt_id: Mapped[str | None] = mapped_column(String(36))
    child_request_id: Mapped[str | None] = mapped_column(String(128))
    child_request_digest: Mapped[str | None] = mapped_column(String(64))
    input_artifact_id: Mapped[str] = mapped_column(String(36))
    input_artifact_version: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64))
    result_artifact_id: Mapped[str | None] = mapped_column(String(36))
    result_artifact_version: Mapped[int | None] = mapped_column(Integer)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    __table_args__ = fleet_scoped(
        execution_parent("execution_step_instances", "step_instance_id"),
        execution_parent("execution_attempts", "parent_attempt_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "step_instance_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "step_instance_id", "ordinal"),
        UniqueConstraint(*FLEET_SCOPE, "parent_attempt_id", "child_request_id"),
        execution_pair("parent_attempt_id", "child_request_id"),
        execution_pair("parent_attempt_id", "child_request_digest"),
        CheckConstraint("parent_attempt_id IS NULL OR parent_attempt_id <> id"),
        execution_artifact("input"),
        execution_artifact("result"),
        execution_pair("result_artifact_id", "result_artifact_version"),
        execution_pair("result_artifact_id", "result_sha256"),
        CheckConstraint(
            "state IN ('reserved','dispatched','claimed','succeeded','failed','uncertain','cancelled')"
        ),
        fleet_positive("ordinal"),
        *execution_hashes("child_request_digest"),
    )


class ExecutionWait(FleetScoped, Base):
    __tablename__ = "execution_waits"
    execution_id: Mapped[str] = mapped_column(String(36))
    step_instance_id: Mapped[str] = mapped_column(String(36))
    wait_key: Mapped[str] = mapped_column(String(128))
    deadline_ms: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20), default="open")
    __table_args__ = fleet_scoped(
        execution_parent("execution_step_instances", "step_instance_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "wait_key"),
        CheckConstraint("state IN ('open','satisfied','expired','cancelled')"),
        *execution_safe("deadline_ms"),
        Index("execution_wait_deadline_idx", *FLEET_SCOPE, "state", "deadline_ms", "id"),
        Index("execution_wait_due_idx", "deadline_ms", "id", postgresql_where=text("state = 'open'")),
    )


class ExecutionRequest(FleetScoped, Base):
    __tablename__ = "execution_requests"
    execution_id: Mapped[str] = mapped_column(String(36))
    request_key: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(64))
    __table_args__ = fleet_scoped(
        fleet_parent("executions", "execution_id"),
        UniqueConstraint(*FLEET_SCOPE, "request_key"),
        CheckConstraint("length(request_key) BETWEEN 1 AND 128"),
        *execution_hashes("digest"),
    )


class ExecutionMessage(FleetScoped):
    execution_id: Mapped[str] = mapped_column(String(36))
    digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    available_at_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_token_digest: Mapped[str | None] = mapped_column(String(64))
    leased_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    lease_expires_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    delivery_count: Mapped[int] = mapped_column(Integer, default=0)


def execution_message(*constraints):
    return fleet_scoped(
        fleet_parent("executions", "execution_id"),
        fleet_status("'pending','leased','delivered','conflict'"),
        *execution_hashes("digest", "lease_token_digest"),
        *execution_safe("available_at_ms", "leased_at_ms", "lease_expires_at_ms"),
        CheckConstraint("delivery_count BETWEEN 0 AND 2147483647"),
        execution_pair("leased_at_ms", "lease_expires_at_ms"),
        execution_pair("leased_at_ms", "lease_token_digest"),
        CheckConstraint("status <> 'leased' OR leased_at_ms IS NOT NULL"),
        CheckConstraint(
            "lease_expires_at_ms > leased_at_ms AND lease_expires_at_ms <= leased_at_ms + 60000"
        ),
        *constraints,
    )


class ExecutionOutbox(ExecutionMessage, Base):
    __tablename__ = "execution_outbox"
    message_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(20))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    __table_args__ = execution_message(
        execution_parent("execution_attempts", "attempt_id"),
        UniqueConstraint(*FLEET_SCOPE, "message_id"),
        CheckConstraint("kind IN ('engine_start','engine_signal','activity','control')"),
        Index("execution_outbox_queue_idx", *FLEET_SCOPE, "status", "available_at_ms", "id"),
    )


class ExecutionInbox(ExecutionMessage, Base):
    __tablename__ = "execution_inbox"
    wait_id: Mapped[str] = mapped_column(String(36))
    signal_id: Mapped[str] = mapped_column(String(128))
    payload_artifact_id: Mapped[str] = mapped_column(String(36))
    payload_artifact_version: Mapped[int] = mapped_column(Integer)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = execution_message(
        execution_parent("execution_waits", "wait_id"),
        execution_artifact("payload"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "signal_id"),
        *execution_safe("expires_at_ms"),
        Index("execution_inbox_queue_idx", *FLEET_SCOPE, "status", "available_at_ms", "id"),
    )


class ExecutionEvent(FleetScoped, Base):
    """Normalized identity and encrypted canonical event pin; storage is not authority."""

    __tablename__ = "execution_events"
    execution_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    sequence: Mapped[int] = mapped_column(BigInteger)
    family: Mapped[str] = mapped_column(String(24))
    format_version: Mapped[int] = mapped_column(Integer)
    installation_id: Mapped[str] = mapped_column(String(128))
    recovery_epoch: Mapped[int] = mapped_column(BigInteger)
    source_id: Mapped[str] = mapped_column(String(128))
    source_generation: Mapped[int] = mapped_column(BigInteger)
    source_sequence: Mapped[int] = mapped_column(BigInteger)
    event_id: Mapped[str] = mapped_column(String(128))
    event_artifact_id: Mapped[str] = mapped_column(String(36))
    event_artifact_version: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(40))
    occurred_at_ms: Mapped[int] = mapped_column(BigInteger)
    application_state: Mapped[str] = mapped_column(String(16))
    gap_slot: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = fleet_scoped(
        fleet_parent("executions", "execution_id"),
        execution_parent("execution_attempts", "attempt_id"),
        fleet_fk(
            "execution_artifacts",
            (*FLEET_SCOPE, "event_artifact_id", "event_artifact_version", "digest"),
            (*FLEET_SCOPE, "id", "version", "sha256"),
        ),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "sequence"),
        UniqueConstraint(
            *FLEET_SCOPE, "execution_id", "source_id", "source_generation", "source_sequence"
        ),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "source_id", "event_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "gap_slot"),
        CheckConstraint("sequence BETWEEN 1 AND 9007199254740991"),
        CheckConstraint("format_version = 1"),
        CheckConstraint("length(installation_id) BETWEEN 1 AND 128"),
        CheckConstraint("length(source_id) BETWEEN 1 AND 128"),
        CheckConstraint("length(event_id) BETWEEN 1 AND 128"),
        CheckConstraint("event_artifact_version > 0"),
        CheckConstraint("application_state IN ('pending','applied','conflict')"),
        CheckConstraint("gap_slot BETWEEN 1 AND 100"),
        CheckConstraint("(application_state = 'pending') = (gap_slot IS NOT NULL)"),
        # Named as PostgreSQL named it in v15, so v16 can replace it with a superset.
        CheckConstraint(
            "(family = 'sdk_runtime_event' AND code IN ('admission','step_started','step_result','step_failed','wait_started','signal_received','timer_elapsed','intent_created','decision_recorded','action_dispatched','action_receipt','dispatch_timeout','reconciliation_observed','outcome_recorded','execution_completed','cancellation_requested','execution_failed')) OR "
            "(family = 'execution_control' AND code IN (" + EXECUTION_CONTROL_CODES_SQL + "))",
            name="execution_events_check1",
        ),
        *execution_safe("recovery_epoch", "source_generation", "source_sequence", "occurred_at_ms"),
        *execution_hashes("digest"),
    )


class ExecutionControlRequest(FleetScoped, Base):
    __tablename__ = "execution_control_requests"
    execution_id: Mapped[str] = mapped_column(String(36))
    request_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(20))
    digest: Mapped[str] = mapped_column(String(64))
    initiating_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    initiating_api_key_id: Mapped[str | None] = mapped_column(String(36))
    __table_args__ = fleet_scoped(
        fleet_parent("executions", "execution_id"),
        within("api_keys", "initiating_api_key_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "request_id"),
        CheckConstraint("action IN ('pause','resume','cancel')"),
        *execution_hashes("digest"),
    )


class ExecutionUsageReservation(FleetScoped, Base):
    __tablename__ = "execution_usage_reservations"
    execution_id: Mapped[str] = mapped_column(String(36))
    step_instance_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cost_microusd: Mapped[int] = mapped_column(BigInteger)
    active_ms: Mapped[int] = mapped_column(BigInteger)
    observed_input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    observed_output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    observed_cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    completed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    compute_stopped_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    settlement_digest: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = fleet_scoped(
        fleet_fk(
            "execution_attempts",
            (*FLEET_SCOPE, "execution_id", "step_instance_id", "attempt_id"),
            (*FLEET_SCOPE, "execution_id", "step_instance_id", "id"),
        ),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "attempt_id"),
        CheckConstraint("state IN ('reserved','settled','uncertain','released')"),
        *execution_safe(
            "input_tokens",
            "output_tokens",
            "cost_microusd",
            "active_ms",
            "observed_input_tokens",
            "observed_output_tokens",
            "observed_cost_microusd",
            "completed_at_ms",
            "compute_stopped_at_ms",
        ),
        *execution_hashes("settlement_digest"),
        CheckConstraint(
            "state <> 'settled' OR (observed_input_tokens IS NOT NULL AND observed_output_tokens IS NOT NULL AND observed_cost_microusd IS NOT NULL AND completed_at_ms IS NOT NULL AND settlement_digest IS NOT NULL)"
        ),
        CheckConstraint("state <> 'released' OR compute_stopped_at_ms IS NOT NULL"),
    )


class ExecutionCapacity(FleetScoped, Base):
    __tablename__ = "execution_capacities"
    active: Mapped[int] = mapped_column(Integer, default=0)
    pending: Mapped[int] = mapped_column(Integer, default=0)
    retained_waits: Mapped[int] = mapped_column(Integer, default=0)
    next_admission_sequence: Mapped[int] = mapped_column(BigInteger, default=1)
    active_limit: Mapped[int] = mapped_column(Integer, default=5)
    pending_limit: Mapped[int] = mapped_column(Integer, default=1000)
    wait_limit: Mapped[int] = mapped_column(Integer, default=10000)
    __table_args__ = fleet_scoped(
        UniqueConstraint(*FLEET_SCOPE),
        CheckConstraint("next_admission_sequence BETWEEN 1 AND 9007199254740991"),
        CheckConstraint("active_limit BETWEEN 0 AND 5 AND active BETWEEN 0 AND active_limit"),
        CheckConstraint("pending_limit BETWEEN 0 AND 1000 AND pending BETWEEN 0 AND pending_limit"),
        CheckConstraint(
            "wait_limit BETWEEN 0 AND 10000 AND retained_waits BETWEEN 0 AND wait_limit"
        ),
    )


class ExecutionSchedulerCursor(Row, Base):
    """Installation-wide capacity and opaque round-robin cursor; no customer payload."""

    __tablename__ = "execution_scheduler_cursors"
    installation_id: Mapped[str] = mapped_column(String(128), unique=True)
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[int] = mapped_column(Integer, default=0)
    active_limit: Mapped[int] = mapped_column(Integer, default=25)
    __table_args__ = (
        *execution_safe("cursor"),
        CheckConstraint("active_limit BETWEEN 0 AND 25 AND active BETWEEN 0 AND active_limit"),
    )


class ExecutionActivityDelivery(FleetMachine, Base):
    __tablename__ = "execution_activity_deliveries"
    execution_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str] = mapped_column(String(36))
    instance_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str] = mapped_column(String(36))
    generation: Mapped[int] = mapped_column(BigInteger)
    replay_id: Mapped[str] = mapped_column(String(36))
    canonical_envelope: Mapped[bytes] = mapped_column(LargeBinary)
    canonical_signature: Mapped[bytes] = mapped_column(LargeBinary)
    digest: Mapped[str] = mapped_column(String(64))
    issued_at_ms: Mapped[int] = mapped_column(BigInteger)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    retain_until_ms: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    __table_args__ = fleet_machine(
        execution_parent("execution_attempts", "attempt_id"),
        fleet_fk(
            "runtime_sessions",
            (*FLEET_MACHINE, "session_id", "generation"),
            (*FLEET_MACHINE, "id", "generation"),
        ),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "attempt_id", "id"),
        UniqueConstraint("installation_id", "recovery_epoch", "workspace_id", "replay_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "attempt_id"),
        fleet_status("'pending','leased','delivered','conflict'"),
        CheckConstraint(
            "length(canonical_envelope) > 0 AND length(canonical_signature) > 0 AND length(canonical_envelope) + length(canonical_signature) <= 65536"
        ),
        CheckConstraint("generation BETWEEN 1 AND 9007199254740991"),
        *execution_hashes("digest"),
        *execution_safe("recovery_epoch", "issued_at_ms", "expires_at_ms", "retain_until_ms"),
        CheckConstraint("expires_at_ms > issued_at_ms AND expires_at_ms <= issued_at_ms + 300000"),
        CheckConstraint("retain_until_ms >= expires_at_ms + 30000"),
        Index("execution_delivery_retention_idx", *FLEET_SCOPE, "retain_until_ms", "id"),
        Index(
            "execution_delivery_due_idx",
            "expires_at_ms",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
    )


class ExecutionLease(FleetScoped, Base):
    """Lease expiration records lost authority; it never makes an attempt reclaimable."""

    __tablename__ = "execution_leases"
    execution_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str] = mapped_column(String(36))
    delivery_id: Mapped[str] = mapped_column(String(36))
    token_digest: Mapped[str] = mapped_column(String(64))
    claimed_at_ms: Mapped[int] = mapped_column(BigInteger)
    renewed_at_ms: Mapped[int] = mapped_column(BigInteger)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="active")
    __table_args__ = fleet_scoped(
        fleet_fk(
            "execution_activity_deliveries",
            (*FLEET_SCOPE, "execution_id", "attempt_id", "delivery_id"),
            (*FLEET_SCOPE, "execution_id", "attempt_id", "id"),
        ),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "attempt_id"),
        fleet_status("'active','stopped','uncertain'"),
        *execution_hashes("token_digest"),
        *execution_safe("claimed_at_ms", "renewed_at_ms", "expires_at_ms"),
        CheckConstraint(
            "renewed_at_ms >= claimed_at_ms AND expires_at_ms > renewed_at_ms AND expires_at_ms <= renewed_at_ms + 60000"
        ),
        Index("execution_lease_expiry_idx", *FLEET_SCOPE, "status", "expires_at_ms", "id"),
        Index(
            "execution_lease_due_idx", "expires_at_ms", "id", postgresql_where=text("status = 'active'")
        ),
    )


class ExecutionReconciliationObservation(FleetScoped, Base):
    __tablename__ = "execution_reconciliation_observations"
    execution_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str] = mapped_column(String(36))
    source_id: Mapped[str] = mapped_column(String(128))
    observation_id: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(30))
    observed_at_ms: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = fleet_scoped(
        execution_parent("execution_attempts", "attempt_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "attempt_id", "source_id", "observation_id"),
        CheckConstraint(
            "code IN ('compute_stopped','compute_running','outcome_unknown','usage_known','authority_revoked','engine_missing','identity_conflict')"
        ),
        *execution_hashes("digest"),
        *execution_safe("observed_at_ms"),
    )


# Schema v16 (E05): actions, human tasks and outcomes. Rows hold digests, IDs and
# canonical SDK records; proposal values live only in encrypted execution artifacts.
SAFE = "BETWEEN 0 AND 9007199254740991"


def execution_optional_artifact(prefix):
    return (
        execution_artifact(prefix),
        execution_pair(f"{prefix}_artifact_id", f"{prefix}_artifact_version"),
        execution_pair(f"{prefix}_artifact_id", f"{prefix}_sha256"),
    )


class ExecutionActionIntent(FleetScoped, Base):
    __tablename__ = "execution_action_intents"
    execution_id: Mapped[str] = mapped_column(String(36))
    step_instance_id: Mapped[str] = mapped_column(String(36))
    action_id: Mapped[str] = mapped_column(String(36))
    binding_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    intent_sha256: Mapped[str] = mapped_column(String(64))
    recovery_epoch: Mapped[int] = mapped_column(BigInteger)
    connection_id: Mapped[str] = mapped_column(String(36))
    connection_version: Mapped[int] = mapped_column(Integer)
    target_artifact_id: Mapped[str] = mapped_column(String(36))
    target_artifact_version: Mapped[int] = mapped_column(Integer)
    target_sha256: Mapped[str] = mapped_column(String(64))
    input_artifact_id: Mapped[str] = mapped_column(String(36))
    input_artifact_version: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64))
    approval_mode: Mapped[str] = mapped_column(String(8))
    independent_approval: Mapped[bool] = mapped_column(Boolean)
    requester_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    issued_at_ms: Mapped[int] = mapped_column(BigInteger)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    authorization_state: Mapped[str] = mapped_column(String(20), default="proposed")
    effect_state: Mapped[str] = mapped_column(String(20), default="not_dispatched")
    outcome_state: Mapped[str] = mapped_column(String(20), default="unknown")
    __table_args__ = fleet_scoped(
        execution_parent("execution_step_instances", "step_instance_id"),
        fleet_fk(
            "connection_versions",
            (*FLEET_SCOPE, "connection_id", "connection_version"),
            (*FLEET_SCOPE, "connection_id", "version"),
        ),
        execution_artifact("target"),
        execution_artifact("input"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "id", "expires_at_ms"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "step_instance_id"),
        UniqueConstraint(*FLEET_SCOPE, "action_id"),
        UniqueConstraint(*FLEET_SCOPE, "intent_sha256"),
        CheckConstraint("length(binding_bytes) BETWEEN 1 AND 65536"),
        CheckConstraint("approval_mode IN ('none','human')"),
        CheckConstraint(
            "authorization_state IN ('proposed','awaiting_review','approved','denied','review_expired','expired','withdrawn','superseded')"
        ),
        CheckConstraint(
            "effect_state IN ('not_dispatched','dispatching','accepted','observed','unknown','absent')"
        ),
        CheckConstraint("outcome_state IN ('unknown','verified','not_verified')"),
        CheckConstraint("expires_at_ms > issued_at_ms"),
        fleet_positive("connection_version"),
        *execution_hashes("intent_sha256", "target_sha256", "input_sha256"),
        *execution_safe("recovery_epoch", "issued_at_ms", "expires_at_ms"),
        Index(
            "execution_action_hold_idx",
            "recovery_epoch",
            "id",
            postgresql_where=text(
                "effect_state IN ('not_dispatched','dispatching','accepted','unknown') AND authorization_state NOT IN ('denied','review_expired','expired','withdrawn','superseded')"
            ),
        ),
    )


class ExecutionHumanTask(FleetScoped, Base):
    __tablename__ = "execution_human_tasks"
    execution_id: Mapped[str] = mapped_column(String(36))
    step_instance_id: Mapped[str] = mapped_column(String(36))
    wait_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(10))
    intent_id: Mapped[str | None] = mapped_column(String(36))
    assignment: Mapped[str] = mapped_column(String(32))
    escalation_assignment: Mapped[str | None] = mapped_column(String(32))
    escalate_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    escalated_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    deadline_ms: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20), default="open")
    claim_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    claim_expires_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    claim_revision: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = fleet_scoped(
        execution_parent("execution_step_instances", "step_instance_id"),
        execution_parent("execution_waits", "wait_id"),
        execution_parent("execution_action_intents", "intent_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "wait_id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "step_instance_id"),
        CheckConstraint("kind IN ('review','form')"),
        CheckConstraint("(kind = 'review') = (intent_id IS NOT NULL)"),
        CheckConstraint("state IN ('open','decided','submitted','expired','cancelled')"),
        CheckConstraint(
            "assignment IN ('workspace_owners','workspace_admins','workspace_editors')"
        ),
        CheckConstraint(
            "escalation_assignment IS NULL OR escalation_assignment IN ('workspace_owners','workspace_admins','workspace_editors')"
        ),
        CheckConstraint("(escalation_assignment IS NULL) = (escalate_at_ms IS NULL)"),
        CheckConstraint("escalated_at_ms IS NULL OR escalate_at_ms IS NOT NULL"),
        CheckConstraint(
            "(claim_user_id IS NULL) = (claim_expires_at_ms IS NULL) AND (claim_user_id IS NULL) = (claim_revision IS NULL)"
        ),
        CheckConstraint("claim_expires_at_ms IS NULL OR claim_expires_at_ms <= deadline_ms"),
        CheckConstraint("escalate_at_ms IS NULL OR escalate_at_ms < deadline_ms"),
        CheckConstraint(f"escalate_at_ms IS NULL OR escalate_at_ms {SAFE}"),
        CheckConstraint(f"escalated_at_ms IS NULL OR escalated_at_ms {SAFE}"),
        CheckConstraint(f"claim_expires_at_ms IS NULL OR claim_expires_at_ms {SAFE}"),
        CheckConstraint("claim_revision IS NULL OR claim_revision BETWEEN 1 AND 9007199254740991"),
        *execution_safe("deadline_ms"),
        Index("execution_task_due_idx", "deadline_ms", "id", postgresql_where=text("state = 'open'")),
        Index(
            "execution_task_escalation_idx",
            "escalate_at_ms",
            "id",
            postgresql_where=text(
                "state = 'open' AND escalated_at_ms IS NULL AND escalate_at_ms IS NOT NULL"
            ),
        ),
        Index(
            "execution_task_claim_idx",
            "claim_expires_at_ms",
            "id",
            postgresql_where=text("claim_user_id IS NOT NULL"),
        ),
    )


class ExecutionActionDecision(FleetScoped, Base):
    __tablename__ = "execution_action_decisions"
    execution_id: Mapped[str] = mapped_column(String(36))
    task_id: Mapped[str] = mapped_column(String(36))
    intent_id: Mapped[str] = mapped_column(String(36))
    intent_expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    decision_id: Mapped[str] = mapped_column(String(128))
    decision_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    decision_sha256: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(5))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    issued_at_ms: Mapped[int] = mapped_column(BigInteger)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = fleet_scoped(
        execution_parent("execution_human_tasks", "task_id"),
        # The decision's expiry is bounded by its intent's, enforced against the intent row.
        fleet_fk(
            "execution_action_intents",
            (*FLEET_SCOPE, "execution_id", "intent_id", "intent_expires_at_ms"),
            (*FLEET_SCOPE, "execution_id", "id", "expires_at_ms"),
        ),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "task_id"),
        UniqueConstraint(*FLEET_SCOPE, "task_id", "decision_id"),
        CheckConstraint("length(decision_id) BETWEEN 1 AND 128"),
        CheckConstraint("length(decision_bytes) BETWEEN 1 AND 65536"),
        CheckConstraint("decision IN ('allow','deny')"),
        CheckConstraint("expires_at_ms > issued_at_ms AND expires_at_ms <= intent_expires_at_ms"),
        *execution_hashes("decision_sha256"),
        *execution_safe("intent_expires_at_ms", "issued_at_ms", "expires_at_ms"),
    )


class ExecutionActionCommand(FleetScoped, Base):
    """The E05 side of one E03 command admission: its attempt, intent and action lease."""

    __tablename__ = "execution_action_commands"
    execution_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str] = mapped_column(String(36))
    intent_id: Mapped[str] = mapped_column(String(36))
    decision_row_id: Mapped[str | None] = mapped_column(String(36))
    installation_id: Mapped[str] = mapped_column(String(128))
    recovery_epoch: Mapped[int] = mapped_column(BigInteger)
    admission_id: Mapped[str] = mapped_column(String(36))
    command_id: Mapped[str] = mapped_column(String(128))
    command_digest: Mapped[str] = mapped_column(String(64))
    lease_token_digest: Mapped[str | None] = mapped_column(String(64))
    lease_revision: Mapped[int | None] = mapped_column(BigInteger)
    claimed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    renewed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    lease_expires_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="admitted")
    __table_args__ = fleet_scoped(
        execution_parent("execution_attempts", "attempt_id"),
        execution_parent("execution_action_intents", "intent_id"),
        execution_parent("execution_action_decisions", "decision_row_id"),
        fleet_parent("runtime_command_admissions", "admission_id"),
        # The same admission, by its E03 identity: never another workspace or epoch's.
        ForeignKeyConstraint(
            ("installation_id", "recovery_epoch", "workspace_id", "command_id"),
            [
                f"runtime_command_admissions.{field}"
                for field in ("installation_id", "recovery_epoch", "workspace_id", "command_id")
            ],
        ),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "attempt_id"),
        UniqueConstraint(*FLEET_SCOPE, "admission_id"),
        fleet_status("'admitted','claimed','stopped','uncertain','expired'"),
        CheckConstraint("length(installation_id) BETWEEN 1 AND 128"),
        CheckConstraint("length(command_id) BETWEEN 1 AND 128"),
        CheckConstraint(
            "(lease_token_digest IS NULL) = (claimed_at_ms IS NULL) AND (claimed_at_ms IS NULL) = (renewed_at_ms IS NULL) AND (claimed_at_ms IS NULL) = (lease_expires_at_ms IS NULL) AND (claimed_at_ms IS NULL) = (lease_revision IS NULL)"
        ),
        CheckConstraint(
            "claimed_at_ms IS NULL OR (renewed_at_ms >= claimed_at_ms AND lease_expires_at_ms > renewed_at_ms AND lease_expires_at_ms <= renewed_at_ms + 60000)"
        ),
        CheckConstraint("status <> 'claimed' OR claimed_at_ms IS NOT NULL"),
        CheckConstraint(f"claimed_at_ms IS NULL OR claimed_at_ms {SAFE}"),
        CheckConstraint(f"renewed_at_ms IS NULL OR renewed_at_ms {SAFE}"),
        CheckConstraint(f"lease_expires_at_ms IS NULL OR lease_expires_at_ms {SAFE}"),
        CheckConstraint("lease_revision IS NULL OR lease_revision BETWEEN 1 AND 9007199254740991"),
        *execution_hashes("command_digest", "lease_token_digest"),
        *execution_safe("recovery_epoch"),
        Index(
            "execution_action_lease_due_idx",
            "lease_expires_at_ms",
            "id",
            postgresql_where=text("status = 'claimed'"),
        ),
    )


class ExecutionActionReceipt(FleetScoped, Base):
    __tablename__ = "execution_action_receipts"
    execution_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str] = mapped_column(String(36))
    intent_id: Mapped[str] = mapped_column(String(36))
    receipt_id: Mapped[str] = mapped_column(String(128))
    receipt_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    response_artifact_id: Mapped[str | None] = mapped_column(String(36))
    response_artifact_version: Mapped[int | None] = mapped_column(Integer)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(12))
    __table_args__ = fleet_scoped(
        execution_parent("execution_attempts", "attempt_id"),
        execution_parent("execution_action_intents", "intent_id"),
        *execution_optional_artifact("response"),
        UniqueConstraint(*FLEET_SCOPE, "execution_id", "id"),
        UniqueConstraint(*FLEET_SCOPE, "intent_id", "receipt_id"),
        fleet_status("'accepted','observed_effect','unknown'"),
        CheckConstraint("source IN ('live','historical')"),
        CheckConstraint("length(receipt_id) BETWEEN 1 AND 128"),
        CheckConstraint("length(receipt_bytes) BETWEEN 1 AND 65536"),
        *execution_hashes("receipt_sha256", "response_sha256"),
    )


class ExecutionActionObservation(FleetScoped, Base):
    __tablename__ = "execution_action_observations"
    execution_id: Mapped[str] = mapped_column(String(36))
    intent_id: Mapped[str] = mapped_column(String(36))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    observation_id: Mapped[str] = mapped_column(String(128))
    payload_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(20))
    qualified: Mapped[bool] = mapped_column(Boolean)
    rule_id: Mapped[str] = mapped_column(String(128))
    rule_version: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = fleet_scoped(
        execution_parent("execution_action_intents", "intent_id"),
        execution_parent("execution_attempts", "attempt_id"),
        UniqueConstraint(*FLEET_SCOPE, "intent_id", "observation_id"),
        CheckConstraint("result IN ('effect_present','effect_absent','unknown')"),
        CheckConstraint("NOT qualified OR result = 'effect_absent'"),
        CheckConstraint("source IN ('readback','restore_readback','historical','resolution')"),
        CheckConstraint("(source = 'resolution') = (actor_user_id IS NOT NULL)"),
        CheckConstraint("length(observation_id) BETWEEN 1 AND 128"),
        CheckConstraint("length(rule_id) BETWEEN 1 AND 128"),
        CheckConstraint("length(rule_version) BETWEEN 1 AND 128"),
        CheckConstraint("length(payload_bytes) BETWEEN 1 AND 65536"),
        *execution_hashes("payload_sha256"),
    )


class ExecutionActionOutcome(FleetScoped, Base):
    __tablename__ = "execution_action_outcomes"
    execution_id: Mapped[str] = mapped_column(String(36))
    intent_id: Mapped[str] = mapped_column(String(36))
    receipt_row_id: Mapped[str] = mapped_column(String(36))
    outcome_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    outcome_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    rule_id: Mapped[str] = mapped_column(String(128))
    rule_version: Mapped[str] = mapped_column(String(128))
    observed_at_ms: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = fleet_scoped(
        execution_parent("execution_action_intents", "intent_id"),
        execution_parent("execution_action_receipts", "receipt_row_id"),
        UniqueConstraint(*FLEET_SCOPE, "intent_id"),
        CheckConstraint("state IN ('verified','not_verified','unknown')"),
        CheckConstraint("length(rule_id) BETWEEN 1 AND 128"),
        CheckConstraint("length(rule_version) BETWEEN 1 AND 128"),
        CheckConstraint("length(outcome_bytes) BETWEEN 1 AND 65536"),
        *execution_hashes("outcome_sha256"),
        *execution_safe("observed_at_ms"),
    )


V16_TABLES = frozenset(
    {
        "execution_action_intents",
        "execution_human_tasks",
        "execution_action_decisions",
        "execution_action_commands",
        "execution_action_receipts",
        "execution_action_observations",
        "execution_action_outcomes",
    }
)


EXECUTION_TABLES = frozenset(
    {
        "executable_publications",
        "executable_dependencies",
        "execution_deployments",
        "execution_deployment_revisions",
        "deployment_bindings",
        "deployment_assurance_bindings",
        "execution_artifacts",
        "execution_context_sources",
        "execution_context_versions",
        "executions",
        "execution_step_instances",
        "execution_attempts",
        "execution_waits",
        "execution_requests",
        "execution_outbox",
        "execution_inbox",
        "execution_events",
        "execution_control_requests",
        "execution_usage_reservations",
        "execution_capacities",
        "execution_scheduler_cursors",
        "execution_activity_deliveries",
        "execution_leases",
        "execution_reconciliation_observations",
    }
)
EXECUTION_TABLES = EXECUTION_TABLES | V16_TABLES
