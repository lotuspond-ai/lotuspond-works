"""Bounded local regular-file loading; no references are resolved."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .contract import MAX_PACKAGE_BYTES, WorkflowPackage, validate_package
from .errors import SdkError
from .input import PackageReadError, decode_package_bytes


def read_regular(path: Path, *, max_bytes: int, private: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if private:
        # Fail closed on systems that cannot safely refuse symlinks at open.
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "getuid"):
            raise SdkError("unsafe_private_key")
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise SdkError("file_unreadable")
            if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise SdkError("unsafe_private_key")
            if info.st_size > max_bytes:
                raise SdkError("input_too_large")
            data = source.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise SdkError("input_too_large")
            return data
    except (OSError, ValueError) as error:
        if isinstance(error, SdkError):
            raise
        raise SdkError("file_unreadable") from None


def load_package(path: Path) -> WorkflowPackage:
    try:
        data = read_regular(path, max_bytes=MAX_PACKAGE_BYTES)
    except SdkError as error:
        raise PackageReadError(error.code) from None
    return validate_package(decode_package_bytes(data))
