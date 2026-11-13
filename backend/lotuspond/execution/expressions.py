"""Closed, bounded evaluation of executable-v2 mappings over committed values.

Uses the public SDK parser and type validation; no eval, import, clock, randomness,
network or float conversion of decimals. Integer arithmetic is exact within the
safe-integer domain and division must be exact. Decimal values are canonical
strings; arithmetic is exact rational arithmetic rounded only by the mapping's
declared scale and rounding. `MISSING` is distinct from JSON null and false, has
no truth value and cannot be serialized.
"""

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction

from lotuspond_sdk.executable.expressions import parse_mapping
from lotuspond_sdk.executable.types import (
    ArrayExport,
    DataExport,
    ExportField,
    ObjectExport,
    parse_export,
    resolve_schema,
    validate_value,
)

SAFE_INTEGER = 9007199254740991
DECIMAL_PRECISION = 38
CODES = frozenset(
    {
        "unavailable_value",
        "type_mismatch",
        "integer_overflow",
        "division_by_zero",
        "inexact_division",
        "inexact_decimal",
        "decimal_overflow",
        "invalid_mapping",
        "value_too_large",
        "occurrence_unresolved",
    }
)


class ExpressionError(Exception):
    """A fixed step-failure code; never carries values or caller text."""

    def __init__(self, code: str):
        self.code = code if code in CODES else "invalid_mapping"
        super().__init__(self.code)


class _Missing:
    __slots__ = ()

    def __repr__(self):
        return "MISSING"

    def __bool__(self):
        raise ExpressionError("type_mismatch")

    def __reduce__(self):
        raise TypeError("MISSING cannot be serialized")


MISSING = _Missing()


class DecimalText(str):
    """A canonical decimal value; ordering and arithmetic are numeric, never textual."""

    __slots__ = ()


@dataclass(frozen=True)
class ValueEnvironment:
    input: object
    steps: dict[str, object] = field(default_factory=dict)
    item: object = MISSING
    index: int | None = None


def _tokens(pointer):
    if pointer == "":
        return []
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ExpressionError("invalid_mapping")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def typed_schema(value, schema, definitions=()):
    """Validate a value against a declared data schema and tag its decimals."""
    try:
        resolved = resolve_schema(schema, list(definitions))
        checked = validate_value(value, resolved, [])
    except ExpressionError:
        raise
    except Exception:
        raise ExpressionError("type_mismatch") from None
    return _typed(checked, resolved)


def typed_export(value, export, definitions=()):
    """Validate a value against an exported region/step type and tag its decimals."""
    try:
        resolved = parse_export(export, list(definitions))
    except Exception:
        raise ExpressionError("type_mismatch") from None
    return _export(value, resolved)


def _export(value, export):
    if value is MISSING:
        raise ExpressionError("unavailable_value")
    if export.kind == "data":
        try:
            return _typed(validate_value(plain(value), export.schema, []), export.schema)
        except ExpressionError:
            raise
        except Exception:
            raise ExpressionError("type_mismatch") from None
    if export.kind == "object":
        if type(value) is not dict:
            raise ExpressionError("type_mismatch")
        fields = {item.name: item for item in export.fields}
        if value.keys() - fields.keys() or any(
            item.required and name not in value for name, item in fields.items()
        ):
            raise ExpressionError("type_mismatch")
        return {name: _export(item, fields[name].schema) for name, item in value.items()}
    if export.kind == "array":
        if type(value) is not list or len(value) > export.max_items:
            raise ExpressionError("type_mismatch")
        return [_export(item, export.items) for item in value]
    if export.kind == "handle":
        # E05 (interpreter v2): an opaque handle naming a record; mappings never read
        # inside it (the compiler refuses that), so only its exact shape is checked.
        if (
            type(value) is not dict
            or value.keys() != {"handle_kind", "id"}
            or value["handle_kind"] != export.handle_kind
            or type(value["id"]) is not str
        ):
            raise ExpressionError("type_mismatch")
        return dict(value)
    # Handles appear only in interpreter-v2 plans.
    raise ExpressionError("type_mismatch")


def _typed(value, schema):
    if schema.kind == "decimal":
        return DecimalText(value)
    if schema.kind == "array":
        return [_typed(item, schema.items) for item in value]
    if schema.kind == "object":
        fields = {item.name: item.schema for item in schema.fields}
        return {name: _typed(item, fields[name]) for name, item in value.items()}
    return value


def plain(value):
    """Untag for canonical storage; MISSING is never storable."""
    if value is MISSING:
        raise ExpressionError("unavailable_value")
    if isinstance(value, DecimalText):
        return str.__str__(value)
    if type(value) is dict:
        return {key: plain(item) for key, item in value.items()}
    if type(value) is list:
        return [plain(item) for item in value]
    return value


def _decimal(text):
    if not isinstance(text, DecimalText):
        raise ExpressionError("type_mismatch")
    sign = -1 if text.startswith("-") else 1
    whole, _, fraction = text.lstrip("-").partition(".")
    digits = int(whole + fraction)
    return Fraction(sign * digits, 10 ** len(fraction))


def _round(value: Fraction, scale: int, rounding: str) -> DecimalText:
    scaled = value * 10**scale
    if rounding == "reject":
        if scaled.denominator != 1:
            raise ExpressionError("inexact_decimal")
        units = scaled.numerator
    elif rounding == "floor":
        units = math.floor(scaled)
    elif rounding == "ceiling":
        units = math.ceil(scaled)
    elif rounding == "half_even":
        units = math.floor(scaled)
        remainder = scaled - units
        if remainder > Fraction(1, 2) or (remainder == Fraction(1, 2) and units % 2):
            units += 1
    else:
        raise ExpressionError("invalid_mapping")
    digits = str(abs(units))
    if len(digits.lstrip("0")) > DECIMAL_PRECISION:
        raise ExpressionError("decimal_overflow")
    digits = digits.rjust(scale + 1, "0")
    whole, fraction = digits[: len(digits) - scale], digits[len(digits) - scale :]
    text = whole + ("." + fraction if scale else "")
    return DecimalText(("-" if units < 0 else "") + text)


def _integer(value):
    if type(value) is not int or not -SAFE_INTEGER <= value <= SAFE_INTEGER:
        raise ExpressionError("integer_overflow")
    return value


def _arithmetic(fn, args, mapping):
    left, right = args
    if isinstance(left, DecimalText) or isinstance(right, DecimalText):
        if mapping.scale is None or mapping.rounding is None:
            raise ExpressionError("invalid_mapping")
        a, b = _decimal(left), _decimal(right)
        if fn == "divide":
            if b == 0:
                raise ExpressionError("division_by_zero")
            result = a / b
        else:
            result = {"add": a + b, "subtract": a - b, "multiply": a * b}[fn]
        return _round(result, mapping.scale, mapping.rounding)
    if type(left) is not int or type(right) is not int:
        raise ExpressionError("type_mismatch")
    if fn == "divide":
        if right == 0:
            raise ExpressionError("division_by_zero")
        quotient, remainder = divmod(left, right)
        if remainder:
            raise ExpressionError("inexact_division")
        return _integer(quotient)
    return _integer({"add": left + right, "subtract": left - right, "multiply": left * right}[fn])


def _same_kind(left, right):
    if isinstance(left, DecimalText) != isinstance(right, DecimalText):
        raise ExpressionError("type_mismatch")
    kinds = {type(left), type(right)}
    numbers = {int, float}
    if not (len(kinds) == 1 or kinds <= numbers) or bool in kinds and len(kinds) > 1:
        raise ExpressionError("type_mismatch")


def _compare(fn, left, right):
    _same_kind(left, right)
    if isinstance(left, DecimalText):
        left, right = _decimal(left), _decimal(right)
    elif fn not in ("eq", "ne") and (
        type(left) not in (int, float, str) or type(left) is bool or type(right) is bool
    ):
        raise ExpressionError("type_mismatch")
    if fn == "eq":
        return left == right
    if fn == "ne":
        return left != right
    return {
        "lt": left < right,
        "le": left <= right,
        "gt": left > right,
        "ge": left >= right,
    }[fn]


def _boolean(value):
    if type(value) is not bool:
        raise ExpressionError("type_mismatch")
    return value


def _resolve(mapping, values):
    source = mapping.source
    if source.kind == "package_input":
        value = values.input
    elif source.kind == "step":
        value = values.steps.get(source.id, MISSING)
    elif source.kind == "iteration_item":
        value = values.item
    else:
        value = MISSING if values.index is None else values.index
    for token in _tokens(mapping.pointer):
        if value is MISSING:
            return MISSING
        if type(value) is dict:
            value = value.get(token, MISSING)
        elif type(value) is list:
            if not re.fullmatch(r"0|[1-9][0-9]{0,3}", token):
                raise ExpressionError("type_mismatch")
            position = int(token)
            value = value[position] if position < len(value) else MISSING
        else:
            raise ExpressionError("type_mismatch")
    return value


def _evaluate(mapping, values, definitions):
    kind = mapping.kind
    if kind == "literal":
        return typed_schema(mapping.value, mapping.schema, definitions)
    if kind == "ref":
        return _resolve(mapping, values)
    if kind == "object":
        result = {}
        for item in mapping.fields:
            value = _evaluate(item.value, values, definitions)
            if value is MISSING:
                raise ExpressionError("unavailable_value")
            result[item.name] = value
        return result
    if kind == "array":
        result = [_evaluate(item, values, definitions) for item in mapping.items]
        if any(item is MISSING for item in result):
            raise ExpressionError("unavailable_value")
        return result
    fn = mapping.fn
    if fn == "exists":
        if len(mapping.args) != 1 or mapping.args[0].kind != "ref":
            raise ExpressionError("invalid_mapping")
        return _resolve(mapping.args[0], values) is not MISSING
    if fn == "coalesce":
        if len(mapping.args) != 2:
            raise ExpressionError("invalid_mapping")
        # Only missing selects the fallback; a present null is preserved.
        value = _evaluate(mapping.args[0], values, definitions)
        if value is not MISSING:
            return value
        fallback = _evaluate(mapping.args[1], values, definitions)
        if fallback is MISSING:
            raise ExpressionError("unavailable_value")
        return fallback
    # Every other call evaluates all of its typed arguments; none is short-circuited.
    args = [_evaluate(item, values, definitions) for item in mapping.args]
    if any(item is MISSING for item in args):
        raise ExpressionError("unavailable_value")
    if fn == "concat":
        if len(args) < 2 or any(
            type(item) is not str or isinstance(item, DecimalText) for item in args
        ):
            raise ExpressionError("type_mismatch")
        return "".join(args)
    if fn == "not":
        if len(args) != 1:
            raise ExpressionError("invalid_mapping")
        return not _boolean(args[0])
    if len(args) != 2:
        raise ExpressionError("invalid_mapping")
    if fn in ("and", "or"):
        left, right = _boolean(args[0]), _boolean(args[1])
        return (left and right) if fn == "and" else (left or right)
    if fn in ("add", "subtract", "multiply", "divide"):
        return _arithmetic(fn, args, mapping)
    return _compare(fn, *args)


def evaluate_mapping(mapping: object, values: ValueEnvironment, definitions) -> object:
    """Evaluate one parsed or raw mapping; the result may still need schema validation."""
    if type(values) is not ValueEnvironment:
        raise ExpressionError("invalid_mapping")
    try:
        parsed = parse_mapping(mapping, list(definitions))
    except ExpressionError:
        raise
    except Exception:
        raise ExpressionError("invalid_mapping") from None
    result = _evaluate(parsed, values, list(definitions))
    if result is MISSING:
        raise ExpressionError("unavailable_value")
    return result


@dataclass(frozen=True)
class ExportScope:
    """Resolved export types of the values a mapping may reference."""

    input: object
    steps: dict[str, object] = field(default_factory=dict)
    item: object = None


def data_export(schema, definitions=()):
    try:
        return DataExport(kind="data", schema=resolve_schema(schema, list(definitions)))
    except Exception:
        raise ExpressionError("type_mismatch") from None


def _navigate(export, token):
    if export.kind == "data":
        schema = export.schema
        if schema.kind == "object":
            match = next((item for item in schema.fields if item.name == token), None)
            if match is not None:
                return DataExport(kind="data", schema=match.schema)
        elif schema.kind == "array" and _INDEX_TOKEN.fullmatch(token):
            return DataExport(kind="data", schema=schema.items)
    elif export.kind == "object":
        match = next((item for item in export.fields if item.name == token), None)
        if match is not None:
            return match.schema
    elif export.kind == "array" and _INDEX_TOKEN.fullmatch(token):
        return export.items
    raise ExpressionError("type_mismatch")


_INDEX_TOKEN = re.compile(r"0|[1-9][0-9]{0,3}")


def mapping_export(mapping, scope: ExportScope, definitions=()):
    """The exported type of a mapping over already-typed sources (compiler-checked)."""
    try:
        parsed = parse_mapping(mapping, list(definitions))
    except Exception:
        raise ExpressionError("invalid_mapping") from None
    return _infer(parsed, scope, list(definitions))


def _infer(mapping, scope, definitions):
    kind = mapping.kind
    if kind == "literal":
        return data_export(mapping.schema, definitions)
    if kind == "ref":
        source = mapping.source
        if source.kind == "package_input":
            export = scope.input
        elif source.kind == "step":
            export = scope.steps.get(source.id)
        elif source.kind == "iteration_item":
            export = scope.item
        else:
            export = data_export({"kind": "integer", "min": 0, "max": 999})
        if export is None:
            raise ExpressionError("type_mismatch")
        for token in _tokens(mapping.pointer):
            export = _navigate(export, token)
        return export
    if kind == "object":
        return ObjectExport(
            kind="object",
            fields=[
                ExportField(
                    name=item.name, schema=_infer(item.value, scope, definitions), required=True
                )
                for item in mapping.fields
            ],
        )
    if kind == "array":
        try:
            items = parse_export(mapping.item_schema, definitions)
        except Exception:
            raise ExpressionError("type_mismatch") from None
        return ArrayExport(kind="array", items=items, max_items=len(mapping.items))
    fn = mapping.fn
    if fn in ("exists", "eq", "ne", "lt", "le", "gt", "ge", "and", "or", "not"):
        return data_export({"kind": "boolean"})
    if fn == "coalesce":
        return _infer(mapping.args[0], scope, definitions)
    if fn == "concat":
        lengths = []
        for arg in mapping.args:
            export = _infer(arg, scope, definitions)
            if export.kind != "data" or export.schema.kind != "string":
                raise ExpressionError("type_mismatch")
            lengths.append(export.schema.max_length)
        return data_export({"kind": "string", "max_length": min(sum(lengths), 65536)})
    if mapping.scale is not None:
        return data_export({"kind": "decimal", "precision": 38, "scale": mapping.scale})
    return data_export({"kind": "integer"})
