"""Task 11: the sweeper takes due work item by item, each once, in its own transaction.

Due work comes in real database time, from fixtures with short timers, expiries,
leases and deadlines; no deadline, lease or expiry column is ever edited.
"""

import json
from threading import Barrier, Thread
from time import sleep

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution import sweeper as sweeper_module
from lotuspond.execution.engine_contracts import SweepReport
from lotuspond.execution.orchestrator import clear_runnable, mark_runnable
from lotuspond.execution.progression import wait_key
from lotuspond.execution.sweeper import ExecutionSweeper, queue_depths
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionLease,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    ExecutionWait,
)

from .activity_fixtures import activity as activity
from .control_fixtures import ORCHESTRATOR, own_sweeper, until
from .control_fixtures import controlled as controlled
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_progression import flows as flows
from .test_publication import harness as harness

QUIET = SweepReport(timers=0, expired_waits=0, deadlines=0, lapsed_leases=0, expired_deliveries=0)


def wait_of(h, execution_id, path):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionWait).where(
                ExecutionWait.execution_id == execution_id, ExecutionWait.wait_key == wait_key(path)
            )
        )


def lease_of(h, attempt_id):
    with Session(h.engine) as db:
        return db.scalar(select(ExecutionLease).where(ExecutionLease.attempt_id == attempt_id))


def delivery_of(h, attempt_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionActivityDelivery).where(
                ExecutionActivityDelivery.attempt_id == attempt_id
            )
        )


def execution(h, execution_id):
    with Session(h.engine) as db:
        return db.get(Execution, execution_id)


def paused_overdue(h):
    """A paused execution with nothing left due; its 4 s deadline still runs."""
    execution_id, _ = h.ready("activity_deadline", active_ms=1000)
    h.control(execution_id, "pause")
    h.run()
    row = execution(h, execution_id)
    assert (row.state, row.runnable_at_ms) == ("paused", None)
    return execution_id


def recorded(h, execution_id, path):
    with Session(h.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(ExecutionAttempt)
            .join(
                ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id
            )
            .where(
                ExecutionStepInstance.execution_id == execution_id,
                ExecutionStepInstance.occurrence_path == path,
                ExecutionAttempt.state == "succeeded",
            )
        )


def test_sweeper_items_are_independent_and_idempotent(controlled, monkeypatch):
    h = controlled
    # `expired` moves on to its external wait: its timer through the sweeper itself.
    expired = h.start("waits_fast", None, "waits.policy", key="expired")
    h.run()
    until(h, wait_of(h, expired, "pause").deadline_ms)
    assert h.sweep().timers == 1
    h.run()
    assert wait_of(h, expired, "callback").state == "open"
    # Paused, with its 5 s deadline (and so its timer) still running. Started only
    # now, so however slow the machine, its deadline cannot fall due before this.
    late = h.start("waits_deadline", None, "waits.policy", key="late")
    h.run()
    h.control(late, "pause")
    h.run()
    assert execution(h, late).state == "paused"
    # One of each due kind, each due by its own clock.
    timer = h.start("waits_fast", None, "waits.policy", key="timer")
    h.run()
    _, lapsing = h.ready("activity_read", capacity=4, active_ms=1500)
    h.claim(h.dispatch(lapsing))
    _, unclaimed = h.ready("activity_read", capacity=4)
    delivery_id = json.loads(h.dispatch(unclaimed).envelope)["delivery_id"]
    _, spent = h.ready("activity_read", capacity=4, active_ms=1)
    until(
        h,
        max(
            wait_of(h, timer, "pause").deadline_ms,
            wait_of(h, expired, "callback").deadline_ms,
            lease_of(h, lapsing).expires_at_ms,
            delivery_of(h, unclaimed).expires_at_ms,
            execution(h, late).deadline_ms,
        ),
    )
    # One item fails; every other item is still swept, each in its own transaction.
    original = h.leases.lapse_lease

    def broken(db, lease_id, *, epoch=None):
        raise RuntimeError("PRIVATE_SWEEP_FAILURE")

    monkeypatch.setattr(h.leases, "lapse_lease", broken)
    first = h.sweep()
    assert first == QUIET.model_copy(
        update=dict(timers=2, expired_waits=1, expired_deliveries=1, retired_reservations=1)
    )
    with Session(h.engine) as db:
        assert db.get(ExecutionWait, wait_of(h, timer, "pause").id).state == "satisfied"
        assert db.get(ExecutionWait, wait_of(h, expired, "callback").id).state == "expired"
        assert db.get(Execution, late).runnable_at_ms is not None
        assert db.get(ExecutionAttempt, unclaimed).state == "failed"
        assert db.get(ExecutionActivityDelivery, delivery_id).status == "conflict"
        assert db.get(ExecutionAttempt, spent).state == "failed"
        assert db.get(ExecutionAttempt, lapsing).state == "claimed"  # its item failed alone
    monkeypatch.setattr(h.leases, "lapse_lease", original)
    assert h.sweep() == QUIET.model_copy(update=dict(lapsed_leases=1))
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, lapsing).state == "uncertain"
    # Swept once: sweeping again changes nothing.
    second_sweep_changes = sum(h.sweep().model_dump().values())
    assert second_sweep_changes == 0
    # Progression follows each path: the timer moves on, the expired external wait
    # follows its expiry path, and the overdue paused execution stops at its deadline.
    h.run()
    with Session(h.engine) as db:
        assert wait_of(h, timer, "callback").state == "open"
        assert db.get(Execution, expired).state == "failed"
        late_row = db.get(Execution, late)
        assert (late_row.state, late_row.reason) == ("failed", "deadline_exceeded")


def test_timer_and_wait_deadlines_survive_restart(controlled):
    from .test_progression import fresh_engine

    h = controlled
    execution_id = h.start("waits_fast", None, "waits.policy")
    h.run()
    original_deadline_ms = wait_of(h, execution_id, "pause").deadline_ms
    with Session(h.engine) as db:
        early = database_now_ms(db) < original_deadline_ms
    assert early and h.sweep() == QUIET  # nothing is due yet
    # The timer's own deadline passes; a restarted sweeper and orchestrator follow.
    until(h, original_deadline_ms)
    restarted = ExecutionSweeper(leases=h.leases, recovery=h.fence)
    assert h.sweep(sweeper=restarted).timers == 1
    assert h.sweep(sweeper=ExecutionSweeper(leases=h.leases, recovery=h.fence)) == QUIET
    engine = fresh_engine(h)
    for _ in range(50):
        if engine.progress(h.sessions, ORCHESTRATOR, limit=10).claimed == 0:
            break
    with Session(h.engine) as db:
        timer = db.get(ExecutionWait, wait_of(h, execution_id, "pause").id)
        # It fired once, at the deadline it was given, which no restart moved.
        assert (timer.state, timer.deadline_ms) == ("satisfied", original_deadline_ms)
    assert recorded(h, execution_id, "pause") == 1  # the timer step recorded exactly once
    assert wait_of(h, execution_id, "callback").state == "open"


def test_a_sweeper_killed_mid_batch_keeps_what_it_finished(controlled, monkeypatch):
    h = controlled
    first = h.start("waits_fast", None, "waits.policy", key="first")
    second = h.start("waits_fast", None, "waits.policy", key="second")
    h.run()
    until(h, max(wait_of(h, e, "pause").deadline_ms for e in (first, second)))
    original = ExecutionSweeper._wait
    seen = []

    def dies_on_the_second(self, db, wait_id, epoch):
        if seen:
            raise KeyboardInterrupt  # the process is killed between items
        seen.append(wait_id)
        return original(self, db, wait_id, epoch)

    monkeypatch.setattr(ExecutionSweeper, "_wait", dies_on_the_second)
    with pytest.raises(KeyboardInterrupt):
        h.sweep()
    monkeypatch.setattr(ExecutionSweeper, "_wait", original)
    states = {wait_of(h, e, "pause").state for e in (first, second)}
    assert states == {"satisfied", "open"}  # the finished item stayed committed
    assert h.sweep(sweeper=ExecutionSweeper(leases=h.leases, recovery=h.fence)).timers == 1
    assert {wait_of(h, e, "pause").state for e in (first, second)} == {"satisfied"}


def test_queue_depths_keep_unknown_distinct_from_zero(controlled, monkeypatch):
    h = controlled
    with Session(h.engine) as db, db.begin():
        idle = queue_depths(db, h.fence.current(db))
    assert set(idle.model_dump().values()) == {0}
    execution_id = h.start("waits_fast", None, "waits.policy")
    h.run()
    until(h, wait_of(h, execution_id, "pause").deadline_ms)
    h.ready("activity_read", active_ms=1)
    sleep(0.01)
    with Session(h.engine) as db, db.begin():
        depths = queue_depths(db, h.fence.current(db))
    assert (depths.due_waits, depths.spent_reservations, depths.restored_attempts) == (1, 1, 0)

    def unreadable():
        raise RuntimeError("PRIVATE_COUNT_FAILURE")

    monkeypatch.setattr(sweeper_module, "_spent", unreadable)
    with Session(h.engine) as db, db.begin():
        depths = queue_depths(db, h.fence.current(db))
    # The count that cannot be read is unknown, never zero; the others are still read.
    assert depths.spent_reservations is None and depths.due_waits == 1
    monkeypatch.undo()
    # A pass keeps the depths it read after its items, for the worker's metrics.
    assert h.sweeper.depths is None
    report = h.sweep()
    assert (report.timers, report.retired_reservations) == (1, 1)
    assert (h.sweeper.depths.due_waits, h.sweeper.depths.spent_reservations) == (0, 0)


def test_no_wake_up_outlasts_the_deadline(controlled):
    h = controlled
    # A retryable failure whose declared back-off (an hour) ends long after the
    # execution's 4 s deadline: progression wakes it at the deadline instead.
    execution_id, attempt_id = h.ready("activity_backoff", active_ms=3000)
    h.result(attempt_id, h.claim(h.dispatch(attempt_id)), code="unavailable")
    h.run()
    row = execution(h, execution_id)
    assert row.state == "running" and row.runnable_at_ms == row.deadline_ms
    until(h, row.deadline_ms)
    h.run()
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "deadline_exceeded")


def test_an_overdue_execution_marked_too_late_is_made_due(controlled):
    h = controlled
    execution_id = paused_overdue(h)
    until(h, execution(h, execution_id).deadline_ms)

    def marked(delay_ms):
        with Session(h.engine) as db, db.begin():
            row = db.get(Execution, execution_id, with_for_update=True)
            clear_runnable(db, row)
            mark_runnable(db, row, database_now_ms(db) + delay_ms)

    # Within the engine's longest back-off (60 s): the back-off is never cut short.
    marked(30000)
    assert h.sweep().deadlines == 0
    # Later than any back-off (a marker no capped wake-up sets): made due now.
    marked(3_600_000)
    assert h.sweep().deadlines == 1
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).runnable_at_ms <= database_now_ms(db)
    h.run()
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "deadline_exceeded")


def test_concurrent_sweepers_take_each_item_once(controlled):
    h = controlled
    executions = [h.start("waits_fast", None, "waits.policy", key=f"t{n}") for n in range(6)]
    h.run()
    until(h, max(wait_of(h, e, "pause").deadline_ms for e in executions))
    start, reports = Barrier(2), []

    def sweep(sweeper):
        start.wait(10)
        reports.append(h.sweep(sweeper=sweeper))

    threads = [Thread(target=sweep, args=(own_sweeper(h),)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    # Every timer fired exactly once between the two, whichever took it.
    assert len(reports) == 2 and sum(report.timers for report in reports) == 6
    assert {wait_of(h, e, "pause").state for e in executions} == {"satisfied"}
    assert h.sweep() == QUIET


def test_the_sweeper_and_orchestrator_race_without_double_effects(controlled):
    h = controlled
    executions = [h.start("waits_fast", None, "waits.policy", key=f"r{n}") for n in range(4)]
    h.run()
    until(h, max(wait_of(h, e, "pause").deadline_ms for e in executions))
    start, errors, other = Barrier(2), [], own_sweeper(h)

    def sweeper():
        start.wait(10)
        try:
            for _ in range(5):
                h.sweep(sweeper=other)
        except Exception as error:
            errors.append(error)

    def orchestrator():
        start.wait(10)
        try:
            for _ in range(5):
                h.run(rounds=20)
        except Exception as error:
            errors.append(error)

    threads = [Thread(target=sweeper), Thread(target=orchestrator)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert errors == []
    # Whatever either skipped while the other held a row, a later pass finishes.
    h.sweep()
    h.run()
    for execution_id in executions:
        assert recorded(h, execution_id, "pause") == 1
        assert wait_of(h, execution_id, "callback").state == "open"


def test_an_item_whose_rows_are_held_is_deferred_not_waited_on(controlled):
    h = controlled
    timer = h.start("waits_fast", None, "waits.policy")
    h.run()
    lapsing_execution, lapsing = h.ready("activity_read", active_ms=1500)
    h.claim(h.dispatch(lapsing))
    until(h, max(wait_of(h, timer, "pause").deadline_ms, lease_of(h, lapsing).expires_at_ms))
    held, release = Barrier(2), Barrier(2)

    def hold():
        # Another transaction (say, a slow tenant request) holds the execution row.
        with Session(h.engine) as db, db.begin():
            db.get(Execution, lapsing_execution, with_for_update=True)
            held.wait(10)
            release.wait(30)

    holder = Thread(target=hold)
    holder.start()
    held.wait(10)
    try:
        with Session(h.engine) as db:
            started = database_now_ms(db)
        report = h.sweep()
        with Session(h.engine) as db:
            elapsed = database_now_ms(db) - started
    finally:
        release.wait(10)
        holder.join(10)
    # The other item went through; the held one waited at most its lock timeout.
    assert (report.timers, report.lapsed_leases) == (1, 0)
    assert elapsed < 3000
    assert h.sweep().lapsed_leases == 1  # a later pass takes it


def test_a_sweep_pass_reserves_the_next_attempts_through_the_scheduler(controlled):
    """Task 13: nothing else drives Task 5's `schedule_batch`, so the sweeper does.

    Without it no attempt is ever reserved outside tests, and none reaches a runtime.
    """
    from lotuspond.execution.contracts import AdmissionRequest
    from lotuspond.execution.scheduler import Scheduler

    from .activity_fixtures import Demand, attempt_at, deploy

    h = controlled
    revision = deploy(h, "activity_read")
    with Session(h.engine) as db, db.begin():
        execution_id = h.service.admit_execution(
            db, h.actor, AdmissionRequest(deployment_revision_id=revision, input=None), "sched"
        ).id
    h.run()
    attempt_id = attempt_at(h, execution_id, "read")

    def reserved():
        with Session(h.engine) as db:
            return db.scalar(
                select(func.count())
                .select_from(ExecutionUsageReservation)
                .where(ExecutionUsageReservation.attempt_id == attempt_id)
            )

    # The plain sweeper reserves nothing: the attempt would wait forever.
    assert h.sweep().scheduled_attempts == 0 and reserved() == 0
    scheduling = ExecutionSweeper(
        leases=h.leases,
        recovery=h.fence,
        scheduler=Scheduler(h.budgets, Demand(), test_mode=True),
    )
    assert h.sweep(sweeper=scheduling).scheduled_attempts == 1
    assert reserved() == 1
    # Once reserved it is no longer a candidate: a repeat pass reserves nothing more.
    assert h.sweep(sweeper=scheduling).scheduled_attempts == 0 and reserved() == 1
    h.dispatch(attempt_id)  # the reservation lets a runtime receive it


def test_an_idle_scheduling_pass_takes_no_cursor_lock(controlled, caplog):
    """Task 13: with nothing to reserve, the scheduler never queues on the cursor."""
    import logging
    import time

    from lotuspond.execution.scheduler import Scheduler
    from lotuspond.models import ExecutionSchedulerCursor

    from .activity_fixtures import Demand

    h = controlled
    scheduling = ExecutionSweeper(
        leases=h.leases,
        recovery=h.fence,
        scheduler=Scheduler(h.budgets, Demand(), test_mode=True),
    )
    with Session(h.engine) as holder, holder.begin():
        holder.scalar(select(ExecutionSchedulerCursor).with_for_update())  # held throughout
        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="lotuspond.execution"):
            assert h.sweep(sweeper=scheduling).scheduled_attempts == 0
        assert time.monotonic() - started < 5  # it never waited behind the holder
        assert "scheduling deferred" not in caplog.text  # nor tried for the lock at all


@pytest.mark.parametrize(
    ("configured", "effective"), [("200ms", "200ms"), ("15s", "500ms"), ("0", "500ms")]
)
def test_a_sweep_item_only_ever_lowers_the_worker_lock_timeout(
    controlled, monkeypatch, configured, effective
):
    """Whole-branch review: an operator's lower lock timeout is never raised."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from .control_fixtures import SWEEPER

    h = controlled
    with Session(h.engine) as db:
        schema = db.scalar(text("SELECT current_schema()"))
    # A worker's connections carry its configured timeout as a session setting.
    worker = create_engine(
        h.engine.url,
        connect_args={"options": f"-c search_path={schema} -c lock_timeout={configured}"},
        hide_parameters=True,
    )
    seen = []

    def item(self, kind, db, identifier, epoch):
        seen.append(db.scalar(text("SHOW lock_timeout")))

    monkeypatch.setattr(
        ExecutionSweeper, "_candidates", lambda self, sessions, limit: {"waits": ["probe"]}
    )
    monkeypatch.setattr(ExecutionSweeper, "_item", item)
    try:
        h.sweeper.sweep(sessionmaker(worker), SWEEPER, limit=100)
        with worker.connect() as connection:  # positive control: the session's own value
            assert connection.scalar(text("SHOW lock_timeout")) == configured
    finally:
        worker.dispose()
    assert seen == [effective]
