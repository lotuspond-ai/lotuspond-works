"""Strict execution service contracts, independent of HTTP and engine SDKs."""

from typing import Annotated, Literal

import rfc8785
from lotuspond_runtime.protocol import CanonicalUUID, Digest, OpaqueId, SafeInt
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.input import decode_executable_bytes, project_json
from lotuspond_sdk.executable.package import ArtifactRef
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .errors import ExecutionError


def array(raw):
    if type(raw) not in (tuple, list):
        raise ExecutionError("invalid_request")
    return tuple(raw)


class Contract(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    @classmethod
    def model_validate_json(cls, json_data, **kwargs):
        """Preserve duplicate-key and raw byte bounds before typed validation."""
        try:
            if type(json_data) is str:
                json_data = json_data.encode("utf-8")
            if type(json_data) is not bytes:
                raise ValueError
            raw = decode_executable_bytes(json_data)
            return cls.model_validate(raw, **kwargs)
        except ExecutionError:
            raise
        except Exception:
            raise ExecutionError("invalid_request") from None


class PackageCheckRequest(Contract):
    """A Studio check: the publication path without trust or storage."""

    package: object
    dependency_ids: Annotated[
        tuple[CanonicalUUID, ...], BeforeValidator(array), Field(max_length=511)
    ] = ()

    @model_validator(mode="after")
    def bounded(self):
        canonical_value({"package": self.package, "dependency_ids": list(self.dependency_ids)})
        if len(set(self.dependency_ids)) != len(self.dependency_ids):
            raise ExecutionError("invalid_request")
        return self


class PublicationRequest(Contract):
    package: object
    dependency_ids: Annotated[
        tuple[CanonicalUUID, ...], BeforeValidator(array), Field(max_length=511)
    ]
    signature: object

    @model_validator(mode="after")
    def bounded(self):
        canonical_value(
            {
                "package": self.package,
                "dependency_ids": list(self.dependency_ids),
                "signature": self.signature,
            }
        )
        if len(set(self.dependency_ids)) != len(self.dependency_ids):
            raise ExecutionError("invalid_request")
        return self


class PublicationView(Contract):
    id: CanonicalUUID
    # What it is, so an author can match a package's pinned references (Studio v2).
    kind: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=100)
    package_sha256: Digest
    plan_sha256: Digest | None
    validator_id: str = Field(min_length=1, max_length=128)
    compiler_id: str | None = Field(default=None, min_length=1, max_length=128)


class ExecutionLimits(Contract):
    max_steps: int = Field(ge=1, le=10000)
    active_seconds: int = Field(ge=1, le=86400)
    elapsed_seconds: int = Field(ge=1, le=31536000)
    retained_waits: int = Field(ge=0, le=10000)
    max_input_tokens: int = Field(ge=0, le=10000000)
    max_output_tokens: int = Field(ge=0, le=10000000)
    max_cost_microusd: int = Field(ge=0, le=1000000000000)
    active_capacity: int = Field(ge=1, le=5)
    pending_capacity: int = Field(ge=1, le=1000)


class DependencyBinding(Contract):
    dependency: ArtifactRef
    connection_id: CanonicalUUID | None = None
    connection_version: int | None = Field(default=None, ge=1, le=9007199254740991)
    source_id: CanonicalUUID | None = None
    source_version: int | None = Field(default=None, ge=1, le=9007199254740991)

    @model_validator(mode="after")
    def pairs(self):
        if (self.connection_id is None) != (self.connection_version is None) or (
            self.source_id is None
        ) != (self.source_version is None):
            raise ExecutionError("invalid_request")
        return self


class DeploymentConfig(Contract):
    bindings: Annotated[
        tuple[DependencyBinding, ...], BeforeValidator(array), Field(max_length=511)
    ]
    limits: ExecutionLimits

    @model_validator(mode="after")
    def unique(self):
        identities = [
            (x.dependency.kind, x.dependency.name, x.dependency.version) for x in self.bindings
        ]
        if len(set(identities)) != len(identities):
            raise ExecutionError("invalid_request")
        return self


class DeploymentRequest(Contract):
    deployment_id: CanonicalUUID | None
    publication_id: CanonicalUUID
    pool_id: CanonicalUUID
    configuration: DeploymentConfig
    release_id: CanonicalUUID | None


class DeploymentView(Contract):
    id: CanonicalUUID
    revision_id: CanonicalUUID | None
    status: Literal["enabled", "disabled", "revoked"]
    # E05 (owner decision 3): `connector/operation` of each write that would dispatch
    # without review. Listed on creation and in deployment detail.
    unreviewed_operations: Annotated[
        tuple[str, ...], BeforeValidator(array), Field(max_length=64)
    ] = ()


class ExecutionView(Contract):
    id: CanonicalUUID
    state: Literal[
        "pending",
        "running",
        "waiting",
        "paused",
        "cancelled",
        "succeeded",
        "failed",
        "reconciliation_required",
    ]
    requested_state: Literal["running", "paused", "cancelled"]
    reason: OpaqueId | None
    admitted_at_ms: SafeInt
    deadline_ms: SafeInt
    result: ArtifactHandle | None
    next_event_sequence: SafeInt


class AdmissionRequest(Contract):
    deployment_revision_id: CanonicalUUID
    input: object

    @model_validator(mode="after")
    def bounded(self):
        canonical_value(self.input)
        return self


class SignalRequest(Contract):
    signal_id: OpaqueId
    wait_id: CanonicalUUID
    payload: object

    @model_validator(mode="after")
    def bounded(self):
        canonical_value(self.payload)
        return self


class SignalReceipt(Contract):
    id: CanonicalUUID
    status: Literal["pending", "leased", "delivered", "conflict"]


class ControlRequest(Contract):
    request_id: OpaqueId
    action: Literal["pause", "resume", "cancel"]


def canonical_value(raw):
    try:
        content = rfc8785.dumps(project_json(raw))
        if len(content) > 1048576:
            raise ValueError
        return content
    except Exception:
        raise ExecutionError("invalid_request") from None


def canonical_configuration(raw):
    return canonical_value(DeploymentConfig.model_validate(raw).model_dump(mode="json"))
