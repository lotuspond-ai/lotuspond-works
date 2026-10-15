"""Explicit bounded local executable-v2 tooling; never executes a workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

from ..cli import SafeArgumentParser, _HelpRequested
from ..errors import SDK_ERRORS, SdkError
from ..files import load_signing_key, read_regular, write_new
from ..input import READ_ERRORS, PackageReadError
from ._validation import StrictModel, report_bytes
from .compatibility import (
    COMPATIBILITY_MODELS,
    ExecutableCompatibilityReport,
    check_declared_compatibility,
    validate_executable_compatibility_report,
)
from .compiler import compile_package
from .diff import DIFF_MODELS, ExecutableDiff, diff_executable_packages, validate_executable_diff
from .errors import ExecutableError
from .input import decode_executable_bytes
from .metadata import COMPILER_REVISION
from .package import Digest
from .plan import canonical_plan_json, compiled_plan_digest
from .schema import SchemaKind, schema_bytes
from .signing import (
    SIGNING_MODELS,
    ExecutableVerificationReport,
    executable_signature_envelope_json,
    sign_executable_package,
    validate_executable_verification_report,
    verify_executable_package,
)
from .validation import canonical_executable_json, executable_digest, validate_executable_package


class DigestResult(StrictModel):
    sha256: Digest


class ValidationResult(DigestResult):
    schema_version: Literal["lotuspond.workflow-package.v2"] = "lotuspond.workflow-package.v2"
    compilation_status: Literal["not_checked"] = "not_checked"
    authority_granted: Literal[False] = False


class CompileResult(StrictModel):
    package_sha256: Digest
    plan_sha256: Digest
    compiler_revision: Literal["lotuspond.workflow-compiler.v2.1"] = COMPILER_REVISION
    authority_granted: Literal[False] = False


class SigningResult(DigestResult):
    key_id: Digest


class SchemaResult(DigestResult):
    kind: SchemaKind
    format: Literal["json-schema-2020-12"] = "json-schema-2020-12"


class Output(StrictModel):
    ok: bool
    result: (
        ValidationResult
        | SigningResult
        | SchemaResult
        | DigestResult
        | CompileResult
        | ExecutableDiff
        | ExecutableVerificationReport
        | ExecutableCompatibilityReport
    )


OUTPUT_MODELS = (
    Output,
    DigestResult,
    ValidationResult,
    CompileResult,
    SigningResult,
    SchemaResult,
    *DIFF_MODELS,
    *SIGNING_MODELS,
    *COMPATIBILITY_MODELS,
)


def _parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(prog="lotuspond-package v2", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    for name in (
        "validate",
        "digest",
        "canonicalize",
        "compile",
        "sign",
        "verify",
        "compatibility",
    ):
        command = commands.add_parser(name)
        command.add_argument("path", type=Path)
        if name in ("canonicalize", "compile", "sign"):
            command.add_argument("--output", type=Path, required=True)
        if name == "sign":
            command.add_argument("--key", type=Path, required=True)
        if name == "verify":
            command.add_argument("--signature", type=Path, required=True)
            command.add_argument("--trust", type=Path, required=True)
        if name == "compatibility":
            command.add_argument("--profile", type=Path, required=True)
            command.add_argument("--requirements", type=Path, required=True)
        if name in ("compile", "compatibility"):
            command.add_argument("--catalog", type=Path, required=True)
            command.add_argument("--dependency", type=Path, action="append", default=[])
    difference = commands.add_parser("diff")
    difference.add_argument("before", type=Path)
    difference.add_argument("after", type=Path)
    difference.add_argument("--include-values", action="store_true")
    schema = commands.add_parser("schema")
    schema.add_argument("--kind", choices=("package", "plan", "runtime"), required=True)
    schema.add_argument("--output", type=Path, required=True)
    return parser


def _read(path: Path, limit: int = 1048576) -> object:
    return decode_executable_bytes(read_regular(path, max_bytes=limit))


def _execute(args: argparse.Namespace) -> tuple[object, int]:
    if args.command == "schema":
        data = schema_bytes(args.kind)
        write_new(args.output, data)
        return SchemaResult(sha256=hashlib.sha256(data).hexdigest(), kind=args.kind), 0
    if args.command == "diff":
        result = diff_executable_packages(
            _read(args.before), _read(args.after), include_values=args.include_values
        )
        return validate_executable_diff(result), 0
    if args.command in ("compile", "compatibility") and len(args.dependency) > 63:
        raise ExecutableError("input_too_complex")
    package = validate_executable_package(_read(args.path))
    if args.command in ("validate", "digest"):
        return ValidationResult(sha256=executable_digest(package)), 0
    if args.command == "canonicalize":
        write_new(args.output, canonical_executable_json(package))
        return DigestResult(sha256=executable_digest(package)), 0
    if args.command == "sign":
        envelope = sign_executable_package(package, load_signing_key(args.key))
        write_new(args.output, executable_signature_envelope_json(envelope))
        return SigningResult(sha256=envelope.sha256, key_id=envelope.key_id), 0
    if args.command == "verify":
        result = validate_executable_verification_report(
            verify_executable_package(
                package, _read(args.signature, 4096), _read(args.trust, 65536)
            )
        )
        return result, 0 if result.valid else 3
    dependencies = [_read(path) for path in args.dependency]
    catalog = _read(args.catalog)
    if args.command == "compile":
        plan = compile_package(package, dependencies=dependencies, catalog=catalog)
        write_new(args.output, canonical_plan_json(plan))
        return CompileResult(
            package_sha256=plan.package_sha256, plan_sha256=compiled_plan_digest(plan)
        ), 0
    result = validate_executable_compatibility_report(
        check_declared_compatibility(
            package,
            _read(args.profile, 65536),
            _read(args.requirements, 16384),
            dependencies=dependencies,
            catalog=catalog,
        )
    )
    return result, 0 if result.compatible else 3


def main(argv: list[str] | None = None) -> int:
    try:
        result, status = _execute(_parser().parse_args(argv))
        if type(status) is not int or status not in (0, 3):
            raise ExecutableError("invalid_json_value")
        if type(result) is ExecutableDiff:
            result = validate_executable_diff(result)
        elif type(result) is ExecutableVerificationReport:
            result = validate_executable_verification_report(result)
            if (status == 0) != result.valid:
                raise ExecutableError("invalid_json_value")
        elif type(result) is ExecutableCompatibilityReport:
            result = validate_executable_compatibility_report(result)
            if (status == 0) != result.compatible:
                raise ExecutableError("invalid_json_value")
        data = report_bytes(
            Output(ok=status == 0, result=result),
            model_type=Output,
            model_types=OUTPUT_MODELS,
            code="invalid_json_value",
        )
    except _HelpRequested:
        return 0
    except (ExecutableError, SdkError, PackageReadError) as error:
        if isinstance(error, ExecutableError):
            errors = [
                {"path": issue.pointer, "code": issue.code, "message": issue.message}
                for issue in error.issues
            ]
            errors = errors or [{"path": "", "code": error.code, "message": str(error)}]
            result = {"ok": False, "errors": errors, "errors_truncated": error.truncated}
        else:
            messages = SDK_ERRORS if isinstance(error, SdkError) else READ_ERRORS
            result = {
                "ok": False,
                "errors": [{"path": [], "code": error.code, "message": messages[error.code]}],
                "errors_truncated": False,
            }
        data = (
            json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
        ).encode("ascii")
        status = 2
    sys.stdout.write(data.decode("ascii"))
    return status
