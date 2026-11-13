"""Fixed synthetic read and model providers for tests only.

Selectable only when the worker and its descriptors are in test mode, and only by
their fixed identifiers. They implement `ReadProvider` and `ModelProvider`; their
behaviour comes from a data script carried by the test-only descriptor, and no code
is loaded. Neither has a network effect, and the read fixture has no write
operation. Their only file effect is an optional call counter: an absolute path
whose file name is `provider-calls`, opened for append without following links.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from lotuspond_sdk.activity import UsageObservation

from .providers import ActivityFailure, ModelReply, ProviderResult, ToolProposal

COUNTER_NAME = "provider-calls"


def _usage(raw):
    raw = raw or {}
    # Completion is stamped by the worker on its server-anchored clock.
    return UsageObservation(
        input_tokens=raw.get("input_tokens", 1),
        output_tokens=raw.get("output_tokens", 1),
        cost_microusd=raw.get("cost_microusd", 1),
        completed_at_ms=0,
    )


# The attempt this child serves, set by the test-mode child before each call.
ATTEMPT_ID = None


def _count(script):
    """One line per provider call, naming its attempt, so calls are counted per attempt."""
    path = script.get("counter")
    if path is None:
        return
    if type(path) is not str or not os.path.isabs(path) or os.path.basename(path) != COUNTER_NAME:
        raise ValueError("invalid counter")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, f"{ATTEMPT_ID or 'unknown'}\n".encode("ascii"))
    finally:
        os.close(fd)


def _act(script, step, seen):
    if step.get("ignore_term"):
        import signal

        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # forces the KILL escalation
    _count(script)  # after any TERM handling is installed: tests wait for it
    if step.get("sleep_s"):
        time.sleep(step["sleep_s"])
    if step.get("lognormal"):
        # Injected provider latency for load runs: exp(N(mu, sigma)) seconds, capped.
        import random

        shape = step["lognormal"]
        time.sleep(min(shape["cap_s"], random.lognormvariate(shape["mu"], shape["sigma"])))
    if "raise" in step:
        # Private provider text: must never leave the child.
        raise RuntimeError(step["raise"])
    required = step.get("require_seen", ())
    for value in [required] if type(required) is str else required:
        if value not in seen:
            raise ActivityFailure("provider_failed")  # positive control: input arrived
    if "failure" in step:
        raise ActivityFailure(
            step["failure"],
            usage=_usage(step["usage"]) if "usage" in step else None,
            reached=False if step.get("not_reached") else None,
        )


class FixtureReadProvider:
    """`ReadProvider` for the fixture connector's read operation."""

    def __init__(self, descriptor, secrets):
        self.descriptor, self._secrets = descriptor, secrets
        self._script = descriptor.script_data() or {}

    def invoke(self, records, arguments, deadline_ms) -> ProviderResult:
        if records.envelope.operation not in self.descriptor.operations:
            raise ActivityFailure("unavailable", reached=False)
        step = self._script.get("read", {})
        digests = [hashlib.sha256(secret).hexdigest() for secret in self._secrets]
        if "require_secrets" in step and step["require_secrets"] != digests:
            raise ActivityFailure("provider_failed", reached=False)
        _act(self._script, step, json.dumps(arguments))
        if "value_bytes" in step:
            return ProviderResult("x" * step["value_bytes"], _usage(step.get("usage")))
        return ProviderResult(step.get("value"), _usage(step.get("usage")))


class FixtureModelProvider:
    """`ModelProvider` replaying scripted rounds, indexed by the transcript length."""

    def __init__(self, descriptor, secrets):
        self.descriptor, self._secrets = descriptor, secrets
        self._script = descriptor.script_data() or {}

    def generate(self, request, deadline_ms) -> ModelReply:
        rounds = self._script.get("rounds", [])
        index = len(request.transcript)
        step = rounds[min(index, len(rounds) - 1)] if rounds else {"output": None}
        digests = [hashlib.sha256(secret).hexdigest() for secret in self._secrets]
        if "require_secrets" in step and step["require_secrets"] != digests:
            raise ActivityFailure("provider_failed", reached=False)
        seen = json.dumps(
            [request.instructions, request.input, list(request.context), list(request.transcript)]
        )
        _act(self._script, step, seen)
        calls = tuple(
            ToolProposal(call["name"], call["arguments"]) for call in step.get("tool_calls", [])
        )
        return ModelReply(step.get("output"), calls, _usage(step.get("usage")))


PROVIDERS = {"fixture-read": FixtureReadProvider, "fixture-model": FixtureModelProvider}


class FixtureTrust:
    """Test-only delivery trust: a private `TrustSet` file, reread on every call.

    A test rotates or revokes the signer by rewriting the file while the worker runs.
    """

    def __init__(self, path):
        from pathlib import Path

        self.path = Path(path)

    def current(self, scope, now_ms):
        from lotuspond_sdk.trust import TrustSet

        from .state import read_private_file

        return TrustSet.model_validate_json(read_private_file(self.path, 65536))


def worker_config(raw: bytes):
    """A test-mode `activity-worker` configuration: synthetic providers and their trust.

    Strict, closed data: `trust_file` and `journal_directory` are absolute private
    paths; each provider is one of the fixed synthetic identifiers above, pinned to
    its catalog dependency, with an optional behaviour script. Never code.
    """
    from pathlib import Path

    from lotuspond_sdk.executable.package import ArtifactRef

    from .protocol import ConnectorPin
    from .providers import ProviderDescriptor

    data = json.loads(raw)
    if type(data) is not dict or set(data) != {"trust_file", "journal_directory", "providers"}:
        raise ValueError("Invalid activity worker configuration")
    paths = [data["trust_file"], data["journal_directory"]]
    if any(
        type(path) is not str or not path.startswith("/") or ".." in path.split("/")
        for path in paths
    ):
        raise ValueError("Invalid activity worker path")
    providers = data["providers"]
    if type(providers) is not list or not 1 <= len(providers) <= 8:
        raise ValueError("Invalid providers")
    descriptors = []
    for item in providers:
        if type(item) is not dict or set(item) - {
            "provider_id",
            "dependency",
            "operations",
            "connector",
            "script",
        }:
            raise ValueError("Invalid provider")
        provider_id = item.get("provider_id")
        # The synthetic target's read operation serves E05 read-backs and verification.
        from .action_fixture import READ_PROVIDERS

        if provider_id not in PROVIDERS and provider_id not in READ_PROVIDERS:
            raise ValueError("Unknown synthetic provider")
        read = provider_id == "fixture-read" or provider_id in READ_PROVIDERS
        operations = item.get("operations", [])
        if type(operations) is not list or any(type(name) is not str for name in operations):
            raise ValueError("Invalid operations")
        if read and "connector" not in item:
            raise ValueError("A read provider names its connector")
        descriptors.append(
            ProviderDescriptor(
                provider_id,
                ArtifactRef.model_validate(item.get("dependency")),
                "read" if read else "model",
                frozenset(operations),
                test_only=True,
                script=item.get("script"),
                connector=ConnectorPin.model_validate(item["connector"]) if read else None,
            )
        )
    return Path(data["trust_file"]), Path(data["journal_directory"]), tuple(descriptors)
