"""E05 Task 12: the spike's write checks on real processes (tier 1).

Counts are measured: writes from the synthetic target's log, commands from the rows
the API admitted. Nothing is inferred from a receipt.
"""

from __future__ import annotations

import signal

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionActionIntent, RuntimeCommandAdmission

REVIEWED_INPUT = {"amount_minor": 75000, "currency": "EUR"}


def intent_of(a, execution_id):
    with Session(a.world.engine) as db:
        row = db.scalar(
            select(ExecutionActionIntent).where(ExecutionActionIntent.execution_id == execution_id)
        )
        return row.intent_sha256, bytes(row.binding_bytes), row.target_sha256, row.input_sha256


def test_a_review_survives_killed_processes_and_writes_once(reviewed):
    """Spike checks 1-7 with a review and a write: SIGKILL the orchestrator, sweeper
    and runtime during the review; fresh processes finish with exactly one write."""
    a = reviewed
    first = a.runtime("runtime-a")
    revision, record = a.deploy()
    execution_id = a.start(revision)
    task = a.task_of(execution_id)
    stored = intent_of(a, execution_id)
    digest = stored[0]
    assert task["intent_sha256"] == digest
    for name in ("orchestrator-a", "sweeper-a", "runtime-a"):
        a.processes.signal(name, signal.SIGKILL)
    for name in ("orchestrator-a", "sweeper-a", "runtime-a"):
        a.processes.stop(name)
    a.processes.start_worker("orchestrator", "orchestrator-b", wait=False)
    a.processes.start_worker("sweeper", "sweeper-b", wait=False)
    a.processes.wait_ready("orchestrator-b", "sweeper-b")
    second = a.runtime("runtime-b")  # a fresh enrollment: another identity
    # The intent's bytes and digest are unchanged by the kills.
    assert intent_of(a, execution_id) == stored
    reviewer = a.reviewer()
    a.claim(reviewer, task["id"])
    # Positive control first: the stored digest is what the reviewer saw.
    assert a.proposal(reviewer, task["id"])["intent_sha256"] == digest
    other = a.decide(reviewer, task["id"], "0" * 64, expect=(409,))
    assert other is None or other.get("error") == "conflict"
    assert a.writes(record) == [] and a.commands(execution_id) == []
    a.decide(reviewer, task["id"], digest)
    a.settled(execution_id, seconds=180)
    # Exactly one write, carrying the reviewed values.
    [write] = a.writes(record)
    assert (write["target"], write["input"]) == ({"record_id": record}, REVIEWED_INPUT)
    assert write["idempotency_key"] == a.actions(execution_id)[0]["action_id"]
    [command] = a.commands(execution_id)
    with Session(a.world.engine) as db:
        admission = db.get(RuntimeCommandAdmission, command.admission_id)
    # The fresh runtime wrote; the killed one never did.
    assert admission.instance_id == second["instance_id"] != first["instance_id"]
    [action] = a.actions(execution_id)
    assert (action["effect"], action["outcome"]) == ("observed", "verified")


def test_spike_15_concurrent_admissions_under_one_key_write_once(reviewed):
    """25 concurrent admissions under one idempotency key: one execution, one write."""
    from concurrent.futures import ThreadPoolExecutor

    a = reviewed
    a.runtime()
    revision, record = a.deploy()
    key = "spike-15-" + record

    def admit(_):
        return a.operator.start(a.customer, revision, key=key)["id"]

    with ThreadPoolExecutor(25) as pool:
        admitted = set(pool.map(admit, range(25)))
    assert len(admitted) == 1
    [execution_id] = admitted
    a.approve(execution_id)
    a.settled(execution_id, seconds=180)
    assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 1
