"""E05 Task 12: disconnected runtimes around reviewed writes (tier 1), through the proxy.

DISCA-1 to DISCA-3. Severing the proxy cuts live connections and refuses new ones,
exactly as a lost network would.
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionActionIntent, ExecutionAttempt

from .conftest import wait_until


def write_states(a, execution_id):
    with Session(a.world.engine) as db:
        return [
            row.state
            for row in db.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.execution_id == execution_id)
                .order_by(ExecutionAttempt.ordinal)
            )
        ]


def effect(a, execution_id):
    with Session(a.world.engine) as db:
        return db.scalar(
            select(ExecutionActionIntent.effect_state).where(
                ExecutionActionIntent.execution_id == execution_id
            )
        )


def test_disca_1_a_write_cut_off_in_flight_is_read_back(reviewed):
    """The lease lapses; read-back settles it; the late receipt is historical only; one
    write."""
    a = reviewed
    runtime = a.runtime("runtime-a")
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.fault("slow")
    a.approve(execution_id)
    wait_until(lambda: a.writes(record), 60)
    a.processes.proxy.sever()
    wait_until(lambda: effect(a, execution_id) == "unknown", 180)  # the lease lapsed
    runtime["process"].wait(timeout=120)  # a session it cannot renew ends the worker
    a.processes.proxy.restore()
    # A fresh runtime reads back at once (a restarted one first waits out its previous
    # session lease); the restarted one reports from its journal, historical only.
    a.runtime("runtime-b")
    a.processes.start_runtime_process("runtime-a")  # its later report is historical only
    wait_until(lambda: a.actions(execution_id)[0]["effect"] == "observed", 240)
    # The cut-off write keeps its slot until its stop is confirmed: an owner confirms
    # it (a restarted runtime reports only after its previous session lease).
    a.resolve(a.customer, execution_id, a.actions(execution_id)[0]["action_id"], "compute_stopped")
    a.settled(execution_id, seconds=240)
    [action] = a.actions(execution_id)
    assert (action["effect"], action["outcome"]) == ("observed", "verified")
    assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 1


def test_disca_2_a_runtime_disconnected_and_revoked_never_writes_again(reviewed):
    a = reviewed
    runtime = a.runtime("runtime-a")
    revision, record = a.deploy()
    control = a.start(revision)  # positive control: it writes before the cut
    a.approve(control)
    a.settled(control)
    a.processes.proxy.sever()
    a.operator.revoke(a.customer, runtime["instance_id"])
    a.processes.proxy.restore()
    assert runtime["process"].wait(timeout=120) == 3  # refused on reconnection
    revision, later_record = a.deploy()
    later = a.start(revision)
    a.approve(later)
    time.sleep(5)
    assert a.commands(later) == [] and a.writes(later_record) == []
    assert len(a.writes(record)) == 1


def test_disca_3_reviews_wait_while_every_runtime_is_unreachable(reviewed):
    """Reviews and waits unchanged; no command claimed; recovery after reconnection."""
    a = reviewed
    a.runtime("runtime-a")
    # Unclaimed commands that expire during the outage ran nowhere: retries take them.
    revision, record = a.deploy(max_attempts=5)
    execution_id = a.start(revision)
    task = a.task_of(execution_id)
    a.processes.proxy.sever()
    try:
        person = a.reviewer()
        a.claim(person, task["id"])  # reviewers are not runtimes: the task still works
        a.decide(person, task["id"], task["intent_sha256"])
        time.sleep(20)
        assert a.task(task["id"])["state"] == "decided"
        assert all(c.claimed_at_ms is None for c in a.commands(execution_id))
        assert a.writes(record) == []
    finally:
        a.processes.proxy.restore()
    a.processes.stop("runtime-a")
    a.runtime("runtime-b")  # a fresh runtime after the outage
    a.settled(execution_id, seconds=300)
    assert len(a.writes(record)) == 1
    assert write_states(a, execution_id).count("uncertain") == 0
