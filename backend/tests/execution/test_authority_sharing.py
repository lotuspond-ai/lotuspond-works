"""Throughput option C, step 2: execution writes share their authority rows, under
fair gates (`lotuspond.authority_gate`).

`authorize_execution` shares the account rows for `execute`, `signal` and `control`;
the deployment, revision, pool and connection checks share theirs. Real concurrent
PostgreSQL connections show that two such writes no longer queue for each other,
that an authority change still fences them and never deadlocks with them, that it
is granted within the owner's 2 s bound while they overlap without a gap, and that
pool concurrency, no longer counted under an exclusive pool row, still holds.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from lotuspond import authority_gate
from lotuspond.execution.authorization import authorize_execution, scoped
from lotuspond.execution.deployments import _pool, gates
from lotuspond.execution.errors import ExecutionError
from lotuspond.models import (
    ExecutionCapacity,
    ExecutionDeploymentRevision,
    ExecutionUsageReservation,
    RuntimePoolPolicy,
    Workspace,
)

from .test_budgets import budget_harness as budget_harness
from .test_budgets import demand, set_concurrency
from .test_budgets import harness as harness


@pytest.fixture
def shared(budget_harness):
    h = budget_harness
    if h.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks required")
    return h


def execution_write(h, hold=0.0):
    """The authority part of an execution write: its fair gates (workspace, user,
    pool), then the account rows, then the pool row, as an execution path takes them."""
    with Session(h.engine) as db, db.begin():
        gates(db, h.scope, h.revision_id, users=[h.principal.user_id])
        actor = authorize_execution(db, h.principal, h.scope, "execute")
        revision = db.get(ExecutionDeploymentRevision, h.revision_id)
        _pool(db, actor.scope, revision.pool_id)
        time.sleep(hold)


def suspend(h, status="suspended", hold=0.0):
    """An authority change: the exclusive gate, the admin route's FOR UPDATE, its write."""
    with Session(h.engine) as db, db.begin():
        authority_gate.workspace(db, h.scope.workspace_id, exclusive=True)
        workspace = db.scalar(
            select(Workspace).where(Workspace.id == h.scope.workspace_id).with_for_update()
        )
        workspace.status = status
        time.sleep(hold)


def test_two_execution_writes_in_one_workspace_hold_its_rows_together(shared):
    h = shared
    holding = threading.Event()

    def first():
        with Session(h.engine) as db, db.begin():
            gates(db, h.scope, h.revision_id, users=[h.principal.user_id])
            authorize_execution(db, h.principal, h.scope, "execute")
            holding.set()
            time.sleep(1.5)

    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(first)
        assert holding.wait(5)
        started = time.monotonic()
        execution_write(h)  # FOR UPDATE would wait out the first's 1.5 s
        assert time.monotonic() - started < 0.5
        future.result()


def test_an_authority_change_still_fences_an_execution_write(shared):
    h = shared
    holding, release = threading.Event(), threading.Event()

    def write():
        with Session(h.engine) as db, db.begin():
            gates(db, h.scope, h.revision_id, users=[h.principal.user_id])
            authorize_execution(db, h.principal, h.scope, "execute")
            holding.set()
            release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as workers:
        writing = workers.submit(write)
        assert holding.wait(5)
        change = workers.submit(suspend, h)
        time.sleep(0.5)
        assert not change.done()  # waits for the write's commit
        release.set()
        writing.result()
        change.result(timeout=2)
    with pytest.raises(ExecutionError) as refused:
        execution_write(h)
    assert refused.value.code == "forbidden"
    suspend(h, "active")


def test_authority_changes_and_execution_writes_never_deadlock(shared):
    h = shared
    deadlocks, stop = [], threading.Event()

    def guarded(call):
        try:
            call()
        except ExecutionError:
            pass  # refused while suspended: not a deadlock
        except OperationalError as error:
            if "deadlock" in str(error.orig).lower():
                deadlocks.append(str(error.orig))
            else:
                raise

    def writes():
        while not stop.is_set():
            guarded(lambda: execution_write(h, hold=0.01))

    def changes():
        for _ in range(20):
            guarded(lambda: suspend(h, "suspended", hold=0.01))
            guarded(lambda: suspend(h, "active", hold=0.01))
        stop.set()

    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = [workers.submit(writes) for _ in range(3)] + [workers.submit(changes)]
        for future in futures:
            future.result(timeout=60)
    assert deadlocks == []


def test_an_authority_change_is_granted_within_2s_under_sustained_writes(shared):
    """The owner's bound, while shared holds overlap without a gap."""
    h = shared
    stop, waits = threading.Event(), []

    def writes(offset):
        time.sleep(offset)
        while not stop.is_set():
            execution_write(h, hold=0.2)

    with ThreadPoolExecutor(max_workers=4) as workers:
        loops = [workers.submit(writes, offset) for offset in (0, 0.05, 0.1, 0.15)]
        time.sleep(0.5)
        try:
            for _ in range(5):
                started = time.monotonic()
                with Session(h.engine) as db, db.begin():
                    authority_gate.workspace(db, h.scope.workspace_id, exclusive=True)
                    db.scalar(
                        select(Workspace)
                        .where(Workspace.id == h.scope.workspace_id)
                        .with_for_update()
                    )
                    waits.append(time.monotonic() - started)
                time.sleep(0.1)
        finally:
            stop.set()
        for loop in loops:
            loop.result(timeout=10)
    print("revocation waits (s):", [round(wait, 3) for wait in waits])
    assert max(waits) < 2.0, waits


def test_pool_concurrency_holds_at_its_limit_minus_one(shared):
    """No longer counted under an exclusive pool row: the environment's capacity row,
    which every slot-taking reservation locks first, serializes the count."""
    from lotuspond_runtime.protocol import PoolPolicy, canonical_pool_json, pool_digest

    h = shared
    with Session(h.engine) as db, db.begin():
        set_concurrency(db, h, 5)  # the environment allows 5
        row = db.scalar(select(RuntimePoolPolicy))
        policy = PoolPolicy.model_validate_json(row.canonical_policy).model_copy(
            update={"max_concurrent_invocations": 1}  # the pool allows 1
        )
        row.canonical_policy, row.policy_digest = canonical_pool_json(policy), pool_digest(policy)
        first, _ = h.add(db)
        second, _ = h.add(db)
    barrier = threading.Barrier(2)

    def reserve(identifier):
        barrier.wait(timeout=5)
        try:
            with Session(h.engine) as db, db.begin():
                h.budgets.reserve_attempt(db, h.actor, identifier, demand(1))
            return "reserved"
        except ExecutionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = sorted(workers.map(reserve, [first, second]))
    assert results == ["capacity_unavailable", "reserved"]
    with Session(h.engine) as db:
        assert len(list(db.scalars(select(ExecutionUsageReservation.id)))) == 1
        capacity = db.scalar(scoped(ExecutionCapacity, h.scope))
        assert capacity.active == 1
