"""Authorized context for agent activities, reread under current access every time.

A context value is fetched from the control plane for one attempt and kept only in
worker memory for that activity. Nothing is cached across activities: a source whose
access was revoked or changed cannot be reread because an earlier activity saw it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import FleetError
from .providers import ActivityFailure


@dataclass(frozen=True)
class ContextRef:
    source_id: str
    version: int


class ContextClient(Protocol):
    def read(self, attempt_id: str, ref: ContextRef) -> object: ...


class UnavailableContextClient:
    def read(self, attempt_id: str, ref: ContextRef) -> object:
        raise FleetError("authority_unavailable")


class ContextReader:
    """Reads each declared reference for one attempt; any refusal ends the activity."""

    def __init__(self, client, attempt_id: str):
        self.client, self.attempt_id = client or UnavailableContextClient(), attempt_id

    def read_all(self, refs) -> tuple:
        values = []
        for ref in refs:
            if type(ref) is not ContextRef:
                raise ActivityFailure("provider_failed")
            try:
                values.append(self.client.read(self.attempt_id, ref))
            except FleetError as error:
                # Current access decides: a revoked or changed source is not reread.
                raise ActivityFailure(
                    "unavailable" if error.code == "authority_unavailable" else "provider_failed"
                ) from None
        return tuple(values)
