from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.scheduler import Scheduler
from lotuspond.models import ExecutionCapacity, ExecutionSchedulerCursor

from .test_budgets import budget_harness as budget_harness
from .test_budgets import demand
from .test_budgets import harness as harness


class Demands:
    test_only = True

    def demand(self, attempt_id):
        return demand(1)


def test_fair_capacity_and_wait_release(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        first, _ = h.add(db)
        h.add(db)
    scheduler = Scheduler(h.budgets, Demands(), test_mode=True)
    with Session(h.engine) as db, db.begin():
        scheduled = scheduler.schedule_batch(db, "test-installation")
        assert [value.attempt_id for value in scheduled] == [first]
        installation_active = db.scalar(select(ExecutionSchedulerCursor.active))
        environment_active = {
            row.environment_id: row.active for row in db.scalars(select(ExecutionCapacity))
        }
        assert installation_active <= 25
        assert max(environment_active.values()) <= 5
        h.stop(db, first, "stop")
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 0
    scheduler.close()


def test_wait_quota_requires_stop_counts_parallel_once_and_keeps_deadline(budget_harness):
    import pytest

    from lotuspond.execution.errors import ExecutionError
    from lotuspond.models import Execution, ExecutionStepInstance, ExecutionWait, new_id

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, execution_id = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, attempt, demand(1))
        execution = db.get(Execution, execution_id)
        deadline = execution.deadline_ms
        step = db.scalar(select(ExecutionStepInstance))
        waits = [
            ExecutionWait(
                **h.scope.model_dump(),
                execution_id=execution.id,
                step_instance_id=step.id,
                wait_key=new_id(),
                deadline_ms=deadline,
            )
            for _ in range(2)
        ]
        db.add_all(waits)
        db.flush()
        with pytest.raises(ExecutionError, match="conflict"):
            h.budgets.retain_wait(db, h.actor, waits[0].id)
        h.stop(db, attempt, "confirmed")
        for wait in waits:
            h.budgets.retain_wait(db, h.actor, wait.id)
        h.budgets.retain_wait(db, h.actor, waits[0].id)
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 2
        assert all(wait.deadline_ms == deadline for wait in waits)
        assert execution.deadline_ms == deadline
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 0
        capacity = db.scalar(select(ExecutionCapacity))
        capacity.retained_waits = 10000
        db.flush()
        third = ExecutionWait(
            **h.scope.model_dump(),
            execution_id=execution.id,
            step_instance_id=step.id,
            wait_key=new_id(),
            deadline_ms=deadline,
        )
        db.add(third)
        db.flush()
        with pytest.raises(ExecutionError, match="capacity_unavailable"):
            h.budgets.retain_wait(db, h.actor, third.id)


def clone_environment(h, db):
    """Copy only synthetic owned records into a second environment in this DB."""
    from copy import deepcopy

    from lotuspond import models as m

    environment_id = m.new_id()
    db.add(
        m.Environment(
            id=environment_id,
            workspace_id=h.scope.workspace_id,
            project_id=h.scope.project_id,
            name="other",
            kind="local",
        )
    )
    db.flush()
    classes = [
        m.RuntimePool,
        m.RuntimePoolPolicy,
        m.ExecutablePublication,
        m.ExecutableDependency,
        m.ExecutionDeployment,
        m.ExecutionDeploymentRevision,
        m.DeploymentBinding,
        m.ExecutionArtifact,
        m.Execution,
        m.ExecutionStepInstance,
        m.ExecutionAttempt,
        m.ExecutionCapacity,
    ]
    rows = {
        cls: [
            dict(row)
            for row in db.execute(
                select(cls.__table__).where(cls.environment_id == h.scope.environment_id)
            ).mappings()
        ]
        for cls in classes
    }
    ids = {row["id"]: m.new_id() for collection in rows.values() for row in collection}
    ids[h.scope.environment_id] = environment_id
    deferred = []
    for cls, collection in rows.items():
        for row in collection:
            copied = {
                key: ids.get(value, value) if isinstance(value, str) else value
                for key, value in row.items()
            }
            if cls is m.Execution:
                copied["engine_identity"] = m.new_id()
            for key in ("current_policy_version", "active_revision_id"):
                if key in copied:
                    deferred.append((cls, copied["id"], key, copied[key]))
                    copied[key] = None
            db.execute(cls.__table__.insert().values(**copied))
    for cls, identifier, key, value in deferred:
        db.execute(cls.__table__.update().where(cls.id == identifier).values(**{key: value}))

    def current(scope):
        snapshot = deepcopy(h.authority.snapshot)
        snapshot["scope"] = scope.model_dump()
        return snapshot

    h.authority.current = current
    return ids


def test_exhausted_environment_does_not_starve_other_and_fifo_is_sequence(budget_harness):
    from lotuspond.models import Execution, ExecutionAttempt

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        first, first_execution = h.add(db)
        second, second_execution = h.add(db)
        # Reverse timestamps: only durable sequence may define FIFO.
        db.get(Execution, first_execution).admitted_at_ms -= 100
        db.get(Execution, second_execution).admitted_at_ms -= 1000
        db.flush()
        ids = clone_environment(h, db)
        original = db.scalar(
            select(ExecutionCapacity).where(
                ExecutionCapacity.environment_id == h.scope.environment_id
            )
        )
        original.active_limit = 0
    scheduler = Scheduler(h.budgets, Demands(), test_mode=True)
    with Session(h.engine) as db, db.begin():
        scheduled = scheduler.schedule_batch(db, "test-installation")
        assert [item.attempt_id for item in scheduled] == [ids[first]]
        assert db.get(ExecutionAttempt, ids[second]).state == "reserved"
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 1
    scheduler.close()


def test_parallel_scheduler_enforces_installation_25_and_environment_5(budget_harness):
    from concurrent.futures import ThreadPoolExecutor
    from hashlib import sha256
    from threading import Barrier

    from lotuspond_runtime.protocol import PoolPolicy, canonical_pool_json, pool_digest

    from lotuspond.execution.contracts import DeploymentConfig, canonical_configuration
    from lotuspond.models import (
        ExecutionDeploymentRevision,
        ExecutionUsageReservation,
        RuntimePoolPolicy,
    )

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        revision = db.get(ExecutionDeploymentRevision, h.revision_id)
        config = DeploymentConfig.model_validate_json(revision.canonical_configuration)
        config = config.model_copy(
            update={"limits": config.limits.model_copy(update={"active_capacity": 5})}
        )
        revision.canonical_configuration = canonical_configuration(config)
        revision.configuration_sha256 = sha256(revision.canonical_configuration).hexdigest()
        row = db.scalar(select(RuntimePoolPolicy))
        policy = PoolPolicy.model_validate_json(row.canonical_policy).model_copy(
            update={"max_concurrent_invocations": 5}
        )
        row.canonical_policy = canonical_pool_json(policy)
        row.policy_digest = pool_digest(policy)
        db.flush()
        for _ in range(6):
            h.add(db)
        for _ in range(5):
            clone_environment(h, db)
    scheduler = Scheduler(h.budgets, Demands(), test_mode=True)
    barrier = Barrier(2)

    def run(_):
        barrier.wait(timeout=5)
        with Session(h.engine) as db, db.begin():
            return scheduler.schedule_batch(db, "test-installation")

    with ThreadPoolExecutor(2) as pool:
        for _ in range(3):
            batches = list(pool.map(run, range(2)))
            assert all(len(batch) <= 25 for batch in batches)
    with Session(h.engine) as db:
        installation_active = db.scalar(select(ExecutionSchedulerCursor.active))
        environment_active = {
            row.environment_id: row.active for row in db.scalars(select(ExecutionCapacity))
        }
        assert installation_active == 25
        assert installation_active <= 25
        assert max(environment_active.values()) <= 5
        assert sum(environment_active.values()) == 25
        assert len(list(db.scalars(select(ExecutionUsageReservation)))) == 25
    scheduler.close()


def test_wait_accounting_rollback_and_conflicting_identity(budget_harness):
    import pytest

    from lotuspond.execution.errors import ExecutionError
    from lotuspond.models import (
        Execution,
        ExecutionRequest,
        ExecutionStepInstance,
        ExecutionWait,
        new_id,
    )

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        _, execution_id = h.add(db)
        execution = db.get(Execution, execution_id)
        step = db.scalar(select(ExecutionStepInstance))
        wait = ExecutionWait(
            **h.scope.model_dump(),
            execution_id=execution_id,
            step_instance_id=step.id,
            wait_key=new_id(),
            deadline_ms=execution.deadline_ms,
        )
        db.add(wait)
        db.flush()
        wait_id = wait.id
    with Session(h.engine) as db:
        h.budgets.retain_wait(db, h.actor, wait_id)
        db.rollback()
    with Session(h.engine) as db, db.begin():
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 0
        assert db.scalar(select(ExecutionRequest)) is None
        h.budgets.retain_wait(db, h.actor, wait_id)
        row = db.get(ExecutionWait, wait_id)
        row.deadline_ms -= 1
        db.flush()
        with pytest.raises(ExecutionError, match="conflict"):
            h.budgets.retain_wait(db, h.actor, wait_id)
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 1


def stop_and_replenish(h, db, attempt_id):
    from lotuspond_runtime.protocol import Scope

    from lotuspond.execution.authorization import authorize_execution
    from lotuspond.fleet.authority import database_now_ms
    from lotuspond.models import Execution, ExecutionAttempt, ExecutionReconciliationObservation

    attempt = db.get(ExecutionAttempt, attempt_id)
    scope = Scope(
        workspace_id=attempt.workspace_id,
        project_id=attempt.project_id,
        environment_id=attempt.environment_id,
    )
    observation = ExecutionReconciliationObservation(
        **scope.model_dump(),
        execution_id=attempt.execution_id,
        attempt_id=attempt.id,
        source_id="fixture-stop",
        observation_id=attempt.id,
        digest="a" * 64,
        code="compute_stopped",
        observed_at_ms=database_now_ms(db),
    )
    db.add(observation)
    db.flush()
    h.budgets.confirm_stopped(db, attempt.id, observation.id)
    previous = h.scope, h.actor, h.revision_id, h.artifact_id
    try:
        h.scope = scope
        h.actor = authorize_execution(db, h.principal, scope, "execute")
        h.revision_id = db.get(Execution, attempt.execution_id).deployment_revision_id
        h.artifact_id = attempt.input_artifact_id
        h.add(db)
    finally:
        h.scope, h.actor, h.revision_id, h.artifact_id = previous
    return scope.environment_id


def test_single_slot_round_robin_alternates_after_confirmed_stop_and_replenish(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        h.add(db)
        clone_environment(h, db)
        db.scalar(select(ExecutionSchedulerCursor)).active_limit = 1
        environments = sorted(db.scalars(select(ExecutionCapacity.environment_id)))
    scheduler = Scheduler(h.budgets, Demands(), test_mode=True)
    try:
        turns = []
        for _ in range(6):
            with Session(h.engine) as db, db.begin():
                selected = scheduler.schedule_batch(db, "test-installation")
                assert len(selected) == 1
                turns.append(stop_and_replenish(h, db, selected[0].attempt_id))
        assert turns == environments * 3
    finally:
        scheduler.close()


def test_round_robin_tracks_skipped_environment_and_wraps(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        h.add(db)
        clone_environment(h, db)
        clone_environment(h, db)
        capacities = list(
            db.scalars(select(ExecutionCapacity).order_by(ExecutionCapacity.environment_id))
        )
        capacities[0].active_limit = 0
        environments = [row.environment_id for row in capacities[1:]]
        db.scalar(select(ExecutionSchedulerCursor)).active_limit = 1
    scheduler = Scheduler(h.budgets, Demands(), test_mode=True)
    try:
        turns = []
        for _ in range(4):
            with Session(h.engine) as db, db.begin():
                selected = scheduler.schedule_batch(db, "test-installation", limit=1)
                assert len(selected) == 1
                turns.append(stop_and_replenish(h, db, selected[0].attempt_id))
        assert turns == environments * 2
    finally:
        scheduler.close()


def test_wait_accounting_concurrent_replay_changes_counter_once(budget_harness):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from lotuspond.models import (
        Execution,
        ExecutionRequest,
        ExecutionStepInstance,
        ExecutionWait,
        new_id,
    )

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        _, execution_id = h.add(db)
        execution = db.get(Execution, execution_id)
        step = db.scalar(select(ExecutionStepInstance))
        wait = ExecutionWait(
            **h.scope.model_dump(),
            execution_id=execution_id,
            step_instance_id=step.id,
            wait_key=new_id(),
            deadline_ms=execution.deadline_ms,
        )
        db.add(wait)
        db.flush()
        wait_id = wait.id
    barrier = Barrier(2)

    def retain(_):
        barrier.wait(timeout=5)
        with Session(h.engine) as db, db.begin():
            h.budgets.retain_wait(db, h.actor, wait_id)

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(retain, range(2)))
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 1
        assert len(list(db.scalars(select(ExecutionRequest)))) == 1


def test_wait_accounting_same_key_cannot_refer_to_other_execution(budget_harness):
    from hashlib import sha256

    import pytest

    from lotuspond.execution.contracts import canonical_value
    from lotuspond.execution.errors import ExecutionError
    from lotuspond.models import (
        Execution,
        ExecutionRequest,
        ExecutionStepInstance,
        ExecutionWait,
        new_id,
    )

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        _, first = h.add(db)
        _, second = h.add(db)
        execution = db.get(Execution, second)
        step = db.scalar(
            select(ExecutionStepInstance).where(ExecutionStepInstance.execution_id == second)
        )
        wait = ExecutionWait(
            **h.scope.model_dump(),
            execution_id=second,
            step_instance_id=step.id,
            wait_key=new_id(),
            deadline_ms=execution.deadline_ms,
        )
        db.add(wait)
        db.flush()
        digest = sha256(
            canonical_value(
                dict(
                    wait_id=wait.id,
                    execution_id=second,
                    step_instance_id=step.id,
                    deadline_ms=wait.deadline_ms,
                )
            )
        ).hexdigest()
        db.add(
            ExecutionRequest(
                **h.scope.model_dump(),
                execution_id=first,
                request_key="capacity.wait:" + wait.id,
                digest=digest,
            )
        )
        db.flush()
        with pytest.raises(ExecutionError, match="conflict"):
            h.budgets.retain_wait(db, h.actor, wait.id)
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 0
        assert execution.state == "pending"
