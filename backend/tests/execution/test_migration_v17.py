"""E06, second part: exact committed-v16 preservation, schema parity and atomic refusal."""

from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

from lotuspond import initialize
from lotuspond.config import Settings
from lotuspond.models import V17_TABLES, Base, SchemaVersion
from lotuspond.schema_version import SCHEMA_VERSION
from tests.fleet.test_migration import rows, signature
from tests.fleet.test_schema import load

from .conftest import execution_test_url
from .test_migration import install_ledger_hook, populated, write_hooks
from .test_schema import execution_rows

FIXTURES = Path(__file__).parent / "fixtures"


def upgrade(engine, monkeypatch):
    monkeypatch.setattr(initialize, "engine_for", lambda _: engine)
    initialize.migrate(Settings(env="test", database_url=execution_test_url()))


def v16(execution_database):
    engine = execution_database()
    with engine.connect() as connection:
        connection.exec_driver_sql((FIXTURES / "schema-v16.sql").read_text())
    return engine


def test_the_committed_v16_fixture_is_the_v16_export():
    """The fixture is main's v16 `schema.sql` byte for byte, apart from nothing."""
    fixture = (FIXTURES / "schema-v16.sql").read_text()
    assert "INSERT INTO schema_version (version, installed_at) VALUES (16," in fixture
    assert not any(f"CREATE TABLE {name} " in fixture for name in V17_TABLES)


def test_v16_upgrade_preserves_rows(execution_database, monkeypatch):
    engine = v16(execution_database)
    with engine.begin() as connection:
        populated(connection)
        load(connection, execution_rows(3))
        before = rows(connection)
        installed_at = connection.scalar(select(SchemaVersion.installed_at))
    upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        after = rows(connection)
        assert {name: after[name] for name in before} == before
        assert V17_TABLES <= set(inspect(connection).get_table_names())
        assert not any(after.get(name) for name in V17_TABLES)
        assert connection.scalar(select(SchemaVersion.installed_at)) == installed_at
        assert connection.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION == 17


def test_fresh_upgrade_signature_equal(execution_database, monkeypatch):
    fresh, migrated, exported = (
        execution_database(),
        v16(execution_database),
        execution_database(),
    )
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
        "partial_table",
        "wrong_version",
        "missing_table",
        "loosened_v16_check",
        "missing_v16_index",
        "user_trigger",
    ],
)
def test_partial_v16_refused_atomically(execution_database, monkeypatch, corruption):
    engine = v16(execution_database)
    with engine.begin() as connection:
        populated(connection)
        load(connection, execution_rows(3))
        if corruption == "partial_table":
            Base.metadata.tables["execution_cases"].create(connection)
        elif corruption == "wrong_version":
            connection.execute(text("UPDATE schema_version SET version = 15"))
        elif corruption == "missing_table":
            connection.execute(text("DROP TABLE execution_action_outcomes"))
        elif corruption == "loosened_v16_check":
            # A v16 table's check replaced by a weaker one under the same name.
            (name,) = [
                c["name"]
                for c in inspect(connection).get_check_constraints("execution_action_outcomes")
                if "verified" in c["sqltext"]
            ]
            connection.execute(
                text(
                    f"ALTER TABLE execution_action_outcomes DROP CONSTRAINT {name}, "
                    f"ADD CONSTRAINT {name} CHECK (true)"
                )
            )
        elif corruption == "missing_v16_index":
            connection.execute(text("DROP INDEX execution_task_due_idx"))
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


def test_caller_rollback_after_v17_returns(execution_database):
    from lotuspond.execution.migration import migrate_execution_v17

    engine = v16(execution_database)
    with engine.begin() as connection:
        populated(connection)
        load(connection, execution_rows(3))
        before, before_signature = rows(connection), signature(connection)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        migrate_execution_v17(connection)
        assert connection.scalar(select(SchemaVersion.version)) == 17
        transaction.rollback()
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert connection.scalar(select(SchemaVersion.version)) == 16


def test_direct_v16_helper_still_creates_exact_v16(execution_database):
    """Upgrading exact v15 with only the v16 step gives the committed v16 schema."""
    from lotuspond.execution.migration import migrate_execution_v16

    engine = execution_database()
    with engine.connect() as connection:
        connection.exec_driver_sql((FIXTURES / "schema-v15.sql").read_text())
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        migrate_execution_v16(connection)
    expected = v16(execution_database)
    with expected.connect() as connection:
        v16_signature = signature(connection)
    with engine.connect() as connection:
        assert signature(connection) == v16_signature
        assert not V17_TABLES & set(inspect(connection).get_table_names())
