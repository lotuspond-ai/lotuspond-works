"""Internal synthetic-only invocation. This module is never selected by the supervisor."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .destination import DestinationTransport, remaining
from .errors import FleetError
from .protocol import ConnectionConfig, ConnectorPin, RuntimeIdentity, SecretPin
from .resolver import MountedSecretResolver


@dataclass(frozen=True)
class ResolvedSecretBinding:
    reference_id: str
    version: int
    binding_id: str


@dataclass(frozen=True)
class InvocationRequest:
    identity: RuntimeIdentity
    command_sha256: str
    configuration: ConnectionConfig
    operation: str
    path: str
    input_bytes: bytes
    secret_binding: ResolvedSecretBinding | None


@dataclass(frozen=True)
class FixtureResult:
    status: Literal["observed", "denied", "uncertain"]
    request_bytes: int
    response_bytes: int


class InvocationAuthority(Protocol):
    def authorize(self, request: InvocationRequest) -> None: ...


class DenyInvocationAuthority:
    def authorize(self, request: InvocationRequest) -> None:
        raise FleetError("forbidden")


@dataclass(frozen=True)
class InvocationLimits:
    operator_max_children: int = 1
    pool_max_children: int = 1

    def __post_init__(self):
        if any(
            type(n) is not int or not 1 <= n <= 32
            for n in (self.operator_max_children, self.pool_max_children)
        ):
            raise FleetError("invalid_request")


_lock = threading.Lock()
_limits = InvocationLimits()
_active = 0
_pools: dict[tuple[str, ...], int] = {}
_configuring = False
_cleanup_failed = False
_authority_slot = threading.BoundedSemaphore(1)


@contextmanager
def _configured_limits(limits: InvocationLimits):
    """Operator integration only, process-wide; request/child data cannot select limits."""
    global _limits, _configuring
    with _lock:
        if type(limits) is not InvocationLimits or _active or _configuring:
            raise FleetError("conflict")
        InvocationLimits(limits.operator_max_children, limits.pool_max_children)
        old, _limits, _configuring = _limits, limits, True
    try:
        yield
    finally:
        with _lock:
            if _active:
                # Keep current caps in force; never silently reconfigure active children.
                _configuring = False
                raise FleetError("conflict")
            _limits, _configuring = old, False


def fixture_pin() -> ConnectorPin:
    """Identity of the fixed packaged synthetic connector source artifact."""
    path = Path(__file__).with_name("fixture_connector.py")
    with path.open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise FleetError("trust_unavailable")
    return ConnectorPin(
        name="lotuspond-fixture",
        version="1.0.0",
        artifact_sha256=hashlib.sha256(content).hexdigest(),
    )


def _validate(request):
    if type(request) is not InvocationRequest:
        raise FleetError("invalid_request")
    identity = RuntimeIdentity.model_validate(request.identity)
    config = ConnectionConfig.model_validate(request.configuration)
    if (
        type(request.command_sha256) is not str
        or not re.fullmatch("[0-9a-f]{64}", request.command_sha256)
        or config.connector != fixture_pin()
        or request.operation != "inspect"
        or request.operation not in config.operations
        or type(request.input_bytes) is not bytes
        or len(request.input_bytes) > config.data_handling.max_request_bytes
        or len(config.destinations) != 1
        or len(config.secrets) != 1
    ):
        raise FleetError("command_rejected")
    from .destination import normalize_path

    normalize_path(request.path, config.destinations[0].path_prefix)
    binding = request.secret_binding
    if (
        type(binding) is not ResolvedSecretBinding
        or type(binding.binding_id) is not str
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", binding.binding_id)
        or SecretPin(reference_id=binding.reference_id, version=binding.version)
        != config.secrets[0]
    ):
        raise FleetError("secret_unavailable")
    return InvocationRequest(
        identity,
        request.command_sha256,
        config,
        request.operation,
        request.path,
        request.input_bytes,
        binding,
    )


def _send(channel, value, deadline):
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(data) > 65536:
        raise FleetError("input_limit")
    channel.settimeout(remaining(deadline))
    channel.sendall(struct.pack("!I", len(data)) + data)


def _receive(channel, deadline):
    def read(count):
        data = bytearray()
        while len(data) < count:
            channel.settimeout(remaining(deadline))
            chunk = channel.recv(count - len(data))
            if not chunk:
                raise FleetError("transport_failed")
            data.extend(chunk)
        return bytes(data)

    length = struct.unpack("!I", read(4))[0]
    if length > 65536:
        raise FleetError("input_limit")
    from .canonical import decode_fleet_json

    return decode_fleet_json(read(length))


def _observe(event: str):
    """Test observation hook; emits only fixed stage names, never payloads/paths."""


def _authorize(authority, request, deadline):
    """Bound even a stalled authority dependency; a late answer cannot grant a send."""
    if not _authority_slot.acquire(blocking=False):
        raise FleetError("forbidden")
    done = threading.Event()
    errors = []

    def run():
        try:
            authority.authorize(request)
        except BaseException:
            errors.append(True)
        finally:
            _authority_slot.release()
            done.set()

    try:
        threading.Thread(target=run, daemon=True).start()
    except RuntimeError:
        _authority_slot.release()
        raise FleetError("authority_unavailable") from None
    if not done.wait(remaining(deadline)) or errors:
        raise FleetError("forbidden")
    remaining(deadline)


def invoke_fixture(
    request: InvocationRequest,
    *,
    authority: InvocationAuthority,
    destination: DestinationTransport,
    resolver: MountedSecretResolver,
) -> FixtureResult:
    global _active, _cleanup_failed
    child = parent = channel = None
    admitted = sent = possible_send = proven_pre_send = False
    result = None
    cleanup_error = False
    key = None
    started_ns = time.monotonic_ns()
    deadline = started_ns + 30_000_000_000
    try:
        request = _validate(request)
        deadline = min(
            deadline,
            started_ns + request.configuration.data_handling.timeout_ms * 1_000_000,
        )
        if (
            type(destination) is not DestinationTransport
            or type(resolver) is not MountedSecretResolver
        ):
            raise FleetError("invalid_request")
        key = (
            request.identity.scope.workspace_id,
            request.identity.scope.project_id,
            request.identity.scope.environment_id,
            request.identity.pool_id,
        )
        with _lock:
            if (
                _cleanup_failed
                or _active >= _limits.operator_max_children
                or _pools.get(key, 0)
                >= min(_limits.operator_max_children, _limits.pool_max_children)
            ):
                raise FleetError("forbidden")
            _active += 1
            _pools[key] = _pools.get(key, 0) + 1
            admitted = True
        _authorize(authority, request, deadline)
        envelope = {
            "identity": request.identity.model_dump(mode="json"),
            "command_sha256": request.command_sha256,
            "configuration": request.configuration.model_dump(mode="json"),
            "operation": request.operation,
            "path": request.path,
            "input": base64.b64encode(request.input_bytes).decode("ascii"),
            "binding": vars(request.secret_binding),
            "policy": vars(destination.policy),
            "ca": str(destination.ca_file),
            "root": str(resolver.root),
            "mapping": str(resolver.mapping_file),
            "test_loopback": destination._test_loopback,
            "deadline": deadline,
        }
        # Check the complete envelope before spawning a worker.
        if len(json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode()) > 65536:
            raise FleetError("input_limit")
        parent, channel = socket.socketpair()
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "lotuspond_runtime.fixture_connector",
                str(channel.fileno()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={},
            pass_fds=(channel.fileno(),),
            close_fds=True,
            start_new_session=True,
        )
        channel.close()
        channel = None
        _send(parent, envelope, deadline)
        events = 0
        while True:
            if (
                possible_send
                and not select.select([parent], [], [], min(0.05, remaining(deadline)))[0]
            ):
                _authorize(authority, request, deadline)
                continue
            message = _receive(parent, deadline)
            events += 1
            if type(message) is not dict or events > 128:
                raise FleetError("transport_failed")
            if message == {"event": "authorize"}:
                _authorize(authority, request, deadline)
                # Grant delivery can succeed even if this call reports failure or the
                # parent stalls before consuming the child's later send notification.
                possible_send = True
                _send(parent, {"authorized": True}, deadline)
            elif message == {"event": "sending"}:
                sent = possible_send = True
                _observe("sending")
            elif message == {"event": "secret_open"}:
                _observe("secret_open")
            elif set(message) == {"result", "request_bytes", "response_bytes"}:
                if (
                    message["result"] not in ("observed", "denied", "uncertain")
                    or type(message["request_bytes"]) is not int
                    or not 0 <= message["request_bytes"] <= len(request.input_bytes)
                    or type(message["response_bytes"]) is not int
                    or not 0
                    <= message["response_bytes"]
                    <= request.configuration.data_handling.max_response_bytes
                ):
                    raise FleetError("transport_failed")
                child.wait(timeout=remaining(deadline))
                if child.returncode:
                    raise FleetError("transport_failed")
                if message["result"] == "denied":
                    if sent or message["request_bytes"] or message["response_bytes"]:
                        raise FleetError("transport_failed")
                    # Complete fixed-child result, successful exit and no contradictory
                    # notification provide positive proof of a pre-send refusal.
                    proven_pre_send = True
                result = FixtureResult(
                    message["result"], message["request_bytes"], message["response_bytes"]
                )
                break
            else:
                raise FleetError("transport_failed")
    except (FleetError, OSError, ValueError, TypeError, subprocess.SubprocessError):
        result = FixtureResult(
            "uncertain" if possible_send else "denied",
            len(request.input_bytes) if possible_send else 0,
            0,
        )
    finally:
        if child is not None:
            try:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=0.5)
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    cleanup_error = True
            except (OSError, subprocess.SubprocessError):
                cleanup_error = True
        for stream in (parent, channel):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    cleanup_error = True
        with _lock:
            if cleanup_error:
                _cleanup_failed = True
            if admitted:
                _active -= 1
                _pools[key] -= 1
                if not _pools[key]:
                    del _pools[key]
    # Finalization never overrides an unexpected exception. Expected failures keep
    # their safe outcome; incomplete cleanup fences future work and cannot erase
    # evidence that permission to transmit may already have reached the child.
    if cleanup_error:
        exposed = possible_send and not proven_pre_send
        return FixtureResult(
            "uncertain" if exposed else "denied",
            len(request.input_bytes) if exposed else 0,
            result.response_bytes if exposed else 0,
        )
    return result
