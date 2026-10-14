"""Bound raw executable JSON and project only explicitly allowed model classes."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TypeAlias

from pydantic import BaseModel

from ..input import PackageReadError, _check_nesting, _constant, _unique_object
from .errors import ExecutableError

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class JsonLimits:
    max_bytes: int
    max_depth: int
    max_items: int

    def __post_init__(self) -> None:
        for value, maximum in (
            (self.max_bytes, 8388608),
            (self.max_depth, 32),
            (self.max_items, 262144),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("JSON limits require supported exact positive integers.")


PACKAGE_LIMITS = JsonLimits(1048576, 32, 10000)
WIRE_LIMITS = JsonLimits(65536, 32, 10000)
REPORT_LIMITS = JsonLimits(8388608, 32, 262144)


def _integer(token: str) -> int:
    if len(token.lstrip("-")) > 309:
        raise PackageReadError("number_too_large")
    return int(token)


def _float(token: str) -> float:
    if len(token) > 1024:
        raise PackageReadError("number_too_large")
    result = float(token)
    if not math.isfinite(result):
        raise PackageReadError("nonfinite_number")
    return result


def decode_executable_bytes(content: bytes) -> object:
    """Decode under package limits, leaving numeric-domain selection to declared fields."""
    if type(content) is not bytes:
        raise ExecutableError("invalid_json_value")
    if len(content) > PACKAGE_LIMITS.max_bytes:
        raise PackageReadError("input_too_large")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise PackageReadError("invalid_utf8") from None
    _check_nesting(text)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_constant,
            parse_int=_integer,
            parse_float=_float,
        )
    except PackageReadError:
        raise
    except (ValueError, RecursionError):
        raise PackageReadError("invalid_json") from None
    return project_json(value)


def project_json(
    raw: object,
    *,
    model_types: tuple[type[BaseModel], ...] = (),
    limits: JsonLimits = PACKAGE_LIMITS,
) -> JsonValue:
    """Copy exact JSON/known fields without hooks; bound raw shape before validation.

    This is a projection, not schema validation. Callers must subsequently validate
    the returned JSON and project the normalized model again to count defaults.
    Dictionary keys count as items; depth counts nested containers, root scalar zero.
    Raw integers are at most 1024 bits until the declared field selects its domain.
    """
    if type(limits) is not JsonLimits or type(model_types) is not tuple:
        raise ExecutableError("invalid_json_value")
    if any(not isinstance(kind, type) or not issubclass(kind, BaseModel) for kind in model_types):
        raise ExecutableError("invalid_json_value")
    try:
        limits = JsonLimits(limits.max_bytes, limits.max_depth, limits.max_items)
    except (AttributeError, ValueError):
        raise ExecutableError("invalid_json_value") from None
    active: set[int] = set()
    count = 0
    string_bytes = 0
    size_code = (
        "report_too_large" if limits.max_bytes > PACKAGE_LIMITS.max_bytes else "input_too_large"
    )

    def visit(value: object, depth: int) -> JsonValue:
        nonlocal count, string_bytes
        count += 1
        if count > limits.max_items:
            raise ExecutableError("input_too_complex")
        if depth > limits.max_depth:
            raise ExecutableError("input_too_deep")
        kind = type(value)
        if kind is str:
            if len(value) > limits.max_bytes:
                raise ExecutableError(size_code)
            try:
                string_bytes += len(value.encode("utf-8"))
            except UnicodeEncodeError:
                raise ExecutableError("invalid_unicode") from None
            if string_bytes > limits.max_bytes:
                raise ExecutableError(size_code)
            return value
        if kind is float:
            if not math.isfinite(value):
                raise ExecutableError("nonfinite_number")
            return value
        if value is None or kind is bool:
            return value
        if kind is int:
            if value.bit_length() > 1024:
                raise ExecutableError("invalid_json_value")
            return value
        if kind in model_types:
            try:
                data = object.__getattribute__(value, "__dict__")
                extras = object.__getattribute__(value, "__pydantic_extra__")
            except AttributeError:
                raise ExecutableError("invalid_json_value") from None
            if (
                type(data) is not dict
                or any(type(key) is not str for key in data)
                or set(data) - set(kind.model_fields)
                or (extras is not None and (type(extras) is not dict or extras))
            ):
                raise ExecutableError("invalid_json_value")
        elif kind in (dict, list):
            data = value
        else:
            raise ExecutableError("invalid_json_value")
        if id(value) in active:
            raise ExecutableError("cyclic_input")
        if depth >= limits.max_depth:
            raise ExecutableError("input_too_deep")
        needed = len(data) * (1 if kind is list else 2)
        if count + needed > limits.max_items:
            raise ExecutableError("input_too_complex")
        active.add(id(value))
        try:
            if kind is list:
                return [visit(child, depth + 1) for child in data]
            if any(type(key) is not str for key in data):
                raise ExecutableError("invalid_json_value")
            return {visit(key, depth + 1): visit(child, depth + 1) for key, child in data.items()}
        finally:
            active.remove(id(value))

    projected = visit(raw, 0)
    # Only exact JSON remains, so this cannot invoke an arbitrary serializer.
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    total = 0
    for chunk in encoder.iterencode(projected):
        total += len(chunk.encode("utf-8"))
        if total > limits.max_bytes:
            raise ExecutableError(size_code)
    return projected
