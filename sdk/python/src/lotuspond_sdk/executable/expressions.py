"""Bounded lexical mapping inference. No evaluation or constant folding."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field, replace
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from ..contract import Identifier
from ._canonical import jcs_bytes
from ._validation import StrictModel
from .errors import ExecutableError
from .input import project_json
from .types import (
    SCHEMA_MODELS,
    BooleanSchema,
    DataSchema,
    DataType,
    DecimalSchema,
    ExportSchema,
    FlowArrayType,
    FlowObjectType,
    FlowType,
    IntegerSchema,
    Name,
    NamedSchema,
    StringSchema,
    _assign,
    _data_assign,
    _resolve,
    _value,
    merge_flow_elements,
    parse_export,
    parse_schema,
    schema_definitions,
    select_array_item,
    validate_flow,
)


class InputSource(StrictModel):
    kind: Literal["package_input"]


class StepSource(StrictModel):
    kind: Literal["step"]
    id: Identifier


class ItemSource(StrictModel):
    kind: Literal["iteration_item"]


class IndexSource(StrictModel):
    kind: Literal["iteration_index"]


Source: TypeAlias = Annotated[
    InputSource | StepSource | ItemSource | IndexSource, Field(discriminator="kind")
]


# The public JSON field deliberately replaces Pydantic's deprecated schema() API.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "schema" in ".*" shadows an attribute in parent "StrictModel"',
        category=UserWarning,
    )

    class LiteralMapping(StrictModel):
        kind: Literal["literal"]
        schema: DataSchema
        value: object


class RefMapping(StrictModel):
    kind: Literal["ref"]
    source: Source
    pointer: str = Field(max_length=512)


class MappingField(StrictModel):
    name: Name
    value: Mapping


class ObjectMapping(StrictModel):
    kind: Literal["object"]
    fields: list[MappingField] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("Duplicate field.")
        return self


class ArrayMapping(StrictModel):
    kind: Literal["array"]
    items: list[Mapping] = Field(max_length=1000)
    item_schema: ExportSchema


class CallMapping(StrictModel):
    kind: Literal["call"]
    fn: Literal[
        "concat",
        "add",
        "subtract",
        "multiply",
        "divide",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and",
        "or",
        "not",
        "exists",
        "coalesce",
    ]
    args: list[Mapping] = Field(max_length=63)
    scale: int | None = Field(default=None, ge=0, le=18)
    rounding: Literal["reject", "half_even", "floor", "ceiling"] | None = None


Mapping: TypeAlias = Annotated[
    LiteralMapping | RefMapping | ObjectMapping | ArrayMapping | CallMapping,
    Field(discriminator="kind"),
]
MAPPING_MODELS = (
    *SCHEMA_MODELS,
    InputSource,
    StepSource,
    ItemSource,
    IndexSource,
    LiteralMapping,
    RefMapping,
    MappingField,
    ObjectMapping,
    ArrayMapping,
    CallMapping,
)
for _model in MAPPING_MODELS:
    _model.model_rebuild()
_MAPPING = TypeAdapter(Mapping)


@dataclass(frozen=True, slots=True)
class ReferenceKey:
    source_kind: str
    step_id: str | None
    pointer: str


@dataclass(slots=True)
class TypeEnvironment:
    """Caller-supplied lexical scope, never a runtime trust or authorization context.

    The compiler exposes only earlier/joined outputs. A decision's true branch
    may add presence_guard(condition); false branches get no such refinement.
    """

    input_schema: DataSchema
    step_outputs: dict[str, FlowType] = field(default_factory=dict)
    iteration_item: FlowType | None = None
    schema_definitions: list[NamedSchema] = field(default_factory=list)
    present_refs: frozenset[ReferenceKey] = field(default_factory=frozenset)


def _tokens(pointer):
    if pointer == "":
        return []
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ExecutableError("invalid_mapping")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def _key(mapping):
    return ReferenceKey(mapping.source.kind, getattr(mapping.source, "id", None), mapping.pointer)


def parse_mapping(raw: object, definitions: list[NamedSchema] | None = None) -> Mapping:
    """Validate syntax, expression budgets and literal domains before final JCS."""
    projected = project_json(raw, model_types=MAPPING_MODELS)
    try:
        parsed = _MAPPING.validate_python(projected)
    except ValidationError:
        raise ExecutableError("invalid_mapping") from None
    named = schema_definitions([] if definitions is None else definitions)
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 64 or depth > 8:
            raise ExecutableError("invalid_mapping")
        if item.kind == "literal":
            resolved = _resolve(item.schema, named, ())
            item.value = _value(project_json(item.value), resolved)
        elif item.kind == "ref":
            _tokens(item.pointer)
        elif item.kind == "object":
            for child in item.fields:
                visit(child.value, depth + 1)
        elif item.kind == "array":
            # Validate all local references in declarations, even for empty arrays.
            parse_export(item.item_schema, [] if definitions is None else definitions)
            for child in item.items:
                visit(child, depth + 1)
        else:
            for child in item.args:
                visit(child, depth + 1)

    visit(parsed, 1)
    jcs_bytes(project_json(parsed, model_types=MAPPING_MODELS))
    return parsed


def presence_guard(raw: object) -> frozenset[ReferenceKey]:
    """Only a direct exists(ref) true decision edge proves presence, not truthiness."""
    mapping = parse_mapping(raw)
    if (
        mapping.kind == "call"
        and mapping.fn == "exists"
        and len(mapping.args) == 1
        and mapping.args[0].kind == "ref"
        and mapping.scale is None
        and mapping.rounding is None
    ):
        return frozenset({_key(mapping.args[0])})
    return frozenset()


def _environment(raw):
    if (
        type(raw) is not TypeEnvironment
        or type(raw.step_outputs) is not dict
        or len(raw.step_outputs) > 128
        or type(raw.present_refs) is not frozenset
    ):
        raise ExecutableError("invalid_mapping")
    named = schema_definitions(raw.schema_definitions)
    input_schema = _resolve(parse_schema(raw.input_schema), named, ())
    outputs = {}
    for name, flow in raw.step_outputs.items():
        try:
            TypeAdapter(Identifier).validate_python(name, strict=True)
        except ValidationError:
            raise ExecutableError("invalid_mapping") from None
        outputs[name] = validate_flow(flow, definitions=raw.schema_definitions)
    for key in raw.present_refs:
        if type(key) is not ReferenceKey:
            raise ExecutableError("invalid_mapping")
        source = {"kind": key.source_kind}
        if key.step_id is not None:
            source["id"] = key.step_id
        parse_mapping({"kind": "ref", "source": source, "pointer": key.pointer})
    return (
        input_schema,
        outputs,
        None
        if raw.iteration_item is None
        else validate_flow(raw.iteration_item, definitions=raw.schema_definitions),
        named,
    )


def _reference(mapping, environment, input_schema, outputs, iteration):
    source = mapping.source
    if source.kind == "package_input":
        flow = DataType(input_schema)
    elif source.kind == "step":
        if source.id not in outputs:
            raise ExecutableError("unknown_reference")
        flow = outputs[source.id]
    else:
        if iteration is None:
            raise ExecutableError("unavailable_value")
        flow = (
            iteration
            if source.kind == "iteration_item"
            else DataType(IntegerSchema(kind="integer", min=0, max=999))
        )
    tokens = _tokens(mapping.pointer)
    prefix = ""
    for token in tokens:
        parent_missing = flow.may_be_missing
        if isinstance(flow, DataType) and flow.schema.kind == "object":
            fields = {f.name: f for f in flow.schema.fields}
            if token not in fields:
                raise ExecutableError("unknown_reference")
            item = fields[token]
            flow = DataType(item.schema, not item.required)
        elif isinstance(flow, FlowObjectType):
            if token not in flow.fields:
                raise ExecutableError("unknown_reference")
            flow = flow.fields[token]
        elif (
            isinstance(flow, FlowArrayType)
            or isinstance(flow, DataType)
            and flow.schema.kind == "array"
        ):
            limit = flow.max_items if isinstance(flow, FlowArrayType) else flow.schema.max_items
            if not re.fullmatch(r"0|[1-9][0-9]*", token) or len(token) > 4 or int(token) >= limit:
                raise ExecutableError("unknown_reference")
            flow = select_array_item(flow, int(token))
        else:
            raise ExecutableError("type_mismatch")
        prefix += "/" + token.replace("~", "~0").replace("/", "~1")
        key = ReferenceKey(source.kind, getattr(source, "id", None), prefix)
        flow = replace(
            flow,
            may_be_missing=(parent_missing or flow.may_be_missing)
            and key not in environment.present_refs,
        )
    if _key(mapping) in environment.present_refs:
        flow = replace(flow, may_be_missing=False)
    return flow


def _call(mapping, args):
    fn = mapping.fn
    arity = 1 if fn in ("not", "exists") else 2
    if (fn == "concat" and not 2 <= len(args) <= 63) or (fn != "concat" and len(args) != arity):
        raise ExecutableError("invalid_mapping")
    if fn == "exists":
        if (
            mapping.args[0].kind != "ref"
            or mapping.scale is not None
            or mapping.rounding is not None
        ):
            raise ExecutableError("invalid_mapping")
        return DataType(BooleanSchema(kind="boolean"))
    if fn == "coalesce":
        if mapping.scale is not None or mapping.rounding is not None:
            raise ExecutableError("invalid_mapping")
        # Defaults are data only: a call cannot manufacture/select a handle origin.
        if not all(isinstance(arg, DataType) for arg in args) or args[1].may_be_missing:
            raise ExecutableError("type_mismatch")
        _data_assign(args[1].schema, args[0].schema)
        return replace(args[0], may_be_missing=False)
    if any(arg.may_be_missing for arg in args):
        raise ExecutableError("unavailable_value")
    if not all(isinstance(arg, DataType) for arg in args):
        raise ExecutableError("type_mismatch")
    kinds = {arg.schema.kind for arg in args}
    arithmetic = fn in ("add", "subtract", "multiply", "divide")
    decimal = arithmetic and kinds == {"decimal"}
    if decimal:
        if (
            mapping.scale is None
            or mapping.rounding is None
            or len({arg.schema.scale for arg in args}) != 1
        ):
            raise ExecutableError("invalid_mapping")
        return DataType(DecimalSchema(kind="decimal", precision=38, scale=mapping.scale))
    if mapping.scale is not None or mapping.rounding is not None:
        raise ExecutableError("invalid_mapping")
    if arithmetic and kinds == {"integer"}:
        # Bounds describe all successful results. Overflow, zero division and
        # inexact integer division are specified runtime failures, not evaluated.
        return DataType(IntegerSchema(kind="integer"))
    if fn == "concat" and kinds == {"string"}:
        limit = sum(arg.schema.max_length for arg in args)
        if limit > 65536:
            raise ExecutableError("type_mismatch")
        return DataType(StringSchema(kind="string", max_length=limit))
    if fn in ("and", "or", "not") and kinds == {"boolean"}:
        return DataType(BooleanSchema(kind="boolean"))
    comparable = (
        {"null", "boolean", "integer", "number", "string", "decimal"}
        if fn in ("eq", "ne")
        else {"integer", "number", "string", "decimal"}
    )
    if fn in ("eq", "ne", "lt", "le", "gt", "ge") and len(kinds) == 1 and kinds <= comparable:
        if kinds == {"decimal"} and args[0].schema.scale != args[1].schema.scale:
            raise ExecutableError("type_mismatch")
        return DataType(BooleanSchema(kind="boolean"))
    raise ExecutableError("type_mismatch")


def infer_mapping(raw: object, environment: TypeEnvironment) -> FlowType:
    input_schema, outputs, iteration, named = _environment(environment)
    mapping = parse_mapping(raw, environment.schema_definitions)

    def infer(item):
        if item.kind == "literal":
            return DataType(_resolve(item.schema, named, ()))
        if item.kind == "ref":
            return _reference(item, environment, input_schema, outputs, iteration)
        if item.kind == "object":
            fields = {f.name: infer(f.value) for f in item.fields}
            if any(value.may_be_missing for value in fields.values()):
                raise ExecutableError("unavailable_value")
            return FlowObjectType(fields)
        if item.kind == "array":
            expected = parse_export(item.item_schema, environment.schema_definitions)
            elements = tuple(infer(child) for child in item.items)
            for child in elements:
                _assign(child, expected)
            return FlowArrayType(
                merge_flow_elements(elements, expected) if elements else None,
                len(elements),
                elements=elements,
                item_schema=expected,
            )
        return _call(item, [infer(arg) for arg in item.args])

    return infer(mapping)
