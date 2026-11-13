"""E05 Task 12: controls around reviews and writes on real processes (tier 1).

Spike checks 26 (write half), 28 and 34 with a review.
"""

from __future__ import annotations

from .conftest import wait_until


def test_spike_26_a_cancelled_in_flight_write_finishes_and_records(reviewed):
    """Cancelling while a write is in flight: it finishes, its receipt is recorded, it is
    reported as a completed effect, and no later step (verification) runs."""
    a = reviewed
    a.runtime()
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.fault("slow")
    a.approve(execution_id)
    wait_until(lambda: a.writes(record), 60)  # applied; its answer is 4 s away
    a.operator.control(a.customer, execution_id, "cancel")
    final = a.settled(execution_id, ("cancelled",), seconds=120)
    assert final["state"] == "cancelled"
    [action] = a.actions(execution_id)
    assert (action["effect"], action["receipt_status"]) == ("accepted", "accepted")
    assert action["outcome"] == "unknown"  # verification never ran, nothing inferred
    assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 1


def test_spike_28_a_cancelled_review_writes_nothing(reviewed):
    a = reviewed
    a.runtime()
    revision, record = a.deploy()
    execution_id = a.start(revision)
    person = a.reviewer()
    task = a.task_of(execution_id, person=person)
    a.claim(person, task["id"])
    a.operator.control(a.customer, execution_id, "cancel")
    a.settled(execution_id, ("cancelled",), seconds=60)
    assert a.task(task["id"])["state"] == "cancelled"
    refused = a.decide(person, task["id"], task["intent_sha256"], expect=(403, 409))
    assert refused["error"] in ("forbidden", "conflict")
    assert a.commands(execution_id) == [] and a.writes(record) == []


def test_spike_34_an_unanswered_review_expires_through_the_sweeper(reviewed):
    a = reviewed
    a.runtime()
    revision, record = a.deploy(timeout_seconds=5)
    execution_id = a.start(revision)
    task = a.task_of(execution_id)
    final = a.settled(execution_id, ("failed",), seconds=120)
    assert final.get("reason") == "review_expired"
    assert a.task(task["id"])["state"] == "expired"
    assert a.commands(execution_id) == [] and a.writes(record) == []
