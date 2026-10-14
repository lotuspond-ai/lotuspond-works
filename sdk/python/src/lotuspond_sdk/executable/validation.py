"""Bounded structural admission. Lexical compilation and authority are separate."""

from __future__ import annotations

from pydantic import ValidationError

from ._canonical import jcs_bytes, sha256_hex
from ._validation import StrictModel, validate_model
from .errors import ExecutableError, safe_validation_issues
from .expressions import (
    ArrayMapping,
    CallMapping,
    LiteralMapping,
    ObjectMapping,
    RefMapping,
    _tokens,
    parse_mapping,
)
from .input import project_json
from .package import (
    PACKAGE_MODELS,
    AgentStep,
    ArtifactRef,
    Body,
    ComparisonRule,
    DecisionStep,
    ExecutablePackage,
    LogicalRule,
    PresentRule,
    unique,
)
from .types import (
    ArrayExport,
    ArraySchema,
    BooleanSchema,
    DataExport,
    DecimalSchema,
    HandleExport,
    IntegerSchema,
    NullSchema,
    NumberSchema,
    ObjectExport,
    ObjectSchema,
    RefSchema,
    StringSchema,
    parse_export,
    resolve_schema,
    schema_definitions,
)

_MAPPINGS = (LiteralMapping, RefMapping, ObjectMapping, ArrayMapping, CallMapping)
_SCHEMAS = (
    NullSchema,
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    StringSchema,
    DecimalSchema,
    ArraySchema,
    ObjectSchema,
    RefSchema,
)
_EXPORTS = (DataExport, HandleExport, ObjectExport, ArrayExport)
_RULES = (ComparisonRule, PresentRule, LogicalRule)
_FAMILIES = {
    "models": "model",
    "connectors": "connector",
    "agents": "agent",
    "subworkflows": "workflow",
    "evaluations": "evaluation",
    "policies": "policy",
    "knowledge": "knowledge",
    "artifacts": "artifact",
}


def _raw_rules(raw):
    """Budget each rule tree before recursive Pydantic validation, without entering literals."""
    stack = [raw]
    while stack:
        value = stack.pop()
        if type(value) is list:
            stack.extend(value)
        elif type(value) is dict:
            if value.get("kind") == "literal":
                continue
            if value.get("op") in ("eq", "ne", "lt", "le", "gt", "ge", "present", "and", "or"):
                pending = [(value, 1)]
                count = 0
                while pending:
                    rule, depth = pending.pop()
                    count += 1
                    if count > 64 or depth > 8:
                        raise ExecutableError("invalid_schema")
                    if type(rule) is dict and rule.get("op") in ("and", "or"):
                        children = rule.get("rules")
                        if type(children) is list:
                            pending.extend((child, depth + 1) for child in children)
                # Rule expected mappings may contain literals, not more rules.
            else:
                stack.extend(value.values())


def preflight_package(raw):
    if type(raw) is not dict:
        return
    _schema_count(raw)
    count = 0
    pending = [(raw.get("body"), 0)]
    while pending:
        body, depth = pending.pop()
        if type(body) is not dict or type(body.get("steps")) is not list:
            continue
        for step in body["steps"]:
            count += 1
            if count > 128:
                raise ExecutableError("invalid_package")
            if type(step) is not dict:
                continue
            kind = step.get("kind")
            if kind in ("decision", "parallel", "iteration"):
                if depth >= 8:
                    raise ExecutableError("invalid_package")
                if kind == "decision":
                    pending.extend((step.get(key), depth + 1) for key in ("then_body", "else_body"))
                elif kind == "iteration":
                    pending.append((step.get("body"), depth + 1))
                elif type(step.get("branches")) is list:
                    pending.extend(
                        (b.get("body"), depth + 1) for b in step["branches"] if type(b) is dict
                    )
    _raw_rules(raw)


def _schema_count(raw):
    if type(raw) is dict and type(raw.get("schemas")) is list and len(raw["schemas"]) > 64:
        raise ExecutableError("invalid_schema")


def preflight_catalog(raw):
    _schema_count(raw)
    if type(raw) is dict and type(raw.get("documents")) is list:
        for document in raw["documents"]:
            _schema_count(document)
    _raw_rules(raw)


def _rule_budget(rules):
    pending = [(r, 1) for r in rules]
    count = 0
    while pending:
        rule, depth = pending.pop()
        count += 1
        if count > 64 or depth > 8:
            raise ExecutableError("invalid_schema")
        if isinstance(rule, LogicalRule):
            pending.extend((r, depth + 1) for r in rule.rules)
        else:
            _tokens(rule.readback_pointer)


def normalize_tree(root, definitions):
    """Normalize every declared literal domain before the enclosing final JCS check.

    Retain authored schema references, checking them against this document's local
    definitions. Never model_dump untrusted input or use caller serializers.
    """
    schema_definitions(definitions)

    def visit(value):
        if isinstance(value, _MAPPINGS):
            return parse_mapping(value, definitions)
        if isinstance(value, _SCHEMAS):
            resolve_schema(value, definitions)
            return value
        if isinstance(value, _EXPORTS):
            parse_export(value, definitions)
            return value
        if isinstance(value, StrictModel):
            if isinstance(value, _RULES):
                _rule_budget([value])
            for name in type(value).model_fields:
                child = getattr(value, name)
                if type(child) is list and child and all(isinstance(r, _RULES) for r in child):
                    _rule_budget(child)
                setattr(value, name, visit(child))
        elif type(value) is list:
            return [visit(child) for child in value]
        return value

    return visit(root)


def _walk(root):
    pending = [root]
    while pending:
        item = pending.pop()
        yield item
        # Literal JSON is data, never a place to interpret step/reference-like objects.
        if isinstance(item, LiteralMapping):
            continue
        if isinstance(item, StrictModel):
            pending.extend(getattr(item, name) for name in type(item).model_fields)
        elif type(item) is list:
            pending.extend(item)


def check_unique_tree(root):
    for node in _walk(root):
        if not isinstance(node, StrictModel):
            continue
        for name in (
            "allowed_classifications",
            "retry_codes",
            "required_capabilities",
            "destination_classes",
            "operation_classes",
            "evaluation_refs",
            "outcome_rule_refs",
            "knowledge_refs",
            "allowed_operation_classes",
            "allowed_capability_classes",
        ):
            if name in type(node).model_fields:
                unique(getattr(node, name))
        if "platforms" in type(node).model_fields:
            unique([(p.os, p.architecture) for p in node.platforms])
        for name, key in (("branches", "id"), ("operations", "id")):
            if name in type(node).model_fields:
                unique([getattr(v, key) for v in getattr(node, name)])
        if "rules" in type(node).model_fields and node.rules:
            named = [r.rule_id for r in node.rules if "rule_id" in type(r).model_fields]
            unique(named)
        if "read_tools" in type(node).model_fields:
            keys = []
            for tool in node.read_tools:
                ref = tool.connector_ref
                identity = (
                    (ref.kind, ref.name, ref.version, ref.sha256)
                    if isinstance(ref, ArtifactRef)
                    else ref
                )
                keys.append((identity, tool.operation))
            unique(keys)


def shape_model(projected, model_type, code):
    """Shape-only pass; domain checks run outside Pydantic to retain fixed codes."""
    try:
        return model_type.model_validate(projected)
    except ValidationError as error:
        issues, truncated = safe_validation_issues(error, code=code)
        raise ExecutableError(code, issues=issues, truncated=truncated) from None


def check_package(package: ExecutablePackage):
    normalize_tree(package, package.schemas)
    check_unique_tree(package)
    declarations = {}
    pinned = {}
    for family, kind in _FAMILIES.items():
        entries = getattr(package, family)
        unique([entry.id for entry in entries])
        declarations[family] = {entry.id for entry in entries}
        for entry in entries:
            if entry.ref.kind != kind:
                raise ExecutableError("invalid_package")
            key = (kind, entry.ref.name, entry.ref.version)
            if key in pinned and pinned[key] != entry.ref.sha256:
                raise ExecutableError("dependency_conflict")
            pinned[key] = entry.ref.sha256
    unique(package.secret_refs)
    for entry in package.connectors:
        if entry.credential_ref is not None and entry.credential_ref not in package.secret_refs:
            raise ExecutableError("unknown_reference")
    all_nodes = list(_walk(package.body))
    bodies = [node for node in all_nodes if isinstance(node, Body)]
    steps = [step for body in bodies for step in body.steps]
    unique([step.id for step in steps])
    step_ids = {step.id for step in steps}
    refs = {
        "connector_ref": "connectors",
        "policy_ref": "policies",
        "assignment_policy_ref": "policies",
        "escalation_policy_ref": "policies",
        "workflow_ref": "subworkflows",
        "model_ref": "models",
        "agent_ref": "agents",
    }
    for node in all_nodes:
        if isinstance(node, RefMapping) and node.source.kind == "step":
            if node.source.id not in step_ids:
                raise ExecutableError("unknown_reference")
        if isinstance(node, StrictModel) and not isinstance(node, ArtifactRef):
            for slot, family in refs.items():
                if slot in type(node).model_fields:
                    value = getattr(node, slot)
                    if value is not None and value not in declarations[family]:
                        raise ExecutableError("unknown_reference")
        if isinstance(node, AgentStep):
            if not set(node.knowledge_refs) <= declarations["knowledge"]:
                raise ExecutableError("unknown_reference")
        if isinstance(node, DecisionStep):
            if parse_export(node.then_body.result_schema, package.schemas) != parse_export(
                node.else_body.result_schema, package.schemas
            ):
                raise ExecutableError("type_mismatch")
    if not set(package.assurance.evaluation_refs) <= declarations["evaluations"]:
        raise ExecutableError("unknown_reference")
    rule_ids = {
        node.rule_id for node in all_nodes if "rule_id" in getattr(type(node), "model_fields", {})
    }
    if not set(package.assurance.outcome_rule_refs) <= rule_ids:
        raise ExecutableError("unknown_reference")


def validate_executable_package(raw: object) -> ExecutablePackage:
    """Validate structure/local references. Does not resolve supplied artifact closure."""
    projected = project_json(raw, model_types=PACKAGE_MODELS)
    preflight_package(projected)
    package = shape_model(projected, ExecutablePackage, "invalid_package")
    check_package(package)
    return validate_model(
        package, model_type=ExecutablePackage, model_types=PACKAGE_MODELS, code="invalid_package"
    )


def canonical_executable_json(raw: object) -> bytes:
    package = validate_executable_package(raw)
    return jcs_bytes(project_json(package, model_types=PACKAGE_MODELS))


def executable_digest(raw: object) -> str:
    return sha256_hex(canonical_executable_json(raw))
