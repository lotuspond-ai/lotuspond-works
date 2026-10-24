"""Task 11 harness: the real fence, sweeper, controls and event projection over Task 9.

Built on the activity harness (real PostgreSQL, real admission, progression and
activity services). Every provider is still a test fixture, so production cannot
select any of it. Time moves only in real database time: no deadline, lease or
expiry column is edited.
"""

from time import sleep

import pytest
from sqlalchemy.orm import Session

from lotuspond.execution.contracts import ControlRequest
from lotuspond.execution.control import ControlService
from lotuspond.execution.engine import PostgresExecutionEngine
from lotuspond.execution.engine_contracts import INTERPRETER_V1, WorkerIdentity
from lotuspond.execution.projections import EventProjection
from lotuspond.execution.recovery import RecoveryFence
from lotuspond.execution.sweeper import ExecutionSweeper
from lotuspond.fleet.authority import database_now_ms

from .activity_fixtures import Demand

SWEEPER = WorkerIdentity(worker_id="sweeper-a", role="sweeper", interpreter_ids=[INTERPRETER_V1])
ORCHESTRATOR = WorkerIdentity(
    worker_id="orchestrator-t", role="orchestrator", interpreter_ids=[INTERPRETER_V1]
)


@pytest.fixture
def controlled(activity):
    h = activity
    h.fence = RecoveryFence(h.fleet)
    h.sweeper = ExecutionSweeper(leases=h.leases, recovery=h.fence)
    h.controls = ControlService(
        budgets=h.budgets,
        artifacts=h.store,
        artifact_policy=h.policy,
        installation_id="test-installation",
        recovery=h.fence,
    )
    h.events = EventProjection(
        artifacts=h.store,
        artifact_policy=h.policy,
        installation_id="test-installation",
        recovery=h.fence,
    )
    # The real engine: Task 8's interpreter, this sweeper and the fence.
    h.real_engine = PostgresExecutionEngine(
        h.interpreter, h.sweeper, h.fence, interpreter_ids=[INTERPRETER_V1], test_mode=True
    )

    def sweep(limit=100, sweeper=None):
        return (sweeper or h.sweeper).sweep(h.sessions, SWEEPER, limit=limit)

    def control(execution_id, action, request_id=None, actor=None):
        with Session(h.engine) as db, db.begin():
            return h.controls.request_control(
                db,
                actor or h.actor,
                execution_id,
                ControlRequest(request_id=request_id or f"{action}-{execution_id}", action=action),
            )

    def run(rounds=200):
        reports = []
        for _ in range(rounds):
            report = h.real_engine.progress(h.sessions, ORCHESTRATOR, limit=10)
            reports.append(report)
            if report.claimed == 0:
                break
        return reports

    h.sweep, h.control, h.run = sweep, control, run
    h.closing = []
    yield h
    for fleet in h.closing:
        fleet.close()


def own_fence(h):
    """A fence with its own bounded authority lookup, as another process has.

    The fleet lookup admits one call at a time and denies the rest, so concurrent
    callers in one test each need their own, as separate worker processes would.
    """
    from lotuspond.config import Settings
    from lotuspond.fleet.authority import FleetDependencies
    from lotuspond.fleet.catalog import InstalledCatalog
    from lotuspond.security import RateLimiter

    fleet = FleetDependencies(
        recovery=h.fleet_recovery,
        catalog=InstalledCatalog(),
        installation_id="test-installation",
        limiter=RateLimiter(Settings(env="test")),
    )
    h.closing.append(fleet)
    return RecoveryFence(fleet)


def own_sweeper(h):
    return ExecutionSweeper(leases=h.leases, recovery=own_fence(h))


def until(h, at_ms):
    """Wait in real database time until it passes `at_ms`; no column is ever edited."""
    while True:
        with Session(h.engine) as db:
            if database_now_ms(db) > at_ms:
                return
        sleep(0.05)


def reserve(h, attempt_id, active_ms=10000, actor=None):
    """Reserve a step's attempt as the scheduler would (Task 5)."""
    with Session(h.engine) as db, db.begin():
        h.budgets.reserve_attempt(
            db, actor or h.actor, attempt_id, Demand(active_ms).demand(attempt_id)
        )
