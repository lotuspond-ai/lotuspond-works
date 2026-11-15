"""Keeps a worker's polls inside the control plane's per-instance poll allowance.

The control plane allows each runtime instance `POLL_LIMIT_PER_MINUTE` activity polls
and as many action polls (`activity-poll:<instance>`, `action-poll:<instance>`), in a
fixed window that every request counts towards, refused ones included. A worker that
overshoots is refused until that window ends, up to a minute without work, which is
longer than a delivery's claim window. So a worker never asks faster than the
allowance: consecutive polls start at least `interval_s` apart, a little more than the
allowance's average spacing, so no 60-second span can hold more polls than it allows
even with some network jitter. A refusal anyway (another process of the same instance,
a restart inside the window) backs off exponentially up to `MAX_BACKOFF_S` rather than
asking again every second.
"""

from __future__ import annotations

import time

# The control plane's allowance for one instance's activity polls, and for its action
# polls (backend `dispatch.py`, `action_dispatch.py`).
POLL_LIMIT_PER_MINUTE = 60
# The share of the allowance a worker uses, leaving room for jitter in arrival times.
POLL_SHARE = 0.95
# An idle worker polls again after IDLE_MIN_S, doubling to IDLE_MAX_S (never sooner
# than the pacing allows).
IDLE_MIN_S, IDLE_MAX_S = 0.2, 1.0
# After a refusal for rate, a worker waits FIRST_BACKOFF_S, doubling to MAX_BACKOFF_S.
FIRST_BACKOFF_S, MAX_BACKOFF_S = 2.0, 8.0
# After any other failure (fenced, unavailable, refused), a worker waits this long.
FAILURE_PAUSE_S = 1.0


class PollPacer:
    """When a worker may poll next; one per worker, kept across client rebuilds."""

    def __init__(self, per_minute=POLL_LIMIT_PER_MINUTE, share=POLL_SHARE, clock=time.monotonic):
        self.interval_s = 60.0 / (per_minute * share)
        self._clock = clock
        self._last = None
        self._idle = IDLE_MIN_S
        self._backoff = None

    def delay(self) -> float:
        """Seconds until the next poll may start (0 when it may start now)."""
        if self._last is None:
            return 0.0
        return max(0.0, self._last + self.interval_s - self._clock())

    def now(self) -> float:
        return self._clock()

    def polled(self, at: float):
        """A poll started at `at` (a `now()` reading)."""
        self._last = at

    def after(self, status: str) -> float:
        """How long to wait after a pass that ended in `status`, pacing included."""
        if status == "rate_limited":
            self._idle = IDLE_MIN_S
            self._backoff = (
                FIRST_BACKOFF_S if self._backoff is None else min(self._backoff * 2, MAX_BACKOFF_S)
            )
            return max(self._backoff, self.delay())
        self._backoff = None
        if status == "idle":
            wait = self._idle
            self._idle = min(self._idle * 2, IDLE_MAX_S)
            return max(wait, self.delay())
        self._idle = IDLE_MIN_S
        if status in ("fenced", "unavailable", "refused"):
            return max(FAILURE_PAUSE_S, self.delay())
        return self.delay()


def run_paced(stop, pacer: PollPacer, once):
    """Run `once()` (one worker pass, at most one poll) until `stop` is set, paced.

    `once()` returns the pass's status; `"rate_limited"` when the control plane refused
    it for rate. Waits use `stop.wait`, so setting `stop` ends the loop at once.
    """
    wait = 0.0
    while not stop.is_set():
        if wait > 0 and stop.wait(wait):
            return
        began = pacer.now()
        status = once()
        if status is None:  # nothing to do this pass (no session yet): no poll was made
            wait = 0.2
            continue
        pacer.polled(began)
        wait = pacer.after(status)
