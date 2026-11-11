"""Exact committed-v13 preservation and transactional refusal."""

from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

from lotuspond import initialize
from lotuspond.config import Settings
from lotuspond.models import EXECUTION_TABLES, Base, SchemaVersion, new_id
from lotuspond.schema_version import SCHEMA_VERSION
from tests.fleet.test_migration import rows, seed, signature
from tests.fleet.test_schema import load

from .conftest import execution_test_url
from .test_schema import execution_rows


def upgrade(engine, monkeypatch):
    monkeypatch.setattr(initialize, "engine_for", lambda _: engine)
    initialize.migrate(Settings(env="test", database_url=execution_test_url()))


def populated(connection):
    seed(connection)
    values = execution_rows(2)
    load(connection, {name: row for name, row in values.items() if name not in EXECUTION_TABLES})
    legacy_run = dict(values["runs"], id=new_id(), environment_id=None)
    connection.execute(Base.metadata.tables["runs"].insert().values(**legacy_run))
    connection.execute(
        Base.metadata.tables["releases"]
        .insert()
        .values(**dict(values["releases"], id=new_id(), candidate_run_id=legacy_run["id"]))
    )


def test_v13_upgrade_preserves_all_rows(execution_database, monkeypatch):
    engine = execution_database(v13=True)
    with engine.begin() as connection:
        populated(connection)
        before_legacy_rows = rows(connection)
        installed_at = connection.scalar(select(SchemaVersion.installed_at))
    upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        after_legacy_rows = {name: rows(connection)[name] for name in before_legacy_rows}
        assert after_legacy_rows == before_legacy_rows
        assert connection.scalar(select(SchemaVersion.installed_at)) == installed_at
        # The chain continues through every later reviewed step (v15 since Task 7).
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION == 16


def test_fresh_upgrade_signature_equal(execution_database, monkeypatch):
    fresh, migrated, exported = (
        execution_database(),
        execution_database(v13=True),
        execution_database(),
    )
    # PostgreSQL create_all emits deferred AddConstraint DDL which mutates its
    # metadata objects. Copy definitions so later SQLite tests keep their FKs.
    deepcopy(Base.metadata).create_all(fresh)
    upgrade(migrated, monkeypatch)
    with exported.connect() as connection:
        connection.exec_driver_sql((Path(__file__).parents[2] / "schema.sql").read_text())
        exported_signature = signature(connection)
    with fresh.connect() as connection:
        fresh_signature = signature(connection)
    with migrated.connect() as connection:
        migrated_signature = signature(connection)
    assert fresh_signature == migrated_signature == exported_signature


@pytest.mark.parametrize(
    "corruption",
    [
        "execution_table",
        "missing_table",
        "wrong_index",
        "wrong_constraint",
        "unvalidated",
        "disabled_trigger",
        "orphan",
        "wrong_type",
        "duplicate_fk",
        "duplicate_unique",
    ],
)
def test_partial_v13_refused_atomically(execution_database, monkeypatch, corruption):
    engine = execution_database(v13=True)
    with engine.begin() as connection:
        populated(connection)
        if corruption == "execution_table":
            connection.execute(text("CREATE TABLE executions (id integer)"))
        elif corruption == "missing_table":
            connection.execute(text("DROP TABLE fleet_mutation_requests"))
        elif corruption == "wrong_index":
            connection.execute(text("DROP INDEX runtime_instances_active_idx"))
            connection.execute(
                text("CREATE INDEX runtime_instances_active_idx ON runtime_instances (status)")
            )
        elif corruption == "wrong_constraint":
            check = inspect(connection).get_check_constraints("connections")[0]["name"]
            connection.execute(
                text(
                    f'ALTER TABLE connections DROP CONSTRAINT "{check}", ADD CONSTRAINT "{check}" CHECK (true)'
                )
            )
        elif corruption == "unvalidated":
            connection.execute(
                text("ALTER TABLE connections ADD CONSTRAINT suspect CHECK (true) NOT VALID")
            )
        elif corruption == "disabled_trigger":
            connection.execute(text("ALTER TABLE connections DISABLE TRIGGER ALL"))
        elif corruption == "orphan":
            connection.execute(text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                text("UPDATE connections SET environment_id='00000000-0000-0000-0000-000000000000'")
            )
        elif corruption == "wrong_type":
            connection.execute(text("ALTER TABLE connections ALTER COLUMN name TYPE text"))
        if corruption == "duplicate_fk":
            connection.execute(
                text(
                    "ALTER TABLE connections ADD CONSTRAINT redundant_scope FOREIGN KEY (workspace_id, project_id, environment_id) REFERENCES environments (workspace_id, project_id, id)"
                )
            )
        elif corruption == "duplicate_unique":
            connection.execute(
                text(
                    "ALTER TABLE connections ADD CONSTRAINT redundant_unique UNIQUE (workspace_id, project_id, environment_id, id)"
                )
            )
        before, before_signature = rows(connection), signature(connection)
    with pytest.raises(ValueError, match="Unexpected schema state; no changes made"):
        upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        failed_upgrade_version = connection.scalar(select(SchemaVersion.version))
        assert failed_upgrade_version == 13


def test_caller_rollback_after_migration_returns(execution_database):
    from lotuspond.execution.migration import migrate_execution_v14

    engine = execution_database(v13=True)
    with engine.begin() as connection:
        populated(connection)
        before, before_signature = rows(connection), signature(connection)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        migrate_execution_v14(connection)
        assert "executions" in inspect(connection).get_table_names()
        assert connection.scalar(select(SchemaVersion.version)) == 14
        transaction.rollback()
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert connection.scalar(select(SchemaVersion.version)) == 13


@pytest.mark.parametrize(
    "predicate",
    [
        "status = 'revoked'",
        "status <> 'active'",
        "status = 'ACTIVE'",
        "status = 'active' OR status = 'revoked'",
    ],
)
def test_wrong_partial_index_predicate_refused_atomically(
    execution_database, monkeypatch, predicate
):
    engine = execution_database(v13=True)
    with engine.begin() as connection:
        populated(connection)
        connection.execute(text("DROP INDEX runtime_instances_active_idx"))
        connection.execute(
            text(
                "CREATE INDEX runtime_instances_active_idx ON runtime_instances (workspace_id, project_id, environment_id, pool_id, id) WHERE "
                + predicate
            )
        )
        before, before_signature = rows(connection), signature(connection)
    with pytest.raises(ValueError, match="Unexpected schema state; no changes made"):
        upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert connection.scalar(select(SchemaVersion.version)) == 13


def test_direct_fleet_upgrade_still_stops_at_v13(execution_database):
    """The historical helper remains independent of the current initializer target."""
    from lotuspond.fleet.migration import migrate_fleet_v13

    engine = execution_database()
    with engine.connect() as connection:
        connection.exec_driver_sql(
            (Path(__file__).parents[1] / "fleet/fixtures/schema-v12.sql").read_text()
        )
    with engine.begin() as connection:
        seed(connection)
        before = rows(connection)
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        migrate_fleet_v13(connection)
        connection.execute(SchemaVersion.__table__.update().values(version=13))
    exact = execution_database(v13=True)
    with exact.connect() as connection:
        expected = signature(connection)
    with engine.connect() as connection:
        assert signature(connection) == expected
        assert {name: rows(connection)[name] for name in before} == before
        assert connection.scalar(select(SchemaVersion.version)) == 13


def test_database_configuration_refuses_missing_conflicting_or_remote(monkeypatch):
    from .conftest import execution_test_url

    dedicated = "LOTUSPOND_EXECUTION_TEST_DATABASE_URL"
    platform = "LOTUSPOND_PLATFORM_TEST_DATABASE_URL"
    local = "postgresql+psycopg://lotuspond@127.0.0.1:55439/lotuspond_platform_test"
    monkeypatch.delenv(dedicated, raising=False)
    monkeypatch.delenv(platform, raising=False)
    with pytest.raises(pytest.fail.Exception, match="Verified localhost PostgreSQL required"):
        execution_test_url()
    monkeypatch.setenv(dedicated, local)
    assert execution_test_url() == local
    monkeypatch.setenv(platform, local)
    assert execution_test_url() == local
    monkeypatch.setenv(platform, local.replace("55439", "5432"))
    with pytest.raises(ValueError, match="Conflicting"):
        execution_test_url()
    monkeypatch.delenv(platform)
    for invalid in (
        local.replace("127.0.0.1", "external.invalid"),
        local.replace("lotuspond_platform_test", "other_database"),
        local + "?host=external.invalid",
    ):
        monkeypatch.setenv(dedicated, invalid)
        with pytest.raises(ValueError, match="Isolated localhost"):
            execution_test_url()


def write_hooks(connection):
    """Snapshot definitions and enabled state, including normal internal FK triggers."""
    return {
        "triggers": list(
            connection.execute(
                text(
                    "SELECT c.relname, t.tgname, t.tgenabled, t.tgisinternal, "
                    "pg_get_triggerdef(t.oid), pg_get_functiondef(t.tgfoid) "
                    "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=current_schema() ORDER BY c.relname, t.tgname"
                )
            )
        ),
        "rules": list(
            connection.execute(
                text(
                    "SELECT c.relname, r.rulename, r.ev_enabled, pg_get_ruledef(r.oid) "
                    "FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=current_schema() ORDER BY c.relname, r.rulename"
                )
            )
        ),
    }


def install_ledger_hook(connection, behavior):
    if behavior == "rule":
        connection.execute(
            text("CREATE RULE unexpected_ledger AS ON UPDATE TO schema_version DO INSTEAD NOTHING")
        )
        return
    body = {
        "suppress": "RETURN NULL;",
        "mutate": "UPDATE users SET name='changed-by-trigger'; RETURN NEW;",
        "redirect": "NEW.version := 15; RETURN NEW;",
        "extra_row": "INSERT INTO schema_version VALUES (99, CURRENT_TIMESTAMP); RETURN NEW;",
    }[behavior]
    connection.execute(
        text(
            "CREATE FUNCTION unexpected_ledger() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN " + body + " END $$"
        )
    )
    connection.execute(
        text(
            "CREATE TRIGGER unexpected_ledger BEFORE UPDATE ON schema_version "
            "FOR EACH ROW EXECUTE FUNCTION unexpected_ledger()"
        )
    )


@pytest.mark.parametrize("behavior", ["suppress", "mutate", "rule"])
def test_unexpected_write_hooks_refused_before_mutation(execution_database, behavior):
    from lotuspond.execution.migration import LEGACY_ADDITIONS, migrate_execution_v14

    engine = execution_database(v13=True)
    with engine.begin() as connection:
        populated(connection)
        install_ledger_hook(connection, behavior)
        before, before_signature, before_hooks = (
            rows(connection),
            signature(connection),
            write_hooks(connection),
        )
    # Catch inside the transaction and commit: refusal must happen before any
    # mutation, not merely rely on a caller rolling back partially executed DDL.
    refused = False
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        try:
            migrate_execution_v14(connection)
        except ValueError as error:
            assert str(error) == "Unexpected schema state; no changes made"
            refused = True
    with engine.connect() as connection:
        after = rows(connection)
        print(
            {
                "hook": behavior,
                "refused": refused,
                "version": connection.scalar(select(SchemaVersion.version)),
                "legacy_rows_equal": {name: after[name] for name in before} == before,
                "execution_tables_present": bool(
                    set(inspect(connection).get_table_names()) & EXECUTION_TABLES
                ),
            }
        )
        assert refused
        assert after == before
        assert signature(connection) == before_signature
        assert write_hooks(connection) == before_hooks
        assert connection.scalar(select(SchemaVersion.version)) == 13
        assert not set(inspect(connection).get_table_names()) & EXECUTION_TABLES
        assert (
            not {
                constraint["name"]
                for table in ("api_keys", "runs", "releases")
                for constraint in inspect(connection).get_unique_constraints(table)
            }
            & LEGACY_ADDITIONS
        )


@pytest.mark.parametrize("behavior", ["suppress", "redirect", "extra_row"])
def test_final_ledger_postcondition_rolls_back_all_ddl(execution_database, monkeypatch, behavior):
    from lotuspond.execution import migration

    engine = execution_database(v13=True)
    with engine.begin() as connection:
        populated(connection)
        before, before_signature, before_hooks = (
            rows(connection),
            signature(connection),
            write_hooks(connection),
        )
    original = migration._preflight

    def inject_after_valid_preflight(connection):
        original(connection)
        # Fault injection after real preflight exercises defensive write/read
        # postconditions using PostgreSQL behavior, not mocked result objects.
        install_ledger_hook(connection, behavior)

    monkeypatch.setattr(migration, "_preflight", inject_after_valid_preflight)
    with pytest.raises(ValueError, match="Unexpected schema state; no changes made"):
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
            migration.migrate_execution_v14(connection)
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert write_hooks(connection) == before_hooks
        assert connection.scalar(select(SchemaVersion.version)) == 13
