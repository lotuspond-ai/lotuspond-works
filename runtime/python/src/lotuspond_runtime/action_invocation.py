"""Write providers in supervised children (E05).

A write provider is selected by the exact installed connector pin the command's
connection names, and by an operation it declares as a write; nothing is discovered,
loaded by path or chosen by a command. Each descriptor also records whether the
operation declares `target_key` idempotency, which decides whether the intent's
`action_id` is sent as the idempotency key. Synthetic providers are test-only.

Each write runs in a fresh provider child under E04's supervision
(`call_provider`): its own process group, an empty environment, a private socket, a
deadline, and TERM/KILL cleanup that fences the worker when it cannot be confirmed.
This module is also that child's fixed entry point
(`python -I -m lotuspond_runtime.action_invocation <fd> <worker pid>`). Secrets are
resolved by the worker immediately before the call and travel only over the child's
socket; they are never logged, journaled or stored.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

import rfc8785

from .action_protocol import WriteResult, encode_records
from .activity_invocation import call_provider
from .errors import FleetError
from .protocol import ConnectorPin, SecretPin
from .providers import ActivityFailure

IDEMPOTENCY = ("none", "target_key")
CHILD = "lotuspond_runtime.action_invocation"


class WriteRefused(FleetError):
    """This runtime cannot serve the write: no provider for the exact pin and operation."""

    def __init__(self):
        super().__init__("command_rejected")


@dataclass(frozen=True)
class WriteDescriptor:
    """Immutable selection record for one write provider.

    `operations` maps each write operation to its declared idempotency (`none` or
    `target_key`). `script` is a synthetic provider's behaviour data and `settings` a
    real provider's operator data, both canonical JSON text; never code.
    """

    provider_id: str
    connector: ConnectorPin
    operations: object
    test_only: bool = False
    script: object = field(default=None, compare=False, hash=False)
    settings: object = field(default=None, compare=False, hash=False)
    credentials: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "connector", ConnectorPin.model_validate(self.connector))
        raw = dict(self.operations)
        if not 1 <= len(raw) <= 64 or any(
            type(name) is not str or value not in IDEMPOTENCY for name, value in raw.items()
        ):
            raise ValueError("Invalid write operations")
        object.__setattr__(self, "operations", tuple(sorted(raw.items())))
        if type(self.credentials) is not tuple or len(self.credentials) > 16:
            raise ValueError("Invalid credentials")
        object.__setattr__(
            self, "credentials", tuple(SecretPin.model_validate(pin) for pin in self.credentials)
        )
        if self.script is not None:
            if not self.test_only:
                raise ValueError("Only a synthetic provider carries a script")
            object.__setattr__(self, "script", rfc8785.dumps(self.script).decode("utf-8"))
        if self.settings is not None:
            if self.test_only:
                raise ValueError("A synthetic provider carries a script, not settings")
            object.__setattr__(self, "settings", rfc8785.dumps(self.settings).decode("utf-8"))

    def idempotency(self, operation) -> str | None:
        return dict(self.operations).get(operation)

    def script_data(self):
        import json

        return None if self.script is None else json.loads(self.script)

    def settings_data(self):
        import json

        return None if self.settings is None else json.loads(self.settings)

    def to_json(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "connector": self.connector.model_dump(mode="json"),
            "operations": dict(self.operations),
            "test_only": self.test_only,
            "script": self.script_data(),
            "settings": self.settings_data(),
        }

    @classmethod
    def from_json(cls, raw: dict) -> WriteDescriptor:
        return cls(
            provider_id=raw["provider_id"],
            connector=ConnectorPin.model_validate(raw["connector"]),
            operations=raw["operations"],
            test_only=raw["test_only"] is True,
            script=raw["script"],
            settings=raw["settings"],
        )


class WriteProviderSet:
    """The constructor-selected write providers of one worker, keyed by exact pins."""

    def __init__(self, descriptors=(), *, test_mode=False):
        found = {}
        for descriptor in descriptors:
            if type(descriptor) is not WriteDescriptor:
                raise ValueError("Write providers are selected by descriptor")
            if descriptor.test_only and test_mode is not True:
                raise ValueError("Synthetic providers require test mode")
            key = descriptor.connector
            if key in found:
                raise ValueError("One write provider per connector pin")
            found[key] = descriptor
        self._found, self._test_mode = found, test_mode is True

    @property
    def test_mode(self) -> bool:
        return self._test_mode

    def select(self, connector, operation) -> WriteDescriptor:
        """The descriptor for exactly this installed pin and write operation."""
        descriptor = self._found.get(ConnectorPin.model_validate(connector))
        if descriptor is None or descriptor.idempotency(operation) is None:
            raise WriteRefused
        return descriptor


def invoke_write(
    descriptor: WriteDescriptor,
    records,
    idempotency_key: str | None,
    secrets: list[str],
    *,
    test_mode: bool,
    cancel,
    deadline_ns: int,
    deadline_ms: int,
    fence=None,
    spawned=None,
    counter_id=None,
):
    """One write in a fresh supervised child; never raises for provider failures."""
    request = {
        "descriptor": descriptor.to_json(),
        "test_mode": test_mode is True,
        "records": encode_records(records),
        "idempotency_key": idempotency_key,
        "deadline_ms": deadline_ms,
        "secrets": secrets,
    }
    if test_mode is True:
        request["counter_id"] = counter_id  # synthetic call counters only
    return call_provider(
        request,
        deadline_ns=deadline_ns,
        cancel=cancel,
        fence=fence,
        spawned=spawned,
        module=CHILD,
    )


# ------------------------------------------------------------- the provider child


def _provider(descriptor: WriteDescriptor, secrets, test_mode):
    if test_mode is True and descriptor.test_only:
        from . import action_fixture

        implementation = action_fixture.WRITE_PROVIDERS.get(descriptor.provider_id)
        return None if implementation is None else implementation(descriptor, secrets)
    if descriptor.test_only:
        return None
    # Real write providers, by fixed identifier only; any other is unavailable, never
    # a fallback to anything else.
    if descriptor.provider_id == "https-json":
        from .https_json import HttpsJsonWriteProvider

        return HttpsJsonWriteProvider(descriptor, secrets, test_mode=test_mode is True)
    return None


def _answer(request):
    from .action_protocol import decode_records
    from .activity_child import _failure

    descriptor = WriteDescriptor.from_json(request["descriptor"])
    secrets = tuple(bytes.fromhex(value) for value in request["secrets"])
    provider = _provider(descriptor, secrets, request.get("test_mode"))
    script = {}
    if request.get("test_mode") is True:
        from . import action_fixture

        action_fixture.COUNTER_ID = request.get("counter_id")
        script = descriptor.script_data() or {} if descriptor.test_only else {}
    if provider is None:
        _note(script, "no_provider")
        return {"failure": "unavailable", "not_reached": True}
    try:
        result = provider.invoke(
            decode_records(request["records"]),
            request["idempotency_key"],
            request["deadline_ms"],
        )
        if type(result) is not WriteResult:
            raise TypeError
        return {
            "ok": {
                "status": result.status,
                "response": result.response,
                "usage": result.usage.model_dump(mode="json"),
            }
        }
    except ActivityFailure as failure:
        _note(script, "refused_" + failure.code)
        return _failure(failure)


def _note(script, code):
    """Test mode: a synthetic provider's refusal code beside its call counter (no
    values), so a test can tell why a write never reached the target."""
    if script.get("counter") is None:
        return
    from . import action_fixture

    action_fixture._failed(script, code, False)


def main(argv):
    import socket

    from .activity_child import MAX_CALL_MS, _die_with_parent
    from .activity_framing import receive, send

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
