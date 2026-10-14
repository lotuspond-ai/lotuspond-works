"""Common strict records and explicit known-model validation/report boundaries."""

from __future__ import annotations

import json
from typing import Literal, TypeVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from ._canonical import jcs_bytes
from .errors import ErrorCode, ExecutableError, safe_validation_issues
from .input import PACKAGE_LIMITS, REPORT_LIMITS, JsonLimits, JsonValue, project_json

Model = TypeVar("Model", bound=BaseModel)


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )

    @model_validator(mode="before")
    @classmethod
    def exact_fixed_types(cls, raw: object) -> object:
        """Pydantic Literal equality alone admits bool/int/float lookalikes."""
        if type(raw) is dict:
            for name, field in cls.model_fields.items():
                if name not in raw:
                    continue
                annotation = field.annotation
                variants = get_args(annotation)
                if type(None) in variants and len(variants) == 2:
                    if raw[name] is None:
                        continue
                    annotation = next(item for item in variants if item is not type(None))
                expected = get_args(annotation) if get_origin(annotation) is Literal else ()
                boolean = annotation is bool or (
                    expected and all(type(item) is bool for item in expected)
                )
                integer = annotation is int or (
                    expected and all(type(item) is int for item in expected)
                )
                if boolean and type(raw[name]) is not bool:
                    raise ValueError("Boolean fields require exact booleans.")
                if integer:
                    if type(raw[name]) is not int:
                        raise ValueError("Integer fields require exact integers.")
                    if not -9007199254740991 <= raw[name] <= 9007199254740991:
                        raise ValueError("Integer fields require safe integers.")
        return raw


def validate_model(
    raw: object,
    *,
    model_type: type[Model],
    model_types: tuple[type[BaseModel], ...],
    code: ErrorCode,
    limits: JsonLimits = PACKAGE_LIMITS,
) -> Model:
    """Revalidate fields, then bound complete normalized JCS including defaults.

    Model validators must select every declared numeric domain before returning;
    unsafe raw integers must become finite floats only in declared number fields.
    The projection's structural byte lower bound is not final admission evidence.
    """
    validated, normalized = _validated_json(
        raw, model_type=model_type, model_types=model_types, code=code, limits=limits
    )
    jcs_bytes(normalized, max_bytes=limits.max_bytes)
    return validated


def _validated_json(
    raw: object,
    *,
    model_type: type[Model],
    model_types: tuple[type[BaseModel], ...],
    code: ErrorCode,
    limits: JsonLimits,
) -> tuple[Model, JsonValue]:
    """Share known-model revalidation; the caller measures its actual output format."""
    if model_type not in model_types:
        raise ExecutableError("invalid_json_value")
    projected = project_json(raw, model_types=model_types, limits=limits)
    try:
        validated = model_type.model_validate(projected)
    except ValidationError as error:
        issues, truncated = safe_validation_issues(error, code=code)
        raise ExecutableError(code, issues=issues, truncated=truncated) from None
    normalized = project_json(validated, model_types=model_types, limits=limits)
    return validated, normalized


def report_bytes(
    raw: object,
    *,
    model_type: type[Model],
    model_types: tuple[type[BaseModel], ...],
    code: ErrorCode,
    max_bytes: int = 8388608,
) -> bytes:
    """Revalidate and emit ASCII JSON plus newline under the final report cap.

    CLI wrappers must be included in the model passed here, so the complete
    emitted report is counted. This never calls a supplied model serializer.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= REPORT_LIMITS.max_bytes:
        raise ExecutableError("invalid_json_value")
    _, projected = _validated_json(
        raw, model_type=model_type, model_types=model_types, code=code, limits=REPORT_LIMITS
    )
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    output = bytearray()
    for chunk in encoder.iterencode(projected):
        if len(output) + len(chunk) + 1 > max_bytes:
            raise ExecutableError("report_too_large")
        output.extend(chunk.encode("ascii"))
    output.extend(b"\n")
    return bytes(output)
