"""Deterministic read-back rules over the SDK's rule models (E05 Task 8).

A rule selects a value from a read-back result by its typed `readback_pointer` and
compares it with an `expected` mapping evaluated under E04's expression semantics.
Nothing is coerced: a missing pointer is false for `present` and an error
(`unavailable_value`) for a comparison, and values of different kinds are a
`type_mismatch`, exactly as E04's comparisons are.

Presence guards follow the SDK compiler (`compiler.rules`): the `present` rules of a
rule list, or of an `and` (directly or in nested `and`s), guard the comparisons
beside them. So a list or an `and` whose guard is false is false without evaluating
those comparisons. An `or` evaluates every alternative: the compiler refuses an
alternative that compares an optional value without its own guard.
"""

from __future__ import annotations

import re

from lotuspond_sdk.executable.package import ComparisonRule, LogicalRule, PresentRule, Rule
from pydantic import TypeAdapter

from .expressions import (
    MISSING,
    ExpressionError,
    ValueEnvironment,
    _compare,
    _tokens,
    evaluate_mapping,
)

_RULES = TypeAdapter(list[Rule])
# Every result a read-back can settle to (`ExecutionActionObservation.result`).
PRESENT, ABSENT, UNKNOWN = "effect_present", "effect_absent", "unknown"


def _select(readback, pointer):
    """The value at `pointer`, or MISSING; never a coercion (E04's `_resolve`)."""
    value = readback
    for token in _tokens(pointer):
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


def _guards(rules):
    """The `present` rules that guard this list: its own and nested `and`s' (compiler)."""
    found = []
    for rule in rules:
        if isinstance(rule, PresentRule):
            found.append(rule)
        elif isinstance(rule, LogicalRule) and rule.op == "and":
            found.extend(_guards(rule.rules))
    return found


def _all(rules, readback, expected, definitions):
    if not all(_select(readback, g.readback_pointer) is not MISSING for g in _guards(rules)):
        return False
    # Every rule is evaluated: none is short-circuited past its guards.
    results = [_one(rule, readback, expected, definitions) for rule in rules]
    return all(results)


def _one(rule, readback, expected, definitions):
    if isinstance(rule, PresentRule):
        return _select(readback, rule.readback_pointer) is not MISSING
    if isinstance(rule, ComparisonRule):
        selected = _select(readback, rule.readback_pointer)
        if selected is MISSING:
            raise ExpressionError("unavailable_value")
        value = evaluate_mapping(rule.expected, expected, definitions)
        return _compare(rule.op, selected, value)
    if isinstance(rule, LogicalRule):
        if rule.op == "and":
            return _all(rule.rules, readback, expected, definitions)
        results = [_one(item, readback, expected, definitions) for item in rule.rules]
        return any(results)
    raise ExpressionError("invalid_mapping")


def evaluate_rules(rules, readback, expected: ValueEnvironment, definitions=()) -> bool:
    """Whether every rule holds for this typed read-back value.

    `rules` are SDK rule models (or their JSON); `readback` is the result already
    typed against the read operation's output schema (`typed_schema`), and
    `expected` the environment the `expected` mappings read (for reconciliation,
    the write's input as `package_input`). Raises `ExpressionError` with E04's
    fixed codes; never returns a guess.
    """
    if type(expected) is not ValueEnvironment:
        raise ExpressionError("invalid_mapping")
    try:
        parsed = _RULES.validate_python(
            [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in rules]
        )
    except Exception:
        raise ExpressionError("invalid_mapping") from None
    if not parsed:
        raise ExpressionError("invalid_mapping")
    return _all(parsed, readback, expected, list(definitions))


def settle(effect_rules, absence_rules, readback, expected, definitions=()) -> str:
    """A read-back's result: present, absent, or unknown when the rules cannot decide.

    Both rule sets are evaluated. Any evaluation error, neither holding, or both
    holding (a contradiction) is `unknown`: an ambiguous read never settles an effect.
    """
    try:
        effect = evaluate_rules(effect_rules, readback, expected, definitions)
        absence = evaluate_rules(absence_rules, readback, expected, definitions)
    except ExpressionError:
        return UNKNOWN
    if effect and not absence:
        return PRESENT
    if absence and not effect:
        return ABSENT
    return UNKNOWN
