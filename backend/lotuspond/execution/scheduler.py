"""Bounded per-environment FIFO, persistent round-robin and no dispatch authority."""

import json

from lotuspond_runtime.protocol import CanonicalUUID, Scope
from sqlalchemy import func, select

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
from .authorization import authorize_execution, lock, record, scoped
from .budgets import BudgetDemand
from .contracts import Contract
from .deployments import _pool, load_deployment
from .errors import ExecutionError, execution_service
from .publication import _Lookup


class ScheduledAttempt(Contract):
    attempt_id: CanonicalUUID


class Scheduler:
    def __init__(self, budgets, demand_provider, *, test_mode=False):
        if not test_mode and getattr(demand_provider, "test_only", False):
            raise ValueError("Synthetic providers require test mode")
        self.budgets, self.demand_provider = budgets, demand_provider
        self._slot = _Lookup()

    def close(self):
        self._slot.closed = True

    @execution_service
    def schedule_batch(self, db, installation_id, limit=25):
        if (
            installation_id != self.budgets.installation_id
            or type(limit) is not int
            or not 1 <= limit <= 25
        ):
            raise ExecutionError("invalid_request")
        cursor = self.budgets._cursor(db)
        # IDs only, bounded to eligible environment heads. A full environment
        # cannot conceal another behind its queue. A head failure never bypasses
        # FIFO within that environment.
        count = db.scalar(select(func.count()).select_from(ExecutionCapacity))
        if not count:
            return ()
        start = cursor.cursor % count
        query = select(ExecutionCapacity).order_by(ExecutionCapacity.environment_id)
        ordered = list(db.scalars(query.offset(start).limit(25)))
        if len(ordered) < min(25, count):
            ordered += list(db.scalars(query.limit(min(25, count) - len(ordered))))
        heads = []
        positions = {}
        traversed = 0
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
            candidate = db.execute(
                select(ExecutionAttempt.id, Execution.id)
                .join(Execution, Execution.id == ExecutionAttempt.execution_id)
                .where(
                    Execution.workspace_id == scope.workspace_id,
                    Execution.project_id == scope.project_id,
                    Execution.environment_id == scope.environment_id,
                    Execution.requested_state == "running",
                    Execution.state.in_(("pending", "running")),
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
                .limit(1)
            ).first()
            if candidate:
                execution = record(db, Execution, scope, candidate[1])
                heads.append((scope, candidate[0], execution))
            if len(heads) >= limit:
                break
        # Pre-acquire every shared parent before the first execution child. This
        # prevents cross-scope batches from inverting E03's parent order.
        for workspace_id in sorted({scope.workspace_id for scope, _, _ in heads}):
            lock(db, select(Workspace).where(Workspace.id == workspace_id))
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
            pool = record(db, RuntimePool, scope, revision.pool_id, locked=True)
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
            record(db, Connection, scope, identifier, locked=True)
        for scope, _, revision in sorted(eligible, key=lambda item: item[2].pool_id):
            try:
                _pool(db, scope, revision.pool_id)
            except ExecutionError:
                blocked.add(scope.environment_id)
        for scope, _, _ in sorted(eligible, key=lambda item: item[0].environment_id):
            lock(db, scoped(ExecutionCapacity, scope))
        for scope, attempt_id, revision in sorted(
            eligible, key=lambda item: (item[2].deployment_id, item[2].id)
        ):
            if scope.environment_id in blocked:
                continue
            try:
                load_deployment(
                    db, actors[attempt_id], revision.id, dependencies=self.budgets.deployments
                )
            except ExecutionError:
                blocked.add(scope.environment_id)
        # All same-kind child IDs are acquired in sorted order, regardless of
        # the round-robin traversal used to make scheduling decisions.
        for scope, _, execution in sorted(heads, key=lambda item: item[2].id):
            record(db, Execution, scope, execution.id, locked=True)
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
            if cursor.active >= cursor.active_limit:
                break
            # A turn includes a handled refusal, but never a head merely
            # collected before another environment filled the last slot.
            next_position = (positions[scope.environment_id] + 1) % count
            if attempt_id not in actors or scope.environment_id in blocked:
                execution.reason = "authority_revoked"
                continue
            try:
                demand = BudgetDemand.model_validate(
                    self._slot.call(
                        self.demand_provider,
                        "demand",
                        (attempt_id,),
                        self.budgets.timeout_seconds,
                        "price_unavailable",
                    )
                )
                # Refusal leaves no partial reservation/counter writes.
                with db.begin_nested():
                    self.budgets.reserve_attempt(db, actors[attempt_id], attempt_id, demand)
                execution.reason = None
                selected.append(ScheduledAttempt(attempt_id=attempt_id))
            except ExecutionError as error:
                execution.reason = {
                    "capacity_unavailable": "capacity",
                    "budget_exceeded": "budget",
                    "entitlement_unavailable": "entitlement_unavailable",
                    "forbidden": "authority_revoked",
                }.get(error.code, "provider_unavailable")
                continue
        cursor.cursor = next_position if heads else (start + traversed) % count
        db.flush()
        return tuple(selected)
