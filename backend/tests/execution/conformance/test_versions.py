"""Task 13 conformance: pinned upgrades and withdrawn revisions (spike 29–33, REV-5)."""

import time

from driver import form_package, patient_read, timer_then_read
from processes import Processes
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionDeploymentRevision,
)


def revision_of(world, execution_id):
    with Session(world.engine) as db:
        return db.get(Execution, execution_id).deployment_revision_id


def deliveries(world, execution_id):
    with Session(world.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(ExecutionActivityDelivery)
            .where(ExecutionActivityDelivery.execution_id == execution_id)
        )


def deployment_of(world, revision_id):
    with Session(world.engine) as db:
        return db.get(ExecutionDeploymentRevision, revision_id).deployment_id


def test_a_waiting_execution_finishes_on_its_revision_after_an_upgrade(system):
    """Spike 29–32: revision 2 enabled while revision 1 waits."""
    w = system
    operator, customer = w.operator, w.customer
    first = operator.deploy(customer, "form", package=form_package(), dependencies=["waits.policy"])
    waiting = operator.start(customer, first)["id"]
    wait = operator.open_wait(customer, waiting, "review")
    package = form_package()
    package["version"] = "1.0.1"
    second = operator.deploy(
        customer,
        "form",
        package=package,
        dependencies=["waits.policy"],
        deployment_id=deployment_of(w, first),
        label="form@1.0.1",
    )
    upgraded = operator.start(customer, second)["id"]
    assert revision_of(w, upgraded) == second
    operator.signal(customer, waiting, wait["id"], "form", {"approved": True})
    assert operator.settled(customer, waiting, {"succeeded", "failed"})["state"] == "succeeded"
    assert revision_of(w, waiting) == first  # it finished on the revision it was admitted to


def test_a_runtime_without_the_activity_capability_never_receives_deliveries(system, linux):
    """Spike 33: a runtime running only E03's command loop is never sent an activity.

    (An activity runtime that lacks one provider is a different case: it receives the
    delivery and reports `unavailable`; recorded as a known limitation.)
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    operator.demand(30000)  # the lease may run to the read's 30 s deadline
    revision = operator.deploy(
        customer, "activity_read_30", package=patient_read(), dependencies=[operator.documents[0]]
    )
    enrolled = operator.runtime(customer, "commands-only", start=False)
    home = enrolled["home"]
    processes._spawn(
        "commands-only",
        [
            processes.python,
            "-I",
            "-m",
            "lotuspond_runtime",
            "run",
            "--config",
            str(home / "config.json"),
        ],
    )
    execution_id = operator.start(customer, revision)["id"]
    time.sleep(5)  # it polls commands repeatedly
    assert deliveries(w, execution_id) == 0
    assert processes.children["commands-only"].poll() is None  # alive, just not eligible
    runtime = operator.runtime(customer, "activity-runtime")  # positive control
    assert (
        operator.settled(customer, execution_id, {"succeeded", "failed"}, 90)["state"]
        == "succeeded"
    )
    assert Processes.calls(runtime["counter"]) == 1


def test_withdrawn_revisions_get_no_further_activities(system, linux):
    """REV-5: after activation pinned executions continue; after disable or revoke, none."""
    w = system
    operator, customer = w.operator, w.customer
    operator.demand(30000)  # the continuing read's lease may run to its 30 s deadline
    first = operator.deploy(
        customer,
        "timer-read",
        package=timer_then_read(3, timeout_seconds=30),
        dependencies=[operator.documents[0]],
    )
    runtime = operator.runtime(customer, "runtime-a")
    continuing = operator.start(customer, first)["id"]
    package = timer_then_read(20)
    package["version"] = "1.0.1"
    second = operator.deploy(
        customer,
        "timer-read",
        package=package,
        dependencies=[operator.documents[0]],
        deployment_id=deployment_of(w, first),
        label="timer-read@1.0.1",
    )
    withdrawn = operator.start(customer, second)["id"]
    started = time.monotonic()
    path = f"/deployments/{deployment_of(w, second)}/revisions/{second}/status"
    customer.call("POST", path, {"status": "revoked"})
    # Revision 1 was pinned before revision 2 was activated: it continues.
    assert (
        operator.settled(customer, continuing, {"succeeded", "failed"}, 90)["state"] == "succeeded"
    )
    time.sleep(max(0, started + 25 - time.monotonic()))  # past the revoked one's timer
    assert deliveries(w, withdrawn) == 0
    assert operator.execution(customer, withdrawn)["state"] != "succeeded"
    # Disabling: a separate deployment (revoking a deployment's active revision also
    # ends admissions to its earlier revisions).
    package = timer_then_read(3)
    package["package_id"] = "timer-read-disabled"
    other = operator.deploy(
        customer, "timer-read-disabled", package=package, dependencies=[operator.documents[0]]
    )
    later = operator.start(customer, other)["id"]
    customer.call("POST", f"/deployments/{deployment_of(w, other)}/status", {"status": "disabled"})
    time.sleep(6)  # past its timer
    assert deliveries(w, later) == 0
    assert Processes.calls(runtime["counter"]) == 1  # only the continuing execution's read
