import base64
import os
from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from lotuspond import initialize as migration
from lotuspond.config import Settings
from lotuspond.models import (
    AlertDelivery,
    GatewayUsage,
    IdentityChallenge,
    Incident,
    IncidentEvent,
    Invitation,
    LoginSession,
    MailOutbox,
    MfaEnrollment,
    MfaFactor,
    MfaLoginChallenge,
    MfaRecoveryCode,
    OfflineEnrollment,
    OfflineIdentityAttestation,
    SchemaVersion,
    Trace,
    User,
    Workspace,
    new_id,
    now,
)
from lotuspond.schema_version import SCHEMA_VERSION


def rewind_identity_v5(connection):
    MailOutbox.__table__.drop(connection)
    Invitation.__table__.drop(connection)
    IdentityChallenge.__table__.drop(connection)
    connection.execute(text("ALTER TABLE api_keys DROP COLUMN created_by_user_id"))


def rewind_fleet_v13(connection):
    from lotuspond.fleet.migration import FLEET_TABLES
    from lotuspond.models import Base

    Base.metadata.drop_all(connection, tables=[Base.metadata.tables[name] for name in FLEET_TABLES])
    connection.execute(text("ALTER TABLE environments DROP CONSTRAINT environments_fleet_scope_unique"))


def rewind_registry_v12(connection):
    from lotuspond.models import PackagePublicationRequest, WorkflowPackageVersion

    rewind_fleet_v13(connection)
    PackagePublicationRequest.__table__.drop(connection)
    WorkflowPackageVersion.__table__.drop(connection)


def rewind_plan_v11(connection):
    rewind_registry_v12(connection)
    connection.execute(text("ALTER TABLE workspaces DROP CONSTRAINT workspaces_plan_check"))


def rewind_notices_v10(connection):
    rewind_plan_v11(connection)
    """Restore the exact version-9 mail_outbox constraint shape, including its generated name."""
    connection.execute(
        text(
            "ALTER TABLE mail_outbox "
            "DROP CONSTRAINT mail_outbox_kind_check, "
            "DROP CONSTRAINT mail_outbox_reference_check, "
            "ADD CONSTRAINT mail_outbox_kind_check CHECK ("
            "kind IN ('email_verification','password_reset','invitation')), "
            "ADD CONSTRAINT mail_outbox_check CHECK ("
            "(identity_challenge_id IS NOT NULL AND invitation_id IS NULL) OR "
            "(identity_challenge_id IS NULL AND invitation_id IS NOT NULL))"
        )
    )


def rewind_policy_v9(connection):
    rewind_notices_v10(connection)
    connection.execute(
        text(
            "ALTER TABLE workspaces "
            "DROP CONSTRAINT workspaces_require_mfa_scope_check, "
            "DROP COLUMN require_mfa"
        )
    )


def rewind_mfa_v8(connection):
    rewind_policy_v9(connection)
    MfaRecoveryCode.__table__.drop(connection)
    MfaLoginChallenge.__table__.drop(connection)
    MfaEnrollment.__table__.drop(connection)
    connection.execute(text("DROP INDEX sessions_mfa_factor_idx"))
    connection.execute(
        text(
            "ALTER TABLE sessions "
            "DROP CONSTRAINT sessions_mfa_factor_fk, "
            "DROP CONSTRAINT sessions_mfa_binding_check, "
            "DROP CONSTRAINT sessions_mfa_verified_time_check, "
            "DROP CONSTRAINT sessions_mfa_version_check, "
            "DROP COLUMN mfa_verified_at, "
            "DROP COLUMN mfa_factor_id, "
            "DROP COLUMN mfa_factor_version"
        )
    )
    MfaFactor.__table__.drop(connection)


def rewind_trace_and_offline_identity_v7(connection):
    rewind_mfa_v8(connection)
    OfflineEnrollment.__table__.drop(connection)
    OfflineIdentityAttestation.__table__.drop(connection)
    connection.execute(text("ALTER TABLE traces DROP CONSTRAINT traces_cost_measurement_check"))
    connection.execute(text("ALTER TABLE traces DROP COLUMN cost_measurement"))
    connection.execute(text("ALTER TABLE traces ALTER COLUMN cost_usd SET NOT NULL"))


def rewind_monitoring_v6(connection):
    rewind_trace_and_offline_identity_v7(connection)
    AlertDelivery.__table__.drop(connection)
    IncidentEvent.__table__.drop(connection)
    connection.execute(text("ALTER TABLE incidents DROP CONSTRAINT incidents_severity_check"))
    # v5→v6 adds the resolution predicate; it does not belong in the v5 fixture.
    # Rewinding a current ORM schema must remove its anonymously named copy too.
    resolution = next(
        item["name"] for item in migration.inspect(connection).get_check_constraints("incidents")
        if "resolved_at" in item["sqltext"]
    )
    connection.execute(text(f'ALTER TABLE incidents DROP CONSTRAINT "{resolution}"'))
    for column in (
        "project_id",
        "voice_session_id",
        "assessment_id",
        "monitor_id",
        "category",
        "source",
        "resolution_reason",
        "reopened_at",
    ):
        connection.execute(text(f"ALTER TABLE incidents DROP COLUMN {column}"))


def test_additive_migration_preserves_existing_rows(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    # Only the isolated random test schema is rewound to the immediately prior fixture shape.
    with engine.begin() as connection:
        rewind_monitoring_v6(connection)
        rewind_identity_v5(connection)
        GatewayUsage.__table__.drop(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=2))
        before = list(connection.execute(select(User.id)).scalars())
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert list(connection.execute(select(User.id)).scalars()) == before
        assert connection.scalar(select(func.count()).select_from(GatewayUsage)) == 0
        assert set(("identity_challenges", "invitations", "mail_outbox")).issubset(
            set(migration.inspect(connection).get_table_names())
        )
        assert "created_by_user_id" in {
            item["name"] for item in migration.inspect(connection).get_columns("api_keys")
        }
    with pytest.raises(ValueError, match="already"):
        migration.migrate(settings)
    with pytest.raises(ValueError, match="not empty"):
        migration.initialize(settings)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user@remote.example/lotuspond_platform_dev",
        "postgresql+psycopg://user@127.0.0.1/customer_data",
    ],
)
def test_database_tools_refuse_nonlocal_or_unapproved_names(url):
    settings = Settings(database_url=url)
    with pytest.raises(ValueError, match="localhost"):
        migration.initialize(settings)
    with pytest.raises(ValueError, match="localhost"):
        migration.migrate(settings)


def test_ledger_widening_preserves_recorded_money(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("Precision migration requires PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_monitoring_v6(connection)
        rewind_identity_v5(connection)
        for column in ("reserved_usd", "charged_usd", "actual_usd"):
            connection.execute(
                text(f"ALTER TABLE gateway_usage ALTER COLUMN {column} TYPE NUMERIC(14,6)")
            )
        for column in ("prompt_tokens", "completion_tokens"):
            connection.execute(
                text(f"ALTER TABLE gateway_usage ALTER COLUMN {column} TYPE INTEGER")
            )
        connection.execute(
            GatewayUsage.__table__.insert().values(
                workspace_id=account,
                model="fixture",
                status="settled",
                reserved_usd="0.123456",
                charged_usd="0.123456",
                actual_usd="0.123456",
                prompt_tokens=123,
                completion_tokens=12,
            )
        )
        connection.execute(SchemaVersion.__table__.update().values(version=3))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.begin() as connection:
        assert str(connection.scalar(select(GatewayUsage.charged_usd))) == "0.123456"
        connection.execute(GatewayUsage.__table__.update().values(prompt_tokens=10**12))
        assert connection.scalar(select(GatewayUsage.prompt_tokens)) == 10**12


def test_identity_migration_from_v4_preserves_existing_rows(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_monitoring_v6(connection)
        rewind_identity_v5(connection)
        before = list(connection.execute(select(User.id)).scalars())
        connection.execute(SchemaVersion.__table__.update().values(version=4))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert list(connection.execute(select(User.id)).scalars()) == before
        assert "created_by_user_id" in {
            item["name"] for item in migration.inspect(connection).get_columns("api_keys")
        }


def test_identity_migration_refuses_v4_without_ledger(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_monitoring_v6(connection)
        rewind_identity_v5(connection)
        GatewayUsage.__table__.drop(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=4))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    with pytest.raises(ValueError, match="Unexpected schema state"):
        migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == 4


def test_monitoring_v6_migration_preserves_existing_incidents(
    engine, client, account, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    incident_id = "11111111-1111-4111-8111-111111111111"
    with engine.begin() as connection:
        connection.execute(
            Incident.__table__.insert().values(
                id=incident_id,
                workspace_id=account,
                title="Existing incident",
                severity="high",
                status="open",
            )
        )
        rewind_monitoring_v6(connection)
        before = list(connection.execute(select(User.id)).scalars())
        connection.execute(SchemaVersion.__table__.update().values(version=5))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert list(connection.execute(select(User.id)).scalars()) == before
        restored = connection.execute(
            select(Incident.category, Incident.source).where(Incident.id == incident_id)
        ).one()
        assert restored.category == "manual"
        assert restored.source == "manual"
        assert connection.scalar(select(func.count()).select_from(IncidentEvent)) == 0
        assert connection.scalar(select(func.count()).select_from(AlertDelivery)) == 0


def test_monitoring_v6_migration_refuses_partial_schema(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_monitoring_v6(connection)
        connection.execute(
            text("ALTER TABLE incidents ADD COLUMN category VARCHAR(40) NOT NULL DEFAULT 'manual'")
        )
        connection.execute(SchemaVersion.__table__.update().values(version=5))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    with pytest.raises(ValueError, match="Unexpected schema state"):
        migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == 5
        assert "category" in {
            item["name"] for item in migration.inspect(connection).get_columns("incidents")
        }
        assert not migration.inspect(connection).has_table("incident_events")


def test_trace_cost_and_offline_identity_v7_migration_preserves_legacy_amount(
    engine, client, account, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    trace_id = new_id()
    with engine.begin() as connection:
        connection.execute(
            Trace.__table__.insert().values(
                id=trace_id,
                workspace_id=account,
                external_id="legacy-precise-cost",
                name="Legacy cost",
                status="ok",
                latency_ms=1,
                cost_usd="0.123456",
                cost_measurement="unknown",
                attributes={},
            )
        )
        rewind_trace_and_offline_identity_v7(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=6))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.connect() as connection:
        restored = connection.execute(
            select(Trace.cost_usd, Trace.cost_measurement).where(Trace.id == trace_id)
        ).one()
        assert str(restored.cost_usd) == "0.123456"
        assert restored.cost_measurement == "unknown"
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert migration.inspect(connection).has_table("offline_identity_attestations")
        assert migration.inspect(connection).has_table("offline_enrollments")
    replay = client.post(
        "/api/v1/traces",
        json={
            "external_id": "legacy-precise-cost",
            "name": "Legacy cost",
            "status": "ok",
            "latency_ms": 1,
            "cost_usd": 0.123456,
        },
    )
    assert replay.status_code == 409


def test_v7_migration_refuses_partial_schema(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_trace_and_offline_identity_v7(connection)
        connection.execute(
            text(
                "ALTER TABLE traces ADD COLUMN cost_measurement "
                "VARCHAR(20) NOT NULL DEFAULT 'unknown'"
            )
        )
        connection.execute(SchemaVersion.__table__.update().values(version=6))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    with pytest.raises(ValueError, match="Unexpected schema state"):
        migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == 6
        assert not migration.inspect(connection).has_table("offline_identity_attestations")


def test_offline_enrollment_requires_same_user_attestation(engine, client, account):
    if engine.dialect.name != "postgresql":
        pytest.skip("Composite identity integrity requires isolated PostgreSQL")
    second_user_id = new_id()
    attestation_id = new_id()
    with engine.begin() as connection:
        owner_id = connection.scalar(select(User.id).limit(1))
        connection.execute(
            User.__table__.insert().values(
                id=second_user_id,
                email=f"{second_user_id}@example.com",
                name="Second user",
                password_hash="test-only",
            )
        )
        connection.execute(
            OfflineIdentityAttestation.__table__.insert().values(
                id=attestation_id,
                user_id=owner_id,
                source="in_person",
                source_reference="test-source",
                operator_reference="test-operator",
                attested_at=now(),
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            OfflineEnrollment.__table__.insert().values(
                user_id=second_user_id,
                attestation_id=attestation_id,
                token_hash="a" * 64,
                expires_at=now() + timedelta(hours=1),
            )
        )


def test_mfa_v8_migration_preserves_sessions_without_guessing_verification(
    engine, client, account, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        session_before = connection.execute(
            select(LoginSession.id, LoginSession.user_id).limit(1)
        ).one()
        rewind_mfa_v8(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=7))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.begin() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        restored = connection.execute(
            select(
                LoginSession.mfa_verified_at,
                LoginSession.mfa_factor_id,
                LoginSession.mfa_factor_version,
            ).where(LoginSession.id == session_before.id)
        ).one()
        assert restored == (None, None, None)
        assert {
            "mfa_factors",
            "mfa_enrollments",
            "mfa_login_challenges",
            "mfa_recovery_codes",
        }.issubset(set(migration.inspect(connection).get_table_names()))
        factor_id = new_id()
        connection.execute(
            MfaFactor.__table__.insert().values(
                id=factor_id,
                user_id=session_before.user_id,
                secret_ciphertext="encrypted-test-secret",
                version=1,
                enabled_at=now(),
            )
        )
        connection.execute(
            MfaLoginChallenge.__table__.insert().values(
                user_id=session_before.user_id,
                factor_id=factor_id,
                factor_version=1,
                token_hash="a" * 64,
                expires_at=now() + timedelta(minutes=5),
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            MfaRecoveryCode.__table__.insert().values(
                user_id=session_before.user_id,
                factor_id=factor_id,
                factor_version=2,
                code_hash="b" * 64,
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            LoginSession.__table__.insert().values(
                user_id=session_before.user_id,
                token_hash="c" * 64,
                expires_at=now() + timedelta(hours=1),
                mfa_verified_at=now(),
                mfa_factor_id=factor_id,
                mfa_factor_version=2,
            )
        )


def test_mfa_v8_migration_refuses_partial_schema(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_mfa_v8(connection)
        MfaFactor.__table__.create(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=7))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    with pytest.raises(ValueError, match="Unexpected schema state"):
        migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == 7
        assert migration.inspect(connection).has_table("mfa_factors")
        assert not migration.inspect(connection).has_table("mfa_enrollments")
        assert "mfa_verified_at" not in {
            item["name"] for item in migration.inspect(connection).get_columns("sessions")
        }


def test_policy_v9_migration_keeps_workspaces_and_starts_without_the_requirement(
    engine, client, account, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        before = list(
            connection.execute(
                select(Workspace.id, Workspace.kind, Workspace.name).order_by(Workspace.id)
            ).all()
        )
        rewind_policy_v9(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=8))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.begin() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert (
            list(
                connection.execute(
                    select(Workspace.id, Workspace.kind, Workspace.name).order_by(Workspace.id)
                ).all()
            )
            == before
        )
        # Every migrated workspace keeps its access unchanged until an administrator opts in.
        assert list(connection.execute(select(Workspace.require_mfa)).scalars()) == [False] * len(
            before
        )
        assert "require_mfa" not in str(
            {
                item["name"]: item["default"]
                for item in migration.inspect(connection).get_columns("workspaces")
                if item["default"] is not None
            }
        )
        connection.execute(
            Workspace.__table__.update()
            .where(Workspace.kind == "organization")
            .values(require_mfa=True)
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            Workspace.__table__.update()
            .where(Workspace.kind == "personal")
            .values(require_mfa=True)
        )


def test_policy_v9_migration_refuses_an_existing_column(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE workspaces DROP CONSTRAINT workspaces_require_mfa_scope_check")
        )
        connection.execute(SchemaVersion.__table__.update().values(version=8))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    with pytest.raises(ValueError, match="Unexpected schema state"):
        migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == 8


def test_notice_v10_migration_widens_mail_outbox_without_touching_rows(
    engine, client, account, app, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    app.state.settings.mail_enabled = True
    app.state.settings.mail_encryption_key = app.state.settings.mail_encryption_key or SecretStr(
        base64.urlsafe_b64encode(b"k" * 32).decode()
    )
    app.state.settings.smtp_host = "localhost"
    app.state.settings.smtp_from = "identity@lotuspond.example.com"
    app.state.settings.smtp_starttls = False
    assert client.post("/api/v1/auth/verification/request", json={}).status_code == 202
    with engine.begin() as connection:
        before = list(
            connection.execute(
                select(MailOutbox.id, MailOutbox.kind, MailOutbox.identity_challenge_id).order_by(
                    MailOutbox.id
                )
            ).all()
        )
        assert before
        rewind_notices_v10(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=9))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.begin() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert (
            list(
                connection.execute(
                    select(
                        MailOutbox.id, MailOutbox.kind, MailOutbox.identity_challenge_id
                    ).order_by(MailOutbox.id)
                ).all()
            )
            == before
        )
        connection.execute(
            MailOutbox.__table__.insert().values(
                id=new_id(),
                kind="security_notice",
                recipient_hash="a" * 64,
                body_ciphertext="v1.fixture",
                status="queued",
                attempts=0,
                available_at=now(),
                created_at=now(),
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            MailOutbox.__table__.insert().values(
                id=new_id(),
                kind="security_notice",
                recipient_hash="a" * 64,
                body_ciphertext="v1.fixture",
                status="queued",
                attempts=0,
                available_at=now(),
                created_at=now(),
                identity_challenge_id=before[0].identity_challenge_id,
            )
        )


def test_notice_v10_migration_refuses_a_partial_constraint_shape(
    engine, client, account, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_notices_v10(connection)
        # Neither the complete version-9 pair nor the version-10 constraint remains.
        connection.execute(
            text("ALTER TABLE mail_outbox DROP CONSTRAINT mail_outbox_kind_check")
        )
        connection.execute(SchemaVersion.__table__.update().values(version=9))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    with pytest.raises(ValueError, match="Unexpected schema state"):
        migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == 9
        assert "mail_outbox_reference_check" not in set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'mail_outbox'::regclass AND contype = 'c'"
                )
            ).scalars()
        )


def test_an_unwidened_table_at_version_9_is_still_migrated(engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_registry_v12(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=9))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    # A table already created from the authoritative model carries the target shape already.
    migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION


def test_plan_v11_migration_narrows_the_column_and_keeps_every_workspace(
    engine, client, account, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        before = list(
            connection.execute(select(Workspace.id, Workspace.plan).order_by(Workspace.id)).all()
        )
        rewind_plan_v11(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=10))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.begin() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert (
            list(
                connection.execute(
                    select(Workspace.id, Workspace.plan).order_by(Workspace.id)
                ).all()
            )
            == before
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(Workspace.__table__.update().values(plan="platinum"))


def test_plan_v11_migration_refuses_a_workspace_outside_the_ladder(
    engine, client, account, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("The migration is intentionally restricted to local PostgreSQL")
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_plan_v11(connection)
        connection.execute(
            Workspace.__table__.update().where(Workspace.id == account).values(plan="platinum")
        )
        connection.execute(SchemaVersion.__table__.update().values(version=10))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    # Narrowing must not quietly rewrite a tier nobody chose; the whole run rolls back.
    with pytest.raises(Exception):
        migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == 10
        assert connection.scalar(
            select(Workspace.plan).where(Workspace.id == account)
        ) == "platinum"


def test_registry_v12_additive_preserves_rows_and_refuses_repeat(
    engine, client, account, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("Registry migration requires isolated PostgreSQL")
    from lotuspond import models

    assert hasattr(models, "WorkflowPackageVersion"), "Registry model missing"
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        before = list(connection.execute(select(User.id, User.email)).all())
        rewind_registry_v12(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=11))
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert list(connection.execute(select(User.id, User.email)).all()) == before
        assert (
            connection.scalar(select(func.count()).select_from(models.WorkflowPackageVersion)) == 0
        )
        assert (
            connection.scalar(select(func.count()).select_from(models.PackagePublicationRequest))
            == 0
        )
    with pytest.raises(ValueError, match="already"):
        migration.migrate(settings)


@pytest.mark.parametrize("partial", ["version", "request", "both", "older"])
def test_registry_partial_state_rolls_back_entire_chain(
    engine, client, account, monkeypatch, partial
):
    if engine.dialect.name != "postgresql":
        pytest.skip("Registry migration requires isolated PostgreSQL")
    from lotuspond import models

    assert hasattr(models, "WorkflowPackageVersion"), "Registry model missing"
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        rewind_fleet_v13(connection)
        if partial in {"version", "older"}:
            models.PackagePublicationRequest.__table__.drop(connection)
        if partial == "request":
            # Remove only the FK, retaining a deliberate inconsistent fixture table.
            fk = next(
                item
                for item in migration.inspect(connection).get_foreign_keys(
                    "package_publication_requests"
                )
                if item["referred_table"] == "workflow_package_versions"
            )
            connection.execute(
                text(f'ALTER TABLE package_publication_requests DROP CONSTRAINT "{fk["name"]}"')
            )
            models.WorkflowPackageVersion.__table__.drop(connection)
        if partial == "older":
            connection.execute(text("ALTER TABLE workspaces DROP CONSTRAINT workspaces_plan_check"))
        version = 10 if partial == "older" else 11
        connection.execute(SchemaVersion.__table__.update().values(version=version))
        before_tables = set(migration.inspect(connection).get_table_names())
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    with pytest.raises(ValueError, match="Unexpected schema state"):
        migration.migrate(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == version
        assert set(migration.inspect(connection).get_table_names()) == before_tables
        if partial == "older":
            assert "workspaces_plan_check" not in {
                item["name"]
                for item in migration.inspect(connection).get_check_constraints("workspaces")
            }


def test_registry_fresh_empty_initialization(engine, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("Fresh initializer requires isolated PostgreSQL")
    # This fixture owns a random schema; no other database objects are removed.
    migration.Base.metadata.drop_all(engine)
    settings = Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    monkeypatch.setattr(migration, "engine_for", lambda _: engine)
    migration.initialize(settings)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert {"workflow_package_versions", "package_publication_requests"}.issubset(
            migration.inspect(connection).get_table_names()
        )
    with pytest.raises(ValueError, match="not empty"):
        migration.initialize(settings)
