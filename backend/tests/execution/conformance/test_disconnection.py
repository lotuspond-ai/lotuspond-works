"""Task 13 conformance: disconnected runtimes (DISC-1 to DISC-4), through the TCP proxy.

The proxy sits between every runtime and the API; severing it cuts live connections
and refuses new ones, exactly as a lost network would.
"""

import time

import pytest
from driver import form_package, reconcile_from_journal, serving
from lotuspond_runtime.errors import FleetError
from processes import Processes, wait_for
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.models import (
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionUsageReservation,
    ExecutionWait,
)


def attempt_states(world, execution_id):
    with Session(world.engine) as db:
        return [
            row.state
            for row in db.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.execution_id == execution_id)
                .order_by(ExecutionAttempt.ordinal)
            )
        ]


def test_a_runtime_disconnected_before_its_claim(system, linux):
    """DISC-1: the delivery expires, a new ordinal runs; the old one is never claimable."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    hand = operator.hand(customer, "hand-a")
    try:
        execution_id = operator.start(customer, revision)["id"]
        delivery = wait_for(lambda: hand.activity.poll(), 60)
        runtime = operator.runtime(customer, "runtime-b")  # meanwhile, another runtime
        view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 120)
        assert view["state"] == "succeeded", view
        # Reconnected: the expired delivery can never be claimed.
        with pytest.raises(FleetError):
            hand.activity.claim(delivery)
    finally:
        hand.close()
    assert attempt_states(w, execution_id) == ["failed", "succeeded"]
    assert Processes.calls(runtime["counter"]) == 1


def test_a_runtime_disconnected_during_a_provider_call(system, linux):
    """DISC-2: the lease lapses, the attempt is uncertain, and the journal's stop and
    usage reconcile it as historical evidence. One provider call in total."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    revision = operator.deploy(customer, "activity_slow", dependencies=[operator.documents[0]])
    operator.demand(60000)  # longer than its slow read
    runtime = operator.runtime(customer, "runtime-a", read={"read": {"value": None, "sleep_s": 20}})
    execution_id = operator.start(customer, revision)["id"]
    wait_for(lambda: Processes.calls(runtime["counter"]) > 0, 60)
    processes.proxy.sever()
    view = operator.settled(customer, execution_id, {"reconciliation_required", "succeeded"}, 150)
    assert view["state"] == "reconciliation_required", view
    assert attempt_states(w, execution_id) == ["uncertain"]
    # The provider finished while cut off; its report never arrived. The worker
    # cannot keep a session it cannot renew, and ends.
    runtime["process"].wait(timeout=120)
    processes.proxy.restore()
    entry = reconcile_from_journal(w, customer, runtime, execution_id)
    assert entry["phase"] in ("finished", "abandoned"), entry["phase"]
    # The journal's usage is what reconciliation recorded: evidence, not a guess.
    usage = (entry.get("outcome") or {}).get("usage")
    with Session(w.engine) as db:
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.execution_id == execution_id
            )
        )
    observed = (
        reservation.observed_input_tokens,
        reservation.observed_output_tokens,
        reservation.observed_cost_microusd,
    )
    if usage is None:
        assert observed == (None, None, None)
    else:
        assert observed == (usage["input_tokens"], usage["output_tokens"], usage["cost_microusd"])
    # Uncertain is no retry class: the reconciled step fails.
    view = operator.settled(customer, execution_id, {"failed", "succeeded"}, 60)
    assert view["state"] == "failed", view
    assert Processes.calls(runtime["counter"]) == 1


def test_a_runtime_revoked_while_disconnected_cannot_return(system, linux):
    """DISC-3: its session is refused on reconnection; it calls nothing more."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    revision = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    runtime = operator.runtime(customer, "runtime-a")
    # Positive control: it serves work before it is cut off.
    first = operator.start(customer, revision)["id"]
    assert operator.settled(customer, first, {"succeeded", "failed"}, 60)["state"] == "succeeded"
    processes.proxy.sever()
    operator.revoke(customer, runtime["instance_id"])
    processes.proxy.restore()
    assert runtime["process"].wait(timeout=90) == 3
    execution_id = operator.start(customer, revision)["id"]
    time.sleep(3)
    with Session(w.engine) as db:
        assert (
            db.scalar(
                select(ExecutionActivityDelivery.id).where(
                    ExecutionActivityDelivery.execution_id == execution_id
                )
            )
            is None
        )
    assert Processes.calls(runtime["counter"]) == 1  # only the first, before the cut
    # Positive control: the new work is dispatchable, to a runtime still allowed.
    other = operator.runtime(customer, "runtime-b")
    assert operator.settled(customer, execution_id, {"succeeded", "failed"}, 60)["state"] == (
        "succeeded"
    )
    assert Processes.calls(other["counter"]) == 1


def test_all_runtimes_unreachable_for_several_lease_periods(system, linux):
    """DISC-4: waiting executions are unchanged; work completes after reconnection."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    form = operator.deploy(customer, "form", package=form_package(), dependencies=["waits.policy"])
    read = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    waiting = operator.start(customer, form)["id"]
    wait = operator.open_wait(customer, waiting, "review")
    runtime = operator.runtime(customer, "runtime-a")
    serving(w, runtime)
    processes.proxy.sever()
    severed = time.monotonic()
    with Session(w.engine) as db:
        before = db.get(ExecutionWait, wait["id"]).deadline_ms
    # Several 60 s session leases pass with no runtime reachable; the runtime cannot
    # keep its session and ends.
    assert runtime["process"].wait(timeout=200) == 3
    time.sleep(max(0, severed + 150 - time.monotonic()))
    with Session(w.engine) as db:
        row = db.get(ExecutionWait, wait["id"])
        assert (row.state, row.deadline_ms) == ("open", before)
    assert operator.execution(customer, waiting)["state"] == "waiting"
    processes.proxy.restore()
    restarted = processes.start_runtime_process("runtime-a")  # its service manager restarts it
    # Recovery: new work runs, and the execution that waited through the outage
    # completes. (A read admitted during an outage longer than its dispatch timeout
    # fails by its declared retry policy: observed, recorded.)
    queued = operator.start(customer, read)["id"]
    view = operator.settled(customer, queued, {"succeeded", "failed"}, 120)
    assert view["state"] == "succeeded", view
    assert Processes.calls(restarted["counter"]) == 1
    operator.signal(customer, waiting, wait["id"], "form", {"approved": True})
    assert operator.settled(customer, waiting, {"succeeded", "failed"})["state"] == "succeeded"
