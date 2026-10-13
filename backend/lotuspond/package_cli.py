"""Validate one local declarative workflow package without executing it."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from pydantic import ValidationError

from .package_contract import MAX_PACKAGE_BYTES, package_digest, validate_package
from .package_input import (
    READ_ERRORS,
    PackageReadError,
    decode_package_bytes,
    safe_validation_errors,
)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PackageReadError("invalid_arguments")


def read_package(path: Path) -> object:
    try:
        # Nonblocking open also avoids hanging on FIFOs before regular-file validation.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise PackageReadError("file_unreadable")
            data = source.read(MAX_PACKAGE_BYTES + 1)
    except (OSError, ValueError) as error:
        if isinstance(error, PackageReadError):
            raise
        raise PackageReadError("file_unreadable") from None
    return decode_package_bytes(data)


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    validate = commands.add_parser("validate", help="Validate without executing the package")
    validate.add_argument("path", type=Path)
    try:
        args = parser.parse_args(argv)
        package = validate_package(read_package(args.path))
        result = {
            "ok": True,
            "schema_version": package.schema_version,
            "sha256": package_digest(package),
        }
    except PackageReadError as error:
        result = {
            "ok": False,
            "errors": [{"path": [], "code": error.code, "message": READ_ERRORS[error.code]}],
        }
    except ValidationError as error:
        result = {"ok": False, "errors": safe_validation_errors(error)}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
