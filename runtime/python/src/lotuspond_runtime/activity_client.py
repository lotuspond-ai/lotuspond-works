"""Typed activity calls over the verified direct control transport (E04).

Uses the runtime's credential and current session, never evaluation keys. Every
response is bounded and checked against what was sent before it is returned; none
of it is authority. The worker still verifies signatures and pinned records before
any provider call.
"""

from __future__ import annotations

import hashlib
import json
import re

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
from lotuspond_sdk.executable.package import ArtifactRef
from pydantic import SecretStr, TypeAdapter

from .activity_protocol import (
    MAX_VALUE_BYTES,
    ActivityClaim,
    ActivityLease,
    ActivityResult,
    ChildActivityRequest,
    ChildActivityView,
    PinnedActivityRecords,
)
from .canonical import decode_fleet_json
from .context import ContextRef
from .control_transport import ControlTransport
from .errors import FleetError
from .protocol import CanonicalUUID, ConnectionConfig, validate_token

VALUE_LIMITS = JsonLimits(MAX_VALUE_BYTES, 32, 10000)
# A held attempt's records, input and context, as served (Task 12's read routes).
READ_LIMITS = JsonLimits(1048576, 32, 100000)
_RECORD_FIELDS = {
    "envelope",
    "connection",
    "dependency",
    "deadline_ms",
    "input_schema",
    "output_schema",
    "request_sha256",
}
_AGENT_FIELDS = {
    "model",
    "model_name",
    "instructions",
    "tools",
    "context",
    "max_steps",
    "max_input_tokens",
    "max_output_tokens",
    "max_cost_microusd",
}
_SAFE = 2**53 - 1


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

    def poll(self, parent_attempt_id: str | None = None) -> ActivityDelivery | None:
        """The next delivery for this session; with a parent, only that parent's children."""
        body = b"{}"
        if parent_attempt_id is not None:
            try:
                parent = TypeAdapter(CanonicalUUID).validate_python(parent_attempt_id, strict=True)
            except Exception:
                raise FleetError("invalid_request") from None
            body = json.dumps({"parent_attempt_id": parent}, separators=(",", ":")).encode()
        content = self._call("/activities/poll", body)
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

    @staticmethod
    def _named(delivery) -> tuple:
        """The delivery's envelope and the claim body naming its exact digest."""
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
        return envelope, request

    def claim(self, delivery: ActivityDelivery) -> ActivityLease:
        envelope, request = self._named(delivery)
        lease = ActivityLease.model_validate_json(self._call("/activities/claim", _body(request)))
        if (
            lease.attempt_id != envelope.attempt_id
            or lease.generation != envelope.generation
            or lease.expires_at_ms > lease.deadline_ms
        ):
            raise FleetError("command_rejected")
        return lease

    def decline(self, delivery: ActivityDelivery) -> None:
        """Refuse a delivery this runtime cannot verify; it is never claimed.

        Names the delivery by its exact digest, so the control plane can match it to
        what it dispatched to this session. Nothing in the delivery is trusted.
        """
        _, request = self._named(delivery)
        if decode_fleet_json(self._call("/activities/decline", _body(request))) != {}:
            raise FleetError("command_rejected")

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

    @staticmethod
    def _claim_of(envelope) -> ActivityClaim:
        """Names one delivery by its exact canonical digest."""
        try:
            canonical = canonical_activity_json(envelope)
        except Exception:
            raise FleetError("command_rejected") from None
        return ActivityClaim(
            delivery_id=validate_activity(envelope).delivery_id,
            delivery_sha256=hashlib.sha256(canonical).hexdigest(),
        )

    def _read(self, route, body: bytes):
        """A bounded JSON answer; a malformed one is the control plane's, never declinable."""
        content = self._call(route, body)
        try:
            return project_json(decode_executable_bytes(content), limits=READ_LIMITS)
        except Exception:
            raise FleetError("transport_failed") from None

    def records(self, envelope) -> PinnedActivityRecords:
        """The control plane's pinned records for exactly this delivery, claimed or not."""
        raw = self._read("/activity-artifacts/records", _body(self._claim_of(envelope)))
        try:
            if type(raw) is not dict or set(raw) != _RECORD_FIELDS:
                raise ValueError
            text = raw["envelope"]
            returned = validate_activity(decode_executable_bytes(text.encode("utf-8")))
            if canonical_activity_json(returned) != text.encode("utf-8"):
                raise ValueError
            deadline = raw["deadline_ms"]
            if type(deadline) is not int or not 0 <= deadline <= _SAFE:
                raise ValueError
            digest = raw["request_sha256"]
            if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError
            if type(raw["input_schema"]) is not dict or type(raw["output_schema"]) is not dict:
                raise ValueError
            return PinnedActivityRecords(
                envelope=returned,
                connection=(
                    None
                    if raw["connection"] is None
                    else ConnectionConfig.model_validate(raw["connection"])
                ),
                dependency=ArtifactRef.model_validate(raw["dependency"]),
                deadline_ms=deadline,
                input_schema=raw["input_schema"],
                output_schema=raw["output_schema"],
                request_sha256=digest,
            )
        except Exception:
            raise FleetError("transport_failed") from None

    def agent(self, envelope):
        """The pinned declarations of a root agent attempt this session holds."""
        from .agent import MAX_STEPS, AgentRecords
        from .providers import ToolSpec

        raw = self._read("/activity-artifacts/agent", _body(self._claim_of(envelope)))
        try:
            if type(raw) is not dict or set(raw) != _AGENT_FIELDS:
                raise ValueError
            tools = tuple(
                ToolSpec(
                    name=item["name"],
                    dependency=ArtifactRef.model_validate(item["dependency"]),
                    operation=item["operation"],
                    input_schema=item["input_schema"],
                    output_schema=item["output_schema"],
                )
                for item in raw["tools"]
                if type(item) is dict
                and set(item)
                == {"name", "dependency", "operation", "input_schema", "output_schema"}
            )
            context = tuple(
                ContextRef(
                    TypeAdapter(CanonicalUUID).validate_python(item["source_id"], strict=True),
                    item["version"],
                )
                for item in raw["context"]
                if type(item) is dict
                and set(item) == {"source_id", "version"}
                and type(item["version"]) is int
                and 1 <= item["version"] <= 2147483647
            )
            if len(tools) != len(raw["tools"]) or len(context) != len(raw["context"]):
                raise ValueError
            steps = raw["max_steps"]
            if type(steps) is not int or steps < 1:
                raise ValueError
            return AgentRecords(
                model=ArtifactRef.model_validate(raw["model"]),
                model_name=raw["model_name"],
                instructions=raw["instructions"],
                tools=tools,
                context=context,
                # This runtime runs at most MAX_STEPS model rounds, whatever is declared.
                max_steps=min(steps, MAX_STEPS),
                max_input_tokens=raw["max_input_tokens"],
                max_output_tokens=raw["max_output_tokens"],
                max_cost_microusd=raw["max_cost_microusd"],
            )
        except Exception:
            raise FleetError("transport_failed") from None

    def input(self, attempt_id: str, handle) -> bytes:
        """The exact input of an attempt this session holds; its digest is checked."""
        try:
            attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
            handle = ArtifactHandle.model_validate(handle)
        except Exception:
            raise FleetError("invalid_request") from None
        content = self._call(f"/activity-artifacts/{attempt_id}/input", _body(handle))
        if hashlib.sha256(content).hexdigest() != handle.sha256:
            raise FleetError("transport_failed")
        return content

    def context(self, attempt_id: str, ref) -> object:
        """One pinned knowledge value of a held agent attempt, read under current access."""
        try:
            attempt_id = TypeAdapter(CanonicalUUID).validate_python(attempt_id, strict=True)
            if type(ref) is not ContextRef:
                raise ValueError
            body = json.dumps(
                {"source_id": ref.source_id, "version": ref.version}, separators=(",", ":")
            ).encode()
        except Exception:
            raise FleetError("invalid_request") from None
        return self._read(f"/activity-artifacts/{attempt_id}/context", body)

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


class ControlPlaneSources:
    """Task 10's record, value and context sources, served by the control plane.

    `load` and `agent` are an `ActivityRecordSource`, `fetch` a `ValueSource` and
    `read` a `ContextClient`; each call is one control request on the client's
    single-flight transport.
    """

    def __init__(self, client: ActivityClient):
        if type(client) is not ActivityClient:
            raise ValueError("An activity client is required")
        self.client = client

    def load(self, envelope) -> PinnedActivityRecords:
        return self.client.records(envelope)

    def agent(self, envelope):
        return self.client.agent(envelope)

    def fetch(self, attempt_id: str, handle) -> bytes:
        return self.client.input(attempt_id, handle)

    def read(self, attempt_id: str, ref) -> object:
        return self.client.context(attempt_id, ref)
