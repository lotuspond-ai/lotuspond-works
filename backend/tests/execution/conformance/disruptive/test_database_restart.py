"""Task 13 tier 2: an immediate PostgreSQL restart (spike 1–2, REG-4) and ENG-4's grants."""

import re
import time
from pathlib import Path
from uuid import uuid4

from driver import form_package
from processes import Processes
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from lotuspond.models import (
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionUsageReservation,
    ExecutionWait,
)


def wait_row(world, wait_id):
    with Session(world.engine) as db:
        row = db.get(ExecutionWait, wait_id)
        return (row.id, row.wait_key, row.deadline_ms, row.state, row.step_instance_id)


def reserved(world, execution_id):
    """Usage reservations of this execution's attempts still `reserved`."""
    with Session(world.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(ExecutionUsageReservation)
            .join(ExecutionAttempt, ExecutionAttempt.id == ExecutionUsageReservation.attempt_id)
            .where(
                ExecutionAttempt.execution_id == execution_id,
                ExecutionUsageReservation.state == "reserved",
            )
        )


def until_reserved(world, execution_id, seconds=60):
    deadline = time.monotonic() + seconds
    while not reserved(world, execution_id):
        if time.monotonic() > deadline:
            raise AssertionError("the attempt was never reserved")
        time.sleep(0.5)


def test_a_form_wait_survives_an_immediate_restart(system):
    """Spike 2: `pg_ctl -m immediate` during a form wait; the bound wait is intact."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(
        customer, "form", package=form_package(), dependencies=["waits.policy"]
    )
    execution_id = operator.start(customer, revision)["id"]
    wait = operator.open_wait(customer, execution_id, "review")
    before = wait_row(w, wait["id"])
    w.cluster.immediate_restart()
    assert wait_row(w, wait["id"]) == before
    # Every process reconnects on its own; the signal completes the execution.
    operator.signal(customer, execution_id, wait["id"], "form", {"approved": True})
    try:
        view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 120)
    except AssertionError as stalled:
        # Seen once in Task 14's final runs and not reproduced since: keep the rows
        # that would show whether the wake-up was lost, and what held the tenant.
        raise AssertionError(f"{stalled}; {stall_evidence(w, execution_id)}") from None
    assert view["state"] == "succeeded", view


def stall_evidence(world, execution_id):
    """The execution's marker, wake-ups, signals and wait, and what held locks then."""
    queries = {
        "execution": "SELECT state, requested_state, reason, runnable_at_ms, recovery_epoch,"
        " floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint AS now_ms"
        " FROM executions WHERE id = :id",
        "outbox": "SELECT kind, status, available_at_ms FROM execution_outbox"
        " WHERE execution_id = :id ORDER BY available_at_ms",
        "inbox": "SELECT status, available_at_ms, expires_at_ms FROM execution_inbox"
        " WHERE execution_id = :id",
        "waits": "SELECT state, deadline_ms FROM execution_waits WHERE execution_id = :id",
        "sessions": "SELECT application_name, state, wait_event_type, wait_event,"
        " round(extract(epoch FROM clock_timestamp() - xact_start)) AS xact_s"
        " FROM pg_stat_activity WHERE datname = current_database()"
        " AND pid <> pg_backend_pid() AND state <> 'idle'",
        "locks": "SELECT c.relname, l.mode, l.granted, a.application_name, a.state"
        " FROM pg_locks l JOIN pg_class c ON c.oid = l.relation"
        " JOIN pg_stat_activity a ON a.pid = l.pid"
        " WHERE c.relname IN ('workspaces', 'executions') AND a.pid <> pg_backend_pid()",
    }
    found = {}
    with Session(world.engine) as db:
        for name, query in queries.items():
            found[name] = [tuple(row) for row in db.execute(text(query), {"id": execution_id})]
    return found


def test_a_timer_fires_once_at_its_deadline_across_an_immediate_restart(system):
    """REG-4 (tier 2)."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "waits_fast", dependencies=["waits.policy"])
    execution_id = operator.start(customer, revision)["id"]
    timer = operator.open_wait(customer, execution_id, "pause")
    w.cluster.immediate_restart()
    callback = operator.open_wait(customer, execution_id, "callback", seconds=120)
    with Session(w.engine) as db:
        row = db.get(ExecutionWait, timer["id"])
        others = db.scalars(
            select(ExecutionWait.id).where(
                ExecutionWait.execution_id == execution_id, ExecutionWait.id != timer["id"]
            )
        ).all()
        opened_ms = db.scalar(
            select(func.floor(func.extract("epoch", ExecutionWait.created_at) * 1000)).where(
                ExecutionWait.id == callback["id"]
            )
        )
    assert row.state == "satisfied" and row.deadline_ms == timer["deadline_ms"]
    assert list(others) == [callback["id"]]
    assert opened_ms >= timer["deadline_ms"]


def documented_grants(role, name, schema):
    """The operations guide's grant block for one worker role, for this schema."""
    guide = Path(__file__).resolve().parents[5] / "docs/operations/durable-execution.md"
    blocks = re.findall(r"```sql\n(-- The (\w+) role:.*?)```", guide.read_text(), re.S)
    (sql,) = [block for block, which in blocks if which == role]
    return f'SET search_path TO "{schema}";' + sql.replace(
        f"lotuspond_{role}", f'"{name}"'
    ).replace("ON SCHEMA lotuspond", f'ON SCHEMA "{schema}"')


def test_the_orchestrator_role_cannot_change_events(system, linux):
    """ENG-4: the operations guide's least-privilege worker grants, proven.

    Uniquely named orchestrator and sweeper roles, on the disposable cluster only
    (owner decision, 2026-10-26), get exactly the guide's grants. Both workers then
    run as them (positive control: a timer flow and an activity complete), and
    PostgreSQL itself refuses changing or deleting events, and deleting anything.
    They also run the paths tier 1 never ran under its privilege recording (the
    whole-branch review, 27 October 2026): an API key's execution, and a pause and
    a cancellation of attempts reserved but never claimed.
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    schema = w.config["schema"]
    names = {
        role: f"lp_conformance_{role}_{uuid4().hex[:12]}" for role in ("orchestrator", "sweeper")
    }
    for role, name in names.items():
        w.cluster.psql(f'CREATE ROLE "{name}" LOGIN;' + documented_grants(role, name, schema))
    try:
        processes.kill("orchestrator-a")
        processes.kill("sweeper-a")
        processes.start_worker("orchestrator", "orchestrator-role", user=names["orchestrator"])
        processes.start_worker("sweeper", "sweeper-role", user=names["sweeper"])
        timed = operator.deploy(customer, "waits_fast", dependencies=["waits.policy"])
        read = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
        runtime = operator.runtime(customer, "runtime-a")
        waiting = operator.start(customer, timed)["id"]
        operator.open_wait(customer, waiting, "callback", seconds=60)  # past its timer
        reading = operator.start(customer, read)["id"]
        state = operator.settled(customer, reading, {"succeeded", "failed"}, 90)["state"]
        assert state == "succeeded"
        assert Processes.calls(runtime["counter"]) == 1
        # An API key initiates: scheduling and progress authorize it under lock.
        slow = operator.deploy(customer, "activity_slow", dependencies=[operator.documents[0]])
        issued = customer.call(
            "POST", "api/v1/api-keys", {"name": "ENG-4", "scopes": ["execute", "read"]}
        )
        keyed = customer.client.post(
            customer.prefix + "/executions",
            json={"deployment_revision_id": slow, "input": None},
            headers={"authorization": f"Bearer {issued['key']}", "idempotency-key": uuid4().hex},
        )
        assert keyed.status_code == 201, keyed.status_code
        state = operator.settled(customer, keyed.json()["id"], {"succeeded", "failed"}, 150)
        assert state["state"] == "succeeded"
        # Nothing claims from here: a pause and a cancellation meet reserved attempts,
        # and the capacity step confirms their stop and frees their reservations.
        processes.stop("runtime-a")
        paused, cancelled = (operator.start(customer, slow)["id"] for _ in range(2))
        for execution_id in (paused, cancelled):
            until_reserved(w, execution_id)
        operator.control(customer, paused, "pause")
        operator.control(customer, cancelled, "cancel")
        assert operator.settled(customer, cancelled, {"cancelled"}, 90)["state"] == "cancelled"
        assert operator.settled(customer, paused, {"paused"}, 90)["state"] == "paused"
        deadline = time.monotonic() + 60
        while reserved(w, paused) or reserved(w, cancelled):
            assert time.monotonic() < deadline, "a stopped attempt kept its reservation"
            time.sleep(0.5)
        # A fresh runtime serves the resumed read: a restarted one would first wait out
        # its old session lease, longer than the read's dispatch retries (finding 18).
        fresh = operator.runtime(customer, "runtime-b")
        operator.control(customer, paused, "resume")
        view = operator.settled(customer, paused, {"succeeded", "failed"}, 150)
        assert view["state"] == "succeeded", (view, operator.events(customer, paused))
        assert Processes.calls(runtime["counter"]) == 2
        assert Processes.calls(fresh["counter"]) == 1
        restricted = create_engine(
            w.cluster.url.replace("lotuspond@", f"{names['orchestrator']}@"),
            connect_args={"options": f"-c search_path={schema}"},
            hide_parameters=True,
        )
        try:
            with Session(restricted) as db:
                assert db.scalar(select(func.count()).select_from(ExecutionEvent)) > 0
            for statement in (
                "UPDATE execution_events SET code = code",
                "DELETE FROM execution_events",
                "DELETE FROM executions",
            ):
                with Session(restricted) as db:
                    try:
                        db.execute(text(statement))
                    except ProgrammingError as error:
                        assert "permission denied" in str(error.orig).lower()
                    else:
                        raise AssertionError(f"the orchestrator role could run: {statement}")
        finally:
            restricted.dispose()
    finally:
        for name in ("orchestrator-role", "sweeper-role"):
            if name in processes.children:
                processes.stop(name)
        for name in names.values():
            w.cluster.psql(f'DROP OWNED BY "{name}"; DROP ROLE "{name}";')
