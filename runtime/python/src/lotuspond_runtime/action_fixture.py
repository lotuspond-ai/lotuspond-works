"""The synthetic target connector's write and read providers, for tests only.

Selectable only by a test-mode worker with test-only descriptors, by their fixed
identifiers; production settings leave them unavailable. They reach the synthetic
target (a separate test process serving loopback HTTPS) only through
`DestinationTransport`: one resolution, the egress policy on every resolved address,
the descriptor's CA file, no proxy and no redirect. Their behaviour comes from the
descriptor's data script (CA file, egress policy, an optional call counter); no code
is loaded.

The target's protocol is fixed: `POST <prefix>/write` with the canonical JSON
`{"operation", "idempotency_key", "target", "input"}` answers
`{"status": "applied" | "duplicate", "record": ...}`; `POST <prefix>/read` with
`{"operation", "target"}` answers the records it holds for that target. The
connection's one secret, when pinned, travels as the fixture secret header.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import rfc8785
from lotuspond_sdk.activity import UsageObservation

from .action_protocol import WriteResult
from .destination import DestinationTransport, EgressPolicy
from .errors import FleetError
from .providers import ActivityFailure, ProviderResult

COUNTER_NAME = "provider-calls"
# The command this child serves, set by the test-mode child before each call.
COUNTER_ID = None
_STATUS = {"applied": "accepted", "duplicate": "observed_effect"}


def _zero():
    # A synthetic write has no metered cost: zero is known, not assumed.
    return UsageObservation(input_tokens=0, output_tokens=0, cost_microusd=0, completed_at_ms=0)


def _count(script):
    """One line per provider call, so tests observe calls rather than assume them."""
    path = script.get("counter")
    if path is None:
        return
    if type(path) is not str or not os.path.isabs(path) or os.path.basename(path) != COUNTER_NAME:
        raise ValueError("invalid counter")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, f"{COUNTER_ID or 'unknown'}\n".encode("ascii"))
    finally:
        os.close(fd)


class _Target:
    """The pinned connection's one destination, reached through `DestinationTransport`."""

    def __init__(self, descriptor, secrets):
        script = descriptor.script_data() or {}
        egress = script.get("egress") or {}
        self.descriptor, self._secrets, self._script = descriptor, secrets, script
        self._policy = EgressPolicy(
            tuple(egress.get("allowed_private_cidrs", ())),
            tuple(egress.get("denied_cidrs", ())),
            tuple(egress.get("denied_hosts", ())),
        )
        self._ca = Path(script.get("ca_file", ""))
        if not self._ca.is_absolute():
            raise ValueError("The CA file must be an absolute path")
        self._loopback = script.get("loopback") is True

    def _headers(self):
        if not self._secrets:
            return {}
        try:
            value = self._secrets[0].decode("ascii").strip()
        except UnicodeError:
            raise ActivityFailure("unavailable", reached=False) from None
        return {"X-Lotuspond-Fixture-Secret": value}

    def post(self, connection, route, body: bytes, deadline_ms):
        """One request; `(sent, answer)`, where `answer` is None when none arrived."""
        import time

        if connection is None or len(connection.destinations) != 1:
            raise ActivityFailure("unavailable", reached=False)
        if len(self._secrets) < len(connection.secrets):
            raise ActivityFailure("unavailable", reached=False)
        rule = connection.destinations[0]
        limits = connection.data_handling
        if len(body) > limits.max_request_bytes:
            raise ActivityFailure("provider_failed", reached=False)
        headers = self._headers()
        _count(self._script)
        deadline = time.monotonic_ns() + limits.timeout_ms * 1_000_000
        transport = (
            DestinationTransport._for_test(self._policy, self._ca)
            if self._loopback
            else DestinationTransport(self._policy, self._ca)
        )
        transport._worker_process = True  # the DNS helper stays in the child's group
        path = rule.path_prefix.rstrip("/") + route
        sent = False
        try:
            with transport.connect(rule, path, deadline) as connected:
                try:
                    content = connected.send("POST", body, headers, limits.max_response_bytes)
                finally:
                    sent = connected.sent
        except FleetError:
            return sent, None
        try:
            return True, json.loads(content)
        except ValueError:
            return True, None


class SyntheticTargetWriteProvider(_Target):
    """`WriteProvider` for the synthetic target connector's write operations."""

    def invoke(self, records, idempotency_key, deadline_ms) -> WriteResult:
        binding = records.intent.binding
        if self.descriptor.idempotency(binding.operation) is None:
            raise ActivityFailure("unavailable", reached=False)
        body = rfc8785.dumps(
            {
                "operation": binding.operation,
                "idempotency_key": idempotency_key,
                "target": json.loads(records.actual_target),
                "input": json.loads(records.actual_input),
            }
        )
        sent, answer = self.post(records.configuration, "/write", body, deadline_ms)
        if not sent:
            raise ActivityFailure("unavailable", reached=False)  # nothing left this host
        if (
            type(answer) is not dict
            or set(answer) != {"status", "record"}
            or answer["status"] not in _STATUS
        ):
            # Sent, but no usable answer: the target may have applied it. Usage is
            # still known zero only because this synthetic target has no metered
            # cost; a metered provider reports unknown usage here.
            return WriteResult("unknown", None, _zero())
        return WriteResult(_STATUS[answer["status"]], answer["record"], _zero())


class SyntheticTargetReadProvider(_Target):
    """`ReadProvider` for the synthetic target's read operation (read-back, Task 8)."""

    def invoke(self, records, arguments, deadline_ms) -> ProviderResult:
        operation = records.envelope.operation
        if operation not in self.descriptor.operations:
            raise ActivityFailure("unavailable", reached=False)
        if type(arguments) is not dict:
            raise ActivityFailure("provider_failed", reached=False)
        body = rfc8785.dumps({"operation": operation, "target": arguments.get("target")})
        sent, answer = self.post(records.connection, "/read", body, deadline_ms)
        if not sent:
            raise ActivityFailure("unavailable", reached=False)
        if answer is None:
            raise ActivityFailure("provider_failed")
        return ProviderResult(answer, _zero())


WRITE_PROVIDERS = {"synthetic-target-write": SyntheticTargetWriteProvider}
READ_PROVIDERS = {"synthetic-target-read": SyntheticTargetReadProvider}


class FixtureCommandTrust:
    """Test-only command trust: a private `TrustSet` file, reread on every call.

    Each snapshot is valid for the 60 s E03 allows, on the worker's server-anchored
    clock, so a test rotates or revokes the signer by rewriting the file.
    """

    def __init__(self, path, clock):
        self.path, self.clock = Path(path), clock

    def current(self, installation_id):
        from lotuspond_sdk.trust import TrustSet

        from .protocol import CommandTrustSnapshot
        from .state import read_private_file

        trust = TrustSet.model_validate_json(read_private_file(self.path, 65536))
        return CommandTrustSnapshot(1, trust, self.clock.now_ms() + 60000)


def worker_config(raw) -> tuple:
    """A test-mode configuration's `actions` section: command trust and write providers.

    Strict, closed data: `{"trust_file": <absolute path>, "providers": [{"provider_id",
    "connector", "operations", "script"}]}`, each provider one of the fixed synthetic
    identifiers. Never code. Returns `(trust_file, descriptors)`.
    """
    from .action_invocation import WriteDescriptor
    from .protocol import ConnectorPin

    if type(raw) is not dict or set(raw) != {"trust_file", "providers"}:
        raise ValueError("Invalid action configuration")
    trust_file = raw["trust_file"]
    if (
        type(trust_file) is not str
        or not trust_file.startswith("/")
        or ".." in trust_file.split("/")
    ):
        raise ValueError("Invalid action trust path")
    providers = raw["providers"]
    if type(providers) is not list or not 1 <= len(providers) <= 8:
        raise ValueError("Invalid providers")
    descriptors = []
    for item in providers:
        if type(item) is not dict or set(item) - {
            "provider_id",
            "connector",
            "operations",
            "script",
        }:
            raise ValueError("Invalid provider")
        if item.get("provider_id") not in WRITE_PROVIDERS:
            raise ValueError("Unknown synthetic provider")
        descriptors.append(
            WriteDescriptor(
                item["provider_id"],
                ConnectorPin.model_validate(item["connector"]),
                item["operations"],
                test_only=True,
                script=item.get("script"),
            )
        )
    return Path(trust_file), tuple(descriptors)
