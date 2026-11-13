"""E05 Task 7 harness: a fake action control plane, the synthetic target and a worker.

`FakeActionPlane` plays the server's side of the action protocol in memory, with
what a runtime must survive: its own clock and signing key, one admitted command
per action (signed exactly as the API signs it), durable claims (an equal claim
returns the same lease), records served only to the live lease holder, renewals
that can be refused (`refuse_renewal`: forbidden while live) or lost, and values,
receipts and stops that need the live lease. Reports for a lease that no longer
holds are kept as `historical`, as Task 8's route will. It grants nothing a real
control plane would not; the worker verifies everything it receives.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
import uuid
from types import SimpleNamespace

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.actions import (
    BindingContext,
    HumanDecision,
    build_action_intent,
    decision_digest,
)
from lotuspond_sdk.executable.signing import command_signature_envelope_json, sign_runtime_command
from lotuspond_sdk.executable.wire import RuntimeCommand, canonical_wire_json
from lotuspond_sdk.trust import TrustSet

from lotuspond_runtime.action_invocation import WriteDescriptor, WriteProviderSet
from lotuspond_runtime.action_protocol import ActionLease
from lotuspond_runtime.action_worker import ActionDependencies
from lotuspond_runtime.activity_state import InvocationJournal
from lotuspond_runtime.activity_worker import ServerClock
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    CommandDelivery,
    CommandTrustSnapshot,
    ConnectionConfig,
    ConnectorPin,
    DataHandling,
    DestinationRule,
    PinnedCommandRecords,
    ResolvedSecretBinding,
    RuntimeIdentity,
    SecretPin,
)

PIN = ConnectorPin(name="synthetic-target", version="1.0.0", artifact_sha256="e" * 64)
SECRET_PIN = SecretPin(reference_id="11111111-1111-4111-8111-111111111111", version=1)
LOOPBACK = {"allowed_private_cidrs": ["127.0.0.0/8", "::1/128"]}


def uid():
    return str(uuid.uuid4())


def wall_ms():
    return time.time_ns() // 1_000_000


def identity():
    return RuntimeIdentity(
        scope=dict(workspace_id=uid(), project_id=uid(), environment_id=uid()),
        installation_id="test-installation",
        recovery_epoch=1,
        pool_id=uid(),
        instance_id=uid(),
        credential_version=1,
        generation=1,
    )


@dataclasses.dataclass
class Action:
    attempt_id: str
    command: RuntimeCommand
    delivery: CommandDelivery
    records: PinnedCommandRecords
    status: str = "pending"  # pending, claimed, reported
    lease: ActionLease | None = None


class FakeActionPlane:
    def __init__(
        self,
        ident,
        *,
        port,
        skew_ms=0,
        lease_ms=60000,
        deadline_ms=600000,
        secret=False,
        operations=("submit",),
    ):
        self.identity, self.skew_ms, self.port = ident, skew_ms, port
        self.lease_ms, self.deadline_ms, self.secret = lease_ms, deadline_ms, secret
        self.operations = operations
        self.key = Ed25519PrivateKey.generate()
        self.actions, self.values = {}, {}
        self.claims, self.renewals, self.calls = [], [], []
        self.reports, self.historical = [], []
        self.refuse_renewal = self.lose_renewal = False
        self.offers = []

    def now_ms(self):
        return wall_ms() + self.skew_ms

    # -- trust ---------------------------------------------------------------
    def current(self, installation_id):
        public = self.key.public_key().public_bytes_raw()
        trust = TrustSet.model_validate(
            {
                "format_version": 1,
                "keys": [
                    {
                        "key_id": hashlib.sha256(public).hexdigest(),
                        "public_key": public.hex(),
                        "state": "allowed",
                    }
                ],
            }
        )
        return CommandTrustSnapshot(1, trust, self.now_ms() + 60000)

    # -- actions -------------------------------------------------------------
    def configuration(self, **changes):
        fields = dict(
            connector=PIN,
            operations=self.operations,
            destinations=(
                DestinationRule(host="localhost", port=self.port, path_prefix="/target"),
            ),
            secrets=(SECRET_PIN,) if self.secret else (),
            data_handling=DataHandling(timeout_ms=10000),
        )
        fields.update(changes)
        return ConnectionConfig(**fields)

    def action(self, *, operation="submit", target=None, value=None, configuration=None):
        """An approved intent with its signed, admitted command pending for this runtime."""
        ident, now = self.identity, self.now_ms()
        target = {"invoice_id": "INV-7"} if target is None else target
        value = {"amount": 1250} if value is None else value
        actual_target, actual_input = rfc8785.dumps(target), rfc8785.dumps(value)
        configuration = configuration or self.configuration()
        common = dict(
            installation_id=ident.installation_id,
            recovery_epoch=ident.recovery_epoch,
            **ident.scope.model_dump(),
            execution_id=uid(),
            action_id=uid(),
        )
        binding = dict(
            **common,
            step_id="dispatch",
            package_sha256="1" * 64,
            configuration_sha256="2" * 64,
            plan_sha256="3" * 64,
            policy_sha256="4" * 64,
            requester_id="requester-1",
            connector_name=configuration.connector.name,
            connector_version=configuration.connector.version,
            connector_artifact_sha256=configuration.connector.artifact_sha256,
            operation=operation,
            target_sha256=hashlib.sha256(actual_target).hexdigest(),
            input_sha256=hashlib.sha256(actual_input).hexdigest(),
            approval_mode="human",
            independent_approval=True,
            issued_at_ms=now - 1000,
            expires_at_ms=now + 600000,
        )
        intent = build_action_intent(binding)
        decision = HumanDecision(
            decision_id=uid(),
            intent_sha256=intent.sha256,
            decision="allow",
            actor_id="reviewer-1",
            actor_kind="human",
            policy_sha256="4" * 64,
            issued_at_ms=now - 500,
            expires_at_ms=now + 600000,
        )
        context = BindingContext(
            **{k: binding[k] for k in BindingContext.model_fields if k in binding},
            worker_id=ident.instance_id,
            fencing_generation=ident.generation,
            now_ms=now,
        )
        command = RuntimeCommand(
            **common,
            command_id=uid(),
            replay_id=uid(),
            intent_sha256=intent.sha256,
            decision_sha256=decision_digest(decision),
            worker_id=ident.instance_id,
            fencing_generation=ident.generation,
            issued_at_ms=now - 100,
            expires_at_ms=now + 299900,
        )
        delivery = CommandDelivery(
            canonical_wire_json(command),
            command_signature_envelope_json(sign_runtime_command(command, self.key)),
            1,
        )
        records = PinnedCommandRecords(
            intent,
            decision,
            context,
            uid(),
            1,
            configuration,
            (ResolvedSecretBinding(SECRET_PIN.reference_id, 1, "binding-1"),)
            if configuration.secrets
            else (),
            actual_input,
            actual_target,
        )
        action = Action(uid(), command, delivery, records)
        self.actions[command.command_id] = action
        return action

    def _find(self, delivery):
        command = json.loads(delivery.command_bytes)
        action = self.actions.get(command["command_id"])
        if action is None or action.delivery.command_bytes != delivery.command_bytes:
            raise FleetError("not_found")
        return action

    def _by_attempt(self, attempt_id):
        for action in self.actions.values():
            if action.attempt_id == attempt_id:
                return action
        raise FleetError("not_found")

    def _live(self, action):
        return (
            action.status == "claimed"
            and action.lease is not None
            and self.now_ms() < action.lease.expires_at_ms
        )

    # -- the runtime's client ------------------------------------------------
    def poll(self):
        self.calls.append("poll")
        if self.offers:
            return self.offers.pop(0)
        for action in self.actions.values():
            if action.status == "pending":
                return action.delivery
        return None

    def claim(self, delivery):
        self.calls.append("claim")
        action = self._find(delivery)
        self.claims.append(action.attempt_id)
        if action.lease is not None:
            if not self._live(action):
                raise FleetError("conflict")
            return action.lease
        now = self.now_ms()
        deadline = now + self.deadline_ms
        action.lease = ActionLease(
            attempt_id=action.attempt_id,
            command_id=action.command.command_id,
            ordinal=1,
            lease_revision=1,
            expires_at_ms=min(now + self.lease_ms, deadline),
            deadline_ms=deadline,
        )
        action.status = "claimed"
        return action.lease

    def records(self, delivery):
        self.calls.append("records")
        action = self._find(delivery)
        if not self._live(action):
            raise FleetError("conflict")
        return action.records

    def renew(self, lease):
        self.calls.append("renew")
        action = self._by_attempt(lease.attempt_id)
        self.renewals.append(lease.lease_revision)
        if not self._live(action) or self.lose_renewal:
            raise FleetError("conflict")
        if self.refuse_renewal:
            raise FleetError("forbidden")
        now = self.now_ms()
        action.lease = action.lease.model_copy(
            update={
                "lease_revision": action.lease.lease_revision + 1,
                "expires_at_ms": min(now + self.lease_ms, action.lease.deadline_ms),
            }
        )
        return action.lease

    def store(self, attempt_id, content):
        self.calls.append("store")
        action = self._by_attempt(attempt_id)
        if not self._live(action):
            raise FleetError("conflict")
        handle = ArtifactHandle(id=uid(), version=1, sha256=hashlib.sha256(content).hexdigest())
        self.values[handle.id] = content
        return handle

    def _report(self, attempt_id, revision, item):
        action = self._by_attempt(attempt_id)
        if self._live(action) and revision <= action.lease.lease_revision:
            action.status = "reported"
            self.reports.append(item)
        else:
            self.historical.append(item)  # Task 8's route records late evidence

    def receipt(self, report):
        self.calls.append("receipt")
        self._report(report.attempt_id, report.lease_revision, report)

    def stop(self, stop):
        self.calls.append("stop")
        self._report(stop.attempt_id, stop.lease_revision, stop)


def anchored(plane) -> ServerClock:
    clock = ServerClock()
    sent = time.monotonic()
    now = plane.now_ms()
    clock.anchor(now, sent=sent, received=time.monotonic())
    return clock


def descriptor(target, counter, *, idempotency="target_key", egress=LOOPBACK, loopback=True):
    return WriteDescriptor(
        "synthetic-target-write",
        PIN,
        {"submit": idempotency},
        test_only=True,
        script={
            "ca_file": str(target.ca_file),
            "egress": egress,
            "loopback": loopback,
            "counter": str(counter),
        },
    )


class Secrets:
    """Fake mounted secrets: records when each binding was resolved."""

    def __init__(self, value: bytes, plane=None):
        self.value, self.plane, self.resolved = value, plane, []

    def resolve(self, binding):
        self.plane.calls.append("resolve")
        self.resolved.append(binding.binding_id)
        return self.value


def build(tmp_path, target, *, checkpoint=None, secrets=None, plane=None, **options):
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    journal_dir = tmp_path / "action-journal"
    journal_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(journal_dir, 0o700)
    counter = tmp_path / "provider-calls"
    plane = plane or FakeActionPlane(identity(), port=target.port, secret=secrets is not None)
    if isinstance(secrets, Secrets):
        secrets.plane = plane
    journal = InvocationJournal(journal_dir)
    providers = WriteProviderSet((descriptor(target, counter, **options),), test_mode=True)
    deps = ActionDependencies(
        identity=plane.identity,
        client=plane,
        trust=plane,
        providers=providers,
        journal=journal,
        clock=anchored(plane),
        secrets=secrets,
        renew_every_ms=1000,
        retry_pause_s=0.01,
        checkpoint=checkpoint,
    )
    return SimpleNamespace(
        plane=plane, deps=deps, journal=journal, journal_dir=journal_dir, counter=counter
    )


def reopen(env, **changes):
    """A restarted worker: the old journal handle is closed, the directory reopened."""
    env.journal.close()
    journal = InvocationJournal(env.journal_dir)
    deps = dataclasses.replace(env.deps, journal=journal, **changes)
    return SimpleNamespace(**{**vars(env), "journal": journal, "deps": deps})


def calls(counter) -> int:
    return len(counter.read_text().splitlines()) if counter.exists() else 0
