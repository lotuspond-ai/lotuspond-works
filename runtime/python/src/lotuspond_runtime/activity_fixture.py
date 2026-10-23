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


def _count(script):
    path = script.get("counter")
    if path is None:
        return
    if type(path) is not str or not os.path.isabs(path) or os.path.basename(path) != COUNTER_NAME:
        raise ValueError("invalid counter")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, b"invoked\n")
    finally:
        os.close(fd)


def _act(script, step, seen):
    if step.get("ignore_term"):
        import signal

        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # forces the KILL escalation
    _count(script)  # after any TERM handling is installed: tests wait for it
    if step.get("sleep_s"):
        time.sleep(step["sleep_s"])
    if "raise" in step:
        # Private provider text: must never leave the child.
        raise RuntimeError(step["raise"])
    if "require_seen" in step and step["require_seen"] not in seen:
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
