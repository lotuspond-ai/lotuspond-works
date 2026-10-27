"""Task 13 conformance: process kills during waits and activities (spike checks 1–7, REG-4).

Every process is really killed (SIGKILL of its whole group) and a fresh one started;
nothing in the database or the authorities is edited to simulate it.
"""

import time

from driver import Refused, decoded_events, form_package
from processes import Processes
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionActivityDelivery, ExecutionWait


def wait_row(world, wait_id):
    with Session(world.engine) as db:
        row = db.get(ExecutionWait, wait_id)
        return (row.id, row.wait_key, row.deadline_ms, row.state, row.step_instance_id)


def restart_workers(processes, suffix):
    for name in ("orchestrator-a", "sweeper-a"):
        if name in processes.children and processes.children[name].poll() is None:
            processes.kill(name)
    processes.start_worker("orchestrator", f"orchestrator-{suffix}")
    processes.start_worker("sweeper", f"sweeper-{suffix}")


def test_a_form_wait_survives_killed_processes_byte_for_byte(system, linux):
    """Spike 1–4: SIGKILL every process during a form wait; the bound wait is intact."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    revision = operator.deploy(
        customer, "form", package=form_package(), dependencies=["waits.policy"]
    )
    operator.runtime(customer, "runtime-a")  # killed with the rest
    execution_id = operator.start(customer, revision)["id"]
    wait = operator.open_wait(customer, execution_id, "review")
    before = wait_row(w, wait["id"])
    processes.kill("runtime-a")
    restart_workers(processes, "b")
    operator.runtime(customer, "runtime-b")
    time.sleep(2)  # fresh processes run several passes over the waiting execution
    assert wait_row(w, wait["id"]) == before  # identity, key, deadline and state intact
    # A signal naming another wait, or a payload outside the bound schema, is refused
    # and the wait stays open (positive control first: the API answers this execution).
    assert operator.execution(customer, execution_id)["state"] == "waiting"
    other = "00000000-0000-4000-8000-000000000001"
    for wait_id, payload in ((other, {"approved": True}), (wait["id"], {"approved": "yes"})):
        try:
            operator.signal(customer, execution_id, wait_id, "form", payload)
        except Refused as refused:
            assert refused.status in (400, 404, 409), refused
        else:
            raise AssertionError("a mismatched signal was accepted")
    assert wait_row(w, wait["id"]) == before
    operator.signal(customer, execution_id, wait["id"], "form", {"approved": True})
    view = operator.settled(customer, execution_id, {"succeeded", "failed"})
    assert view["state"] == "succeeded", view


def test_fresh_processes_finish_an_activity_with_one_provider_call(system, linux):
    """Spike 5–7: kill the workers mid-flight; new ones finish; one call; distinct actors."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    revision = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    execution_id = operator.start(customer, revision)["id"]
    restart_workers(processes, "b")  # before any runtime exists: nothing is dispatched
    runtime = operator.runtime(customer, "runtime-a")
    view = operator.settled(customer, execution_id, {"succeeded", "failed"}, seconds=90)
    assert view["state"] == "succeeded", view
    assert Processes.calls(runtime["counter"]) == 1
    # Orchestration and activity events record who caused them: decoded, the origins
    # are the orchestrator and the runtime's activity, and the activity's lease names
    # that runtime instance. (No event names which worker process acted: recorded as
    # a limitation in the Task 13 validation document.)
    origins = {content["origin"] for _, content in decoded_events(w, execution_id)}
    assert origins == {"orchestrator", "activity"}, origins
    with Session(w.engine) as db:
        holders = set(
            db.scalars(
                select(ExecutionActivityDelivery.instance_id).where(
                    ExecutionActivityDelivery.execution_id == execution_id
                )
            )
        )
    assert holders == {runtime["instance_id"]}


def test_a_timer_fires_once_at_its_deadline_across_kills(system, linux):
    """REG-4 (tier 1): orchestrator and sweeper killed while a timer runs.

    No event marks a timer; its single firing shows in the rows: the timer wait is
    satisfied once, with its original deadline, and the next step's wait is opened
    exactly once, no earlier than that deadline.
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    revision = operator.deploy(customer, "waits_fast", dependencies=["waits.policy"])
    execution_id = operator.start(customer, revision)["id"]
    timer = operator.open_wait(customer, execution_id, "pause")
    restart_workers(processes, "b")
    processes.kill("sweeper-b")
    processes.start_worker("sweeper", "sweeper-c")
    callback = operator.open_wait(customer, execution_id, "callback", seconds=30)
    with Session(w.engine) as db:
        row = db.get(ExecutionWait, timer["id"])
        opened = list(
            db.scalars(
                select(ExecutionWait).where(
                    ExecutionWait.execution_id == execution_id,
                    ExecutionWait.id != timer["id"],
                )
            )
        )
        opened_ms = db.scalar(
            select(func.floor(func.extract("epoch", ExecutionWait.created_at) * 1000)).where(
                ExecutionWait.id == callback["id"]
            )
        )
    assert row.state == "satisfied" and row.deadline_ms == timer["deadline_ms"]
    assert [wait.id for wait in opened] == [callback["id"]]  # the next step, once
    assert opened_ms >= timer["deadline_ms"]  # never before the timer's deadline
