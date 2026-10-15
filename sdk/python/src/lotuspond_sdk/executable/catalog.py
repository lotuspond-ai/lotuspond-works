"""Exact local catalog definitions; external artifacts remain availability claims."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..contract import Identifier, ReferenceRevision, Version
from ._canonical import jcs_bytes, sha256_hex
from ._validation import StrictModel, validate_model
from .errors import ExecutableError
from .input import project_json
from .package import (
    PACKAGE_MODELS,
    AgentLimits,
    Approval,
    ArtifactRef,
    Digest,
    RetryPolicy,
    Rule,
)
from .types import DataSchema, NamedSchema


class ReconciliationSpec(StrictModel):
    mode: Literal["none", "read_back"]
    read_operation: Identifier | None = None
    absence_rules: list[Rule] | None = Field(default=None, min_length=1, max_length=64)
    effect_rules: list[Rule] | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def mode_payload(self):
        fields = (self.read_operation, self.absence_rules, self.effect_rules)
        if self.mode == "none" and any(f is not None for f in fields):
            raise ValueError("No reconciliation requires null fields.")
        if self.mode == "read_back" and any(f is None for f in fields):
            raise ValueError("Read-back requires an operation and deterministic rules.")
        return self


class ConnectorOperation(StrictModel):
    id: Identifier
    target_schema: DataSchema
    input_schema: DataSchema
    output_schema: DataSchema
    effect: Literal["read", "write"]
    idempotency: Literal["none", "target_key"]
    reconciliation: ReconciliationSpec


class ConnectorDefinition(StrictModel):
    artifact_sha256: Digest
    operations: list[ConnectorOperation] = Field(min_length=1, max_length=64)


class CatalogReadTool(StrictModel):
    connector_ref: ArtifactRef
    operation: Identifier
    retry: RetryPolicy

    @model_validator(mode="after")
    def connector_kind(self):
        if self.connector_ref.kind != "connector":
            raise ValueError("Expected connector reference.")
        return self


class ModelDefinition(StrictModel):
    profile: ReferenceRevision
    configuration_revision: ReferenceRevision
    configuration_sha256: Digest
    input_schema: DataSchema
    output_schema: DataSchema
    read_tools: list[CatalogReadTool] = Field(default_factory=list, max_length=32)
    limits: AgentLimits


class AgentDefinition(StrictModel):
    model_ref: ArtifactRef
    instructions: str = Field(min_length=1, max_length=16384)
    input_schema: DataSchema
    output_schema: DataSchema
    read_tools: list[CatalogReadTool] = Field(default_factory=list, max_length=32)
    limits: AgentLimits

    @model_validator(mode="after")
    def model_kind(self):
        if self.model_ref.kind != "model":
            raise ValueError("Expected model reference.")
        return self


class NamedRule(StrictModel):
    rule_id: Identifier
    version: ReferenceRevision
    rule: Rule


class EvaluationDefinition(StrictModel):
    input_schema: DataSchema
    rules: list[NamedRule] = Field(min_length=1, max_length=64)


class PolicyDefinition(StrictModel):
    approval: Approval
    independent_review: bool
    assignment: Identifier
    escalation: Identifier | None = None
    allowed_operation_classes: list[Identifier] = Field(max_length=64)
    allowed_capability_classes: list[Identifier] = Field(max_length=64)


class KnowledgeDefinition(StrictModel):
    source_revision: ReferenceRevision
    content_sha256: Digest
    freshness_seconds: int = Field(ge=1, le=31536000)
    classification: Identifier
    citation_schema: DataSchema


class ArtifactDefinition(StrictModel):
    media_type: str = Field(min_length=1, max_length=160, pattern=r"^[^\s/]+/[^\s/]+$")
    content_sha256: Digest
    size_bytes: int = Field(ge=0, le=9007199254740991)
    classification: Identifier


DEFINITIONS = {
    "connector": ConnectorDefinition,
    "model": ModelDefinition,
    "agent": AgentDefinition,
    "evaluation": EvaluationDefinition,
    "policy": PolicyDefinition,
    "knowledge": KnowledgeDefinition,
    "artifact": ArtifactDefinition,
}


class CatalogDocument(StrictModel):
    format_version: Literal[1]
    kind: Literal["connector", "model", "agent", "evaluation", "policy", "knowledge", "artifact"]
    name: Identifier
    version: Version
    schemas: list[NamedSchema] = Field(max_length=64)
    definition: (
        ConnectorDefinition
        | ModelDefinition
        | AgentDefinition
        | EvaluationDefinition
        | PolicyDefinition
        | KnowledgeDefinition
        | ArtifactDefinition
    )

    @model_validator(mode="after")
    def definition_kind(self):
        if type(self.definition) is not DEFINITIONS[self.kind]:
            raise ValueError("Definition kind mismatch.")
        return self


class CatalogBundle(StrictModel):
    documents: list[CatalogDocument] = Field(max_length=448)

    @model_validator(mode="before")
    @classmethod
    def preflight(cls, raw):
        from .validation import preflight_catalog

        preflight_catalog(raw)
        return raw


CATALOG_MODELS = (
    *PACKAGE_MODELS,
    ReconciliationSpec,
    ConnectorOperation,
    ConnectorDefinition,
    CatalogReadTool,
    ModelDefinition,
    AgentDefinition,
    NamedRule,
    EvaluationDefinition,
    PolicyDefinition,
    KnowledgeDefinition,
    ArtifactDefinition,
    CatalogDocument,
    CatalogBundle,
)


def _check_document(document):
    from .validation import check_unique_tree, normalize_tree

    normalize_tree(document.definition, document.schemas)
    check_unique_tree(document.definition)
    if isinstance(document.definition, ConnectorDefinition):
        reads = {op.id for op in document.definition.operations if op.effect == "read"}
        for op in document.definition.operations:
            if (
                op.reconciliation.mode == "read_back"
                and op.reconciliation.read_operation not in reads
            ):
                raise ExecutableError("unknown_reference")


def validate_catalog(raw: object) -> CatalogBundle:
    """Validate every occurrence; compilation deduplicates identical resolved definitions."""
    from .validation import preflight_catalog, shape_model

    projected = project_json(raw, model_types=CATALOG_MODELS)
    preflight_catalog(projected)
    bundle = shape_model(projected, CatalogBundle, "invalid_catalog")
    counts = {}
    identities = {}
    for document in bundle.documents:
        _check_document(document)
        counts[document.kind] = counts.get(document.kind, 0) + 1
        if counts[document.kind] > 64:
            raise ExecutableError("invalid_catalog")
        key = (document.kind, document.name, document.version)
        content = jcs_bytes(project_json(document, model_types=CATALOG_MODELS))
        if key in identities and identities[key] != content:
            raise ExecutableError("dependency_conflict")
        identities[key] = content
    return validate_model(
        bundle, model_type=CatalogBundle, model_types=CATALOG_MODELS, code="invalid_catalog"
    )


def canonical_catalog_document(raw: object) -> bytes:
    """Canonicalize a complete document, excluding its separate ArtifactRef wrapper."""
    from .validation import preflight_catalog, shape_model

    projected = project_json(raw, model_types=CATALOG_MODELS)
    preflight_catalog(projected)
    document = shape_model(projected, CatalogDocument, "invalid_catalog")
    _check_document(document)
    document = validate_model(
        document, model_type=CatalogDocument, model_types=CATALOG_MODELS, code="invalid_catalog"
    )
    return jcs_bytes(project_json(document, model_types=CATALOG_MODELS))


def catalog_document_digest(raw: object) -> str:
    return sha256_hex(canonical_catalog_document(raw))
