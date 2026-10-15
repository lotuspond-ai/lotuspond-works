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
