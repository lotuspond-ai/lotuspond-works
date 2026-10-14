"""Typed data admission, numeric domains and local-schema boundaries."""

import math

import pytest

from lotuspond_sdk.executable import ExecutableError, decode_executable_bytes
from lotuspond_sdk.executable._canonical import jcs_bytes, sha256_hex
from lotuspond_sdk.executable.types import (
    DataType,
    HandleType,
    SourceSymbol,
    assert_assignable,
    parse_schema,
    validate_value,
)

INTEGER = {"kind": "integer"}
STRING = {"kind": "string", "max_length": 20}


@pytest.mark.parametrize("value", [True, 1.0, "1", 9007199254740992])
def test_integer_exact_domain(value):
    with pytest.raises(ExecutableError) as error:
        validate_value(value, INTEGER, [])
    assert error.value.code == "type_mismatch"


@pytest.mark.parametrize(
    "value",
    [1.0, -0.0, 1e20, math.nextafter(1e20, math.inf), float.fromhex("0x1.fffffffffffffp+1023")],
)
def test_number_jcs_reread(value):
    normalized = validate_value(value, {"kind": "number"}, [])
    assert type(normalized) is float
    first = jcs_bytes(normalized)
    second = jcs_bytes(validate_value(decode_executable_bytes(first), {"kind": "number"}, []))
    assert first == second
    assert sha256_hex(first) == sha256_hex(second)


@pytest.mark.parametrize("value", ["+1.00", "01.00", "-0.00", "1e0", "1.0", "100.00", 1.0])
def test_decimal_rejects_noncanonical_or_overprecision(value):
    with pytest.raises(ExecutableError):
        validate_value(value, {"kind": "decimal", "precision": 4, "scale": 2}, [])


@pytest.mark.parametrize("value", ["0.00", "-0.01", "99.99"])
def test_decimal_valid(value):
    assert validate_value(value, {"kind": "decimal", "precision": 4, "scale": 2}, []) == value


def test_optional_is_absence_not_null_and_object_is_closed():
    schema = {"kind": "object", "fields": [{"name": "a", "schema": STRING, "required": False}]}
    assert validate_value({}, schema, []) == {}
    for value in ({"a": None}, {"extra": "x"}):
        with pytest.raises(ExecutableError):
            validate_value(value, schema, [])


@pytest.mark.parametrize(
    "schema",
    [
        {"kind": "handle", "handle_kind": "action_intent"},
        {"kind": "integer", "min": 1.0},
        {"kind": "integer", "min": 2, "max": 1},
        {"kind": "number", "min": True},
        {"kind": "string"},
        {"kind": "string", "max_length": 65537},
        {"kind": "decimal", "precision": 1, "scale": 2},
        {"kind": "array", "items": INTEGER, "max_items": 1001},
        {"kind": "object", "fields": [{"name": "a", "schema": INTEGER, "required": 0}]},
        {"kind": "null", "extra": 1},
    ],
)
def test_closed_schema_and_bounds(schema):
    with pytest.raises(ExecutableError):
        parse_schema(schema)


def test_definitions_cycles_duplicates_unknown_and_depth():
    def ref(name):
        return {"kind": "ref", "name": name}

    for definitions in (
        [{"name": "a", "schema": ref("a")}],
        [{"name": "a", "schema": INTEGER}] * 2,
        [{"name": "a", "schema": ref("b")}],
    ):
        with pytest.raises(ExecutableError):
            validate_value(1, ref("a"), definitions)
    for size in (8, 9):
        definitions = [
            {"name": f"s{i}", "schema": ref(f"s{i + 1}") if i < size - 1 else INTEGER}
            for i in range(size)
        ]
        if size == 8:
            assert validate_value(1, ref("s0"), definitions) == 1
        else:
            with pytest.raises(ExecutableError):
                validate_value(1, ref("s0"), definitions)


def test_mutated_schema_is_revalidated():
    schema = parse_schema(INTEGER)
    schema.min = True
    with pytest.raises(ExecutableError):
        validate_value(1, schema, [])


def test_schema_array_object_bounds_and_duplicate_names():
    field = {"name": "a", "schema": INTEGER, "required": True}
    with pytest.raises(ExecutableError):
        parse_schema({"kind": "object", "fields": [field, field]})
    with pytest.raises(ExecutableError):
        validate_value([1, 2], {"kind": "array", "items": INTEGER, "max_items": 1}, [])


def test_handles_cannot_be_data_or_relabelled():
    handle = HandleType("action_intent", SourceSymbol("action_intent", "propose"))
    assert_assignable(handle, {"kind": "handle", "handle_kind": "action_intent"})
    with pytest.raises(ExecutableError):
        assert_assignable(
            DataType(parse_schema({"kind": "object", "fields": []})),
            {"kind": "handle", "handle_kind": "action_intent"},
        )
    with pytest.raises(ExecutableError):
        assert_assignable(handle, {"kind": "handle", "handle_kind": "human_decision"})
    with pytest.raises((ExecutableError, ValueError)):
        HandleType("action_intent", SourceSymbol("model", "propose"))


def test_named_reference_expansion_is_bounded_before_materializing():
    definitions = [{"name": "s0", "schema": INTEGER}]
    for index in range(1, 8):
        definitions.append(
            {
                "name": f"s{index}",
                "schema": {
                    "kind": "object",
                    "fields": [
                        {
                            "name": f"f{field}",
                            "required": True,
                            "schema": {"kind": "ref", "name": f"s{index - 1}"},
                        }
                        for field in range(4)
                    ],
                },
            }
        )
    with pytest.raises(ExecutableError) as error:
        validate_value({}, {"kind": "ref", "name": "s7"}, definitions)
    assert error.value.code == "input_too_complex"


def test_exact_schema_definition_and_object_field_caps():
    from lotuspond_sdk.executable.types import schema_definitions

    definitions = [{"name": f"s{i}", "schema": INTEGER} for i in range(64)]
    assert len(schema_definitions(definitions)) == 64
    with pytest.raises(ExecutableError):
        schema_definitions([*definitions, {"name": "s64", "schema": INTEGER}])
    fields = [{"name": f"f{i}", "schema": INTEGER, "required": True} for i in range(64)]
    assert len(parse_schema({"kind": "object", "fields": fields}).fields) == 64
    with pytest.raises(ExecutableError):
        parse_schema(
            {
                "kind": "object",
                "fields": [*fields, {"name": "extra", "schema": INTEGER, "required": True}],
            }
        )


def test_public_import_does_not_emit_schema_shadow_warnings():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-W", "error::UserWarning", "-c", "import lotuspond_sdk.executable"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
