"""E05 Task 8: read-back rule semantics (pure; no database)."""

import pytest

from lotuspond.execution.action_rules import ABSENT, PRESENT, UNKNOWN, evaluate_rules, settle
from lotuspond.execution.expressions import ExpressionError, ValueEnvironment

INTEGER = {"kind": "integer", "min": -1000, "max": 1000}
EXPECTED = ValueEnvironment(input={"amount_minor": 5, "currency": "EUR"})


def literal(value, schema=INTEGER):
    return {"kind": "literal", "schema": schema, "value": value}


def compare(op, pointer, expected):
    return {"op": op, "readback_pointer": pointer, "expected": expected}


def present(pointer):
    return {"op": "present", "readback_pointer": pointer}


def both(op, *rules):
    return {"op": op, "rules": list(rules)}


def holds(rules, readback, expected=EXPECTED):
    return evaluate_rules(rules, readback, expected)


def test_rule_semantics():
    # Every comparison operator against a literal, on both sides of the value.
    table = {
        "eq": (False, True, False),
        "ne": (True, False, True),
        "lt": (True, False, False),
        "le": (True, True, False),
        "gt": (False, False, True),
        "ge": (False, True, True),
    }
    for op, expected in table.items():
        for value, outcome in zip((4, 5, 6), expected, strict=True):
            assert holds([compare(op, "/n", literal(5))], {"n": value}) is outcome, (op, value)
    # `expected` mappings read the write's input with E04's expression semantics.
    write_amount = {"kind": "ref", "source": {"kind": "package_input"}, "pointer": "/amount_minor"}
    assert holds([compare("eq", "/n", write_amount)], {"n": 5}) is True
    assert holds([compare("eq", "/n", write_amount)], {"n": 6}) is False
    text = {"kind": "string", "max_length": 8}
    currency = {"kind": "ref", "source": {"kind": "package_input"}, "pointer": "/currency"}
    assert holds([compare("eq", "/c", currency)], {"c": "EUR"}) is True
    assert holds([compare("ne", "/c", literal("USD", text))], {"c": "EUR"}) is True
    # Nested pointers and array indexes select exactly; never a coercion.
    assert holds([compare("eq", "/a/1/b", literal(2))], {"a": [{"b": 1}, {"b": 2}]}) is True
    assert holds([present("/a/2")], {"a": [1, 2]}) is False
    # A list is an implicit `and`; nested `and` and `or`.
    yes, no = compare("eq", "/n", literal(5)), compare("eq", "/n", literal(9))
    assert holds([yes, yes], {"n": 5}) is True
    assert holds([yes, no], {"n": 5}) is False
    assert holds([both("or", no, yes)], {"n": 5}) is True
    assert holds([both("or", no, no)], {"n": 5}) is False
    assert holds([both("and", yes, both("or", no, yes))], {"n": 5}) is True
    assert holds([both("or", both("and", yes, no), both("and", yes, yes))], {"n": 5}) is True
    # A missing pointer: false for `present`, an error for a comparison.
    assert holds([present("/n")], {"n": 5}) is True
    assert holds([present("/n")], {}) is False
    assert holds([present("/n")], {"n": None}) is True  # a present null is present
    with pytest.raises(ExpressionError) as missing:
        holds([compare("eq", "/n", literal(5))], {})
    assert missing.value.code == "unavailable_value"
    with pytest.raises(ExpressionError):
        holds([both("or", present("/m"), compare("eq", "/n", literal(5)))], {})
    # A `present` guard in the same list or `and` decides first, as the compiler types it.
    guarded = both("and", compare("eq", "/n", literal(5)), present("/n"))
    assert holds([guarded], {}) is False
    assert holds([guarded], {"n": 5}) is True
    assert holds([compare("gt", "/n", literal(1)), present("/n")], {}) is False
    # Mismatched kinds are refused, never coerced.
    for readback in ({"n": "5"}, {"n": True}, {"n": [5]}):
        with pytest.raises(ExpressionError) as mismatch:
            holds([compare("eq", "/n", literal(5))], readback)
        assert mismatch.value.code == "type_mismatch"
    with pytest.raises(ExpressionError):
        holds([compare("lt", "/b", literal(True, {"kind": "boolean"}))], {"b": False})
    # Malformed rules or environments are refused.
    for bad in ([], [{"op": "exists", "readback_pointer": "/n"}], [both("and", yes)]):
        with pytest.raises(ExpressionError):
            holds(bad, {"n": 5})
    with pytest.raises(ExpressionError):
        evaluate_rules([yes], {"n": 5}, {"input": None})


def test_settlement_is_never_a_guess():
    effect = [compare("eq", "/present", literal(True, {"kind": "boolean"}))]
    absence = [compare("eq", "/present", literal(False, {"kind": "boolean"}))]
    assert settle(effect, absence, {"present": True}, EXPECTED) == PRESENT
    assert settle(effect, absence, {"present": False}, EXPECTED) == ABSENT
    # An evaluation error, a contradiction or neither rule set holding is unknown.
    assert settle(effect, absence, {}, EXPECTED) == UNKNOWN
    assert settle(effect, effect, {"present": True}, EXPECTED) == UNKNOWN
    neither = [compare("eq", "/present", literal(True, {"kind": "boolean"})), present("/other")]
    assert settle(neither, neither, {"present": True}, EXPECTED) == UNKNOWN
