"""Validate one local declarative workflow package without executing it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .contract import package_digest
from .files import load_package
from .input import (
    READ_ERRORS,
    PackageReadError,
    safe_validation_errors,
)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PackageReadError("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    validate = commands.add_parser("validate", help="Validate without executing the package")
    validate.add_argument("path", type=Path)
    try:
        args = parser.parse_args(argv)
        package = load_package(args.path)
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
