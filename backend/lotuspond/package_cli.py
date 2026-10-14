"""Backward-compatible package CLI and decoded-object reader."""

import sys
from pathlib import Path

from lotuspond_sdk.cli import SafeArgumentParser as SafeArgumentParser
from lotuspond_sdk.cli import main as main
from lotuspond_sdk.contract import MAX_PACKAGE_BYTES
from lotuspond_sdk.errors import SdkError
from lotuspond_sdk.files import read_regular
from lotuspond_sdk.input import PackageReadError, decode_package_bytes


def read_package(path: Path) -> object:
    try:
        data = read_regular(path, max_bytes=MAX_PACKAGE_BYTES)
    except SdkError as error:
        raise PackageReadError(error.code) from None
    return decode_package_bytes(data)


if __name__ == "__main__":
    sys.exit(main())
