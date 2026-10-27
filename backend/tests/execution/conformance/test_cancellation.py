"""Task 13 conformance: cancellation (spike checks 23–27)."""

import time

import pytest
from driver import Refused, form_package, serving, two_reads
from processes import Processes
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.models import (
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionReconciliationObservation,
    ExecutionRequest,
    ExecutionWait,
)


def deliveries(world, execution_id):
    with Session(world.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(ExecutionActivityDelivery)
            .where(ExecutionActivityDelivery.execution_id == execution_id)
        )


def test_cancelling_a_review_cancels_its_wait_and_refuses_later_signals(system):
    """Spike 23–24."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(
        customer, "form", package=form_package(), dependencies=["waits.policy"]
    )
    execution_id = operator.start(customer, revision)["id"]
    wait = operator.open_wait(customer, execution_id, "review")
    operator.control(customer, execution_id, "cancel")
    view = operator.settled(customer, execution_id, {"cancelled", "succeeded", "failed"})
    assert view["state"] == "cancelled", view
    with Session(w.engine) as db:
        assert db.get(ExecutionWait, wait["id"]).state != "open"
    with pytest.raises(Refused):
        operator.signal(customer, execution_id, wait["id"], "form", {"approved": True})
    assert operator.execution(customer, execution_id)["state"] == "cancelled"


def test_cancelling_an_in_flight_read_records_its_result_and_runs_nothing_next(system, linux):
    """Spike 25: renewal reports the cancellation; the reported result is recorded."""
    w = system
    operator, customer = w.operator, w.customer
    # A lease long enough that its renewal, refused by the cancellation, leaves time to
    # stop the provider and report (the runtime's margin); no agent step caps it.
    revision = operator.deploy(
        customer, "two-reads", package=two_reads(), dependencies=[operator.documents[0]]
    )
    operator.demand(60000)
    runtime = operator.runtime(
        customer, "runtime-slow", read={"read": {"value": None, "sleep_s": 28}}
    )
    execution_id = operator.start(customer, revision)["id"]
    # Positive control: the read really started before the cancellation.
    deadline = time.monotonic() + 60
    while Processes.calls(runtime["counter"]) == 0:
        assert time.monotonic() < deadline, "the read never started"
        time.sleep(0.2)
    operator.control(customer, execution_id, "cancel")
    # The provider was stopped mid-call, so its usage is unknown: owner decision 2 of
    # Task 11 requires one owner reconciliation before the cancellation completes.
    view = operator.settled(
        customer, execution_id, {"reconciliation_required", "cancelled", "succeeded", "failed"}, 120
    )
    assert view["state"] == "reconciliation_required", view
    with Session(w.engine) as db:
        reported = db.scalar(
            select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution_id)
        )
        # The runtime's report is recorded: its result request and its unknown usage.
        assert reported.state == "uncertain"
        recorded = db.scalar(
            select(ExecutionRequest.id).where(
                ExecutionRequest.request_key == "activity.result:" + reported.id
            )
        )
    if not recorded:
        from lotuspond_runtime.activity_state import InvocationJournal

        w.processes.stop("runtime-slow")
        journal = InvocationJournal(runtime["home"] / "journal")
        entry = journal.get(reported.id)
        journal.close()
        raise AssertionError(("no recorded report", entry))
    with Session(w.engine) as db:
        assert db.scalar(
            select(ExecutionRequest.id).where(
                ExecutionRequest.request_key == "activity.result:" + reported.id
            )
        )
        assert "outcome_unknown" in set(
            db.scalars(
                select(ExecutionReconciliationObservation.code).where(
                    ExecutionReconciliationObservation.attempt_id == reported.id
                )
            )
        )
    customer.call("POST", f"/executions/{execution_id}/reconciliation", {})
    view = operator.settled(customer, execution_id, {"cancelled", "succeeded", "failed"})
    assert view["state"] == "cancelled", view
    with Session(w.engine) as db:
        attempts = list(
            db.scalars(
                select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution_id)
            )
        )
    # One attempt only: the agent step never ran.
    assert len(attempts) == 1, [(a.state, a.ordinal) for a in attempts]
    assert Processes.calls(runtime["counter"]) == 1


def test_cancelling_before_dispatch_signs_nothing_and_calls_nothing(system, linux):
    """Spike 26 (cancel before dispatch) and 27 (a cancelled execution never succeeds)."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    execution_id = operator.start(customer, revision)["id"]
    operator.control(customer, execution_id, "cancel")
    view = operator.settled(customer, execution_id, {"cancelled", "succeeded", "failed"})
    assert view["state"] == "cancelled", view
    runtime = operator.runtime(customer, "runtime-late")
    serving(w, runtime)
    time.sleep(3)  # it polls repeatedly: nothing is there for it
    assert deliveries(w, execution_id) == 0
    assert Processes.calls(runtime["counter"]) == 0
    assert operator.execution(customer, execution_id)["state"] == "cancelled"
    # Positive control: the same runtime serves the next admission at once.
    control = operator.start(customer, revision)["id"]
    assert operator.settled(customer, control, {"succeeded", "failed"}, 60)["state"] == "succeeded"
    assert Processes.calls(runtime["counter"]) == 1 and deliveries(w, execution_id) == 0
