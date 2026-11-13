"""E05 Task 12: review cases ACT-1 to ACT-7 on real processes (tier 1).

ACT-12 (a reviewer without source access) has no real-process form yet: every
proposal value is stored under the workspace response policy, never a source pin,
so withdrawing source access cannot reach it. Task 5's unit test forces the refusal;
the evidence document records the limit.

Every reviewer acts through the public task routes with a real session. A positive
control precedes each denial assertion.
"""

from __future__ import annotations

import threading
import time

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionHumanTask, ExecutionOutbox, Membership

from .conftest import wait_until


def reason_of(a, execution_id):
    row = a.execution(execution_id)
    return row["state"], row.get("reason")


def test_act_1_only_an_eligible_independent_reviewer_decides(reviewed):
    a = reviewed
    revision, record = a.deploy()
    execution_id = a.start(revision)
    task = a.task_of(execution_id)
    digest = task["intent_sha256"]
    admin, editor, viewer = a.reviewer("admin"), a.reviewer("editor"), a.reviewer("viewer")
    # Positive control: the admin may claim (eligible, independent of the requester).
    a.claim(admin, task["id"])
    a.release(admin, task["id"])
    # The requester (the owner who started it) is refused by independence.
    a.claim(a.customer, task["id"])
    refused = a.decide(a.customer, task["id"], digest, expect=(403,))
    assert refused["error"] == "forbidden"
    a.release(a.customer, task["id"])
    # An ineligible editor and a viewer cannot even claim.
    for person in (editor, viewer):
        assert a.claim(person, task["id"], expect=(403,))["error"] == "forbidden"
        assert a.decide(person, task["id"], digest, expect=(403,))["error"] == "forbidden"
    # An API key, whatever its scopes, is refused before any lock.
    key = a.key(["execute", "execution_signal", "read", "write"])
    assert a.keyed(key, "GET", "/human-tasks").status_code == 200  # control: it reads
    for path, body in (
        ("claim", {}),
        ("decision", {"decision_id": "k", "intent_sha256": digest, "decision": "allow"}),
    ):
        response = a.keyed(key, "POST", f"/human-tasks/{task['id']}/{path}", body)
        assert (response.status_code, response.json()["error"]) == (403, "forbidden")
    a.runtime()
    a.claim(admin, task["id"])
    a.decide(admin, task["id"], digest)
    a.settled(execution_id)
    assert len(a.writes(record)) == 1


def test_act_2_one_holder_expiry_and_release(reviewed):
    a = reviewed
    revision, record = a.deploy()
    execution_id = a.start(revision)
    task = a.task_of(execution_id)
    first, second = a.reviewer(), a.reviewer()
    results = {}

    def race(person, name):
        results[name] = person.call(
            "POST", f"/human-tasks/{task['id']}/claim", {}, expect=(200, 409)
        )

    threads = [threading.Thread(target=race, args=p) for p in ((first, "a"), (second, "b"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    holders = [n for n, r in results.items() if r.get("claim") == "caller"]
    assert len(holders) == 1, results
    holder, other = (first, second) if holders == ["a"] else (second, first)
    # A decision without a live claim is refused.
    assert a.decide(other, task["id"], task["intent_sha256"], expect=(409,))["error"] == "conflict"
    # An expired claim frees the task (the sweeper expires it).
    with Session(a.world.engine) as db, db.begin():
        db.execute(
            update(ExecutionHumanTask)
            .where(ExecutionHumanTask.id == task["id"])
            .values(claim_expires_at_ms=1)
        )
    wait_until(lambda: a.task(task["id"])["claim"] == "none", 30)
    a.claim(other, task["id"])
    # An admin releases another's claim.
    admin = a.reviewer("admin")
    assert a.release(admin, task["id"])["claim"] == "none"
    assert a.decide(other, task["id"], task["intent_sha256"], expect=(409,))["error"] == "conflict"
    assert a.writes(record) == [] and a.commands(execution_id) == []


def test_act_3_escalation_at_half_the_timeout(reviewed):
    """Escalated at half the timeout: the claim released, the escalation group (owners)
    eligible, and no notification row: nothing is delivered to anyone."""
    a = reviewed
    revision, record = a.deploy(policy="escalating", escalation="owners", timeout_seconds=12)
    execution_id = a.start(revision)
    task = a.task_of(execution_id)
    holder = a.reviewer()
    a.claim(holder, task["id"])
    with Session(a.world.engine) as db:
        outbox_before = db.scalar(select(func.count()).select_from(ExecutionOutbox))
    assert task["escalated"] is False and task["escalate_at_ms"] is not None
    escalated = wait_until(lambda: (t := a.task(task["id"]))["escalated"] and t, 30)
    assert escalated["claim"] == "none"  # the claim was released
    owner = a.reviewer("owner")
    assert a.claim(owner, task["id"])["claim"] == "caller"  # the escalation group may claim
    with Session(a.world.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionOutbox)) == outbox_before
    assert a.writes(record) == [] and a.commands(execution_id) == []


def test_act_4_intent_expiring_after_allow_never_dispatches(reviewed):
    a = reviewed
    revision, record = a.deploy(expiry_seconds=4)
    execution_id = a.start(revision)
    a.approve(execution_id)  # no runtime: nothing can claim before the expiry
    a.settled(execution_id, ("failed",), seconds=120)
    assert reason_of(a, execution_id) == ("failed", "approval_expired")
    a.runtime()
    time.sleep(3)
    assert a.commands(execution_id) == [] and a.writes(record) == []


def test_act_5_denial_writes_nothing(reviewed):
    a = reviewed
    a.runtime()
    revision, record = a.deploy()
    execution_id = a.start(revision)
    person = a.reviewer()
    task = a.task_of(execution_id, person=person)
    a.claim(person, task["id"])
    a.decide(person, task["id"], task["intent_sha256"], decision="deny")
    a.settled(execution_id, ("failed",))
    assert reason_of(a, execution_id) == ("failed", "approval_denied")
    assert a.commands(execution_id) == [] and a.writes(record) == []


def test_act_6_a_demoted_reviewer_s_approval_stops_counting(reviewed):
    """Decision 7: the reviewer loses their role after `allow`, before dispatch."""
    a = reviewed
    revision, record = a.deploy()
    execution_id = a.start(revision)
    _, person = a.approve(execution_id)
    with Session(a.world.engine) as db, db.begin():
        db.execute(
            update(Membership)
            .where(
                Membership.user_id == person.user_id,
                Membership.workspace_id == a.customer.workspace,
            )
            .values(role="viewer")
        )
    a.runtime()
    a.settled(execution_id, ("failed",), seconds=120)
    assert reason_of(a, execution_id) == ("failed", "approval_revoked")
    assert a.commands(execution_id) == [] and a.writes(record) == []


def test_act_7_approval_none_dispatches_without_a_task(reviewed):
    a = reviewed
    a.runtime()
    revision, record = a.deploy(policy="unreviewed", approval="none")
    deployment = a.customer.call("GET", "/deployments")["items"]
    listed = [d for d in deployment if d["revision_id"] == revision]
    detail = a.customer.call("GET", f"/deployments/{listed[0]['id']}")
    assert "submit" in str(detail["unreviewed_operations"])
    execution_id = a.start(revision)
    a.settled(execution_id)
    assert [t for t in a.tasks() if t["execution_id"] == execution_id] == []
    assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 1
