"""Task 13 tier 2: an immediate PostgreSQL restart (spike 1–2, REG-4) and ENG-4's grants."""

from uuid import uuid4

from driver import form_package
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionEvent, ExecutionWait


def wait_row(world, wait_id):
    with Session(world.engine) as db:
        row = db.get(ExecutionWait, wait_id)
        return (row.id, row.wait_key, row.deadline_ms, row.state, row.step_instance_id)


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
    view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 120)
    assert view["state"] == "succeeded", view


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


def test_the_orchestrator_role_cannot_change_events(system):
    """ENG-4: a uniquely named least-privilege orchestrator role (disposable cluster only).

    The role may read and write every execution table but only read and append
    events. Its worker still advances executions (positive control); its UPDATE and
    DELETE on events are refused by PostgreSQL itself.
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    schema = w.config["schema"]
    role = "lp_conformance_orchestrator_" + uuid4().hex[:12]
    w.cluster.psql(
        f'CREATE ROLE "{role}" LOGIN;'
        f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}";'
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}";'
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}";'
        f'REVOKE UPDATE, DELETE, TRUNCATE ON "{schema}".execution_events FROM "{role}";'
    )
    processes.kill("orchestrator-a")
    processes.start_worker("orchestrator", "orchestrator-role", user=role)
    revision = operator.deploy(customer, "waits_fast", dependencies=["waits.policy"])
    execution_id = operator.start(customer, revision)["id"]
    operator.open_wait(customer, execution_id, "callback", seconds=60)  # it advanced it
    restricted = create_engine(
        w.cluster.url.replace("lotuspond@", f"{role}@"),
        connect_args={"options": f"-c search_path={schema}"},
        hide_parameters=True,
    )
    try:
        with Session(restricted) as db:
            assert db.scalar(select(func.count()).select_from(ExecutionEvent)) > 0
        for statement in (
            "UPDATE execution_events SET code = code",
            "DELETE FROM execution_events",
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
        processes.stop("orchestrator-role")
        w.cluster.psql(f'DROP OWNED BY "{role}"; DROP ROLE "{role}";')
