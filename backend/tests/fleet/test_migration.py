import importlib.util
import os

import pytest
from sqlalchemy import inspect, select, text

from lotuspond import initialize
from lotuspond.config import Settings
from lotuspond.models import (
    Base,
    Environment,
    MfaFactor,
    Project,
    Run,
    Runner,
    Scenario,
    SchemaVersion,
    User,
    WorkflowPackageVersion,
    Workspace,
    new_id,
)


def upgrade(engine, monkeypatch):
    monkeypatch.setattr(initialize, "engine_for", lambda _: engine)
    initialize.migrate(
        Settings(env="test", database_url=os.environ["LOTUSPOND_PLATFORM_TEST_DATABASE_URL"])
    )


def seed(connection):
    user, workspace, project, environment, runner = [new_id() for _ in range(5)]
    connection.execute(
        User.__table__.insert().values(
            id=user, email="v12@example.test", name="v12", password_hash="synthetic"
        )
    )
    connection.execute(
        Workspace.__table__.insert().values(
            id=workspace, name="v12", slug="v12", kind="organization", plan="team", owner_id=user
        )
    )
    connection.execute(
        Project.__table__.insert().values(id=project, workspace_id=workspace, name="v12")
    )
    connection.execute(
        Environment.__table__.insert().values(
            id=environment,
            workspace_id=workspace,
            project_id=project,
            name="v12",
            kind="sandbox",
            configuration={"arbitrary": ["preserve", {"value": 42}]},
        )
    )
    connection.execute(
        Scenario.__table__.insert().values(
            workspace_id=workspace, project_id=project, name="v12", input={"old": True}, expected={}
        )
    )
    connection.execute(
        Runner.__table__.insert().values(id=runner, workspace_id=workspace, name="v12")
    )
    connection.execute(
        Run.__table__.insert().values(
            workspace_id=workspace,
            project_id=project,
            environment_id=environment,
            runner_id=runner,
            name="v12",
            revision="legacy",
            scenario_snapshot=[],
            status="queued",
        )
    )
    connection.execute(
        MfaFactor.__table__.insert().values(
            user_id=user,
            version=1,
            secret_ciphertext="synthetic-encrypted",
            enabled_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
    )
    connection.execute(
        WorkflowPackageVersion.__table__.insert().values(
            workspace_id=workspace,
            project_id=project,
            package_id="legacy",
            version="1.0.0",
            schema_version="1",
            validator_revision="legacy",
            canonicalization="jcs",
            sha256="b" * 64,
            canonical_content=b'{"old":true}',
            content_bytes=12,
            publisher_id=user,
            publisher_kind="user",
        )
    )


def rows(connection):
    return {
        table: list(
            connection.execute(
                text(f'SELECT row_to_json(t)::text FROM "{table}" t ORDER BY row_to_json(t)::text')
            ).scalars()
        )
        for table in inspect(connection).get_table_names()
        if table != "schema_version"
    }


def signature(connection):
    inspector = inspect(connection)
    return {
        table: {
            "columns": [
                (c["name"], str(c["type"]), c["nullable"], c["default"])
                for c in inspector.get_columns(table)
            ],
            "pk": inspector.get_pk_constraint(table)["constrained_columns"],
            "unique": sorted(
                tuple(c["column_names"]) for c in inspector.get_unique_constraints(table)
            ),
            "fk": sorted(
                (
                    tuple(c["constrained_columns"]),
                    c["referred_table"],
                    tuple(c["referred_columns"]),
                    tuple(sorted(c["options"].items())),
                )
                for c in inspector.get_foreign_keys(table)
            ),
            "checks": sorted(c["sqltext"] for c in inspector.get_check_constraints(table)),
            "indexes": sorted(
                (
                    i["name"],
                    tuple(i["column_names"]),
                    i["unique"],
                    str(i.get("dialect_options", {}).get("postgresql_where", "")),
                )
                for i in inspector.get_indexes(table)
            ),
        }
        for table in inspector.get_table_names()
    }


def test_migration_interface_exists():
    assert importlib.util.find_spec("lotuspond.fleet") is not None
    assert importlib.util.find_spec("lotuspond.fleet.migration") is not None


def test_upgrade_preserves_v12_rows(schema_database, monkeypatch):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)
        existing_rows_before = rows(connection)
    upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        existing_rows_after = {table: rows(connection)[table] for table in existing_rows_before}
        assert existing_rows_after == existing_rows_before
        assert connection.scalar(select(SchemaVersion.version)) == 13
        assert all(
            not values
            for table, values in rows(connection).items()
            if table not in existing_rows_before
        )


def test_fresh_upgrade_schema_parity(schema_database, monkeypatch):
    fresh, upgraded = schema_database(), schema_database(v12=True)
    Base.metadata.create_all(fresh)
    upgrade(upgraded, monkeypatch)
    with fresh.connect() as connection:
        fresh_schema_signature = signature(connection)
    with upgraded.connect() as connection:
        upgraded_schema_signature = signature(connection)
    assert fresh_schema_signature == upgraded_schema_signature


@pytest.mark.parametrize(
    "corruption",
    ["table", "constraint", "ledger", "missing_fk", "orphan", "wrong_type", "unvalidated"],
)
def test_partial_or_corrupt_v12_refused_atomically(schema_database, monkeypatch, corruption):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        if corruption == "table":
            connection.execute(text("CREATE TABLE runtime_instances (id integer)"))
        elif corruption == "constraint":
            connection.execute(
                text(
                    "ALTER TABLE environments ADD CONSTRAINT environments_fleet_scope_unique UNIQUE (workspace_id, project_id, id)"
                )
            )
        elif corruption == "ledger":
            connection.execute(text("UPDATE schema_version SET version=99"))
        elif corruption == "missing_fk":
            name = next(
                fk["name"]
                for fk in inspect(connection).get_foreign_keys("environments")
                if fk["referred_table"] == "projects"
            )
            connection.execute(text(f'ALTER TABLE environments DROP CONSTRAINT "{name}"'))
        elif corruption == "orphan":
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(
                Environment.__table__.insert().values(
                    workspace_id=new_id(), project_id=new_id(), name="orphan", kind="local"
                )
            )
        elif corruption == "wrong_type":
            connection.execute(
                text(
                    "ALTER TABLE environments ALTER COLUMN configuration TYPE text USING configuration::text"
                )
            )
        elif corruption == "unvalidated":
            connection.execute(
                text("ALTER TABLE environments ADD CONSTRAINT suspicious CHECK (true) NOT VALID")
            )
        before, before_signature = rows(connection), signature(connection)
        ledger = list(connection.execute(select(SchemaVersion.version)).scalars())
    with pytest.raises(ValueError) as error:
        upgrade(engine, monkeypatch)
    partial_upgrade_error = error.value
    assert str(partial_upgrade_error) == "Unexpected schema state; no changes made"
    with engine.connect() as connection:
        assert rows(connection) == before
        assert signature(connection) == before_signature
        assert list(connection.execute(select(SchemaVersion.version)).scalars()) == ledger


def test_exported_fresh_orm_and_upgrade_schema_parity(schema_database, monkeypatch):
    from pathlib import Path

    exported, fresh, upgraded = schema_database(), schema_database(), schema_database(v12=True)
    with exported.connect() as connection:
        connection.exec_driver_sql((Path(__file__).parents[2] / "schema.sql").read_text())
    Base.metadata.create_all(fresh)
    upgrade(upgraded, monkeypatch)
    with exported.connect() as connection:
        export_signature = signature(connection)
    with fresh.connect() as connection:
        orm_signature = signature(connection)
    with upgraded.connect() as connection:
        upgrade_signature = signature(connection)
    assert export_signature == orm_signature == upgrade_signature


def test_old_v12_application_readiness_refuses_v13(client, monkeypatch):
    import lotuspond.app as application

    monkeypatch.setattr(application, "SCHEMA_VERSION", 12)
    assert client.get("/api/v1/ready").status_code == 503


def test_same_name_weakened_check_refused_atomically(schema_database, monkeypatch):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE workspaces DROP CONSTRAINT workspaces_plan_check, ADD CONSTRAINT workspaces_plan_check CHECK (true)"
            )
        )
        before = signature(connection)
    with pytest.raises(ValueError, match="Unexpected schema state; no changes made"):
        upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        assert signature(connection) == before
        assert connection.scalar(select(SchemaVersion.version)) == 12


def test_changed_fk_options_refused_atomically(schema_database, monkeypatch):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        fk = next(
            fk
            for fk in inspect(connection).get_foreign_keys("environments")
            if fk["referred_table"] == "projects"
        )
        connection.execute(
            text(
                f'ALTER TABLE environments DROP CONSTRAINT "{fk["name"]}", ADD CONSTRAINT "{fk["name"]}" FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED'
            )
        )
        before = signature(connection)
    with pytest.raises(ValueError, match="Unexpected schema state; no changes made"):
        upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        assert signature(connection) == before
        assert connection.scalar(select(SchemaVersion.version)) == 12


def test_ddl_failure_rolls_back_parent_unique_tables_and_ledger(schema_database, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.exc import DBAPIError

    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)
        before_rows, before_signature = rows(connection), signature(connection)

    def fail_ddl(_table, connection, **_kw):
        connection.execute(text("SELECT 1 / 0"))

    table = Base.metadata.tables["runtime_pool_policies"]
    event.listen(table, "before_create", fail_ddl)
    try:
        with pytest.raises(DBAPIError):
            upgrade(engine, monkeypatch)
    finally:
        event.remove(table, "before_create", fail_ddl)
    with engine.connect() as connection:
        assert rows(connection) == before_rows
        assert signature(connection) == before_signature
        assert connection.scalar(select(SchemaVersion.version)) == 12


@pytest.mark.parametrize("corruption", ["table", "column"])
def test_missing_ledger_shape_refused_atomically(schema_database, monkeypatch, corruption):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)
        if corruption == "table":
            connection.execute(text("DROP TABLE schema_version"))
        else:
            connection.execute(text("ALTER TABLE schema_version DROP COLUMN version"))
        before_rows, before_signature = rows(connection), signature(connection)
    with pytest.raises(ValueError) as error:
        upgrade(engine, monkeypatch)
    assert str(error.value) == "Unexpected schema state; no changes made"
    with engine.connect() as connection:
        assert rows(connection) == before_rows
        assert signature(connection) == before_signature


def fk_trigger_catalog(connection):
    return list(
        connection.execute(
            text(
                "SELECT c.conname, child.relname, referenced.relname, t.tgname, target.relname, "
                "t.tgenabled, t.tgisinternal, t.tgtype, t.tgdeferrable, t.tginitdeferred, "
                "pg_get_triggerdef(t.oid) FROM pg_constraint c "
                "JOIN pg_class child ON child.oid=c.conrelid "
                "JOIN pg_class referenced ON referenced.oid=c.confrelid "
                "JOIN pg_namespace n ON n.oid=child.relnamespace "
                "JOIN pg_trigger t ON t.tgconstraint=c.oid "
                "JOIN pg_class target ON target.oid=t.tgrelid "
                "WHERE n.nspname=current_schema() AND c.contype='f' "
                "ORDER BY c.conname, child.relname, target.relname, t.tgname"
            )
        )
    )


def corrupt_snapshot(connection):
    return (
        rows(connection),
        signature(connection),
        fk_trigger_catalog(connection),
        list(
            connection.execute(text("SELECT row_to_json(s)::text FROM schema_version s")).scalars()
        ),
        list(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname=current_schema() ORDER BY indexname"
                )
            )
        ),
    )


def assert_corrupt_refusal(engine, monkeypatch, before):
    with pytest.raises(ValueError) as error:
        upgrade(engine, monkeypatch)
    assert str(error.value) == "Unexpected schema state; no changes made"
    with engine.connect() as connection:
        assert corrupt_snapshot(connection) == before
        from lotuspond.fleet.migration import FLEET_TABLES

        assert not FLEET_TABLES.intersection(inspect(connection).get_table_names())


@pytest.mark.parametrize("literal", ["personal::text", "personal::text''::numeric"])
def test_cast_looking_check_literal_refused_atomically(schema_database, monkeypatch, literal):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)  # Organization rows remain valid under the corrupt kind CHECK.
        check = next(
            c
            for c in inspect(connection).get_check_constraints("workspaces")
            if "'personal'" in c["sqltext"]
        )
        connection.execute(
            text(
                f"ALTER TABLE workspaces DROP CONSTRAINT \"{check['name']}\", ADD CONSTRAINT \"{check['name']}\" CHECK (kind IN ('{literal}','organization'))"
            )
        )
        before = corrupt_snapshot(connection)
    assert_corrupt_refusal(engine, monkeypatch, before)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("kind::text = 'personal::text'::text", "kind = 'personal::text'"),
        ("kind::text = 'personal''::text'::text", "kind = 'personal''::text'"),
        ("\"kind::text\"::text = 'value'::character varying", "\"kind::text\" = 'value'"),
        ("kind::text = E'personal\\'::text'::text", "kind = E'personal\\'::text'"),
        ("value::numeric >= 0::numeric", "value >= 0"),
        (
            "kind::text = ANY (ARRAY['personal'::character varying]::text[])",
            "kind = ANY (ARRAY['personal'])",
        ),
        ("value::textual = value::numeric_precision", "value::textual = value::numeric_precision"),
    ],
)
def test_check_normalization_preserves_quotes_and_only_accepted_casts(expression, expected):
    from lotuspond.fleet.migration import _normalize_check

    assert _normalize_check(expression) == expected


def environment_project_fk_triggers(connection, table):
    return list(
        connection.execute(
            text(
                "SELECT t.tgname FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint "
                "JOIN pg_class child ON child.oid=c.conrelid "
                "JOIN pg_class parent ON parent.oid=c.confrelid "
                "JOIN pg_namespace n ON n.oid=child.relnamespace "
                "JOIN pg_class target ON target.oid=t.tgrelid "
                "WHERE n.nspname=current_schema() AND c.contype='f' "
                "AND child.relname='environments' AND parent.relname='projects' "
                "AND target.relname=:table ORDER BY t.tgname"
            ),
            {"table": table},
        ).scalars()
    )


@pytest.mark.parametrize("table", ["environments", "projects"])
@pytest.mark.parametrize("mode", ["DISABLE", "ENABLE REPLICA"])
def test_disabled_fk_enforcement_refused_atomically(schema_database, monkeypatch, table, mode):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)
        triggers = environment_project_fk_triggers(connection, table)
        assert len(triggers) == 2
        # One disabled insert/update or delete/update trigger already leaves a gap.
        connection.execute(text(f'ALTER TABLE "{table}" {mode} TRIGGER "{triggers[0]}"'))
        before = corrupt_snapshot(connection)
    assert_corrupt_refusal(engine, monkeypatch, before)


@pytest.mark.parametrize("table", ["environments", "projects"])
def test_always_enabled_fk_triggers_preserve_normal_write_enforcement(
    schema_database, monkeypatch, table
):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)
        for trigger in environment_project_fk_triggers(connection, table):
            connection.execute(text(f'ALTER TABLE "{table}" ENABLE ALWAYS TRIGGER "{trigger}"'))
        before_rows, before_triggers = rows(connection), fk_trigger_catalog(connection)
    upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        assert {name: rows(connection)[name] for name in before_rows} == before_rows
        assert set(before_triggers).issubset(set(fk_trigger_catalog(connection)))
        assert connection.scalar(select(SchemaVersion.version)) == 13


@pytest.mark.parametrize(
    ("index", "definition"),
    [
        ("ix_sessions_expires_at", "(expires_at) WHERE false"),
        ("ix_sessions_user_id", "(user_id) WHERE false"),
        ("ix_sessions_user_id", "USING hash (user_id)"),
        ("ix_sessions_user_id", "(user_id DESC)"),
        ("ix_sessions_user_id", "(user_id) INCLUDE (expires_at)"),
        ("ix_sessions_user_id", "(user_id varchar_pattern_ops)"),
        ("ix_sessions_user_id", "(user_id) WITH (fillfactor=70)"),
    ],
)
def test_changed_index_definition_refused_atomically(
    schema_database, monkeypatch, index, definition
):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)
        connection.execute(text(f'DROP INDEX "{index}"'))
        connection.execute(text(f'CREATE INDEX "{index}" ON sessions {definition}'))
        before = corrupt_snapshot(connection)
    assert_corrupt_refusal(engine, monkeypatch, before)


def test_array_ledger_value_refused_atomically(schema_database, monkeypatch):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)
        connection.execute(
            text(
                "ALTER TABLE schema_version ALTER COLUMN version DROP DEFAULT, ALTER COLUMN version TYPE integer[] USING ARRAY[version]"
            )
        )
        before = corrupt_snapshot(connection)
    assert_corrupt_refusal(engine, monkeypatch, before)


@pytest.mark.parametrize(
    ("column", "definition"),
    [
        ("kind", "kind::text IN ('personal'::text,'organization'::text)"),
        (
            "monthly_budget_usd",
            "monthly_budget_usd IS NULL OR monthly_budget_usd >= CAST(0 AS NUMERIC)",
        ),
    ],
)
def test_accepted_check_cast_rendering_preserves_v12_rows(
    schema_database, monkeypatch, column, definition
):
    engine = schema_database(v12=True)
    with engine.begin() as connection:
        seed(connection)
        check = next(
            c
            for c in inspect(connection).get_check_constraints("workspaces")
            if c["sqltext"].startswith(column)
        )
        connection.execute(
            text(
                f'ALTER TABLE workspaces DROP CONSTRAINT "{check["name"]}", ADD CONSTRAINT "{check["name"]}" CHECK ({definition})'
            )
        )
        before = rows(connection)
    upgrade(engine, monkeypatch)
    with engine.connect() as connection:
        assert {name: rows(connection)[name] for name in before} == before
        assert connection.scalar(select(SchemaVersion.version)) == 13
