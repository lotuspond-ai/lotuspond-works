"""Exact synthetic capability declarations, never runtime or execution authority."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .contract import (
    MAX_JSON_DEPTH,
    MAX_JSON_ITEMS,
    SCHEMA_VERSION,
    Identifier,
    ReferenceRevision,
    Version,
    check_json_bounds,
    package_digest,
    validate_package,
)
from .errors import SdkError
from .metadata import CANONICALIZATION
from .trust import _StrictRecord

MAX_CAPABILITIES = 64
MAX_REASONS = 20
NodeKind = Literal["start", "end", "agent", "connector", "human_review"]
ReasonCode = Literal[
    "unsupported_schema",
    "unsupported_canonicalization",
    "unsupported_node",
    "unsupported_connector",
    "unsupported_operation",
    "unsupported_model",
    "unsupported_platform",
    "missing_capability",
]


def _record_input(raw: object) -> object:
    """Project bounded known records without serializers dropping mutated fields."""
    known = (
        ConnectorCapability,
        ModelCapability,
        PlatformCapability,
        CapabilityProfile,
        CompatibilityRequirements,
        CompatibilityReason,
        CompatibilityReport,
    )
    active: set[int] = set()
    visits = 0

    def project(value: object, depth: int) -> object:
        nonlocal visits
        visits += 1
        if visits > MAX_JSON_ITEMS or depth > MAX_JSON_DEPTH:
            raise ValueError("Declaration input exceeds supported bounds.")
        kind = type(value)
        if value is None or kind in (str, bool, int, float):
            return value
        if kind in known:
            try:
                data = object.__getattribute__(value, "__dict__")
                extras = object.__getattribute__(value, "__pydantic_extra__")
            except AttributeError:
                raise ValueError("Declaration records must contain valid fields.") from None
            if extras is not None and (type(extras) is not dict or extras):
                raise ValueError("Extra declaration fields are not permitted.")
            if type(data) is not dict or set(data) - set(kind.model_fields):
                raise ValueError("Extra declaration fields are not permitted.")
        elif kind in (dict, list):
            data = value
        else:
            raise ValueError("Declarations must contain JSON values or known records.")
        if len(data) > MAX_JSON_ITEMS or id(value) in active:
            raise ValueError("Declaration input exceeds supported bounds.")
        if kind is not list and any(type(key) is not str for key in data):
            raise ValueError("Declaration keys must be strings.")
        active.add(id(value))
        try:
            if kind is list:
                return [project(child, depth + 1) for child in data]
            return {key: project(child, depth + 1) for key, child in data.items()}
        finally:
            active.remove(id(value))

    projected = project(raw, 0)
    check_json_bounds(projected)
    return projected


def _unique(keys: list[object]) -> None:
    if len(set(keys)) != len(keys):
        raise ValueError("Declaration keys must be unique.")


class _DeclarationRecord(_StrictRecord):
    @model_validator(mode="wrap")
    @classmethod
    def revalidate_fields(cls, raw, handler):
        return handler(_record_input(raw))


class ConnectorCapability(_DeclarationRecord):
    connector: Identifier
    version: Version
    operations: list[Identifier] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_operations(self) -> ConnectorCapability:
        _unique(self.operations)
        return self


class ModelCapability(_DeclarationRecord):
    profile: Identifier
    revision: ReferenceRevision


class PlatformCapability(_DeclarationRecord):
    os: Identifier
    architecture: Identifier
    capabilities: list[Identifier] = Field(max_length=MAX_CAPABILITIES)

    @model_validator(mode="after")
    def unique_capabilities(self) -> PlatformCapability:
        _unique(self.capabilities)
        return self


class CapabilityProfile(_DeclarationRecord):
    """A complete explicit synthetic declaration, with no implied support."""

    format_version: Literal["lotuspond.workflow-capabilities.v1"]
    assurance: Literal["synthetic-declaration"]
    schema_versions: list[Literal[SCHEMA_VERSION]] = Field(max_length=MAX_CAPABILITIES)
    canonicalizations: list[Literal[CANONICALIZATION]] = Field(max_length=MAX_CAPABILITIES)
    node_kinds: list[NodeKind] = Field(max_length=MAX_CAPABILITIES)
    connectors: list[ConnectorCapability] = Field(max_length=MAX_CAPABILITIES)
    models: list[ModelCapability] = Field(max_length=MAX_CAPABILITIES)
    platforms: list[PlatformCapability] = Field(max_length=MAX_CAPABILITIES)

    @model_validator(mode="after")
    def unambiguous_declarations(self) -> CapabilityProfile:
        for values in (self.schema_versions, self.canonicalizations, self.node_kinds):
            _unique(values)
        _unique([(record.connector, record.version) for record in self.connectors])
        _unique([(record.profile, record.revision) for record in self.models])
        _unique([(record.os, record.architecture) for record in self.platforms])
        return self


class CompatibilityRequirements(_DeclarationRecord):
    """Explicit assessment context; never part of the workflow-package v1 digest."""

    format_version: Literal[1]
    os: Identifier
    architecture: Identifier
    required_capabilities: list[Identifier] = Field(max_length=MAX_CAPABILITIES)

    @field_validator("format_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Only integer requirements format version 1 is supported.")
        return value

    @model_validator(mode="after")
    def unique_capabilities(self) -> CompatibilityRequirements:
        _unique(self.required_capabilities)
        return self


class CompatibilityReason(_DeclarationRecord):
    code: ReasonCode
    # Only fixed field names and numeric offsets, never supplied IDs or values.
    path: str = Field(
        max_length=128,
        pattern=(
            r"^/(?:schema_version|canonicalization|nodes/[0-9]+/kind|"
            r"connectors/[0-9]+(?:/operations/[0-9]+)?|models/[0-9]+|"
            r"requirements/(?:os|architecture|required_capabilities/[0-9]+))$"
        ),
    )


class CompatibilityReport(_DeclarationRecord):
    compatible: bool
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assurance: Literal["synthetic-declaration"]
    contract_valid: Literal[True] = True
    signature_status: Literal["not_checked"] = "not_checked"
    authority_granted: Literal[False] = False
    reasons: list[CompatibilityReason] = Field(max_length=MAX_REASONS)
    reasons_truncated: bool

    @field_validator("contract_valid", "authority_granted", mode="before")
    @classmethod
    def exact_assurance_flags(cls, value, info):
        expected = info.field_name == "contract_valid"
        if value is not expected:
            raise ValueError("Compatibility assurance flags must be exact booleans.")
        return value

    @model_validator(mode="after")
    def consistent_outcome(self) -> CompatibilityReport:
        pairs = [(reason.code, reason.path) for reason in self.reasons]
        if pairs != sorted(set(pairs)):
            raise ValueError("Compatibility reasons must be unique and sorted.")
        if self.compatible != (not self.reasons and not self.reasons_truncated):
            raise ValueError("Compatibility must reflect every refusal, including truncation.")
        return self


def _declaration_digest(record: _DeclarationRecord) -> str:
    content = json.dumps(
        BaseModel.model_dump(record, mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def check_declared_compatibility(
    raw: object, profile: object, requirements: object
) -> CompatibilityReport:
    """Compare exact declarations; no signatures, providers, credentials or runtime.

    Every package declaration is checked, including unused models, connectors
    and operations. Invalid shapes fail with fixed SDK errors; valid declarations
    lacking any exact match produce a bounded incompatible report.
    """
    package = validate_package(raw)
    try:
        target = CapabilityProfile.model_validate(profile)
    except ValidationError:
        raise SdkError("invalid_capability_profile") from None
    try:
        context = CompatibilityRequirements.model_validate(requirements)
    except ValidationError:
        raise SdkError("invalid_compatibility_requirements") from None
    reasons: list[CompatibilityReason] = []

    def emit(code: ReasonCode, path: str) -> None:
        reasons.append(CompatibilityReason(code=code, path=path))

    if package.schema_version not in target.schema_versions:
        emit("unsupported_schema", "/schema_version")
    if CANONICALIZATION not in target.canonicalizations:
        emit("unsupported_canonicalization", "/canonicalization")
    for index, node in enumerate(package.nodes):
        if node.kind not in target.node_kinds:
            emit("unsupported_node", f"/nodes/{index}/kind")
    connectors = {(record.connector, record.version): record for record in target.connectors}
    for index, declaration in enumerate(package.connectors):
        supported = connectors.get((declaration.connector, declaration.version))
        if supported is None:
            emit("unsupported_connector", f"/connectors/{index}")
            continue
        for operation_index, operation in enumerate(declaration.operations):
            if operation not in supported.operations:
                emit("unsupported_operation", f"/connectors/{index}/operations/{operation_index}")
    models = {(record.profile, record.revision) for record in target.models}
    for index, declaration in enumerate(package.models):
        if (declaration.profile, declaration.revision) not in models:
            emit("unsupported_model", f"/models/{index}")
    platform = next(
        (
            record
            for record in target.platforms
            if (record.os, record.architecture) == (context.os, context.architecture)
        ),
        None,
    )
    if platform is None:
        emit("unsupported_platform", "/requirements/os")
    else:
        for index, capability in enumerate(context.required_capabilities):
            if capability not in platform.capabilities:
                emit("missing_capability", f"/requirements/required_capabilities/{index}")
    reasons.sort(key=lambda reason: (reason.code, reason.path))
    return CompatibilityReport(
        compatible=not reasons,
        package_sha256=package_digest(package),
        profile_sha256=_declaration_digest(target),
        requirements_sha256=_declaration_digest(context),
        assurance="synthetic-declaration",
        reasons=reasons[:MAX_REASONS],
        reasons_truncated=len(reasons) > MAX_REASONS,
    )
