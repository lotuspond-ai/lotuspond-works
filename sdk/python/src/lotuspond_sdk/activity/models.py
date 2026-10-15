"""Frozen activity declarations. No record grants execution authority."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from ..contract import Identifier
from ..executable._validation import StrictModel
from ..executable.actions import NonnegativeInt, OpaqueId
from ..executable.package import ArtifactRef, Digest

UUID = Annotated[
    str, Field(min_length=36, max_length=36,
               pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]


class ActivityRecord(StrictModel):
    model_config = ConfigDict(frozen=True)


class ArtifactHandle(ActivityRecord):
    id: UUID
    version: int = Field(ge=1, le=2147483647)
    sha256: Digest


class ActivityEnvelope(ActivityRecord):
    schema_version: Literal["lotuspond.runtime-activity.v1"]
    installation_id: OpaqueId
    recovery_epoch: NonnegativeInt
    workspace_id: UUID
    project_id: UUID
    environment_id: UUID
    execution_id: UUID
    step_instance_id: UUID
    attempt_id: UUID
    deployment_revision_id: UUID
    package_sha256: Digest
    plan_sha256: Digest
    configuration_sha256: Digest
    input: ArtifactHandle
    kind: Literal["connector_read", "model"]
    dependency: ArtifactRef
    operation: Identifier | None
    pool_id: UUID
    instance_id: UUID
    generation: int = Field(ge=1, le=9007199254740991)
    delivery_id: UUID
    replay_id: UUID
    issued_at_ms: NonnegativeInt
    expires_at_ms: NonnegativeInt

    @model_validator(mode="after")
    def activity_domain(self) -> ActivityEnvelope:
        if not 0 < self.expires_at_ms - self.issued_at_ms <= 300000:
            raise ValueError("Activity lifetime must be positive and at most five minutes.")
        if self.kind == "connector_read":
            valid = self.dependency.kind == "connector" and self.operation is not None
        else:
            valid = self.dependency.kind == "model" and self.operation is None
        if not valid:
            raise ValueError("Activity kind, dependency and operation must agree.")
        return self


class ActivitySignature(ActivityRecord):
    format_version: Literal[1]
    algorithm: Literal["ed25519"]
    context: Literal["lotuspond-runtime-activity-signature-v1"]
    schema_version: Literal["lotuspond.runtime-activity.v1"]
    canonicalization: Literal["lotuspond.runtime-activity.jcs.v1"]
    key_id: Digest
    sha256: Digest
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")


class ActivityVerificationReport(ActivityRecord):
    valid: bool
    code: Literal["verified", "untrusted_key", "revoked_key", "digest_mismatch", "invalid_signature"]
    sha256: Digest
    key_id: Digest
    binding_status: Literal["not_checked"] = "not_checked"
    authority_granted: Literal[False] = False

    @model_validator(mode="after")
    def consistent_result(self) -> ActivityVerificationReport:
        if self.valid != (self.code == "verified"):
            raise ValueError("Verification outcome must match its code.")
        return self


class ActivityDelivery(ActivityRecord):
    envelope: str
    signature: str

    @model_validator(mode="after")
    def combined_size(self) -> ActivityDelivery:
        try:
            size = len(self.envelope.encode("utf-8")) + len(self.signature.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("Delivery documents require valid Unicode.") from None
        if size > 65536:
            raise ValueError("Combined activity documents exceed the byte limit.")
        return self


class UsageObservation(ActivityRecord):
    """Explicit unknowns remain unknown; counts and costs grant no authority."""

    input_tokens: NonnegativeInt | None
    output_tokens: NonnegativeInt | None
    cost_microusd: NonnegativeInt | None
    completed_at_ms: NonnegativeInt


ACTIVITY_MODELS = (
    ActivityEnvelope, ArtifactHandle, ArtifactRef, ActivitySignature,
    ActivityVerificationReport, ActivityDelivery, UsageObservation,
)
