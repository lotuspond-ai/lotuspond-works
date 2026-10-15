"""Bounded source-bound plans. Metadata is not an execution authorization."""

from __future__ import annotations

import warnings
from typing import Annotated, Literal

from pydantic import Field

from ..contract import Identifier, Version
from ._canonical import jcs_bytes, sha256_hex
from ._validation import StrictModel, validate_model
from .errors import ExecutableError
from .input import REPORT_LIMITS, project_json
from .package import PACKAGE_MODELS, ArtifactRef, Digest, Environment, PackageLimits
from .types import (
    SCHEMA_MODELS,
    DataSchema,
    DataType,
    ExportSchema,
    FlowArrayType,
    FlowObjectType,
    HandleType,
    OriginChoice,
    SourceSymbol,
    validate_flow,
)


class Binding(StrictModel):
    owner: Identifier
    path: list[str]


class Symbol(StrictModel):
    kind: Literal["source"]
    operation: Literal["action_intent", "human_review", "action_dispatch", "verification"]
    step_id: Identifier
    occurrence_path: list[str]
    occurrence_bindings: list[Binding]


class Choice(StrictModel):
    kind: Literal["choice"]
    options: list[Origin | None]
    selection_path: list[str]
    selection_bindings: list[Binding]


Origin = Annotated[Symbol | Choice, Field(discriminator="kind")]


with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore", message=r"Field name .* shadows an attribute", category=UserWarning
    )

    class PlanData(StrictModel):
        kind: Literal["data"]
        schema: DataSchema
        may_be_missing: bool


class PlanHandle(StrictModel):
    kind: Literal["handle"]
    handle_kind: Literal[
        "action_intent", "human_decision", "action_receipt", "verification_evidence"
    ]
    source: Origin
    intent_source: Origin | None
    may_be_missing: bool


class PlanField(StrictModel):
    name: str
    value: PlanFlow


class PlanObject(StrictModel):
    kind: Literal["object"]
    fields: list[PlanField]
    may_be_missing: bool


class PlanArray(StrictModel):
    kind: Literal["array"]
    items: PlanFlow | None
    max_items: int
    may_be_missing: bool
    elements: list[PlanFlow] | None
    item_schema: ExportSchema | None
    iteration_id: Identifier | None
    alternatives: list[PlanArray | None] | None
    selection_path: list[str]
    selection_bindings: list[Binding]


PlanFlow = Annotated[PlanData | PlanHandle | PlanObject | PlanArray, Field(discriminator="kind")]


class DefinitionDigest(StrictModel):
    kind: Literal[
        "workflow", "connector", "model", "agent", "evaluation", "policy", "knowledge", "artifact"
    ]
    name: Identifier
    version: Version
    sha256: Digest


class PlanStep(StrictModel):
    package_sha256: Digest
    step_id: Identifier
    region_id: str = Field(min_length=1, max_length=160)
    region_pointer: str = Field(min_length=1, max_length=512)
    source_pointer: str = Field(min_length=1, max_length=512)
    kind: Literal[
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
    source_sha256: Digest
    input_mapping_sha256: Digest
    output_type_sha256: Digest
    output_type: PlanFlow
    pins: list[ArtifactRef]
    operation: Identifier | None
    maximum_step_attempts: int = Field(ge=1, le=10000)


class PlanRegion(StrictModel):
    package_sha256: Digest
    region_id: str = Field(min_length=1, max_length=160)
    source_pointer: str = Field(min_length=1, max_length=512)
    kind: Literal["sequence"]
    step_ids: list[Identifier]
    result_mapping_sha256: Digest
    output_type: PlanFlow
    maximum_step_attempts: int = Field(ge=1, le=10000)


class PlanEdge(StrictModel):
    package_sha256: Digest
    source_pointer: str = Field(min_length=1, max_length=512)
    target_pointer: str = Field(min_length=1, max_length=512)
    kind: Literal["sequence", "then", "else", "branch", "iteration", "call"]
    target_package_sha256: Digest


class Requirement(StrictModel):
    package_sha256: Digest
    environment: Environment
    secret_refs: list[Identifier]


class OutcomeObligation(StrictModel):
    package_sha256: Digest
    dispatch: Symbol
    verified_by: list[Symbol]


class CompiledPlan(StrictModel):
    schema_version: Literal["lotuspond.compiled-workflow.v2"] = "lotuspond.compiled-workflow.v2"
    compiler_revision: Literal["lotuspond.workflow-compiler.v2.1"] = (
        "lotuspond.workflow-compiler.v2.1"
    )
    package_sha256: Digest
    dependency_digests: list[DefinitionDigest]
    catalog_digests: list[DefinitionDigest]
    steps: list[PlanStep]
    regions: list[PlanRegion]
    edges: list[PlanEdge]
    requirements: list[Requirement]
    limits: PackageLimits
    maximum_step_attempts: int = Field(ge=1, le=10000)
    outcome_obligations: list[OutcomeObligation]
    artifact_availability: Literal["not_checked"] = "not_checked"
    authority_granted: Literal[False] = False


PLAN_MODELS = (
    Binding,
    Symbol,
    Choice,
    PlanData,
    PlanHandle,
    PlanField,
    PlanObject,
    PlanArray,
    DefinitionDigest,
    PlanStep,
    PlanRegion,
    PlanEdge,
    Requirement,
    OutcomeObligation,
    CompiledPlan,
    *PACKAGE_MODELS,
    *SCHEMA_MODELS,
)
for _model in PLAN_MODELS:
    _model.model_rebuild()


def origin_projection(origin):
    def bindings(values):
        return [{"owner": owner, "path": list(path)} for owner, path in values]

    if type(origin) is SourceSymbol:
        return {
            "kind": "source",
            "operation": origin.operation,
            "step_id": origin.step_id,
            "occurrence_path": list(origin.occurrence_path),
            "occurrence_bindings": bindings(origin.occurrence_bindings),
        }
    if type(origin) is OriginChoice:
        return {
            "kind": "choice",
            "options": [None if x is None else origin_projection(x) for x in origin.options],
            "selection_path": list(origin.selection_path),
            "selection_bindings": bindings(origin.selection_bindings),
        }
    raise ExecutableError("invalid_mapping")


def flow_projection(raw):
    """Project the full checked flow, including ordered owners and empty alternatives."""
    flow = validate_flow(raw)
    common = {"may_be_missing": flow.may_be_missing}
    if type(flow) is DataType:
        return {
            "kind": "data",
            "schema": project_json(flow.schema, model_types=SCHEMA_MODELS),
            **common,
        }
    if type(flow) is HandleType:
        return {
            "kind": "handle",
            "handle_kind": flow.handle_kind,
            "source": origin_projection(flow.source),
            "intent_source": None
            if flow.intent_source is None
            else origin_projection(flow.intent_source),
            **common,
        }
    if type(flow) is FlowObjectType:
        return {
            "kind": "object",
            "fields": [{"name": k, "value": flow_projection(v)} for k, v in flow.fields.items()],
            **common,
        }
    return {
        "kind": "array",
        "items": None if flow.items is None else flow_projection(flow.items),
        "max_items": flow.max_items,
        "elements": None if flow.elements is None else [flow_projection(x) for x in flow.elements],
        "item_schema": None
        if flow.item_schema is None
        else project_json(flow.item_schema, model_types=SCHEMA_MODELS),
        "iteration_id": flow.iteration_id,
        "alternatives": None
        if flow.alternatives is None
        else [None if x is None else flow_projection(x) for x in flow.alternatives],
        "selection_path": list(flow.selection_path),
        "selection_bindings": [
            {"owner": owner, "path": list(path)} for owner, path in flow.selection_bindings
        ],
        **common,
    }


def _origin_from(raw):
    if raw.kind == "source":
        return SourceSymbol(
            raw.operation,
            raw.step_id,
            tuple(raw.occurrence_path),
            tuple((x.owner, tuple(x.path)) for x in raw.occurrence_bindings),
        )
    return OriginChoice(
        tuple(None if x is None else _origin_from(x) for x in raw.options),
        tuple(raw.selection_path),
        tuple((x.owner, tuple(x.path)) for x in raw.selection_bindings),
    )


def _flow_from(raw):
    if raw.kind == "data":
        return DataType(raw.schema, raw.may_be_missing)
    if raw.kind == "handle":
        return HandleType(
            raw.handle_kind,
            _origin_from(raw.source),
            raw.may_be_missing,
            None if raw.intent_source is None else _origin_from(raw.intent_source),
        )
    if raw.kind == "object":
        if len({x.name for x in raw.fields}) != len(raw.fields):
            raise ExecutableError("invalid_mapping")
        return FlowObjectType({x.name: _flow_from(x.value) for x in raw.fields}, raw.may_be_missing)
    return FlowArrayType(
        None if raw.items is None else _flow_from(raw.items),
        raw.max_items,
        raw.may_be_missing,
        None if raw.elements is None else tuple(_flow_from(x) for x in raw.elements),
        raw.item_schema,
        raw.iteration_id,
        None
        if raw.alternatives is None
        else tuple(None if x is None else _flow_from(x) for x in raw.alternatives),
        tuple(raw.selection_path),
        tuple((x.owner, tuple(x.path)) for x in raw.selection_bindings),
    )


def canonical_plan_json(plan: object) -> bytes:
    checked = validate_model(
        plan,
        model_type=CompiledPlan,
        model_types=PLAN_MODELS,
        code="invalid_package",
        limits=REPORT_LIMITS,
    )
    for entry in [*checked.steps, *checked.regions]:
        normalized = flow_projection(validate_flow(_flow_from(entry.output_type)))
        if normalized != project_json(
            entry.output_type, model_types=PLAN_MODELS, limits=REPORT_LIMITS
        ):
            raise ExecutableError("invalid_mapping")
        if type(entry) is PlanStep and entry.output_type_sha256 != sha256_hex(
            jcs_bytes(
                project_json(entry.output_type, model_types=PLAN_MODELS, limits=REPORT_LIMITS)
            )
        ):
            raise ExecutableError("invalid_package")
    for obligation in checked.outcome_obligations:
        if (
            obligation.dispatch.operation != "action_dispatch"
            or not obligation.verified_by
            or any(x.operation != "verification" for x in obligation.verified_by)
        ):
            raise ExecutableError("invalid_mapping")
        _origin_from(obligation.dispatch)
        for origin in obligation.verified_by:
            _origin_from(origin)
    return jcs_bytes(
        project_json(checked, model_types=PLAN_MODELS, limits=REPORT_LIMITS),
        max_bytes=REPORT_LIMITS.max_bytes,
    )


def compiled_plan_digest(plan: object) -> str:
    return sha256_hex(canonical_plan_json(plan))
