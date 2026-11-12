"""E05 Task 2: the v16 action, task and outcome records keep full scope and their bounds."""

import pytest
from sqlalchemy.exc import IntegrityError

from lotuspond.execution import action_contracts as ac
from lotuspond.models import (
    EXECUTION_CONTROL_CODES,
    EXECUTION_CONTROL_CODES_V15,
    EXECUTION_REASONS,
    EXECUTION_REASONS_V15,
    V16_TABLES,
    Base,
    new_id,
)
from tests.execution.test_schema import execution_rows
from tests.fleet.test_schema import load


@pytest.fixture
def engine(execution_database):
    engine = execution_database()
    Base.metadata.create_all(engine)
    return engine


def action_rows(base):
    """One row of every v16 record, linked to `base` (from `execution_rows`)."""
    scope = {k: base["executions"][k] for k in ("workspace_id", "project_id", "environment_id")}
    execution = base["executions"]["id"]
    step = base["execution_step_instances"]["id"]
    artifact = base["execution_artifacts"]
    admission = base["runtime_command_admissions"]
    ids = {k: new_id() for k in ("intent", "task", "decision", "command", "receipt", "observation")}
    intent = dict(
        scope,
        id=ids["intent"],
        execution_id=execution,
        step_instance_id=step,
        action_id=new_id(),
        binding_bytes=b"{}",
        intent_sha256="1" * 64,
        recovery_epoch=1,
        connection_id=base["connection_versions"]["connection_id"],
        connection_version=base["connection_versions"]["version"],
        target_artifact_id=artifact["id"],
        target_artifact_version=artifact["version"],
        target_sha256=artifact["sha256"],
        input_artifact_id=artifact["id"],
        input_artifact_version=artifact["version"],
        input_sha256=artifact["sha256"],
        approval_mode="human",
        independent_approval=True,
        requester_user_id=base["users"]["id"],
        issued_at_ms=1000,
        expires_at_ms=100000,
    )
    return {
        "execution_action_intents": intent,
        "execution_human_tasks": dict(
            scope,
            id=ids["task"],
            execution_id=execution,
            step_instance_id=step,
            wait_id=base["execution_waits"]["id"],
            kind="review",
            intent_id=ids["intent"],
            assignment="workspace_admins",
            escalation_assignment="workspace_owners",
            escalate_at_ms=5000,
            deadline_ms=10000,
        ),
        "execution_action_decisions": dict(
            scope,
            id=ids["decision"],
            execution_id=execution,
            task_id=ids["task"],
            intent_id=ids["intent"],
            intent_expires_at_ms=100000,
            decision_id="decision-1",
            decision_bytes=b"{}",
            decision_sha256="d" * 64,
            decision="allow",
            actor_user_id=base["users"]["id"],
            issued_at_ms=2000,
            expires_at_ms=100000,
        ),
        "execution_action_commands": dict(
            scope,
            id=ids["command"],
            execution_id=execution,
            attempt_id=base["execution_attempts"]["id"],
            intent_id=ids["intent"],
            decision_row_id=ids["decision"],
            installation_id=admission["installation_id"],
            recovery_epoch=admission["recovery_epoch"],
            admission_id=admission["id"],
            command_id=admission["command_id"],
            command_digest=admission["command_digest"],
        ),
        "execution_action_receipts": dict(
            scope,
            id=ids["receipt"],
            execution_id=execution,
            attempt_id=base["execution_attempts"]["id"],
            intent_id=ids["intent"],
            receipt_id="receipt-1",
            receipt_bytes=b"{}",
            receipt_sha256="e" * 64,
            status="accepted",
            source="live",
        ),
        "execution_action_observations": dict(
            scope,
            id=ids["observation"],
            execution_id=execution,
            intent_id=ids["intent"],
            attempt_id=base["execution_attempts"]["id"],
            observation_id="observation-1",
            payload_bytes=b"{}",
            payload_sha256="f" * 64,
            result="effect_present",
            qualified=False,
            rule_id="readback",
            rule_version="1",
            source="readback",
        ),
        "execution_action_outcomes": dict(
            scope,
            id=new_id(),
            execution_id=execution,
            intent_id=ids["intent"],
            receipt_row_id=ids["receipt"],
            outcome_bytes=b"{}",
            outcome_sha256="a" * 64,
            state="verified",
            rule_id="verify",
            rule_version="1",
            observed_at_ms=3000,
        ),
    }


def populated(n=0):
    base = execution_rows(n)
    return base, action_rows(base)


def insert(connection, name, row):
    connection.execute(Base.metadata.tables[name].insert().values(**row))


@pytest.mark.parametrize("boundary", ["workspace", "project", "environment"])
def test_action_full_scope_fk_matrix(engine, boundary):
    (left_base, left), (right_base, right) = populated(), populated(1)
    if boundary in {"project", "environment"}:
        for rows in (right_base, right):
            for row in rows.values():
                if "workspace_id" in row:
                    row["workspace_id"] = left_base["workspaces"]["id"]
        del right_base["workspaces"]
    if boundary == "environment":
        for rows in (right_base, right):
            for row in rows.values():
                if "project_id" in row:
                    row["project_id"] = left_base["projects"]["id"]
        del right_base["projects"]
    with engine.begin() as connection:
        load(connection, left_base)
        load(connection, left)  # positive control: every record inserts
        load(connection, right_base)
        load(connection, right)
        assert set(left) == V16_TABLES
        checked = set()
        for name, row in left.items():
            table = Base.metadata.tables[name]
            for fk in table.foreign_key_constraints:
                if len(fk.columns) < 2:
                    continue
                for element in fk.elements:
                    column = element.parent.name
                    if column in {"workspace_id", "project_id", "environment_id"} or (
                        row.get(column) == right[name].get(column) or (name, column) in checked
                    ):
                        continue
                    checked.add((name, column))
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            table.update()
                            .where(table.c.id == row["id"])
                            .values(**{column: right[name][column]})
                        )
        assert {
            ("execution_action_intents", "step_instance_id"),
            ("execution_action_intents", "connection_id"),
            ("execution_action_intents", "target_artifact_id"),
            ("execution_human_tasks", "wait_id"),
            ("execution_human_tasks", "intent_id"),
            ("execution_action_decisions", "task_id"),
            ("execution_action_decisions", "intent_id"),
            ("execution_action_commands", "attempt_id"),
            ("execution_action_commands", "admission_id"),
            ("execution_action_receipts", "intent_id"),
            ("execution_action_observations", "intent_id"),
            ("execution_action_outcomes", "receipt_row_id"),
        } <= checked


def refused(connection, name, row):
    with pytest.raises(IntegrityError), connection.begin_nested():
        insert(connection, name, row)


def test_intent_task_and_decision_uniqueness(engine):
    base, rows = populated()
    with engine.begin() as connection:
        load(connection, base)
        load(connection, rows)
        intent, task = rows["execution_action_intents"], rows["execution_human_tasks"]
        decision, command = rows["execution_action_decisions"], rows["execution_action_commands"]
        # A second intent for one step instance (with a fresh action and digest).
        refused(
            connection,
            "execution_action_intents",
            dict(intent, id=new_id(), action_id=new_id(), intent_sha256="9" * 64),
        )
        # A second decision for one task.
        refused(
            connection,
            "execution_action_decisions",
            dict(decision, id=new_id(), decision_id="decision-2"),
        )
        # A decision outliving its intent: by its own bound, or by misquoting the intent's.
        decisions = Base.metadata.tables["execution_action_decisions"]
        for change in (
            {"expires_at_ms": 100001},
            {"intent_expires_at_ms": 100001, "expires_at_ms": 100001},
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    decisions.update().where(decisions.c.id == decision["id"]).values(**change)
                )
        # A lease longer than 60,000 ms, then a valid one (positive control).
        lease = dict(
            lease_token_digest="b" * 64,
            lease_revision=1,
            claimed_at_ms=1000,
            renewed_at_ms=1000,
            status="claimed",
        )
        table = Base.metadata.tables["execution_action_commands"]
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                table.update()
                .where(table.c.id == command["id"])
                .values(**lease, lease_expires_at_ms=61001)
            )
        connection.execute(
            table.update()
            .where(table.c.id == command["id"])
            .values(**lease, lease_expires_at_ms=61000)
        )
        # A review task without an intent, and a claim with only some of its fields.
        tasks = Base.metadata.tables["execution_human_tasks"]
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                tasks.update().where(tasks.c.id == task["id"]).values(intent_id=None)
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                tasks.update().where(tasks.c.id == task["id"]).values(claim_expires_at_ms=9000)
            )
        connection.execute(
            tasks.update()
            .where(tasks.c.id == task["id"])
            .values(claim_user_id=base["users"]["id"], claim_expires_at_ms=9000, claim_revision=1)
        )
        # A qualified observation must be an absence.
        observations = Base.metadata.tables["execution_action_observations"]
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(observations.update().values(qualified=True))
        # A closed assignment vocabulary.
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(tasks.update().values(assignment="reviewers"))


def test_outcome_names_exactly_one_receipt_or_observation(engine):
    """Task 9: a receipt row, or the effect-present observation acting as one."""
    base, rows = populated()
    other_base, other = populated(1)
    outcome = rows["execution_action_outcomes"]
    observation = rows["execution_action_observations"]["id"]
    with engine.begin() as connection:
        load(connection, base)
        load(connection, rows)
        load(connection, other_base)
        load(connection, other)
        table = Base.metadata.tables["execution_action_outcomes"]
        where = table.c.id == outcome["id"]
        for values in (
            {"observation_row_id": observation},  # both
            {"receipt_row_id": None},  # neither
            # another execution's observation
            {
                "receipt_row_id": None,
                "observation_row_id": other["execution_action_observations"]["id"],
            },
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(table.update().where(where).values(**values))
        # Positive control: the observation alone.
        connection.execute(
            table.update().where(where).values(receipt_row_id=None, observation_row_id=observation)
        )


def test_widened_checks_are_supersets(engine):
    assert set(EXECUTION_REASONS_V15) < set(EXECUTION_REASONS)
    assert set(EXECUTION_CONTROL_CODES_V15) < set(EXECUTION_CONTROL_CODES)
    assert set(EXECUTION_REASONS) - set(EXECUTION_REASONS_V15) == set(ac.EXECUTION_REASONS)
    assert set(EXECUTION_CONTROL_CODES) - set(EXECUTION_CONTROL_CODES_V15) == set(ac.CONTROL_CODES)
    base = execution_rows()
    with engine.begin() as connection:
        load(connection, base)
        executions = Base.metadata.tables["executions"]
        accepted = []
        for reason in EXECUTION_REASONS:
            with connection.begin_nested():
                connection.execute(executions.update().values(reason=reason))
                accepted.append(reason)
        assert accepted == list(EXECUTION_REASONS)
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(executions.update().values(reason="review_pending"))
        events = Base.metadata.tables["execution_events"]
        connection.execute(events.update().values(family="execution_control", code="admitted"))
        for code in EXECUTION_CONTROL_CODES:
            with connection.begin_nested():
                connection.execute(events.update().values(code=code))
        for family, code in (
            ("execution_control", "task_reopened"),
            ("sdk_runtime_event", "task_opened"),
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(events.update().values(family=family, code=code))


def test_action_command_references_e03_admission(engine):
    (base, rows), (other_base, _) = populated(), populated(1)
    with engine.begin() as connection:
        load(connection, base)
        load(connection, rows)
        load(connection, other_base)
        command = rows["execution_action_commands"]
        table = Base.metadata.tables["execution_action_commands"]
        foreign = other_base["runtime_command_admissions"]
        for change in (
            {"admission_id": foreign["id"]},  # another workspace's admission
            {"command_id": foreign["command_id"]},  # another admission's identity
            {"recovery_epoch": command["recovery_epoch"] + 1},  # another installation epoch
            {"installation_id": "installation-other"},
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    table.update().where(table.c.id == command["id"]).values(**change)
                )
        # A second command row for the same admission.
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                table.insert().values(
                    **dict(command, id=new_id(), attempt_id=base["execution_attempts"]["id"])
                )
            )


def test_byte_and_integer_bounds(engine):
    base, rows = populated()
    with engine.begin() as connection:
        load(connection, base)
        load(connection, rows)
        for name, column in (
            ("execution_action_intents", "binding_bytes"),
            ("execution_action_decisions", "decision_bytes"),
            ("execution_action_receipts", "receipt_bytes"),
            ("execution_action_observations", "payload_bytes"),
            ("execution_action_outcomes", "outcome_bytes"),
        ):
            table = Base.metadata.tables[name]
            connection.execute(table.update().values(**{column: b"x" * 65536}))  # positive
            for value in (b"x" * 65537, b""):
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(table.update().values(**{column: value}))
        outcomes = Base.metadata.tables["execution_action_outcomes"]
        connection.execute(outcomes.update().values(observed_at_ms=9007199254740991))
        for value in (9007199254740992, -1):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(outcomes.update().values(observed_at_ms=value))


def test_v16_records_hold_no_free_text(engine):
    """Every text column is an ID, digest, closed code or bounded identifier."""
    for name in V16_TABLES:
        for column in Base.metadata.tables[name].columns:
            length = getattr(column.type, "length", None)
            if column.type.python_type is str:
                assert length is not None and length <= 128, (name, column.name)
