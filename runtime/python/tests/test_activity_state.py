"""Task 10: the private invocation journal and supervised provider children.

Journal storage follows `state.private_directory`, Linux-only by design.
"""

import os
import socket
import struct
import subprocess
import threading
import time

import pytest
from activity_env import build
from fixtures.activity_provider import calls

import lotuspond_runtime.activity_invocation as invocation
from lotuspond_runtime.activity_framing import Cancelled, receive
from lotuspond_runtime.activity_state import InvocationJournal
from lotuspond_runtime.activity_worker import ActivityWorker, invoke_read, verify_delivery
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.providers import Fenced

ATTEMPT = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
ARGUMENTS = {"target": None, "input": None}


def journal(tmp_path, **options):
    directory = tmp_path / "journal"
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    return InvocationJournal(directory, **options), directory


def test_phases_only_move_forward_and_identity_is_fixed(tmp_path):
    store, directory = journal(tmp_path)
    store.advance(ATTEMPT, "claimed", delivery_sha256="a" * 64, generation=1)
    store.advance(ATTEMPT, "started")
    with pytest.raises(FleetError, match="conflict"):
        store.advance(ATTEMPT, "claimed")  # never backwards
    with pytest.raises(FleetError, match="conflict"):
        store.advance(ATTEMPT, "finished", delivery_sha256="b" * 64)  # identity is fixed
    store.advance(ATTEMPT, "finished", outcome={"code": "succeeded"})
    store.advance(ATTEMPT, "recorded", result_sha256="c" * 64)
    with pytest.raises(FleetError, match="conflict"):
        store.advance(ATTEMPT, "abandoned")  # an outcome is final
    entry = store.get(ATTEMPT)
    assert (entry["phase"], entry["delivery_sha256"], entry["generation"]) == (
        "recorded",
        "a" * 64,
        1,
    )
    assert entry["outcome"] == {"code": "succeeded"}  # kept as evidence
    path = directory / f"attempt-{ATTEMPT}.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert not any(name.startswith(".journal-") for name in os.listdir(directory))


def test_a_torn_record_is_ambiguous_and_recovery_abandons_every_one(tmp_path):
    env = build(tmp_path)
    store = env.journal
    store.advance(ATTEMPT, "claimed")
    store.advance(OTHER, "claimed")
    store.advance(OTHER, "started")
    (env.journal_dir / f"attempt-{ATTEMPT}.json").write_text('{"phase": "clai')  # torn write
    assert store.get(ATTEMPT)["phase"] == "started"
    assert store.ambiguous() == [ATTEMPT, OTHER]
    with pytest.raises(FleetError, match="conflict"):
        store.advance(ATTEMPT, "started")  # never resumed
    # Recovery does not stop at the unreadable record: both are abandoned.
    assert ActivityWorker(env.deps).recover() == [ATTEMPT, OTHER]
    assert store.ambiguous() == []
    assert [store.get(a)["phase"] for a in (ATTEMPT, OTHER)] == ["abandoned", "abandoned"]


def test_unsafe_journal_storage_is_refused(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    os.chmod(loose, 0o755)
    with pytest.raises(FleetError, match="unsafe_storage"):
        InvocationJournal(loose)
    store, directory = journal(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text('{"attempt_id": "x", "phase": "recorded"}')
    os.symlink(target, directory / f"attempt-{ATTEMPT}.json")
    # A planted link is never followed: the journal refuses instead of trusting it.
    with pytest.raises(FleetError, match="unsafe_storage"):
        store.get(ATTEMPT)
    # A planted FIFO is refused at once, never waited on.
    os.mkfifo(directory / f"attempt-{OTHER}.json", 0o600)
    started = time.monotonic()
    with pytest.raises(FleetError, match="unsafe_storage"):
        store.get(OTHER)
    assert time.monotonic() - started < 1
    # A record readable by others is not the journal's.
    third = "00000000-0000-4000-8000-000000000003"
    loose_file = directory / f"attempt-{third}.json"
    loose_file.write_text(f'{{"attempt_id": "{third}", "phase": "recorded"}}')
    os.chmod(loose_file, 0o644)
    with pytest.raises(FleetError, match="unsafe_storage"):
        store.get(third)


def test_one_process_holds_the_journal(tmp_path):
    store, directory = journal(tmp_path)
    with pytest.raises(FleetError, match="unsafe_storage"):
        InvocationJournal(directory)  # a second worker would both claim and invoke
    store.close()
    InvocationJournal(directory).close()


def test_a_crash_before_replacement_keeps_the_previous_phase(tmp_path):
    armed = []

    def checkpoint(point):
        if armed and point == "before_replace":
            raise RuntimeError("crash")  # the process dies with the write half done

    store, directory = journal(tmp_path, checkpoint=checkpoint)
    store.advance(ATTEMPT, "claimed", delivery_sha256="a" * 64, generation=1)
    armed.append(True)
    with pytest.raises(RuntimeError):
        store.advance(ATTEMPT, "started")
    store.close()
    reopened = InvocationJournal(directory)
    assert reopened.get(ATTEMPT)["phase"] == "claimed"  # never a torn half state
    assert not any(name.startswith(".journal-") for name in os.listdir(directory))


def test_the_fence_survives_a_restart(tmp_path):
    store, directory = journal(tmp_path)
    assert not store.fenced()
    store.fence()
    store.close()
    assert InvocationJournal(directory).fenced()


def read_call(env):
    attempt = env.server.root("connector_read")
    envelope, records = verify_delivery(attempt.delivery, env.deps)
    deadline = env.deps.clock.now_ms() + 20000
    return lambda cancel: invoke_read(env.deps, envelope, records, ARGUMENTS, cancel, deadline)


class AfterStart:
    """Cancels once the provider has observably started (its counter was written)."""

    def __init__(self, counter):
        self.counter, self.at = counter, None

    def is_set(self):
        if self.at is None and calls(self.counter) >= 1:
            self.at = time.monotonic()
        return self.at is not None


def test_a_child_ignoring_term_is_killed_and_cleanup_confirmed(tmp_path):
    env = build(tmp_path, read_script={"read": {"ignore_term": True, "sleep_s": 30}})
    cancel = AfterStart(env.counter)
    outcome = read_call(env)(cancel)
    elapsed = time.monotonic() - cancel.at
    assert outcome.code == "cancelled" and outcome.cleanup.confirmed is True
    # TERM was ignored (installed before the counter was written): KILL followed
    # the 1 s grace, and exit was confirmed within 1 s more.
    assert 0.9 < elapsed < 3.0
    assert not invocation.fenced()


def test_unconfirmed_cleanup_fences_all_further_provider_work(tmp_path, monkeypatch):
    env = build(tmp_path, read_script={"read": {"sleep_s": 5}})
    monkeypatch.setattr(invocation, "_fenced", False)
    monkeypatch.setattr(invocation, "_gone", lambda pid: False)
    call = read_call(env)
    outcome = call(AfterStart(env.counter))
    assert outcome.cleanup.confirmed is False
    assert invocation.fenced()
    assert env.journal.fenced()  # persisted where it was detected, before anything else
    with pytest.raises(Fenced):
        call(threading.Event())


def test_a_group_outliving_its_leader_is_stopped():
    child = subprocess.Popen(
        ["sh", "-c", "sleep 30 & exit 0"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    child.wait(timeout=5)
    assert not invocation._gone(child.pid)  # the leader is gone, its group is not
    cleanup = invocation._finish(child, graceful=True)
    assert cleanup.confirmed is True and invocation._gone(child.pid)


def test_an_interrupt_during_a_call_still_stops_the_group(tmp_path, monkeypatch):
    env = build(tmp_path, read_script={"read": {"sleep_s": 30}})
    spawned = []
    original = subprocess.Popen

    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(invocation.subprocess, "Popen", tracked)

    class Interrupt:
        def is_set(self):
            if calls(env.counter) >= 1:
                raise KeyboardInterrupt  # the operator stops the worker mid-call
            return False

    with pytest.raises(KeyboardInterrupt):
        read_call(env)(Interrupt())
    (child,) = spawned
    assert invocation._gone(child.pid) and not invocation.fenced()


def test_a_stalled_reply_frame_still_observes_cancellation():
    left, right = socket.socketpair()
    try:
        right.sendall(struct.pack("!I", 100))  # a length prefix, then nothing
        cancel = threading.Event()
        threading.Timer(0.2, cancel.set).start()
        started = time.monotonic()
        with pytest.raises(Cancelled):
            receive(left, time.monotonic_ns() + 10_000_000_000, cancel)
        assert time.monotonic() - started < 1
    finally:
        left.close()
        right.close()
