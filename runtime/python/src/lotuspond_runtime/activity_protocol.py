"""Signed read/model activity records between runtimes and the control plane (E04).

Pure values: no network, files, environment or provider loading. No record grants
authority. The API reloads persisted records and current E03 identity on every
claim, renewal, child request and result; a runtime verifies the exact signed bytes
and its pinned records before any provider call. Runtime code imports shared SDK
types and never backend types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from lotuspond_sdk.activity import ActivityEnvelope, ArtifactHandle, UsageObservation
from lotuspond_sdk.contract import Identifier
from lotuspond_sdk.executable.package import ArtifactRef
from lotuspond_sdk.trust import TrustSet
from pydantic import Field

from .protocol import CanonicalUUID, ConnectionConfig, Digest, FleetModel, OpaqueId, SafeInt, Scope

# Fixed outcome codes. Only the first is success; the three retryable codes are the
# declared retry classes. Nothing else, including provider text, crosses the wire.
ResultCode = Literal[
    "succeeded",
    "unavailable",
    "rate_limited",
    "timeout_before_dispatch",
    "provider_failed",
    "invalid_output",
    "cancelled",
]
# Derived from the server's stored claim and renewal times; increases on every renewal.
LeaseRevision = Annotated[SafeInt, Field(ge=1)]
MAX_LEASE_MS = 60000
MAX_DELIVERY_MS = 300000
# Values a runtime stores travel in one control request, so they are bounded by the
# control transport's 64 KiB body (stricter than the 1 MiB artifact bound).
MAX_VALUE_BYTES = 65536
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")


@dataclass(frozen=True)
class PinnedActivityRecords:
    """Authoritative reconstruction for one attempt; never built from a request body.

    `input_schema` and `output_schema` are `{"schema": ..., "definitions": [...]}`
    in the SDK's data-schema JSON. A connector read's input is `{"target", "input"}`
    and its input schema is `{"target": ..., "input": ..., "definitions": [...]}`.
    `request_sha256` is the SHA-256 of the exact canonical input content.
    """

    envelope: ActivityEnvelope
    connection: ConnectionConfig | None
    dependency: ArtifactRef
    deadline_ms: int
    input_schema: object
    output_schema: object
    request_sha256: str


class ActivityLease(FleetModel):
    attempt_id: CanonicalUUID
    generation: Annotated[SafeInt, Field(ge=1)]
    lease_revision: LeaseRevision
    expires_at_ms: SafeInt
    deadline_ms: SafeInt


class ActivityClaim(FleetModel):
    delivery_id: CanonicalUUID
    delivery_sha256: Digest


class ActivityResult(FleetModel):
    attempt_id: CanonicalUUID
    lease_revision: LeaseRevision
    result: ArtifactHandle | None
    code: ResultCode
    usage: UsageObservation


class ChildActivityRequest(FleetModel):
    request_id: OpaqueId
    parent_attempt_id: CanonicalUUID
    kind: Literal["connector_read", "model"]
    declaration_name: Identifier
    arguments: ArtifactHandle


class ChildActivityView(FleetModel):
    attempt_id: CanonicalUUID


class HistoricalObservation(FleetModel):
    """Late evidence for reconciliation; not a lease, permission or overwrite."""

    observation_id: OpaqueId
    attempt_id: CanonicalUUID
    delivery_sha256: Digest
    result: ArtifactHandle | None
    code: ResultCode
    usage: UsageObservation


class ActivityTrustAuthority(Protocol):
    """Separately provisioned activity trust; never publisher or action trust."""

    def current(self, scope: Scope, now_ms: int) -> TrustSet: ...


class UnavailableActivityTrustAuthority:
    def current(self, scope: Scope, now_ms: int) -> TrustSet:
        from .errors import FleetError

        raise FleetError("trust_unavailable")


def tool_name(connector_name: str, operation: str) -> str | None:
    """The one name of a declared read tool, or None when no valid name exists.

    Both sides derive it from the pinned catalog name and operation; a model's
    proposed name only selects among declared tools and is never resolved further.
    """
    name = f"{connector_name}-{operation}"
    return name if len(name) <= 64 and _TOOL_NAME.fullmatch(name) else None


def validate_lease(raw: object, *, now_ms: int) -> ActivityLease:
    """Structural and time bounds only; the server remains the lease authority."""
    from pydantic import TypeAdapter

    from .errors import FleetError

    lease = ActivityLease.model_validate(raw)
    now = TypeAdapter(SafeInt).validate_python(now_ms, strict=True)
    if (
        lease.expires_at_ms <= now
        or lease.expires_at_ms - now > MAX_LEASE_MS
        or lease.expires_at_ms > lease.deadline_ms
    ):
        raise FleetError("unauthenticated")
    return lease


__all__ = [
    "ActivityClaim",
    "ActivityLease",
    "ActivityResult",
    "ActivityTrustAuthority",
    "ChildActivityRequest",
    "ChildActivityView",
    "HistoricalObservation",
    "MAX_DELIVERY_MS",
    "MAX_LEASE_MS",
    "MAX_VALUE_BYTES",
    "PinnedActivityRecords",
    "ResultCode",
    "UnavailableActivityTrustAuthority",
    "tool_name",
    "validate_lease",
]
