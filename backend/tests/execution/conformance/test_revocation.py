"""Task 13 conformance: revocation (REV-1 to REV-4 and REV-6).

Membership and workspace suspension are E01 and platform-operator actions; they are
written as rows here and labelled so. Everything observed goes through the API.
"""

import time
from datetime import timedelta

import pytest
from driver import (
    Refused,
    diagnose,
    form_package,
    patient_read,
    serving,
    timer_then_read,
    two_reads,
)
from lotuspond_runtime.errors import FleetError
from processes import Processes, wait_for
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from lotuspond import models
from lotuspond.models import (
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionLease,
    ExecutionWait,
)


def deliveries(world, execution_id, instance_id=None):
    with Session(world.engine) as db:
        query = (
            select(func.count())
            .select_from(ExecutionActivityDelivery)
            .where(ExecutionActivityDelivery.execution_id == execution_id)
        )
        if instance_id is not None:
            query = query.where(ExecutionActivityDelivery.instance_id == instance_id)
        return db.scalar(query)


def attempts(world, execution_id):
    with Session(world.engine) as db:
        return [
            (row.ordinal, row.state)
            for row in db.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.execution_id == execution_id)
                .order_by(ExecutionAttempt.ordinal)
            )
        ]


def started(counter, seconds=60):
    wait_for(lambda: Processes.calls(counter) > 0, seconds)


def dispatch_evidence(world, execution_ids, runtimes):
    """Each execution's timeline in database milliseconds, with deliveries by runtime."""
    ms = "floor(extract(epoch FROM {}) * 1000)::bigint"
    queries = {
        "execution": f"SELECT state, requested_state, reason, {ms.format('created_at')},"
        f" {ms.format('clock_timestamp()')} AS now_ms FROM executions WHERE id = :id",
        "events": "SELECT sequence, code, attempt_id, occurred_at_ms FROM execution_events"
        " WHERE execution_id = :id ORDER BY sequence",
        "attempts": f"SELECT id, ordinal, state, {ms.format('created_at')}"
        " FROM execution_attempts WHERE execution_id = :id ORDER BY ordinal",
        "reservations": f"SELECT attempt_id, state, active_ms, {ms.format('created_at')},"
        " compute_stopped_at_ms, completed_at_ms FROM execution_usage_reservations"
        " WHERE execution_id = :id",
        "deliveries": "SELECT attempt_id, instance_id, generation, status, issued_at_ms,"
        " expires_at_ms FROM execution_activity_deliveries WHERE execution_id = :id"
        " ORDER BY issued_at_ms",
        "leases": "SELECT attempt_id, status, claimed_at_ms, renewed_at_ms, expires_at_ms"
        " FROM execution_leases WHERE execution_id = :id",
    }
    names = {runtime["instance_id"]: name for name, runtime in runtimes.items()}
    found = {"entitlement": world.state("entitlement")}
    with Session(world.engine) as db:
        for execution_id in execution_ids:
            found[execution_id] = {
                name: [
                    tuple(names.get(value, value) for value in row)
                    for row in db.execute(text(query), {"id": execution_id})
                ]
                for name, query in queries.items()
            }
        found["runtimes"] = [
            (names.get(row.id, row.id), row.status, row.last_seen_at_ms)
            for row in db.execute(text("SELECT id, status, last_seen_at_ms FROM runtime_instances"))
        ]
    return found


def test_a_runtime_revoked_while_holding_a_lease(system, linux):
    """REV-1: renewal and result refused; the attempt becomes uncertain; no new delivery."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "activity_slow", dependencies=[operator.documents[0]])
    operator.demand(90000)  # longer than the revocation takes, within the package's 100 s
    runtime = operator.runtime(customer, "runtime-a", read={"read": {"value": None, "sleep_s": 90}})
    execution_id = operator.start(customer, revision)["id"]
    started(runtime["counter"])
    operator.revoke(customer, runtime["instance_id"])
    view = operator.settled(customer, execution_id, {"reconciliation_required", "succeeded"}, 150)
    assert view["state"] == "reconciliation_required", view
    assert attempts(w, execution_id) == [(1, "uncertain")]
    assert runtime["process"].wait(timeout=60) == 3  # the revoked runtime stops
    assert deliveries(w, execution_id, runtime["instance_id"]) == 1
    assert Processes.calls(runtime["counter"]) == 1


def test_a_runtime_revoked_between_delivery_and_claim(system, linux):
    """REV-2: the claim is refused, the delivery expires, a new ordinal runs elsewhere."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    hand = operator.hand(customer, "hand-a")
    try:
        execution_id = operator.start(customer, revision)["id"]
        delivery = wait_for(lambda: hand.activity.poll(), 60)
        operator.revoke(customer, hand.identity.instance_id)
        with pytest.raises(FleetError):
            hand.activity.claim(delivery)
    finally:
        hand.close()
    runtime = operator.runtime(customer, "runtime-b")
    view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 120)
    assert view["state"] == "succeeded", view
    assert [state for _, state in attempts(w, execution_id)] == ["failed", "succeeded"]
    assert deliveries(w, execution_id, runtime["instance_id"]) == 1
    assert Processes.calls(runtime["counter"]) == 1


def add_member(world, workspace, role):
    """E01's surface, written as rows: a member and their signed-in session."""
    from uuid import uuid4

    from lotuspond.security import digest, new_token, now

    user_id, token = str(uuid4()), new_token()
    with Session(world.engine) as db, db.begin():
        db.add(
            models.User(id=user_id, email=f"{user_id}@example.com", name=role, password_hash="x")
        )
        db.flush()
        db.add(models.Membership(workspace_id=workspace, user_id=user_id, role=role))
        db.add(
            models.LoginSession(
                user_id=user_id, token_hash=digest(token), expires_at=now() + timedelta(hours=1)
            )
        )
    return user_id, f"lotuspond_session={token}"


def test_a_form_signal_from_a_removed_member_is_refused(system):
    """REV-3: removed after the wait opened; refused; the wait stays open."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(
        customer, "form", package=form_package(), dependencies=["waits.policy"]
    )
    execution_id = operator.start(customer, revision)["id"]
    wait = operator.open_wait(customer, execution_id, "review")
    user_id, cookie = add_member(w, customer.workspace, "admin")
    with Session(w.engine) as db, db.begin():
        db.delete(db.get(models.Membership, (customer.workspace, user_id)))
    removed = dict(customer.headers, cookie=cookie)
    response = customer.client.post(
        customer.prefix + f"/executions/{execution_id}/signals",
        json={"signal_id": "form", "wait_id": wait["id"], "payload": {"approved": True}},
        headers=removed,
    )
    assert response.status_code in (401, 403), response.text
    with Session(w.engine) as db:
        assert db.get(ExecutionWait, wait["id"]).state == "open"
    # Positive control: a current owner's signal is accepted.
    operator.signal(customer, execution_id, wait["id"], "form", {"approved": True})
    assert operator.settled(customer, execution_id, {"succeeded", "failed"})["state"] == "succeeded"


def test_a_suspended_workspace_admits_and_dispatches_nothing(system, linux):
    """REV-4: admission refused, nothing dispatched, waits preserved.

    The read falls due while the workspace is suspended, with its runtime up.
    Positive control: reactivated, the same work runs.
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    form = operator.deploy(customer, "form", package=form_package(), dependencies=["waits.policy"])
    operator.demand(30000)  # the reactivated read's lease may run to its 30 s deadline
    held = operator.deploy(
        customer,
        "timer-read",
        package=timer_then_read(6, timeout_seconds=30),
        dependencies=[operator.documents[0]],
    )
    waiting = operator.start(customer, form)["id"]
    wait = operator.open_wait(customer, waiting, "review")
    runtime = operator.runtime(customer, "runtime-a")
    serving(w, runtime)
    queued = operator.start(customer, held)["id"]  # its read falls due in 6 s
    with Session(w.engine) as db, db.begin():  # a platform operator suspends it
        db.get(models.Workspace, customer.workspace).status = "suspended"
        before = db.get(ExecutionWait, wait["id"]).deadline_ms
    with pytest.raises(Refused) as refused:
        operator.start(customer, held)
    assert refused.value.status == 403
    time.sleep(10)  # past the read's timer
    exited = runtime["process"].poll()
    assert exited in (None, 3), exited  # up and polling, or its session refused
    assert deliveries(w, queued) == 0 and Processes.calls(runtime["counter"]) == 0
    with Session(w.engine) as db:
        row = db.get(ExecutionWait, wait["id"])
        assert (row.state, row.deadline_ms) == ("open", before)
    with Session(w.engine) as db, db.begin():
        db.get(models.Workspace, customer.workspace).status = "active"
    if exited is not None:
        processes.start_runtime_process("runtime-a")
    assert operator.settled(customer, queued, {"succeeded", "failed"}, 90)["state"] == "succeeded"
    assert Processes.calls(runtime["counter"]) == 1


def test_expired_entitlement_stops_dispatch_but_not_reads_or_reconciliation(system, linux):
    """REV-6: no new dispatch; read, export and owner reconciliation still work."""
    w = system
    operator, customer = w.operator, w.customer
    documents = operator.documents
    slow = operator.deploy(customer, "two-reads", package=two_reads(), dependencies=[documents[0]])
    # Two active slots: the read refused while the authority is expired keeps one as
    # uncertain liability until it is reconciled.
    # A read that completes normally on a loaded host: the fixture read's 2 s step
    # deadline is also its lease, and claim, read and report can outlast it.
    read = operator.deploy(
        customer,
        "activity_read_30",
        package=patient_read(),
        dependencies=[documents[0]],
        capacity=2,
    )
    operator.demand(60000)  # long enough to stop and report after a refused renewal
    runtime = operator.runtime(customer, "runtime-slow", read={"read": {"sleep_s": 28}})
    reconciled = operator.start(customer, slow)["id"]
    started(runtime["counter"])
    operator.control(customer, reconciled, "cancel")
    operator.settled(customer, reconciled, {"reconciliation_required"}, 120)
    queued = operator.start(customer, read)["id"]
    entitlement = w.state("entitlement")
    w.update("entitlement", expires_at_ms=0, price_expires_at_ms=0)
    fresh = operator.runtime(customer, "runtime-fresh")
    serving(w, fresh)
    time.sleep(4)
    assert deliveries(w, queued) == 0 and Processes.calls(fresh["counter"]) == 0
    # Reads and export of evidence answer; owner reconciliation completes.
    assert operator.execution(customer, queued)["state"] in ("pending", "running")
    assert operator.events(customer, reconciled)
    customer.call("POST", f"/executions/{reconciled}/reconciliation", {})
    assert operator.settled(customer, reconciled, {"cancelled"})["state"] == "cancelled"
    # The slow runtime's part is over: new work goes to the fresh runtime only.
    w.processes.stop("runtime-slow")
    # Positive control: with the authority current again, the same runtime serves new
    # work. The read refused meanwhile either runs now too, or, fenced for
    # reconciliation after repeated refusals (Task 11's rule), waits for its owner;
    # both were observed. Either way no attempt runs twice.
    w.update(
        "entitlement",
        expires_at_ms=entitlement["expires_at_ms"],
        price_expires_at_ms=entitlement["price_expires_at_ms"],
    )
    control = operator.start(customer, read)["id"]
    try:
        view = operator.settled(customer, control, {"succeeded", "failed"}, 90)
    except AssertionError as stalled:  # with every attempt's state, for the record
        from lotuspond_runtime.activity_state import InvocationJournal

        w.processes.stop("runtime-fresh")
        journal = InvocationJournal(fresh["home"] / "journal")
        try:
            entries = {
                path.name: journal.get(path.name[len("attempt-") : -len(".json")])
                for path in (fresh["home"] / "journal").glob("attempt-*.json")
            }
        finally:
            journal.close()
        with Session(w.engine) as db:
            leases = db.execute(
                select(
                    ExecutionLease.status,
                    ExecutionLease.claimed_at_ms,
                    ExecutionLease.expires_at_ms,
                ).where(ExecutionLease.execution_id == control)
            ).all()
        raise AssertionError(
            (
                str(stalled),
                diagnose(w, control),
                diagnose(w, queued),
                entries,
                leases,
                Processes.calls_by_attempt(fresh["counter"]),
                Processes.calls_by_attempt(runtime["counter"]),
                dispatch_evidence(
                    w, (control, queued), {"runtime-slow": runtime, "runtime-fresh": fresh}
                ),
            )
        ) from None
    assert view["state"] == "succeeded", (
        diagnose(w, control),
        diagnose(w, queued),
        dispatch_evidence(w, (control, queued), {"runtime-slow": runtime, "runtime-fresh": fresh}),
    )
    calls = Processes.calls_by_attempt(fresh["counter"])
    assert calls and max(calls.values()) == 1 and "unknown" not in calls, calls
