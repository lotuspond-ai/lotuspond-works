"""Typed activity calls over the verified direct control transport (E04).

Uses the runtime's credential and current session, never evaluation keys. Every
response is bounded and checked against what was sent before it is returned; none
of it is authority. The worker still verifies signatures and pinned records before
any provider call.
"""

from __future__ import annotations

import hashlib
import json

import rfc8785
from lotuspond_sdk.activity import (
    ActivityDelivery,
    ArtifactHandle,
    canonical_activity_json,
    decode_activity_delivery,
    validate_activity,
)
from lotuspond_sdk.activity.canonical import DELIVERY_LIMITS
from lotuspond_sdk.executable.input import JsonLimits, decode_executable_bytes, project_json
from pydantic import SecretStr, TypeAdapter

from .activity_protocol import (
    MAX_VALUE_BYTES,
    ActivityClaim,
    ActivityLease,
    ActivityResult,
    ChildActivityRequest,
    ChildActivityView,
)
from .canonical import decode_fleet_json
from .control_transport import ControlTransport
from .errors import FleetError
from .protocol import CanonicalUUID, validate_token

VALUE_LIMITS = JsonLimits(MAX_VALUE_BYTES, 32, 10000)


def _body(model) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"), separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


def canonical_value(value) -> bytes:
    """Canonical JSON content of a value the worker stores (RFC 8785)."""
    return rfc8785.dumps(value)


class ActivityClient:
    def __init__(self, transport: ControlTransport, token: SecretStr, session: SecretStr):
        validate_token(token, "lpr_")
        validate_token(session, "lps_")
        self.transport, self._token, self._session = transport, token, session

    def _call(self, route, body: bytes) -> bytes:
        return self.transport.request("POST", route, body, self._token, self._session)

    def poll(self) -> ActivityDelivery | None:
        content = self._call("/activities/poll", b"{}")
        if content == b"null":
            return None
        try:
            envelope, _ = decode_activity_delivery(content)
            delivery = ActivityDelivery.model_validate(
                project_json(decode_executable_bytes(content), limits=DELIVERY_LIMITS)
            )
        except Exception:
            raise FleetError("command_rejected") from None
        # Only exact canonical envelope bytes can later match their signature.
        if canonical_activity_json(envelope) != delivery.envelope.encode("utf-8"):
            raise FleetError("command_rejected")
        return delivery

    def claim(self, delivery: ActivityDelivery) -> ActivityLease:
        delivery = ActivityDelivery.model_validate(delivery)
        try:
            envelope = validate_activity(decode_executable_bytes(delivery.envelope.encode("utf-8")))
        except Exception:
            raise FleetError("command_rejected") from None
        canonical = canonical_activity_json(envelope)
        request = ActivityClaim(
            delivery_id=envelope.delivery_id,
            delivery_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        lease = ActivityLease.model_validate_json(self._call("/activities/claim", _body(request)))
        if (
            lease.attempt_id != envelope.attempt_id
            or lease.generation != envelope.generation
            or lease.expires_at_ms > lease.deadline_ms
        ):
            raise FleetError("command_rejected")
        return lease

    def renew(self, lease: ActivityLease) -> ActivityLease:
        lease = ActivityLease.model_validate(lease)
        renewed = ActivityLease.model_validate_json(self._call("/activities/renew", _body(lease)))
        if (
            renewed.attempt_id != lease.attempt_id
            or renewed.generation != lease.generation
            or renewed.deadline_ms != lease.deadline_ms
            or renewed.lease_revision < lease.lease_revision
            or renewed.expires_at_ms < lease.expires_at_ms
            or renewed.expires_at_ms > renewed.deadline_ms
        ):
            raise FleetError("command_rejected")
        return renewed

    def store(self, attempt_id: str, content: bytes) -> ArtifactHandle:
        """Store one canonical JSON value for a held attempt; returns its handle."""
        try:
            attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
            if type(content) is not bytes or not 0 < len(content) <= MAX_VALUE_BYTES:
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
            decode_fleet_json(self._call(f"/activities/{attempt_id}/values", content))
        )
        if handle.sha256 != hashlib.sha256(content).hexdigest():
            raise FleetError("command_rejected")
        return handle

    def request_child(self, request: ChildActivityRequest) -> str:
        request = ChildActivityRequest.model_validate(request)
        view = ChildActivityView.model_validate_json(
            self._call("/activities/children", _body(request))
        )
        return view.attempt_id

    def result(self, result: ActivityResult) -> None:
        result = ActivityResult.model_validate(result)
        if decode_fleet_json(self._call("/activities/results", _body(result))) != {}:
            raise FleetError("command_rejected")

    def close(self) -> None:
        self.transport.close()
