"""Complete synthetic declarations; no execution, lookup, or availability authority."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import Field, model_validator

from ..contract import Identifier, ReferenceRevision
from ._canonical import jcs_bytes, sha256_hex
from ._validation import StrictModel, report_bytes, validate_model
from .catalog import ConnectorDefinition, ModelDefinition
from .compiler import compile_package
from .dependencies import _dependency_inventory
from .errors import _safe_pointer
from .expressions import ArrayMapping, CallMapping, IndexSource, ObjectMapping
from .input import REPORT_LIMITS, project_json
from .metadata import CANONICALIZATION, COMPILER_REVISION, SCHEMA_VERSION
from .package import ArtifactRef, Digest, PackageLimits, Step
from .plan import PlanArray, PlanHandle, PlanObject
from .types import ArrayExport, DataSchema, HandleExport, ObjectExport
from .validation import executable_digest

StepKind = Literal[
    "transform",
    "decision",
    "parallel",
    "iteration",
    "connector_read",
    "agent",
    "action_intent",
    "human_task",
    "action_dispatch",
    "timer",
    "external_wait",
    "subworkflow",
    "outcome_verification",
]
ExpressionFunction = CallMapping.model_fields["fn"].annotation
ValueKind = Literal[
    "null",
    "boolean",
    "integer",
    "number",
    "string",
    "decimal",
    "array",
    "object",
    "ref",
    "action_intent",
    "human_decision",
    "action_receipt",
    "verification_evidence",
]
ReasonCode = Literal[
    "dependency_missing",
    "unsupported_schema",
    "unsupported_compiler",
    "unsupported_canonicalization",
    "unsupported_step",
    "unsupported_expression",
    "unsupported_value",
    "unsupported_artifact",
    "unsupported_operation",
    "unsupported_model_configuration",
    "unsupported_platform",
    "missing_capability",
    "insufficient_limit",
]
_DATA_TYPES = get_args(get_args(DataSchema)[0])
_STEP_TYPES = get_args(get_args(Step)[0])


def _unique(values):
    if len(values) != len(set(values)):
        raise ValueError("Declaration keys must be unique.")


class SupportedLimits(StrictModel):
    max_steps: int = Field(ge=0, le=10000)
    active_seconds: int = Field(ge=0, le=86400)
    elapsed_seconds: int = Field(ge=0, le=31536000)
    retained_waits: int = Field(ge=0, le=10000)
    max_input_tokens: int = Field(ge=0, le=10000000)
    max_output_tokens: int = Field(ge=0, le=10000000)
    max_cost_microusd: int = Field(ge=0, le=1000000000000)


class ExecutableOperationCapability(StrictModel):
    connector_ref: ArtifactRef
    operations: list[Identifier] = Field(max_length=32)

    @model_validator(mode="after")
    def exact_connector(self):
        if self.connector_ref.kind != "connector":
            raise ValueError("Expected connector pin.")
        _unique(self.operations)
        return self


class ExecutableModelConfiguration(StrictModel):
    model_ref: ArtifactRef
    configuration_revision: ReferenceRevision
    configuration_sha256: Digest

    @model_validator(mode="after")
    def exact_model(self):
        if self.model_ref.kind != "model":
            raise ValueError("Expected model pin.")
        return self


class ExecutablePlatformCapability(StrictModel):
    os: Identifier
    architecture: Identifier
    capabilities: list[Identifier] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_capabilities(self):
        _unique(self.capabilities)
        return self


class ExecutableCapabilityProfile(StrictModel):
    format_version: Literal["lotuspond.workflow-capabilities.v2"]
    assurance: Literal["synthetic-declaration"]
    schema_versions: list[Literal[SCHEMA_VERSION]] = Field(max_length=64)
    compiler_revisions: list[Literal[COMPILER_REVISION]] = Field(max_length=64)
    canonicalizations: list[Literal[CANONICALIZATION]] = Field(max_length=64)
    step_kinds: list[StepKind] = Field(max_length=64)
    expression_functions: list[ExpressionFunction] = Field(max_length=64)
    value_kinds: list[ValueKind] = Field(max_length=64)
    artifacts: list[ArtifactRef] = Field(max_length=64)
    operations: list[ExecutableOperationCapability] = Field(max_length=64)
    model_configurations: list[ExecutableModelConfiguration] = Field(max_length=64)
    platforms: list[ExecutablePlatformCapability] = Field(max_length=64)
    supported_limits: SupportedLimits

    @model_validator(mode="after")
    def unambiguous_support(self):
        for name in (
            "schema_versions",
            "compiler_revisions",
            "canonicalizations",
            "step_kinds",
            "expression_functions",
            "value_kinds",
        ):
            _unique(getattr(self, name))
        _unique([(p.os, p.architecture) for p in self.platforms])
        groups = [
            self.artifacts,
            [o.connector_ref for o in self.operations],
            [m.model_ref for m in self.model_configurations],
        ]
        pins = {}
        for group in groups:
            _unique([_identity(ref) for ref in group])
            for ref in group:
                key = _identity(ref)
                if key in pins and pins[key] != ref.sha256:
                    raise ValueError("Immutable artifact pins conflict.")
                pins[key] = ref.sha256
        return self


class ExecutableRequirements(StrictModel):
    format_version: Literal[1]
    runtime_contract: Literal["lotuspond.runtime-contract.v1"]
    os: Identifier
    architecture: Identifier
    required_capabilities: list[Identifier] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_capabilities(self):
        _unique(self.required_capabilities)
        return self


class ExecutableCompatibilityReason(StrictModel):
    code: ReasonCode
    path: str = Field(max_length=512)

    @model_validator(mode="after")
    def private_path(self):
        if self.path != _safe_pointer(self.path):
            raise ValueError("Reason paths must be sanitized.")
        return self


class ExecutableCompatibilityReport(StrictModel):
    contract_valid: Literal[True] = True
    closure_complete: bool
    compilation_status: Literal["valid", "unresolved"]
    compatible: bool
    package_sha256: Digest
    profile_sha256: Digest
    requirements_sha256: Digest
    reasons: list[ExecutableCompatibilityReason] = Field(max_length=20)
    reasons_truncated: bool
    signature_status: Literal["not_checked"] = "not_checked"
    artifact_availability: Literal["not_checked"] = "not_checked"
    provider_availability: Literal["not_checked"] = "not_checked"
    policy_status: Literal["not_checked"] = "not_checked"
    credentials_status: Literal["not_checked"] = "not_checked"
    live_support_status: Literal["not_checked"] = "not_checked"
    assurance: Literal["synthetic-declaration"] = "synthetic-declaration"
    authority_granted: Literal[False] = False

    @model_validator(mode="after")
    def truthful_outcome(self):
        pairs = [(reason.code, reason.path) for reason in self.reasons]
        if pairs != sorted(set(pairs)):
            raise ValueError("Reasons must be unique and sorted.")
        if self.closure_complete != (self.compilation_status == "valid"):
            raise ValueError("Incomplete closure cannot be compiled.")
        missing = any(reason.code == "dependency_missing" for reason in self.reasons)
        if missing == self.closure_complete:
            raise ValueError("Missing definitions require unresolved compilation.")
        if self.reasons_truncated and len(self.reasons) != 20:
            raise ValueError("Truncated reports retain the first twenty reasons.")
        if self.compatible != (self.closure_complete and not pairs and not self.reasons_truncated):
            raise ValueError("Compatibility must reflect every refusal.")
        return self


COMPATIBILITY_MODELS = (
    ArtifactRef,
    SupportedLimits,
    ExecutableOperationCapability,
    ExecutableModelConfiguration,
    ExecutablePlatformCapability,
    ExecutableCapabilityProfile,
    ExecutableRequirements,
    ExecutableCompatibilityReason,
    ExecutableCompatibilityReport,
)


def validate_executable_capability_profile(raw: object) -> ExecutableCapabilityProfile:
    return validate_model(
        raw,
        model_type=ExecutableCapabilityProfile,
        model_types=COMPATIBILITY_MODELS,
        code="invalid_profile",
    )


def validate_executable_requirements(raw: object) -> ExecutableRequirements:
    return validate_model(
        raw,
        model_type=ExecutableRequirements,
        model_types=COMPATIBILITY_MODELS,
        code="invalid_requirements",
    )


def validate_executable_compatibility_report(raw: object) -> ExecutableCompatibilityReport:
    return validate_model(
        raw,
        model_type=ExecutableCompatibilityReport,
        model_types=COMPATIBILITY_MODELS,
        code="invalid_requirements",
        limits=REPORT_LIMITS,
    )


def canonical_capability_profile_json(raw: object) -> bytes:
    return jcs_bytes(
        project_json(validate_executable_capability_profile(raw), model_types=COMPATIBILITY_MODELS)
    )


def canonical_requirements_json(raw: object) -> bytes:
    return jcs_bytes(
        project_json(validate_executable_requirements(raw), model_types=COMPATIBILITY_MODELS)
    )


def executable_compatibility_report_json(raw: object) -> bytes:
    return report_bytes(
        raw,
        model_type=ExecutableCompatibilityReport,
        model_types=COMPATIBILITY_MODELS,
        code="invalid_requirements",
    )


def _identity(ref):
    return ref.kind, ref.name, ref.version


def _walk_models(value, path=""):
    """Walk admitted records, excluding opaque literal payloads and private values."""
    from ._validation import StrictModel
    from .expressions import LiteralMapping

    if isinstance(value, StrictModel):
        yield value, path
        for name in type(value).model_fields:
            if isinstance(value, LiteralMapping) and name == "value":
                continue
            yield from _walk_models(getattr(value, name), f"{path}/{name}")
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _walk_models(child, f"{path}/{index}")


def check_declared_compatibility(
    raw: object,
    profile: object,
    requirements: object,
    *,
    dependencies: list[object],
    catalog: object,
) -> ExecutableCompatibilityReport:
    """Assess every known source declaration, never provider or runtime authority.

    Missing definitions yield an unresolved report after complete supplied-input
    admission and known-graph auditing. Lexical/type compilation is deferred until
    closure is complete, and any compiler failure then remains an input error.
    """
    target = validate_executable_capability_profile(profile)
    context = validate_executable_requirements(requirements)
    inventory = _dependency_inventory(raw, dependencies, catalog)
    complete = not inventory.missing
    plan = None
    if complete:
        # The public compiler independently reloads/admit sources and exact closure.
        plan = compile_package(raw, dependencies=dependencies, catalog=catalog)
    refusals = set()

    def emit(code, path):
        refusals.add((code, _safe_pointer(path)))

    if not complete:
        emit("dependency_missing", "/dependencies")
    if SCHEMA_VERSION not in target.schema_versions:
        emit("unsupported_schema", "/schema_version")
    if COMPILER_REVISION not in target.compiler_revisions:
        emit("unsupported_compiler", "/compiler_revision")
    if CANONICALIZATION not in target.canonicalizations:
        emit("unsupported_canonicalization", "/canonicalization")
    artifact_pins = {(*_identity(ref), ref.sha256) for ref in target.artifacts}
    operations = {
        (*_identity(item.connector_ref), item.connector_ref.sha256): item.operations
        for item in target.operations
    }
    configurations = {
        (*_identity(item.model_ref), item.model_ref.sha256): (
            item.configuration_revision,
            item.configuration_sha256,
        )
        for item in target.model_configurations
    }
    selected = (context.os, context.architecture)
    platform = next((p for p in target.platforms if (p.os, p.architecture) == selected), None)
    if platform is None:
        emit("unsupported_platform", "/requirements/os")
    capabilities = set(platform.capabilities) if platform is not None else set()
    for index, capability in enumerate(context.required_capabilities):
        if capability not in capabilities:
            emit("missing_capability", f"/requirements/required_capabilities/{index}")

    def require_operation(ref, operation, path):
        if operation not in operations.get((*_identity(ref), ref.sha256), ()):
            emit("unsupported_operation", path)

    def scan(source, prefix, connectors):
        for node, path in _walk_models(source, prefix):
            if isinstance(node, ArtifactRef):
                if (*_identity(node), node.sha256) not in artifact_pins:
                    emit("unsupported_artifact", path)
            if isinstance(node, _STEP_TYPES):
                if node.kind not in target.step_kinds:
                    emit("unsupported_step", path + "/kind")
                produced_kind = {
                    "action_intent": "action_intent",
                    "action_dispatch": "action_receipt",
                    "outcome_verification": "verification_evidence",
                    "parallel": "object",
                    "iteration": "array",
                    "timer": "null",
                }.get(node.kind)
                if node.kind == "human_task" and node.mode == "review":
                    produced_kind = "human_decision"
                if produced_kind is not None and produced_kind not in target.value_kinds:
                    emit("unsupported_value", path + "/kind")
            if isinstance(node, CallMapping):
                if node.fn not in target.expression_functions:
                    emit("unsupported_expression", path + "/fn")
                # Fixed result domains need support even in guards/intermediate
                # expressions with no explicit output schema. Arithmetic preserves
                # its declared operand domain; coalesce preserves its input types.
                result_kind = (
                    "string"
                    if node.fn == "concat"
                    else (
                        "boolean"
                        if node.fn
                        in ("eq", "ne", "lt", "le", "gt", "ge", "and", "or", "not", "exists")
                        else None
                    )
                )
                if result_kind is not None and result_kind not in target.value_kinds:
                    emit("unsupported_value", path + "/fn")
            if isinstance(node, IndexSource) and "integer" not in target.value_kinds:
                emit("unsupported_value", path + "/kind")
            if isinstance(
                node, _DATA_TYPES + (ObjectExport, ArrayExport, ObjectMapping, ArrayMapping)
            ):
                if node.kind not in target.value_kinds:
                    emit("unsupported_value", path + "/kind")
            if isinstance(node, HandleExport) and node.handle_kind not in target.value_kinds:
                emit("unsupported_value", path + "/kind")
            # Inspect each authored origin and compensation/read tool; a compiled
            # multi-origin dispatch operation=None never grants unconstrained support.
            if "connector_ref" in type(node).model_fields:
                ref = node.connector_ref
                if type(ref) is str:
                    ref = connectors[ref]
                for field in ("operation", "read_operation"):
                    if field in type(node).model_fields:
                        require_operation(ref, getattr(node, field), path + "/" + field)

    root_key = (inventory.root.package_id, inventory.root.version)
    ordered_packages = [(root_key, inventory.root)] + [
        (key, package) for key, package in inventory.packages.items() if key != root_key
    ]
    for index, (_, package) in enumerate(ordered_packages):
        prefix = "" if index == 0 else f"/dependencies/{index - 1}"
        if selected not in {(p.os, p.architecture) for p in package.environment.platforms}:
            emit("unsupported_platform", prefix + "/environment/platforms")
        for offset, capability in enumerate(package.environment.required_capabilities):
            if capability not in capabilities:
                emit("missing_capability", f"{prefix}/environment/required_capabilities/{offset}")
        for name in PackageLimits.model_fields:
            if getattr(package.limits, name) > getattr(target.supported_limits, name):
                emit("insufficient_limit", prefix + "/limits/" + name)
        scan(package, prefix, {d.id: d.ref for d in package.connectors})
    for index, (key, document) in enumerate(inventory.catalog.items()):
        prefix = f"/catalog/documents/{index}"
        scan(document, prefix, {})
        pin = (*key, inventory.catalog_digests[key])
        if isinstance(document.definition, ConnectorDefinition):
            for offset, operation in enumerate(document.definition.operations):
                if operation.id not in operations.get(pin, ()):
                    emit("unsupported_operation", f"{prefix}/definition/operations/{offset}")
        if isinstance(document.definition, ModelDefinition):
            definition = document.definition
            if configurations.get(pin) != (
                definition.configuration_revision,
                definition.configuration_sha256,
            ):
                emit("unsupported_model_configuration", prefix + "/definition")
    if plan is not None:
        prefixes = {
            inventory.package_digests[key]: "" if index == 0 else f"/dependencies/{index - 1}"
            for index, (key, _) in enumerate(ordered_packages)
        }
        # Include implicit output values (for example a discarded parallel object)
        # without using operation=None or lossy plan pins as a support shortcut.
        for entry in [*plan.steps, *plan.regions]:
            prefix = prefixes[entry.package_sha256] + entry.source_pointer
            for node, _ in _walk_models(entry.output_type):
                kind = None
                if isinstance(node, _DATA_TYPES + (PlanArray, PlanObject)):
                    kind = node.kind
                elif isinstance(node, (PlanHandle, HandleExport)):
                    kind = node.handle_kind
                if kind is not None and kind not in target.value_kinds:
                    emit("unsupported_value", prefix + "/output")
    reasons = sorted(refusals)
    return validate_executable_compatibility_report(
        ExecutableCompatibilityReport(
            closure_complete=complete,
            compilation_status="valid" if complete else "unresolved",
            compatible=not refusals,
            package_sha256=executable_digest(inventory.root),
            profile_sha256=sha256_hex(canonical_capability_profile_json(target)),
            requirements_sha256=sha256_hex(canonical_requirements_json(context)),
            reasons=[
                ExecutableCompatibilityReason(code=code, path=path) for code, path in reasons[:20]
            ],
            reasons_truncated=len(reasons) > 20,
        )
    )
