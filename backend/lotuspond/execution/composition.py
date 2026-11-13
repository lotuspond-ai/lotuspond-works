"""One wiring of every execution service, over whatever authorities the caller supplies.

Production passes the operator's directory (`operator_authority.OperatorAuthorities`);
the conformance harness passes its file-backed test authorities with `test_mode=True`.
Both go through this function, so what the harness tests is the production wiring.
Nothing here reads settings, files or the environment itself.
"""

from __future__ import annotations

from types import SimpleNamespace

INTERPRETER = "lotuspond.execution-interpreter.v1"


def compose(authorities, *, engine, installation_id: str, limiter, test_mode: bool = False):
    from sqlalchemy.orm import sessionmaker

    from lotuspond.execution.admission import AdmissionService
    from lotuspond.execution.artifacts import (
        WORKSPACE_ACCESS_POLICY,
        ArtifactPolicy,
        ArtifactStore,
    )
    from lotuspond.execution.boundary import ExecutionServices
    from lotuspond.execution.budgets import BudgetService
    from lotuspond.execution.context import ContextSource
    from lotuspond.execution.control import ControlService
    from lotuspond.execution.dispatch import ActivityService
    from lotuspond.execution.engine import PostgresExecutionEngine
    from lotuspond.execution.inbox import InboxService
    from lotuspond.execution.leases import LeaseService
    from lotuspond.execution.progression import InterpreterV1
    from lotuspond.execution.projections import EventProjection
    from lotuspond.execution.publication import ExecutionDependencies
    from lotuspond.execution.reconciliation import ReconciliationService
    from lotuspond.execution.recovery import RecoveryFence
    from lotuspond.execution.scheduler import Scheduler
    from lotuspond.execution.sweeper import ExecutionSweeper
    from lotuspond.fleet.authority import FleetDependencies
    from lotuspond.fleet.catalog import InstalledCatalog

    a = authorities
    dependencies = ExecutionDependencies(authority=a.publishers, test_mode=test_mode)
    budgets = BudgetService(
        entitlement=a.entitlement,
        tenant_policy=a.tenant,
        charges=a.charges,
        recovery=a.recovery,
        installation_id=installation_id,
        deployments=dependencies,
        test_mode=test_mode,
    )
    store = ArtifactStore(a.keys, a.access, test_mode=test_mode)
    policy = ArtifactPolicy(
        classification="customer-private",
        source_id=None,
        source_version=None,
        retain_until_ms=a.retain_until_ms,
        access_policy_id=WORKSPACE_ACCESS_POLICY,
        key_id=a.artifact_key_id,
    )
    fleet = FleetDependencies(
        recovery=a.recovery,
        catalog=InstalledCatalog(),
        installation_id=installation_id,
        limiter=limiter,
    )
    fence = RecoveryFence(fleet)
    activities = ActivityService(
        fleet=fleet,
        budgets=budgets,
        artifacts=store,
        artifact_policy=policy,
        trust=a.activity_trust,
        signer=a.signer,
        demand=a.demand,
        installation_id=installation_id,
        test_mode=test_mode,
    )
    leases = LeaseService(activities)
    interpreter = InterpreterV1(
        artifacts=store,
        artifact_policy=policy,
        budgets=budgets,
        recovery=fence,
        installation_id=installation_id,
        test_mode=test_mode,
    )
    sweeper = ExecutionSweeper(
        leases=leases,
        recovery=fence,
        scheduler=Scheduler(budgets, a.demand, test_mode=test_mode),
    )
    controls = ControlService(
        budgets=budgets,
        artifacts=store,
        artifact_policy=policy,
        installation_id=installation_id,
        recovery=fence,
    )
    inbox = InboxService(budgets=budgets, artifacts=store, artifact_policy=policy)
    reconciliation = ReconciliationService(activities)
    context = ContextSource(store)
    services = ExecutionServices(
        artifacts=store,
        artifact_policy=policy,
        budgets=budgets,
        dependencies=dependencies,
        admission=AdmissionService(
            budgets=budgets, artifacts=store, artifact_policy=policy, interpreter_id=INTERPRETER
        ),
        inbox=inbox,
        controls=controls,
        activities=activities,
        leases=leases,
        reconciliation=reconciliation,
        context=context,
    )
    return SimpleNamespace(
        engine=engine,
        sessions=sessionmaker(engine, expire_on_commit=False),
        services=services,
        fleet=fleet,
        fence=fence,
        store=store,
        policy=policy,
        budgets=budgets,
        dependencies=dependencies,
        activities=activities,
        leases=leases,
        controls=controls,
        reconciliation=reconciliation,
        events=EventProjection(
            artifacts=store,
            artifact_policy=policy,
            installation_id=installation_id,
            recovery=fence,
        ),
        execution_engine=PostgresExecutionEngine(
            interpreter, sweeper, fence, interpreter_ids=[INTERPRETER], test_mode=test_mode
        ),
    )
