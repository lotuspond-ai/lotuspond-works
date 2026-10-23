"""Closed mapping evaluation: presence, exact numbers, bounds and no implicit truth."""

import json
import pickle

import pytest

from lotuspond.execution.expressions import (
    MISSING,
    DecimalText,
    ExpressionError,
    ValueEnvironment,
    evaluate_mapping,
    plain,
    typed_export,
    typed_schema,
)

INPUT = {"kind": "package_input"}


def lit(value, kind="integer", **schema):
    return {"kind": "literal", "schema": {"kind": kind, **schema}, "value": value}


def dec(value, scale=3):
    return lit(value, "decimal", precision=38, scale=scale)


def ref(pointer, source=INPUT):
    return {"kind": "ref", "source": source, "pointer": pointer}


def call(fn, *args, **options):
    return {"kind": "call", "fn": fn, "args": list(args), **options}


def evaluate(mapping, values=None, definitions=()):
    return evaluate_mapping(mapping, values or ValueEnvironment(input=None), definitions)


def code(mapping, values=None):
    with pytest.raises(ExpressionError) as error:
        evaluate(mapping, values)
    return error.value.code


def test_missing_is_not_null_or_false():
    values = ValueEnvironment(input={"present_null": None, "false": False})
    # coalesce(null, fallback) preserves null; only missing selects the fallback.
    assert evaluate(call("coalesce", ref("/present_null"), lit(5)), values) is None
    assert evaluate(call("coalesce", ref("/absent"), lit(5)), values) == 5
    assert evaluate(call("coalesce", ref("/false", INPUT), lit(True, "boolean")), values) is False
    # exists checks presence, not truthiness.
    assert evaluate(call("exists", ref("/present_null")), values) is True
    assert evaluate(call("exists", ref("/false")), values) is True
    assert evaluate(call("exists", ref("/absent")), values) is False
    assert evaluate(call("exists", ref("", {"kind": "step", "id": "later"})), values) is False
    # A missing operand is a fixed failure, never null or false.
    assert code(call("eq", ref("/absent"), lit(None, "null")), values) == "unavailable_value"
    assert code(call("not", ref("/absent")), values) == "unavailable_value"
    assert code(ref("/absent"), values) == "unavailable_value"
    assert code({"kind": "object", "fields": [{"name": "x", "value": ref("/absent")}]}, values) == (
        "unavailable_value"
    )
    # No implicit truth and no serialization of the sentinel.
    with pytest.raises(ExpressionError):
        bool(MISSING)
    with pytest.raises(TypeError):
        json.dumps(MISSING)
    with pytest.raises(TypeError):
        pickle.dumps(MISSING)
    with pytest.raises(ExpressionError):
        plain({"x": MISSING})
    # Booleans require booleans: no truthiness of integers or strings.
    assert code(call("and", lit(1), lit(True, "boolean"))) == "type_mismatch"


def test_decimal_rounding_and_safe_integer_overflow():
    half_even = dict(scale=2, rounding="half_even")
    decimal_half_even_result = plain(evaluate(call("add", dec("1.005"), dec("0.000"), **half_even)))
    assert decimal_half_even_result == "1.00"
    assert plain(evaluate(call("add", dec("1.015"), dec("0.000"), **half_even))) == "1.02"
    assert plain(evaluate(call("subtract", dec("0.000"), dec("1.005"), **half_even))) == "-1.00"
    assert plain(
        evaluate(call("add", dec("1.001"), dec("0.000"), scale=2, rounding="ceiling"))
    ) == ("1.01")
    assert plain(evaluate(call("add", dec("-1.001"), dec("0.000"), scale=2, rounding="floor"))) == (
        "-1.01"
    )
    assert plain(evaluate(call("divide", dec("1.000"), dec("3.000"), **half_even))) == "0.33"
    assert plain(evaluate(call("multiply", dec("0.001"), dec("0.001"), **half_even))) == "0.00"
    assert code(call("divide", dec("1.000"), dec("3.000"), scale=2, rounding="reject")) == (
        "inexact_decimal"
    )
    assert code(call("divide", dec("1.000"), dec("0.000"), **half_even)) == "division_by_zero"
    big = dec("9" * 35 + ".000")
    assert code(call("multiply", big, dec("10.000"), scale=3, rounding="reject")) == (
        "decimal_overflow"
    )
    # Exact rational arithmetic: no binary float rounding at any step.
    assert plain(
        evaluate(call("add", dec("0.100", 3), dec("0.200", 3), scale=1, rounding="reject"))
    ) == ("0.3")
    # Integers: exact, safe-integer bounded, and division must be exact.
    assert evaluate(call("divide", lit(-8), lit(2))) == -4
    integer_inexact_division_error = pytest.raises(ExpressionError)
    with integer_inexact_division_error as error:
        evaluate(call("divide", lit(7), lit(2)))
    assert error.value.code == "inexact_division"
    assert code(call("divide", lit(7), lit(0))) == "division_by_zero"
    assert code(call("add", lit(9007199254740991), lit(1))) == "integer_overflow"
    assert code(call("multiply", lit(-9007199254740991), lit(2))) == "integer_overflow"
    assert evaluate(call("subtract", lit(-9007199254740990), lit(1))) == -9007199254740991


def test_comparisons_are_typed_and_decimal_ordering_is_numeric():
    assert evaluate(call("lt", dec("9.000"), dec("10.000"))) is True
    assert evaluate(call("gt", dec("-1.000"), dec("-10.000"))) is True
    assert evaluate(call("eq", dec("1.000"), dec("1.000"))) is True
    assert evaluate(call("lt", lit("10", "string", max_length=4), lit("9", "string", max_length=4)))
    values = ValueEnvironment(
        input=typed_schema(
            {"a": "9.50", "b": "10.00"},
            {
                "kind": "object",
                "fields": [
                    {
                        "name": "a",
                        "schema": {"kind": "decimal", "precision": 5, "scale": 2},
                        "required": True,
                    },
                    {
                        "name": "b",
                        "schema": {"kind": "decimal", "precision": 5, "scale": 2},
                        "required": True,
                    },
                ],
            },
        )
    )
    assert isinstance(values.input["a"], DecimalText)
    assert evaluate(call("lt", ref("/a"), ref("/b")), values) is True
    assert code(call("lt", lit(True, "boolean"), lit(False, "boolean"))) == "type_mismatch"
    assert code(call("lt", dec("1.000"), lit("1.000", "string", max_length=8))) == "type_mismatch"
    assert (
        evaluate(call("concat", lit("a", "string", max_length=1), lit("b", "string", max_length=1)))
        == "ab"
    )


def test_iteration_scope_and_step_references():
    values = ValueEnvironment(
        input=None, steps={"earlier": {"list": [1, 2, 3]}}, item={"name": "x"}, index=4
    )
    assert evaluate(ref("/list/2", {"kind": "step", "id": "earlier"}), values) == 3
    assert code(ref("/list/3", {"kind": "step", "id": "earlier"}), values) == "unavailable_value"
    assert evaluate(ref("/name", {"kind": "iteration_item"}), values) == "x"
    assert evaluate(ref("", {"kind": "iteration_index"}), values) == 4
    assert code(ref("", {"kind": "iteration_item"})) == "unavailable_value"


def test_ast_bounds_and_closed_operations_are_enforced():
    deep = lit(1)
    for _ in range(8):
        deep = call("add", deep, lit(1))
    assert code(deep) == "invalid_mapping"  # depth 9
    wide = {
        "kind": "array",
        "items": [lit(1)] * 64,
        "item_schema": {"kind": "data", "schema": {"kind": "integer"}},
    }
    assert code(wide) == "invalid_mapping"  # 65 nodes
    for invalid in (
        {"kind": "call", "fn": "eval", "args": []},
        {"kind": "call", "fn": "add", "args": [lit(1)]},
        {"kind": "ref", "source": INPUT, "pointer": "no-slash"},
        {"kind": "literal", "schema": {"kind": "integer"}, "value": 1.5},
    ):
        assert code(invalid) == "invalid_mapping"


def test_exports_validate_and_tag_composite_values():
    export = {
        "kind": "object",
        "fields": [
            {
                "name": "total",
                "schema": {
                    "kind": "data",
                    "schema": {"kind": "decimal", "precision": 6, "scale": 2},
                },
                "required": True,
            },
            {
                "name": "items",
                "schema": {
                    "kind": "array",
                    "items": {"kind": "data", "schema": {"kind": "integer"}},
                    "max_items": 2,
                },
                "required": False,
            },
        ],
    }
    value = typed_export({"total": "10.50", "items": [1, 2]}, export)
    assert isinstance(value["total"], DecimalText)
    assert plain(value) == {"total": "10.50", "items": [1, 2]}
    for invalid in (
        {"items": [1]},
        {"total": "10.5"},
        {"total": "10.50", "items": [1, 2, 3]},
        {"total": "1.00", "x": 1},
    ):
        with pytest.raises(ExpressionError, match="type_mismatch"):
            typed_export(invalid, export)
