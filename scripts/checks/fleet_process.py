"""Bounded ownership for test-only fleet controllers and their fixed subprocesses."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class Cancelled(RuntimeError):
    """Sanitized cooperative controller interruption."""


@contextmanager
def cancellation_signals():
    def cancel(signum, frame):
        raise Cancelled("qualification cancelled")

    previous = {
        sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@dataclass(frozen=True)
class Outcome:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    cleanup_confirmed: bool


def _signal_group(pid, signum):
    try:
        os.killpg(pid, signum)
    except OSError:
        # The caller records unconfirmed cleanup if an exact group cannot be signalled.
        pass


def _absent(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _wait_absent(pid, deadline):
    while not _absent(pid) and time.monotonic() < deadline:
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    return _absent(pid)


def wait_owned(process, *, timeout, grace=30, receipt: Path | None = None):
    """Wait only for a Popen created with start_new_session=True by this caller.

    Cancellation is propagated after bounded child cleanup and receipt persistence.
    An unconfirmed group or forced escalation is reported, never inferred clean.
    """
    interrupted = None
    timed_out = escalated = False
    stdout = stderr = b""
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, Cancelled, KeyboardInterrupt) as error:
        interrupted = error
        timed_out = isinstance(error, subprocess.TimeoutExpired)
    finally:
        if (
            interrupted is not None
            or process.poll() is None
            or not _absent(process.pid)
        ):
            deadline = time.monotonic() + grace
            _signal_group(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(
                    timeout=max(0, deadline - time.monotonic())
                )
            except subprocess.TimeoutExpired as error:
                stdout, stderr = error.output or b"", error.stderr or b""
            # Leader exit and pipe EOF do not establish descendant cleanup. Both
            # collection and exact-group disappearance share one grace deadline.
            if not _wait_absent(process.pid, deadline) or process.poll() is None:
                escalated = True
                deadline = time.monotonic() + 5
                _signal_group(process.pid, signal.SIGKILL)
                try:
                    stdout, stderr = process.communicate(
                        timeout=max(0, deadline - time.monotonic())
                    )
                except subprocess.TimeoutExpired as error:
                    stdout, stderr = error.output or b"", error.stderr or b""
                _wait_absent(process.pid, deadline)
        confirmed = process.poll() is not None and _absent(process.pid)
        if receipt is not None:
            receipt.write_text(
                json.dumps(
                    {
                        "pid": process.pid,
                        "group": process.pid,
                        "exit": process.returncode,
                        "timed_out": timed_out,
                        "cancelled": interrupted is not None,
                        "escalated": escalated,
                        "cleanup_confirmed": confirmed,
                    },
                    indent=2,
                )
                + "\n"
            )
            receipt.with_suffix(".stdout").write_bytes(stdout)
            receipt.with_suffix(".stderr").write_bytes(stderr)
    if interrupted is not None and not timed_out:
        raise interrupted
    return Outcome(process.returncode, stdout, stderr, timed_out, confirmed)


def run_owned(args, *, cwd, env=None, timeout, grace=30, receipt=None):
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    return wait_owned(process, timeout=timeout, grace=grace, receipt=receipt)
