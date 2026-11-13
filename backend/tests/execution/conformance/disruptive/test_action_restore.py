"""E05 Task 12 tier 2: reviewed writes across a restore (RESTORE-1 to RESTORE-5).

A restore stops every process but the synthetic target, returns the database to the
backup and advances the independent recovery epoch. The target's store, like the
runtimes' local state, lives outside the database: a restore never rolls a write
back. E04's hold applies unchanged: a restored execution and its intents wait for
their owner's release, and nothing is read back before it (decision 13, restated).
"""

from __future__ import annotations

import signal
import time

from processes import Processes
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionAttempt, ExecutionStepInstance

from ..actions.conftest import reviewed as reviewed  # noqa: F401
from ..actions.conftest import wait_until
from .test_restore import held


def restore(a, dump, *, epoch, available=True, workers=True):
    """Everything but the target stops; the database returns to `dump`."""
    processes = a.processes
    for name, process in list(processes.children.items()):
        if name != "target" and process.poll() is None:
            processes.stop(name)
    a.world.cluster.restore(dump)
    a.world.update("recovery", epoch=epoch, available=available)
    processes.start_api()
    if workers:
        processes.start_worker("orchestrator", f"orchestrator-e{epoch}", wait=False)
        processes.start_worker("sweeper", f"sweeper-e{epoch}", wait=False)
        processes.wait_ready(f"orchestrator-e{epoch}", f"sweeper-e{epoch}")


def write_attempts(a, execution_id):
    """The write (`#dispatch`) attempts, not the intent's or the review's."""
    with Session(a.world.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(ExecutionAttempt)
            .join(
                ExecutionStepInstance,
                ExecutionStepInstance.id == ExecutionAttempt.step_instance_id,
            )
            .where(
                ExecutionAttempt.execution_id == execution_id,
                ExecutionStepInstance.occurrence_path.like("%#dispatch"),
            )
        )


def release(a, execution_id, expect=(200,)):
    return a.customer.call("POST", f"/executions/{execution_id}/reconciliation", {}, expect=expect)


def approved_and_reserved(a):
    """An approved intent whose write attempt is reserved, with no runtime to take it."""
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.approve(execution_id)
    wait_until(lambda: write_attempts(a, execution_id) > 0, 60)
    return execution_id, record


def test_restore_1_a_write_after_the_backup_is_read_back_never_repeated(reviewed):
    a = reviewed
    execution_id, record = approved_and_reserved(a)
    dump = a.world.cluster.backup()
    a.runtime("runtime-a")
    a.settled(execution_id, seconds=180)
    assert len(a.writes(record)) == 1
    restore(a, dump, epoch=2)
    wait_until(lambda: held(a.world, execution_id), 120)
    fresh = a.runtime("runtime-b")  # enrolled under the new epoch
    time.sleep(6)
    # Held: no read-back before the owner's release.
    assert Processes.calls(fresh["counter"]) == 0
    release(a, execution_id)
    a.settled(execution_id, seconds=240)
    [action] = a.actions(execution_id)
    assert (action["effect"], action["outcome"]) == ("observed", "verified")
    assert len(a.writes(record)) == 1 and a.commands(execution_id) == []


def under_review_at_backup(a):
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.task_of(execution_id)
    return execution_id, record, a.world.cluster.backup()


def test_restore_2_a_review_at_backup_whose_write_happened_after(reviewed):
    """The backup holds the review open; afterwards a decision, a dispatch and a write.
    The older-epoch intent is held with its execution; after the release it is read
    back and never resent. As built (Task 10), a restored intent these rows never
    dispatched that reads back present fails `recovery_epoch_changed`: its rows lack
    the decision and dispatch that wrote it. The plan expected `observed`; the evidence
    document records the difference for the owner."""
    a = reviewed
    execution_id, record, dump = under_review_at_backup(a)
    a.runtime("runtime-a")
    a.approve(execution_id)
    a.settled(execution_id, seconds=180)
    assert len(a.writes(record)) == 1
    restore(a, dump, epoch=2)
    wait_until(lambda: held(a.world, execution_id), 120)
    release(a, execution_id)
    a.runtime("runtime-b")
    final = a.settled(execution_id, ("failed",), seconds=240)
    assert final.get("reason") == "recovery_epoch_changed"
    assert len(a.writes(record)) == 1 and a.commands(execution_id) == []


def test_restore_2_absent_a_review_at_backup_never_written(reviewed):
    a = reviewed
    execution_id, record, dump = under_review_at_backup(a)
    restore(a, dump, epoch=2)
    wait_until(lambda: held(a.world, execution_id), 120)
    release(a, execution_id)
    a.runtime("runtime-b")
    final = a.settled(execution_id, ("failed",), seconds=240)
    assert final.get("reason") == "recovery_epoch_changed"
    [action] = a.actions(execution_id)
    assert action["authorization"] == "superseded"
    assert a.writes(record) == [] and a.commands(execution_id) == []


def test_restore_3_a_restored_grant_cannot_authorize(reviewed):
    """Backup before a runtime revocation: the restored rows show it active, yet the
    restored approval dispatches nothing under the new epoch."""
    a = reviewed
    execution_id, record = approved_and_reserved(a)
    early = a.runtime("runtime-early", start=False)
    dump = a.world.cluster.backup()
    a.operator.revoke(a.customer, early["instance_id"])
    restore(a, dump, epoch=2)
    returning = a.processes.start_runtime_process("runtime-early")
    assert returning["process"].wait(timeout=90) == 3  # an older-epoch runtime is refused
    wait_until(lambda: held(a.world, execution_id), 120)
    release(a, execution_id)
    a.runtime("runtime-b")
    a.settled(execution_id, ("failed",), seconds=240)
    assert a.commands(execution_id) == [] and a.writes(record) == []


def test_restore_4_without_recovery_authority_only_reads_work(reviewed):
    a = reviewed
    execution_id, record = approved_and_reserved(a)
    early = a.runtime("runtime-early", start=False)
    dump = a.world.cluster.backup()
    restore(a, dump, epoch=2, available=False, workers=False)
    for role in ("orchestrator", "sweeper"):
        refused = a.processes.start_worker(role, f"{role}-unavailable", wait=False)
        assert refused.wait(timeout=60) == 2
    returning = a.processes.start_runtime_process("runtime-early")
    assert returning["process"].wait(timeout=90) == 3
    assert Processes.calls(early["counter"]) == 0
    # Reads and exports still answer; nothing dispatched, claimed or read back.
    assert a.actions(execution_id)[0]["authorization"] == "approved"
    assert a.operator.events(a.customer, execution_id)
    assert a.commands(execution_id) == [] and a.writes(record) == []
    # Positive control: the authority returns and the hold proceeds as RESTORE-1's.
    a.world.update("recovery", available=True)
    a.processes.start_worker("orchestrator", "orchestrator-back")
    a.processes.start_worker("sweeper", "sweeper-back")
    wait_until(lambda: held(a.world, execution_id), 120)


def test_restore_5_a_write_claimed_at_backup_blocks_the_release(reviewed):
    """The release is refused (`conflict`) until the write's stop is confirmed; then a
    read-back observes it: one write, no resend."""
    a = reviewed
    a.runtime("runtime-a")
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.fault("hang_after_apply")
    a.approve(execution_id)
    wait_until(lambda: a.writes(record), 60)  # claimed and applied; no answer yet
    dump = a.world.cluster.backup()
    a.processes.signal("runtime-a", signal.SIGKILL)
    restore(a, dump, epoch=2)
    wait_until(lambda: held(a.world, execution_id), 120)
    refused = release(a, execution_id, expect=(409,))
    assert refused["error"] == "conflict"
    [action] = a.actions(execution_id)
    a.resolve(a.customer, execution_id, action["action_id"], "compute_stopped")
    release(a, execution_id)
    a.runtime("runtime-b")
    a.settled(execution_id, seconds=240)
    [action] = a.actions(execution_id)
    assert action["effect"] == "observed"
    assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 1


def test_spike_1_7_a_review_survives_kills_and_an_immediate_restart(reviewed):
    """Spike checks 1-7 (tier 2): SIGKILL the orchestrator, sweeper and runtime during a
    review, then an immediate PostgreSQL restart. The intent's bytes and digest are
    unchanged; fresh processes finish with exactly one write of the reviewed values."""
    from lotuspond.models import ExecutionActionIntent

    def intent_of(execution_id):
        with Session(a.world.engine) as db:
            row = db.scalar(
                select(ExecutionActionIntent).where(
                    ExecutionActionIntent.execution_id == execution_id
                )
            )
            return row.intent_sha256, bytes(row.binding_bytes), row.target_sha256, row.input_sha256

    a = reviewed
    first = a.runtime("runtime-a")
    revision, record = a.deploy()
    execution_id = a.start(revision)
    task = a.task_of(execution_id)
    stored = intent_of(execution_id)
    for name in ("orchestrator-a", "sweeper-a", "runtime-a"):
        a.processes.signal(name, signal.SIGKILL)
    for name in ("orchestrator-a", "sweeper-a", "runtime-a"):
        a.processes.stop(name)
    a.world.cluster.immediate_restart()
    assert intent_of(execution_id) == stored
    a.processes.start_worker("orchestrator", "orchestrator-b", wait=False)
    a.processes.start_worker("sweeper", "sweeper-b", wait=False)
    a.processes.wait_ready("orchestrator-b", "sweeper-b")
    second = a.runtime("runtime-b")
    person = a.reviewer()
    a.claim(person, task["id"])
    assert a.decide(person, task["id"], "0" * 64, expect=(409,))["error"] == "conflict"
    a.decide(person, task["id"], stored[0])
    a.settled(execution_id, seconds=240)
    [write] = a.writes(record)
    assert write["input"] == {"amount_minor": 75000, "currency": "EUR"}
    [command] = a.commands(execution_id)
    from lotuspond.models import RuntimeCommandAdmission

    with Session(a.world.engine) as db:
        admission = db.get(RuntimeCommandAdmission, command.admission_id)
    assert admission.instance_id == second["instance_id"] != first["instance_id"]
