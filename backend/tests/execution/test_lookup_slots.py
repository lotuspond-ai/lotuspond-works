"""Finding 3 (owner decision, 2026-10-29): provider lookups wait for their slot.

A concurrent caller waits for the slot, and its own timeout bounds the wait and the
call together. Still one call in flight per provider, a stuck provider refuses every
caller within its bound, and a late answer is never used.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.publication import _Lookup


class Provider:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls, self.active, self.overlap = [], 0, 0

    def current(self, value):
        self.active += 1
        self.overlap = max(self.overlap, self.active)
        self.calls.append(value)
        if len(self.calls) == 1:
            self.entered.set()
            self.release.wait(5)
        self.active -= 1
        return value


def test_a_concurrent_caller_waits_and_is_served():
    slot, provider = _Lookup(), Provider()
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(slot.call, provider, "current", ("first",), 2, "authority_unavailable")
        assert provider.entered.wait(5)
        second = pool.submit(
            slot.call, provider, "current", ("second",), 2, "authority_unavailable"
        )
        time.sleep(0.2)
        assert not second.done()  # waiting for the slot, not refused at once
        provider.release.set()
        assert first.result(timeout=5) == "first"
        assert second.result(timeout=5) == "second"
    assert provider.overlap == 1


def test_a_stuck_provider_refuses_each_caller_within_its_bound():
    slot, provider = _Lookup(), Provider()
    try:
        with pytest.raises(ExecutionError, match="authority_unavailable"):
            slot.call(provider, "current", ("stuck",), 0.1, "authority_unavailable")
        assert provider.entered.is_set()
        for _ in range(5):
            started = time.monotonic()
            with pytest.raises(ExecutionError, match="authority_unavailable"):
                slot.call(provider, "current", ("waiting",), 0.1, "authority_unavailable")
            # The wait and the call share one bound: never two timeouts' worth.
            assert time.monotonic() - started < 0.1 + 0.05
        assert provider.calls == ["stuck"]  # no waiter reached the provider
    finally:
        provider.release.set()


def test_a_waiter_gets_only_the_time_its_wait_left():
    slot, provider = _Lookup(), Provider()
    provider_slow = threading.Event()

    class Slow:
        def current(self, value):
            provider_slow.wait(5)
            return value

    with ThreadPoolExecutor(1) as pool:
        held = pool.submit(slot.call, provider, "current", ("first",), 2, "authority_unavailable")
        assert provider.entered.wait(5)
        threading.Timer(0.3, provider.release.set).start()
        started = time.monotonic()
        with pytest.raises(ExecutionError, match="authority_unavailable"):
            # Waits about 0.3 s for the slot, then the slow provider outlasts what is left.
            slot.call(Slow(), "current", ("late",), 0.5, "authority_unavailable")
        assert time.monotonic() - started < 0.5 + 0.05
        provider_slow.set()
        assert held.result(timeout=5) == "first"
    # The late answer is discarded, never cached: the next call asks again.
    assert slot.call(provider, "current", ("next",), 2, "authority_unavailable") == "next"


def test_a_closed_slot_refuses_its_waiters():
    slot, provider = _Lookup(), Provider()
    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(slot.call, provider, "current", ("first",), 2, "authority_unavailable")
        assert provider.entered.wait(5)
        waiting = pool.submit(
            slot.call, provider, "current", ("second",), 2, "authority_unavailable"
        )
        time.sleep(0.1)
        slot.closed = True
        provider.release.set()
        with pytest.raises(ExecutionError):
            held.result(timeout=5)
        with pytest.raises(ExecutionError):
            waiting.result(timeout=5)
    assert provider.calls == ["first"]
