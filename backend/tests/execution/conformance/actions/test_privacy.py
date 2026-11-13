"""E05 Task 12: PRIV-1, proposal values stay in their encrypted artifacts (tier 1).

Unique sentinels go into the action's target and input (as execution input, so the
package holds none), the target's response, and a provider error. Each is first shown
to arrive where it is authorized (the proposal response, the target), then every other
surface is searched: projections, decoded events, every row of every table, command
deliveries and every process log.
"""

from __future__ import annotations

from uuid import uuid4

from driver import decoded_events

from ..test_privacy import every_row
from .conftest import input_package


def sentinel(kind):
    return f"PRIVATE_{kind}_{uuid4().hex}"


def test_priv_1_sentinels_appear_only_in_artifacts_and_the_proposal(reviewed):
    a = reviewed
    a.runtime()
    private_sentinels = {
        "TARGET": sentinel("TARGET"),
        "INPUT": sentinel("INPUT"),
        "PROVIDER": sentinel("PROVIDER"),
    }
    revision = a.deploy_package(input_package("e05-private"), "private")
    value = {
        "target": {"record_id": private_sentinels["TARGET"]},
        "input": {"amount_minor": 1, "currency": private_sentinels["INPUT"]},
    }
    execution_id = a.start(revision, value=value)
    person = a.reviewer()
    task = a.task_of(execution_id, person=person)
    a.claim(person, task["id"])
    proposal = a.proposal(person, task["id"])
    # Positive controls: the reviewer sees both values; the target receives them.
    assert proposal["target"] == value["target"] and proposal["input"] == value["input"]
    a.decide(person, task["id"], task["intent_sha256"])
    a.settled(execution_id, seconds=180)
    [write] = a.writes(private_sentinels["TARGET"])
    assert write["input"] == value["input"]
    # A provider error carrying private text: a second execution whose verification
    # read answers with it (an invalid result by its schema).
    second = a.start(revision, value={**value, "target": {"record_id": "R-provider"}})
    a.reads_answer("R-provider", {"present": True, "detail": private_sentinels["PROVIDER"]})
    a.approve(second)
    a.settled(second, ("failed", "succeeded", "reconciliation_required"), seconds=180)
    surfaces = {
        "projections": [
            a.execution(execution_id),
            a.actions(execution_id),
            a.tasks(),
            a.task(task["id"]),
            a.operator.events(a.customer, execution_id),
            a.execution(second),
            a.actions(second),
        ],
        "events": [decoded_events(a.world, execution_id), decoded_events(a.world, second)],
    }
    rows = every_row(a.world)
    # Encrypted artifacts hold the values as ciphertext; nothing else may hold them.
    rows.pop("execution_artifacts", None)
    decoded_records = str(surfaces) + str(rows)
    logs = a.world.root / "logs"
    for log in sorted(logs.glob("*")) if logs.exists() else ():
        decoded_records += log.read_text(errors="replace")
    assert all(sentinel not in decoded_records for sentinel in private_sentinels.values())
