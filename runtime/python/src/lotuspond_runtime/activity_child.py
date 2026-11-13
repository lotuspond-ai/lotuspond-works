"""Fixed provider child process: one call, bounded framing, fixed failure codes.

Started only as `python -I -m lotuspond_runtime.activity_child <fd> <worker pid>` by
the activity supervisor with an empty environment. It dies with the worker (Linux
parent-death signal), receives one request on its socket, rebuilds the frozen
request types, selects a provider implementation by fixed identifier (synthetic
ones only in test mode) and calls `ReadProvider.invoke` or
`ModelProvider.generate`. It answers with the provider's result or a fixed failure
code before the deadline it was given. Exception text never leaves it.
"""

from __future__ import annotations

import ctypes
import os
import signal
import socket
import sys
import time

from lotuspond_sdk.activity import validate_activity
from lotuspond_sdk.executable.package import ArtifactRef

from .activity_framing import receive, send
from .activity_protocol import PinnedActivityRecords
from .protocol import ConnectionConfig, ConnectorPin
from .providers import (
    ActivityFailure,
    ModelReply,
    ModelRequest,
    ProviderDescriptor,
    ProviderResult,
)

PR_SET_PDEATHSIG = 1
MAX_CALL_MS = 3_600_000


def _die_with_parent(worker_pid: int):
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError("parent-death signal unavailable")
    if os.getppid() != worker_pid:
        raise OSError("the worker is gone")


def _descriptor(raw) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=raw["provider_id"],
        dependency=ArtifactRef.model_validate(raw["dependency"]),
        effect=raw["effect"],
        operations=frozenset(raw["operations"]),
        test_only=raw["test_only"] is True,
        script=raw["script"],
        settings=raw.get("settings"),
        connector=None
        if raw["connector"] is None
        else ConnectorPin.model_validate(raw["connector"]),
    )


def _records(raw) -> PinnedActivityRecords:
    return PinnedActivityRecords(
        envelope=validate_activity(raw["envelope"]),
        connection=(
            None
            if raw["connection"] is None
            else ConnectionConfig.model_validate(raw["connection"])
        ),
        dependency=ArtifactRef.model_validate(raw["dependency"]),
        deadline_ms=raw["deadline_ms"],
        input_schema=raw["input_schema"],
        output_schema=raw["output_schema"],
        request_sha256=raw["request_sha256"],
    )


def _provider(descriptor, secrets, test_mode):
    if test_mode is True and descriptor.test_only:
        from . import activity_fixture

        implementation = activity_fixture.PROVIDERS.get(descriptor.provider_id)
        if implementation is not None:
            return implementation(descriptor, secrets)
        return None
    if descriptor.test_only:
        return None
    # Real providers, by fixed identifier only. One whose optional dependency is not
    # installed is unavailable, never a fallback to anything else.
    if descriptor.provider_id == "https-json" and descriptor.effect == "read":
        from .https_json import HttpsJsonReadProvider

        return HttpsJsonReadProvider(descriptor, secrets, test_mode=test_mode is True)
    if descriptor.provider_id == "anthropic-messages" and descriptor.effect == "model":
        try:
            from .anthropic_messages import AnthropicModelProvider
        except ImportError:
            return None
        return AnthropicModelProvider(descriptor, secrets)
    return None


def _failure(failure: ActivityFailure) -> dict:
    answer = {"failure": failure.code}
    if failure.usage is not None:
        answer["usage"] = failure.usage.model_dump(mode="json")
    if failure.reached is False:
        answer["not_reached"] = True
    return answer


def _answer(request):
    descriptor = _descriptor(request["descriptor"])
    secrets = tuple(bytes.fromhex(value) for value in request["secrets"])
    provider = _provider(descriptor, secrets, request.get("test_mode"))
    if request.get("test_mode") is True:
        from . import activity_fixture

        activity_fixture.ATTEMPT_ID = request.get("attempt_id")  # its call counter
    if provider is None:
        return {"failure": "unavailable", "not_reached": True}
    try:
        if descriptor.effect == "read":
            result = provider.invoke(
                _records(request["records"]), request["arguments"], request["deadline_ms"]
            )
            if type(result) is not ProviderResult:
                raise TypeError
            return {"ok": {"value": result.value, "usage": result.usage.model_dump(mode="json")}}
        reply = provider.generate(
            ModelRequest.from_json(request["model_request"]), request["deadline_ms"]
        )
        if type(reply) is not ModelReply:
            raise TypeError
        calls = [{"name": call.name, "arguments": call.arguments} for call in reply.tool_calls]
        return {
            "ok": {
                "output": reply.output,
                "tool_calls": calls,
                "usage": reply.usage.model_dump(mode="json"),
                "continuation": reply.continuation,
            }
        }
    except ActivityFailure as failure:
        return _failure(failure)


def main(argv):
    channel = socket.socket(fileno=int(argv[1]))
    started = time.monotonic_ns()
    try:
        try:
            _die_with_parent(int(argv[2]))
        except Exception:
            send(channel, {"failure": "unavailable", "not_reached": True}, started + 5 * 10**9)
            return 1
        request = receive(channel, started + 30 * 10**9)
        timeout_ms = request["timeout_ms"]
        if type(timeout_ms) is not int or not 0 < timeout_ms <= MAX_CALL_MS:
            raise ValueError
        deadline = started + timeout_ms * 1_000_000
        try:
            answer = _answer(request)
        except Exception:
            answer = {"failure": "provider_failed"}  # never the exception's text
        send(channel, answer, deadline)
    finally:
        channel.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
