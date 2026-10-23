"""Test-only control-plane stand-in and synthetic provider descriptors (E04 Task 10).

`FakeControlPlane` plays the server's side of the activity protocol in memory, with
the behaviour a runtime must survive in production:

- its own clock, `skew_ms` away from the runtime's wall clock;
- one call at a time, like the control transport: an overlapping call fails with
  `transport_failed` (and is counted), and `latency_s` delays every call;
- a poll that serves pending deliveries first, then reserved children of held
  parents; a targeted poll (`parent_attempt_id`) serves only that parent's children
  to the session holding it;
- one delivery per attempt and durable claims: an equal claim returns the same lease;
- child admission under a shared root budget, checking the declaration and that the
  arguments are a value the parent's holder stored;
- results that need the live lease, refuse a root while one of its children is
  claimed, refuse a completion in the server's future and are idempotent by attempt.

Faults are injected per call name (`faults["claim_ack"] = ["transport_failed"]`
fails one root claim after committing it, `child_claim_ack` a child's); `child_override = (kind, changes)` signs the
next child of that kind with altered fields. It grants nothing a real control plane would
not; the runtime under test verifies everything it receives as in production.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lotuspond_sdk.activity import (
    ActivityDelivery,
    ArtifactHandle,
    canonical_activity_json,
    sign_activity,
    validate_activity,
)
from lotuspond_sdk.executable.package import ArtifactRef
from lotuspond_sdk.trust import TrustSet

from lotuspond_runtime.activity_protocol import ActivityLease, PinnedActivityRecords, tool_name
from lotuspond_runtime.agent import AgentRecords
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import ConnectionConfig, ConnectorPin, DataHandling, RuntimeIdentity
from lotuspond_runtime.providers import ProviderDescriptor, ToolSpec

NULL = {"kind": "null"}
BOUNDED = {"schema": None, "definitions": []}
TYPED_NULL = {"schema": NULL, "definitions": []}
READ_INPUT = {"target": NULL, "input": NULL, "definitions": []}
CONNECTOR = ArtifactRef(
    kind="connector", name="connector-fixture", version="1.0.0", sha256="c" * 64
)
MODEL = ArtifactRef(kind="model", name="model-fixture", version="1.0.0", sha256="d" * 64)
# The installed connector artifact: a different digest from the catalog document's.
INSTALLED = ConnectorPin(name="connector-fixture", version="1.0.0", artifact_sha256="f" * 64)


def uid():
    return str(uuid.uuid4())


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


def connection(operations=("read",), secrets=(), connector=INSTALLED, data_handling=None):
    return ConnectionConfig(
        connector=connector,
        operations=operations,
        destinations=(),
        secrets=secrets,
        data_handling=data_handling or DataHandling(),
    )


def read_tool():
    return ToolSpec(
        name=tool_name("connector-fixture", "read"),
        dependency=CONNECTOR,
        operation="read",
        input_schema=READ_INPUT,
        output_schema=TYPED_NULL,
    )


def agent_records(**changes):
    fields = dict(
        model=MODEL,
        model_name="model-fixture",
        instructions="Synthetic instructions",
        tools=(read_tool(),),
        context=(),
        max_steps=4,
        max_input_tokens=1000,
        max_output_tokens=1000,
        max_cost_microusd=1000,
    )
    fields.update(changes)
    return AgentRecords(**fields)


def descriptors(read_script=None, model_script=None, counter=None, credentials=()):
    """Synthetic providers for the fixture connector and model; scripts are data."""
    read = dict(read_script or {"read": {"value": None}})
    model = dict(model_script or {"rounds": [{"output": None}]})
    if counter is not None:
        read["counter"] = model["counter"] = str(counter)
    return (
        ProviderDescriptor(
            "fixture-read",
            CONNECTOR,
            "read",
            frozenset({"read"}),
            test_only=True,
            script=read,
            connector=INSTALLED,
        ),
        ProviderDescriptor(
            "fixture-model", MODEL, "model", test_only=True, script=model, credentials=credentials
        ),
    )


def calls(counter: Path) -> int:
    """Provider calls observed by the synthetic child providers, never assumed."""
    return len(counter.read_text().splitlines()) if counter.exists() else 0


@dataclass
class Attempt:
    id: str
    kind: str
    dependency: ArtifactRef
    operation: str | None
    input: ArtifactHandle
    input_schema: dict
    output_schema: dict
    deadline_ms: int
    parent: str | None = None
    declaration: str | None = None
    status: str = "reserved"  # reserved, pending, leased, reported, expired, withdrawn
    delivery: ActivityDelivery | None = None
    lease: ActivityLease | None = None
    result: object = None
    values: set = field(default_factory=set)


class FakeControlPlane:
    def __init__(
        self,
        identity: RuntimeIdentity,
        wall_ms,
        *,
        skew_ms=0,
        budget_children=16,
        lease_ms=60000,
        child_lease_ms=None,
        deadline_ms=600000,
        refuse_renewal=False,
        secrets=(),
        latency_s=0.0,
        connection_changes=None,
    ):
        self.identity, self._wall, self.skew_ms = identity, wall_ms, skew_ms
        self.key = Ed25519PrivateKey.generate()
        self.budget_children, self.lease_ms, self.deadline_ms = (
            budget_children,
            lease_ms,
            deadline_ms,
        )
        self.child_lease_ms = child_lease_ms or lease_ms
        self.refuse_renewal, self.secrets, self.latency_s = refuse_renewal, secrets, latency_s
        self.connection_changes = connection_changes or {}
        self.values, self.attempts, self.agent_records = {}, {}, {}
        self.offers, self.child_requests, self.reported = [], {}, {}
        self.claims, self.renewals, self.results, self.calls = [], [], [], []
        self.faults, self.child_override, self.refuse_renewal_ids = {}, None, set()
        self.children = self.overlaps = 0
        self._busy = False
        self.execution_id, self.step_id, self.revision_id = uid(), uid(), uid()

    def now_ms(self):
        return self._wall() + self.skew_ms

    @contextmanager
    def _flight(self, name):
        # The control transport carries one request at a time.
        if self._busy:
            self.overlaps += 1
            raise FleetError("transport_failed")
        self._busy = True
        try:
            if self.latency_s:
                time.sleep(self.latency_s)
            self.calls.append(name)
            pending = self.faults.get(name)
            if pending:
                code = pending.pop(0)  # None lets this call through
                if code is not None:
                    raise FleetError(code)
            yield
        finally:
            self._busy = False

    def _live(self, attempt, now):
        return attempt.status == "leased" and attempt.lease.expires_at_ms > now

    # -- trust and records -------------------------------------------------
    def current(self, scope, now_ms):
        public = self.key.public_key().public_bytes_raw()
        return TrustSet.model_validate(
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

    def load(self, envelope):
        with self._flight("records"):
            attempt = self.attempts[envelope.attempt_id]
            return PinnedActivityRecords(
                envelope=envelope,
                connection=(
                    connection(secrets=self.secrets, **self.connection_changes)
                    if attempt.kind == "connector_read"
                    else None
                ),
                dependency=attempt.dependency,
                deadline_ms=attempt.deadline_ms,
                input_schema=attempt.input_schema,
                output_schema=attempt.output_schema,
                request_sha256=attempt.input.sha256,
            )

    def agent(self, envelope):
        with self._flight("agent"):
            return self.agent_records[envelope.attempt_id]

    # -- values ------------------------------------------------------------
    def put(self, value) -> ArtifactHandle:
        content = rfc8785.dumps(value)
        handle = ArtifactHandle(id=uid(), version=1, sha256=hashlib.sha256(content).hexdigest())
        self.values[handle.id] = content
        return handle

    def fetch(self, attempt_id, handle):
        with self._flight("fetch"):
            return self.values[handle.id]

    def store(self, attempt_id, content):
        with self._flight("store"):
            attempt = self.attempts[attempt_id]
            if not self._live(attempt, self.now_ms()):
                raise FleetError("conflict")
            if len(content) > 65536:
                raise FleetError("invalid_request")
            handle = ArtifactHandle(id=uid(), version=1, sha256=hashlib.sha256(content).hexdigest())
            self.values[handle.id] = content
            attempt.values.add(handle.id)
            return handle

    def value(self, handle):
        return json.loads(self.values[handle.id])

    # -- attempts ----------------------------------------------------------
    def _deliver(self, attempt):
        now = self.now_ms()
        raw = dict(
            schema_version="lotuspond.runtime-activity.v1",
            installation_id=self.identity.installation_id,
            recovery_epoch=self.identity.recovery_epoch,
            **self.identity.scope.model_dump(),
            execution_id=self.execution_id,
            step_instance_id=self.step_id,
            attempt_id=attempt.id,
            deployment_revision_id=self.revision_id,
            package_sha256="a" * 64,
            plan_sha256="b" * 64,
            configuration_sha256="e" * 64,
            input=attempt.input.model_dump(),
            kind=attempt.kind,
            dependency=attempt.dependency.model_dump(),
            operation=attempt.operation,
            pool_id=self.identity.pool_id,
            instance_id=self.identity.instance_id,
            generation=self.identity.generation,
            delivery_id=uid(),
            replay_id=uid(),
            issued_at_ms=now,
            # Like the real control plane: never past the attempt's deadline.
            expires_at_ms=min(now + 300000, attempt.deadline_ms),
        )
        override = self.child_override
        if attempt.parent is not None and override and override[0] == attempt.kind:
            raw.update(override[1])  # a validly signed child, bound to something else
            self.child_override = None
        envelope = validate_activity(raw)
        signature = sign_activity(envelope, self.key)
        attempt.delivery = ActivityDelivery(
            envelope=canonical_activity_json(envelope).decode(),
            signature=json.dumps(signature.model_dump(), sort_keys=True, separators=(",", ":")),
        )
        attempt.status = "pending"
        return attempt.delivery

    def root(self, kind="connector_read", value=None, *, operation="read", agent=None):
        """A dispatched root attempt and its pending delivery."""
        if value is None:
            value = {"target": None, "input": None} if kind == "connector_read" else None
        attempt = Attempt(
            id=uid(),
            kind=kind,
            dependency=CONNECTOR if kind == "connector_read" else MODEL,
            operation=operation if kind == "connector_read" else None,
            input=self.put(value),
            input_schema=READ_INPUT if kind == "connector_read" else BOUNDED,
            output_schema=TYPED_NULL,
            deadline_ms=self.now_ms() + self.deadline_ms,
        )
        self.attempts[attempt.id] = attempt
        if kind == "model":
            self.agent_records[attempt.id] = agent or agent_records()
        self._deliver(attempt)
        return attempt

    def withdraw(self, attempt):
        """The attempt's delivery is no longer offered (for tests that offer copies)."""
        attempt.status = "withdrawn"

    # -- the runtime's client ----------------------------------------------
    def poll(self, parent_attempt_id=None):
        with self._flight("poll"):
            if self.offers:
                return self.offers.pop(0)  # an explicit re-offer injected by a test
            now = self.now_ms()
            for attempt in self.attempts.values():
                if (
                    attempt.status == "pending"
                    and json.loads(attempt.delivery.envelope)["expires_at_ms"] <= now
                ):
                    attempt.status = "expired"
            ordered = list(self.attempts.values())
            if parent_attempt_id is not None:
                parent = self.attempts.get(parent_attempt_id)
                if parent is None or not self._live(parent, now):
                    return None
                ordered = [a for a in ordered if a.parent == parent_attempt_id]
            else:
                ordered = [
                    a
                    for a in ordered
                    if a.parent is None or self._live(self.attempts[a.parent], now)
                ]
            for attempt in ordered:
                if attempt.status == "pending":
                    return attempt.delivery
            for attempt in ordered:
                if attempt.status == "reserved":
                    return self._deliver(attempt)
            return None

    def claim(self, delivery):
        with self._flight("claim"):
            envelope = json.loads(delivery.envelope)
            attempt = self.attempts[envelope["attempt_id"]]
            if attempt.delivery is None or attempt.delivery.envelope != delivery.envelope:
                raise FleetError("unauthenticated")
            now = self.now_ms()
            self.claims.append(attempt.id)
            if attempt.lease is None:
                if attempt.status != "pending" or now >= envelope["expires_at_ms"]:
                    raise FleetError("conflict")
                if attempt.parent is not None and not self._live(
                    self.attempts[attempt.parent], now
                ):
                    raise FleetError("conflict")
                length = self.lease_ms if attempt.parent is None else self.child_lease_ms
                attempt.lease = ActivityLease(
                    attempt_id=attempt.id,
                    generation=self.identity.generation,
                    lease_revision=1,
                    expires_at_ms=min(now + length, attempt.deadline_ms),
                    deadline_ms=attempt.deadline_ms,
                )
                attempt.status = "leased"
            elif not self._live(attempt, now):
                raise FleetError("conflict")
            lost = self.faults.get("claim_ack" if attempt.parent is None else "child_claim_ack")
            if lost:
                raise FleetError(lost.pop(0))  # committed, but the answer is lost
            return attempt.lease

    def renew(self, lease):
        with self._flight("renew"):
            self.renewals.append(lease.attempt_id)
            attempt = self.attempts[lease.attempt_id]
            now = self.now_ms()
            if self.refuse_renewal or lease.attempt_id in self.refuse_renewal_ids:
                raise FleetError("forbidden")  # cancelled: reported by refusing renewal
            if not self._live(attempt, now) or (
                attempt.parent is not None and not self._live(self.attempts[attempt.parent], now)
            ):
                raise FleetError("conflict")
            attempt.lease = attempt.lease.model_copy(
                update={
                    "lease_revision": attempt.lease.lease_revision + 1,
                    "expires_at_ms": min(now + self.lease_ms, attempt.lease.deadline_ms),
                }
            )
            return attempt.lease

    def request_child(self, request):
        with self._flight("request_child"):
            parent = self.attempts[request.parent_attempt_id]
            now = self.now_ms()
            if parent.parent is not None or not self._live(parent, now):
                raise FleetError("conflict")
            digest = rfc8785.dumps(request.model_dump(mode="json"))
            known = self.child_requests.get((parent.id, request.request_id))
            if known is not None:
                if known[1] != digest:
                    raise FleetError("conflict")
                return known[0]
            records = self.agent_records[parent.id]
            if request.arguments.id not in parent.values:
                raise FleetError("forbidden")  # not a value this holder stored
            if request.kind == "model":
                if request.declaration_name != records.model_name:
                    raise FleetError("forbidden")
                dependency, operation = records.model, None
                input_schema = output_schema = BOUNDED
            else:
                spec = {t.name: t for t in records.tools}.get(request.declaration_name)
                arguments = json.loads(self.values[request.arguments.id])
                if (
                    spec is None
                    or type(arguments) is not dict
                    or set(arguments)
                    != {
                        "target",
                        "input",
                    }
                ):
                    raise FleetError("forbidden")
                dependency, operation = spec.dependency, spec.operation
                input_schema, output_schema = spec.input_schema, spec.output_schema
            if self.children >= self.budget_children:
                raise FleetError("forbidden")  # the shared root budget is spent
            self.children += 1
            child = Attempt(
                id=uid(),
                kind=request.kind,
                dependency=dependency,
                operation=operation,
                input=request.arguments,
                input_schema=input_schema,
                output_schema=output_schema,
                deadline_ms=parent.deadline_ms,
                parent=parent.id,
                declaration=request.declaration_name,
            )
            self.attempts[child.id] = child
            self.child_requests[(parent.id, request.request_id)] = (child.id, digest)
            return child.id

    def result(self, result):
        with self._flight("result"):
            attempt = self.attempts[result.attempt_id]
            now = self.now_ms()
            digest = rfc8785.dumps(result.model_dump(mode="json"))
            prior = self.reported.get(attempt.id)
            if prior is not None:
                if prior != digest:
                    raise FleetError("conflict")
                return None  # an equal repeated result changes nothing
            if not self._live(attempt, now) or result.lease_revision > attempt.lease.lease_revision:
                raise FleetError("conflict")
            if attempt.parent is None and any(
                child.parent == attempt.id and child.status == "leased"
                for child in self.attempts.values()
            ):
                raise FleetError("conflict")  # a running child reports first
            if result.usage.completed_at_ms > now:
                raise FleetError("invalid_request")  # never a completion in the future
            if result.code == "succeeded" and (
                result.result is None or result.result.id not in attempt.values
            ):
                raise FleetError("forbidden")
            self.reported[attempt.id] = digest
            self.results.append(result)
            attempt.result, attempt.status = result, "reported"
            if attempt.parent is None:
                for child in self.attempts.values():
                    if child.parent == attempt.id and child.status in ("reserved", "pending"):
                        child.status = "cancelled"  # unstarted children end with the root
            return None
