"""Private invocation journal: what this runtime may already have done, durably.

One owner-only file per attempt in a private directory (`state.private_directory`
rules: owner-only ancestors, no symlinks, 0600 regular files), held by one process
at a time through an exclusive lock, written by atomic replacement with file and
directory fsync. Phases only move forward:

    claimed -> started -> finished -> recorded
                       -> abandoned

`started` is durable before any provider is reached. `finished` holds the outcome
the worker is about to report (identities, codes and usage numbers, never values),
so it survives a crash as evidence for reconciliation. After a restart an attempt in
`started` (or a record that cannot be read) is ambiguous: it is abandoned and never
invoked again. A `fenced` marker persists an unconfirmed provider cleanup across
restarts until an operator removes it. A `declines` count keeps the worker's decline
circuit breaker across workers and restarts. Entries hold identities and digests only,
never values, prompts or secrets.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
from pathlib import Path

from .errors import FleetError
from .state import private_directory

PHASES = ("claimed", "started", "finished", "recorded", "abandoned")
_ORDER = {"claimed": 0, "started": 1, "finished": 2, "recorded": 3, "abandoned": 3}
_NAME = re.compile(r"attempt-[0-9a-f-]{36}\.json")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TEMP = ".journal-"
_FENCE = "fenced"
_DECLINES = "declines"
MAX_ENTRY = 8192


def _regular(fd):
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise FleetError("unsafe_storage")


class InvocationJournal:
    def __init__(self, directory: Path, *, checkpoint=lambda point: None):
        self._directory = self._lock = -1
        self._checkpoint = checkpoint
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
            # One journal holder: two workers can never both claim and invoke.
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # A temporary file is never authoritative: one left by a crash is removed.
            for name in os.listdir(self._directory):
                if name.startswith(_TEMP):
                    os.unlink(name, dir_fd=self._directory)
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

    @staticmethod
    def _name(attempt_id):
        if type(attempt_id) is not str or not _UUID.fullmatch(attempt_id):
            raise FleetError("invalid_request")
        return f"attempt-{attempt_id}.json"

    def _open(self, name):
        return os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self._directory
        )

    def get(self, attempt_id: str) -> dict | None:
        """The attempt's entry, None when absent; an unreadable entry is ambiguous."""
        self._check()
        name = self._name(attempt_id)
        try:
            fd = self._open(name)
        except FileNotFoundError:
            return None
        except OSError:
            raise FleetError("unsafe_storage") from None
        try:
            _regular(fd)
        except FleetError:
            os.close(fd)
            raise
        try:
            data = os.read(fd, MAX_ENTRY + 1)
            entry = json.loads(data) if len(data) <= MAX_ENTRY else None
            if (
                type(entry) is not dict
                or entry.get("attempt_id") != attempt_id
                or entry.get("phase") not in PHASES
            ):
                raise ValueError
            return entry
        except (OSError, ValueError):
            # A torn or foreign record proves nothing: treat it as possibly started.
            return {"attempt_id": attempt_id, "phase": "started", "unreadable": True}
        finally:
            os.close(fd)

    def _write(self, name, content: bytes):
        self._check()
        temporary = _TEMP + secrets.token_hex(16)
        fd = -1
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=self._directory,
            )
            _regular(fd)
            view = memoryview(content)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError
                view = view[count:]
            os.fsync(fd)
            os.close(fd)
            fd = -1
            self._checkpoint("before_replace")
            os.rename(temporary, name, src_dir_fd=self._directory, dst_dir_fd=self._directory)
            os.fsync(self._directory)
            self._checkpoint("after_replace")
        except (OSError, FleetError):
            self._failed = True
            try:
                os.unlink(temporary, dir_fd=self._directory)
            except OSError:
                pass
            raise FleetError("unsafe_storage") from None
        finally:
            if fd >= 0:
                os.close(fd)

    def advance(self, attempt_id: str, phase: str, **identity) -> dict:
        """Move forward only; the delivery identity can never change once written.

        An unreadable entry may only be abandoned: it proves nothing, so it is
        treated as possibly started and never resumed.
        """
        if phase not in PHASES:
            raise FleetError("invalid_request")
        current = self.get(attempt_id)
        entry = {"attempt_id": attempt_id, "phase": phase}
        for key in ("delivery_sha256", "generation", "result_sha256", "outcome", "group", "boot"):
            if key in identity:
                entry[key] = identity[key]
        if current is not None and current.get("unreadable"):
            if phase != "abandoned":
                raise FleetError("conflict")
        elif current is not None:
            if _ORDER[phase] < _ORDER[current["phase"]]:
                raise FleetError("conflict")
            if current["phase"] in ("recorded", "abandoned") and phase != current["phase"]:
                raise FleetError("conflict")
            for key in ("delivery_sha256", "generation"):
                if key in current and entry.get(key, current[key]) != current[key]:
                    raise FleetError("conflict")
                if key in current:
                    entry.setdefault(key, current[key])
            for key in ("outcome", "group", "boot"):
                if key in current:
                    entry.setdefault(key, current[key])
        content = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(content) > MAX_ENTRY:
            raise FleetError("input_limit")
        self._write(self._name(attempt_id), content)
        return entry

    def ambiguous(self) -> list[str]:
        """Attempts that may have reached a provider without a recorded outcome."""
        self._check()
        found = []
        for name in sorted(os.listdir(self._directory)):
            if _NAME.fullmatch(name):
                entry = self.get(name[len("attempt-") : -len(".json")])
                if entry and entry["phase"] == "started":
                    found.append(entry["attempt_id"])
        return found

    def fence(self):
        """Persist an unconfirmed provider cleanup; only an operator removes it."""
        if not self.fenced():
            self._write(_FENCE, b'{"fenced":true}')

    def fenced(self) -> bool:
        self._check()
        try:
            fd = self._open(_FENCE)
        except FileNotFoundError:
            return False
        except OSError:
            raise FleetError("unsafe_storage") from None
        try:
            _regular(fd)
        finally:
            os.close(fd)
        return True

    def declines_in_a_row(self) -> int:
        """Declines since the last verified delivery, across workers and restarts.

        An unreadable count proves nothing, so it reads as the most allowed:
        nothing more is declined until a delivery verifies again.
        """
        self._check()
        try:
            fd = self._open(_DECLINES)
        except FileNotFoundError:
            return 0
        except OSError:
            raise FleetError("unsafe_storage") from None
        try:
            _regular(fd)
            data = os.read(fd, 65)
            count = json.loads(data).get("in_a_row") if len(data) <= 64 else None
            if type(count) is not int or count < 0:
                raise ValueError
            return count
        except (OSError, ValueError, AttributeError):
            return 2**31
        finally:
            os.close(fd)

    def count_decline(self):
        self._write(_DECLINES, b'{"in_a_row":%d}' % min(self.declines_in_a_row() + 1, 2**31))

    def clear_declines(self):
        if self.declines_in_a_row():
            self._write(_DECLINES, b'{"in_a_row":0}')

    def close(self):
        for attribute in ("_lock", "_directory"):
            fd = getattr(self, attribute)
            if fd >= 0:
                os.close(fd)
                setattr(self, attribute, -1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
