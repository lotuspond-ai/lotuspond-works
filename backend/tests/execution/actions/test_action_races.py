"""E05 Task 6: dispatch racing revocation, cancellation and renewal; the cursor a leaf."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.models import Execution, ExecutionActionCommand, ExecutionDeploymentRevision

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..cursor_checks import without_cursor
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import receipt_for, report
from .test_tasks import tasks as tasks  # noqa: F401


def race(*operations):
    barrier = Barrier(len(operations))

    def run(operation):
        barrier.wait(timeout=10)
        try:
            return operation()
        except Exception as error:
            return error

    with ThreadPoolExecutor(len(operations)) as pool:
        return list(pool.map(run, operations))


def no_deadlock(results):
    assert not any(isinstance(r, Exception) and "deadlock" in str(r).lower() for r in results)


def test_dispatch_races_revocation(actions):
    """Either dispatched first, or no command exists: never a deadlock."""
    h = actions
    revoked = h.reviewed("revoke")

    def revoke():
        with Session(h.engine) as db, db.begin():
            execution = db.get(Execution, revoked)
            db.get(
                ExecutionDeploymentRevision, execution.deployment_revision_id
            ).status = "disabled"

    no_deadlock(race(h.poll, revoke))


def test_dispatch_races_cancellation(actions):
    h = actions
    cancelled = h.reviewed("cancel")
    no_deadlock(race(h.poll, lambda: h.control(cancelled, "cancel")))


def test_renewal_races_a_receipt(actions):
    h = actions
    renewed = h.reviewed("renew")
    lease = h.claim(h.poll())

    def renew():
        with Session(h.engine) as db, db.begin():
            return h.dispatcher.renew(db, h.runtime, lease)

    no_deadlock(race(renew, lambda: report(h, receipt_for(h, renewed, lease))))


def test_action_paths_keep_the_cursor_a_leaf(actions):
    h = actions
    execution_id = h.reviewed("cursor")
    delivery = without_cursor(h.engine, h.poll)
    assert delivery is not None
    lease = without_cursor(h.engine, lambda: h.claim(delivery))

    def renew():
        with Session(h.engine) as db, db.begin():
            return h.dispatcher.renew(db, h.runtime, lease)

    without_cursor(h.engine, renew)
    without_cursor(h.engine, lambda: report(h, receipt_for(h, execution_id, lease)))
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionActionCommand)).status == "stopped"


# Task 10's lock orders: the restore hold, the owner's release and the cancellation
# each lock the execution before its attempts, intents, tasks and commands.


def _decide(h, execution_id, key):
    from lotuspond.execution.action_contracts import DecisionRequest

    from .dispatch_fixtures import intent_row, task_id

    task = h.call("get", h.actor, task_id(h, execution_id))
    reviewer = h.reviewer()
    h.call("claim", reviewer, task.id)
    sha = intent_row(h, execution_id).intent_sha256

    def decide():
        return h.call(
            "decide",
            reviewer,
            task.id,
            DecisionRequest(decision_id="d-" + key, intent_sha256=sha, decision="allow"),
        )

    return decide


def test_the_restore_hold_races_a_task_decision(actions):
    """The sweeper holds a restored execution while a reviewer decides its task:
    never a deadlock, and nothing is dispatched under the new epoch either way."""
    from .test_action_recovery import commands, restored

    h = actions
    execution_id = h.reviewed("hold-decide", decide=False)
    decide = _decide(h, execution_id, "hold-decide")
    restored(h)
    no_deadlock(race(h.sweep, decide))
    h.run2()
    assert h.poll() is None and commands(h, execution_id) == []


def test_the_release_races_a_cancellation(actions):
    """An owner releases a held execution while another owner cancels it."""
    from .readback_fixtures import Target, execution
    from .test_action_recovery import accepted_before_verification, release, restored

    h = actions
    execution_id = accepted_before_verification(h, "release-cancel", Target())
    restored(h)
    h.sweep()
    h.run2()
    no_deadlock(race(lambda: release(h, execution_id), lambda: h.control(execution_id, "cancel")))
    h.run2()
    assert execution(h, execution_id).recovery_epoch in (None, 2)


def test_the_release_races_a_runtime_poll(actions):
    """The release reloads the execution under lock while a runtime polls for
    commands: never a deadlock, and no old-epoch command is offered."""
    from .readback_fixtures import Target
    from .test_action_recovery import accepted_before_verification, release, restored

    h = actions
    execution_id = accepted_before_verification(h, "release-poll", Target())
    restored(h)
    h.sweep()
    h.run2()
    results = race(lambda: release(h, execution_id), h.poll)
    no_deadlock(results)
    assert results[1] is None or isinstance(results[1], Exception)


def test_cancel_containment_races_a_readback_report(actions):
    """A restored intent's read-back reports while the cancellation's containment
    derives over the same execution: never a deadlock, and it ends cancelled."""
    from .readback_fixtures import Target, execution, pending_readback, read
    from .test_action_recovery import (
        accepted_before_verification,
        present_for,
        release,
        restored,
    )

    h = actions
    target = Target()
    execution_id = accepted_before_verification(h, "contain-report", target)
    restored(h)
    h.sweep()
    h.run2()
    release(h, execution_id)
    h.control(execution_id, "cancel")
    h.run2()
    attempt = pending_readback(h, execution_id)
    no_deadlock(race(lambda: read(h, attempt.id, present_for(target, h, attempt.id)), h.run2))
    for _ in range(3):
        h.run2()
    assert execution(h, execution_id).state == "cancelled"
