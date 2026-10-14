"""V2 executable-definition foundations; local checks confer no authority."""

from .errors import ExecutableError, ExecutableIssue
from .input import decode_executable_bytes
from .metadata import (
    CANONICALIZATION,
    COMPILER_REVISION,
    PLAN_SCHEMA,
    SCHEMA_VERSION,
    VALIDATOR_REVISION,
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
]
