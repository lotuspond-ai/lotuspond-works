"""Admission rules for E05 effect packages, applied at publication, deployment and load.

Pure checks over validated SDK packages and catalog documents. A plan that uses any
E05 feature (an effect step, a review task, or a form with E05 assignment or
escalation) pins `lotuspond.execution-interpreter.v2`; every other plan keeps v1 and
E04's rules. Policies use a closed, operator-owned vocabulary. Nothing here grants
authority: every intent, decision and dispatch is checked again when it happens.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from lotuspond_sdk.executable.catalog import CATALOG_MODELS
from lotuspond_sdk.executable.errors import ExecutableError
from lotuspond_sdk.executable.input import project_json
from lotuspond_sdk.executable.package import (
    PACKAGE_MODELS,
    ActionDispatchStep,
    ActionIntentStep,
    DecisionStep,
    ExternalWaitStep,
    HumanTaskStep,
    IterationStep,
    OutcomeVerificationStep,
    ParallelStep,
)
from lotuspond_sdk.executable.types import DataType, assert_assignable, parse_export, resolve_schema

from .action_contracts import ASSIGNMENT_ROLES, ESCALATIONS, INTERPRETER_V2
from .engine_contracts import INTERPRETER_V1
from .errors import ExecutionError

EFFECT_STEPS = (ActionIntentStep, ActionDispatchStep, OutcomeVerificationStep)
V1_FORM_ASSIGNMENT = "workspace_admins"


@dataclass(frozen=True)
class PlanFeatures:
    effects: bool
    review: bool
    e05_forms: bool

    @property
    def any(self) -> bool:
        return self.effects or self.review or self.e05_forms


@dataclass(frozen=True)
class Closure:
    """A root package with its workflow packages and catalog documents by digest."""

    packages: tuple  # validated ExecutablePackage, the root first
    catalog: dict  # package_sha256 -> validated catalog document


def steps(body):
    """Every executable step at every nesting level; mappings stay customer data."""
    for step in body.steps:
        yield step
        if isinstance(step, DecisionStep):
            yield from steps(step.then_body)
            yield from steps(step.else_body)
        elif isinstance(step, IterationStep):
            yield from steps(step.body)
        elif isinstance(step, ParallelStep):
            for branch in step.branches:
                yield from steps(branch.body)


def _declared(package, kind, identifier):
    for declaration in getattr(package, kind):
        if declaration.id == identifier:
            return declaration.ref.sha256
    raise ExecutionError("dependency_unavailable")


def _document(closure, sha256, kind):
    document = closure.catalog.get(sha256)
    if document is None or document.kind != kind:
        raise ExecutionError("dependency_unavailable")
    return document


def _policy(closure, package, identifier):
    return _document(closure, _declared(package, "policies", identifier), "policy")


def classify_plan(closure: Closure) -> PlanFeatures:
    effects = review = e05_forms = False
    for package in closure.packages:
        for step in steps(package.body):
            if isinstance(step, EFFECT_STEPS):
                effects = True
            elif isinstance(step, HumanTaskStep):
                if step.mode == "review":
                    review = True
                else:
                    policy = _policy(closure, package, step.assignment_policy_ref)
                    # E05 assignment or escalation (the spec's definition of an E05 form).
                    if (
                        step.escalation_policy_ref is not None
                        or policy.definition.assignment != V1_FORM_ASSIGNMENT
                        or policy.definition.escalation is not None
                    ):
                        e05_forms = True
    return PlanFeatures(effects=effects, review=review, e05_forms=e05_forms)


def select_interpreter(features: PlanFeatures) -> str:
    return INTERPRETER_V2 if features.any else INTERPRETER_V1


def check_catalog_policy(document) -> None:
    """A published policy document uses only the closed vocabulary."""
    if document.kind != "policy":
        return
    definition = document.definition
    if definition.assignment not in ASSIGNMENT_ROLES or (
        definition.escalation is not None and definition.escalation not in ESCALATIONS
    ):
        raise ExecutionError("unsupported_policy")


def e04_form_policy(definition) -> bool:
    """E04's one implemented assignment, kept whole for v1 forms and external waits."""
    return (
        definition.assignment == V1_FORM_ASSIGNMENT
        and definition.escalation is None
        and definition.approval == "human"
        and definition.independent_review
    )


def check_policy(
    document, *, use: Literal["action", "review", "form", "escalation", "external_wait"]
) -> None:
    """Refuse a policy that this use cannot honour, with `unsupported_policy`."""
    if document.kind != "policy":
        raise ExecutionError("unsupported_policy")
    check_catalog_policy(document)
    definition = document.definition
    if use in {"review", "escalation"} and definition.approval != "human":
        raise ExecutionError("unsupported_policy")
    if use == "escalation" and definition.escalation is not None:
        raise ExecutionError("unsupported_policy")  # an escalation group does not escalate
    if use == "external_wait" and not e04_form_policy(definition):
        raise ExecutionError("unsupported_policy")  # E04's rule; E05 does not extend it


def check_plan(closure: Closure, features: PlanFeatures) -> None:
    """Every step's policies and refusals, under the interpreter the features select."""
    v2 = features.any
    for package in closure.packages:
        for step in steps(package.body):
            if isinstance(step, ActionDispatchStep) and step.compensation is not None:
                raise ExecutionError("unsupported_step")  # owner decision 14
            if isinstance(step, ActionIntentStep):
                policy = _policy(closure, package, step.policy_ref)
                check_policy(policy, use="action")
                if (
                    step.approval != policy.definition.approval
                    or step.independent_review != policy.definition.independent_review
                ):
                    raise ExecutionError("unsupported_policy")
            elif isinstance(step, HumanTaskStep):
                policy = _policy(closure, package, step.assignment_policy_ref)
                check_policy(policy, use=step.mode)
                escalates = policy.definition.escalation is not None
                if escalates != (step.escalation_policy_ref is not None):
                    raise ExecutionError("unsupported_policy")  # both halves or neither
                if step.escalation_policy_ref is not None:
                    check_policy(
                        _policy(closure, package, step.escalation_policy_ref), use="escalation"
                    )
                if not v2 and (step.mode != "form" or not e04_form_policy(policy.definition)):
                    raise ExecutionError("unsupported_policy")
            elif isinstance(step, ExternalWaitStep):
                if step.escalation_policy_ref is not None:
                    raise ExecutionError("unsupported_policy")
                check_policy(
                    _policy(closure, package, step.assignment_policy_ref), use="external_wait"
                )


def _operation(document, identifier):
    for operation in document.definition.operations:
        if operation.id == identifier:
            return operation
    raise ExecutionError("unsupported_operation")


def _json(value):
    return project_json(value, model_types=(*PACKAGE_MODELS, *CATALOG_MODELS))


def _accepts(document, read_schema, write_schema) -> bool:
    """True when every value the write's schema admits is admitted by the read's."""
    definitions = document.schemas
    try:
        actual = DataType(resolve_schema(write_schema, definitions))
        expected = parse_export({"kind": "data", "schema": _json(read_schema)}, definitions)
        assert_assignable(actual, expected)
    except ExecutableError:
        return False
    return True


def write_operations(closure: Closure) -> dict[str, set[str]]:
    """Connector digest -> every operation an effect step needs: writes, read-backs, verifications."""
    required = defaultdict(set)
    for package in closure.packages:
        for step in steps(package.body):
            if isinstance(step, ActionIntentStep):
                sha = _declared(package, "connectors", step.connector_ref)
                document = _document(closure, sha, "connector")
                write = _operation(document, step.operation)
                if write.effect != "write" or write.reconciliation.mode != "read_back":
                    raise ExecutionError("unsupported_operation")
                required[sha] |= {write.id, write.reconciliation.read_operation}
            elif isinstance(step, OutcomeVerificationStep):
                sha = _declared(package, "connectors", step.connector_ref)
                document = _document(closure, sha, "connector")
                if _operation(document, step.read_operation).effect != "read":
                    raise ExecutionError("unsupported_operation")
                required[sha].add(step.read_operation)
    return required


def check_write_bindings(closure: Closure, connections: dict, catalog=None) -> None:
    """Every write is bound to a connection that pins its connector and allows it.

    `connections` maps a connector document digest to `(pin, operations)` of the
    deployment's bound connection version. The reconciliation read must accept the
    write's exact target and input (owner decision 18).
    """
    catalog = closure.catalog if catalog is None else catalog
    for sha, operations in write_operations(closure).items():
        document = catalog[sha]
        bound = connections.get(sha)
        if bound is None:
            raise ExecutionError("dependency_unavailable")
        pin, allowed = bound
        if (
            pin.name != document.name
            or pin.version != document.version
            or pin.artifact_sha256 != document.definition.artifact_sha256
            or not operations <= set(allowed)
        ):
            raise ExecutionError("dependency_unavailable")
        for write in document.definition.operations:
            if write.id not in operations or write.effect != "write":
                continue
            read = _operation(document, write.reconciliation.read_operation)
            if read.effect != "read" or not (
                _accepts(document, read.target_schema, write.target_schema)
                and _accepts(document, read.input_schema, write.input_schema)
            ):
                raise ExecutionError("unsupported_operation")


def unreviewed_operations(closure: Closure) -> tuple[str, ...]:
    """`connector/operation` of every write that would dispatch without review."""
    found = set()
    for package in closure.packages:
        for step in steps(package.body):
            if isinstance(step, ActionIntentStep) and step.approval == "none":
                sha = _declared(package, "connectors", step.connector_ref)
                document = _document(closure, sha, "connector")
                found.add(f"{document.name}/{step.operation}")
    return tuple(sorted(found))


def admit_closure(closure: Closure) -> str:
    """Classify, check and return the interpreter this closure pins."""
    features = classify_plan(closure)
    check_plan(closure, features)
    if features.any:
        write_operations(closure)  # every write is a declared, reconcilable operation
    return select_interpreter(features)
