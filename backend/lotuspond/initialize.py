import argparse

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .config import Settings
from .database import engine_for
from .models import AuditEvent, Base, SchemaVersion, User
from .schema_version import SCHEMA_VERSION


def initialize(settings: Settings):
    url = make_url(settings.database_url.get_secret_value())
    if (
        settings.env == "production"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.database not in {"lotuspond_platform_dev", "lotuspond_platform_test"}
    ):
        raise ValueError(
            "Initialization requires a separate, explicitly named localhost development or test database"
        )
    engine = engine_for(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
            if inspect(connection).get_table_names():
                raise ValueError(
                    "Database is not empty. No existing schema or records will be modified."
                )
            Base.metadata.create_all(connection)
            connection.execute(SchemaVersion.__table__.insert().values(version=SCHEMA_VERSION))
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Lotuspond local database tools")
    parser.add_argument("command", choices=["init", "grant-admin", "migrate"])
    parser.add_argument("--user-id")
    parser.add_argument("--confirm-email")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "migrate":
        migrate(settings)
        print("Applied the additive local schema migration. Existing records were preserved.")
        return
    if args.command == "init":
        initialize(settings)
        print(
            "Created the new Lotuspond schema. No accounts, fixtures, or imported data were seeded."
        )
        return
    url = make_url(settings.database_url.get_secret_value())
    if (
        settings.env == "production"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.database != "lotuspond_platform_dev"
    ):
        raise ValueError(
            "This operator provisioning command is restricted to the new localhost development database"
        )
    engine = engine_for(settings)
    try:
        with Session(engine) as db, db.begin():
            user = db.get(User, args.user_id)
            if not user or user.email != args.confirm_email:
                raise ValueError("Exact existing user ID and matching email are required")
            user.is_platform_admin = True
            db.add(
                AuditEvent(
                    actor_id=user.id,
                    action="admin.local_provisioning",
                    resource_id=user.id,
                    details={"method": "local-operator-cli"},
                )
            )
        print("Local platform operator granted. Public signup never grants this permission.")
    finally:
        engine.dispose()


def migrate(settings: Settings):
    """Apply the reviewed additive v2 through v12 chain; never drop/import records."""
    url = make_url(settings.database_url.get_secret_value())
    if (
        settings.env == "production"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.database not in {"lotuspond_platform_dev", "lotuspond_platform_test"}
    ):
        raise ValueError(
            "Migration is restricted to independently configured localhost development/test databases"
        )
    engine = engine_for(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
            versions = list(connection.execute(SchemaVersion.__table__.select()).scalars())
            if versions == [SCHEMA_VERSION]:
                raise ValueError("Database is already at the current version; no changes made")
            if len(versions) != 1 or versions[0] not in {2, 3, 4, 5, 6, 7, 8, 9, 10, 11}:
                raise ValueError("Unexpected schema state; no changes made")
            current = versions[0]
            inspector = inspect(connection)
            has_ledger = inspector.has_table("gateway_usage")
            if current == 2 and not has_ledger:
                Base.metadata.tables["gateway_usage"].create(connection)
                # The authoritative v5 model already has the v4 widened types.
                has_ledger = True
                current = 4
            elif current == 3 and has_ledger:
                # Static column names only. Precision widening preserves every existing value.
                for column in ("reserved_usd", "charged_usd", "actual_usd"):
                    connection.execute(
                        text(f"ALTER TABLE gateway_usage ALTER COLUMN {column} TYPE NUMERIC(30,6)")
                    )
                for column in ("prompt_tokens", "completion_tokens"):
                    connection.execute(
                        text(f"ALTER TABLE gateway_usage ALTER COLUMN {column} TYPE BIGINT")
                    )
                current = 4
            elif current in {2, 3}:
                raise ValueError("Unexpected schema state; no changes made")
            if current == 4:
                if not has_ledger:
                    raise ValueError("Unexpected schema state; no changes made")
                inspector = inspect(connection)
                identity_tables = {"identity_challenges", "invitations", "mail_outbox"}
                columns = {column["name"] for column in inspector.get_columns("api_keys")}
                if identity_tables.intersection(inspector.get_table_names()) or (
                    "created_by_user_id" in columns
                ):
                    raise ValueError("Unexpected schema state; no changes made")
                connection.execute(
                    text(
                        "ALTER TABLE api_keys ADD COLUMN created_by_user_id "
                        "VARCHAR(36) REFERENCES users(id)"
                    )
                )
                for table_name in ("identity_challenges", "invitations", "mail_outbox"):
                    Base.metadata.tables[table_name].create(connection)
                current = 5
            if current == 5:
                inspector = inspect(connection)
                required_tables = {
                    "gateway_usage",
                    "identity_challenges",
                    "invitations",
                    "mail_outbox",
                }
                columns = {column["name"] for column in inspector.get_columns("api_keys")}
                incident_columns = {
                    column["name"] for column in inspector.get_columns("incidents")
                }
                new_incident_columns = {
                    "project_id",
                    "voice_session_id",
                    "assessment_id",
                    "monitor_id",
                    "category",
                    "source",
                    "resolution_reason",
                    "reopened_at",
                }
                existing_tables = set(inspector.get_table_names())
                if (
                    not required_tables.issubset(existing_tables)
                    or "created_by_user_id" not in columns
                    or new_incident_columns.intersection(incident_columns)
                    or {"incident_events", "alert_deliveries"}.intersection(existing_tables)
                ):
                    raise ValueError("Unexpected schema state; no changes made")
                connection.execute(
                    text(
                        "ALTER TABLE incidents "
                        "ADD COLUMN project_id VARCHAR(36), "
                        "ADD COLUMN voice_session_id VARCHAR(36), "
                        "ADD COLUMN assessment_id VARCHAR(36), "
                        "ADD COLUMN monitor_id VARCHAR(36), "
                        "ADD COLUMN category VARCHAR(40) NOT NULL DEFAULT 'manual', "
                        "ADD COLUMN source VARCHAR(40) NOT NULL DEFAULT 'manual', "
                        "ADD COLUMN resolution_reason TEXT, "
                        "ADD COLUMN reopened_at TIMESTAMP WITH TIME ZONE"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE incidents "
                        "ALTER COLUMN category DROP DEFAULT, "
                        "ALTER COLUMN source DROP DEFAULT, "
                        "ADD CONSTRAINT incidents_project_fk FOREIGN KEY (workspace_id, project_id) "
                        "REFERENCES projects(workspace_id, id), "
                        "ADD CONSTRAINT incidents_voice_session_fk "
                        "FOREIGN KEY (workspace_id, voice_session_id) "
                        "REFERENCES voice_sessions(workspace_id, id), "
                        "ADD CONSTRAINT incidents_assessment_fk "
                        "FOREIGN KEY (workspace_id, assessment_id) "
                        "REFERENCES assessments(workspace_id, id), "
                        "ADD CONSTRAINT incidents_monitor_fk FOREIGN KEY (workspace_id, monitor_id) "
                        "REFERENCES monitors(workspace_id, id), "
                        "ADD CONSTRAINT incidents_assessment_unique UNIQUE (workspace_id, assessment_id), "
                        "ADD CONSTRAINT incidents_severity_check "
                        "CHECK (severity IN ('low','medium','high','critical')), "
                        "ADD CONSTRAINT incidents_category_check "
                        "CHECK (category IN ('manual','assessment_failed','assessment_unavailable')), "
                        "ADD CONSTRAINT incidents_source_check "
                        "CHECK (source IN ('manual','monitor','trace_assessment','voice_assessment')), "
                        "ADD CONSTRAINT incidents_resolution_check CHECK ("
                        "(status='open' AND resolved_at IS NULL) OR "
                        "(status='resolved' AND resolved_at IS NOT NULL))"
                    )
                )
                for table_name in ("incident_events", "alert_deliveries"):
                    Base.metadata.tables[table_name].create(connection)
                current = 6
            if current == 6:
                inspector = inspect(connection)
                existing_tables = set(inspector.get_table_names())
                trace_columns = {
                    column["name"]: column for column in inspector.get_columns("traces")
                }
                required_tables = {"incident_events", "alert_deliveries"}
                offline_tables = {
                    "offline_identity_attestations",
                    "offline_enrollments",
                }
                if (
                    not required_tables.issubset(existing_tables)
                    or offline_tables.intersection(existing_tables)
                    or "cost_measurement" in trace_columns
                    or trace_columns.get("cost_usd", {}).get("nullable") is not False
                ):
                    raise ValueError("Unexpected schema state; no changes made")
                connection.execute(
                    text(
                        "ALTER TABLE traces "
                        "ADD COLUMN cost_measurement VARCHAR(20) NOT NULL DEFAULT 'unknown', "
                        "ALTER COLUMN cost_usd DROP NOT NULL"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE traces "
                        "ALTER COLUMN cost_measurement DROP DEFAULT, "
                        "ADD CONSTRAINT traces_cost_measurement_check CHECK ("
                        "(cost_measurement='reported' AND cost_usd IS NOT NULL) OR "
                        "(cost_measurement='unavailable' AND cost_usd IS NULL) OR "
                        "cost_measurement='unknown')"
                    )
                )
                for table_name in (
                    "offline_identity_attestations",
                    "offline_enrollments",
                ):
                    Base.metadata.tables[table_name].create(connection)
                current = 7
            if current == 7:
                inspector = inspect(connection)
                existing_tables = set(inspector.get_table_names())
                session_columns = {
                    column["name"] for column in inspector.get_columns("sessions")
                }
                required_tables = {
                    "incident_events",
                    "alert_deliveries",
                    "offline_identity_attestations",
                    "offline_enrollments",
                }
                mfa_tables = {
                    "mfa_factors",
                    "mfa_enrollments",
                    "mfa_login_challenges",
                    "mfa_recovery_codes",
                }
                mfa_session_columns = {
                    "mfa_verified_at",
                    "mfa_factor_id",
                    "mfa_factor_version",
                }
                trace_columns = {
                    column["name"]: column for column in inspector.get_columns("traces")
                }
                if (
                    not required_tables.issubset(existing_tables)
                    or mfa_tables.intersection(existing_tables)
                    or mfa_session_columns.intersection(session_columns)
                    or "cost_measurement" not in trace_columns
                    or trace_columns["cost_usd"].get("nullable") is not True
                ):
                    raise ValueError("Unexpected schema state; no changes made")
                for table_name in (
                    "mfa_factors",
                    "mfa_enrollments",
                    "mfa_login_challenges",
                    "mfa_recovery_codes",
                ):
                    Base.metadata.tables[table_name].create(connection)
                connection.execute(
                    text(
                        "ALTER TABLE sessions "
                        "ADD COLUMN mfa_verified_at TIMESTAMP WITH TIME ZONE, "
                        "ADD COLUMN mfa_factor_id VARCHAR(36), "
                        "ADD COLUMN mfa_factor_version INTEGER, "
                        "ADD CONSTRAINT sessions_mfa_factor_fk FOREIGN KEY "
                        "(user_id, mfa_factor_id, mfa_factor_version) REFERENCES "
                        "mfa_factors(user_id, id, version), "
                        "ADD CONSTRAINT sessions_mfa_binding_check CHECK ("
                        "(mfa_verified_at IS NULL AND mfa_factor_id IS NULL "
                        "AND mfa_factor_version IS NULL) OR "
                        "(mfa_verified_at IS NOT NULL AND mfa_factor_id IS NOT NULL "
                        "AND mfa_factor_version IS NOT NULL)), "
                        "ADD CONSTRAINT sessions_mfa_verified_time_check CHECK ("
                        "mfa_verified_at IS NULL OR mfa_verified_at <= created_at), "
                        "ADD CONSTRAINT sessions_mfa_version_check CHECK ("
                        "mfa_factor_version IS NULL OR mfa_factor_version >= 1)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX sessions_mfa_factor_idx ON sessions "
                        "(user_id, mfa_factor_id, mfa_factor_version)"
                    )
                )
                current = 8
            if current == 8:
                inspector = inspect(connection)
                existing_tables = set(inspector.get_table_names())
                workspace_columns = {
                    column["name"] for column in inspector.get_columns("workspaces")
                }
                mfa_tables = {
                    "mfa_factors",
                    "mfa_enrollments",
                    "mfa_login_challenges",
                    "mfa_recovery_codes",
                }
                if not mfa_tables.issubset(existing_tables) or "require_mfa" in workspace_columns:
                    raise ValueError("Unexpected schema state; no changes made")
                # Existing workspaces keep every value and start without the policy.
                connection.execute(
                    text(
                        "ALTER TABLE workspaces "
                        "ADD COLUMN require_mfa BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE workspaces "
                        "ALTER COLUMN require_mfa DROP DEFAULT, "
                        "ADD CONSTRAINT workspaces_require_mfa_scope_check CHECK ("
                        "kind = 'organization' OR require_mfa = false)"
                    )
                )
                current = 9
            if current == 9:
                # Both widen what mail_outbox accepts; no existing row can stop satisfying them.
                existing_constraints = set(
                    connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'mail_outbox'::regclass AND contype = 'c'"
                        )
                    ).scalars()
                )
                if {"mail_outbox_kind_check", "mail_outbox_check"}.issubset(
                    existing_constraints
                ):
                    connection.execute(
                        text(
                            "ALTER TABLE mail_outbox "
                            "DROP CONSTRAINT mail_outbox_kind_check, "
                            "DROP CONSTRAINT mail_outbox_check, "
                            "ADD CONSTRAINT mail_outbox_kind_check CHECK ("
                            "kind IN ('email_verification','password_reset','invitation',"
                            "'security_notice')), "
                            "ADD CONSTRAINT mail_outbox_reference_check CHECK ("
                            "(kind = 'security_notice' AND identity_challenge_id IS NULL "
                            "AND invitation_id IS NULL) OR "
                            "(kind <> 'security_notice' AND ("
                            "(identity_challenge_id IS NOT NULL AND invitation_id IS NULL) OR "
                            "(identity_challenge_id IS NULL AND invitation_id IS NOT NULL))))"
                        )
                    )
                elif "mail_outbox_reference_check" not in existing_constraints:
                    raise ValueError("Unexpected schema state; no changes made")
                # Otherwise an earlier step in this same chain created mail_outbox from the
                # authoritative model, which already carries the version-10 constraints.
                current = 10
            if current == 10:
                workspace_constraints = set(
                    connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'workspaces'::regclass AND contype = 'c'"
                        )
                    ).scalars()
                )
                if "workspaces_plan_check" not in workspace_constraints:
                    # Narrowing, so it is verified against every existing row. A workspace
                    # already outside the ladder rolls the whole migration back rather than
                    # being silently rewritten to a tier nobody chose.
                    connection.execute(
                        text(
                            "ALTER TABLE workspaces ADD CONSTRAINT workspaces_plan_check "
                            "CHECK (plan IN ('developer','team','enterprise'))"
                        )
                    )
                current = 11
            if current == 11:
                registry_tables = {"workflow_package_versions", "package_publication_requests"}
                if registry_tables.intersection(inspect(connection).get_table_names()):
                    raise ValueError("Unexpected schema state; no changes made")
                Base.metadata.tables["workflow_package_versions"].create(connection)
                Base.metadata.tables["package_publication_requests"].create(connection)
                current = 12
            connection.execute(SchemaVersion.__table__.update().values(version=current))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
