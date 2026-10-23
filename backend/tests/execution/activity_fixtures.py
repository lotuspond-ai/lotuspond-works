"""Task 9 harness: a real deployment with a bound connection and a runtime session.

Synthetic trust, signing and demand providers are test-only and constructor-injected.
Time moves only in real database time; no deadline, lease or expiry column is edited.
"""

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lotuspond_runtime.protocol import RuntimeIdentity
from lotuspond_sdk.activity import sign_activity
from lotuspond_sdk.trust import TrustSet
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.config import Settings
from lotuspond.execution.budgets import BudgetDemand
from lotuspond.execution.deployments import create_deployment, set_deployment_status
from lotuspond.execution.dispatch import ActivityService
from lotuspond.execution.leases import LeaseService
from lotuspond.execution.reconciliation import ReconciliationService
from lotuspond.fleet.authentication import RuntimePrincipal
from lotuspond.fleet.authority import FleetDependencies, RecoverySnapshot, database_now_ms
from lotuspond.fleet.catalog import InstalledCatalog
from lotuspond.models import (
    ExecutionAttempt,
    ExecutionStepInstance,
    RuntimeCredential,
    RuntimeInstance,
    RuntimeSession,
)
from lotuspond.security import RateLimiter

from .test_deployments import catalog_ref, connection_request

FLOW = Path(__file__).parent / "fixtures/control-flow"
CATALOG = Path(__file__).resolve().parents[3] / "contracts/examples/executable-v2/catalog.json"


class Signer:
    test_only = True

    def __init__(self, key):
        self.key = key

    def sign(self, envelope):
        return sign_activity(envelope, self.key)


class Trust:
    test_only = True

    def __init__(self, key):
        public = key.public_key().public_bytes_raw()
        self.key_id = sha256(public).hexdigest()
        self.public = public.hex()
        self.state = "allowed"
        self.calls = 0

    def current(self, scope, now_ms):
        self.calls += 1
        return TrustSet.model_validate(
            {
                "format_version": 1,
                "keys": [{"key_id": self.key_id, "public_key": self.public, "state": self.state}],
            }
        )


class Demand:
    """Bounded demand; leases never outlive the reserved active time."""

    test_only = True

    def __init__(self, active_ms=10000):
        self.active_ms = active_ms

    def demand(self, attempt_id):
        return BudgetDemand(
            input_tokens=10, output_tokens=10, cost_microusd=10, active_ms=self.active_ms
        )


class Recovery:
    test_only = True

    def __init__(self):
        self.available = True

    def current(self, installation_id):
        if not self.available:
            raise RuntimeError("PRIVATE_RECOVERY_OUTAGE")
        return RecoverySnapshot(installation_id, 1, True)


def documents():
    return json.loads(CATALOG.read_text())["documents"]


def widen(db, h, request, capacity):
    """Allow `capacity` concurrent attempts: deployment limit and a new pool policy."""
    from lotuspond_runtime.protocol import PoolPolicy, canonical_pool_json, pool_digest

    from lotuspond.models import RuntimePool, RuntimePoolPolicy, RuntimePoolPolicyConnection

    pool = db.get(RuntimePool, h.pool_id)
    current = db.scalar(
        select(RuntimePoolPolicy).where(
            RuntimePoolPolicy.pool_id == pool.id,
            RuntimePoolPolicy.version == pool.current_policy_version,
        )
    )
    policy = PoolPolicy.model_validate_json(current.canonical_policy).model_copy(
        update={"max_concurrent_invocations": capacity}
    )
    version = current.version + 1
    db.add(
        RuntimePoolPolicy(
            **h.scope.model_dump(),
            pool_id=pool.id,
            version=version,
            canonical_policy=canonical_pool_json(policy),
            policy_digest=pool_digest(policy),
        )
    )
    db.flush()
    for connection_id in policy.connection_ids:
        db.add(
            RuntimePoolPolicyConnection(
                **h.scope.model_dump(),
                pool_id=pool.id,
                policy_version=version,
                connection_id=connection_id,
            )
        )
    pool.current_policy_version = version
    db.flush()
    limits = request.configuration.limits.model_copy(update={"active_capacity": capacity})
    return request.model_copy(
        update={"configuration": request.configuration.model_copy(update={"limits": limits})}
    )


def deploy(h, name, *, model=None, capacity=1):
    """Publish a fixture with the connector bound to a real connection version."""
    if name in h.deployed:
        return h.deployed[name]
    connector = documents()[0]
    with Session(h.engine) as db, db.begin():
        published = h.publish(db, connector, (), request_key=f"{name}-connector")
        ids, docs = [published.id], [connector]
        if model is not None:
            ids.append(h.publish(db, model, tuple(ids), request_key=f"{name}-model").id)
            docs.append(model)
        package = json.loads((FLOW / f"{name}.json").read_text())
        publication = h.publish(db, package, tuple(ids), request_key=name)
        request = connection_request(db, h, publication, docs)
        if capacity > 1:
            request = widen(db, h, request, capacity)
        view = create_deployment(db, h.actor, request, name, dependencies=h.dependencies)
        set_deployment_status(
            db,
            h.actor,
            view.id,
            view.revision_id,
            "enabled",
            name + "-enable",
            dependencies=h.dependencies,
        )
    h.deployed[name] = view.revision_id
    return view.revision_id


def runtime(h, *, session_ms=3_600_000, generation=1):
    """One active runtime instance, credential and session in the deployment's pool."""
    fields = dict(
        **h.scope.model_dump(),
        pool_id=h.pool_id,
        installation_id="test-installation",
        recovery_epoch=1,
    )
    with Session(h.engine) as db, db.begin():
        now = database_now_ms(db)
        instance = RuntimeInstance(
            **fields,
            generation=generation,
            observation_ceiling=b'{"platform":"linux","connectors":[]}',
        )
        db.add(instance)
        db.flush()
        credential = RuntimeCredential(
            **fields,
            instance_id=instance.id,
            version=1,
            token_digest=sha256(instance.id.encode()).hexdigest(),
            status="active",
            expires_at_ms=now + 3_600_000,
        )
        session = RuntimeSession(
            **fields,
            instance_id=instance.id,
            generation=generation,
            token_digest=sha256(b"session:" + instance.id.encode()).hexdigest(),
            expires_at_ms=now + session_ms,
        )
        db.add_all([credential, session])
        db.flush()
        identity = RuntimeIdentity(
            scope=h.scope,
            pool_id=h.pool_id,
            installation_id="test-installation",
            recovery_epoch=1,
            instance_id=instance.id,
            generation=generation,
            credential_version=1,
        )
        return RuntimePrincipal(identity, credential.id, session.id)


def attempt_at(h, execution_id, path, ordinal=1):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionAttempt.id)
            .join(
                ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id
            )
            .where(
                ExecutionStepInstance.execution_id == execution_id,
                ExecutionStepInstance.occurrence_path == path,
                ExecutionAttempt.ordinal == ordinal,
                ExecutionAttempt.parent_attempt_id.is_(None),
            )
        )


@pytest.fixture
def activity(flows):
    h = flows
    h.activity_key = Ed25519PrivateKey.generate()
    h.trust = Trust(h.activity_key)
    h.fleet_recovery = Recovery()
    h.fleet = FleetDependencies(
        recovery=h.fleet_recovery,
        catalog=InstalledCatalog(),
        installation_id="test-installation",
        limiter=RateLimiter(Settings(env="test")),
    )
    h.activities = ActivityService(
        fleet=h.fleet,
        budgets=h.budgets,
        artifacts=h.store,
        artifact_policy=h.policy,
        trust=h.trust,
        signer=Signer(h.activity_key),
        demand=Demand(),
        installation_id="test-installation",
        test_mode=True,
    )
    h.leases = LeaseService(h.activities)
    h.reconciliation = ReconciliationService(h.activities)
    h.principal_runtime = runtime(h)

    def ready(name, *, model=None, path="read", active_ms=10000, capacity=1, actor=None):
        """Deploy, admit and progress until the step's first attempt is reserved."""
        revision = deploy(h, name, model=model, capacity=capacity)
        from lotuspond.execution.contracts import AdmissionRequest

        with Session(h.engine) as db, db.begin():
            execution_id = h.service.admit_execution(
                db,
                actor or h.actor,
                AdmissionRequest(deployment_revision_id=revision, input=None),
                f"{name}-{uuid4().hex}",
            ).id
        h.drive()
        attempt_id = attempt_at(h, execution_id, path)
        with Session(h.engine) as db, db.begin():
            h.budgets.reserve_attempt(
                db, actor or h.actor, attempt_id, Demand(active_ms).demand(attempt_id)
            )
        return execution_id, attempt_id

    def dispatch(attempt_id, principal=None):
        principal = principal or h.principal_runtime
        with Session(h.engine) as db, db.begin():
            return h.activities.dispatch_activity(db, attempt_id, principal.identity)

    def claim(delivery, principal=None):
        principal = principal or h.principal_runtime
        envelope = json.loads(delivery.envelope)
        with Session(h.engine) as db, db.begin():
            return h.activities.claim_activity(
                db,
                principal,
                envelope["delivery_id"],
                sha256(delivery.envelope.encode()).hexdigest(),
            )

    def store(attempt_id, value, principal=None):
        from lotuspond.execution.contracts import canonical_value

        principal = principal or h.principal_runtime
        with Session(h.engine) as db, db.begin():
            return h.activities.store_activity_value(
                db, principal, attempt_id, canonical_value(value)
            )

    def result(attempt_id, lease, code="succeeded", value=None, usage=None, principal=None):
        from lotuspond_runtime.activity_protocol import ActivityResult

        principal = principal or h.principal_runtime
        handle = store(attempt_id, value, principal) if code == "succeeded" else None
        with Session(h.engine) as db, db.begin():
            h.activities.lock_cursor(db)
            h.activities.record_activity_result(
                db,
                principal,
                ActivityResult(
                    attempt_id=attempt_id,
                    lease_revision=lease.lease_revision,
                    result=handle,
                    code=code,
                    usage=usage
                    or dict(
                        input_tokens=3,
                        output_tokens=4,
                        cost_microusd=5,
                        completed_at_ms=database_now_ms(db) - 1,
                    ),
                ),
            )

    h.ready, h.dispatch, h.claim, h.store_value, h.result = ready, dispatch, claim, store, result
    h.catalog_ref = catalog_ref
    yield h
    h.activities.close()
    h.fleet.close()


def state(h, model, identifier):
    with Session(h.engine) as db:
        row = db.get(model, identifier)
        return SimpleNamespace(**{c.key: getattr(row, c.key) for c in model.__table__.columns})
