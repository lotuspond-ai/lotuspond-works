"""E05 Task 12: tenant isolation around tasks and actions (tier 1), TEN-A1 to TEN-A4."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionActionIntent, ExecutionHumanTask, Membership

from .conftest import wait_until

MISSING = "00000000-0000-4000-8000-000000000000"


def answer(person, method, path, body=None):
    response = person.client.request(
        method,
        person.prefix + path,
        json=body,
        headers={**person.headers, "idempotency-key": "isolation"},
    )
    return response.status_code, response.json() if response.content else None


def test_ten_a1_another_workspace_sees_only_not_found(reviewed):
    a = reviewed
    revision, record = a.deploy()
    execution_id = a.start(revision)
    task = a.task_of(execution_id)
    action_id = a.actions(execution_id)[0]["action_id"]
    other = a.operator.tenant(a.world.engine, label="Other")
    try:
        assert answer(other, "GET", "/human-tasks")[1]["items"] == []
        probes = [
            ("GET", "/human-tasks/{task}", None),
            ("GET", "/human-tasks/{task}/proposal", None),
            ("POST", "/human-tasks/{task}/claim", {}),
            (
                "POST",
                "/human-tasks/{task}/decision",
                {"decision_id": "x", "intent_sha256": "0" * 64, "decision": "allow"},
            ),
            ("GET", "/executions/{execution}/actions", None),
            ("GET", "/executions/{execution}/actions/{action}", None),
            (
                "POST",
                "/executions/{execution}/actions/{action}/resolution",
                {"resolution_id": "x", "kind": "readback", "evidence": None},
            ),
        ]
        for method, template, body in probes:
            known = template.format(task=task["id"], execution=execution_id, action=action_id)
            missing = template.format(task=MISSING, execution=MISSING, action=MISSING)
            # Indistinguishable from a record that does not exist.
            assert answer(other, method, known, body) == answer(other, method, missing, body)
            assert answer(other, method, known, body)[0] == 404, known
    finally:
        other.close()
    assert a.writes(record) == [] and a.task(task["id"])["claim"] == "none"


def test_ten_a2_a_reviewer_in_two_workspaces_sees_each_scope_alone(reviewed):
    a = reviewed
    revision, _ = a.deploy()
    mine = a.start(revision)
    a.task_of(mine)
    person = a.reviewer()
    other = a.operator.tenant(a.world.engine, label="Second")
    try:
        with Session(a.world.engine) as db, db.begin():
            db.add(Membership(workspace_id=other.workspace, user_id=person.user_id, role="admin"))
        there = type(person)(
            a.operator.base_url,
            a.processes.ca,
            a.world.config["origin"],
            person.headers["cookie"],
            other.workspace,
            other.prefix,
        )
        try:
            assert [t["execution_id"] for t in a.tasks(person)] == [mine]
            assert a.tasks(there) == []
        finally:
            there.close()
    finally:
        other.close()


def test_ten_a3_another_pool_s_runtime_learns_nothing_of_a_command(reviewed):
    from lotuspond_runtime.errors import FleetError

    a = reviewed
    hand = a.hand("hand-own")
    other = a.operator.tenant(a.world.engine, label="Foreign")
    foreign = a.operator.hand(other, "hand-foreign")
    try:
        revision, record = a.deploy()
        execution_id = a.start(revision)
        a.approve(execution_id)
        delivery = wait_until(hand.action.poll, 60)
        assert foreign.action.poll() is None

        def code(call):
            with pytest.raises(FleetError) as refused:
                call()
            return refused.value.code

        known = code(lambda: foreign.action.claim(delivery))
        assert known in ("not_found", "forbidden", "unauthenticated", "conflict")
        assert code(lambda: foreign.action.records(delivery)) == known
        assert a.commands(execution_id)[0].claimed_at_ms is None
        assert a.writes(record) == []
    finally:
        hand.close()
        foreign.close()
        other.close()


def test_ten_a4_rows_naming_another_scope_are_rejected(reviewed):
    a = reviewed
    revision, _ = a.deploy()
    execution_id = a.start(revision)
    a.task_of(execution_id)
    other = a.operator.tenant(a.world.engine, label="Rows")
    try:
        with Session(a.world.engine) as db:
            task = db.scalar(
                select(ExecutionHumanTask).where(ExecutionHumanTask.execution_id == execution_id)
            )
            intent = db.scalar(
                select(ExecutionActionIntent).where(
                    ExecutionActionIntent.execution_id == execution_id
                )
            )
            fields = {c.key: getattr(task, c.key) for c in ExecutionHumanTask.__table__.columns}
        fields.update(
            id=MISSING,
            workspace_id=other.workspace,
            **{
                "project_id": other.scope["project_id"],
                "environment_id": other.scope["environment_id"],
            },
        )
        with Session(a.world.engine) as db, pytest.raises(IntegrityError):
            with db.begin():
                db.add(ExecutionHumanTask(**fields))
        assert intent.execution_id == execution_id  # the original rows are untouched
    finally:
        other.close()
