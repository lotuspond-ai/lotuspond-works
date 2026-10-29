"""Task 13 tier 2: restore from an older backup (RESTORE-1 to RESTORE-3).

A restore takes the system down: every process stops, the database returns to the
backup, the independent recovery epoch advances (it lives outside the database), and
the processes start again. Runtimes keep their own local state. Every restored
execution is then held until its owner releases it (owner decision, 2026-10-28).
"""

from driver import form_package, patient_read, reconcile_from_journal
from processes import Processes, wait_for
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionInbox,
    ExecutionUsageReservation,
    RuntimeSession,
)


def stop_all(processes):
    for name, process in list(processes.children.items()):
        if process.poll() is None:
            processes.stop(name)


def restore(world, processes, dump, *, epoch, available=True):
    stop_all(processes)
    world.cluster.restore(dump)
    world.update("recovery", epoch=epoch, available=available)
    processes.start_api()


def deliveries_of(world, execution_id):
    with Session(world.engine) as db:
        return list(
            db.execute(
                select(
                    ExecutionActivityDelivery.status, ExecutionActivityDelivery.recovery_epoch
                ).where(ExecutionActivityDelivery.execution_id == execution_id)
            ).all()
        )


def test_a_restore_refuses_old_epochs_and_holds_every_execution(system, linux):
    """RESTORE-1, under the restore hold (owner decision, 2026-10-28).

    The backup holds a form wait, a delivered read and a queued read. After the backup
    the queued read is claimed and completes, and the form is signalled. After the
    restore: older-epoch sessions and deliveries are refused; every restored execution
    is held for reconciliation, and no restored attempt is delivered under the new
    epoch; the read claimed after the backup is never run again, and reconciles as
    uncertain from its runtime's journal; the owner releases what the journals
    account for, and the original signal is then accepted once.
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    form = operator.deploy(customer, "form", package=form_package(), dependencies=["waits.policy"])
    # Room for three active reads: an uncertain attempt keeps its slot until reconciled.
    read = operator.deploy(
        customer, "activity_read", dependencies=[operator.documents[0]], capacity=3
    )
    # The queued read must complete on a freshly started runtime. The fixture read's 2 s
    # step deadline is also its lease, which claim, read and report can outlast on a
    # loaded host, leaving it uncertain.
    patient = operator.deploy(
        customer, "activity_read_30", package=patient_read(), dependencies=[operator.documents[0]]
    )
    waiting = operator.start(customer, form)["id"]
    wait = operator.open_wait(customer, waiting, "review")
    # Delivered at backup time: a hand-driven runtime polls it, and never claims it.
    # The sweeper stops once the read is reserved, so its delivery cannot expire (the
    # read's dispatch timeout is 2 s) before the backup; it starts again after it.
    hand = operator.hand(customer, "hand")
    delivered = operator.start(customer, read)["id"]
    wait_for(lambda: reservations_of(w, delivered), 60)
    processes.stop("sweeper-a")
    try:
        assert wait_for(lambda: hand.activity.poll(), 60) is not None
    finally:
        hand.close()
    queued = operator.start(customer, patient)["id"]  # no activity runtime yet
    wait_for(lambda: attempt_rows(w, queued), 60)
    dump = w.cluster.backup()
    assert deliveries_of(w, queued) == []  # the backup holds it queued, never delivered
    # After the backup: the queued read runs once; the form is signalled.
    processes.start_worker("sweeper", "sweeper-b")
    runtime = operator.runtime(customer, "runtime-a")
    assert operator.settled(customer, queued, {"succeeded", "failed"}, 90)["state"] == "succeeded"
    claimed_attempt = attempt_rows(w, queued)[0].id
    # Once (the delivered read's own retries, after its delivery expires, may run here).
    assert Processes.calls_by_attempt(runtime["counter"]).get(claimed_attempt) == 1
    operator.signal(customer, waiting, wait["id"], "form", {"approved": True})
    assert operator.settled(customer, waiting, {"succeeded", "failed"})["state"] == "succeeded"
    restore(w, processes, dump, epoch=2)
    processes.start_worker("orchestrator", "orchestrator-r")
    processes.start_worker("sweeper", "sweeper-r")
    # Older-epoch sessions are refused: the runtime cannot come back.
    returning = processes.start_runtime_process("runtime-a")
    assert returning["process"].wait(timeout=90) == 3
    # Every restored execution is held. The delivery signed under epoch 1 is never
    # claimable again; its attempt, which may have run, is uncertain. The queued read
    # may have been still `pending` at the backup: held, it keeps that state.
    for execution_id in (delivered, waiting):
        view = operator.settled(customer, execution_id, {"reconciliation_required"}, 120)
        assert view["state"] == "reconciliation_required"
    wait_for(lambda: held(w, queued), 120)
    assert operator.execution(customer, queued)["state"] in HELD
    assert all(status != "pending" for status, epoch in deliveries_of(w, delivered) if epoch == 1)
    # A fresh runtime under the new epoch serves new work (positive control), and is
    # never delivered a restored attempt.
    fresh = operator.runtime(customer, "runtime-b")
    control = operator.start(customer, read)["id"]
    assert operator.settled(customer, control, {"succeeded", "failed"}, 120)["state"] == "succeeded"
    second_runs = Processes.calls_by_attempt(fresh["counter"]).get(claimed_attempt, 0)
    assert second_runs == 0, "the activity claimed after the backup ran again after the restore"
    assert Processes.calls(fresh["counter"]) == 1
    assert deliveries_of(w, queued) == []
    processes.stop("runtime-b")
    # The read claimed after the backup reconciles as uncertain from runtime-a's
    # journal; it stays held until its owner releases it.
    entry = reconcile_from_journal(w, customer, runtime, queued)
    assert entry["attempt_id"] == claimed_attempt and entry["phase"] == "recorded"
    assert held(w, queued) and operator.execution(customer, queued)["state"] in HELD
    # The owner releases what the journals account for. The read delivered under the
    # older epoch still needs its stop confirmed: nothing proves it never ran.
    release = "/executions/{}/reconciliation"
    customer.call("POST", release.format(delivered), {}, expect=(409,))
    for execution_id in (queued, waiting):
        customer.call("POST", release.format(execution_id), {})
    assert operator.settled(customer, queued, {"failed", "succeeded"})["state"] == "failed"
    # The released form waits again; the original signal is accepted once.
    assert operator.settled(customer, waiting, {"waiting"}, 60)["state"] == "waiting"
    receipt = operator.signal(customer, waiting, wait["id"], "form", {"approved": True})
    # A replay answers the same receipt with its current status: the orchestrator may
    # consume the signal between the two calls.
    replay = operator.signal(customer, waiting, wait["id"], "form", {"approved": True})
    assert replay["id"] == receipt["id"], (receipt, replay)
    assert {receipt["status"], replay["status"]} <= {"pending", "delivered"}, (receipt, replay)
    assert operator.settled(customer, waiting, {"succeeded", "failed"})["state"] == "succeeded"
    with Session(w.engine) as db:
        accepted = db.scalar(
            select(func.count())
            .select_from(ExecutionInbox)
            .where(ExecutionInbox.execution_id == waiting)
        )
        received = db.scalar(
            select(func.count())
            .select_from(ExecutionEvent)
            .where(ExecutionEvent.execution_id == waiting, ExecutionEvent.code == "signal_received")
        )
    assert accepted == 1 and received == 1, (accepted, received)
    assert Processes.calls(fresh["counter"]) == 1  # nothing restored ever ran on it


HELD = ("pending", "reconciliation_required")


def held(world, execution_id):
    """Held after a restore: its recovery epoch is cleared until its owner releases it."""
    with Session(world.engine) as db:
        return db.get(Execution, execution_id).recovery_epoch is None


def reservations_of(world, execution_id):
    with Session(world.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(ExecutionUsageReservation)
            .where(ExecutionUsageReservation.execution_id == execution_id)
        )


def attempt_rows(world, execution_id):
    with Session(world.engine) as db:
        return list(
            db.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.execution_id == execution_id)
                .order_by(ExecutionAttempt.ordinal)
            )
        )


def sessions_of(world, instance_id):
    with Session(world.engine) as db:
        return set(
            db.scalars(select(RuntimeSession.id).where(RuntimeSession.instance_id == instance_id))
        )


def test_a_runtime_revoked_after_the_backup_cannot_return(system, linux):
    """RESTORE-2: its generation and the independent epoch refuse it."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    runtime = operator.runtime(customer, "runtime-a")
    wait_for(lambda: sessions_of(w, runtime["instance_id"]), 30)
    dump = w.cluster.backup()  # the instance is still active here
    operator.revoke(customer, runtime["instance_id"])
    assert runtime["process"].wait(timeout=90) == 3
    restore(w, processes, dump, epoch=2)
    restored = sessions_of(w, runtime["instance_id"])
    returning = processes.start_runtime_process("runtime-a")
    assert returning["process"].wait(timeout=90) == 3
    assert sessions_of(w, runtime["instance_id"]) == restored  # no new session


def test_without_recovery_authority_only_reads_work(system, linux):
    """RESTORE-3: orchestration, sweeping and dispatch refuse; read and export work."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    form = operator.deploy(customer, "form", package=form_package(), dependencies=["waits.policy"])
    read = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    waiting = operator.start(customer, form)["id"]
    operator.open_wait(customer, waiting, "review")
    early = operator.runtime(customer, "runtime-early", start=False)  # enrolled, not running
    queued = operator.start(customer, read)["id"]
    dump = w.cluster.backup()
    restore(w, processes, dump, epoch=2, available=False)
    for role in ("orchestrator", "sweeper"):
        refused = processes.start_worker(role, f"{role}-unavailable", wait=False)
        assert refused.wait(timeout=60) == 2  # the first fenced cycle fails: no start
    returning = processes.start_runtime_process("runtime-early")
    assert returning["process"].wait(timeout=90) == 3  # no session without the authority
    assert Processes.calls(early["counter"]) == 0
    with Session(w.engine) as db:
        delivered = db.scalar(
            select(func.count())
            .select_from(ExecutionAttempt)
            .where(ExecutionAttempt.execution_id == queued, ExecutionAttempt.state != "reserved")
        )
    assert delivered == 0
    assert operator.execution(customer, waiting)["state"] == "waiting"  # reads answer
    assert operator.events(customer, waiting)  # and export
    w.update("recovery", available=True)  # positive control: the authority returns
    processes.start_worker("orchestrator", "orchestrator-back")
    processes.start_worker("sweeper", "sweeper-back")
    # Restored, so held (owner decision, 2026-10-28). No runtime ran it after the
    # backup (runtime-early never started): its owner releases it.
    wait_for(lambda: held(w, queued), 120)
    customer.call("POST", f"/executions/{queued}/reconciliation", {})
    fresh = operator.runtime(customer, "runtime-fresh")  # enrolled under the new epoch
    assert operator.settled(customer, queued, {"succeeded", "failed"}, 120)["state"] == "succeeded"
    assert Processes.calls(fresh["counter"]) == 1
