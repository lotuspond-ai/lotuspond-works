"""Finding 11: installation capacity without an installation-wide lock (real PostgreSQL).

The installation's active count is the sum of the environment counts. Only a
reservation that takes a compute slot, or a scheduling transaction, takes the
installation cursor, and it takes it last
(docs/superpowers/plans/2026-10-29-installation-capacity-lock.md).
"""

import random
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

from lotuspond_runtime.protocol import Scope
from lotuspond_sdk.activity import UsageObservation
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.budgets import installation_active
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.scheduler import Scheduler
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionReconciliationObservation,
    ExecutionSchedulerCursor,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    ExecutionWait,
    new_id,
)
from lotuspond.security import Principal

from .cursor_checks import assert_leaf, leaves, without_cursor
from .test_budgets import budget_harness as budget_harness
from .test_budgets import demand, set_concurrency
from .test_budgets import harness as harness
from .test_scheduler import Demands


def clone_workspace(h, db, *, shared_user=True):
    """Copy the harness's synthetic environment into a new workspace of its own.

    With `shared_user` the harness's user is a member (owner) of both workspaces,
    the one row execution transactions of two tenants can both lock.
    """
    from lotuspond import models as m

    original = db.get(m.Workspace, h.scope.workspace_id)
    user_id = h.principal.user_id
    if not shared_user:
        user_id = new_id()
        db.add(m.User(id=user_id, email=f"{user_id}@example.com", name="other", password_hash="x"))
        db.flush()
    workspace_id, project_id, environment_id = new_id(), new_id(), new_id()
    db.add(
        m.Workspace(id=workspace_id, name="other", slug="other-" + workspace_id, owner_id=user_id)
    )
    db.flush()
    db.add(m.Membership(workspace_id=workspace_id, user_id=user_id, role="owner"))
    db.add(m.Project(id=project_id, workspace_id=workspace_id, name="other"))
    db.flush()
    db.add(
        m.Environment(
            id=environment_id,
            workspace_id=workspace_id,
            project_id=project_id,
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
    ids = {row["id"]: new_id() for collection in rows.values() for row in collection}
    ids.update(
        {
            original.id: workspace_id,
            h.scope.project_id: project_id,
            h.scope.environment_id: environment_id,
        }
    )
    deferred = []
    for cls, collection in rows.items():
        for row in collection:
            copied = {
                key: ids.get(value, value) if isinstance(value, str) else value
                for key, value in row.items()
            }
            if cls is m.ExecutionCapacity:
                copied.update(active=0, pending=0, retained_waits=0, next_admission_sequence=1)
            for key in ("current_policy_version", "active_revision_id"):
                if key in copied:
                    deferred.append((cls, copied["id"], key, copied[key]))
                    copied[key] = None
            db.execute(cls.__table__.insert().values(**copied))
    for cls, identifier, key, value in deferred:
        db.execute(cls.__table__.update().where(cls.id == identifier).values(**{key: value}))
    original_current = h.authority.current

    def current(scope):
        snapshot = deepcopy(original_current(h.scope))
        snapshot["scope"] = scope.model_dump()
        return snapshot

    h.authority.current = current
    scope = Scope(workspace_id=workspace_id, project_id=project_id, environment_id=environment_id)
    principal = Principal(user_id, workspace_id, "owner", user_id)
    return SimpleNamespace(
        scope=scope,
        actor=authorize_execution(db, principal, scope, "execute"),
        revision_id=ids[h.revision_id],
        artifact_id=ids[h.artifact_id],
    )


def widen(db, h):
    """Five slots per environment and pool, and room for many pending executions."""
    from hashlib import sha256

    from lotuspond.execution.contracts import DeploymentConfig, canonical_configuration
    from lotuspond.models import ExecutionDeploymentRevision

    set_concurrency(db, h, 5)
    revision = db.get(ExecutionDeploymentRevision, h.revision_id)
    config = DeploymentConfig.model_validate_json(revision.canonical_configuration)
    config = config.model_copy(
        update={"limits": config.limits.model_copy(update={"pending_capacity": 1000})}
    )
    revision.canonical_configuration = canonical_configuration(config)
    revision.configuration_sha256 = sha256(revision.canonical_configuration).hexdigest()
    db.flush()


def home(h):
    return SimpleNamespace(
        scope=h.scope, actor=h.actor, revision_id=h.revision_id, artifact_id=h.artifact_id
    )


def add(h, db, tenant, execution_id=None, parent=None, ordinal=1):
    """An admitted execution (through Task 5's admission) and one reserved-state attempt."""
    if execution_id is None:
        h.budgets.reserve_admission(db, tenant.actor, tenant.revision_id)
        capacity = db.scalar(
            select(ExecutionCapacity).where(
                ExecutionCapacity.environment_id == tenant.scope.environment_id
            )
        )
        now = database_now_ms(db)
        execution = Execution(
            **tenant.scope.model_dump(),
            deployment_revision_id=tenant.revision_id,
            interpreter_id="fixture",
            engine_identity=new_id(),
            input_artifact_id=tenant.artifact_id,
            input_artifact_version=1,
            input_sha256="a" * 64,
            initiating_user_id=tenant.actor.user_id,
            admitted_at_ms=now,
            admission_sequence=capacity.next_admission_sequence - 1,
            deadline_ms=now + 1000000,
            admission_digest="b" * 64,
            recovery_epoch=1,
        )
        db.add(execution)
        db.flush()
        execution_id = execution.id
    step = db.scalar(
        select(ExecutionStepInstance).where(ExecutionStepInstance.execution_id == execution_id)
    )
    if step is None:
        step = ExecutionStepInstance(
            **tenant.scope.model_dump(),
            execution_id=execution_id,
            occurrence_path="generate",
            node_id="generate",
        )
        db.add(step)
        db.flush()
    attempt = ExecutionAttempt(
        **tenant.scope.model_dump(),
        execution_id=execution_id,
        step_instance_id=step.id,
        ordinal=ordinal,
        parent_attempt_id=parent,
        child_request_id=new_id() if parent else None,
        child_request_digest="c" * 64 if parent else None,
        input_artifact_id=tenant.artifact_id,
        input_artifact_version=1,
        input_sha256="a" * 64,
    )
    db.add(attempt)
    db.flush()
    return attempt.id, execution_id


def stop(h, db, attempt_id):
    """A confirmed compute stop, in the attempt's own scope.

    As every product caller does, the workspace row is locked before the observation
    is inserted: the insert's foreign keys lock its parents `FOR KEY SHARE`, which must
    not come before a parent the order puts first.
    """
    from lotuspond.models import Workspace

    attempt = db.get(ExecutionAttempt, attempt_id)
    db.scalar(select(Workspace).where(Workspace.id == attempt.workspace_id).with_for_update())
    observation = ExecutionReconciliationObservation(
        workspace_id=attempt.workspace_id,
        project_id=attempt.project_id,
        environment_id=attempt.environment_id,
        execution_id=attempt.execution_id,
        attempt_id=attempt_id,
        source_id="fixture-stop",
        observation_id=new_id(),
        digest="d" * 64,
        code="compute_stopped",
        observed_at_ms=database_now_ms(db),
    )
    db.add(observation)
    db.flush()
    return h.budgets.confirm_stopped(db, attempt_id, observation.id)


def open_wait(db, tenant, execution_id):
    step = db.scalar(
        select(ExecutionStepInstance).where(ExecutionStepInstance.execution_id == execution_id)
    )
    wait = ExecutionWait(
        **tenant.scope.model_dump(),
        execution_id=execution_id,
        step_instance_id=step.id,
        wait_key=new_id(),
        deadline_ms=db.get(Execution, execution_id).deadline_ms,
    )
    db.add(wait)
    db.flush()
    return wait.id


def unstopped_slots(db):
    """Reservations whose compute holds a slot: unstopped, and not a shared child."""
    from lotuspond.models import ExecutionRequest

    return db.scalar(
        select(func.count())
        .select_from(ExecutionUsageReservation)
        .where(
            ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
            ~select(ExecutionRequest.id)
            .where(
                ExecutionRequest.request_key
                == "capacity.shared:" + ExecutionUsageReservation.attempt_id
            )
            .exists(),
        )
    )


def deadlocks(engine):
    from time import sleep

    sleep(1)  # PostgreSQL publishes its statistics asynchronously
    with engine.connect() as db:
        db.execute(text("SELECT pg_stat_clear_snapshot()"))
        return db.scalar(
            text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
        )


# ------------------------------------------------------------------ the leaf


def test_a_slot_takes_the_installation_cursor_last(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
    with leaves(h.engine) as records, Session(h.engine) as db, db.begin():
        h.budgets.reserve_attempt(db, h.actor, attempt, demand(1))
    assert len(records) == 1
    assert_leaf(records[0])
    with Session(h.engine) as db:
        assert installation_active(db) == db.scalar(select(ExecutionSchedulerCursor.active)) == 1


def test_nothing_but_a_slot_takes_the_cursor(budget_harness):
    """Admission, retention, releases, settlement, stops and shared children never lock it."""
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        parent, execution = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, parent, demand(1))
        child, _ = h.add(db, execution, parent, 11)
        unstarted, _ = h.add(db, execution, ordinal=2)
        _, pending = h.add(db)
        _, waiting = h.add(db)
        wait = open_wait(db, home(h), waiting)

    def run(operation):
        def transaction():
            with Session(h.engine) as db, db.begin():
                return operation(db)

        return without_cursor(h.engine, transaction)

    run(lambda db: h.budgets.reserve_admission(db, h.actor, h.revision_id))
    run(lambda db: h.budgets.reserve_attempt(db, h.actor, child, demand(1), shared=True))
    usage = UsageObservation(input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=0)
    assert run(lambda db: h.budgets.settle_attempt(db, parent, "known", usage)).state == "settled"
    run(lambda db: stop(h, db, child))
    run(lambda db: stop(h, db, parent))
    run(lambda db: h.budgets.retain_wait(db, h.actor, wait))
    with Session(h.engine) as db, db.begin():
        db.get(ExecutionWait, wait).state = "satisfied"
    run(lambda db: h.budgets.release_wait(db, wait))
    run(lambda db: h.budgets.release_pending(db, pending))
    with Session(h.engine) as db, db.begin():
        h.budgets.reserve_attempt(db, h.actor, unstarted, demand(1))
        db.get(ExecutionAttempt, unstarted).state = "cancelled"
    run(lambda db: stop(h, db, unstarted))
    assert run(lambda db: h.budgets.release_unstarted(db, unstarted, 1)).state == "released"
    with Session(h.engine) as db:
        assert installation_active(db) == 0
        assert db.scalar(select(ExecutionSchedulerCursor.active)) >= installation_active(db)


# ------------------------------------------------------------------ the limit


def test_the_installation_limit_is_checked_at_the_leaf(budget_harness, monkeypatch):
    """A slot taken elsewhere while a reservation works is seen at its leaf: it refuses."""
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        other = clone_workspace(h, db, shared_user=False)
        db.scalar(select(ExecutionSchedulerCursor)).active_limit = 1
    # One workspace per transaction, as in production: its gates come before any row
    # lock (throughput option C, `authority_gate`).
    with Session(h.engine) as db, db.begin():
        first, first_execution = h.add(db)
    with Session(h.engine) as db, db.begin():
        second, _ = add(h, db, other)
    paused, resume = threading.Event(), threading.Event()
    original, slow = h.budgets._leaf, {}

    def leaf(db):
        if slow.get("thread") == threading.get_ident():
            paused.set()  # every lookup and lock is done; the sum is still to come
            assert resume.wait(10)
        return original(db)

    monkeypatch.setattr(h.budgets, "_leaf", leaf)

    def reserve_first():
        slow["thread"] = threading.get_ident()
        try:
            with Session(h.engine) as db, db.begin():
                h.budgets.reserve_attempt(db, h.actor, first, demand(1))
        except ExecutionError as error:
            return error.code
        return "reserved"

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(reserve_first)
        assert paused.wait(10)
        with Session(h.engine) as db, db.begin():  # another tenant takes the last slot
            h.budgets.reserve_attempt(db, other.actor, second, demand(1))
        resume.set()
        assert future.result(timeout=10) == "capacity_unavailable"
    with Session(h.engine) as db:
        assert installation_active(db) == 1
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 1
        assert db.get(Execution, first_execution).state == "pending"
        assert (
            db.scalar(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.attempt_id == first
                )
            )
            is None
        )
        home_capacity = db.scalar(
            select(ExecutionCapacity).where(
                ExecutionCapacity.environment_id == h.scope.environment_id
            )
        )
        assert home_capacity.active == 0 and home_capacity.pending == 1


def test_reservations_racing_stops_never_exceed_the_installation_limit(budget_harness):
    h = budget_harness
    limit, per_tenant = 3, 12
    with Session(h.engine) as db, db.begin():
        widen(db, h)
        tenants = [home(h), clone_workspace(h, db, shared_user=False)]
        db.scalar(select(ExecutionSchedulerCursor)).active_limit = limit
    attempts = {}
    for i, tenant in enumerate(tenants):  # one workspace per transaction (`authority_gate`)
        with Session(h.engine) as db, db.begin():
            attempts[i] = [add(h, db, tenant)[0] for _ in range(per_tenant)]
    done = threading.Event()
    observed, outcomes, reserved = [], [], []
    lock = threading.Lock()

    def reserver(index):
        tenant, queue = tenants[index % 2], list(attempts[index % 2])
        random.Random(index).shuffle(queue)
        for attempt in queue:
            try:
                with Session(h.engine) as db, db.begin():
                    h.budgets.reserve_attempt(db, tenant.actor, attempt, demand(1))
                with lock:
                    reserved.append(attempt)
                outcomes.append("reserved")
            except ExecutionError as error:
                outcomes.append(error.code)

    def stopper():
        while not done.is_set() or reserved:
            with lock:
                attempt = reserved.pop(0) if reserved else None
            if attempt is None:
                done.wait(0.01)
                continue
            with Session(h.engine) as db, db.begin():
                stop(h, db, attempt)

    def sampler():
        with Session(h.engine) as db:
            while not done.is_set():
                observed.append(installation_active(db))
                db.rollback()

    with ThreadPoolExecutor(8) as pool:
        background = [pool.submit(stopper), pool.submit(sampler)]
        list(pool.map(reserver, range(4)))
        done.set()
        for future in background:
            future.result(timeout=30)
    assert observed and max(observed) <= limit
    assert outcomes.count("reserved") >= limit
    assert set(outcomes) <= {"reserved", "capacity_unavailable", "conflict"}
    with Session(h.engine) as db:
        assert installation_active(db) == unstopped_slots(db) == 0
        assert db.scalar(select(ExecutionSchedulerCursor.active)) <= limit
        for capacity in db.scalars(select(ExecutionCapacity)):
            assert capacity.active == 0


# ------------------------------------------------------------------ deadlocks


def test_task5_entry_points_race_across_workspaces_without_deadlock(budget_harness):
    """Two tenants sharing one user: every Task 5 entry point, concurrently, many times.

    Scheduling (one head per transaction), admission, slot reservations, stops, wait
    retention and release and pending releases race in both workspaces. PostgreSQL
    must detect no deadlock, and the counters must match the rows they count.
    """
    h = budget_harness
    rounds = 12
    with Session(h.engine) as db, db.begin():
        widen(db, h)
        tenants = [home(h), clone_workspace(h, db, shared_user=True)]
        db.scalar(select(ExecutionSchedulerCursor)).active_limit = 4
    work = []
    for tenant in tenants:  # one workspace per transaction (`authority_gate`)
        with Session(h.engine) as db, db.begin():
            for _ in range(rounds):
                attempt, execution = add(h, db, tenant)
                _, waiting = add(h, db, tenant)
                work.append((tenant, attempt, execution, open_wait(db, tenant, waiting)))
    scheduler = Scheduler(h.budgets, Demands(), test_mode=True)
    errors, lock = [], threading.Lock()

    def record(exc_context):
        code = getattr(exc_context.original_exception, "sqlstate", None)
        if code == "40P01":
            errors.append(code)

    event.listen(h.engine, "handle_error", record)
    before = deadlocks(h.engine)

    def attempt(operation):
        try:
            with Session(h.engine) as db, db.begin():
                operation(db)
        except ExecutionError as error:
            return error.code
        return "ok"

    def schedule(_):
        reserved = []

        def one(db):
            reserved.extend(scheduler.schedule_batch(db, "test-installation"))

        codes = [attempt(one) for _ in range(rounds * 2)]
        return [("schedule", "ok" if reserved else code) for code in codes[-1:]]

    admitted = {tenant.scope.environment_id: 0 for tenant in tenants}

    def tenant_work(index):
        results = []
        for tenant, attempt_id, execution, wait in work[index::4]:
            code = attempt(
                lambda db: h.budgets.reserve_admission(db, tenant.actor, tenant.revision_id)
            )
            if code == "ok":  # a slot with no execution row: counted below
                with lock:
                    admitted[tenant.scope.environment_id] += 1
            results.append(("admission", code))
            results.append(
                ("retain", attempt(lambda db: h.budgets.retain_wait(db, tenant.actor, wait)))
            )
            results.append(("stop", attempt(lambda db: stop(h, db, attempt_id))))

            def close(db, wait=wait):
                db.get(ExecutionWait, wait).state = "satisfied"
                db.flush()
                h.budgets.release_wait(db, wait)

            results.append(("release", attempt(close)))
            results.append(
                ("pending", attempt(lambda db: h.budgets.release_pending(db, execution)))
            )
        return results

    try:
        with ThreadPoolExecutor(6) as pool:
            futures = [pool.submit(schedule, n) for n in range(2)]
            futures += [pool.submit(tenant_work, n) for n in range(4)]
            outcomes = [code for future in futures for code in future.result(timeout=300)]
    finally:
        event.remove(h.engine, "handle_error", record)
        scheduler.close()
    assert errors == []
    assert deadlocks(h.engine) == before
    # Positive controls: every kind of operation took effect at least once.
    assert {kind for kind, code in outcomes if code == "ok"} == {
        "schedule",
        "admission",
        "retain",
        "stop",
        "release",
        "pending",
    }
    with Session(h.engine) as db:
        from lotuspond.models import ExecutionRequest

        assert installation_active(db) == unstopped_slots(db) <= 4
        for capacity in db.scalars(select(ExecutionCapacity)):
            scope = dict(
                workspace_id=capacity.workspace_id,
                project_id=capacity.project_id,
                environment_id=capacity.environment_id,
            )
            retained = db.scalar(
                select(func.count())
                .select_from(ExecutionRequest)
                .filter_by(**scope)
                .where(ExecutionRequest.request_key.like("capacity.wait:%"))
            )
            released = db.scalar(
                select(func.count())
                .select_from(ExecutionRequest)
                .filter_by(**scope)
                .where(ExecutionRequest.request_key.like("capacity.wait-release:%"))
            )
            assert capacity.retained_waits == retained - released
            pending = db.scalar(
                select(func.count()).select_from(Execution).filter_by(**scope, state="pending")
            )
            pending_released = db.scalar(
                select(func.count())
                .select_from(ExecutionRequest)
                .filter_by(**scope)
                .where(ExecutionRequest.request_key.like("capacity.pending-release:%"))
            )
            assert capacity.pending == (
                pending - pending_released + admitted[capacity.environment_id]
            )
