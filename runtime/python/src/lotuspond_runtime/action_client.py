"""Typed action calls over the verified direct control transport (E05).

Uses the runtime's credential and current session, like `ActivityClient`. Every
answer is bounded and checked against what was sent before it is returned; none of
it is authority. The worker still verifies the signed command against its pinned
records before any write.
"""

from __future__ import annotations

import hashlib
import json

from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.input import JsonLimits, decode_executable_bytes, project_json
from pydantic import SecretStr, TypeAdapter

from .action_protocol import (
    MAX_RESPONSE_BYTES,
    ActionClaim,
    ActionLease,
    ActionReport,
    ActionStop,
    decode_records,
)
from .activity_client import canonical_value
from .canonical import decode_fleet_json
from .commands import decode_delivery
from .control_transport import ControlTransport
from .errors import FleetError
from .protocol import CanonicalUUID, CommandDelivery, PinnedCommandRecords, validate_token

VALUE_LIMITS = JsonLimits(MAX_RESPONSE_BYTES, 32, 10000)
RECORD_LIMITS = JsonLimits(1048576, 32, 100000)


def _body(model) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"), separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


def claim_of(delivery: CommandDelivery) -> ActionClaim:
    """The claim naming a delivery's command by its exact canonical digest."""
    from lotuspond_sdk.executable.wire import canonical_wire_json, validate_runtime_command

    if type(delivery) is not CommandDelivery:
        raise FleetError("command_rejected")
    try:
        command = validate_runtime_command(decode_fleet_json(delivery.command_bytes))
        if canonical_wire_json(command) != delivery.command_bytes:
            raise ValueError
        return ActionClaim(
            command_id=command.command_id,
            command_sha256=hashlib.sha256(delivery.command_bytes).hexdigest(),
        )
    except Exception:
        raise FleetError("command_rejected") from None


class ActionClient:
    def __init__(
        self, transport: ControlTransport, token: SecretStr, session: SecretStr, *, live=None
    ):
        """`live`, when given, returns the supervisor's current (token, session) or None."""
        validate_token(token, "lpr_")
        validate_token(session, "lps_")
        if live is not None and not callable(live):
            raise ValueError("live must be callable")
        self.transport, self._token, self._session, self._live = transport, token, session, live

    def _pair(self):
        current = self._live() if self._live is not None else None
        return current if current is not None else (self._token, self._session)

    def _call(self, route, body: bytes) -> bytes:
        token, session = self._pair()
        try:
            return self.transport.request("POST", route, body, token, session)
        except FleetError as error:
            # An unauthenticated request was never applied: one retry with a newer pair.
            retried = self._pair()
            if error.code != "unauthenticated" or retried == (token, session):
                raise
            return self.transport.request("POST", route, body, *retried)

    def _empty(self, route, body):
        if decode_fleet_json(self._call(route, body)) != {}:
            raise FleetError("command_rejected")

    def poll(self) -> CommandDelivery | None:
        """The next admitted action command for this session, or None."""
        content = self._call("/actions/poll", b"{}")
        try:
            return decode_delivery(content)
        except FleetError as error:
            if error.code == "input_limit":
                raise FleetError("command_rejected") from None
            raise
        except Exception:
            raise FleetError("command_rejected") from None

    def records(self, delivery: CommandDelivery) -> PinnedCommandRecords:
        """The pinned records of a command this session holds the live lease for."""
        content = self._call("/actions/records", _body(claim_of(delivery)))
        try:
            raw = project_json(decode_executable_bytes(content), limits=RECORD_LIMITS)
        except Exception:
            raise FleetError("transport_failed") from None
        return decode_records(raw)

    def claim(self, delivery: CommandDelivery) -> ActionLease:
        request = claim_of(delivery)
        lease = ActionLease.model_validate_json(self._call("/actions/claim", _body(request)))
        if lease.command_id != request.command_id or lease.expires_at_ms > lease.deadline_ms:
            raise FleetError("command_rejected")
        return lease

    def renew(self, lease: ActionLease) -> ActionLease:
        lease = ActionLease.model_validate(lease)
        renewed = ActionLease.model_validate_json(self._call("/actions/renew", _body(lease)))
        if (
            (renewed.attempt_id, renewed.command_id, renewed.ordinal)
            != (lease.attempt_id, lease.command_id, lease.ordinal)
            or renewed.deadline_ms != lease.deadline_ms
            or renewed.lease_revision < lease.lease_revision
            or renewed.expires_at_ms < lease.expires_at_ms
            or renewed.expires_at_ms > renewed.deadline_ms
        ):
            raise FleetError("command_rejected")
        return renewed

    def store(self, attempt_id: str, content: bytes) -> ArtifactHandle:
        """Store one canonical JSON response value for a held action attempt."""
        try:
            attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
            if type(content) is not bytes or not 0 < len(content) <= MAX_RESPONSE_BYTES:
                raise FleetError("input_limit")
            if (
                canonical_value(project_json(decode_executable_bytes(content), limits=VALUE_LIMITS))
                != content
            ):
                raise ValueError
        except FleetError:
            raise
        except Exception:
            raise FleetError("invalid_request") from None
        handle = ArtifactHandle.model_validate(
            decode_fleet_json(self._call(f"/actions/{attempt_id}/values", content))
        )
        if handle.sha256 != hashlib.sha256(content).hexdigest():
            raise FleetError("command_rejected")
        return handle

    def receipt(self, report: ActionReport) -> None:
        self._empty("/actions/receipts", _body(ActionReport.model_validate(report)))

    def stop(self, stop: ActionStop) -> None:
        self._empty("/actions/stops", _body(ActionStop.model_validate(stop)))

    def close(self) -> None:
        self.transport.close()
