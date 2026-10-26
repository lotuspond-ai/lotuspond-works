"""Caller-transaction reservations, conservative liabilities and reconciled capacity.

Lock order: installation cursor, current workspace/account, pool and connection
parents, environment capacity, deployment/revision, execution, sorted steps,
sorted attempts, reservations. Never commit here. Composing callers must enter
at the same parent order, and roll back the entire operation on refusal.
"""

import json
import math
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from lotuspond_runtime.protocol import CanonicalUUID, OpaqueId, SafeInt, Scope
from lotuspond_sdk.activity import UsageObservation
from lotuspond_sdk.executable import validate_executable_package
from lotuspond_sdk.executable.package import ArtifactRef
from pydantic import TypeAdapter
from sqlalchemy import func, literal, select

from ..fleet.authority import RecoverySnapshot, database_now_ms
from ..models import (
    ExecutableDependency,
    ExecutablePublication,
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionDeploymentRevision,
    ExecutionLease,
    ExecutionReconciliationObservation,
    ExecutionRequest,
    ExecutionSchedulerCursor,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    ExecutionWait,
)
from .assurance import check_assurance_current
from .authorization import lock, reauthorize, record, scoped
from .contracts import Contract, DeploymentConfig, canonical_value
from .deployments import _body_steps, _configuration, _pool, _stored_configuration, load_deployment
from .entitlement import EntitlementGrant, PolicyGrant
from .errors import ExecutionError, execution_service
from .program import ProgramError, program_from, resolve_occurrence
from .publication import _Lookup, current_snapshot, load_publication

MAX_SAFE = 9007199254740991
SHARED = "capacity.shared:"


class BudgetDemand(Contract):
    input_tokens: SafeInt
    output_tokens: SafeInt
    cost_microusd: SafeInt
    active_ms: SafeInt


class ReservationView(Contract):
    id: CanonicalUUID
    attempt_id: CanonicalUUID
    state: str


class ChargeAuthority(Protocol):
    def bound(self, dependency: ArtifactRef, demand: BudgetDemand) -> BudgetDemand: ...


def steps(source):
    return [
        step.model_dump(mode="json")
        for step in _body_steps(validate_executable_package(source).body)
    ]


def active_liability(row, now_ms):
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    started = int(created_at.timestamp() * 1000)
    stopped = row.compute_stopped_at_ms if row.compute_stopped_at_ms is not None else now_ms
    return max(row.active_ms, stopped - started, 0)


def liability(row, field, now_ms):
    if row.state == "released":
        return 0  # stopped before any claim: nothing ran, so nothing is charged
    if field == "active_ms":
        return active_liability(row, now_ms)
    observed = getattr(row, "observed_" + field)
    reserved = getattr(row, field)
    if observed is None:
        return reserved
    # An observation alone does not prove that the process stopped charging.
    # Before reconciled stop keep the larger liability, including actual overrun.
    return observed if row.compute_stopped_at_ms is not None else max(reserved, observed)


def view(row):
    return ReservationView(id=row.id, attempt_id=row.attempt_id, state=row.state)


class BudgetService:
    def __init__(
        self,
        *,
        entitlement,
        tenant_policy,
        charges,
        recovery,
        installation_id,
        deployments,
        timeout_seconds=5.0,
        test_mode=False,
    ):
        self.installation_id = TypeAdapter(OpaqueId).validate_python(installation_id, strict=True)
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 5
        ):
            raise ValueError("Invalid provider timeout")
        if not test_mode and any(
            getattr(p, "test_only", False) for p in (entitlement, tenant_policy, charges, recovery)
        ):
            raise ValueError("Synthetic providers require test mode")
        self.entitlement, self.tenant_policy, self.charges, self.recovery = (
            entitlement,
            tenant_policy,
            charges,
            recovery,
        )
        self.deployments, self.timeout_seconds = deployments, timeout_seconds
        self._slots = {
            name: _Lookup() for name in ("entitlement", "tenant_policy", "charges", "recovery")
        }

    def close(self):
        for slot in self._slots.values():
            slot.closed = True

    def _call(self, name, method, args, code):
        return self._slots[name].call(getattr(self, name), method, args, self.timeout_seconds, code)

    def _cursor(self, db):
        row = lock(
            db,
            select(ExecutionSchedulerCursor).where(
                ExecutionSchedulerCursor.installation_id == self.installation_id
            ),
        )
        if row is None:
            raise ExecutionError("capacity_unavailable")
        return row

    def _parents(self, db, actor, revision_id):
        cursor = self._cursor(db)
        actor = reauthorize(db, actor, "execute")
        hint = record(db, ExecutionDeploymentRevision, actor.scope, revision_id)
        policy = _pool(db, actor.scope, hint.pool_id)
        capacity = lock(db, scoped(ExecutionCapacity, actor.scope))
        if capacity is None:
            raise ExecutionError("capacity_unavailable")
        _, revision = load_deployment(db, actor, revision_id, dependencies=self.deployments)
        return cursor, capacity, revision, policy

    def _grants(self, db, scope):
        now = database_now_ms(db)
        try:
            recovery = self._call(
                "recovery", "current", (self.installation_id,), "recovery_unavailable"
            )
            if (
                type(recovery) is not RecoverySnapshot
                or not recovery.active
                or recovery.installation_id != self.installation_id
            ):
                raise ValueError
        except Exception:
            raise ExecutionError("recovery_unavailable") from None
        try:
            grant = EntitlementGrant.model_validate(
                self._call("entitlement", "current", (scope, now), "entitlement_unavailable")
            )
            if (
                grant.scope != scope
                or not grant.execute
                or grant.installation_id != self.installation_id
                or grant.recovery_epoch != recovery.epoch
            ):
                raise ValueError
        except Exception:
            raise ExecutionError("entitlement_unavailable") from None
        try:
            tenant = PolicyGrant.model_validate(
                self._call("tenant_policy", "current", (scope, now), "authority_unavailable")
            )
            if tenant.scope != scope:
                raise ValueError
        except Exception:
            raise ExecutionError("authority_unavailable") from None
        self._expiry(db, grant, tenant)
        return grant, tenant, recovery

    def _expiry(self, db, grant, tenant):
        now = database_now_ms(db)
        if grant.expires_at_ms <= now:
            raise ExecutionError("entitlement_unavailable")
        if tenant.expires_at_ms <= now:
            raise ExecutionError("authority_unavailable")

    def _final(self, db, actor, revision, grant, tenant, recovery, execution=None):
        # These exact parents were acquired before execution children. Re-entry
        # reloads mutable keys/source pins and independently current publisher trust.
        _, _, assurance = load_deployment(
            db, actor, revision.id, dependencies=self.deployments, include_assurance=True
        )
        current = self._call("recovery", "current", (self.installation_id,), "recovery_unavailable")
        if current != recovery:
            raise ExecutionError("recovery_unavailable")
        # Re-read grants after later provider waits; these are point-in-time provider
        # decisions, not durable licenses. The final clock applies to every grant.
        fresh, policy, epoch = self._grants(db, actor.scope)
        if fresh != grant or policy != tenant or epoch != recovery:
            raise ExecutionError("entitlement_unavailable")
        # Last independent publisher lookup precedes pure current DB checks.
        # In particular a context pin may expire during a later entitlement or
        # recovery lookup; an earlier load_deployment cannot authorize it now.
        snapshot = self.deployments.current(db, actor.scope)
        publication, package, links, rows, sources = load_publication(
            db, actor.scope, revision.publication_id, snapshot
        )
        config = _stored_configuration(db, actor.scope, revision, links)
        pool = _pool(db, actor.scope, revision.pool_id)  # held before execution
        _configuration(db, actor.scope, config, package, links, rows, sources, pool, snapshot)
        reauthorize(db, actor, "execute")
        current_snapshot(db, snapshot)
        self._expiry(db, grant, tenant)
        check_assurance_current(db, assurance, package, snapshot, sources)
        if execution and (
            execution.deadline_ms <= database_now_ms(db)
            or execution.requested_state != "running"
            or execution.state not in {"pending", "running"}
        ):
            raise ExecutionError("forbidden")

    def _limits(self, db, scope, revision, grant, tenant):
        publication = record(db, ExecutablePublication, scope, revision.publication_id)
        package = json.loads(publication.canonical_package)
        configuration = DeploymentConfig.model_validate_json(revision.canonical_configuration)
        limits = [
            package["limits"],
            configuration.limits.model_dump(),
            grant.limits.model_dump(),
            tenant.limits.model_dump(),
        ]
        rows = list(
            db.scalars(
                scoped(ExecutablePublication, scope)
                .join(
                    ExecutableDependency,
                    ExecutableDependency.dependency_publication_id == ExecutablePublication.id,
                )
                .where(ExecutableDependency.publication_id == publication.id)
            )
        )
        packages = [package]
        catalog = {}
        for row in rows:
            source = json.loads(row.canonical_package)
            catalog[row.package_sha256] = source
            if row.kind == "workflow":
                packages.append(source)
                limits.append(source["limits"])
        # Conservatively intersect all declared agent scopes, including reused
        # agents and subworkflows; no child receives a fresh root allowance.
        for source in packages:
            for step in steps(source):
                if step["kind"] == "agent":
                    limits.append(step["limits"])
        for source in catalog.values():
            if "limits" in source.get("definition", {}):
                limits.append(source["definition"]["limits"])
        merged = {
            name: min(value[name] for value in limits if name in value)
            for name in configuration.limits.model_dump()
        }
        merged["active_ms"] = min(
            [merged["active_seconds"] * 1000]
            + [v["timeout_seconds"] * 1000 for v in limits if "timeout_seconds" in v]
        )
        return merged, packages, catalog

    @execution_service
    def reserve_admission(self, db, actor, deployment_revision_id) -> None:
        _, capacity, revision, _ = self._parents(db, actor, deployment_revision_id)
        grant, tenant, recovery = self._grants(db, actor.scope)
        limits, _, _ = self._limits(db, actor.scope, revision, grant, tenant)
        if (
            capacity.pending >= min(capacity.pending_limit, limits["pending_capacity"])
            or capacity.next_admission_sequence >= MAX_SAFE
        ):
            raise ExecutionError("capacity_unavailable")
        self._final(db, actor, revision, grant, tenant, recovery)
        capacity.pending += 1
        capacity.next_admission_sequence += 1
        db.flush()

    def _dependency(self, step, packages, catalog, parent):
        # The exact persisted occurrence selects the node (Task 8 resolver); an
        # arbitrary caller ID or a same-named node elsewhere cannot select a model.
        try:
            program = program_from(
                validate_executable_package(packages[0]),
                dict(enumerate([*packages[1:], *catalog.values()])),
            )
            ref = resolve_occurrence(program, step.occurrence_path)
        except ProgramError:
            raise ExecutionError("dependency_unavailable") from None
        if ref.selector is not None or ref.step_id != step.node_id:
            raise ExecutionError("dependency_unavailable")
        source, node = ref.package.model_dump(mode="json"), ref.step.model_dump(mode="json")
        if node["kind"] == "connector_read":
            ref = next(d["ref"] for d in source["connectors"] if d["id"] == node["connector_ref"])
        elif node["kind"] == "agent":
            if node.get("model_ref"):
                ref = next(d["ref"] for d in source["models"] if d["id"] == node["model_ref"])
            else:
                agent = next(d["ref"] for d in source["agents"] if d["id"] == node["agent_ref"])
                ref = catalog[agent["sha256"]]["definition"]["model_ref"]
            # Child calls may be tools: require the charge provider to bound all
            # declared dependencies, not choose a caller-provided tool identity.
        else:
            raise ExecutionError("unsupported_step")
        references = [ref]
        if parent is not None and node["kind"] == "agent":
            for tool in node.get("read_tools", []):
                references.append(
                    next(d["ref"] for d in source["connectors"] if d["id"] == tool["connector_ref"])
                )
            if node.get("agent_ref"):
                references.extend(
                    t["connector_ref"]
                    for t in catalog[agent["sha256"]]["definition"].get("read_tools", [])
                )
        return [ArtifactRef.model_validate(ref) for ref in references]

    @execution_service
    def reserve_attempt(self, db, actor, attempt_id, demand, *, shared=False):
        """Reserve one attempt under the root execution budget.

        `shared` (Task 9) admits an agent's child attempt into its parent's running
        compute: the shared budget still applies, but no compute slot is taken. A
        `capacity.shared:` request row records this, so stopping that child returns
        no slot. Without it a child takes its own slot like any attempt.
        """
        demand = BudgetDemand.model_validate(demand)
        if type(shared) is not bool:
            raise ExecutionError("invalid_request")
        hint = record(db, ExecutionAttempt, actor.scope, attempt_id)
        execution_hint = record(db, Execution, actor.scope, hint.execution_id)
        cursor, capacity, revision, pool = self._parents(
            db, actor, execution_hint.deployment_revision_id
        )
        execution = record(db, Execution, actor.scope, hint.execution_id, locked=True)
        step = record(db, ExecutionStepInstance, actor.scope, hint.step_instance_id, locked=True)
        attempt = record(db, ExecutionAttempt, actor.scope, attempt_id, locked=True)
        existing = lock(
            db,
            scoped(ExecutionUsageReservation, actor.scope).where(
                ExecutionUsageReservation.attempt_id == attempt.id
            ),
        )
        grant, tenant, recovery = self._grants(db, actor.scope)
        limits, packages, catalog = self._limits(db, actor.scope, revision, grant, tenant)
        if (
            execution.requested_state != "running"
            or execution.state not in {"pending", "running"}
            or execution.deadline_ms <= database_now_ms(db)
        ):
            raise ExecutionError("forbidden")
        if existing:
            self._final(db, actor, revision, grant, tenant, recovery, execution)
            return view(existing)
        if attempt.state != "reserved":
            raise ExecutionError("conflict")
        if shared:
            parent = (
                db.scalar(
                    scoped(ExecutionUsageReservation, actor.scope).where(
                        ExecutionUsageReservation.attempt_id == attempt.parent_attempt_id
                    )
                )
                if attempt.parent_attempt_id is not None
                else None
            )
            if parent is None or parent.compute_stopped_at_ms is not None:
                raise ExecutionError("conflict")
        bounded = []
        for dependency in self._dependency(step, packages, catalog, attempt.parent_attempt_id):
            try:
                bound = BudgetDemand.model_validate(
                    self._call("charges", "bound", (dependency, demand), "price_unavailable")
                )
                if any(
                    getattr(bound, key) < getattr(demand, key) for key in BudgetDemand.model_fields
                ):
                    raise ValueError
                bounded.append(bound)
            except Exception:
                raise ExecutionError("price_unavailable") from None
        demand = BudgetDemand(
            **{
                key: max(getattr(item, key) for item in bounded)
                for key in BudgetDemand.model_fields
            }
        )
        self._final(db, actor, revision, grant, tenant, recovery, execution)
        reservations = list(
            db.scalars(
                scoped(ExecutionUsageReservation, actor.scope).where(
                    ExecutionUsageReservation.execution_id == execution.id
                )
            )
        )
        now_ms = database_now_ms(db)
        for field, limit in (
            ("input_tokens", "max_input_tokens"),
            ("output_tokens", "max_output_tokens"),
            ("cost_microusd", "max_cost_microusd"),
            ("active_ms", "active_ms"),
        ):
            total = sum(liability(row, field, now_ms) for row in reservations)
            if total + getattr(demand, field) > limits[limit]:
                raise ExecutionError("budget_exceeded")
        remaining = min(
            execution.deadline_ms, execution.admitted_at_ms + limits["elapsed_seconds"] * 1000
        ) - database_now_ms(db)
        if demand.active_ms > remaining or remaining <= 0:
            raise ExecutionError("budget_exceeded")
        if len(reservations) >= limits["max_steps"]:
            raise ExecutionError("budget_exceeded")
        pool_active = db.scalar(
            select(func.count())
            .select_from(ExecutionUsageReservation)
            .join(Execution, Execution.id == ExecutionUsageReservation.execution_id)
            .join(
                ExecutionDeploymentRevision,
                ExecutionDeploymentRevision.id == Execution.deployment_revision_id,
            )
            .where(
                ExecutionUsageReservation.workspace_id == actor.scope.workspace_id,
                ExecutionUsageReservation.project_id == actor.scope.project_id,
                ExecutionUsageReservation.environment_id == actor.scope.environment_id,
                ExecutionDeploymentRevision.pool_id == revision.pool_id,
                ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
                ~select(ExecutionRequest.id)
                .where(
                    ExecutionRequest.workspace_id == actor.scope.workspace_id,
                    ExecutionRequest.project_id == actor.scope.project_id,
                    ExecutionRequest.environment_id == actor.scope.environment_id,
                    ExecutionRequest.request_key
                    == literal(SHARED) + ExecutionUsageReservation.attempt_id,
                )
                .exists(),
            )
        )
        if not shared and (
            cursor.active >= cursor.active_limit
            or capacity.active >= min(capacity.active_limit, limits["active_capacity"])
            or pool_active >= pool.max_concurrent_invocations
        ):
            raise ExecutionError("capacity_unavailable")
        row = ExecutionUsageReservation(
            created_at=datetime.fromtimestamp(database_now_ms(db) / 1000, UTC),
            **actor.scope.model_dump(),
            execution_id=execution.id,
            step_instance_id=step.id,
            attempt_id=attempt.id,
            **demand.model_dump(),
        )
        db.add(row)
        if shared:
            db.add(
                ExecutionRequest(
                    **actor.scope.model_dump(),
                    execution_id=execution.id,
                    request_key=SHARED + attempt.id,
                    digest=sha256((SHARED + attempt.id).encode()).hexdigest(),
                )
            )
            db.flush()
            return view(row)
        cursor.active += 1
        capacity.active += 1
        if execution.state == "pending":
            if capacity.pending < 1:
                raise ExecutionError("conflict")
            capacity.pending -= 1
            execution.state = "running"
        db.flush()
        return view(row)

    def _settlement_rows(self, db, attempt_id):
        # Internal observation APIs grant no external caller authority. Task6/10
        # must authenticate evidence before entering this service boundary.
        self._cursor(db)
        attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
        hint = db.get(ExecutionAttempt, attempt_id)
        if hint is None:
            raise ExecutionError("not_found")
        scope = Scope(
            workspace_id=hint.workspace_id,
            project_id=hint.project_id,
            environment_id=hint.environment_id,
        )
        from ..models import Workspace

        lock(db, select(Workspace).where(Workspace.id == scope.workspace_id))
        capacity = lock(db, scoped(ExecutionCapacity, scope))
        execution = record(db, Execution, scope, hint.execution_id, locked=True)
        record(db, ExecutionStepInstance, scope, hint.step_instance_id, locked=True)
        attempt = record(db, ExecutionAttempt, scope, attempt_id, locked=True)
        row = lock(
            db,
            scoped(ExecutionUsageReservation, scope).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            ),
        )
        if row is None:
            raise ExecutionError("not_found")
        return scope, capacity, execution, attempt, row

    @execution_service
    def settle_attempt(self, db, attempt_id, observation_id, usage):
        usage = UsageObservation.model_validate(usage)
        observation_id = TypeAdapter(OpaqueId).validate_python(observation_id, strict=True)
        scope, _, _, attempt, row = self._settlement_rows(db, attempt_id)
        digest = sha256(canonical_value(usage.model_dump())).hexdigest()
        existing = db.scalar(
            scoped(ExecutionReconciliationObservation, scope).where(
                ExecutionReconciliationObservation.attempt_id == attempt_id,
                ExecutionReconciliationObservation.source_id == "usage",
                ExecutionReconciliationObservation.observation_id == observation_id,
            )
        )
        if existing:
            if existing.digest != digest:
                raise ExecutionError("conflict")
            return view(row)
        if row.state == "settled" and row.settlement_digest != digest:
            raise ExecutionError("conflict")
        if usage.completed_at_ms > database_now_ms(db):
            raise ExecutionError("invalid_request")
        known = all(
            getattr(usage, key) is not None
            for key in ("input_tokens", "output_tokens", "cost_microusd")
        )
        for key in ("input_tokens", "output_tokens", "cost_microusd"):
            value = getattr(usage, key)
            previous = getattr(row, "observed_" + key)
            if previous is not None and value is not None and previous != value:
                raise ExecutionError("conflict")
            if value is not None:
                setattr(row, "observed_" + key, value)
        row.state = "settled" if known else "uncertain"
        row.completed_at_ms = usage.completed_at_ms
        if known:
            row.settlement_digest = digest
        elif attempt.state in {"reserved", "dispatched", "claimed"}:
            attempt.state = "uncertain"
        db.add(
            ExecutionReconciliationObservation(
                **scope.model_dump(),
                execution_id=attempt.execution_id,
                attempt_id=attempt_id,
                source_id="usage",
                observation_id=observation_id,
                digest=digest,
                code="usage_known" if known else "outcome_unknown",
                observed_at_ms=database_now_ms(db),
            )
        )
        db.flush()
        return view(row)

    @execution_service
    def confirm_stopped(self, db, attempt_id, observation_id):
        scope, capacity, _, attempt, row = self._settlement_rows(db, attempt_id)
        observation = record(db, ExecutionReconciliationObservation, scope, observation_id)
        if (
            observation.attempt_id != attempt_id
            or observation.code != "compute_stopped"
            or observation.observed_at_ms > database_now_ms(db)
        ):
            raise ExecutionError("conflict")
        if row.compute_stopped_at_ms is None:
            shared = db.scalar(
                scoped(ExecutionRequest, scope).where(
                    ExecutionRequest.request_key == SHARED + attempt_id
                )
            )
            if shared is None:  # a shared child took no compute slot
                cursor = self._cursor(db)  # already held from _settlement_rows
                if cursor.active < 1 or capacity is None or capacity.active < 1:
                    raise ExecutionError("conflict")
                cursor.active -= 1
                capacity.active -= 1
            row.compute_stopped_at_ms = observation.observed_at_ms
            row.active_ms = active_liability(row, observation.observed_at_ms)
        db.flush()
        return view(row)

    @execution_service
    def release_unstarted(self, db, attempt_id, epoch):
        """Free a stopped reservation whose attempt no runtime ever claimed.

        Nothing ran, so zero is known, as a runtime reports an outcome no provider
        could have caused; work that ends before it started charges nothing. That is
        a cancellation the capacity step stops (Task 11), and a delivery expiry, a
        reservation retirement or a decline (owner decision, 2026-10-26). Only a
        confirmed stop of a cancelled or failed attempt without a lease is released;
        anything claimed stays charged as observed, and an uncertain attempt (a
        restore's "unclaimed" proves nothing) keeps its uncertain liability. Only a
        `reserved` reservation changes: an `uncertain` or `settled` one, such as a
        restore-fenced attempt that reconciliation failed, is left as it is. So is one
        whose delivery was signed under another recovery epoch than the caller's
        current `epoch`: after a restore, its "unclaimed" proves nothing, so it keeps
        its reserved liability even when the fence has not reached it yet.
        """
        scope, _, _, attempt, row = self._settlement_rows(db, attempt_id)
        claimed = db.scalar(
            scoped(ExecutionLease, scope).where(ExecutionLease.attempt_id == attempt_id)
        )
        if (
            row is None
            or row.compute_stopped_at_ms is None
            or attempt.state not in {"cancelled", "failed"}
            or claimed is not None
        ):
            raise ExecutionError("conflict")
        stale = db.scalar(
            scoped(ExecutionActivityDelivery, scope).where(
                ExecutionActivityDelivery.attempt_id == attempt_id,
                ExecutionActivityDelivery.recovery_epoch != epoch,
            )
        )
        if row.state == "reserved" and stale is None:
            row.state = "released"
            db.flush()
        return view(row)

    @execution_service
    def retain_wait(self, db, actor, wait_id):
        hint = record(db, ExecutionWait, actor.scope, wait_id)
        execution_hint = record(db, Execution, actor.scope, hint.execution_id)
        _, capacity, revision, _ = self._parents(db, actor, execution_hint.deployment_revision_id)
        execution = record(db, Execution, actor.scope, hint.execution_id, locked=True)
        record(db, ExecutionStepInstance, actor.scope, hint.step_instance_id, locked=True)
        wait = record(db, ExecutionWait, actor.scope, wait_id, locked=True)
        grant, tenant, recovery = self._grants(db, actor.scope)
        limits, _, _ = self._limits(db, actor.scope, revision, grant, tenant)
        key = "capacity.wait:" + wait.id
        digest = sha256(
            canonical_value(
                dict(
                    wait_id=wait.id,
                    execution_id=execution.id,
                    step_instance_id=wait.step_instance_id,
                    deadline_ms=wait.deadline_ms,
                )
            )
        ).hexdigest()
        existing = db.scalar(
            scoped(ExecutionRequest, actor.scope).where(ExecutionRequest.request_key == key)
        )
        if existing:
            if existing.execution_id != execution.id or existing.digest != digest:
                raise ExecutionError("conflict")
            self._final(db, actor, revision, grant, tenant, recovery)
            return
        if (
            wait.state != "open"
            or wait.deadline_ms > execution.deadline_ms
            or wait.deadline_ms <= database_now_ms(db)
        ):
            raise ExecutionError("conflict")
        if capacity.retained_waits >= min(capacity.wait_limit, limits["retained_waits"]):
            raise ExecutionError("capacity_unavailable")
        if db.scalar(
            scoped(ExecutionUsageReservation, actor.scope).where(
                ExecutionUsageReservation.execution_id == execution.id,
                ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
            )
        ):
            raise ExecutionError("conflict")
        self._final(db, actor, revision, grant, tenant, recovery)
        if (
            execution.requested_state != "running"
            or execution.state not in {"pending", "running", "waiting"}
            or execution.deadline_ms <= database_now_ms(db)
        ):
            raise ExecutionError("forbidden")
        # Carried Task 6 finding: the wait's own, shorter deadline is checked again
        # after the provider waits in _final, not only the execution deadline.
        if wait.deadline_ms <= database_now_ms(db):
            raise ExecutionError("conflict")
        db.add(
            ExecutionRequest(
                **actor.scope.model_dump(),
                execution_id=execution.id,
                request_key=key,
                digest=digest,
            )
        )
        capacity.retained_waits += 1
        if execution.state == "pending":
            if capacity.pending < 1:
                raise ExecutionError("conflict")
            capacity.pending -= 1
        execution.state = "waiting"
        db.flush()

    def _release_rows(self, db, execution_id):
        """Cursor, workspace, capacity, then the execution: Task 5's order, no actor.

        Releases only shrink counted liability, so they need no customer authority.
        """
        self._cursor(db)
        execution_id = TypeAdapter(CanonicalUUID).validate_python(execution_id, strict=True)
        hint = db.get(Execution, execution_id)
        if hint is None:
            raise ExecutionError("not_found")
        scope = Scope(
            workspace_id=hint.workspace_id,
            project_id=hint.project_id,
            environment_id=hint.environment_id,
        )
        from ..models import Workspace

        lock(db, select(Workspace).where(Workspace.id == scope.workspace_id))
        capacity = lock(db, scoped(ExecutionCapacity, scope))
        if capacity is None:
            raise ExecutionError("capacity_unavailable")
        execution = record(db, Execution, scope, execution_id, locked=True)
        return scope, capacity, execution

    @execution_service
    def release_wait(self, db, wait_id):
        """Return one retained wait slot once its wait closed; repeats are no-ops."""
        wait_id = TypeAdapter(CanonicalUUID).validate_python(wait_id, strict=True)
        hint = db.get(ExecutionWait, wait_id)
        if hint is None:
            raise ExecutionError("not_found")
        scope, capacity, execution = self._release_rows(db, hint.execution_id)
        record(db, ExecutionStepInstance, scope, hint.step_instance_id, locked=True)
        wait = record(db, ExecutionWait, scope, wait_id, locked=True)
        keys = {
            row.request_key: row
            for row in db.scalars(
                scoped(ExecutionRequest, scope).where(
                    ExecutionRequest.request_key.in_(
                        ("capacity.wait:" + wait.id, "capacity.wait-release:" + wait.id)
                    )
                )
            )
        }
        if "capacity.wait:" + wait.id not in keys or "capacity.wait-release:" + wait.id in keys:
            return
        if wait.state == "open" or wait.execution_id != execution.id:
            raise ExecutionError("conflict")
        if capacity.retained_waits < 1:
            raise ExecutionError("conflict")
        capacity.retained_waits -= 1
        db.add(
            ExecutionRequest(
                **scope.model_dump(),
                execution_id=execution.id,
                request_key="capacity.wait-release:" + wait.id,
                digest=sha256(
                    canonical_value({"wait_id": wait.id, "released": wait.state})
                ).hexdigest(),
            )
        )
        db.flush()

    @execution_service
    def release_pending(self, db, execution_id):
        """Return the admission slot of an execution that ends without ever running.

        Keyed by a request row, so a repeat changes nothing even before the caller
        moves the execution to its terminal state in the same transaction.
        """
        scope, capacity, execution = self._release_rows(db, execution_id)
        key = "capacity.pending-release:" + execution.id
        if execution.state != "pending" or db.scalar(
            scoped(ExecutionRequest, scope).where(ExecutionRequest.request_key == key)
        ):
            return
        if capacity.pending < 1:
            raise ExecutionError("conflict")
        capacity.pending -= 1
        db.add(
            ExecutionRequest(
                **scope.model_dump(),
                execution_id=execution.id,
                request_key=key,
                digest=sha256(canonical_value({"execution_id": execution.id})).hexdigest(),
            )
        )
        db.flush()
