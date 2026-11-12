"""Action records between runtimes and the control plane (E05).

Pure values: no network, files, environment, threads or provider loading. No record
grants authority. The control plane rechecks current authority on every poll, claim,
renewal, value and report; a runtime verifies the exact signed command against the
pinned records (E03's `validate_delivery`) before any write is invoked. Runtime code
shares these shapes with the API through this module and imports no backend type.

The pinned records travel as one JSON object (`encode_records`/`decode_records`):
the SDK wire records verbatim, the connection configuration, the operator's secret
binding identifiers and the exact canonical target and input text, whose digests
the binding pins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from lotuspond_sdk.activity import ArtifactHandle, UsageObservation
from lotuspond_sdk.executable.actions import (
    validate_action_intent,
    validate_binding_context,
    validate_human_decision,
)
from lotuspond_sdk.executable.wire import ActionReceipt
from pydantic import Field

from .errors import FleetError
from .protocol import (
    CanonicalUUID,
    ConnectionConfig,
    Digest,
    FleetModel,
    PinnedCommandRecords,
    ResolvedSecretBinding,
    SafeInt,
)

ACTION_LEASE_MS = 60000
ACTION_RENEW_MS = 20000
COMMAND_LIFETIME_MS = 300000
# A response value travels in one control request (the transport's 64 KiB body).
MAX_RESPONSE_BYTES = 65536
_RECORD_FIELDS = {
    "intent",
    "decision",
    "context",
    "connection_id",
    "connection_version",
    "configuration",
    "secret_bindings",
    "actual_input",
    "actual_target",
}

LeaseRevision = Annotated[SafeInt, Field(ge=1)]
StopCode = Literal["not_started", "stopped_unknown"]
WriteStatus = Literal["accepted", "observed_effect", "unknown"]


class ActionClaim(FleetModel):
    """Names one admitted command by its exact canonical digest."""

    command_id: CanonicalUUID
    command_sha256: Digest


class ActionLease(FleetModel):
    attempt_id: CanonicalUUID
    command_id: CanonicalUUID
    # The dispatch ordinal; a receipt's `attempt` must equal it.
    ordinal: Annotated[SafeInt, Field(ge=1)]
    lease_revision: LeaseRevision
    expires_at_ms: SafeInt
    deadline_ms: SafeInt


class ActionReport(FleetModel):
    attempt_id: CanonicalUUID
    lease_revision: LeaseRevision
    receipt: ActionReceipt
    response: ArtifactHandle | None
    usage: UsageObservation


class ActionStop(FleetModel):
    """`not_started`: nothing reached the target. `stopped_unknown`: it may have."""

    attempt_id: CanonicalUUID
    lease_revision: LeaseRevision
    code: StopCode
    usage: UsageObservation


@dataclass(frozen=True)
class WriteResult:
    """A write provider's outcome. `unknown` means the request may have been applied."""

    status: WriteStatus
    response: object | None
    usage: UsageObservation

    def __post_init__(self):
        if self.status not in WriteStatus.__args__ or type(self.usage) is not UsageObservation:
            raise ValueError("Invalid write result")


class WriteProvider(Protocol):
    """Runs one write in the provider child; never retries on its own.

    `idempotency_key` is the intent's `action_id` when the operation declares
    `target_key`, otherwise None.
    """

    def invoke(
        self, records: PinnedCommandRecords, idempotency_key: str | None, deadline_ms: int
    ) -> WriteResult: ...


def validate_action_lease(raw: object, *, now_ms: int) -> ActionLease:
    """Structural and time bounds only; the control plane remains the lease authority."""
    lease = ActionLease.model_validate(raw)
    if (
        type(now_ms) is not int
        or lease.expires_at_ms <= now_ms
        or lease.expires_at_ms - now_ms > ACTION_LEASE_MS
        or lease.expires_at_ms > lease.deadline_ms
    ):
        raise FleetError("unauthenticated")
    return lease


def encode_records(records: PinnedCommandRecords) -> dict:
    """The pinned records as one JSON object (the records route's answer)."""
    if type(records) is not PinnedCommandRecords:
        raise FleetError("invalid_request")
    return {
        "intent": json.loads(records.intent.model_dump_json()),
        "decision": None
        if records.decision is None
        else json.loads(records.decision.model_dump_json()),
        "context": records.context.model_dump(mode="json"),
        "connection_id": records.connection_id,
        "connection_version": records.connection_version,
        "configuration": records.configuration.model_dump(mode="json"),
        "secret_bindings": [
            {"reference_id": b.reference_id, "version": b.version, "binding_id": b.binding_id}
            for b in records.secret_bindings
        ],
        "actual_input": records.actual_input.decode("utf-8"),
        "actual_target": records.actual_target.decode("utf-8"),
    }


def decode_records(raw: object) -> PinnedCommandRecords:
    """Exactly the encoded fields; E03's `validate_records` still checks them in full."""
    from .commands import validate_records

    try:
        if type(raw) is not dict or set(raw) != _RECORD_FIELDS:
            raise ValueError
        bindings = raw["secret_bindings"]
        if type(bindings) is not list or len(bindings) > 16:
            raise ValueError
        resolved = []
        for item in bindings:
            if type(item) is not dict or set(item) != {"reference_id", "version", "binding_id"}:
                raise ValueError
            if type(item["version"]) is not int or type(item["reference_id"]) is not str:
                raise ValueError
            resolved.append(
                ResolvedSecretBinding(item["reference_id"], item["version"], item["binding_id"])
            )
        if any(type(raw[key]) is not str for key in ("actual_input", "actual_target")):
            raise ValueError
        version = raw["connection_version"]
        if type(version) is not int or type(raw["connection_id"]) is not str:
            raise ValueError
        records = PinnedCommandRecords(
            intent=validate_action_intent(raw["intent"]),
            decision=None if raw["decision"] is None else validate_human_decision(raw["decision"]),
            context=validate_binding_context(raw["context"]),
            connection_id=raw["connection_id"],
            connection_version=version,
            configuration=ConnectionConfig.model_validate(raw["configuration"]),
            secret_bindings=tuple(resolved),
            actual_input=raw["actual_input"].encode("utf-8"),
            actual_target=raw["actual_target"].encode("utf-8"),
        )
    except FleetError:
        raise
    except Exception:
        raise FleetError("command_rejected") from None
    return validate_records(records)


__all__ = [
    "ACTION_LEASE_MS",
    "ACTION_RENEW_MS",
    "COMMAND_LIFETIME_MS",
    "MAX_RESPONSE_BYTES",
    "ActionClaim",
    "ActionLease",
    "ActionReport",
    "ActionStop",
    "WriteProvider",
    "WriteResult",
    "decode_records",
    "encode_records",
    "validate_action_lease",
]
