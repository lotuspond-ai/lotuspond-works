"""Closed, value-free executable failures and bounded diagnostic pointers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

ErrorCode = Literal[
    "invalid_json_value",
    "invalid_unicode",
    "nonfinite_number",
    "safe_integer",
    "input_too_large",
    "input_too_deep",
    "input_too_complex",
    "cyclic_input",
    "invalid_package",
    "invalid_catalog",
    "invalid_schema",
    "invalid_mapping",
    "duplicate_identifier",
    "unknown_reference",
    "unavailable_value",
    "type_mismatch",
    "dependency_missing",
    "dependency_conflict",
    "dependency_cycle",
    "dependency_digest_mismatch",
    "unsupported_operation",
    "expansion_exceeded",
    "intent_mismatch",
    "verification_missing",
    "invalid_wire",
    "invalid_context",
    "invalid_profile",
    "invalid_requirements",
    "invalid_envelope",
    "report_too_large",
]
ERROR_MESSAGES = {
    "invalid_json_value": "Input must contain only supported JSON values.",
    "invalid_unicode": "Input contains invalid Unicode.",
    "nonfinite_number": "Numbers must be finite.",
    "safe_integer": "Integers must be within the interoperable safe-integer range.",
    "input_too_large": "Input exceeds the byte limit.",
    "input_too_deep": "Input exceeds the nesting limit.",
    "input_too_complex": "Input exceeds the item limit.",
    "cyclic_input": "Input must not contain cyclic objects.",
    "invalid_package": "The executable package is invalid.",
    "invalid_catalog": "The supplied catalog is invalid.",
    "invalid_schema": "The data schema is invalid.",
    "invalid_mapping": "The mapping is invalid.",
    "duplicate_identifier": "Declaration identifiers must be unique.",
    "unknown_reference": "A reference is not declared.",
    "unavailable_value": "A referenced value is not available.",
    "type_mismatch": "The supplied types do not match.",
    "dependency_missing": "An exact dependency definition is missing.",
    "dependency_conflict": "Dependency definitions conflict.",
    "dependency_cycle": "Dependencies must not contain cycles.",
    "dependency_digest_mismatch": "A dependency digest does not match its definition.",
    "unsupported_operation": "The operation is not supported.",
    "expansion_exceeded": "Conservative execution expansion exceeds the declared bound.",
    "intent_mismatch": "The action intent bindings do not match.",
    "verification_missing": "Required action verification is missing.",
    "invalid_wire": "The runtime wire record is invalid.",
    "invalid_context": "The supplied context is invalid.",
    "invalid_profile": "The synthetic capability profile is invalid.",
    "invalid_requirements": "The explicit compatibility requirements are invalid.",
    "invalid_envelope": "The signature envelope is invalid.",
    "report_too_large": "The report exceeds the supported byte limit.",
}
MAX_ISSUES = 20
# A closed vocabulary: never extend this set with names discovered in supplied data.
_SAFE_TOKENS = frozenset(
    """
schema_version package_id version name id kind value format_version authority_granted synthetic
body steps schemas fields schema required input output models connectors knowledge tools policies
limits max_steps active_seconds elapsed_seconds retained_waits max_input_tokens max_output_tokens
max_cost_microusd timeout_seconds backoff_seconds max_attempts retry_codes min max min_length
max_length precision scale max_items items ref source pointer fn args rounding label instructions
profile revision connector connector_ref operation secret_ref secret_refs operations model_ref
condition then otherwise branches exports result dependencies digest sha256 package version
installation workspace project environment execution step action target input_digest target_digest
policy_digest package_digest configuration_digest compiled_plan_digest connector_artifact epoch
recovery_epoch recipient worker generation command_id event_id sequence issued_at expires_at
observed_at outcome evidence verification state status reasons issues truncated code message
before after changes category path before_path after_path assurance algorithm canonicalization
compiler_revision validator_revision key_id signature requirements capabilities
""".split()
)


def _code(code: object) -> ErrorCode:
    if type(code) is not str or code not in ERROR_MESSAGES:
        raise ValueError("Executable errors require a supported fixed code.")
    return code


def _safe_pointer(pointer: object) -> str:
    if type(pointer) is not str:
        raise ValueError("Diagnostic pointers must be strings.")
    if not pointer:
        return ""
    # Bound before splitting attacker-controlled text; output never contains supplied names.
    parts = pointer[:512].split("/")[1:] if pointer.startswith("/") else ["*"]
    safe = []
    for part in parts:
        numeric = part.isascii() and part.isdecimal() and len(part) <= 16
        safe.append(part if part in _SAFE_TOKENS or numeric else "*")
    return ("/" + "/".join(safe))[:512]


@dataclass(frozen=True, slots=True)
class ExecutableIssue:
    code: ErrorCode
    pointer: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _code(self.code))
        object.__setattr__(self, "pointer", _safe_pointer(self.pointer))

    @property
    def message(self) -> str:
        return ERROR_MESSAGES[self.code]


class ExecutableError(ValueError):
    """Fixed failure; truncating diagnostics never changes its failure status."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        issues: list[ExecutableIssue] | tuple[ExecutableIssue, ...] = (),
        truncated: bool = False,
    ):
        self.code = _code(code)
        if type(truncated) is not bool or type(issues) not in (list, tuple):
            raise ValueError("Error diagnostics require exact bounded types.")
        if any(type(issue) is not ExecutableIssue for issue in issues[:MAX_ISSUES]):
            raise ValueError("Error diagnostics require fixed issues.")
        # Reconstruct even frozen records: object.__setattr__ can bypass their constructor.
        self.issues = tuple(
            ExecutableIssue(issue.code, issue.pointer) for issue in issues[:MAX_ISSUES]
        )
        self.truncated = truncated or len(issues) > MAX_ISSUES
        super().__init__(ERROR_MESSAGES[self.code])


def safe_validation_issues(
    error: ValidationError, *, code: ErrorCode
) -> tuple[tuple[ExecutableIssue, ...], bool]:
    """Discard exception text, values, unknown field names and custom error codes."""
    code = _code(code)
    issues = []
    for item in error.errors(include_url=False, include_context=False, include_input=False)[
        :MAX_ISSUES
    ]:
        tokens = []
        for part in item["loc"]:
            if type(part) is int and 0 <= part <= 9007199254740991:
                tokens.append(str(part))
            elif type(part) is str and part in _SAFE_TOKENS:
                tokens.append(part)
            else:
                tokens.append("*")
        issues.append(ExecutableIssue(code, "/" + "/".join(tokens) if tokens else ""))
    return tuple(issues), error.error_count() > MAX_ISSUES
