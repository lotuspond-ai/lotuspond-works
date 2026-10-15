"""Fixed synthetic connector child, reachable only through internal test integration.

No plugin selection, CLI registration, control credential or session proof is accepted.
"""

from __future__ import annotations

import base64
import socket
import sys
from pathlib import Path

from .destination import DestinationTransport, EgressPolicy
from .invocation import (
    InvocationRequest,
    ResolvedSecretBinding,
    _receive,
    _send,
    _validate,
)
from .protocol import ConnectionConfig, RuntimeIdentity
from .resolver import MountedSecretResolver


def _run(channel):
    deadline = 0
    sent = False
    request_bytes = 0
    try:
        envelope = _receive(channel, __import__("time").monotonic_ns() + 30_000_000_000)
        deadline = envelope["deadline"]
        request = _validate(
            InvocationRequest(
                RuntimeIdentity.model_validate(envelope["identity"]),
                envelope["command_sha256"],
                ConnectionConfig.model_validate(envelope["configuration"]),
                envelope["operation"],
                envelope["path"],
                base64.b64decode(envelope["input"], validate=True),
                ResolvedSecretBinding(**envelope["binding"]),
            )
        )
        policy = EgressPolicy(
            tuple(envelope["policy"]["allowed_private_cidrs"]),
            tuple(envelope["policy"]["denied_cidrs"]),
            tuple(envelope["policy"]["denied_hosts"]),
        )
        constructor = (
            DestinationTransport._for_test if envelope["test_loopback"] else DestinationTransport
        )
        destination = constructor(policy, Path(envelope["ca"]))
        destination._worker_process = True
        resolver = MountedSecretResolver(Path(envelope["root"]), Path(envelope["mapping"]))
        with destination.connect(
            request.configuration.destinations[0], request.path, deadline
        ) as connected:
            # Observe actual OS open attempts only while resolving; never emit names or values.
            observing = True

            def audit(event, args):
                if observing and event == "open":
                    _send(channel, {"event": "secret_open"}, deadline)

            sys.addaudithook(audit)
            binding = request.secret_binding
            secret = resolver.resolve(binding.binding_id, binding.version)
            observing = False
            # No opaque child payload can grant authority: only parent can answer this exchange.
            _send(channel, {"event": "authorize"}, deadline)
            if _receive(channel, deadline) != {"authorized": True}:
                raise ValueError
            # Secret values are header-safe synthetic fixtures, never arbitrary header construction.
            value = secret.decode("ascii")
            if any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError
            _send(channel, {"event": "sending"}, deadline)
            sent = True
            request_bytes = len(request.input_bytes)
            response = connected.send(
                "POST",
                request.input_bytes,
                {"X-Lotuspond-Fixture-Secret": value},
                request.configuration.data_handling.max_response_bytes,
            )
        _send(
            channel,
            {"result": "observed", "request_bytes": request_bytes, "response_bytes": len(response)},
            deadline,
        )
    except Exception:
        if deadline:
            try:
                _send(
                    channel,
                    {
                        "result": "uncertain" if sent else "denied",
                        "request_bytes": request_bytes,
                        "response_bytes": 0,
                    },
                    deadline,
                )
            except Exception:
                pass


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        return 1
    with socket.socket(fileno=int(sys.argv[1])) as channel:
        _run(channel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
