"""Linux descriptor-pinned private C/P persistence. Session proof is never stored."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
import sys
from pathlib import Path

from .errors import FleetError
from .protocol import CredentialState, validate_token


def _regular(fd: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise FleetError("unsafe_storage")


def private_directory(path: Path) -> int:
    """Walk without following links, pinning each ancestor before opening its child."""
    if sys.platform != "linux" or not path.is_absolute() or ".." in path.parts:
        raise FleetError("unsafe_storage")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            info = os.fstat(fd)
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in (0, os.geteuid()) or (info.st_mode & 0o022 and not sticky_root):
                raise FleetError("unsafe_storage")
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
            )
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise FleetError("unsafe_storage")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_private_file(path: Path, limit: int) -> bytes:
    """Private operator input; all ancestors and final inode are checked."""
    directory = -1
    fd = -1
    try:
        directory = private_directory(path.parent)
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory
        )
        _regular(fd)
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise FleetError("input_limit")
        return data
    except OSError:
        raise FleetError("unsafe_storage") from None
    finally:
        if fd >= 0:
            os.close(fd)
        if directory >= 0:
            os.close(directory)


class CredentialStore:
    def __init__(self, directory: Path):
        self._directory = self._lock = -1
        self._checkpoint = lambda point: None
        self._failed = False
        try:
            self._directory = private_directory(directory)
            self._lock = os.open(
                "lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                0o600,
                dir_fd=self._directory,
            )
            _regular(self._lock)
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, FleetError):
            self.close()
            raise FleetError("unsafe_storage") from None

    def _check(self):
        if self._directory < 0 or self._failed:
            raise FleetError("unsafe_storage")
        info = os.fstat(self._directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise FleetError("unsafe_storage")
        _regular(self._lock)

    def _load(self):
        fd = os.open(
            "state.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=self._directory,
        )
        try:
            _regular(fd)
            data = os.read(fd, 8193)
            if len(data) > 8192:
                raise FleetError("unsafe_storage")
            state = CredentialState.model_validate_json(data)
            validate_token(state.active_token, "lpr_")
            if state.pending_token is not None:
                validate_token(state.pending_token, "lpr_")
            return state
        finally:
            os.close(fd)

    def load(self) -> CredentialState:
        try:
            self._check()
            return self._load()
        except (OSError, FleetError):
            raise FleetError("unsafe_storage") from None

    def save(self, expected_revision: int | None, state: CredentialState) -> None:
        fd = -1
        try:
            self._check()
            state = CredentialState.model_validate(state)
            validate_token(state.active_token, "lpr_")
            if state.pending_token is not None:
                validate_token(state.pending_token, "lpr_")
            if expected_revision is not None and type(expected_revision) is not int:
                raise FleetError("unsafe_storage")
            try:
                current = self._load()
            except FileNotFoundError:
                current = None
            if expected_revision is None:
                if current is not None or state.revision != 0:
                    raise FleetError("unsafe_storage")
            elif (
                current is None
                or current.revision != expected_revision
                or state.revision != expected_revision + 1
            ):
                raise FleetError("unsafe_storage")
            raw = state.model_dump(mode="json")
            raw["active_token"] = state.active_token.get_secret_value()
            raw["pending_token"] = (
                state.pending_token.get_secret_value() if state.pending_token is not None else None
            )
            content = json.dumps(raw, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            if len(content) > 8192:
                raise FleetError("unsafe_storage")
            name = ".state-" + secrets.token_hex(16)
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=self._directory,
            )
            _regular(fd)
            self._checkpoint("temp_created")
            remaining = memoryview(content)
            while remaining:
                count = os.write(fd, remaining)
                if count <= 0:
                    raise FleetError("unsafe_storage")
                remaining = remaining[count:]
            self._checkpoint("written")
            os.fsync(fd)
            self._checkpoint("file_fsynced")
            os.close(fd)
            fd = -1
            os.rename(name, "state.json", src_dir_fd=self._directory, dst_dir_fd=self._directory)
            self._checkpoint("renamed")
            os.fsync(self._directory)
            self._checkpoint("directory_fsynced")
        except (OSError, FleetError):
            self._failed = True
            raise FleetError("unsafe_storage") from None
        finally:
            if fd >= 0:
                os.close(fd)

    def close(self):
        for name in ("_lock", "_directory"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
