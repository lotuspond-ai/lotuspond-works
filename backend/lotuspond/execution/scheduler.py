"""Bounded per-environment FIFO, persistent round-robin and no dispatch authority."""

import json
from time import monotonic

from lotuspond_runtime.protocol import CanonicalUUID, Scope
from sqlalchemy import func, or_, select

from .. import authority_gate
from ..fleet.authority import database_now_ms
from ..models import (
    Connection,
    Execution,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionDeploymentRevision,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    RuntimePool,
    RuntimePoolPolicy,
    Workspace,
)
from ..security import Principal
from .authorization import authorize_execution, lock, record, scoped, share
from .budgets import BudgetDemand, installation_active
from .contracts import Contract
from .deployments import _pool, gates, load_deployment
from .errors import ExecutionError, execution_service
from .orchestrator import OUT_OF_TIME, mark_runnable, runs_clause
from .publication import _Lookup


class ScheduledAttempt(Contract):
    attempt_id: CanonicalUUID


# Candidates read from one environment's front per transaction: the out-of-time ones
# it can retire before reaching a head.
FRONT = 25


def _out_of_time(deadline_ms, demand, now) -> bool:
    """Its step's reserved active time no longer fits before the deadline.

    `reserve_attempt` refuses such a step (`budget_exceeded`) now and on every later
    turn: time only shrinks and the attempt's demand is its own. Nothing in a program
    proceeds past a step that never runs, so the execution can only fail.
    """
    remaining = deadline_ms - now
    return remaining <= 0 or demand.active_ms > remaining


def _retire(db, execution) -> None:
    """Never a candidate again (`OUT_OF_TIME`), and due now: progression fails it at
    once (`past_deadline`), as at its deadline. The caller holds its row lock."""
    execution.reason = OUT_OF_TIME
    mark_runnable(db, execution, database_now_ms(db))


class Scheduler:
    def __init__(self, budgets, demand_provider, *, test_mode=False):
        if not test_mode and getattr(demand_provider, "test_only", False):
            raise ValueError("Synthetic providers require test mode")
        self.budgets, self.demand_provider = budgets, demand_provider
        self._slot = _Lookup()

    def close(self):
        self._slot.closed = True

    def _demand(self, attempt_id):
        """The attempt's demand, or the error its lookup raised: raised again under the
        head's locks, where its reason is written, as when it was looked up there."""
        try:
            return BudgetDemand.model_validate(
                self._slot.call(
                    self.demand_provider,
                    "demand",
                    (attempt_id,),
                    self.budgets.timeout_seconds,
                    "price_unavailable",
                )
            )
        except Exception as error:
            return error

    @execution_service
    def schedule_batch(self, db, installation_id, limit=1, *, epoch=None):
        """Reserve one environment head; with `epoch`, only executions current under it.

        One head per transaction, for every caller. A transaction holds its head's
        rows until it commits, and takes the installation cursor last
        (`BudgetService._leaf`), so it may not lock a second head after that. `limit`
        must be 1, so no caller silently gets fewer than it asked for: one that wants
        more runs more transactions. The round-robin position is read without a lock
        and written at the leaf; a visibly full installation takes no lock at all.
        A restored execution is held until an owner releases it (`recovery.py`), and
        `reserve_attempt` refuses one whatever the caller passes.

        Demands are looked up before the first lock, for up to `FRONT` candidates
        from the front of one environment's queue. Each one whose step no longer fits
        before its deadline (`_out_of_time`) is retired (`_retire`), under the same
        locks and in the same order as the head: it is never a candidate again, and
        progression fails it at once. The first that fits is the head. Under overload
        the front is all out of time, and retiring it one turn at a time would never
        reach a head that can still run.
        """
        if installation_id != self.budgets.installation_id or type(limit) is not int or limit != 1:
            raise ExecutionError("invalid_request")
        installation = self.budgets._installation(db)
        if installation_active(db) >= installation.active_limit:
            return ()
        # IDs only, bounded to eligible environment heads. A full environment
        # cannot conceal another behind its queue. A head failure never bypasses
        # FIFO within that environment. An execution past its deadline is not a
        # head, nor is one retired as out of time: it can never run, and it is failed
        # (`deadline_exceeded`), so under overload the queue serves what can still run
        # instead of stalling.
        now = database_now_ms(db)
        count = db.scalar(select(func.count()).select_from(ExecutionCapacity))
        if not count:
            return ()
        start = installation.cursor % count
        query = select(ExecutionCapacity).order_by(ExecutionCapacity.environment_id)
        ordered = list(db.scalars(query.offset(start).limit(25)))
        if len(ordered) < min(25, count):
            ordered += list(db.scalars(query.limit(min(25, count) - len(ordered))))
        heads, retired, demands = [], {}, {}
        positions = {}
        traversed = 0
        began = monotonic()
        for offset, capacity in enumerate(ordered):
            traversed = offset + 1
            positions[capacity.environment_id] = (start + offset) % count
            if capacity.active >= capacity.active_limit:
                continue
            scope = Scope(
                workspace_id=capacity.workspace_id,
                project_id=capacity.project_id,
                environment_id=capacity.environment_id,
            )
            candidates = db.execute(
                select(ExecutionAttempt.id, Execution.id, Execution.deadline_ms)
                .join(Execution, Execution.id == ExecutionAttempt.execution_id)
                .where(
                    Execution.workspace_id == scope.workspace_id,
                    Execution.project_id == scope.project_id,
                    Execution.environment_id == scope.environment_id,
                    runs_clause(ExecutionAttempt.id),
                    Execution.state.in_(("pending", "running")),
                    Execution.deadline_ms > now,
                    or_(Execution.reason.is_(None), Execution.reason != OUT_OF_TIME),
                    *(() if epoch is None else (Execution.recovery_epoch == epoch,)),
                    ExecutionAttempt.state == "reserved",
                    ~select(ExecutionUsageReservation.id)
                    .where(ExecutionUsageReservation.attempt_id == ExecutionAttempt.id)
                    .exists(),
                )
                .order_by(
                    Execution.admission_sequence,
                    Execution.id,
                    ExecutionAttempt.ordinal,
                    ExecutionAttempt.id,
                )
                .limit(FRONT)
            ).all()
            for attempt_id, execution_id, deadline_ms in candidates:
                if execution_id in retired:
                    continue  # another attempt of one already retired
                if retired and monotonic() - began > self.budgets.timeout_seconds:
                    break  # a slow provider: retire these, look further next turn
                demands[attempt_id] = self._demand(attempt_id)
                if not isinstance(demands[attempt_id], Exception) and _out_of_time(
                    deadline_ms, demands[attempt_id], now
                ):
                    retired[execution_id] = scope
                    continue
                heads.append((scope, attempt_id, record(db, Execution, scope, execution_id)))
                break
            if heads or retired:
                break  # one environment's rows per transaction
        # Pre-acquire every shared parent before the first execution child. This
        # prevents cross-scope batches from inverting E03's parent order.
        scopes = [scope for scope, _, _ in heads] + list(retired.values())
        # The fair gates come first, before any row lock (`authority_gate`): each
        # workspace's, then each head's pool's.
        for workspace_id in sorted({scope.workspace_id for scope in scopes}):
            authority_gate.workspace(db, workspace_id)
        for scope, _, execution in heads:
            gates(db, scope, execution.deployment_revision_id, users=[execution.initiating_user_id])
        for workspace_id in sorted({scope.workspace_id for scope in scopes}):
            share(db, select(Workspace).where(Workspace.id == workspace_id))
        actors = {}
        eligible = []
        for scope, attempt_id, execution in sorted(
            heads,
            key=lambda item: (
                item[0].workspace_id,
                item[2].initiating_user_id,
                item[2].initiating_api_key_id or "",
            ),
        ):
            principal = Principal(
                execution.initiating_api_key_id or execution.initiating_user_id,
                scope.workspace_id,
                "service" if execution.initiating_api_key_id else "member",
                None if execution.initiating_api_key_id else execution.initiating_user_id,
            )
            try:
                actors[attempt_id] = authorize_execution(db, principal, scope, "execute")
            except ExecutionError:
                continue
            revision = record(
                db, ExecutionDeploymentRevision, scope, execution.deployment_revision_id
            )
            eligible.append((scope, attempt_id, revision))
        blocked = set()
        # All pools precede all connections, including overlapping policy sets.
        connections = {}
        for scope, _, revision in sorted(eligible, key=lambda item: item[2].pool_id):
            pool = record(db, RuntimePool, scope, revision.pool_id, shared=True)
            policy = db.scalar(
                scoped(RuntimePoolPolicy, scope).where(
                    RuntimePoolPolicy.pool_id == pool.id,
                    RuntimePoolPolicy.version == pool.current_policy_version,
                )
            )
            if policy is not None:
                for identifier in json.loads(policy.canonical_policy)["connection_ids"]:
                    connections[identifier] = scope
        for identifier, scope in sorted(connections.items()):
            record(db, Connection, scope, identifier, shared=True)
        # What is checked here, under these locks, `reserve_attempt` reuses in this
        # transaction (`held_parents`) instead of checking it again.
        policies, capacities, loaded = {}, {}, {}
        for scope, attempt_id, revision in sorted(eligible, key=lambda item: item[2].pool_id):
            try:
                policies[attempt_id] = _pool(db, scope, revision.pool_id)
            except ExecutionError:
                blocked.add(scope.environment_id)
        for scope, _, _ in sorted(eligible, key=lambda item: item[0].environment_id):
            capacities[scope.environment_id] = lock(db, scoped(ExecutionCapacity, scope))
        for scope, attempt_id, revision in sorted(
            eligible, key=lambda item: (item[2].deployment_id, item[2].id)
        ):
            if scope.environment_id in blocked:
                continue
            try:
                _, loaded[attempt_id] = load_deployment(
                    db, actors[attempt_id], revision.id, dependencies=self.budgets.deployments
                )
            except ExecutionError:
                blocked.add(scope.environment_id)
        # All same-kind child IDs are acquired in sorted order, regardless of
        # the round-robin traversal used to make scheduling decisions.
        rows = dict(retired) | {execution.id: scope for scope, _, execution in heads}
        locked = {
            identifier: record(db, Execution, rows[identifier], identifier, locked=True)
            for identifier in sorted(rows)
        }
        for identifier in retired:
            execution = locked[identifier]
            if execution.requested_state == "running" and execution.state in {"pending", "running"}:
                _retire(db, execution)
        attempts = [
            (scope, record(db, ExecutionAttempt, scope, identifier))
            for scope, identifier, _ in heads
        ]
        for scope, attempt in sorted(attempts, key=lambda item: item[1].step_instance_id):
            record(db, ExecutionStepInstance, scope, attempt.step_instance_id, locked=True)
        for scope, attempt in sorted(attempts, key=lambda item: item[1].id):
            record(db, ExecutionAttempt, scope, attempt.id, locked=True)
        selected = []
        next_position = start
        for scope, attempt_id, execution in heads:
            # A turn includes a handled refusal, the leaf's included.
            next_position = (positions[scope.environment_id] + 1) % count
            if attempt_id not in actors or scope.environment_id in blocked:
                execution.reason = "authority_revoked"
                continue
            demand = demands[attempt_id]
            try:
                if isinstance(demand, Exception):
                    raise demand
                # Written before the reservation, which ends at the leaf.
                execution.reason = None
                parents = None
                if capacities.get(scope.environment_id) is not None:
                    parents = self.budgets.held_parents(
                        db,
                        actors[attempt_id],
                        installation,
                        capacities[scope.environment_id],
                        loaded[attempt_id],
                        policies[attempt_id],
                    )
                # Refusal leaves no partial reservation/counter writes.
                with db.begin_nested():
                    self.budgets.reserve_attempt(
                        db, actors[attempt_id], attempt_id, demand, parents=parents
                    )
                selected.append(ScheduledAttempt(attempt_id=attempt_id))
            except ExecutionError as error:
                if error.code == "forbidden" and execution.deadline_ms <= database_now_ms(db):
                    # Expired since the head was chosen: not a revoked authority.
                    execution.reason = "deadline_exceeded"
                    continue
                if error.code == "budget_exceeded" and _out_of_time(
                    execution.deadline_ms, demand, database_now_ms(db)
                ):
                    # Out of time since its demand was looked up: retired like the rest.
                    _retire(db, execution)
                    continue
                execution.reason = {
                    "capacity_unavailable": "capacity",
                    "budget_exceeded": "budget",
                    "entitlement_unavailable": "entitlement_unavailable",
                    "forbidden": "authority_revoked",
                }.get(error.code, "provider_unavailable")
                continue
        position = next_position if heads else (start + traversed) % count
        if heads or position != start:
            # Last: only the cursor's own row and the sum follow (`_leaf`).
            cursor = self.budgets._leaf(db)
            cursor.cursor = position
            self.budgets._record_active(db, cursor)
        return tuple(selected)
