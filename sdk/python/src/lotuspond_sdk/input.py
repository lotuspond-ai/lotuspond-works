"""Shared bounded JSON decoding and value-free package errors."""

import json
import math

from pydantic import ValidationError

from .contract import ERROR_MESSAGES, MAX_JSON_DEPTH, MAX_PACKAGE_BYTES

READ_ERRORS = {
    "invalid_arguments": "Use validate followed by one package file path.",
    "file_unreadable": "The package must be a readable regular file.",
    "invalid_utf8": "The package file must contain UTF-8 text.",
    "invalid_json": "The package file must contain one valid JSON value.",
    "duplicate_key": "JSON object keys must be unique.",
    "number_too_large": "A JSON number exceeds the supported size.",
    **ERROR_MESSAGES,
}
SAFE_PATH_PARTS = {
    "schema_version",
    "package_id",
    "version",
    "name",
    "entry_node",
    "models",
    "connectors",
    "secret_refs",
    "nodes",
    "edges",
    "id",
    "profile",
    "revision",
    "connector",
    "secret_ref",
    "operations",
    "kind",
    "model_ref",
    "instructions",
    "limits",
    "max_steps",
    "timeout_seconds",
    "max_input_tokens",
    "max_output_tokens",
    "max_cost_microusd",
    "connector_ref",
    "operation",
    "source",
    "target",
    "start",
    "end",
    "agent",
    "human_review",
}


class PackageReadError(ValueError):
    def __init__(self, code: str):
        super().__init__(READ_ERRORS[code])
        self.code = code


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageReadError("duplicate_key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise PackageReadError("nonfinite_number")


def _integer(value: str) -> int:
    if len(value.lstrip("-")) > 19:
        raise PackageReadError("number_too_large")
    return int(value)


def _float(value: str) -> float:
    if len(value) > 64:
        raise PackageReadError("number_too_large")
    result = float(value)
    if not math.isfinite(result):
        raise PackageReadError("nonfinite_number")
    return result


def _check_nesting(text: str) -> None:
    """Reject deep input before the standard JSON parser descends recursively."""
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise PackageReadError("input_too_deep")
        elif character in "]}":
            depth -= 1


def decode_package_bytes(data: bytes) -> object:
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageReadError("input_too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PackageReadError("invalid_utf8") from None
    _check_nesting(text)
    try:
        return json.loads(
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


def safe_validation_errors(error: ValidationError) -> list[dict]:
    safe = []
    for item in error.errors(include_url=False, include_context=False, include_input=False)[:20]:
        path = [
            part if type(part) is int or part in SAFE_PATH_PARTS else "*" for part in item["loc"]
        ]
        code = item["type"]
        safe.append(
            {
                "path": path,
                "code": code,
                "message": ERROR_MESSAGES.get(code, "Field does not satisfy the package contract."),
            }
        )
    return safe
