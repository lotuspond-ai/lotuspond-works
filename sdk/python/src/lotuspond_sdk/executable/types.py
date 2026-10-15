"""Closed data schemas and compiler-only flow metadata; never runtime authority."""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Annotated, Literal, Mapping, TypeAlias

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from ..contract import Identifier
from ._canonical import jcs_bytes
from ._validation import StrictModel
from .errors import ExecutableError
from .input import JsonValue, project_json

SAFE_INTEGER = 9007199254740991
Name = Annotated[str, Field(min_length=1, max_length=128)]
HandleKind: TypeAlias = Literal[
    "action_intent", "human_decision", "action_receipt", "verification_evidence"
]


def _number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("Expected a number.")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError("Expected a finite number.") from None
    if not math.isfinite(number):
        raise ValueError("Expected a finite number.")
    return number


class NullSchema(StrictModel):
    kind: Literal["null"]


class BooleanSchema(StrictModel):
    kind: Literal["boolean"]


class IntegerSchema(StrictModel):
    kind: Literal["integer"]
    min: int | None = None
    max: int | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Invalid bounds.")
        return self


class NumberSchema(StrictModel):
    kind: Literal["number"]
    min: float | None = None
    max: float | None = None

    @field_validator("min", "max", mode="before")
    @classmethod
    def numeric_domain(cls, value):
        return None if value is None else _number(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Invalid bounds.")
        return self


class StringSchema(StrictModel):
    kind: Literal["string"]
    max_length: int = Field(ge=1, le=65536)


class DecimalSchema(StrictModel):
    kind: Literal["decimal"]
    precision: int = Field(ge=1, le=38)
    scale: int = Field(ge=0, le=18)

    @model_validator(mode="after")
    def valid_scale(self):
        if self.scale > self.precision:
            raise ValueError("Invalid scale.")
        return self


class ArraySchema(StrictModel):
    kind: Literal["array"]
    items: DataSchema
    max_items: int = Field(ge=0, le=1000)


# The public JSON field deliberately replaces Pydantic's deprecated schema() API.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "schema" in ".*" shadows an attribute in parent "StrictModel"',
        category=UserWarning,
    )

    class FieldSpec(StrictModel):
        name: Name
        schema: DataSchema
        required: bool


class ObjectSchema(StrictModel):
    kind: Literal["object"]
    fields: list[FieldSpec] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({item.name for item in self.fields}) != len(self.fields):
            raise ValueError("Duplicate field.")
        return self


class RefSchema(StrictModel):
    kind: Literal["ref"]
    name: Identifier


DataSchema: TypeAlias = Annotated[
    NullSchema
    | BooleanSchema
    | IntegerSchema
    | NumberSchema
    | StringSchema
    | DecimalSchema
    | ArraySchema
    | ObjectSchema
    | RefSchema,
    Field(discriminator="kind"),
]


# The public JSON field deliberately replaces Pydantic's deprecated schema() API.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "schema" in ".*" shadows an attribute in parent "StrictModel"',
        category=UserWarning,
    )

    class NamedSchema(StrictModel):
        name: Identifier
        schema: DataSchema


# The public JSON field deliberately replaces Pydantic's deprecated schema() API.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "schema" in ".*" shadows an attribute in parent "StrictModel"',
        category=UserWarning,
    )

    class DataExport(StrictModel):
        kind: Literal["data"]
        schema: DataSchema


class HandleExport(StrictModel):
    kind: Literal["handle"]
    handle_kind: HandleKind


# The public JSON field deliberately replaces Pydantic's deprecated schema() API.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "schema" in ".*" shadows an attribute in parent "StrictModel"',
        category=UserWarning,
    )

    class ExportField(StrictModel):
        name: Name
        schema: ExportSchema
        required: bool


class ObjectExport(StrictModel):
    kind: Literal["object"]
    fields: list[ExportField] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({item.name for item in self.fields}) != len(self.fields):
            raise ValueError("Duplicate field.")
        return self


class ArrayExport(StrictModel):
    kind: Literal["array"]
    items: ExportSchema
    max_items: int = Field(ge=0, le=1000)


ExportSchema: TypeAlias = Annotated[
    DataExport | HandleExport | ObjectExport | ArrayExport, Field(discriminator="kind")
]
SCHEMA_MODELS = (
    NullSchema,
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    StringSchema,
    DecimalSchema,
    ArraySchema,
    FieldSpec,
    ObjectSchema,
    RefSchema,
    NamedSchema,
    DataExport,
    HandleExport,
    ExportField,
    ObjectExport,
    ArrayExport,
)
for _model in SCHEMA_MODELS:
    _model.model_rebuild()
_DATA = TypeAdapter(DataSchema)
_EXPORT = TypeAdapter(ExportSchema)
_DEFINITIONS = TypeAdapter(list[NamedSchema])


def _parse(raw: object, adapter: TypeAdapter):
    projected = project_json(raw, model_types=SCHEMA_MODELS)
    try:
        parsed = adapter.validate_python(projected)
    except ValidationError:
        raise ExecutableError("invalid_schema") from None
    jcs_bytes(project_json(parsed, model_types=SCHEMA_MODELS))
    return parsed


def parse_schema(raw: object) -> DataSchema:
    """Validate a schema's closed shape; use resolve_schema to check local refs."""
    return _parse(raw, _DATA)


def schema_definitions(raw: object) -> dict[str, DataSchema]:
    definitions = _parse(raw, _DEFINITIONS)
    if len(definitions) > 64:
        raise ExecutableError("invalid_schema")
    result = {item.name: item.schema for item in definitions}
    if len(result) != len(definitions):
        raise ExecutableError("duplicate_identifier")
    # Validate unused definitions too: an unused cycle is still an invalid set.
    for name in result:
        _resolve(RefSchema(kind="ref", name=name), result, ())
    return result


def _resolve(schema, definitions, active, _budget=None):
    budget = [10000] if _budget is None else _budget
    budget[0] -= 1
    if budget[0] < 0:
        raise ExecutableError("input_too_complex")
    if schema.kind == "ref":
        if schema.name not in definitions:
            raise ExecutableError("unknown_reference")
        if schema.name in active or len(active) >= 8:
            raise ExecutableError("invalid_schema")
        return _resolve(definitions[schema.name], definitions, (*active, schema.name), budget)
    if schema.kind == "array":
        return ArraySchema(
            kind="array",
            items=_resolve(schema.items, definitions, active, budget),
            max_items=schema.max_items,
        )
    if schema.kind == "object":
        return ObjectSchema(
            kind="object",
            fields=[
                FieldSpec(
                    name=f.name,
                    schema=_resolve(f.schema, definitions, active, budget),
                    required=f.required,
                )
                for f in schema.fields
            ],
        )
    return schema


def resolve_schema(raw: object, definitions: object = ()) -> DataSchema:
    """Resolve at most eight named-reference levels, checking the whole local set."""
    if type(definitions) is tuple and not definitions:
        definitions = []
    resolved = _resolve(parse_schema(raw), schema_definitions(definitions), ())
    # Expanded schemas also obey the package shape/byte budget.
    project_json(resolved, model_types=SCHEMA_MODELS)
    return resolved


def parse_export(raw: object, definitions: object = ()) -> ExportSchema:
    parsed = _parse(raw, _EXPORT)
    named = schema_definitions(
        [] if type(definitions) is tuple and not definitions else definitions
    )

    def resolve(item):
        if item.kind == "data":
            return DataExport(kind="data", schema=_resolve(item.schema, named, ()))
        if item.kind == "object":
            return ObjectExport(
                kind="object",
                fields=[
                    ExportField(name=f.name, schema=resolve(f.schema), required=f.required)
                    for f in item.fields
                ],
            )
        if item.kind == "array":
            return ArrayExport(kind="array", items=resolve(item.items), max_items=item.max_items)
        return item

    result = resolve(parsed)
    project_json(result, model_types=SCHEMA_MODELS)
    return result


def validate_value(raw: object, schema: DataSchema, definitions: list[NamedSchema]) -> JsonValue:
    """Normalize only declared number values; all other domains remain exact."""
    value = project_json(raw)
    result = _value(value, resolve_schema(schema, definitions))
    jcs_bytes(result)
    return result


def _value(value, schema):
    kind = schema.kind
    valid = False
    if kind == "null":
        valid = value is None
    elif kind == "boolean":
        valid = type(value) is bool
    elif kind == "integer":
        valid = type(value) is int and -SAFE_INTEGER <= value <= SAFE_INTEGER
    elif kind == "number":
        try:
            value = _number(value)
            valid = True
        except ValueError:
            pass
    elif kind == "string":
        valid = type(value) is str and len(value) <= schema.max_length
    elif kind == "decimal" and type(value) is str:
        fraction = rf"\.[0-9]{{{schema.scale}}}" if schema.scale else ""
        valid = re.fullmatch(r"-?(?:0|[1-9][0-9]*)" + fraction, value) is not None
        digits = value.lstrip("-").replace(".", "")
        # Leading zero before a fractional value does not consume precision.
        valid = valid and len(digits.lstrip("0")) <= schema.precision
        valid = valid and not (value.startswith("-") and not digits.strip("0"))
        whole = value.lstrip("-").split(".")[0]
        valid = valid and (whole == "0" or len(whole) <= schema.precision - schema.scale)
    elif kind == "array" and type(value) is list:
        valid = len(value) <= schema.max_items
        if valid:
            value = [_value(item, schema.items) for item in value]
    elif kind == "object" and type(value) is dict:
        fields = {f.name: f for f in schema.fields}
        valid = not (value.keys() - fields.keys()) and all(
            not f.required or f.name in value for f in schema.fields
        )
        if valid:
            value = {name: _value(item, fields[name].schema) for name, item in value.items()}
    if valid and kind in ("integer", "number"):
        valid = (schema.min is None or value >= schema.min) and (
            schema.max is None or value <= schema.max
        )
    if not valid:
        raise ExecutableError("type_mismatch")
    return value


OccurrenceBindings: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class SourceSymbol:
    """Immutable static origin, including the occurrence within bounded arrays."""

    operation: str
    step_id: str
    occurrence_path: tuple[str, ...] = ()
    occurrence_bindings: OccurrenceBindings = ()

    def __post_init__(self):
        if self.operation not in (
            "action_intent",
            "human_review",
            "action_dispatch",
            "verification",
        ):
            raise ExecutableError("invalid_mapping")
        try:
            TypeAdapter(Identifier).validate_python(self.step_id, strict=True)
        except ValidationError:
            raise ExecutableError("invalid_mapping") from None
        if (
            type(self.occurrence_path) is not tuple
            or len(self.occurrence_path) > 32
            or any(type(x) is not str or not 1 <= len(x) <= 512 for x in self.occurrence_path)
        ):
            raise ExecutableError("invalid_mapping")
        project_json(list(self.occurrence_path))
        _check_bindings(self.occurrence_bindings, self.occurrence_path)


@dataclass(frozen=True, slots=True)
class OriginChoice:
    """One of several existing origins, selected at a stable compiler occurrence.

    Empty selection_path denotes an unresolved array item, never a concrete
    origin. A compiler must bind it through iteration_item_flow before use.
    """

    options: tuple[SourceSymbol | OriginChoice | None, ...]
    selection_path: tuple[str, ...] = ()
    selection_bindings: OccurrenceBindings = ()

    def __post_init__(self):
        _check_origin(self)


def _check_occurrence(path):
    if (
        type(path) is not tuple
        or len(path) > 32
        or any(type(x) is not str or not 1 <= len(x) <= 512 for x in path)
    ):
        raise ExecutableError("invalid_mapping")
    project_json(list(path))


def _check_bindings(bindings, unresolved_path):
    if type(bindings) is not tuple or len(bindings) > 32:
        raise ExecutableError("invalid_mapping")
    seen = set()
    for entry in bindings:
        if type(entry) is not tuple or len(entry) != 2:
            raise ExecutableError("invalid_mapping")
        owner, path = entry
        try:
            TypeAdapter(Identifier).validate_python(owner, strict=True)
        except ValidationError:
            raise ExecutableError("invalid_mapping") from None
        _check_occurrence(path)
        if not path or owner in seen or f"$item:{owner}" in unresolved_path:
            raise ExecutableError("invalid_mapping")
        seen.add(owner)


def _check_origin(raw, *, _depth=0, _budget=None):
    budget = [10000] if _budget is None else _budget
    budget[0] -= 1
    if _depth > 8 or budget[0] < 0:
        raise ExecutableError("invalid_mapping")
    if type(raw) is SourceSymbol:
        SourceSymbol(raw.operation, raw.step_id, raw.occurrence_path, raw.occurrence_bindings)
        return raw.operation
    if (
        type(raw) is not OriginChoice
        or type(raw.options) is not tuple
        or not 1 <= len(raw.options) <= 1000
    ):
        raise ExecutableError("invalid_mapping")
    _check_occurrence(raw.selection_path)
    _check_bindings(raw.selection_bindings, raw.selection_path)
    operations = {
        _check_origin(x, _depth=_depth + 1, _budget=budget) for x in raw.options if x is not None
    }
    if len(operations) != 1:
        raise ExecutableError("invalid_mapping")
    return next(iter(operations))


def _origin(raw):
    _check_origin(raw)
    if type(raw) is SourceSymbol:
        return SourceSymbol(
            raw.operation, raw.step_id, raw.occurrence_path, raw.occurrence_bindings
        )
    return OriginChoice(
        tuple(None if x is None else _origin(x) for x in raw.options),
        raw.selection_path,
        raw.selection_bindings,
    )


def _origin_operation(raw):
    return _check_origin(raw)


@dataclass(frozen=True, slots=True)
class DataType:
    schema: DataSchema
    may_be_missing: bool = False


@dataclass(frozen=True, slots=True)
class HandleType:
    handle_kind: HandleKind
    source: SourceSymbol | OriginChoice
    may_be_missing: bool = False
    intent_source: SourceSymbol | OriginChoice | None = None

    def __post_init__(self):
        expected = {
            "action_intent": "action_intent",
            "human_decision": "human_review",
            "action_receipt": "action_dispatch",
            "verification_evidence": "verification",
        }
        if type(self.handle_kind) is not str or expected.get(self.handle_kind) != _origin_operation(
            self.source
        ):
            raise ExecutableError("invalid_mapping")
        if type(self.may_be_missing) is not bool:
            raise ExecutableError("invalid_mapping")
        if self.handle_kind != "action_intent" and (
            self.intent_source is None or _origin_operation(self.intent_source) != "action_intent"
        ):
            raise ExecutableError("intent_mismatch")
        if self.handle_kind == "action_intent" and self.intent_source is not None:
            raise ExecutableError("intent_mismatch")


@dataclass(frozen=True, slots=True)
class FlowObjectType:
    fields: Mapping[str, FlowType]
    may_be_missing: bool = False

    def __post_init__(self):
        if type(self.fields) not in (dict, MappingProxyType):
            raise ExecutableError("invalid_mapping")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass(frozen=True, slots=True)
class FlowArrayType:
    items: FlowType | None
    max_items: int
    may_be_missing: bool = False
    # Per-element origins are needed when a constructor combines different handles.
    elements: tuple[FlowType, ...] | None = field(default=None, repr=False)
    item_schema: ExportSchema | None = field(default=None, repr=False)
    iteration_id: str | None = None
    # A selected nested array retains each alternative's owner and cardinality.
    alternatives: tuple[FlowArrayType | None, ...] | None = field(default=None, repr=False)
    selection_path: tuple[str, ...] = ()
    selection_bindings: OccurrenceBindings = ()


FlowType: TypeAlias = DataType | HandleType | FlowObjectType | FlowArrayType


def validate_flow(raw: object, *, definitions=(), _active=None, _depth=0, _budget=None) -> FlowType:
    """Revalidate compiler context too, including dataclass mutation bypasses."""
    active = set() if _active is None else _active
    budget = [10000] if _budget is None else _budget
    budget[0] -= 1
    if budget[0] < 0:
        raise ExecutableError("input_too_complex")
    if id(raw) in active or _depth > 32:
        raise ExecutableError("invalid_mapping")
    if (
        type(raw) not in (DataType, HandleType, FlowObjectType, FlowArrayType)
        or type(raw.may_be_missing) is not bool
    ):
        raise ExecutableError("invalid_mapping")
    active.add(id(raw))
    try:
        if type(raw) is DataType:
            return DataType(resolve_schema(raw.schema, definitions), raw.may_be_missing)
        if type(raw) is HandleType:
            source = _origin(raw.source)
            intent = None if raw.intent_source is None else _origin(raw.intent_source)
            return HandleType(raw.handle_kind, source, raw.may_be_missing, intent)

        def child(value):
            return validate_flow(
                value, definitions=definitions, _active=active, _depth=_depth + 1, _budget=budget
            )

        if type(raw) is FlowObjectType:
            if (
                type(raw.fields) not in (dict, MappingProxyType)
                or len(raw.fields) > 64
                or any(type(k) is not str or not 1 <= len(k) <= 128 for k in raw.fields)
            ):
                raise ExecutableError("invalid_mapping")
            project_json(list(raw.fields))
            return FlowObjectType({k: child(v) for k, v in raw.fields.items()}, raw.may_be_missing)
        if type(raw.max_items) is not int or not 0 <= raw.max_items <= 1000:
            raise ExecutableError("invalid_mapping")
        if raw.elements is not None and (
            type(raw.elements) is not tuple or len(raw.elements) > raw.max_items
        ):
            raise ExecutableError("invalid_mapping")
        if raw.iteration_id is not None:
            try:
                TypeAdapter(Identifier).validate_python(raw.iteration_id, strict=True)
            except ValidationError:
                raise ExecutableError("invalid_mapping") from None
        declared = None if raw.item_schema is None else parse_export(raw.item_schema, definitions)
        _check_occurrence(raw.selection_path)
        _check_bindings(raw.selection_bindings, raw.selection_path)
        if raw.alternatives is not None:
            if (
                type(raw.alternatives) is not tuple
                or not 1 <= len(raw.alternatives) <= 1000
                or raw.elements is not None
                or raw.iteration_id is not None
                or declared is None
            ):
                raise ExecutableError("invalid_mapping")
            alternatives = tuple(
                None if value is None else child(value) for value in raw.alternatives
            )
            if not any(value is not None for value in alternatives) or any(
                value is not None and type(value) is not FlowArrayType for value in alternatives
            ):
                raise ExecutableError("invalid_mapping")
            if raw.max_items != max(value.max_items for value in alternatives if value is not None):
                raise ExecutableError("invalid_mapping")
            expected_array = ArrayExport(kind="array", items=declared, max_items=raw.max_items)
            for value in alternatives:
                if value is not None:
                    _assign(replace(value, may_be_missing=False), expected_array)
            if raw.items is not None:
                _assign(child(raw.items), declared)
            elif raw.max_items:
                raise ExecutableError("invalid_mapping")
            return _merge_arrays(
                alternatives,
                declared,
                raw.selection_path,
                raw.may_be_missing,
                raw.selection_bindings,
            )
        if raw.selection_path or raw.selection_bindings:
            raise ExecutableError("invalid_mapping")
        if raw.items is None:
            if raw.max_items != 0 or declared is None or raw.elements not in (None, ()):
                raise ExecutableError("invalid_mapping")
            return FlowArrayType(
                None, 0, raw.may_be_missing, raw.elements, declared, raw.iteration_id
            )
        items = child(raw.items)
        elements = None if raw.elements is None else tuple(child(v) for v in raw.elements)
        expected = declared or flow_export(items)
        _assign(items, expected)
        if elements:
            for element in elements:
                _assign(element, expected)
            items = merge_flow_elements(elements, expected)
        return FlowArrayType(
            items, raw.max_items, raw.may_be_missing, elements, expected, raw.iteration_id
        )
    finally:
        active.remove(id(raw))


def flow_export(flow: FlowType) -> ExportSchema:
    """Describe a flow without transferring provenance into its declaration."""
    if isinstance(flow, DataType):
        return DataExport(kind="data", schema=flow.schema)
    if isinstance(flow, HandleType):
        return HandleExport(kind="handle", handle_kind=flow.handle_kind)
    if isinstance(flow, FlowObjectType):
        return ObjectExport(
            kind="object",
            fields=[
                ExportField(name=k, schema=flow_export(v), required=not v.may_be_missing)
                for k, v in flow.fields.items()
            ],
        )
    return ArrayExport(
        kind="array", items=flow.item_schema or flow_export(flow.items), max_items=flow.max_items
    )


def _data_assign(actual, expected):
    if actual.kind != expected.kind:
        raise ExecutableError("type_mismatch")
    kind = actual.kind
    if kind in ("integer", "number"):
        low = -SAFE_INTEGER if kind == "integer" else -math.inf
        high = SAFE_INTEGER if kind == "integer" else math.inf
        if (actual.min if actual.min is not None else low) < (
            expected.min if expected.min is not None else low
        ) or (actual.max if actual.max is not None else high) > (
            expected.max if expected.max is not None else high
        ):
            raise ExecutableError("type_mismatch")
    elif kind == "string" and actual.max_length > expected.max_length:
        raise ExecutableError("type_mismatch")
    elif kind == "decimal" and (
        actual.scale != expected.scale or actual.precision > expected.precision
    ):
        raise ExecutableError("type_mismatch")
    elif kind == "array":
        if actual.max_items > expected.max_items:
            raise ExecutableError("type_mismatch")
        _data_assign(actual.items, expected.items)
    elif kind == "object":
        fields = {f.name: f for f in actual.fields}
        if fields.keys() - {f.name for f in expected.fields}:
            raise ExecutableError("type_mismatch")
        for item in expected.fields:
            found = fields.get(item.name)
            if found is None:
                if item.required:
                    raise ExecutableError("unavailable_value")
            else:
                if item.required and not found.required:
                    raise ExecutableError("unavailable_value")
                _data_assign(found.schema, item.schema)


def _assign(actual, expected):
    if actual.may_be_missing:
        raise ExecutableError("unavailable_value")
    if isinstance(actual, DataType) and expected.kind == "data":
        _data_assign(actual.schema, expected.schema)
    elif (
        isinstance(actual, HandleType)
        and expected.kind == "handle"
        and actual.handle_kind == expected.handle_kind
    ):
        return
    elif isinstance(actual, FlowObjectType) and expected.kind in ("object", "data"):
        if expected.kind == "data":
            if expected.schema.kind != "object":
                raise ExecutableError("type_mismatch")
            fields = [
                ExportField(
                    name=f.name,
                    schema=DataExport(kind="data", schema=f.schema),
                    required=f.required,
                )
                for f in expected.schema.fields
            ]
        else:
            fields = expected.fields
        if actual.fields.keys() - {f.name for f in fields}:
            raise ExecutableError("type_mismatch")
        for item in fields:
            found = actual.fields.get(item.name)
            if found is None:
                if item.required:
                    raise ExecutableError("unavailable_value")
            else:
                if item.required and found.may_be_missing:
                    raise ExecutableError("unavailable_value")
                _assign(replace(found, may_be_missing=False), item.schema)
    elif isinstance(actual, FlowArrayType) and expected.kind in ("array", "data"):
        if expected.kind == "data":
            if expected.schema.kind != "array":
                raise ExecutableError("type_mismatch")
            limit, item = (
                expected.schema.max_items,
                DataExport(kind="data", schema=expected.schema.items),
            )
        else:
            limit, item = expected.max_items, expected.items
        if actual.max_items > limit:
            raise ExecutableError("type_mismatch")
        if actual.max_items == 0:
            return
        _assign(actual.items, item)
        for element in actual.elements or ():
            _assign(element, item)
    else:
        raise ExecutableError("type_mismatch")


def assert_assignable(actual: FlowType, expected: ExportSchema) -> None:
    """Check total availability, structural bounds and handle kind without casts."""
    _assign(validate_flow(actual), parse_export(expected))


def _choose_origin(origins, path, bindings=()):
    if all(origin == origins[0] for origin in origins):
        return origins[0]
    return OriginChoice(tuple(origins), path, bindings)


class _NoElement:
    """An impossible selection (zero cardinality), not an absent field value."""


_NO_ELEMENT = _NoElement()


def _merge_arrays(alternatives, item_schema, selection_path, missing, selection_bindings=()):
    present = tuple(value for value in alternatives if value is not None)
    if not present:
        raise ExecutableError("unavailable_value")
    if len(present) == len(alternatives) and all(value == present[0] for value in present):
        return replace(present[0], may_be_missing=missing)
    maximum = max(value.max_items for value in present)
    # Impossible branches retain their positional slot in provenance but cannot
    # make an element of a present array absent. In particular [] has no item.
    children = tuple(
        _NO_ELEMENT if value is None or value.max_items == 0 else value.items
        for value in alternatives
    )
    items = (
        None
        if maximum == 0
        else merge_flow_elements(children, item_schema, selection_path, selection_bindings)
    )
    return FlowArrayType(
        items,
        maximum,
        missing,
        item_schema=item_schema,
        alternatives=alternatives,
        selection_path=selection_path,
        selection_bindings=selection_bindings,
    )


def merge_flow_elements(
    elements: tuple[FlowType | None | _NoElement, ...],
    expected: ExportSchema,
    selection_path: tuple[str, ...] = (),
    selection_bindings: OccurrenceBindings = (),
) -> FlowType:
    """Conservatively describe constructor elements without choosing an origin."""
    missing = any(x is not _NO_ELEMENT and (x is None or x.may_be_missing) for x in elements)
    if expected.kind == "data":
        return DataType(expected.schema, missing)
    if expected.kind == "handle":
        if (
            not elements
            or not any(x is not None and x is not _NO_ELEMENT for x in elements)
            or not all(x is None or x is _NO_ELEMENT or type(x) is HandleType for x in elements)
        ):
            raise ExecutableError("type_mismatch")
        source = _choose_origin(
            [None if x is None or x is _NO_ELEMENT else x.source for x in elements],
            selection_path,
            selection_bindings,
        )
        intent = (
            None
            if expected.handle_kind == "action_intent"
            else _choose_origin(
                [None if x is None or x is _NO_ELEMENT else x.intent_source for x in elements],
                selection_path,
                selection_bindings,
            )
        )
        return HandleType(
            expected.handle_kind, source, may_be_missing=missing, intent_source=intent
        )
    if expected.kind == "object":
        result = {}
        for f in expected.fields:
            values = tuple(
                x if x is None or x is _NO_ELEMENT else x.fields.get(f.name) for x in elements
            )
            if not any(x is not None and x is not _NO_ELEMENT for x in values):
                continue
            result[f.name] = merge_flow_elements(
                values, f.schema, selection_path, selection_bindings
            )
        return FlowObjectType(result, missing)
    alternatives = tuple(None if value is _NO_ELEMENT else value for value in elements)
    return _merge_arrays(alternatives, expected.items, selection_path, missing, selection_bindings)


def _bind_occurrence_path(original, path, owner):
    slot = f"$item:{owner}"
    if owner is None or slot not in original:
        return original
    index = original.index(slot)
    return (*original[:index], *path, *original[index + 1 :])


def _bind_owner(original_path, bindings, path, owner):
    if owner is not None and f"$item:{owner}" in original_path:
        return (*bindings, (owner, path))
    return bindings


def _occurrence_flow(flow, path, owner):
    if type(flow) is HandleType:

        def bind(origin):
            if type(origin) is OriginChoice:
                return replace(
                    origin,
                    options=tuple(None if item is None else bind(item) for item in origin.options),
                    selection_path=_bind_occurrence_path(origin.selection_path, path, owner),
                    selection_bindings=_bind_owner(
                        origin.selection_path, origin.selection_bindings, path, owner
                    ),
                )
            return replace(
                origin,
                occurrence_path=_bind_occurrence_path(origin.occurrence_path, path, owner),
                occurrence_bindings=_bind_owner(
                    origin.occurrence_path, origin.occurrence_bindings, path, owner
                ),
            )

        return replace(
            flow,
            source=bind(flow.source),
            intent_source=None if flow.intent_source is None else bind(flow.intent_source),
        )
    if type(flow) is FlowObjectType:
        return replace(
            flow, fields={k: _occurrence_flow(v, path, owner) for k, v in flow.fields.items()}
        )
    if type(flow) is FlowArrayType:
        return replace(
            flow,
            items=None if flow.items is None else _occurrence_flow(flow.items, path, owner),
            elements=None
            if flow.elements is None
            else tuple(_occurrence_flow(v, path, owner) for v in flow.elements),
            alternatives=None
            if flow.alternatives is None
            else tuple(
                None if value is None else _occurrence_flow(value, path, owner)
                for value in flow.alternatives
            ),
            selection_path=_bind_occurrence_path(flow.selection_path, path, owner),
            selection_bindings=_bind_owner(
                flow.selection_path, flow.selection_bindings, path, owner
            ),
        )
    return flow


def select_array_item(raw: FlowArrayType | DataType, index: int) -> FlowType:
    """Select a bounded index. Presence still needs a guard; aliases keep origins.

    Arrays without concrete elements describe repeated operation occurrences;
    only $item:<iteration_id> slots owned by that array receive this index. Constructed arrays instead retain
    their exact existing element origins unchanged.
    """
    flow = validate_flow(raw)
    if type(index) is not int or index < 0:
        raise ExecutableError("invalid_mapping")
    if type(flow) is DataType and flow.schema.kind == "array":
        if index >= flow.schema.max_items:
            raise ExecutableError("unknown_reference")
        return DataType(flow.schema.items, True)
    if type(flow) is not FlowArrayType or index >= flow.max_items:
        raise ExecutableError("unknown_reference")
    if flow.alternatives is not None:
        selected = tuple(
            _NO_ELEMENT
            if value is None
            or index >= value.max_items
            or value.elements is not None
            and index >= len(value.elements)
            else replace(select_array_item(value, index), may_be_missing=False)
            for value in flow.alternatives
        )
        if all(value is _NO_ELEMENT for value in selected):
            raise ExecutableError("unknown_reference")
        item = merge_flow_elements(
            selected, flow.item_schema, flow.selection_path, flow.selection_bindings
        )
    elif flow.elements is not None:
        if index >= len(flow.elements):
            raise ExecutableError("unknown_reference")
        item = flow.elements[index]
    else:
        item = _occurrence_flow(flow.items, (str(index),), flow.iteration_id)
    return replace(item, may_be_missing=True)


def iteration_item_flow(
    raw: FlowArrayType | DataType, *, occurrence_path: tuple[str, ...]
) -> FlowType:
    """Derive a present iteration item at an explicit stable compiler occurrence.

    Distinct existing origins become a symbolic choice bound to this iteration;
    no first-element origin is selected. Repeated-operation arrays specialize
    their source templates. The compiler supplies only lexically available arrays.
    """
    _check_occurrence(occurrence_path)
    if not occurrence_path:
        raise ExecutableError("invalid_mapping")
    flow = validate_flow(raw)
    if flow.may_be_missing:
        raise ExecutableError("unavailable_value")
    if type(flow) is DataType and flow.schema.kind == "array" and flow.schema.max_items:
        return DataType(flow.schema.items)
    if type(flow) is not FlowArrayType or not flow.max_items:
        raise ExecutableError("unavailable_value")
    if flow.alternatives is not None:
        selected = tuple(
            _NO_ELEMENT
            if value is None or not value.max_items
            else iteration_item_flow(
                replace(value, may_be_missing=False), occurrence_path=occurrence_path
            )
            for value in flow.alternatives
        )
        return merge_flow_elements(
            selected, flow.item_schema, flow.selection_path, flow.selection_bindings
        )
    if flow.elements is not None:
        return merge_flow_elements(
            flow.elements, flow.item_schema or flow_export(flow.items), occurrence_path
        )
    return _occurrence_flow(flow.items, occurrence_path, flow.iteration_id)
