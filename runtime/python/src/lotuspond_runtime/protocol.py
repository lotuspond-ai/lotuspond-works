"""Pure strict shared fleet records. Identity metadata never supplies proof."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal, TypeVar
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .canonical import (
    MAX_SAFE_INTEGER,
    _project_fleet_value,
    canonical_connection_json,
    canonical_pool_json,
    connection_digest,
    decode_fleet_json,
    pool_digest,
)
from .errors import FleetError

# Re-export the pure public helpers from this shared module.
__all__ = [
    "Scope",
    "ConnectorPin",
    "SecretPin",
    "DestinationRule",
    "DataHandling",
    "ConnectionConfig",
    "PoolPolicy",
    "RuntimeObservation",
    "RuntimeIdentity",
    "SelfState",
    "IssuedCredential",
    "PreparedCredential",
    "AcquiredSession",
    "CredentialState",
    "FleetError",
    "decode_fleet_json",
    "validate_connection_config",
    "validate_pool_policy",
    "canonical_connection_json",
    "connection_digest",
    "canonical_pool_json",
    "pool_digest",
    "encode_issued_credential",
    "encode_prepared_credential",
    "encode_acquired_session",
]


def _uuid(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise FleetError("invalid_request") from None
    return value


def _exact_int(value: object) -> object:
    if type(value) is not int:
        raise FleetError("invalid_request")
    return value


def _exact_false(value: object) -> object:
    if value is not False:
        raise FleetError("invalid_request")
    return value


def _array(value: object) -> tuple:
    if type(value) not in (list, tuple):
        raise FleetError("invalid_request")
    return tuple(value)


def _secret(value: object) -> SecretStr | str:
    if type(value) not in (SecretStr, str):
        raise FleetError("invalid_request")
    return value


CanonicalUUID = Annotated[str, Field(min_length=36, max_length=36), AfterValidator(_uuid)]
Identifier = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
]
Version = Annotated[
    str,
    Field(
        max_length=20, pattern=r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$"
    ),
]
OpaqueId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
]
Digest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
SafeInt = Annotated[int, BeforeValidator(_exact_int), Field(ge=0, le=MAX_SAFE_INTEGER)]
Secret = Annotated[SecretStr, BeforeValidator(_secret)]


class FleetModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
        defer_build=True,
    )

    @classmethod
    def model_validate_json(
        cls, json_data, *, strict=None, extra=None, context=None, by_alias=None, by_name=None
    ):
        # Route the public JSON entry point through the same byte/key/depth budget.
        try:
            content = json_data.encode("utf-8") if type(json_data) is str else json_data
        except UnicodeError:
            raise FleetError("invalid_request") from None
        return cls.model_validate(
            decode_fleet_json(content),
            strict=True,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @model_validator(mode="wrap")
    @classmethod
    def _bounded_validation(cls, value, handler, info: ValidationInfo):
        # Native Pydantic JSON parsing loses raw byte counts and duplicate keys.
        # Supported JSON entry points decode first, then validate in Python mode.
        if info.mode == "json":
            raise FleetError("invalid_request")
        try:
            projected = _project_fleet_value(value, models=True)
            # Per-call extra='ignore'/'allow' must not relax the wire contract.
            # Each nested FleetModel enforces its own declared fields as well.
            if type(projected) is not dict or any(key not in cls.model_fields for key in projected):
                raise FleetError("invalid_request")
            return handler(projected)
        except (ValidationError, ValueError, TypeError, RecursionError):
            raise FleetError("invalid_request") from None


class VersionedModel(FleetModel):
    format_version: Annotated[Literal[1], BeforeValidator(_exact_int)] = 1


class Scope(FleetModel):
    workspace_id: CanonicalUUID
    project_id: CanonicalUUID
    environment_id: CanonicalUUID


class ConnectorPin(FleetModel):
    name: Identifier
    version: Version
    artifact_sha256: Digest


class SecretPin(FleetModel):
    reference_id: CanonicalUUID
    version: Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=2147483647)]


class DestinationRule(FleetModel):
    host: Annotated[str, Field(min_length=1, max_length=253)]
    port: Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=65535)]
    path_prefix: Annotated[str, Field(min_length=1)]

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        if not value.isascii() or value != value.lower() or value.endswith(".") or "%" in value:
            raise FleetError("invalid_request")
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            if ":" in value or re.fullmatch(r"[0-9.]+", value):
                raise FleetError("invalid_request") from None
            if not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in value.split(".")
            ):
                raise FleetError("invalid_request") from None
        else:
            # Python 3.14 changed mapped-IPv6 text rendering. Freeze hex form
            # across supported interpreters; Task 7 refuses mapped sockets.
            canonical = str(address)
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                tail = int(address.ipv4_mapped)
                canonical = f"::ffff:{tail >> 16:x}:{tail & 65535:x}"
            if canonical != value:
                raise FleetError("invalid_request")
        return value

    @field_validator("path_prefix")
    @classmethod
    def _path(cls, value: str) -> str:
        # Structural normalized-prefix checks only. Task 7 owns request URL
        # normalization, segment matching, network refusal and socket pinning.
        if len(value.encode("utf-8")) > 1024 or not value.startswith("/"):
            raise FleetError("invalid_request")
        if any(c in value for c in ("?", "#", "\\", "%")) or any(
            ord(c) < 32 or ord(c) == 127 for c in value
        ):
            raise FleetError("invalid_request")
        if any(segment in (".", "..") for segment in value.split("/")) or "//" in value:
            raise FleetError("invalid_request")
        return value


class DataHandling(FleetModel):
    retain_payloads: Annotated[Literal[False], BeforeValidator(_exact_false)] = False
    max_request_bytes: Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=65536)] = 65536
    max_response_bytes: Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=1048576)] = (
        1048576
    )
    timeout_ms: Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=30000)] = 10000


def _unique(items: tuple) -> tuple:
    if len(set(items)) != len(items):
        raise FleetError("invalid_request")
    return items


class ConnectionConfig(VersionedModel):
    connector: ConnectorPin
    operations: Annotated[
        tuple[Identifier, ...],
        BeforeValidator(_array),
        Field(min_length=1, max_length=32),
        AfterValidator(_unique),
    ]
    destinations: Annotated[
        tuple[DestinationRule, ...],
        BeforeValidator(_array),
        Field(max_length=16),
        AfterValidator(_unique),
    ]
    secrets: Annotated[
        tuple[SecretPin, ...],
        BeforeValidator(_array),
        Field(max_length=16),
        AfterValidator(_unique),
    ]
    data_handling: DataHandling


class PoolPolicy(VersionedModel):
    connectors: Annotated[
        tuple[ConnectorPin, ...],
        BeforeValidator(_array),
        Field(max_length=64),
        AfterValidator(_unique),
    ]
    platforms: Annotated[
        tuple[Literal["linux"], ...], BeforeValidator(_array), Field(min_length=1, max_length=1)
    ]
    connection_ids: Annotated[
        tuple[CanonicalUUID, ...],
        BeforeValidator(_array),
        Field(max_length=64),
        AfterValidator(_unique),
    ]
    max_instances: Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=1000)]
    max_concurrent_invocations: Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=32)]


class RuntimeObservation(FleetModel):
    platform: Literal["linux"]
    connectors: Annotated[
        tuple[ConnectorPin, ...],
        BeforeValidator(_array),
        Field(max_length=64),
        AfterValidator(_unique),
    ]


class RuntimeIdentity(FleetModel):
    scope: Scope
    installation_id: OpaqueId
    recovery_epoch: SafeInt
    pool_id: CanonicalUUID
    instance_id: CanonicalUUID
    credential_version: SafeInt
    generation: SafeInt


class SelfState(FleetModel):
    identity: RuntimeIdentity
    credential_expires_at_ms: SafeInt
    session_expires_at_ms: SafeInt | None
    server_time_ms: SafeInt


class IssuedCredential(FleetModel):
    token: Secret
    state: SelfState


class PreparedCredential(FleetModel):
    token: Secret
    credential_version: SafeInt
    activation_deadline_ms: SafeInt
    server_time_ms: SafeInt


class AcquiredSession(FleetModel):
    secret: Secret
    identity: RuntimeIdentity
    expires_at_ms: SafeInt
    server_time_ms: SafeInt


class CredentialState(VersionedModel):
    revision: SafeInt
    instance_id: CanonicalUUID
    active_token: Secret
    pending_token: Secret | None
    pending_version: SafeInt | None

    @model_validator(mode="after")
    def _pending_pair(self):
        if (self.pending_token is None) != (self.pending_version is None):
            raise FleetError("invalid_request")
        return self


Model = TypeVar("Model", bound=FleetModel)


def _validate(model: type[Model], raw: object) -> Model:
    try:
        return model.model_validate(raw)
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise FleetError("invalid_request") from None


def validate_connection_config(raw: object) -> ConnectionConfig:
    return _validate(ConnectionConfig, raw)


def validate_pool_policy(raw: object) -> PoolPolicy:
    return _validate(PoolPolicy, raw)


def _identity_projection(identity: RuntimeIdentity) -> dict[str, object]:
    return {
        "scope": {
            "workspace_id": identity.scope.workspace_id,
            "project_id": identity.scope.project_id,
            "environment_id": identity.scope.environment_id,
        },
        "installation_id": identity.installation_id,
        "recovery_epoch": identity.recovery_epoch,
        "pool_id": identity.pool_id,
        "instance_id": identity.instance_id,
        "credential_version": identity.credential_version,
        "generation": identity.generation,
    }


def encode_issued_credential(raw: IssuedCredential) -> dict[str, object]:
    model = _validate(IssuedCredential, raw)
    return {
        "token": model.token.get_secret_value(),
        "state": {
            "identity": _identity_projection(model.state.identity),
            "credential_expires_at_ms": model.state.credential_expires_at_ms,
            "session_expires_at_ms": model.state.session_expires_at_ms,
            "server_time_ms": model.state.server_time_ms,
        },
    }


def encode_prepared_credential(raw: PreparedCredential) -> dict[str, object]:
    model = _validate(PreparedCredential, raw)
    return {
        "token": model.token.get_secret_value(),
        "credential_version": model.credential_version,
        "activation_deadline_ms": model.activation_deadline_ms,
        "server_time_ms": model.server_time_ms,
    }


def encode_acquired_session(raw: AcquiredSession) -> dict[str, object]:
    model = _validate(AcquiredSession, raw)
    return {
        "secret": model.secret.get_secret_value(),
        "identity": _identity_projection(model.identity),
        "expires_at_ms": model.expires_at_ms,
        "server_time_ms": model.server_time_ms,
    }


# Explicit management wire records. Private configuration appears only in detail.
ResourceStatus = Literal["active", "disabled", "revoked"]
Revision = Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=2147483647)]
Name = Annotated[str, Field(min_length=1, max_length=160)]


class ManagementNameRequest(FleetModel):
    name: Name


class ManagementStatusRequest(FleetModel):
    status: ResourceStatus


class SecretRegistrationRequest(FleetModel):
    binding_id: OpaqueId
    version: Revision


class EmptyManagementRequest(FleetModel):
    pass


class ManagementPageRequest(FleetModel):
    after_id: CanonicalUUID | None = None
    limit: Annotated[int, BeforeValidator(_exact_int), Field(ge=1, le=100)] = 50


class ConnectionView(FleetModel):
    id: CanonicalUUID
    scope: Scope
    name: Name
    status: ResourceStatus
    current_version: Revision | None
    created_at_ms: SafeInt


class PoolView(ConnectionView):
    pass


class ConnectionVersionView(FleetModel):
    id: CanonicalUUID
    connection_id: CanonicalUUID
    scope: Scope
    version: Revision
    sha256: Digest
    created_at_ms: SafeInt


class ConnectionVersionDetail(ConnectionVersionView):
    config: ConnectionConfig


class PoolPolicyView(FleetModel):
    id: CanonicalUUID
    pool_id: CanonicalUUID
    scope: Scope
    version: Revision
    sha256: Digest
    created_at_ms: SafeInt


class PoolPolicyDetail(PoolPolicyView):
    policy: PoolPolicy


class SecretReferenceView(FleetModel):
    id: CanonicalUUID
    scope: Scope
    binding_id: OpaqueId
    version: Revision
    status: Literal["active", "revoked"]
    created_at_ms: SafeInt


class ConnectionList(FleetModel):
    items: Annotated[tuple[ConnectionView, ...], BeforeValidator(_array), Field(max_length=100)]
    next_after_id: CanonicalUUID | None


class PoolList(FleetModel):
    items: Annotated[tuple[PoolView, ...], BeforeValidator(_array), Field(max_length=100)]
    next_after_id: CanonicalUUID | None


class ConnectionVersionList(FleetModel):
    items: Annotated[
        tuple[ConnectionVersionView, ...], BeforeValidator(_array), Field(max_length=100)
    ]
    next_after_id: CanonicalUUID | None


class PoolPolicyList(FleetModel):
    items: Annotated[tuple[PoolPolicyView, ...], BeforeValidator(_array), Field(max_length=100)]
    next_after_id: CanonicalUUID | None


class SecretReferenceList(FleetModel):
    items: Annotated[
        tuple[SecretReferenceView, ...], BeforeValidator(_array), Field(max_length=100)
    ]
    next_after_id: CanonicalUUID | None


__all__ += [
    "ResourceStatus",
    "ManagementNameRequest",
    "ManagementStatusRequest",
    "SecretRegistrationRequest",
    "EmptyManagementRequest",
    "ManagementPageRequest",
    "ConnectionView",
    "PoolView",
    "ConnectionVersionView",
    "ConnectionVersionDetail",
    "PoolPolicyView",
    "PoolPolicyDetail",
    "SecretReferenceView",
    "ConnectionList",
    "PoolList",
    "ConnectionVersionList",
    "PoolPolicyList",
    "SecretReferenceList",
]


class EnrollmentIssued(FleetModel):
    enrollment_id: CanonicalUUID
    token: Secret
    expires_at_ms: SafeInt


class EnrollmentView(FleetModel):
    id: CanonicalUUID
    scope: Scope
    pool_id: CanonicalUUID
    status: Literal["issued", "consumed", "revoked"]
    policy_version: Revision
    expires_at_ms: SafeInt
    consumed_at_ms: SafeInt | None
    created_at_ms: SafeInt


class InstanceView(FleetModel):
    id: CanonicalUUID
    scope: Scope
    pool_id: CanonicalUUID
    status: Literal["active", "revoked"]
    installation_id: OpaqueId
    recovery_epoch: SafeInt
    generation: SafeInt
    current_policy_version: Revision | None
    last_seen_at_ms: SafeInt | None
    created_at_ms: SafeInt


class EnrollmentList(FleetModel):
    items: Annotated[tuple[EnrollmentView, ...], BeforeValidator(_array), Field(max_length=100)]
    next_after_id: CanonicalUUID | None


class InstanceList(FleetModel):
    items: Annotated[tuple[InstanceView, ...], BeforeValidator(_array), Field(max_length=100)]
    next_after_id: CanonicalUUID | None


def encode_enrollment_issued(raw: EnrollmentIssued) -> dict[str, object]:
    model = _validate(EnrollmentIssued, raw)
    return {
        "enrollment_id": model.enrollment_id,
        "token": model.token.get_secret_value(),
        "expires_at_ms": model.expires_at_ms,
    }


__all__ += [
    "EnrollmentIssued",
    "EnrollmentView",
    "InstanceView",
    "EnrollmentList",
    "InstanceList",
    "encode_enrollment_issued",
]
