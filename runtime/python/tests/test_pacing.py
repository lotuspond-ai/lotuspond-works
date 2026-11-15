"""A worker's polls stay inside the control plane's per-instance poll allowance."""

from __future__ import annotations

import pytest

from lotuspond_runtime import cli
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.pacing import (
    FAILURE_PAUSE_S,
    FIRST_BACKOFF_S,
    IDLE_MAX_S,
    IDLE_MIN_S,
    MAX_BACKOFF_S,
    POLL_LIMIT_PER_MINUTE,
    PollPacer,
    run_paced,
)


class FakeTime:
    """A clock and a `stop` event whose waits advance the clock instead of sleeping."""

    def __init__(self):
        self.now = 1000.0
        self.waits = []
        self.stopped = False

    def clock(self):
        return self.now

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return self.stopped


def drive(statuses, *, work_s=0.0, limit=None):
    """Run the paced loop over `statuses`; returns the poll start times and the fake."""
    fake = FakeTime()
    pacer = PollPacer(clock=fake.clock)
    polls = []
    queue = list(statuses)

    def once():
        status = queue.pop(0)
        if status is not None:
            polls.append(fake.now)
        fake.now += work_s
        if not queue or (limit is not None and len(polls) >= limit):
            fake.stopped = True
        return status

    run_paced(fake, pacer, once)
    return polls, fake


def most_in_any_minute(times):
    return max(sum(1 for t in times if start <= t < start + 60) for start in times)


def test_the_interval_leaves_room_under_the_allowance():
    pacer = PollPacer()
    assert pacer.interval_s * POLL_LIMIT_PER_MINUTE > 60 * 1.05 - 1e-9


@pytest.mark.parametrize("status", ["completed", "declined", "idle", "refused"])
def test_a_busy_worker_never_exceeds_the_allowance_in_any_minute(status):
    # Before pacing, a worker with work after every poll asked every 0.2 s: 300 a minute.
    polls, _ = drive([status] * 400)
    assert most_in_any_minute(polls) <= POLL_LIMIT_PER_MINUTE - 2


def test_it_stays_under_with_arrival_jitter():
    polls, _ = drive(["completed"] * 400)
    # Each arrival up to a second late or early still fits the allowance.
    jittered = [t + (1.0 if i % 2 else -1.0) for i, t in enumerate(polls)]
    assert most_in_any_minute(jittered) <= POLL_LIMIT_PER_MINUTE


def test_long_work_polls_again_at_once():
    # Work longer than the interval leaves nothing to wait for: no added latency.
    polls, fake = drive(["completed"] * 5, work_s=3.0)
    assert fake.waits == []
    assert [b - a for a, b in zip(polls, polls[1:])] == [3.0] * 4


def test_idle_backs_off_from_the_floor_to_the_ceiling_never_under_the_interval():
    pacer = PollPacer(clock=lambda: 0.0)
    pacer.polled(0.0)
    waits = [pacer.after("idle") for _ in range(5)]
    assert waits == [max(w, pacer.interval_s) for w in (0.2, 0.4, 0.8, 1.0, 1.0)]
    assert IDLE_MIN_S < IDLE_MAX_S < pacer.interval_s


def test_work_resets_the_idle_backoff():
    pacer = PollPacer(clock=lambda: 100.0)
    for _ in range(4):
        pacer.after("idle")
    pacer.after("completed")
    assert pacer._idle == IDLE_MIN_S


def test_a_refusal_for_rate_backs_off_exponentially_to_a_ceiling():
    _, fake = drive(["rate_limited"] * 6)
    assert fake.waits == [2.0, 4.0, 8.0, 8.0, 8.0]
    assert (FIRST_BACKOFF_S, MAX_BACKOFF_S) == (2.0, 8.0)


def test_a_pass_after_a_refusal_resets_the_backoff():
    _, fake = drive(["rate_limited", "rate_limited", "idle", "rate_limited", "idle"])
    assert fake.waits[:2] == [FIRST_BACKOFF_S, FIRST_BACKOFF_S * 2]
    assert fake.waits[3] == FIRST_BACKOFF_S


def test_other_failures_pause_a_second_and_stay_paced():
    pacer = PollPacer(clock=lambda: 0.0)
    pacer.polled(0.0)
    for status in ("fenced", "unavailable", "refused"):
        assert pacer.after(status) == max(FAILURE_PAUSE_S, pacer.interval_s)


def test_no_session_is_no_poll_and_does_not_hold_off_the_next():
    polls, fake = drive([None, None, "completed"])
    assert fake.waits == [0.2, 0.2]
    assert len(polls) == 1


def test_stop_ends_a_wait_at_once():
    fake = FakeTime()
    calls = []

    def once():
        calls.append(fake.now)
        return "rate_limited"

    def wait(seconds):
        fake.stopped = True
        return True

    fake.wait = wait
    run_paced(fake, PollPacer(clock=fake.clock), once)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("code", "status"),
    [("rate_limited", "rate_limited"), ("transport_failed", "unavailable")],
)
def test_a_pass_tells_a_rate_refusal_apart(code, status):
    def run():
        raise FleetError(code)

    assert cli._pass(run) == status


def test_a_pass_still_fails_on_unsafe_storage():
    def run():
        raise FleetError("unsafe_storage")

    with pytest.raises(FleetError):
        cli._pass(run)
