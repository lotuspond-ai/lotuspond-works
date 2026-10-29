"""Supervised bounded provider children for activities.

Each provider call runs in its own child process group, started from a fixed module
with an empty environment. No API credential, runtime session or signed delivery
reaches the child; connector and provider secrets are resolved by the worker
immediately before the call and passed only over the child's private socket. The
child learns its own deadline from the request and dies with the worker (Linux
parent-death signal).

Every call ends with the group gone: cancellation (lease loss, deadline, shutdown or
any exception, including an interrupt) sends SIGTERM to the whole group, allows 1 s,
then SIGKILL, and confirms exit within 1 s more; a group that outlives a finished
leader is stopped the same way. Process groups are lifecycle controls, not
hostile-code isolation. If cleanup cannot be confirmed the worker is fenced: it
refuses all further provider work.
"""

from __future__ import annotations

import dataclasses
import os
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from .activity_framing import Cancelled, encode, receive, send
from .errors import FleetError
from .providers import FAILURES, ActivityFailure, Fenced

TERM_GRACE_S = 1.0
KILL_CONFIRM_S = 1.0
# No single provider call outlives an hour, whatever deadline it was given.
MAX_CALL_MS = 3_600_000

_lock = threading.Lock()
_fenced = False


@dataclass(frozen=True)
class Cleanup:
    confirmed: bool


@dataclass(frozen=True)
class ChildOutcome:
    """`reply` on success; otherwise `code` (a fixed result code).

    `started` means the request reached the child, so the provider may have been
    reached. A failing provider may report `usage`, and `reached` False when it
    positively sent nothing.
    """

    reply: dict | None
    code: str | None
    cleanup: Cleanup
    started: bool
    usage: dict | None = None
    reached: bool | None = None


def fenced() -> bool:
    with _lock:
        return _fenced


def _fence():
    global _fenced
    with _lock:
        _fenced = True


def _gone(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def stop_group(child) -> Cleanup:
    """TERM, 1 s grace, KILL, 1 s to confirm; fence the worker if it cannot confirm."""
    try:
        for sig, wait in ((signal.SIGTERM, TERM_GRACE_S), (signal.SIGKILL, KILL_CONFIRM_S)):
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                child.poll()
                if child.returncode is not None and _gone(child.pid):
                    return Cleanup(True)
                time.sleep(0.02)
        child.poll()
        confirmed = child.returncode is not None and _gone(child.pid)
    except OSError:
        confirmed = False
    if not confirmed:
        _fence()
    return Cleanup(confirmed)


def _finish(child, graceful: bool) -> Cleanup:
    """The group is gone afterwards, or the worker is fenced."""
    if child is None:
        return Cleanup(True)
    if graceful:
        try:
            child.wait(timeout=KILL_CONFIRM_S)
        except subprocess.TimeoutExpired:
            pass
    child.poll()
    if child.returncode is not None and _gone(child.pid):
        return Cleanup(True)
    return stop_group(child)


def _decode(answer, started) -> ChildOutcome:
    if type(answer) is dict and set(answer) == {"ok"} and type(answer["ok"]) is dict:
        return ChildOutcome(answer["ok"], None, Cleanup(False), started)
    if (
        type(answer) is dict
        and "failure" in answer
        and set(answer)
        <= {
            "failure",
            "usage",
            "not_reached",
        }
    ):
        code = answer["failure"] if answer["failure"] in FAILURES else "provider_failed"
        usage = answer.get("usage") if type(answer.get("usage")) is dict else None
        reached = False if answer.get("not_reached") is True else None
        return ChildOutcome(None, code, Cleanup(False), started, usage, reached)
    return ChildOutcome(None, "provider_failed", Cleanup(False), started)


def call_provider(
    request: dict, *, deadline_ns: int, cancel, fence=None, spawned=None
) -> ChildOutcome:
    """Run one provider call in a fresh child; never raises for provider failures.

    `cancel` is anything with `is_set()`; it is polled every 50 ms, also while a
    reply is arriving (a lease keeper renews from inside that call). `fence`, when
    given, persists an unconfirmed cleanup at once (the worker's journal), so a
    crash right after it cannot lose the fence. `spawned(group)`, when given,
    records the child's process group before the child receives its request.
    """
    if fenced():
        raise Fenced  # earlier cleanup was not confirmed
    parent = channel = child = None
    started = answered = False
    outcome = ChildOutcome(None, "provider_failed", Cleanup(False), False)
    try:
        timeout_ms = (deadline_ns - time.monotonic_ns()) // 1_000_000
        if timeout_ms <= 0:
            raise ActivityFailure("provider_failed", reached=False)
        request = dict(request, timeout_ms=min(timeout_ms, MAX_CALL_MS))
        encode(request)  # bounded before anything is spawned
        parent, channel = socket.socketpair()
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "lotuspond_runtime.activity_child",
                str(channel.fileno()),
                str(os.getpid()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={},
            pass_fds=(channel.fileno(),),
            close_fds=True,
            start_new_session=True,
        )
        channel.close()
        channel = None
        if spawned is not None:
            spawned(child.pid)
        send(parent, request, deadline_ns)
        started = True
        with selectors.DefaultSelector() as selector:
            selector.register(parent, selectors.EVENT_READ)
            # The answer, the deadline or a cancellation, whichever is first.
            while True:
                if cancel.is_set():
                    outcome = ChildOutcome(None, "cancelled", Cleanup(False), started)
                    break
                if time.monotonic_ns() >= deadline_ns:
                    outcome = ChildOutcome(None, "provider_failed", Cleanup(False), started)
                    break
                if selector.select(0.05):
                    outcome = _decode(receive(parent, deadline_ns, cancel), started)
                    answered = True
                    break
    except Cancelled:
        outcome = ChildOutcome(None, "cancelled", Cleanup(False), started)
    except ActivityFailure as failure:
        outcome = ChildOutcome(None, failure.code, Cleanup(False), started, None, failure.reached)
    except (FleetError, OSError, ValueError, subprocess.SubprocessError):
        outcome = ChildOutcome(None, "provider_failed", Cleanup(False), started)
    finally:
        # Also on an interrupt or any other exception: the group never outlives us.
        cleanup = _finish(child, graceful=answered)
        if not cleanup.confirmed and fence is not None:
            try:
                fence()
            except Exception:
                pass  # the in-process fence holds; the worker persists it again
        for stream in (parent, channel):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
    return dataclasses.replace(outcome, cleanup=cleanup)


def require(outcome: ChildOutcome):
    """The reply of a successful call, or an ActivityFailure with its fixed code."""
    if outcome.reply is None:
        reached = False if not outcome.started else outcome.reached
        raise ActivityFailure(outcome.code or "provider_failed", reached=reached)
    return outcome.reply
