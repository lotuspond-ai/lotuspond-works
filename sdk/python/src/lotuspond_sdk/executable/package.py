"""Closed executable authoring records. These declarations do not execute work."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from ..contract import Identifier, ReferenceRevision, Version
from ._validation import StrictModel
from .errors import ExecutableError
from .expressions import MAPPING_MODELS, Mapping, RefMapping
from .types import DataSchema, ExportSchema, NamedSchema

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Approval = Literal["none", "human"]
ArtifactKind = Literal[
    "workflow", "connector", "model", "agent", "evaluation", "policy", "knowledge", "artifact"
]


def unique(values) -> None:
    if len(values) != len(set(values)):
        raise ExecutableError("duplicate_identifier")


class ArtifactRef(StrictModel):
    kind: ArtifactKind
    name: Identifier
    version: Version
    sha256: Digest


class Declaration(StrictModel):
    id: Identifier
    ref: ArtifactRef


class ConnectorDeclaration(Declaration):
    credential_ref: Identifier | None = None


class KnowledgeDeclaration(Declaration):
    required_freshness_seconds: int = Field(ge=1, le=31536000)
    allowed_classifications: list[Identifier] = Field(max_length=64)
    citation_schema: DataSchema


class RetryPolicy(StrictModel):
    max_attempts: int = Field(ge=1, le=10)
    timeout_seconds: int = Field(ge=1, le=86400)
    backoff_seconds: int = Field(ge=0, le=3600)
    retry_codes: list[Literal["unavailable", "rate_limited", "timeout_before_dispatch"]] = Field(
        max_length=3
    )


class SpendLimits(StrictModel):
    max_input_tokens: int = Field(ge=0, le=10000000)
    max_output_tokens: int = Field(ge=0, le=10000000)
    max_cost_microusd: int = Field(ge=0, le=1000000000000)


class PackageLimits(SpendLimits):
    max_steps: int = Field(ge=1, le=10000)
    active_seconds: int = Field(ge=1, le=86400)
    elapsed_seconds: int = Field(ge=1, le=31536000)
    retained_waits: int = Field(ge=0, le=10000)


class AgentLimits(SpendLimits):
    max_steps: int = Field(ge=1, le=1000)
    timeout_seconds: int = Field(ge=1, le=3600)


class Platform(StrictModel):
    os: Identifier
    architecture: Identifier


class Environment(StrictModel):
    runtime_contract: Literal["lotuspond.runtime-contract.v1"]
    platforms: list[Platform] = Field(max_length=64)
    required_capabilities: list[Identifier] = Field(max_length=64)
    destination_classes: list[Identifier] = Field(max_length=64)
    operation_classes: list[Identifier] = Field(max_length=64)


class Assurance(StrictModel):
    evaluation_refs: list[Identifier] = Field(max_length=64)
    outcome_rule_refs: list[Identifier] = Field(max_length=64)
    required_classification: Identifier


class ComparisonRule(StrictModel):
    op: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    readback_pointer: str = Field(max_length=512)
    expected: Mapping


class PresentRule(StrictModel):
    op: Literal["present"]
    readback_pointer: str = Field(max_length=512)


class LogicalRule(StrictModel):
    op: Literal["and", "or"]
    rules: list[Rule] = Field(min_length=2, max_length=63)


Rule: TypeAlias = Annotated[ComparisonRule | PresentRule | LogicalRule, Field(discriminator="op")]


class VerificationSpec(StrictModel):
    connector_ref: Identifier
    read_operation: Identifier
    target: Mapping
    input: Mapping
    rule_id: Identifier
    version: ReferenceRevision
    rules: list[Rule] = Field(min_length=1, max_length=64)


class CompensationSpec(StrictModel):
    connector_ref: Identifier
    operation: Identifier
    target: Mapping
    input: Mapping
    policy_ref: Identifier
    approval: Approval
    independent_review: bool
    expiry_seconds: int = Field(ge=1, le=2592000)
    retry: RetryPolicy
    verification: VerificationSpec


class ReadTool(StrictModel):
    connector_ref: Identifier
    operation: Identifier
    retry: RetryPolicy


class TransformStep(StrictModel):
    id: Identifier
    kind: Literal["transform"]
    mapping: Mapping
    output_schema: DataSchema


class DecisionStep(StrictModel):
    id: Identifier
    kind: Literal["decision"]
    condition: Mapping
    then_body: Body
    else_body: Body


class Branch(StrictModel):
    id: Identifier
    body: Body


class ParallelStep(StrictModel):
    id: Identifier
    kind: Literal["parallel"]
    branches: list[Branch] = Field(min_length=1, max_length=64)
    join: Literal["all"]


class IterationStep(StrictModel):
    id: Identifier
    kind: Literal["iteration"]
    input: Mapping
    max_items: int = Field(ge=1, le=1000)
    concurrency: int = Field(ge=1, le=64)
    body: Body

    @model_validator(mode="after")
    def bounded_concurrency(self):
        if self.concurrency > self.max_items:
            raise ValueError("Concurrency exceeds item count.")
        return self


class ConnectorReadStep(StrictModel):
    id: Identifier
    kind: Literal["connector_read"]
    connector_ref: Identifier
    operation: Identifier
    target: Mapping
    input: Mapping
    retry: RetryPolicy


class AgentStep(StrictModel):
    id: Identifier
    kind: Literal["agent"]
    model_ref: Identifier | None = None
    agent_ref: Identifier | None = None
    instructions: str = Field(min_length=1, max_length=16384)
    output_schema: DataSchema
    read_tools: list[ReadTool] = Field(default_factory=list, max_length=32)
    knowledge_refs: list[Identifier] = Field(default_factory=list, max_length=64)
    limits: AgentLimits
    retry: RetryPolicy

    @model_validator(mode="after")
    def one_source(self):
        if (self.model_ref is None) == (self.agent_ref is None):
            raise ValueError("Exactly one model or reusable agent is required.")
        return self


class ActionIntentStep(StrictModel):
    id: Identifier
    kind: Literal["action_intent"]
    connector_ref: Identifier
    operation: Identifier
    target: Mapping
    input: Mapping
    policy_ref: Identifier
    approval: Approval
    independent_review: bool
    expiry_seconds: int = Field(ge=1, le=2592000)


class HumanTaskStep(StrictModel):
    id: Identifier
    kind: Literal["human_task"]
    assignment_policy_ref: Identifier
    timeout_seconds: int = Field(ge=1, le=31536000)
    escalation_policy_ref: Identifier | None = None
    mode: Literal["form", "review"]
    form_schema: DataSchema | None = None
    intent: RefMapping | None = None

    @model_validator(mode="after")
    def mode_payload(self):
        if self.mode == "form":
            if self.form_schema is None or self.intent is not None:
                raise ValueError("Form requires only a data schema.")
        elif self.intent is None or self.form_schema is not None:
            raise ValueError("Review requires only an intent reference.")
        return self


class ActionDispatchStep(StrictModel):
    id: Identifier
    kind: Literal["action_dispatch"]
    intent: RefMapping
    decision: RefMapping | None = None
    retry: RetryPolicy
    compensation: CompensationSpec | None = None


class TimerStep(StrictModel):
    id: Identifier
    kind: Literal["timer"]
    delay_seconds: int = Field(ge=1, le=31536000)


class ExternalWaitStep(StrictModel):
    id: Identifier
    kind: Literal["external_wait"]
    signal_schema: DataSchema
    correlation: Mapping
    expiry_seconds: int = Field(ge=1, le=31536000)
    assignment_policy_ref: Identifier
    escalation_policy_ref: Identifier | None = None


class SubworkflowStep(StrictModel):
    id: Identifier
    kind: Literal["subworkflow"]
    workflow_ref: Identifier
    input: Mapping


class OutcomeVerificationStep(VerificationSpec):
    id: Identifier
    kind: Literal["outcome_verification"]
    receipt: RefMapping


Step: TypeAlias = Annotated[
    TransformStep
    | DecisionStep
    | ParallelStep
    | IterationStep
    | ConnectorReadStep
    | AgentStep
    | ActionIntentStep
    | HumanTaskStep
    | ActionDispatchStep
    | TimerStep
    | ExternalWaitStep
    | SubworkflowStep
    | OutcomeVerificationStep,
    Field(discriminator="kind"),
]


class Body(StrictModel):
    steps: list[Step] = Field(max_length=128)
    result: Mapping
    result_schema: ExportSchema


class ExecutablePackage(StrictModel):
    schema_version: Literal["lotuspond.workflow-package.v2"]
    package_id: Identifier
    version: Version
    name: str = Field(min_length=1, max_length=160)
    input_schema: DataSchema
    output_schema: DataSchema
    schemas: list[NamedSchema] = Field(max_length=64)
    body: Body
    models: list[Declaration] = Field(max_length=64)
    connectors: list[ConnectorDeclaration] = Field(max_length=64)
    agents: list[Declaration] = Field(max_length=64)
    subworkflows: list[Declaration] = Field(max_length=64)
    evaluations: list[Declaration] = Field(max_length=64)
    policies: list[Declaration] = Field(max_length=64)
    knowledge: list[KnowledgeDeclaration] = Field(max_length=64)
    artifacts: list[Declaration] = Field(max_length=64)
    secret_refs: list[Identifier] = Field(max_length=64)
    environment: Environment
    limits: PackageLimits
    assurance: Assurance

    @model_validator(mode="before")
    @classmethod
    def preflight(cls, raw):
        from .validation import preflight_package

        preflight_package(raw)
        return raw


PACKAGE_MODELS = (
    *MAPPING_MODELS,
    ArtifactRef,
    Declaration,
    ConnectorDeclaration,
    KnowledgeDeclaration,
    RetryPolicy,
    SpendLimits,
    PackageLimits,
    AgentLimits,
    Platform,
    Environment,
    Assurance,
    ComparisonRule,
    PresentRule,
    LogicalRule,
    VerificationSpec,
    CompensationSpec,
    ReadTool,
    TransformStep,
    DecisionStep,
    Branch,
    ParallelStep,
    IterationStep,
    ConnectorReadStep,
    AgentStep,
    ActionIntentStep,
    HumanTaskStep,
    ActionDispatchStep,
    TimerStep,
    ExternalWaitStep,
    SubworkflowStep,
    OutcomeVerificationStep,
    Body,
    ExecutablePackage,
)
for _model in PACKAGE_MODELS:
    _model.model_rebuild()
