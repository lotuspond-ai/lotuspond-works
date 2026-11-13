"""E05 Task 6 harness: approved intents dispatched through E03's real command service.

Every provider is a test fixture (command key, trust set, installed catalog, demand),
so production cannot select any of it. The command key signs only here, in the
API-side service, as the operator key would.
"""

import json
from hashlib import sha256
from types import MappingProxyType

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lotuspond_runtime.protocol import CommandTrustSnapshot, ConnectorPin
from lotuspond_sdk.executable.signing import sign_runtime_command
from lotuspond_sdk.trust import TrustSet
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.config import Settings
from lotuspond.execution.action_contracts import DecisionRequest
from lotuspond.execution.action_dispatch import (
    ActionDispatchService,
    ExecutionDispatchAuthority,
    ExecutionPinnedRecordSource,
)
from lotuspond.fleet.authority import FleetDependencies, database_now_ms
from lotuspond.fleet.catalog import ConnectorDescriptor, InstalledCatalog, OperationDescriptor
from lotuspond.fleet.commands import CommandService
from lotuspond.models import (
    ExecutionActionCommand,
    ExecutionActionIntent,
    ExecutionAttempt,
    ExecutionHumanTask,
    ExecutionStepInstance,
    RuntimeInstance,
)
from lotuspond.security import RateLimiter

from ..activity_fixtures import Demand, runtime
from .test_admission import doc


def synthetic_catalog():
    """The installed descriptor of the synthetic target connector (test only)."""
    connector = doc("connector")
    pin = ConnectorPin(
        name=connector["name"],
        version=connector["version"],
        artifact_sha256=connector["definition"]["artifact_sha256"],
    )
    descriptor = ConnectorDescriptor(
        pin,
        {
            "submit": OperationDescriptor("write", False, False),
            "lookup": OperationDescriptor("read", False, False),
        },
    )
    catalog = InstalledCatalog()
    object.__setattr__(catalog, "_connectors", MappingProxyType({pin: descriptor}))
    object.__setattr__(catalog, "test_only", True)
    return catalog, pin


class CommandKey:
    """The operator command key and its trust set, both fixtures."""

    test_only = True

    def __init__(self, h):
        self.h = h
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes_raw()
        self.entry = {
            "key_id": sha256(public).hexdigest(),
            "public_key": public.hex(),
            "state": "allowed",
        }
        self.available = True

    def current(self, installation_id):
        if not self.available:
            from lotuspond_runtime.errors import FleetError

            raise FleetError("trust_unavailable")
        with Session(self.h.engine) as db:
            now = database_now_ms(db)
        return CommandTrustSnapshot(
            1, TrustSet.model_validate({"format_version": 1, "keys": [self.entry]}), now + 60000
        )

    def sign(self, command, snapshot):
        return sign_runtime_command(command, self.key)


class Charges:
    test_only = True

    def bound_action(self, binding):
        return True


@pytest.fixture
def actions(tasks):
    h = tasks
    catalog, pin = synthetic_catalog()
    h.command_fleet = FleetDependencies(
        recovery=h.fleet_recovery,
        catalog=catalog,
        installation_id="test-installation",
        limiter=RateLimiter(Settings(env="test")),
    )
    h.closing.append(h.command_fleet)
    h.command_key = CommandKey(h)
    h.records = ExecutionPinnedRecordSource(
        budgets=h.budgets, artifacts=h.store, recovery=h.fence, installation_id="test-installation"
    )
    h.commands = CommandService(
        h.fleet_recovery,
        ExecutionDispatchAuthority(charges=Charges()),
        h.records,
        h.command_key,
        dependencies=h.command_fleet,
    )
    h.dispatcher = ActionDispatchService(
        commands=h.commands,
        fleet=h.command_fleet,
        budgets=h.budgets,
        artifacts=h.store,
        artifact_policy=h.policy,
        signer=h.command_key,
        trust=h.command_key,
        installation_id="test-installation",
    )
    h.runtime = action_runtime(h, pin)

    def reviewed(key, package=None, documents=None, decide=True, active_ms=60000):
        """Run to an approved intent with its command attempt reserved."""
        from .test_tasks import review_package

        if package is None:
            package, documents = review_package()
            full = doc("reviewed_action")
            package["body"]["steps"] = full["body"]["steps"]  # intent, review, dispatch, verify
        execution_id = h.start2(package, key, documents)
        task = h.call("get", h.actor, task_id(h, execution_id)) if decide else None
        if decide:
            reviewer = h.reviewer()
            h.call("claim", reviewer, task.id)
            intent = intent_row(h, execution_id)
            h.call(
                "decide",
                reviewer,
                task.id,
                DecisionRequest(
                    decision_id="d-" + key, intent_sha256=intent.intent_sha256, decision="allow"
                ),
            )
            h.run2()
            reserve(h, dispatch_attempt(h, execution_id), active_ms)
        return execution_id

    def poll(principal=None):
        with Session(h.engine) as db, db.begin():
            return h.dispatcher.poll(db, principal or h.runtime)

    def claim(delivery, principal=None):
        command = json.loads(delivery.command_bytes)
        with Session(h.engine) as db, db.begin():
            return h.dispatcher.claim(
                db,
                principal or h.runtime,
                command["command_id"],
                sha256(delivery.command_bytes).hexdigest(),
            )

    h.reviewed, h.poll, h.claim = reviewed, poll, claim
    return h


def action_runtime(h, pin, epoch=1):
    """A runtime instance whose observation ceiling includes the synthetic connector."""
    h.pin = pin
    principal = runtime(h, epoch=epoch)
    with Session(h.engine) as db, db.begin():
        instance = db.get(RuntimeInstance, principal.identity.instance_id)
        instance.observation_ceiling = json.dumps(
            {"platform": "linux", "connectors": [pin.model_dump()]}, sort_keys=True
        ).encode()
    return principal


def task_id(h, execution_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionHumanTask.id).where(ExecutionHumanTask.execution_id == execution_id)
        )


def intent_row(h, execution_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionActionIntent).where(ExecutionActionIntent.execution_id == execution_id)
        )


def dispatch_attempt(h, execution_id, last=False):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionAttempt.id)
            .join(
                ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id
            )
            .where(
                ExecutionAttempt.execution_id == execution_id,
                ExecutionStepInstance.occurrence_path.like("%#dispatch"),
            )
            .order_by(ExecutionAttempt.ordinal.desc() if last else ExecutionAttempt.ordinal)
            .limit(1)
        )


def reserve(h, attempt_id, active_ms=60000):
    with Session(h.engine) as db, db.begin():
        h.budgets.reserve_attempt(db, h.actor, attempt_id, Demand(active_ms).demand(attempt_id))


def action_commands(h):
    with Session(h.engine) as db:
        return list(db.scalars(select(ExecutionActionCommand)))


def receipt_for(h, execution_id, lease, *, status="accepted", intent_sha256=None, action_id=None):
    from lotuspond.execution.action_contracts import ActionReport

    intent = intent_row(h, execution_id)
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, lease.attempt_id)
    return ActionReport(
        attempt_id=lease.attempt_id,
        lease_revision=lease.lease_revision,
        receipt={
            "receipt_id": "receipt-" + lease.attempt_id[:8],
            "action_id": action_id or intent.action_id,
            "intent_sha256": intent_sha256 or intent.intent_sha256,
            "attempt": attempt.ordinal,
            "target_operation_ref": "1",
            "status": status,
            "evidence_refs": [],
        },
        response=None,
        usage={"input_tokens": 0, "output_tokens": 0, "cost_microusd": 0, "completed_at_ms": 0},
    )


def report(h, receipt, principal=None):
    with Session(h.engine) as db, db.begin():
        return h.dispatcher.record_receipt(db, principal or h.runtime, receipt)
