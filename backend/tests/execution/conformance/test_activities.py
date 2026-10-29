"""Task 13 conformance: activities on real runtime processes (spike checks 5–10,
ENG-2, ENG-3, and the synthetic reference workflow)."""

import json
import signal
import time

from driver import diagnose, reconcile_from_journal
from processes import Processes, wait_for
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionLease,
)


def attempts(world, execution_id):
    with Session(world.engine) as db:
        return list(
            db.scalars(
                select(ExecutionAttempt)
                .where(
                    ExecutionAttempt.execution_id == execution_id,
                    ExecutionAttempt.parent_attempt_id.is_(None),
                )
                .order_by(ExecutionAttempt.ordinal)
            )
        )


def test_a_read_runs_once_on_a_real_runtime(system, linux):
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    runtime = operator.runtime(customer, "runtime-a")
    started = operator.start(customer, revision)
    view = operator.settled(customer, started["id"], {"succeeded", "failed"}, seconds=90)
    assert view["state"] == "succeeded", view
    assert Processes.calls(runtime["counter"]) == 1


def test_the_reference_agent_calls_its_model_and_tool_as_counted(system, linux):
    """Synthetic reference workflow: model round 1 asks for the read tool, round 2 ends."""
    w = system
    operator, customer = w.operator, w.customer
    model = operator.model_document()
    revision = operator.deploy(customer, "agent_tools", dependencies=[operator.documents[0], model])
    operator.demand(7000)  # the root and its three children within the agent's 30 s
    rounds = [
        {
            "tool_calls": [
                {"name": "connector-fixture-read", "arguments": {"target": None, "input": None}}
            ]
        },
        {"output": None},
    ]
    runtime = operator.runtime(customer, "runtime-a", model={"rounds": rounds})
    helper = operator.runtime(customer, "runtime-b", model={"rounds": rounds})
    execution_id = operator.start(customer, revision)["id"]
    view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 120)
    assert view["state"] == "succeeded", diagnose(w, execution_id)
    reads = Processes.calls(runtime["counter"]) + Processes.calls(helper["counter"])
    models = Processes.calls(runtime["model_counter"]) + Processes.calls(helper["model_counter"])
    assert (models, reads) == (2, 1)


def test_a_stopped_lease_holder_is_fenced_and_its_late_result_is_evidence(system, linux):
    """Spike 8–10 (runtime): SIGSTOP inside a provider call; the lease lapses, the attempt
    is uncertain and never re-leased; after SIGCONT the late report is refused.

    Uncertain attempts are never retried (no retry class covers them), so after the
    owner reconciles, the replacement is a new execution, which completes once.
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    revision = operator.deploy(customer, "activity_slow", dependencies=[operator.documents[0]])
    runtime = operator.runtime(customer, "runtime-a", read={"read": {"value": None, "sleep_s": 3}})
    execution_id = operator.start(customer, revision)["id"]
    wait_for(lambda: Processes.calls(runtime["counter"]) > 0, 60)
    processes.signal("runtime-a", signal.SIGSTOP)
    view = operator.settled(customer, execution_id, {"reconciliation_required", "succeeded"}, 150)
    assert view["state"] == "reconciliation_required", view
    processes.signal("runtime-a", signal.SIGCONT)
    time.sleep(5)  # the late report is sent and refused
    rows = attempts(w, execution_id)
    assert [row.state for row in rows] == ["uncertain"]
    with Session(w.engine) as db:
        leases = list(
            db.scalars(select(ExecutionLease).where(ExecutionLease.attempt_id == rows[0].id))
        )
        assert [lease.status for lease in leases] == ["uncertain"]  # never re-leased
    # The stopped runtime's journal is the evidence; it ends before the owner reads it.
    processes.stop("runtime-a")
    entry = reconcile_from_journal(w, customer, runtime, execution_id)
    with Session(w.engine) as db:
        delivery = db.scalar(
            select(ExecutionActivityDelivery).where(
                ExecutionActivityDelivery.attempt_id == rows[0].id
            )
        )
    assert entry["attempt_id"] == rows[0].id and entry["delivery_sha256"] == delivery.digest
    assert entry["phase"] in ("started", "finished", "abandoned"), entry
    # Uncertain is no retry class: the reconciled step fails; it gets no new ordinal.
    assert operator.settled(customer, execution_id, {"failed", "succeeded"})["state"] == "failed"
    assert len(attempts(w, execution_id)) == 1
    # Its unconfirmed provider stop fences runtime-a's journal: no further work there.
    fresh = operator.runtime(customer, "runtime-b")
    replacement = operator.start(customer, revision)["id"]
    assert (
        operator.settled(customer, replacement, {"succeeded", "failed"}, 120)["state"]
        == "succeeded"
    )
    # One provider call per execution: the stopped one's, and the replacement's.
    assert Processes.calls(runtime["counter"]) == Processes.calls(fresh["counter"]) == 1


def test_retries_stop_at_their_bound_and_bad_output_never_becomes_data(system, linux):
    """ENG-2."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    failing = operator.runtime(
        customer, "runtime-failing", read={"read": {"failure": "unavailable", "not_reached": True}}
    )
    execution_id = operator.start(customer, revision)["id"]
    assert (
        operator.settled(customer, execution_id, {"failed", "succeeded"}, 120)["state"] == "failed"
    )
    assert len(attempts(w, execution_id)) == 3  # the step's max_attempts
    assert Processes.calls(failing["counter"]) == 3
    w.processes.kill("runtime-failing")
    bad = operator.runtime(customer, "runtime-bad", read={"read": {"value": 5}})
    execution_id = operator.start(customer, revision)["id"]
    assert (
        operator.settled(customer, execution_id, {"failed", "succeeded"}, 120)["state"] == "failed"
    )
    rows = attempts(w, execution_id)
    assert [row.state for row in rows] == ["failed"]  # invalid output is not retried
    graph = w.graph()
    with Session(w.engine) as db:
        from driver import _scope_of

        failure = json.loads(
            graph.store._get_internal(
                db,
                _scope_of(rows[0]),
                {
                    "id": rows[0].result_artifact_id,
                    "version": rows[0].result_artifact_version,
                    "sha256": rows[0].result_sha256,
                },
            )
        )
    assert failure.get("failure") == "invalid_result", failure  # the 5 is not data
    assert Processes.calls(bad["counter"]) == 1
    w.processes.kill("runtime-bad")
    # A model's non-conforming output: the agent's output schema is null, the model
    # answers a string. The step fails; the string never becomes the result.
    model = operator.model_document()
    agent = operator.deploy(customer, "agent_tools", dependencies=[operator.documents[0], model])
    operator.demand(5000)
    talker = operator.runtime(customer, "runtime-model", model={"rounds": [{"output": "x"}]})
    execution_id = operator.start(customer, agent)["id"]
    view = operator.settled(customer, execution_id, {"failed", "succeeded"}, 120)
    assert view["state"] == "failed", view
    with Session(w.engine) as db:
        execution = db.get(Execution, execution_id)
    assert execution.result_artifact_id is None  # never data
    assert Processes.calls(talker["model_counter"]) == 1


def test_deliveries_are_single_use_and_leases_short(system, linux):
    """ENG-3: one delivery per attempt within 5 minutes; leases at most 60 s, in deadline."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "activity_slow", dependencies=[operator.documents[0]])
    operator.demand(40000)  # longer than its slow read
    # 26 s: past the 20 s renewal, within the connection's 30 s call bound.
    runtime = operator.runtime(customer, "runtime-a", read={"read": {"value": None, "sleep_s": 26}})
    execution_id = operator.start(customer, revision)["id"]
    wait_for(lambda: Processes.calls(runtime["counter"]) > 0, 60)
    samples = []
    deadline = time.monotonic() + 50
    while (
        time.monotonic() < deadline
        and operator.execution(customer, execution_id)["state"] == "running"
    ):
        with Session(w.engine) as db:
            from lotuspond.fleet.authority import database_now_ms

            now = database_now_ms(db)
            for lease in db.scalars(
                select(ExecutionLease).where(ExecutionLease.execution_id == execution_id)
            ):
                if lease.status == "active":
                    samples.append((now, lease.expires_at_ms, lease.renewed_at_ms))
        time.sleep(2)
    final = operator.settled(
        customer, execution_id, {"succeeded", "failed", "reconciliation_required"}, 120
    )
    with Session(w.engine) as db:
        leases = [
            (lease.status, lease.claimed_at_ms, lease.renewed_at_ms, lease.expires_at_ms)
            for lease in db.scalars(
                select(ExecutionLease).where(ExecutionLease.execution_id == execution_id)
            )
        ]
    assert final["state"] == "succeeded", (diagnose(w, execution_id), leases, samples[-3:])
    assert samples and all(expires - now <= 60000 for now, expires, _ in samples)
    assert len({renewed for _, _, renewed in samples}) >= 2  # it was renewed, never longer
    with Session(w.engine) as db:
        deliveries = list(
            db.scalars(
                select(ExecutionActivityDelivery).where(
                    ExecutionActivityDelivery.execution_id == execution_id
                )
            )
        )
        attempt = attempts(w, execution_id)[0]
        lease = db.scalar(select(ExecutionLease).where(ExecutionLease.attempt_id == attempt.id))
    assert len(deliveries) == 1
    assert deliveries[0].expires_at_ms - deliveries[0].issued_at_ms <= 300000
    # Within the attempt deadline: the delivery's issue plus the step's 120 s timeout.
    attempt_deadline = deliveries[0].issued_at_ms + 120000
    assert lease.expires_at_ms <= attempt_deadline
    assert all(expires <= attempt_deadline for _, expires, _ in samples)


def test_a_delivery_is_claimed_once_by_its_own_session(system, linux):
    """ENG-3, single use: a claimed delivery is never claimed again, by anyone."""
    from lotuspond_runtime.errors import FleetError

    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "activity_slow", dependencies=[operator.documents[0]])
    mine, other = operator.hand(customer, "hand-a"), operator.hand(customer, "hand-b")
    try:
        operator.start(customer, revision)
        delivery = wait_for(lambda: mine.activity.poll(), 60, interval=1.0)
        lease = mine.activity.claim(delivery)  # positive control: claimed once
        attempt_id = json.loads(delivery.envelope)["attempt_id"]
        assert lease.attempt_id == attempt_id
        # Its holder's equal claim (a lost acknowledgement) gets the same lease back,
        # never a second one.
        again = mine.activity.claim(delivery)
        assert (again.lease_revision, again.expires_at_ms) == (
            lease.lease_revision,
            lease.expires_at_ms,
        )
        try:  # another session is refused
            other.activity.claim(delivery)
        except FleetError:
            pass
        else:
            raise AssertionError("another session claimed the delivery")
        with Session(w.engine) as db:
            leases = db.scalar(
                select(func.count())
                .select_from(ExecutionLease)
                .where(ExecutionLease.attempt_id == attempt_id)
            )
        assert leases == 1
    finally:
        mine.close()
        other.close()
