"""Exact committed-v14 preservation, the runnable marker backfill and atomic refusal.

Existing executions read recovery epoch 0, as rows from before any restore.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

from lotuspond import initialize
from lotuspond.config import Settings
from lotuspond.models import (
    EXECUTION_TABLES,
    V16_TABLES,
    V17_TABLES,
    Base,
    SchemaVersion,
    new_id,
)
from lotuspond.schema_version import SCHEMA_VERSION
from tests.fleet.test_migration import rows, signature
from tests.fleet.test_schema import load

from .conftest import execution_test_url
from .test_migration import install_ledger_hook, populated, write_hooks
from .test_schema import execution_rows

FIXTURES = Path(__file__).parent / "fixtures"
V15_INDEXES = {
    "execution_runnable_idx",
    "execution_restored_idx",
    "execution_wait_due_idx",
    "execution_lease_due_idx",
    "execution_delivery_due_idx",
}


def upgrade(engine, monkeypatch):
    monkeypatch.setattr(initialize, "engine_for", lambda _: engine)
    initialize.migrate(Settings(env="test", database_url=execution_test_url()))


def v14(execution_database):
    engine = execution_database()
    with engine.connect() as connection:
        connection.exec_driver_sql((FIXTURES / "schema-v14.sql").read_text())
    return engine


def insert(connection, table, **values):
    connection.execute(Base.metadata.tables[table].insert().values(**values))


def execution_cases(connection, base):
    """Executions in every relevant state/outbox combination; returns expected markers."""
    expected = {base["executions"]["id"]: 0}  # pending engine_start at available 0
    cases = [
        ("pending", [("engine_start", "pending", 700)], 700),
        ("waiting", [("engine_signal", "pending", 50), ("control", "pending", 40)], 40),
        ("reconciliation_required", [("control", "pending", 90)], 90),
        ("paused", [("control", "pending", 120), ("engine_start", "delivered", 5)], 120),
        ("running", [("engine_start", "delivered", 10)], None),
        ("running", [("activity", "pending", 10)], None),
        ("running", [("engine_start", "leased", 10)], None),
        ("running", [], None),
        ("succeeded", [("engine_start", "pending", 10)], None),
        ("failed", [("engine_signal", "pending", 10)], None),
        ("cancelled", [("control", "pending", 10)], None),
    ]
    scope = {k: base["executions"][k] for k in ("workspace_id", "project_id", "environment_id")}
    for sequence, (state, messages, marker) in enumerate(cases, start=2):
        execution = dict(
            base["executions"],
            id=new_id(),
            engine_identity=f"case-{sequence}",
            admission_sequence=sequence,
            state=state,
        )
        insert(connection, "executions", **execution)
        for kind, status, available in messages:
            lease = (
                dict(
                    lease_token_digest="a" * 64,
                    leased_at_ms=available,
                    lease_expires_at_ms=available + 1,
                )
                if status == "leased"
                else {}
            )
            insert(
                connection,
                "execution_outbox",
                **scope,
                id=new_id(),
                execution_id=execution["id"],
                message_id=new_id(),
                kind=kind,
                digest="e" * 64,
                status=status,
                available_at_ms=available,
                **lease,
            )
        expected[execution["id"]] = marker
    return expected


def without_marker(values):
    parsed = [json.loads(value) for value in values]
    return sorted(
        json.dumps(
            {k: v for k, v in row.items() if k not in ("runnable_at_ms", "recovery_epoch")},
            sort_keys=True,
        )
        for row in parsed
    )


def test_v14_upgrade_preserves_rows_and_marks_pending_work(execution_database, monkeypatch):
    engine = v14(execution_database)
    with engine.begin() as connection:
        populated(connection)
        base = execution_rows(3)
        load(connection, base)
        expected = execution_cases(connection, base)
        before_legacy_rows = rows(connection)
        installed_at = connection.scalar(select(SchemaVersion.installed_at))
    upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        after = rows(connection)
        after_legacy_rows = {
            name: after[name] for name in before_legacy_rows if name != "executions"
        }
        assert after_legacy_rows == {
            name: value for name, value in before_legacy_rows.items() if name != "executions"
        }
        assert without_marker(after["executions"]) == without_marker(
            before_legacy_rows["executions"]
        )
        markers = dict(connection.execute(text("SELECT id, runnable_at_ms FROM executions")).all())
        assert markers == expected
        epochs = connection.execute(text("SELECT DISTINCT recovery_epoch FROM executions"))
        assert list(epochs.scalars()) == [0]
        assert connection.scalar(select(SchemaVersion.installed_at)) == installed_at
        assert (
            connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        )  # the chain continues


def test_fresh_upgrade_signature_equal(execution_database, monkeypatch):
    fresh, migrated, exported = execution_database(), v14(execution_database), execution_database()
    deepcopy(Base.metadata).create_all(fresh)
    upgrade(migrated, monkeypatch)
    with exported.connect() as connection:
        connection.exec_driver_sql((Path(__file__).parents[2] / "schema.sql").read_text())
        exported_signature = signature(connection)
    with fresh.connect() as connection:
        fresh_signature = signature(connection)
    with migrated.connect() as connection:
        migrated_signature = signature(connection)
        columns = [c["name"] for c in inspect(connection).get_columns("executions")]
        indexes = {
            index["name"]: index
            for table in (
                "executions",
                "execution_waits",
                "execution_leases",
                "execution_activity_deliveries",
            )
            for index in inspect(connection).get_indexes(table)
        }
    assert fresh_signature == migrated_signature == exported_signature
    assert columns[-2:] == ["runnable_at_ms", "recovery_epoch"]
    assert V15_INDEXES <= set(indexes)
    predicates = {
        name: indexes[name]["dialect_options"]["postgresql_where"] for name in V15_INDEXES
    }
    assert "runnable_at_ms IS NOT NULL" in predicates["execution_runnable_idx"]
    assert "'succeeded'" in predicates["execution_restored_idx"]
    assert "'open'" in predicates["execution_wait_due_idx"]
    assert "'active'" in predicates["execution_lease_due_idx"]
    assert "'pending'" in predicates["execution_delivery_due_idx"]


def test_direct_v14_helper_still_creates_exact_v14(execution_database):
    """The historical helper stays independent of the current initializer target."""
    from lotuspond.execution.migration import migrate_execution_v14

    engine = execution_database(v13=True)
    with engine.begin() as connection:
        populated(connection)
        before = rows(connection)
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        migrate_execution_v14(connection)
    expected = v14(execution_database)
    with expected.connect() as connection:
        v14_signature = signature(connection)
    with engine.connect() as connection:
        assert signature(connection) == v14_signature
        assert {name: rows(connection)[name] for name in before} == before
        assert connection.scalar(select(SchemaVersion.version)) == 14
        assert "runnable_at_ms" not in {
            c["name"] for c in inspect(connection).get_columns("executions")
        }


@pytest.mark.parametrize(
    "corruption",
    [
        "partial_column",
        "partial_index",
        "partial_check",
        "extra_table",
        "missing_table",
        "wrong_version",
        "wrong_index",
        "wrong_constraint",
        "unvalidated",
        "disabled_trigger",
        "orphan",
        "wrong_type",
        "duplicate_unique",
        "user_trigger",
    ],
)
def test_partial_v14_refused_atomically(execution_database, monkeypatch, corruption):
    engine = v14(execution_database)
    with engine.begin() as connection:
        populated(connection)
        load(connection, execution_rows(3))
        if corruption == "partial_column":
            connection.execute(text("ALTER TABLE executions ADD COLUMN runnable_at_ms BIGINT"))
        elif corruption == "partial_index":
            connection.execute(
                text(
                    "CREATE INDEX execution_wait_due_idx ON execution_waits (deadline_ms, id) "
                    "WHERE state = 'open'"
                )
            )
        elif corruption == "partial_check":
            connection.execute(
                text(
                    "ALTER TABLE executions ADD CONSTRAINT executions_runnable_terminal_check "
                    "CHECK (state <> 'unknown')"
                )
            )
        elif corruption == "extra_table":
            connection.execute(text("CREATE TABLE execution_claims (id integer)"))
        elif corruption == "missing_table":
            connection.execute(text("DROP TABLE execution_reconciliation_observations"))
        elif corruption == "wrong_version":
            connection.execute(text("UPDATE schema_version SET version = 13"))
        elif corruption == "wrong_index":
            connection.execute(text("DROP INDEX execution_lease_expiry_idx"))
            connection.execute(
                text("CREATE INDEX execution_lease_expiry_idx ON execution_leases (status)")
            )
        elif corruption == "wrong_constraint":
            check = inspect(connection).get_check_constraints("executions")[0]["name"]
            connection.execute(
                text(
                    f'ALTER TABLE executions DROP CONSTRAINT "{check}", ADD CONSTRAINT "{check}" CHECK (true)'
                )
            )
        elif corruption == "unvalidated":
            connection.execute(
                text("ALTER TABLE executions ADD CONSTRAINT suspect CHECK (true) NOT VALID")
            )
        elif corruption == "disabled_trigger":
            connection.execute(text("ALTER TABLE execution_outbox DISABLE TRIGGER ALL"))
        elif corruption == "orphan":
            connection.execute(text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                text(
                    "UPDATE execution_outbox SET execution_id='00000000-0000-0000-0000-000000000000'"
                )
            )
        elif corruption == "wrong_type":
            connection.execute(text("ALTER TABLE executions ALTER COLUMN reason TYPE text"))
        elif corruption == "duplicate_unique":
            connection.execute(
                text(
                    "ALTER TABLE executions ADD CONSTRAINT redundant_unique UNIQUE (workspace_id, project_id, environment_id, id)"
                )
            )
        elif corruption == "user_trigger":
            install_ledger_hook(connection, "suppress")
        before, before_signature, before_hooks = (
            rows(connection),
            signature(connection),
            write_hooks(connection),
        )
        before_version = connection.scalar(select(SchemaVersion.version))
    with pytest.raises(ValueError, match="Unexpected schema state; no changes made"):
        upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert write_hooks(connection) == before_hooks
        assert connection.scalar(select(SchemaVersion.version)) == before_version


def test_caller_rollback_after_v15_returns(execution_database):
    from lotuspond.execution.migration import migrate_execution_v15

    engine = v14(execution_database)
    with engine.begin() as connection:
        populated(connection)
        load(connection, execution_rows(3))
        before, before_signature = rows(connection), signature(connection)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        migrate_execution_v15(connection)
        assert (
            connection.scalar(
                text("SELECT count(*) FROM executions WHERE runnable_at_ms IS NOT NULL")
            )
            == 1
        )
        assert connection.scalar(select(SchemaVersion.version)) == 15
        transaction.rollback()
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert connection.scalar(select(SchemaVersion.version)) == 14


def test_v15_requires_postgresql_caller_transaction(execution_database):
    from lotuspond.execution.migration import migrate_execution_v15

    engine = v14(execution_database)
    with engine.connect() as connection:
        assert not connection.in_transaction()
        with pytest.raises(ValueError, match="Unexpected schema state"):
            migrate_execution_v15(connection)
    with engine.connect() as connection:
        assert connection.scalar(select(SchemaVersion.version)) == 14


@pytest.mark.parametrize("behavior", ["suppress", "redirect", "extra_row"])
def test_final_v15_ledger_postcondition_rolls_back_all_ddl(
    execution_database, monkeypatch, behavior
):
    from lotuspond.execution import migration

    engine = v14(execution_database)
    with engine.begin() as connection:
        populated(connection)
        load(connection, execution_rows(3))
        before, before_signature, before_hooks = (
            rows(connection),
            signature(connection),
            write_hooks(connection),
        )
    original = migration._preflight_v15

    def inject_after_valid_preflight(connection):
        original(connection)
        if behavior == "redirect":
            # The shared helper redirects to 15, which is this step's own target.
            connection.execute(
                text(
                    "CREATE FUNCTION unexpected_ledger() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN NEW.version := 16; RETURN NEW; END $$"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER unexpected_ledger BEFORE UPDATE ON schema_version "
                    "FOR EACH ROW EXECUTE FUNCTION unexpected_ledger()"
                )
            )
        else:
            install_ledger_hook(connection, behavior)

    monkeypatch.setattr(migration, "_preflight_v15", inject_after_valid_preflight)
    with pytest.raises(ValueError, match="Unexpected schema state; no changes made"):
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
            migration.migrate_execution_v15(connection)
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert write_hooks(connection) == before_hooks
        assert connection.scalar(select(SchemaVersion.version)) == 14
        assert not set(inspect(connection).get_table_names()) - set(Base.metadata.tables)
        assert (
            set(inspect(connection).get_table_names()) >= EXECUTION_TABLES - V16_TABLES - V17_TABLES
        )
