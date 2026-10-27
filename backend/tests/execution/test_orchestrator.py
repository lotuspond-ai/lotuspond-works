"""Row-locked claims, in-transaction wake-up consumption and the orchestrator lock order."""

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lotuspond.execution.budgets import BudgetDemand
from lotuspond.execution.contracts import SignalRequest
from lotuspond.execution.engine import RETRY_MS
from lotuspond.execution.engine_contracts import INTERPRETER_V1, ProgressReport, WorkerIdentity
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.orchestrator import (
    claim_runnable,
    clear_runnable,
    consume_wakeups,
    mark_runnable,
)
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionCapacity,
    ExecutionOutbox,
    ExecutionStepInstance,
    Workspace,
)

from .engine_fixtures import FixtureProgression, fixture_engine, relation_locks
from .test_admission import admission as admission
from .test_admission import admit
from .test_budgets import budget_harness as budget_harness
from .test_inbox import accept
from .test_inbox import signals as signals
from .test_publication import harness as harness

WORKER = WorkerIdentity(
    worker_id="orchestrator-a", role="orchestrator", interpreter_ids=[INTERPRETER_V1]
)
OTHER = WorkerIdentity(
    worker_id="orchestrator-b",
    role="orchestrator",
    interpreter_ids=["lotuspond.execution-interpreter.v2"],
)


def sessions(h):
    return sessionmaker(h.engine, expire_on_commit=False)


def locked(db, execution_id):
    return db.scalar(
        select(Execution)
        .where(Execution.id == execution_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def outbox(db, execution_id, kind="engine_start"):
    return db.scalar(
        select(ExecutionOutbox)
        .where(ExecutionOutbox.execution_id == execution_id, ExecutionOutbox.kind == kind)
        .execution_options(populate_existing=True)
    )


def test_admission_and_signal_mark_runnable(signals):
    h = signals
    with Session(h.engine) as db:
        execution = db.get(Execution, h.execution_id)
        start = outbox(db, h.execution_id)
        assert execution.runnable_at_ms == start.available_at_ms == execution.admitted_at_ms
    with Session(h.engine) as db, db.begin():
        clear_runnable(db, locked(db, h.execution_id))
    accept(h)
    with Session(h.engine) as db:
        wakeup = outbox(db, h.execution_id, "engine_signal")
        assert db.get(Execution, h.execution_id).runnable_at_ms == wakeup.available_at_ms
    accept(h)  # an equal replay neither duplicates the wake-up nor moves the marker
    with Session(h.engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExecutionOutbox)
                .where(ExecutionOutbox.kind == "engine_signal")
            )
            == 1
        )
    with Session(h.engine) as db, db.begin():
        execution = locked(db, h.execution_id)
        mark_runnable(db, execution, wakeup.available_at_ms + 5000)
        assert execution.runnable_at_ms == wakeup.available_at_ms  # the earliest request wins
        mark_runnable(db, execution, wakeup.available_at_ms - 1)
        assert execution.runnable_at_ms == wakeup.available_at_ms - 1
        for invalid in (-1, 9007199254740992, True, 1.0):
            with pytest.raises(ExecutionError, match="invalid_request"):
                mark_runnable(db, execution, invalid)


def test_mark_runnable_requires_the_callers_row_lock_and_a_live_execution(admission):
    h = admission
    execution_id = admit(h).id
    holder = Session(h.engine)
    holder.begin()
    locked(holder, execution_id)
    try:
        with Session(h.engine) as db:
            db.begin()
            unlocked = db.get(Execution, execution_id)
            # NOWAIT makes a broken caller contract visible instead of waiting.
            with pytest.raises(ExecutionError, match="authority_unavailable"):
                mark_runnable(db, unlocked, 0)
            db.rollback()
    finally:
        holder.rollback()
        holder.close()
    with Session(h.engine) as db, db.begin():
        execution = locked(db, execution_id)
        clear_runnable(db, execution)
        execution.state = "succeeded"
        db.flush()
        with pytest.raises(ExecutionError, match="conflict"):
            mark_runnable(db, execution, 0)


def test_runnable_marker_constraints(admission):
    h = admission
    execution_id = admit(h).id
    for statement in (
        "UPDATE executions SET runnable_at_ms = -1",
        "UPDATE executions SET runnable_at_ms = 9007199254740992",
        "UPDATE executions SET state = 'cancelled'",
    ):
        with Session(h.engine) as db, pytest.raises(IntegrityError), db.begin():
            db.execute(text(statement))
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "pending"


def test_claim_skips_locked_and_unsupported_interpreters(admission):
    h = admission
    execution_id = admit(h).id
    with Session(h.engine) as db:
        assert claim_runnable(db, OTHER) is None  # interpreter not implemented by this worker
        db.rollback()
    barrier, results = Barrier(2), {}

    def race(name):
        worker = WORKER.model_copy(update={"worker_id": name})
        with Session(h.engine) as db, db.begin():
            barrier.wait(timeout=10)
            row = claim_runnable(db, worker)
            results[name] = row.id if row else None
            barrier.wait(timeout=10)  # hold the claim until both workers have tried

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(race, ("a", "b")))
    claims = [value for value in results.values() if value is not None]
    second_worker_claim = next(value for value in results.values() if value != execution_id)
    assert claims == [execution_id]
    assert second_worker_claim is None
    with Session(h.engine) as db, db.begin():
        execution = locked(db, execution_id)
        clear_runnable(db, execution)
        mark_runnable(db, execution, database_now_ms(db) + 60000)
    with Session(h.engine) as db, db.begin():
        assert claim_runnable(db, WORKER) is None  # not due yet
    with Session(h.engine) as db, db.begin():
        execution = locked(db, execution_id)
        mark_runnable(db, execution, database_now_ms(db))
    with Session(h.engine) as db, db.begin():
        assert claim_runnable(db, WORKER).id == execution_id  # positive control
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        claim_runnable(db, WORKER.model_copy(update={"role": "sweeper"}))


def test_wakeups_consumed_only_with_the_transition(admission, caplog):
    h = admission
    execution_id = admit(h).id
    with Session(h.engine) as db:
        transaction = db.begin()
        row = claim_runnable(db, WORKER)
        assert consume_wakeups(db, row) == ("engine_start",)
        clear_runnable(db, row)
        transaction.rollback()
    with Session(h.engine) as db:
        outbox_status_after_rollback = outbox(db, execution_id).status
        assert outbox_status_after_rollback == "pending"
        assert db.get(Execution, execution_id).runnable_at_ms is not None
    failing = FixtureProgression(fail=True)
    with caplog.at_level(logging.WARNING, logger="lotuspond.execution"):
        with Session(h.engine) as db:
            before = database_now_ms(db)
        report = fixture_engine(failing).progress(sessions(h), WORKER, limit=5)
    assert report == ProgressReport(claimed=1, transitions=0, deferred=1)
    assert "PRIVATE_PROGRESSION_FAILURE" not in caplog.text
    assert "Execution transition deferred: unexpected" in caplog.text
    with Session(h.engine) as db:
        assert outbox(db, execution_id).status == "pending"
        assert db.get(Execution, execution_id).runnable_at_ms >= before + RETRY_MS
    with Session(h.engine) as db, db.begin():
        execution = locked(db, execution_id)
        clear_runnable(db, execution)
        mark_runnable(db, execution, database_now_ms(db))
    progression = FixtureProgression()
    report = fixture_engine(progression).progress(sessions(h), WORKER, limit=5)
    assert report == ProgressReport(claimed=1, transitions=1, deferred=0)
    # Consumed in the same transaction, before the transition ran.
    assert progression.calls[0]["pending_wakeups"] is None
    with Session(h.engine) as db:
        start = outbox(db, execution_id)
        assert (start.status, start.delivery_count) == ("delivered", 1)
        assert (start.lease_token_digest, start.leased_at_ms, start.lease_expires_at_ms) == (
            None,
            None,
            None,
        )
        assert db.get(Execution, execution_id).runnable_at_ms is None
    assert fixture_engine(progression).progress(sessions(h), WORKER, limit=5).claimed == 0
    continuing = FixtureProgression(runnable=True)
    with Session(h.engine) as db, db.begin():
        mark_runnable(db, locked(db, execution_id), database_now_ms(db))
    report = fixture_engine(continuing).progress(sessions(h), WORKER, limit=1)
    assert report == ProgressReport(claimed=1, transitions=1, deferred=1)
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).runnable_at_ms is not None


def test_pure_transition_never_locks_capacity_parents(admission):
    h = admission
    admit(h)
    progression = FixtureProgression()
    assert fixture_engine(progression).progress(sessions(h), WORKER, limit=1).claimed == 1
    locked_parents_during_pure_transition = progression.locks[0]
    assert locked_parents_during_pure_transition == set()
    # Positive control: the same observation sees Task 5's ordered capacity locks.
    with Session(h.engine) as db, db.begin():
        h.budgets._cursor(db)
        db.scalar(select(ExecutionCapacity).with_for_update())
        assert relation_locks(db) >= {"execution_scheduler_cursors", "execution_capacities"}


class CapacityFailure(FixtureProgression):
    def apply_capacity(self, db, worker, execution_id, *, epoch=None):
        super().apply_capacity(db, worker, execution_id)
        raise ExecutionError("capacity_unavailable")


def test_capacity_step_runs_in_its_own_transaction(admission, caplog):
    h = admission
    execution_id = admit(h).id
    progression = FixtureProgression(runnable=True, reason="capacity")
    report = fixture_engine(progression).progress(sessions(h), WORKER, limit=1)
    assert report == ProgressReport(claimed=1, transitions=1, deferred=1)
    assert progression.capacity_calls[0]["execution_id"] == execution_id
    assert progression.capacity_calls[0]["txid"] != progression.calls[0]["txid"]
    with Session(h.engine) as db:
        # The pure pass deferred the marker; the capacity step itself changed nothing.
        assert db.get(Execution, execution_id).runnable_at_ms > database_now_ms(db)
    failing = CapacityFailure(runnable=True, reason="capacity")
    with Session(h.engine) as db, db.begin():
        execution = locked(db, execution_id)
        clear_runnable(db, execution)
        mark_runnable(db, execution, database_now_ms(db))
        before = database_now_ms(db)
    with caplog.at_level(logging.WARNING, logger="lotuspond.execution"):
        fixture_engine(failing).progress(sessions(h), WORKER, limit=1)
    assert "Execution capacity step deferred: capacity_unavailable" in caplog.text
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).runnable_at_ms >= before + RETRY_MS


class StepWriter(FixtureProgression):
    """A representative pure transition: writes an execution child row, no parents."""

    def advance_execution(self, db, worker, execution, *, bound, epoch=None):
        outcome = super().advance_execution(db, worker, execution, bound=bound, epoch=epoch)
        db.add(
            ExecutionStepInstance(
                workspace_id=execution.workspace_id,
                project_id=execution.project_id,
                environment_id=execution.environment_id,
                execution_id=execution.id,
                occurrence_path=f"race/{len(self.calls)}",
                node_id="race",
            )
        )
        db.flush()
        return outcome


def test_claims_race_admission_and_reservation_without_deadlock(admission):
    h = admission
    stop, errors, admitted = Event(), [], []
    progression = StepWriter()
    engine = fixture_engine(progression)
    reserved = [admit(h, f"reserve-{index}").id for index in range(4)]
    with Session(h.engine) as db, db.begin():
        attempt_ids = [h.add(db, execution_id)[0] for execution_id in reserved]

    def orchestrate():
        while not stop.is_set():
            try:
                engine.progress(sessions(h), WORKER, limit=10)
            except Exception as error:  # pragma: no cover - reported below
                errors.append(("orchestrator", getattr(error, "code", type(error).__name__)))

    def admissions():
        # The deployment allows 10 pending executions; 4 are already admitted above.
        for index in range(6):
            try:
                admitted.append(admit(h, f"race-{index}").id)
            except ExecutionError as error:
                errors.append(("admission", error.code))

    def reservations():
        demand = BudgetDemand(input_tokens=1, output_tokens=1, cost_microusd=1, active_ms=1)
        for attempt_id in attempt_ids:
            try:
                with Session(h.engine) as db, db.begin():
                    h.budgets.reserve_attempt(db, h.actor, attempt_id, demand)
            except ExecutionError as error:
                if error.code != "capacity_unavailable":
                    errors.append(("reservation", error.code))

    with ThreadPoolExecutor(4) as pool:
        workers = [pool.submit(orchestrate), pool.submit(orchestrate)]
        pool.submit(admissions).result(timeout=120)
        pool.submit(reservations).result(timeout=120)
        stop.set()
        for worker in workers:
            worker.result(timeout=60)
    engine.progress(sessions(h), WORKER, limit=50)
    assert errors == []
    with Session(h.engine) as db:
        assert len(admitted) == 6
        admitted += reserved
        pending = db.scalar(
            select(func.count())
            .select_from(ExecutionOutbox)
            .where(
                ExecutionOutbox.execution_id.in_(admitted), ExecutionOutbox.status != "delivered"
            )
        )
        assert pending == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(Execution)
                .where(Execution.runnable_at_ms.is_not(None))
            )
            == 0
        )
    assert {call["execution_id"] for call in progression.calls} >= set(admitted)


def test_signal_wakeups_race_the_orchestrator_without_loss_or_deadlock(signals):
    h = signals
    stop, errors = Event(), []
    progression = FixtureProgression()
    engine = fixture_engine(progression)

    def orchestrate():
        while not stop.is_set():
            try:
                engine.progress(sessions(h), WORKER, limit=10)
            except Exception as error:  # pragma: no cover - reported below
                errors.append(getattr(error, "code", type(error).__name__))

    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(orchestrate)
        for index in range(15):
            accept(h, SignalRequest(signal_id=f"race-{index}", wait_id=h.wait_id, payload=None))
        stop.set()
        running.result(timeout=60)
    engine.progress(sessions(h), WORKER, limit=50)
    assert errors == []
    with Session(h.engine) as db:
        statuses = list(
            db.scalars(
                select(ExecutionOutbox.status).where(ExecutionOutbox.kind == "engine_signal")
            )
        )
        assert len(statuses) == 15 and set(statuses) == {"delivered"}
        assert db.get(Execution, h.execution_id).runnable_at_ms is None


def test_locked_workspace_is_skipped_not_waited_on(admission):
    """One tenant's API transaction cannot stall claims of due work elsewhere."""
    from tests.fleet.test_schema import load

    from .test_schema import execution_rows

    h = admission
    head = admit(h).id
    other = execution_rows(5)
    worker = WORKER.model_copy(update={"interpreter_ids": (INTERPRETER_V1, "fixture")})
    with Session(h.engine) as db, db.begin():
        load(db.connection(), other)
    second = other["executions"]["id"]
    with Session(h.engine) as db, db.begin():
        mark_runnable(db, locked(db, second), database_now_ms(db))
    with Session(h.engine) as blocker, blocker.begin():
        blocker.execute(
            select(Workspace.id).where(Workspace.id == h.scope.workspace_id).with_for_update()
        )
        with Session(h.engine) as db, db.begin():
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            claimed = claim_runnable(db, worker)  # would wait, then raise, if it blocked
            assert claimed.id == second
    with Session(h.engine) as db, db.begin():
        assert claim_runnable(db, worker).id == head  # positive control: head when free


def test_a_locked_tenant_filling_the_candidate_window_hides_no_other_tenant(admission, monkeypatch):
    """Whole-branch review: a claim reads past a locked workspace's due work."""
    from lotuspond.execution import orchestrator
    from tests.fleet.test_schema import load

    from .test_schema import execution_rows

    h = admission
    head = admit(h).id
    other = execution_rows(5)
    worker = WORKER.model_copy(update={"interpreter_ids": (INTERPRETER_V1, "fixture")})
    with Session(h.engine) as db, db.begin():
        load(db.connection(), other)
    second = other["executions"]["id"]
    with Session(h.engine) as db, db.begin():
        mark_runnable(db, locked(db, second), database_now_ms(db))
    # The locked tenant's due work fills the whole round.
    monkeypatch.setattr(orchestrator, "CANDIDATES", 1)
    with Session(h.engine) as blocker, blocker.begin():
        blocker.execute(
            select(Workspace.id).where(Workspace.id == h.scope.workspace_id).with_for_update()
        )
        with Session(h.engine) as db, db.begin():
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            claimed = claim_runnable(db, worker)
            assert claimed is not None and claimed.id == second
        # With the other execution held as well, the claim ends empty within its bound.
        with Session(h.engine) as holder, holder.begin():
            locked(holder, second)
            with Session(h.engine) as db, db.begin():
                db.execute(text("SET LOCAL lock_timeout = '2s'"))
                assert claim_runnable(db, worker) is None
    with Session(h.engine) as db, db.begin():
        assert claim_runnable(db, worker).id == head  # positive control: head when free


def test_marker_cleared_after_candidate_read_is_rechecked_under_lock(admission):
    from sqlalchemy import event

    h = admission
    execution_id = admit(h).id
    fired = []

    def clear_after_candidate_read(conn, cursor, statement, parameters, context, many):
        if "runnable_at_ms <=" in statement and "FOR UPDATE" not in statement and not fired:
            fired.append(statement)
            with h.engine.connect() as other:
                other.execute(
                    text("UPDATE executions SET runnable_at_ms = NULL WHERE id = :id"),
                    {"id": execution_id},
                )
                other.commit()

    event.listen(h.engine, "after_cursor_execute", clear_after_candidate_read)
    try:
        with Session(h.engine) as db, db.begin():
            assert claim_runnable(db, WORKER) is None
    finally:
        event.remove(h.engine, "after_cursor_execute", clear_after_candidate_read)
    assert fired
    with Session(h.engine) as db, db.begin():
        mark_runnable(db, locked(db, execution_id), database_now_ms(db))
    with Session(h.engine) as db, db.begin():
        assert claim_runnable(db, WORKER).id == execution_id  # positive control


@pytest.mark.parametrize(
    "operation", ["heartbeat", "instance_revoke", "deployment_revoke", "cancel"]
)
def test_orchestrator_races_renewal_revocation_and_control(admission, operation):
    """Real E03/E04 producers holding their locks never deadlock or stall a claim."""
    from lotuspond.execution.deployments import set_deployment_status
    from lotuspond.fleet.catalog import InstalledCatalog
    from lotuspond.fleet.identity import IdentityManagement, heartbeat
    from lotuspond.models import ExecutionDeploymentRevision, ExecutionSchedulerCursor

    from .test_budgets import runtime_fixture

    h = admission
    execution_id = admit(h).id
    with Session(h.engine) as db, db.begin():
        principal, dependencies = runtime_fixture(h, db)
    progression = StepWriter()
    engine = fixture_engine(progression)
    try:
        with ThreadPoolExecutor(1) as pool:
            with Session(h.engine) as blocker, blocker.begin():
                if operation == "heartbeat":
                    heartbeat(blocker, principal, dependencies)
                elif operation == "instance_revoke":
                    IdentityManagement(
                        blocker, catalog=InstalledCatalog(), actor_id=h.actor.user_id
                    ).revoke_instance(h.scope, principal.identity.instance_id, "revoke")
                elif operation == "deployment_revoke":
                    revision = blocker.get(ExecutionDeploymentRevision, h.revision_id)
                    set_deployment_status(
                        blocker, h.actor, revision.deployment_id, None, "revoked", "revoke"
                    )
                else:
                    blocker.scalar(select(ExecutionSchedulerCursor).with_for_update())
                    blocker.scalar(
                        select(Workspace)
                        .where(Workspace.id == h.scope.workspace_id)
                        .with_for_update()
                    )
                    locked(blocker, execution_id).requested_state = "cancelled"
                    blocker.flush()
                # While the producer holds its locks the claim returns; it never waits.
                during = pool.submit(engine.progress, sessions(h), WORKER, limit=5)
                report = during.result(timeout=30)
            after = engine.progress(sessions(h), WORKER, limit=5)
        assert report.claimed + after.claimed == 1
        assert [call["execution_id"] for call in progression.calls] == [execution_id]
        with Session(h.engine) as db:
            assert outbox(db, execution_id).status == "delivered"
    finally:
        dependencies.close()


class Unproductive(FixtureProgression):
    """Capacity is always next and the capacity step changes nothing."""

    def __init__(self):
        super().__init__(runnable=True, reason="capacity", transitions=0)


def test_blocked_capacity_and_empty_passes_never_spin(admission):
    h = admission
    execution_id = admit(h).id
    progression = Unproductive()
    with Session(h.engine) as db:
        before = database_now_ms(db)
    report = fixture_engine(progression).progress(sessions(h), WORKER, limit=25)
    assert report == ProgressReport(claimed=1, transitions=0, deferred=1)
    assert len(progression.capacity_calls) == 1
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).runnable_at_ms >= before + RETRY_MS
    idle = FixtureProgression(runnable=True, transitions=0)
    with Session(h.engine) as db, db.begin():
        execution = locked(db, execution_id)
        clear_runnable(db, execution)
        mark_runnable(db, execution, database_now_ms(db))
    report = fixture_engine(idle).progress(sessions(h), WORKER, limit=25)
    assert report == ProgressReport(claimed=1, transitions=0, deferred=1)


def test_unproductive_capacity_steps_back_off_exponentially(admission):
    h = admission
    execution_id = admit(h).id
    engine = fixture_engine(Unproductive())
    delays = []
    for _ in range(4):
        with Session(h.engine) as db, db.begin():
            execution = locked(db, execution_id)
            clear_runnable(db, execution)
            mark_runnable(db, execution, database_now_ms(db))
            start = database_now_ms(db)
        engine.progress(sessions(h), WORKER, limit=1)
        with Session(h.engine) as db:
            delays.append(db.get(Execution, execution_id).runnable_at_ms - start)
    # A capacity step that changes nothing (a retention deferred at quota) counts as
    # a refusal, so later deferrals double instead of staying at the base delay.
    assert engine._failures[execution_id] == 4
    assert delays[0] >= RETRY_MS and delays[2] >= 2 * RETRY_MS and delays[3] >= 4 * RETRY_MS


class ProductiveSignal(FixtureProgression):
    """Made progress and is still runnable with a non-capacity reason."""

    def advance_execution(self, db, worker, execution, *, bound, epoch=None):
        outcome = super().advance_execution(db, worker, execution, bound=bound, epoch=epoch)
        return outcome.model_copy(update={"runnable": True, "reason": "signal", "transitions": 3})


class FailingCapacity(FixtureProgression):
    def __init__(self):
        super().__init__(runnable=True, reason="capacity", transitions=1)

    def apply_capacity(self, db, worker, execution_id, *, epoch=None):
        super().apply_capacity(db, worker, execution_id)
        raise ExecutionError("forbidden")


def test_productive_continuation_and_repeated_capacity_refusals(admission):
    h = admission
    execution_id = admit(h).id
    report = fixture_engine(ProductiveSignal()).progress(sessions(h), WORKER, limit=1)
    assert report == ProgressReport(claimed=1, transitions=3, deferred=1)
    with Session(h.engine) as db:
        # Progress with any non-capacity reason continues at once, not after a delay.
        assert db.get(Execution, execution_id).runnable_at_ms <= database_now_ms(db)
    engine = fixture_engine(FailingCapacity())
    delays = []
    for _ in range(3):
        with Session(h.engine) as db, db.begin():
            execution = locked(db, execution_id)
            clear_runnable(db, execution)
            mark_runnable(db, execution, database_now_ms(db))
            start = database_now_ms(db)
        engine.progress(sessions(h), WORKER, limit=1)
        with Session(h.engine) as db:
            delays.append(db.get(Execution, execution_id).runnable_at_ms - start)
    # The pure pass defers by the capacity back-off, which grows with each refusal.
    assert delays[0] >= RETRY_MS and delays[1] >= RETRY_MS and delays[2] >= 2 * RETRY_MS
