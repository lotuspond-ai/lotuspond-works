import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from lotuspond import models
from lotuspond.models import Base, new_id

FLEET_MODELS = (
    "Connection",
    "ConnectionVersion",
    "SecretReference",
    "RuntimePool",
    "RuntimePoolPolicy",
    "ConnectionVersionSecret",
    "RuntimePoolPolicyConnection",
    "RuntimeInstance",
    "RuntimeEnrollment",
    "RuntimeCredential",
    "RuntimeSession",
    "RuntimeCommandAdmission",
    "FleetMutationRequest",
)


def records(n=0):
    ids = {
        name: new_id()
        for name in (
            "workspace",
            "project",
            "environment",
            "connection",
            "version",
            "secret",
            "pool",
            "policy",
            "instance",
            "enrollment",
            "credential",
            "session",
            "command",
            "mutation",
            "user",
        )
    }
    scope = {f"{key}_id": ids[key] for key in ("workspace", "project", "environment")}
    machine = dict(
        scope,
        installation_id=f"installation-{n}",
        recovery_epoch=n,
        pool_id=ids["pool"],
        instance_id=ids["instance"],
    )
    rows = {
        "users": dict(
            id=ids["user"], email=f"fleet-{n}@example.test", name="test", password_hash="synthetic"
        ),
        "workspaces": dict(
            id=ids["workspace"], name="test", slug=f"fleet-{n}", owner_id=ids["user"]
        ),
        "projects": dict(id=ids["project"], workspace_id=ids["workspace"], name="test"),
        "environments": dict(
            id=ids["environment"],
            workspace_id=ids["workspace"],
            project_id=ids["project"],
            name="test",
            kind="local",
        ),
        "connections": dict(scope, id=ids["connection"], name="test", status="active"),
        "secret_references": dict(
            scope, id=ids["secret"], binding_id="fixture", version=1, status="active"
        ),
        "connection_versions": dict(
            scope,
            id=ids["version"],
            connection_id=ids["connection"],
            version=1,
            canonical_config=b"{}",
            config_digest="a" * 64,
        ),
        "runtime_pools": dict(
            scope, id=ids["pool"], name="test", status="active", current_policy_version=1
        ),
        "runtime_pool_policies": dict(
            scope,
            id=ids["policy"],
            pool_id=ids["pool"],
            version=1,
            canonical_policy=b"{}",
            policy_digest="b" * 64,
        ),
        "connection_version_secrets": dict(
            scope,
            id=new_id(),
            connection_id=ids["connection"],
            connection_version=1,
            secret_reference_id=ids["secret"],
            secret_version=1,
        ),
        "runtime_pool_policy_connections": dict(
            scope,
            id=new_id(),
            pool_id=ids["pool"],
            policy_version=1,
            connection_id=ids["connection"],
        ),
        "runtime_instances": dict(
            machine, id=ids["instance"], status="active", generation=0, observation_ceiling=b"{}"
        ),
        "runtime_enrollments": dict(
            scope,
            id=ids["enrollment"],
            installation_id=machine["installation_id"],
            recovery_epoch=n,
            pool_id=ids["pool"],
            policy_version=1,
            token_digest=str(n) * 64,
            status="issued",
            expires_at_ms=600000,
            observation_ceiling=b"{}",
        ),
        "runtime_sessions": dict(
            machine,
            id=ids["session"],
            generation=1,
            token_digest=str(n + 2) * 64,
            status="active",
            expires_at_ms=60000,
        ),
        "runtime_credentials": dict(
            machine,
            id=ids["credential"],
            version=1,
            token_digest=str(n + 4) * 64,
            status="active",
            expires_at_ms=900000,
        ),
        "runtime_command_admissions": dict(
            machine,
            id=ids["command"],
            session_id=ids["session"],
            generation=1,
            command_id=f"command-{n}",
            replay_id=f"replay-{n}",
            recipient=ids["instance"],
            command_bytes=b"{}",
            signature_bytes=b"synthetic",
            command_digest="c" * 64,
            trust_revision=1,
            command_expires_at_ms=300000,
            status="admitted",
            admitted_at_ms=0,
        ),
        "fleet_mutation_requests": dict(
            scope,
            id=ids["mutation"],
            operation="connection.create",
            request_key="request",
            request_digest="d" * 64,
            result={"id": ids["connection"]},
        ),
    }
    if n:
        for row in rows.values():
            for key in (
                "version",
                "connection_version",
                "secret_version",
                "policy_version",
                "current_policy_version",
                "generation",
            ):
                if key in row and row[key] != 0:
                    row[key] = n + 1
    return rows


def load(connection, values):
    for name, row in values.items():
        values = {
            key: value
            for key, value in row.items()
            if key != "instance_id" or name != "runtime_instances"
        }
        if name == "runtime_pools":
            values["current_policy_version"] = None
        connection.execute(Base.metadata.tables[name].insert().values(**values))
        if name == "runtime_pool_policies":
            pool = Base.metadata.tables["runtime_pools"]
            connection.execute(
                pool.update()
                .where(pool.c.id == row["pool_id"])
                .values(current_policy_version=row["version"])
            )


def test_scoped_models_exist():
    assert all(hasattr(models, name) for name in FLEET_MODELS)


def test_full_scope_fk_matrix(engine):
    assert "connections" in Base.metadata.tables
    left, right = records(), records(1)
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
        load(connection, left)
        load(connection, right)
        checked = set()
        for name in list(left)[4:]:
            table = Base.metadata.tables[name]
            for fk in table.foreign_key_constraints:
                if len(fk.columns) < 2:
                    continue
                for element in fk.elements:
                    column = element.parent.name
                    if column not in right[name] or (name, column) in checked:
                        continue
                    checked.add((name, column))
                    # All other binding components remain pinned to the first tree.
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            table.update()
                            .where(table.c.id == left[name]["id"])
                            .values(**{column: right[name][column]})
                        )
        assert {
            "workspace_id",
            "project_id",
            "environment_id",
            "pool_id",
            "instance_id",
            "session_id",
            "secret_reference_id",
            "secret_version",
            "generation",
            "installation_id",
            "recovery_epoch",
        }.issubset({field for _, field in checked})


def test_pending_predecessor_session_and_partial_uniqueness(engine):
    assert "runtime_credentials" in Base.metadata.tables
    left, right = records(), records(1)
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
        load(connection, left)
        load(connection, right)
        credential = Base.metadata.tables["runtime_credentials"]
        pending = dict(
            left["runtime_credentials"],
            id=new_id(),
            version=2,
            status="pending",
            token_digest="e" * 64,
            predecessor_id=left["runtime_credentials"]["id"],
            session_id=left["runtime_sessions"]["id"],
            generation=1,
            prepared_policy_version=1,
            activation_deadline_ms=60000,
        )
        connection.execute(credential.insert().values(**pending))
        for changes in (
            {"predecessor_id": right["runtime_credentials"]["id"]},
            {"session_id": right["runtime_sessions"]["id"]},
            {"generation": 2},
            {"prepared_policy_version": 2},
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    credential.update().where(credential.c.id == pending["id"]).values(**changes)
                )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                credential.update()
                .where(credential.c.id == left["runtime_credentials"]["id"])
                .values(successor_id=right["runtime_credentials"]["id"])
            )
        for name, changes in [
            ("runtime_credentials", {"version": 3, "token_digest": "f" * 64}),
            ("runtime_sessions", {"generation": 2, "token_digest": "f" * 64}),
        ]:
            row = dict(left[name], id=new_id(), **changes)
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(Base.metadata.tables[name].insert().values(**row))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                credential.insert().values(
                    **dict(pending, id=new_id(), version=3, token_digest="f" * 64)
                )
            )
        assert (
            connection.scalar(
                select(Base.metadata.tables["runtime_instances"].c.generation).where(
                    Base.metadata.tables["runtime_instances"].c.id
                    == left["runtime_instances"]["id"]
                )
            )
            == 0
        )


def test_replay_domain_and_terminal_retention(engine):
    assert "runtime_command_admissions" in Base.metadata.tables
    left = records()
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
        load(connection, left)
        table = Base.metadata.tables["runtime_command_admissions"]
        for changes in ({"command_id": "other"}, {"replay_id": "other"}):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    table.insert().values(**dict(left[table.name], id=new_id(), **changes))
                )
        for retained in (86399999, 300001):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    table.update().values(
                        status="terminal", terminal_at_ms=0, retain_until_ms=retained
                    )
                )
        connection.execute(
            table.update().values(status="terminal", terminal_at_ms=0, retain_until_ms=86400000)
        )


def test_pool_current_policy_requires_same_scope_existing_revision(engine):
    left, right = records(), records(1)
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
        load(connection, left)
        load(connection, right)
        table = Base.metadata.tables["runtime_pools"]
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                table.update()
                .where(table.c.id == left[table.name]["id"])
                .values(current_policy_version=2)
            )


def test_replay_domain_spans_projects_and_environments(engine):
    left, right = records(), records(1)
    # A second environment in the same installation/epoch/workspace cannot
    # reuse command or replay IDs even though its full scope is different.
    for row in right.values():
        if "workspace_id" in row:
            row["workspace_id"] = left["workspaces"]["id"]
        if "installation_id" in row:
            row["installation_id"] = left["runtime_instances"]["installation_id"]
            row["recovery_epoch"] = 0
    del right["workspaces"]
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
        load(connection, left)
        load(connection, right)
        table = Base.metadata.tables["runtime_command_admissions"]
        for field in ("replay_id", "command_id"):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    table.update()
                    .where(table.c.id == right[table.name]["id"])
                    .values(**{field: left[table.name][field]})
                )


def test_pending_requires_all_preparation_bindings_and_millisecond_bounds(engine):
    left = records()
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
        load(connection, left)
        table = Base.metadata.tables["runtime_credentials"]
        pending = dict(
            left[table.name],
            id=new_id(),
            version=2,
            status="pending",
            token_digest="e" * 64,
            predecessor_id=left[table.name]["id"],
            session_id=left["runtime_sessions"]["id"],
            generation=1,
            prepared_policy_version=1,
            activation_deadline_ms=60000,
        )
        for field in (
            "predecessor_id",
            "session_id",
            "generation",
            "prepared_policy_version",
            "activation_deadline_ms",
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(table.insert().values(**dict(pending, **{field: None})))
        for name, field, value in (
            ("runtime_instances", "generation", -1),
            ("runtime_instances", "generation", 9007199254740992),
            ("runtime_sessions", "generation", 0),
            ("runtime_enrollments", "policy_version", 0),
            ("runtime_credentials", "version", 0),
            ("secret_references", "version", 0),
            ("connection_versions", "version", 0),
            ("runtime_pool_policies", "version", 0),
            ("runtime_credentials", "expires_at_ms", -1),
            ("runtime_sessions", "expires_at_ms", 9007199254740992),
            ("runtime_enrollments", "expires_at_ms", -1),
        ):
            target = Base.metadata.tables[name]
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    target.update().where(target.c.id == left[name]["id"]).values(**{field: value})
                )
        for name in list(left)[4:]:
            target = Base.metadata.tables[name]
            if "status" in target.c:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        target.update()
                        .where(target.c.id == left[name]["id"])
                        .values(status="unknown")
                    )
        admission = Base.metadata.tables["runtime_command_admissions"]
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                admission.update().values(
                    status="terminal",
                    terminal_at_ms=0,
                    command_expires_at_ms=100000000,
                    retain_until_ms=100029999,
                )
            )
        connection.execute(
            admission.update().values(
                status="terminal",
                terminal_at_ms=0,
                command_expires_at_ms=100000000,
                retain_until_ms=100030000,
            )
        )
