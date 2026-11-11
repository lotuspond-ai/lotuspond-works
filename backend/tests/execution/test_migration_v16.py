"""E05 Task 2: exact committed-v15 preservation, schema parity and atomic refusal."""

from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

from lotuspond import initialize
from lotuspond.config import Settings
from lotuspond.models import (
    EXECUTION_CONTROL_CODES_V15,
    EXECUTION_REASONS_V15,
    V16_TABLES,
    Base,
    SchemaVersion,
    new_id,
)
from tests.fleet.test_migration import rows, signature
from tests.fleet.test_schema import load

from .conftest import execution_test_url
from .test_migration import install_ledger_hook, populated, write_hooks
from .test_schema import execution_rows

FIXTURES = Path(__file__).parent / "fixtures"
STATES = (
    "pending",
    "running",
    "waiting",
    "paused",
    "cancelled",
    "succeeded",
    "failed",
    "reconciliation_required",
)


def upgrade(engine, monkeypatch):
    monkeypatch.setattr(initialize, "engine_for", lambda _: engine)
    initialize.migrate(Settings(env="test", database_url=execution_test_url()))


def v15(execution_database):
    engine = execution_database()
    with engine.connect() as connection:
        connection.exec_driver_sql((FIXTURES / "schema-v15.sql").read_text())
    return engine


def v15_cases(connection, base):
    """One execution per v15 state and per v15 reason, and one event per v15 control code."""
    executions = Base.metadata.tables["executions"]
    events = Base.metadata.tables["execution_events"]
    cases = [(state, None) for state in STATES] + [
        ("failed", reason) for reason in EXECUTION_REASONS_V15
    ]
    for sequence, (state, reason) in enumerate(cases, start=2):
        connection.execute(
            executions.insert().values(
                **dict(
                    base["executions"],
                    id=new_id(),
                    engine_identity=f"v15-{sequence}",
                    admission_sequence=sequence,
                    state=state,
                    reason=reason,
                    runnable_at_ms=None if state in {"succeeded", "failed", "cancelled"} else 7,
                    recovery_epoch=sequence % 3,
                )
            )
        )
    codes = [("sdk_runtime_event", "step_started")] + [
        ("execution_control", code) for code in EXECUTION_CONTROL_CODES_V15
    ]
    for sequence, (family, code) in enumerate(codes, start=2):
        connection.execute(
            events.insert().values(
                **dict(
                    base["execution_events"],
                    id=new_id(),
                    sequence=sequence,
                    family=family,
                    code=code,
                    event_id=f"event-{sequence}",
                    source_sequence=sequence,
                )
            )
        )


def test_v15_upgrade_preserves_rows(execution_database, monkeypatch):
    engine = v15(execution_database)
    with engine.begin() as connection:
        populated(connection)
        base = execution_rows(3)
        load(connection, base)
        v15_cases(connection, base)
        before_legacy_rows = rows(connection)
        installed_at = connection.scalar(select(SchemaVersion.installed_at))
    upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        after = rows(connection)
        after_legacy_rows = {name: after[name] for name in before_legacy_rows}
        assert after_legacy_rows == before_legacy_rows
        assert {name for name in after if after[name]} & V16_TABLES == set()
        assert V16_TABLES <= set(inspect(connection).get_table_names())
        states = set(connection.execute(text("SELECT state FROM executions")).scalars())
        assert set(STATES) <= states
        reasons = set(connection.execute(text("SELECT reason FROM executions")).scalars())
        assert set(EXECUTION_REASONS_V15) <= reasons
        codes = connection.execute(
            text("SELECT code FROM execution_events WHERE family = 'execution_control'")
        )
        assert set(EXECUTION_CONTROL_CODES_V15) <= set(codes.scalars())
        assert connection.scalar(select(SchemaVersion.installed_at)) == installed_at
        assert connection.scalar(select(SchemaVersion.version)) == 16


def test_fresh_upgrade_signature_equal(execution_database, monkeypatch):
    fresh, migrated, exported = (
        execution_database(),
        v15(execution_database),
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
        indexes = {
            index["name"]
            for table in V16_TABLES
            for index in inspect(connection).get_indexes(table)
        }
    assert fresh_signature == migrated_signature == exported_signature
    assert {
        "execution_task_due_idx",
        "execution_task_escalation_idx",
        "execution_task_claim_idx",
        "execution_action_lease_due_idx",
        "execution_action_hold_idx",
    } <= indexes


@pytest.mark.parametrize(
    "corruption",
    [
        "partial_table",
        "partial_index",
        "partial_check",
        "wrong_version",
        "missing_table",
        "wrong_reason_check",
        "missing_v15_index",
        "user_trigger",
    ],
)
def test_partial_v15_refused_atomically(execution_database, monkeypatch, corruption):
    engine = v15(execution_database)
    with engine.begin() as connection:
        populated(connection)
        load(connection, execution_rows(3))
        if corruption == "partial_table":
            Base.metadata.tables["execution_action_intents"].create(connection)
        elif corruption == "partial_index":
            connection.execute(
                text("CREATE INDEX execution_task_due_idx ON execution_waits (deadline_ms, id)")
            )
        elif corruption == "partial_check":
            connection.execute(
                text(
                    "ALTER TABLE executions DROP CONSTRAINT executions_reason_check, "
                    "ADD CONSTRAINT executions_reason_check CHECK (reason IN ("
                    + ",".join(f"'{r}'" for r in (*EXECUTION_REASONS_V15, "review"))
                    + "))"
                )
            )
        elif corruption == "wrong_version":
            connection.execute(text("UPDATE schema_version SET version = 14"))
        elif corruption == "missing_table":
            connection.execute(text("DROP TABLE execution_reconciliation_observations"))
        elif corruption == "wrong_reason_check":
            connection.execute(
                text(
                    "ALTER TABLE executions DROP CONSTRAINT executions_reason_check, "
                    "ADD CONSTRAINT executions_reason_check CHECK (true)"
                )
            )
        elif corruption == "missing_v15_index":
            connection.execute(text("DROP INDEX execution_wait_due_idx"))
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
        if corruption != "wrong_version":
            assert before_version == 15


def test_caller_rollback_after_v16_returns(execution_database):
    from lotuspond.execution.migration import migrate_execution_v16

    engine = v15(execution_database)
    with engine.begin() as connection:
        populated(connection)
        load(connection, execution_rows(3))
        before, before_signature = rows(connection), signature(connection)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        migrate_execution_v16(connection)
        assert connection.scalar(select(SchemaVersion.version)) == 16
        transaction.rollback()
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert connection.scalar(select(SchemaVersion.version)) == 15


def test_direct_v15_helper_still_creates_exact_v15(execution_database, monkeypatch):
    """Upgrading exact v14 with only the v15 step gives the committed v15 schema."""
    from lotuspond.execution.migration import migrate_execution_v15

    engine = execution_database()
    with engine.connect() as connection:
        connection.exec_driver_sql((FIXTURES / "schema-v14.sql").read_text())
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(736183)"))
        migrate_execution_v15(connection)
    expected = v15(execution_database)
    with expected.connect() as connection:
        v15_signature = signature(connection)
    with engine.connect() as connection:
        assert signature(connection) == v15_signature
        assert not V16_TABLES & set(inspect(connection).get_table_names())
