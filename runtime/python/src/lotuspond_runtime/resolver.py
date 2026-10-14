"""Private descriptor-pinned version mounts; values are never cached or hashed."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from .canonical import decode_fleet_json
from .errors import FleetError
from .state import private_directory


def _directory(fd):
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise FleetError("secret_unavailable")


def _identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
    )


def _read(directory, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > limit
        ):
            raise FleetError("secret_unavailable")
        data = bytearray()
        while True:
            chunk = os.read(fd, min(8192, limit + 1 - len(data)))
            data.extend(chunk)
            if len(data) > limit:
                raise FleetError("secret_unavailable")
            if not chunk:
                break
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            _identity(before) != _identity(after)
            or _identity(before) != _identity(current)
            or len(data) != before.st_size
        ):
            raise FleetError("secret_unavailable")
        return bytes(data)
    finally:
        os.close(fd)


class MountedSecretResolver:
    def __init__(self, root: Path, mapping_file: Path):
        self.root, self.mapping_file = root, mapping_file

    def resolve(self, binding_id: str, version: int) -> bytes:
        directories = []
        mapping_dir = -1
        try:
            if (
                type(binding_id) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", binding_id)
                or type(version) is not int
                or not 1 <= version <= 2147483647
            ):
                raise FleetError("secret_unavailable")
            root = private_directory(self.root)
            directories.append(root)
            mapping_dir = private_directory(self.mapping_file.parent)
            mapping = decode_fleet_json(_read(mapping_dir, self.mapping_file.name, 65536))
            if type(mapping) is not dict or set(mapping) != {"bindings"}:
                raise FleetError("secret_unavailable")
            rows = mapping["bindings"]
            if type(rows) is not list or len(rows) > 256:
                raise FleetError("secret_unavailable")
            selected, seen = None, set()
            for row in rows:
                if type(row) is not dict or set(row) != {"binding_id", "version", "filename"}:
                    raise FleetError("secret_unavailable")
                binding, number, filename = row["binding_id"], row["version"], row["filename"]
                if (
                    type(binding) is not str
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", binding)
                    or type(number) is not int
                    or not 1 <= number <= 2147483647
                    or type(filename) is not str
                    or len(filename) > 1024
                    or not filename
                    or filename.startswith("/")
                    or "\\" in filename
                    or any(p in ("", ".", "..") for p in filename.split("/"))
                    or any(ord(c) < 32 for c in filename)
                    or (binding, number) in seen
                ):
                    raise FleetError("secret_unavailable")
                seen.add((binding, number))
                if (binding, number) == (binding_id, version):
                    selected = filename
            if selected is None:
                raise FleetError("secret_unavailable")
            parts = selected.split("/")
            for part in parts[:-1]:
                _directory(directories[-1])
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=directories[-1],
                )
                directories.append(child)
                _directory(child)
            _directory(directories[-1])
            value = _read(directories[-1], parts[-1], 65536)
            for directory in directories:
                _directory(directory)
            return value
        except (OSError, ValueError, TypeError, FleetError):
            raise FleetError("secret_unavailable") from None
        finally:
            if mapping_dir >= 0:
                os.close(mapping_dir)
            for directory in reversed(directories):
                os.close(directory)
