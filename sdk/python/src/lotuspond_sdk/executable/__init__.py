"""V2 executable-definition foundations; local checks confer no authority."""

from .errors import ExecutableError, ExecutableIssue
from .expressions import Mapping, TypeEnvironment, infer_mapping, parse_mapping, presence_guard
from .input import decode_executable_bytes
from .metadata import (
    CANONICALIZATION,
    COMPILER_REVISION,
    PLAN_SCHEMA,
    SCHEMA_VERSION,
    VALIDATOR_REVISION,
)
from .types import (
    DataSchema,
    DataType,
    ExportSchema,
    FlowArrayType,
    FlowObjectType,
    FlowType,
    HandleType,
    NamedSchema,
    OriginChoice,
    SourceSymbol,
    assert_assignable,
    iteration_item_flow,
    parse_export,
    parse_schema,
    resolve_schema,
    select_array_item,
    validate_value,
)

__all__ = [
    "CANONICALIZATION",
    "COMPILER_REVISION",
    "ExecutableError",
    "ExecutableIssue",
    "PLAN_SCHEMA",
    "SCHEMA_VERSION",
    "VALIDATOR_REVISION",
    "decode_executable_bytes",
    "DataSchema",
    "DataType",
    "ExportSchema",
    "FlowArrayType",
    "FlowObjectType",
    "FlowType",
    "HandleType",
    "Mapping",
    "NamedSchema",
    "OriginChoice",
    "SourceSymbol",
    "TypeEnvironment",
    "assert_assignable",
    "infer_mapping",
    "iteration_item_flow",
    "parse_export",
    "parse_mapping",
    "parse_schema",
    "presence_guard",
    "resolve_schema",
    "select_array_item",
    "validate_value",
]
