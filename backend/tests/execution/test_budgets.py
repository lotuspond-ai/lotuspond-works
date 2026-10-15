"""Real PostgreSQL shared budget and conservative settlement qualification."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from lotuspond_sdk.activity import UsageObservation
from lotuspond_sdk.executable.catalog import catalog_document_digest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.budgets import BudgetDemand, BudgetService
from lotuspond.execution.contracts import ExecutionLimits
from lotuspond.execution.deployments import create_deployment, set_deployment_status
from lotuspond.execution.entitlement import EntitlementGrant, PolicyGrant
from lotuspond.execution.errors import ExecutionError
from lotuspond.fleet.authority import RecoverySnapshot, database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionSchedulerCursor,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    new_id,
)

from .test_deployments import deployment_request
from .test_publication import example
from .test_publication import harness as harness


class Providers:
    test_only = True

    def __init__(self, scope, limits):
        self.scope, self.limits = scope, limits
        self.expiry = 9007199254740991
        self.allowed = True

    def current(self, scope, now_ms):
        return EntitlementGrant(
            scope=scope,
            installation_id="test-installation",
            recovery_epoch=1,
            revision="test-revision",
            expires_at_ms=self.expiry,
            execute=self.allowed,
            limits=self.limits,
        )

    def policy(self, scope, now_ms):
        return PolicyGrant(scope=scope, expires_at_ms=self.expiry, limits=self.limits)

    def bound(self, dependency, demand):
        return demand


class Recovery:
    test_only = True

    def current(self, installation_id):
        return RecoverySnapshot(installation_id, 1, True)


class Tenant:
    test_only = True

    def __init__(self, provider):
        self.provider = provider

    def current(self, scope, now_ms):
        return self.provider.policy(scope, now_ms)


@pytest.fixture
def budget_harness(harness):
    h = harness
    raw = example("minimal")
    model = example("catalog")["documents"][1]
    ref = dict(
        kind="model",
        name=model["name"],
        version=model["version"],
        sha256=catalog_document_digest(model),
    )
    raw["models"] = [dict(id="model", ref=ref)]
    raw["body"]["steps"] = [
        dict(
            id="generate",
            kind="agent",
            model_ref="model",
            instructions="Synthetic fixture",
            input=dict(kind="literal", schema=dict(kind="null"), value=None),
            output_schema=dict(kind="null"),
            limits=dict(
                max_steps=5,
                timeout_seconds=30,
                max_input_tokens=1000,
                max_output_tokens=1000,
                max_cost_microusd=100000,
            ),
            retry=dict(max_attempts=10, timeout_seconds=30, backoff_seconds=0, retry_codes=[]),
        )
    ]
    with Session(h.engine) as db, db.begin():
        dep = h.publish(db, model, request_key="model")
        publication = h.publish(db, raw, (dep.id,))
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        from lotuspond.execution.contracts import DependencyBinding

        request = deployment_request(h, publication.id)
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(
                    update={"bindings": (DependencyBinding(dependency=ref),)}
                )
            }
        )
        view = create_deployment(db, actor, request, "deployment", dependencies=h.dependencies)
        set_deployment_status(
            db, actor, view.id, view.revision_id, "enabled", "enable", dependencies=h.dependencies
        )
        db.add(ExecutionSchedulerCursor(installation_id="test-installation"))
        db.add(ExecutionCapacity(**h.scope.model_dump()))
        artifact = ExecutionArtifact(
            **h.scope.model_dump(),
            version=1,
            sha256="a" * 64,
            ciphertext=b"x" * 20,
            nonce=b"x" * 12,
            key_id="fixture",
            classification="synthetic",
            content_bytes=4,
            retain_until_ms=9007199254740991,
            access_policy_id="fixture",
        )
        db.add(artifact)
        db.flush()
        h.revision_id = view.revision_id
        h.artifact_id = artifact.id
        h.actor = authorize_execution(db, h.principal, h.scope, "execute")
    limits = ExecutionLimits(**raw["limits"], active_capacity=5, pending_capacity=1000)
    h.provider = Providers(h.scope, limits)
    h.budgets = BudgetService(
        entitlement=h.provider,
        tenant_policy=Tenant(h.provider),
        charges=h.provider,
        recovery=Recovery(),
        installation_id="test-installation",
        deployments=h.dependencies,
        test_mode=True,
    )
    h.attempts = []

    def add(db, execution_id=None, parent=None, ordinal=1):
        if execution_id is None:
            h.budgets.reserve_admission(db, h.actor, h.revision_id)
            capacity = db.scalar(
                select(ExecutionCapacity).where(
                    ExecutionCapacity.environment_id == h.scope.environment_id
                )
            )
            now = database_now_ms(db)
            execution = Execution(
                **h.scope.model_dump(),
                deployment_revision_id=h.revision_id,
                interpreter_id="fixture",
                engine_identity=new_id(),
                input_artifact_id=h.artifact_id,
                input_artifact_version=1,
                input_sha256="a" * 64,
                initiating_user_id=h.actor.user_id,
                admitted_at_ms=now,
                admission_sequence=capacity.next_admission_sequence - 1,
                deadline_ms=now + 1000000,
                admission_digest="b" * 64,
            )
            db.add(execution)
            db.flush()
            execution_id = execution.id
        step = db.scalar(
            select(ExecutionStepInstance).where(ExecutionStepInstance.execution_id == execution_id)
        )
        if step is None:
            step = ExecutionStepInstance(
                **h.scope.model_dump(),
                execution_id=execution_id,
                occurrence_path="generate",
                node_id="generate",
            )
            db.add(step)
            db.flush()
        attempt = ExecutionAttempt(
            **h.scope.model_dump(),
            execution_id=execution_id,
            step_instance_id=step.id,
            ordinal=ordinal,
            parent_attempt_id=parent,
            child_request_id=new_id() if parent else None,
            child_request_digest="c" * 64 if parent else None,
            input_artifact_id=h.artifact_id,
            input_artifact_version=1,
            input_sha256="a" * 64,
        )
        db.add(attempt)
        db.flush()
        h.attempts.append(attempt.id)
        return attempt.id, execution_id

    def stop(db, attempt_id, label):
        from lotuspond.models import ExecutionReconciliationObservation

        attempt = db.get(ExecutionAttempt, attempt_id)
        observation = ExecutionReconciliationObservation(
            **h.scope.model_dump(),
            execution_id=attempt.execution_id,
            attempt_id=attempt_id,
            source_id="fixture-confirmed-stop",
            observation_id=label,
            digest="d" * 64,
            code="compute_stopped",
            observed_at_ms=database_now_ms(db),
        )
        db.add(observation)
        db.flush()
        return h.budgets.confirm_stopped(db, attempt_id, observation.id)

    h.stop = stop
    h.add = add
    yield h
    h.budgets.close()


def set_concurrency(db, h, count):
    from hashlib import sha256

    from lotuspond_runtime.protocol import PoolPolicy, canonical_pool_json, pool_digest

    from lotuspond.execution.contracts import DeploymentConfig, canonical_configuration
    from lotuspond.models import ExecutionDeploymentRevision, RuntimePoolPolicy

    revision = db.get(ExecutionDeploymentRevision, h.revision_id)
    config = DeploymentConfig.model_validate_json(revision.canonical_configuration)
    config = config.model_copy(
        update={"limits": config.limits.model_copy(update={"active_capacity": count})}
    )
    revision.canonical_configuration = canonical_configuration(config)
    revision.configuration_sha256 = sha256(revision.canonical_configuration).hexdigest()
    row = db.scalar(select(RuntimePoolPolicy))
    policy = PoolPolicy.model_validate_json(row.canonical_policy).model_copy(
        update={"max_concurrent_invocations": count}
    )
    row.canonical_policy = canonical_pool_json(policy)
    row.policy_digest = pool_digest(policy)
    db.flush()


def demand(cost=60000, active=100):
    return BudgetDemand(input_tokens=10, output_tokens=10, cost_microusd=cost, active_ms=active)


def test_parallel_reservations_cannot_overspend(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        set_concurrency(db, h, 2)
        first, execution = h.add(db)
        second, _ = h.add(db, execution, ordinal=2)
    barrier = Barrier(2)

    def reserve(identifier):
        barrier.wait(timeout=5)
        try:
            with Session(h.engine) as db, db.begin():
                h.budgets.reserve_attempt(db, h.actor, identifier, demand())
            return "reserved"
        except ExecutionError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(reserve, [first, second]))
    assert results.count("reserved") == 1
    assert set(results) == {"reserved", "budget_exceeded"}
    with Session(h.engine) as db:
        assert sum(db.scalars(select(ExecutionUsageReservation.cost_microusd))) == 60000


def test_unknown_usage_retains_liability(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, attempt, demand())
        usage = UsageObservation(
            input_tokens=None,
            output_tokens=None,
            cost_microusd=None,
            completed_at_ms=database_now_ms(db),
        )
        unknown_reservation = h.budgets.settle_attempt(db, attempt, "unknown", usage)
        assert unknown_reservation.state == "uncertain"
        row = db.scalar(select(ExecutionUsageReservation))
        assert row.cost_microusd == 60000 and row.compute_stopped_at_ms is None
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 1


def test_nested_attempts_share_root_budget(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        parent, execution = h.add(db)
        child, _ = h.add(db, execution, parent, 2)
        h.budgets.reserve_attempt(db, h.actor, parent, demand())
        h.stop(db, parent, "stopped")
        with pytest.raises(ExecutionError, match="budget_exceeded"):
            h.budgets.reserve_attempt(db, h.actor, child, demand())


def test_duplicate_usage_and_actual_overrun(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, execution = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, attempt, demand())
        usage = UsageObservation(
            input_tokens=11,
            output_tokens=12,
            cost_microusd=150000,
            completed_at_ms=database_now_ms(db),
        )
        settled_once = h.budgets.settle_attempt(db, attempt, "known", usage)
        settled_after_duplicate = h.budgets.settle_attempt(db, attempt, "known", usage)
        assert settled_once == settled_after_duplicate
        assert db.scalar(select(ExecutionUsageReservation.observed_cost_microusd)) == 150000
        h.stop(db, attempt, "stop")
        retry, _ = h.add(db, execution, ordinal=2)
        with pytest.raises(ExecutionError, match="budget_exceeded"):
            h.budgets.reserve_attempt(db, h.actor, retry, demand(1))


def test_admission_fifo_serialization_rollback_and_counter_exhaustion(budget_harness):
    h = budget_harness
    with Session(h.engine) as db:
        h.add(db)
        db.rollback()
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionCapacity.next_admission_sequence)) == 1
        assert db.scalar(select(ExecutionCapacity.pending)) == 0
    barrier = Barrier(2)

    def admit(_):
        barrier.wait(timeout=5)
        with Session(h.engine) as db, db.begin():
            _, execution = h.add(db)
            return db.get(Execution, execution).admission_sequence

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(admit, range(2))) == [1, 2]
    with Session(h.engine) as db, db.begin():
        row = db.scalar(select(ExecutionCapacity))
        row.next_admission_sequence = 9007199254740991
        db.flush()
        with pytest.raises(ExecutionError, match="capacity_unavailable"):
            h.budgets.reserve_admission(db, h.actor, h.revision_id)
        assert row.pending == 2


def test_pending_1001_and_lower_operator_limits_refused(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        capacity = db.scalar(select(ExecutionCapacity))
        capacity.pending = 1000
        db.flush()
        with pytest.raises(ExecutionError, match="capacity_unavailable"):
            h.budgets.reserve_admission(db, h.actor, h.revision_id)
        capacity.pending = 0
        capacity.pending_limit = 0
        db.flush()
        with pytest.raises(ExecutionError, match="capacity_unavailable"):
            h.budgets.reserve_admission(db, h.actor, h.revision_id)


def test_active_time_is_additive_across_retry_and_stop(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        first, execution = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, first, demand(1, 20000))
        h.stop(db, first, "stopped")
        second, _ = h.add(db, execution, ordinal=2)
        with pytest.raises(ExecutionError, match="budget_exceeded"):
            h.budgets.reserve_attempt(db, h.actor, second, demand(1, 20000))
        assert db.scalar(select(ExecutionUsageReservation.active_ms)) == 20000


def test_settlement_conflicts_and_unknown_observation_identity(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, attempt, demand())
        usage = UsageObservation(
            input_tokens=None,
            output_tokens=None,
            cost_microusd=None,
            completed_at_ms=database_now_ms(db),
        )
        h.budgets.settle_attempt(db, attempt, "same", usage)
        changed = usage.model_copy(update={"cost_microusd": 10})
        with pytest.raises(ExecutionError, match="conflict"):
            h.budgets.settle_attempt(db, attempt, "same", changed)
        h.budgets.settle_attempt(
            db,
            attempt,
            "reconciled",
            usage.model_copy(update={"input_tokens": 10, "output_tokens": 20, "cost_microusd": 30}),
        )
        assert db.scalar(select(ExecutionUsageReservation.state)) == "settled"
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 1
        with pytest.raises(ExecutionError):
            h.budgets.confirm_stopped(db, attempt, new_id())
        h.stop(db, attempt, "confirmed")
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 0


@pytest.mark.parametrize("scope", ["tenant", "entitlement", "deployment", "agent", "pool"])
def test_every_scope_limits_reservation(budget_harness, scope):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
        if scope in {"tenant", "entitlement"}:
            limits = h.provider.limits.model_copy(update={"max_cost_microusd": 1})
            if scope == "entitlement":

                class Entitlement:
                    def current(self, scope, now_ms):
                        return h.provider.current(scope, now_ms).model_copy(
                            update={"limits": limits}
                        )

                h.budgets.entitlement = Entitlement()
            else:

                class Policy:
                    def current(self, scope, now_ms):
                        return PolicyGrant(
                            scope=scope, expires_at_ms=9007199254740991, limits=limits
                        )

                h.budgets.tenant_policy = Policy()
            expected = "budget_exceeded"
        elif scope == "agent":
            expected = "budget_exceeded"
        elif scope == "deployment":
            from hashlib import sha256

            from lotuspond.execution.contracts import DeploymentConfig, canonical_configuration
            from lotuspond.models import ExecutionDeploymentRevision

            revision = db.get(ExecutionDeploymentRevision, h.revision_id)
            config = DeploymentConfig.model_validate_json(revision.canonical_configuration)
            config = config.model_copy(
                update={"limits": config.limits.model_copy(update={"max_cost_microusd": 1})}
            )
            revision.canonical_configuration = canonical_configuration(config)
            revision.configuration_sha256 = sha256(revision.canonical_configuration).hexdigest()
            db.flush()
            expected = "budget_exceeded"
        else:
            # Existing pool fixture permits only one concurrent invocation.
            h.budgets.reserve_attempt(db, h.actor, attempt, demand(1))
            second, _ = h.add(db)
            attempt = second
            expected = "capacity_unavailable"
        with pytest.raises(ExecutionError, match=expected):
            h.budgets.reserve_attempt(
                db, h.actor, attempt, demand(2, 30001 if scope == "agent" else 100)
            )


def runtime_fixture(h, db):
    from lotuspond_runtime.protocol import RuntimeIdentity

    from lotuspond.config import Settings
    from lotuspond.fleet.authentication import RuntimePrincipal
    from lotuspond.fleet.authority import FleetDependencies
    from lotuspond.fleet.catalog import InstalledCatalog
    from lotuspond.models import RuntimeCredential, RuntimeInstance, RuntimeSession
    from lotuspond.security import RateLimiter

    fields = dict(
        **h.scope.model_dump(),
        pool_id=h.pool_id,
        installation_id="test-installation",
        recovery_epoch=1,
    )
    instance = RuntimeInstance(
        **fields, generation=1, observation_ceiling=b'{"platform":"linux","connectors":[]}'
    )
    db.add(instance)
    db.flush()
    credential = RuntimeCredential(
        **fields,
        instance_id=instance.id,
        version=1,
        token_digest="d" * 64,
        status="active",
        expires_at_ms=database_now_ms(db) + 900000,
    )
    session = RuntimeSession(
        **fields,
        instance_id=instance.id,
        generation=1,
        token_digest="e" * 64,
        expires_at_ms=database_now_ms(db) + 60000,
    )
    db.add_all([credential, session])
    db.flush()
    identity = RuntimeIdentity(
        scope=h.scope,
        pool_id=h.pool_id,
        installation_id="test-installation",
        recovery_epoch=1,
        instance_id=instance.id,
        generation=1,
        credential_version=1,
    )
    dependencies = FleetDependencies(
        recovery=Recovery(),
        catalog=InstalledCatalog(),
        installation_id="test-installation",
        limiter=RateLimiter(Settings(env="test")),
    )
    return RuntimePrincipal(identity, credential.id, session.id), dependencies


@pytest.mark.parametrize(
    "operation", ["heartbeat", "instance_revoke", "deployment_revoke", "cancel"]
)
def test_reservation_serializes_real_e03_renewal_revocation_and_control(budget_harness, operation):
    from threading import Event
    from time import monotonic

    from sqlalchemy import text

    from lotuspond.fleet.catalog import InstalledCatalog
    from lotuspond.fleet.identity import IdentityManagement, heartbeat
    from lotuspond.models import (
        ExecutionDeploymentRevision,
        RuntimeInstance,
        RuntimeSession,
        Workspace,
    )

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, execution = h.add(db)
        principal, dependencies = runtime_fixture(h, db)
    started = Event()
    pid = []

    def reserve():
        with Session(h.engine) as db, db.begin():
            pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                return h.budgets.reserve_attempt(db, h.actor, attempt, demand(1)).state
            except ExecutionError as error:
                return error.code

    try:
        with ThreadPoolExecutor(1) as pool:
            with Session(h.engine) as blocker, blocker.begin():
                if operation == "heartbeat":
                    before = blocker.get(RuntimeSession, principal.session_id).expires_at_ms
                    renewed = heartbeat(blocker, principal, dependencies)
                    assert renewed.session_expires_at_ms >= before
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
                    # Task6 handler is not implemented; this is its canonical
                    # PostgreSQL parent/control-write contract, not handler qualification.
                    blocker.scalar(select(ExecutionSchedulerCursor).with_for_update())
                    blocker.scalar(
                        select(Workspace)
                        .where(Workspace.id == h.scope.workspace_id)
                        .with_for_update()
                    )
                    row = blocker.scalar(
                        select(Execution).where(Execution.id == execution).with_for_update()
                    )
                    row.requested_state = "cancelled"
                    blocker.flush()
                blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
                future = pool.submit(reserve)
                assert started.wait(5)
                deadline = monotonic() + 5
                with h.engine.connect() as observer:
                    while monotonic() < deadline:
                        if observer.scalar(
                            text("SELECT :blocker = ANY(pg_blocking_pids(:pid))"),
                            {"blocker": blocker_pid, "pid": pid[0]},
                        ):
                            break
                    else:
                        pytest.fail("Reservation did not wait on real producer transaction")
            expected = {
                "heartbeat": "reserved",
                "instance_revoke": "reserved",
                "deployment_revoke": "deployment_unavailable",
                "cancel": "forbidden",
            }[operation]
            assert future.result(timeout=10) == expected
        assert pid[0] != blocker_pid
        if operation == "instance_revoke":
            with Session(h.engine) as db:
                assert db.get(RuntimeInstance, principal.identity.instance_id).status == "revoked"
    finally:
        dependencies.close()


def test_compute_elapsed_overrun_is_recorded_and_never_reset_by_stop(budget_harness):
    from datetime import timedelta

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, execution = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, attempt, demand(1, 1))
        reservation = db.scalar(select(ExecutionUsageReservation))
        # Persisted historical start, evaluated against the real current DB clock.
        reservation.created_at -= timedelta(seconds=40)
        db.flush()
        h.stop(db, attempt, "observed-stop")
        assert reservation.active_ms >= 40000
        retry, _ = h.add(db, execution, ordinal=2)
        with pytest.raises(ExecutionError, match="budget_exceeded"):
            h.budgets.reserve_attempt(db, h.actor, retry, demand(1, 1))


def test_concurrent_duplicate_settlement_and_nested_retry_costs(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        parent, execution = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, parent, demand())
        usage = UsageObservation(
            input_tokens=10,
            output_tokens=10,
            cost_microusd=10000,
            completed_at_ms=database_now_ms(db),
        )
    barrier = Barrier(2)

    def settle(_):
        barrier.wait(timeout=5)
        with Session(h.engine) as db, db.begin():
            return h.budgets.settle_attempt(db, parent, "same", usage)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(settle, range(2)))
        assert results[0] == results[1]
    with Session(h.engine) as db, db.begin():
        from lotuspond.models import ExecutionReconciliationObservation

        assert len(list(db.scalars(select(ExecutionReconciliationObservation)))) == 1
        h.stop(db, parent, "stop")
        child, _ = h.add(db, execution, parent, 2)
        assert h.budgets.reserve_attempt(db, h.actor, child, demand(20000)).state == "reserved"
        h.stop(db, child, "child-stop")
        retry, _ = h.add(db, execution, ordinal=3)
        assert h.budgets.reserve_attempt(db, h.actor, retry, demand(20000)).state == "reserved"
        rows = list(db.scalars(select(ExecutionUsageReservation)))
        assert (
            sum(
                row.observed_cost_microusd
                if row.observed_cost_microusd is not None
                else row.cost_microusd
                for row in rows
            )
            == 50000
        )


def test_subworkflow_attempts_share_one_root_reservation_ledger(budget_harness):
    from lotuspond.execution.contracts import DependencyBinding
    from lotuspond.execution.publication import publication_ref
    from lotuspond.models import ExecutablePublication

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        child = db.scalar(
            select(ExecutablePublication).where(ExecutablePublication.kind == "workflow")
        )
        model = db.scalar(
            select(ExecutablePublication).where(ExecutablePublication.kind == "model")
        )
        raw = example("minimal")
        raw["package_id"] = "root-wrapper"
        raw["subworkflows"] = [dict(id="child", ref=publication_ref(child).model_dump())]
        raw["body"]["steps"] = [
            dict(
                id="sub",
                kind="subworkflow",
                workflow_ref="child",
                input=dict(kind="literal", schema=dict(kind="null"), value=None),
            )
        ]
        publication = h.publish(db, raw, (child.id, model.id), request_key="root-wrapper")
        request = deployment_request(h, publication.id)
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(
                    update={
                        "bindings": tuple(
                            DependencyBinding(dependency=publication_ref(row))
                            for row in (child, model)
                        )
                    }
                )
            }
        )
        view = create_deployment(db, h.actor, request, "subworkflow", dependencies=h.dependencies)
        set_deployment_status(
            db,
            h.actor,
            view.id,
            view.revision_id,
            "enabled",
            "enable-subworkflow",
            dependencies=h.dependencies,
        )
        h.revision_id = view.revision_id
        first, execution = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, first, demand())
        h.stop(db, first, "sub-stop")
        second, _ = h.add(db, execution, ordinal=2)
        with pytest.raises(ExecutionError, match="budget_exceeded"):
            h.budgets.reserve_attempt(db, h.actor, second, demand())
        assert {row.execution_id for row in db.scalars(select(ExecutionUsageReservation))} == {
            execution
        }


def test_known_usage_cannot_release_liability_before_confirmed_stop(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        set_concurrency(db, h, 2)
        first, execution = h.add(db)
        second, _ = h.add(db, execution, ordinal=2)
        h.budgets.reserve_attempt(db, h.actor, first, demand())
        h.budgets.settle_attempt(
            db,
            first,
            "known",
            UsageObservation(
                input_tokens=1,
                output_tokens=1,
                cost_microusd=1,
                completed_at_ms=database_now_ms(db),
            ),
        )
        with pytest.raises(ExecutionError, match="budget_exceeded"):
            h.budgets.reserve_attempt(db, h.actor, second, demand())


def test_duplicate_node_ids_across_subworkflows_refuse_guessed_source(budget_harness):
    import json

    from lotuspond.execution.contracts import DependencyBinding
    from lotuspond.execution.publication import publication_ref
    from lotuspond.models import ExecutablePublication

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        child = db.scalar(
            select(ExecutablePublication).where(ExecutablePublication.kind == "workflow")
        )
        model = db.scalar(
            select(ExecutablePublication).where(ExecutablePublication.kind == "model")
        )
        other = json.loads(child.canonical_package)
        other["package_id"] = "other-child"
        second = h.publish(db, other, (model.id,), request_key="other-child")
        second = db.get(ExecutablePublication, second.id)
        raw = example("minimal")
        raw["package_id"] = "ambiguous-wrapper"
        raw["limits"]["max_steps"] = 1000
        raw["subworkflows"] = [
            dict(id=name, ref=publication_ref(row).model_dump())
            for name, row in (("left", child), ("right", second))
        ]
        raw["body"]["steps"] = [
            dict(
                id=name,
                kind="subworkflow",
                workflow_ref=name,
                input=dict(kind="literal", schema=dict(kind="null"), value=None),
            )
            for name in ("left", "right")
        ]
        publication = h.publish(
            db, raw, (child.id, second.id, model.id), request_key="ambiguous-wrapper"
        )
        request = deployment_request(h, publication.id)
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(
                    update={
                        "bindings": tuple(
                            DependencyBinding(dependency=publication_ref(row))
                            for row in (child, second, model)
                        )
                    }
                )
            }
        )
        view = create_deployment(db, h.actor, request, "ambiguous", dependencies=h.dependencies)
        set_deployment_status(
            db,
            h.actor,
            view.id,
            view.revision_id,
            "enabled",
            "enable-ambiguous",
            dependencies=h.dependencies,
        )
        h.revision_id = view.revision_id
        attempt, _ = h.add(db)
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            h.budgets.reserve_attempt(db, h.actor, attempt, demand(1))
        assert db.scalar(select(ExecutionUsageReservation)) is None
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 0


def test_reservation_active_start_uses_database_clock_not_application_default(
    budget_harness, monkeypatch
):
    from datetime import UTC, datetime

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
        # An application clock far in the future must not erase running time.
        monkeypatch.setattr(
            ExecutionUsageReservation.__table__.c.created_at.default,
            "arg",
            lambda context: datetime(2100, 1, 1, tzinfo=UTC),
        )
        before = database_now_ms(db)
        h.budgets.reserve_attempt(db, h.actor, attempt, demand(1))
        row = db.scalar(select(ExecutionUsageReservation))
        after = database_now_ms(db)
        assert before <= int(row.created_at.timestamp() * 1000) <= after


@pytest.mark.parametrize("offset_minutes", [0, 330, -240])
@pytest.mark.parametrize("stopped", [False, True])
def test_active_liability_preserves_equivalent_timezone_instants(offset_minutes, stopped):
    from datetime import UTC, datetime, timedelta, timezone
    from types import SimpleNamespace

    from lotuspond.execution.budgets import active_liability

    start_ms = 1800000000000
    stop_ms = start_ms + 40000
    zone = timezone(timedelta(minutes=offset_minutes))
    row = SimpleNamespace(
        created_at=datetime.fromtimestamp(start_ms / 1000, UTC).astimezone(zone),
        active_ms=1,
        compute_stopped_at_ms=stop_ms if stopped else None,
    )
    assert active_liability(row, stop_ms if not stopped else stop_ms + 90000) == 40000


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "America/New_York"])
def test_postgresql_timezone_preserves_running_and_stopped_liability(budget_harness, zone):
    from datetime import UTC, datetime

    from sqlalchemy import text

    from lotuspond.execution.budgets import active_liability

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
        h.budgets.reserve_attempt(db, h.actor, attempt, demand(1, 1))
        row = db.scalar(select(ExecutionUsageReservation))
        row.created_at = datetime.fromtimestamp((database_now_ms(db) - 40000) / 1000, UTC)
        db.flush()
        db.execute(text("SELECT set_config('TimeZone',:zone,true)"), {"zone": zone})
        db.refresh(row)
        assert row.created_at.utcoffset().total_seconds() != 0
        assert 40000 <= active_liability(row, database_now_ms(db)) < 41000
        h.stop(db, attempt, "timezone-stop")
        assert 40000 <= row.active_ms < 41000
        assert active_liability(row, database_now_ms(db) + 60000) == row.active_ms
