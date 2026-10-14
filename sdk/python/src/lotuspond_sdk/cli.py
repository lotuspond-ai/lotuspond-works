"""Bounded local workflow-package tools; no execution or authority is granted."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .compatibility import check_declared_compatibility
from .contract import canonical_package_json, check_json_bounds, package_digest
from .diff import diff_packages
from .errors import SDK_ERRORS, SdkError
from .files import generate_keypair, load_package, load_signing_key, read_regular, write_new
from .input import READ_ERRORS, PackageReadError, decode_package_bytes, safe_validation_errors
from .signing import sign_package, verify_package
from .trust import MAX_REPORT_BYTES


class _HelpRequested(Exception):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        # argparse's original message may contain private paths or values.
        raise PackageReadError("invalid_arguments")

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if status == 0:
            raise _HelpRequested
        raise PackageReadError("invalid_arguments")


def _parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(prog="lotuspond-package", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    for name, help_text in (
        ("validate", "Validate without executing the package"),
        ("digest", "Report the validated canonical content digest"),
        ("canonicalize", "Write canonical content exclusively to an explicit file"),
        ("sign", "Write a detached signature using an explicit owner-only key"),
        ("verify", "Verify against an explicit independent trust set"),
        ("compatibility", "Compare explicit synthetic capability declarations"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("path", type=Path)
        if name in ("canonicalize", "sign"):
            command.add_argument("--output", type=Path, required=True)
        if name == "sign":
            command.add_argument("--key", type=Path, required=True)
        if name == "verify":
            command.add_argument("--signature", type=Path, required=True)
            command.add_argument("--trust", type=Path, required=True)
        if name == "compatibility":
            command.add_argument("--profile", type=Path, required=True)
            command.add_argument("--requirements", type=Path, required=True)
    difference = commands.add_parser("diff", help="Compare content; values require explicit opt-in")
    difference.add_argument("before", type=Path)
    difference.add_argument("after", type=Path)
    difference.add_argument("--include-values", action="store_true")
    keygen = commands.add_parser("keygen", help="Exclusively create an Ed25519 key pair")
    keygen.add_argument("--private", type=Path, required=True)
    keygen.add_argument("--public", type=Path, required=True)
    return parser


def _auxiliary(path: Path, limit: int, invalid_code: str) -> object:
    raw = decode_package_bytes(read_regular(path, max_bytes=limit))
    try:
        # Reuse the shared JSON item/Unicode bounds before strict auxiliary models.
        check_json_bounds(raw)
    except ValueError:
        raise SdkError(invalid_code) from None
    return raw


def _execute(args: argparse.Namespace) -> tuple[dict, int]:
    if args.command == "keygen":
        return {"ok": True, "result": {"key_id": generate_keypair(args.private, args.public)}}, 0
    if args.command == "diff":
        difference = diff_packages(
            load_package(args.before), load_package(args.after), include_values=args.include_values
        )
        return {"ok": True, "result": difference.model_dump(mode="json")}, 0
    package = load_package(args.path)
    if args.command == "validate":
        # Preserve legacy field order, shape and presentation exactly.
        return {
            "ok": True,
            "schema_version": package.schema_version,
            "sha256": package_digest(package),
        }, 0
    if args.command in ("digest", "canonicalize"):
        digest = package_digest(package)
        if args.command == "canonicalize":
            write_new(args.output, canonical_package_json(package))
        return {"ok": True, "result": {"sha256": digest}}, 0
    if args.command == "sign":
        envelope = sign_package(package, load_signing_key(args.key))
        write_new(args.output, (envelope.model_dump_json() + "\n").encode("ascii"))
        return {"ok": True, "result": {"sha256": envelope.sha256, "key_id": envelope.key_id}}, 0
    if args.command == "verify":
        verified = verify_package(
            package,
            _auxiliary(args.signature, 4096, "invalid_signature_envelope"),
            _auxiliary(args.trust, 65536, "invalid_trust_set"),
        )
        return {"ok": verified.valid, "result": verified.model_dump(mode="json")}, (
            0 if verified.valid else 3
        )
    compatible = check_declared_compatibility(
        package,
        _auxiliary(args.profile, 65536, "invalid_capability_profile"),
        _auxiliary(args.requirements, 16384, "invalid_compatibility_requirements"),
    )
    return {"ok": compatible.compatible, "result": compatible.model_dump(mode="json")}, (
        0 if compatible.compatible else 3
    )


def _error(code: str, message: str) -> dict:
    return {"ok": False, "errors": [{"path": [], "code": code, "message": message}]}


def main(argv: list[str] | None = None) -> int:
    explicit = sys.argv[1:] if argv is None else argv
    if explicit and explicit[0] == "v2":
        from .executable.cli import main as executable_main

        return executable_main(explicit[1:])
    try:
        args = _parser().parse_args(argv)
        result, status = _execute(args)
        rendered = json.dumps(result, ensure_ascii=True, allow_nan=False)
        if len(rendered) + 1 > MAX_REPORT_BYTES:
            raise SdkError("report_too_large")
    except _HelpRequested:
        return 0
    except PackageReadError as error:
        result, status = _error(error.code, READ_ERRORS[error.code]), 2
    except SdkError as error:
        result, status = _error(error.code, SDK_ERRORS[error.code]), 2
    except ValidationError as error:
        result, status = {"ok": False, "errors": safe_validation_errors(error)}, 2
    if status == 2:
        # Fixed-code errors and at most 20 sanitized contract errors are small.
        rendered = json.dumps(result, ensure_ascii=True, allow_nan=False)
    print(rendered)
    return status


if __name__ == "__main__":
    sys.exit(main())
