"""Each lookup and permission check once per item (owner decision, 2 November 2026).

Within one transaction, a shared operation's authorization and a pool's policy are
checked once at its top level and reused: their rows stay shared and their gates
held to its end. A check made inside a savepoint is not kept (its rollback releases
what it locked), nothing is kept across transactions, and an API key's expiry is
checked again on the clock. A scheduling transaction's parents are reused by
`reserve_attempt` in that transaction only, and `_final` still loads the deployment
again after the grant lookups.
"""

import time
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from lotuspond.execution import budgets as budgets_module
from lotuspond.execution import scheduler as scheduler_module
from lotuspond.execution.authorization import authorize_execution, lock, reauthorize, scoped
from lotuspond.execution.deployments import _pool, gates
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.scheduler import Scheduler
from lotuspond.models import (
    ApiKey,
    ExecutionCapacity,
    ExecutionDeploymentRevision,
    ExecutionUsageReservation,
    now,
)
from lotuspond.security import Principal

from .test_budgets import budget_harness as budget_harness
from .test_budgets import demand
from .test_budgets import harness as harness
from .test_scheduler import Demands


@pytest.fixture
def pg(budget_harness):
    h = budget_harness
    if h.engine.dialect.name != "postgresql":
        pytest.skip("per-transaction reuse is PostgreSQL only")
    return h


class Statements:
    """The SQL one engine runs while counting, by the tables each statement names."""

    def __init__(self, engine):
        self.engine, self.seen = engine, []

    def _note(self, connection, cursor, statement, parameters, context, executemany):
        self.seen.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._note)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._note)

    def naming(self, table):
        return [s for s in self.seen if f"FROM {table}" in s or f"JOIN {table}" in s]


def test_a_shared_check_is_made_once_per_transaction(pg):
    h = pg
    with Session(h.engine) as db, db.begin():
        first = authorize_execution(db, h.principal, h.scope, "execute")
        with Statements(h.engine) as counted:
            again = reauthorize(db, first, "execute")
            in_savepoint = None
            with db.begin_nested():
                in_savepoint = authorize_execution(db, h.principal, h.scope, "execute")
        assert again is first and in_savepoint is first
        assert counted.naming("memberships") == [] and counted.naming("users") == []


def test_a_check_inside_a_savepoint_is_not_kept(pg):
    h = pg
    with Session(h.engine) as db, db.begin():
        nested = db.begin_nested()
        authorize_execution(db, h.principal, h.scope, "execute")
        nested.rollback()  # releases the rows it shared
        with Statements(h.engine) as counted:
            authorize_execution(db, h.principal, h.scope, "execute")
        assert len(counted.naming("memberships")) == 1


def test_nothing_is_kept_across_transactions(pg):
    h = pg
    with Session(h.engine) as db:
        with db.begin():
            authorize_execution(db, h.principal, h.scope, "execute")
        with db.begin(), Statements(h.engine) as counted:
            authorize_execution(db, h.principal, h.scope, "execute")
        assert len(counted.naming("memberships")) == 1


def test_an_exclusive_check_is_made_every_time(pg):
    h = pg
    with Session(h.engine) as db, db.begin():
        authorize_execution(db, h.principal, h.scope, "manage")
        with Statements(h.engine) as counted:
            authorize_execution(db, h.principal, h.scope, "manage")
        assert len(counted.naming("memberships")) == 1


def test_a_reused_key_check_still_expires_on_the_clock(pg):
    h = pg
    with Session(h.engine) as db, db.begin():
        key = ApiKey(
            workspace_id=h.scope.workspace_id,
            name="expiring",
            prefix="expiring",
            token_hash="9" * 64,
            scopes=["execute"],
            expires_at=now() + timedelta(seconds=1.5),
            created_by_user_id=h.principal.user_id,
        )
        db.add(key)
        db.flush()
        principal = Principal(key.id, h.scope.workspace_id, "service")
    with Session(h.engine) as db, db.begin():
        actor = authorize_execution(db, principal, h.scope, "execute")
        assert reauthorize(db, actor, "execute") is actor
        time.sleep(2)
        with pytest.raises(ExecutionError, match="forbidden"):
            reauthorize(db, actor, "execute")


def test_a_pool_policy_is_read_once_per_transaction(pg):
    h = pg
    with Session(h.engine) as db, db.begin():
        gates(db, h.scope, h.revision_id, users=[h.actor.user_id])
        revision = db.get(ExecutionDeploymentRevision, h.revision_id)
        policy = _pool(db, h.scope, revision.pool_id)
        with Statements(h.engine) as counted:
            assert _pool(db, h.scope, revision.pool_id) is policy
        assert counted.seen == []


def held(h, db):
    """The parents in `_parents`' order: gates, pool, capacity, revision."""
    gates(db, h.scope, h.revision_id, users=[h.actor.user_id])
    revision = db.get(ExecutionDeploymentRevision, h.revision_id)
    policy = _pool(db, h.scope, revision.pool_id)
    capacity = lock(db, scoped(ExecutionCapacity, h.scope))
    installation = h.budgets._installation(db)
    return h.budgets.held_parents(db, h.actor, installation, capacity, revision, policy)


def test_held_parents_are_refused_in_another_transaction_or_for_another_actor(pg):
    h = pg
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
    with Session(h.engine) as db, db.begin():
        parents = held(h, db)
    with Session(h.engine) as db, db.begin():
        with pytest.raises(ExecutionError, match="invalid_request"):
            h.budgets.reserve_attempt(db, h.actor, attempt, demand(), parents=parents)
    with Session(h.engine) as db, db.begin():
        parents = held(h, db)
        other = authorize_execution(db, h.principal, h.scope, "signal")
        with pytest.raises(ExecutionError, match="invalid_request"):
            h.budgets.reserve_attempt(db, other, attempt, demand(), parents=parents)
    with Session(h.engine) as db, db.begin(), db.begin_nested():
        with pytest.raises(ExecutionError, match="invalid_request"):
            held(h, db)


def test_a_scheduled_head_loads_its_deployment_twice_not_three_times(pg, monkeypatch):
    """Once by the scheduler under its locks, once by `_final` after the grant
    lookups; `reserve_attempt` reuses the scheduler's parents."""
    h = pg
    with Session(h.engine) as db, db.begin():
        first, _ = h.add(db)
    loads = []

    def counting(original):
        def load(*args, **kwargs):
            loads.append(kwargs.get("include_assurance", False))
            return original(*args, **kwargs)

        return load

    monkeypatch.setattr(
        scheduler_module, "load_deployment", counting(scheduler_module.load_deployment)
    )
    monkeypatch.setattr(budgets_module, "load_deployment", counting(budgets_module.load_deployment))
    scheduler = Scheduler(h.budgets, Demands(), test_mode=True)
    try:
        with Session(h.engine) as db, db.begin():
            scheduled = scheduler.schedule_batch(db, "test-installation")
        assert [value.attempt_id for value in scheduled] == [first]
        assert loads == [False, True]
        with Session(h.engine) as db:
            assert db.scalar(
                select(ExecutionUsageReservation.attempt_id).where(
                    ExecutionUsageReservation.attempt_id == first
                )
            )
    finally:
        scheduler.close()
