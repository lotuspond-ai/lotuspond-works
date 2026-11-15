"""Independent command trust; no publisher trust, discovery or cached allowance."""

from typing import Protocol

from lotuspond_sdk.trust import TrustSet
from pydantic import TypeAdapter

from .errors import FleetError
from .protocol import CommandTrustSnapshot, SafeInt


class CommandTrustAuthority(Protocol):
    def current(self, installation_id: str) -> CommandTrustSnapshot: ...


class UnavailableCommandTrustAuthority:
    def current(self, installation_id: str) -> CommandTrustSnapshot:
        raise FleetError("trust_unavailable")


def validate_snapshot(raw: CommandTrustSnapshot, *, now_ms: int) -> CommandTrustSnapshot:
    try:
        if type(raw) is not CommandTrustSnapshot:
            raise ValueError
        now = TypeAdapter(SafeInt).validate_python(now_ms, strict=True)
        revision = TypeAdapter(SafeInt).validate_python(raw.revision, strict=True)
        until = TypeAdapter(SafeInt).validate_python(raw.valid_until_ms, strict=True)
        if revision < 1 or not now < until <= now + 60000:
            raise ValueError
        trust = TrustSet.model_validate(raw.trust)
        return CommandTrustSnapshot(revision, trust, until)
    except Exception:
        raise FleetError("trust_unavailable") from None


class OperatorCommandTrust:
    """An operator's command trust: `{"revision", "trust"}` in a private file the operator
    installs from the control plane's authority tool (`authority command-trust`), reread on
    every call so a rotation or revocation takes effect on the next command.

    Each snapshot is valid for 50 s on the worker's server-anchored clock, inside the 60 s
    E03 allows, because a caller validates it against a `now` read just before asking.
    """

    def __init__(self, path, clock):
        from pathlib import Path

        self.path, self.clock = Path(path), clock

    def current(self, installation_id: str) -> CommandTrustSnapshot:
        import json

        from .state import read_private_file

        # Unsafe or oversized storage is reported as itself (the worker stops on it);
        # anything wrong with the content is unavailable trust.
        raw = read_private_file(self.path, 65536)
        try:
            state = json.loads(raw)
            if type(state) is not dict or set(state) != {"revision", "trust"}:
                raise ValueError
            revision = TypeAdapter(SafeInt).validate_python(state["revision"], strict=True)
            trust = TrustSet.model_validate(state["trust"])
            return CommandTrustSnapshot(revision, trust, self.clock.now_ms() + 50000)
        except Exception:
            raise FleetError("trust_unavailable") from None
