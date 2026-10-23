"""Shared Task 10 harness: a private journal, a fake control plane and dependencies."""

from __future__ import annotations

import dataclasses
import os
import time
from types import SimpleNamespace

from fixtures.activity_provider import FakeControlPlane, descriptors, identity

from lotuspond_runtime.activity_state import InvocationJournal
from lotuspond_runtime.activity_worker import ActivityDependencies, ServerClock
from lotuspond_runtime.providers import ProviderSet


def wall_ms():
    return time.time_ns() // 1_000_000


def anchored(server) -> ServerClock:
    """The runtime's clock, anchored on the control plane's time as a session would be."""
    clock = ServerClock()
    sent = time.monotonic()
    now = server.now_ms()
    clock.anchor(now, sent=sent, received=time.monotonic())
    return clock


def build(
    tmp_path,
    *,
    read_script=None,
    model_script=None,
    context=None,
    secrets=None,
    credentials=(),
    **plane,
):
    """A worker environment; synthetic providers only, explicitly in test mode."""
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(mode=0o700)
    os.chmod(journal_dir, 0o700)
    counter = tmp_path / "provider-calls"
    server = FakeControlPlane(identity(), wall_ms, **plane)
    journal = InvocationJournal(journal_dir)
    deps = ActivityDependencies(
        identity=server.identity,
        client=server,
        records=server,
        values=server,
        trust=server,
        providers=ProviderSet(
            descriptors(read_script, model_script, counter, credentials), test_mode=True
        ),
        journal=journal,
        clock=anchored(server),
        context=context,
        secrets=secrets,
        renew_every_ms=1000,
        retry_pause_s=0.01,
    )
    return SimpleNamespace(
        server=server, deps=deps, journal=journal, counter=counter, journal_dir=journal_dir
    )


def reopen(env):
    """A restarted worker: the old journal handle is closed, the directory reopened."""
    env.journal.close()
    journal = InvocationJournal(env.journal_dir)
    deps = dataclasses.replace(env.deps, journal=journal)
    return SimpleNamespace(**{**vars(env), "journal": journal, "deps": deps})
