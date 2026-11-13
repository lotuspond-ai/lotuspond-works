"""E05 Task 12: ambiguous effects on real processes (tier 1).

Spike checks 16-22 and ACT-8 to ACT-11. Writes come from the target's log, commands
from the rows the API admitted; a receipt is never taken as an effect.
"""

from __future__ import annotations

import signal

from sqlalchemy import update
from sqlalchemy.orm import Session

from lotuspond.models import RuntimeCommandAdmission

from .conftest import wait_until


def only_action(a, execution_id):
    [action] = a.actions(execution_id)
    return action


def test_spike_16_17_a_lost_response_is_read_back_not_resent(reviewed):
    a = reviewed
    a.runtime()
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.fault("lose_response")
    a.approve(execution_id)
    a.settled(execution_id, seconds=180)
    action = only_action(a, execution_id)
    assert (action["effect"], action["outcome"]) == ("observed", "verified")
    [write] = a.writes(record)
    assert write["idempotency_key"] == action["action_id"]
    assert len(a.commands(execution_id)) == 1, a.explain(execution_id)  # never resent


def test_spike_18_19_a_lost_request_is_resent_under_the_same_key(reviewed):
    """`target_key`: a qualified absence allows one resend under the same key."""
    a = reviewed
    a.runtime()
    revision, record = a.deploy(max_attempts=2)  # a resend needs an attempt left
    execution_id = a.start(revision)
    a.fault("lose_request")
    a.approve(execution_id)
    a.settled(execution_id, seconds=300)
    [write] = a.writes(record)
    assert write["idempotency_key"] == only_action(a, execution_id)["action_id"]
    # The lost one, then the resend: two commands, one write.
    assert len(a.commands(execution_id)) == 2, a.explain(execution_id)


def test_spike_20_22_a_runtime_killed_after_apply_is_read_back(reviewed):
    """`crash_after_apply`: the target applies and the runtime dies before any answer.
    Its lease lapses; another runtime's read-back finds the effect; nothing is resent;
    the restarted runtime's journal report is historical only."""
    a = reviewed
    first = a.runtime("runtime-a")
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.fault("hang_after_apply")
    a.approve(execution_id)
    wait_until(lambda: a.writes(record), 60)  # applied, the answer held back
    a.processes.signal("runtime-a", signal.SIGKILL)
    a.processes.stop("runtime-a")
    a.runtime("runtime-b")
    a.processes.start_runtime_process("runtime-a")  # its journal holds the write
    wait_until(lambda: only_action(a, execution_id)["effect"] == "observed", 240)
    # The lapsed write keeps its slot until its stop is confirmed. A restarted runtime
    # reports only after its previous session lease (not within this case), so an owner
    # confirms the stop, as the runbook says; verification then runs.
    a.resolve(
        a.customer, execution_id, only_action(a, execution_id)["action_id"], "compute_stopped"
    )
    a.settled(execution_id, seconds=240)
    assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 1
    [command] = a.commands(execution_id)
    with Session(a.world.engine) as db:
        assert (
            db.get(RuntimeCommandAdmission, command.admission_id).instance_id
            == (first["instance_id"])
        )


def test_act_8_read_backs_that_stay_unknown_need_an_owner(reviewed):
    """Five read-backs, then `reconciliation_required`; `close_unknown` fails it
    without any write beyond the one in doubt."""
    a = reviewed
    a.runtime()
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.reads_answer(record, {"present": None})  # every read undecidable
    a.fault("lose_response")
    a.approve(execution_id)
    wait_until(lambda: a.execution(execution_id)["state"] == "reconciliation_required", 300)
    action = only_action(a, execution_id)
    assert action["effect"] == "unknown"
    a.reads_answer(record, None)
    resolved = a.resolve(a.customer, execution_id, action["action_id"], "close_unknown")
    assert resolved["effect"] == "unknown"
    final = a.settled(execution_id, ("failed",), seconds=120)
    assert final["state"] == "failed"
    assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 1


def test_act_9_a_failed_verification_keeps_the_effect_visible(reviewed):
    a = reviewed
    a.runtime()
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.reads_answer(record, {"present": False})  # the write is accepted; reads deny it
    a.approve(execution_id)
    final = a.settled(execution_id, ("failed",), seconds=180)
    assert final.get("reason") == "outcome_not_verified"
    action = only_action(a, execution_id)
    assert (action["effect"], action["outcome"]) == ("accepted", "not_verified")
    assert action["evidence"]  # the failed verification cites its read
    assert len(a.writes(record)) == 1


def test_act_11_a_command_expiring_unclaimed_writes_nothing(reviewed):
    """`timeout_before_dispatch`, zero writes; another runtime may take the retry."""
    a = reviewed
    hand = a.hand()
    try:
        revision, record = a.deploy(max_attempts=2)
        execution_id = a.start(revision)
        a.approve(execution_id)
        delivery = wait_until(hand.action.poll, 60)
        assert delivery is not None  # admitted to this session, and never claimed
        with Session(a.world.engine) as db, db.begin():
            [command] = a.commands(execution_id)
            db.execute(
                update(RuntimeCommandAdmission)
                .where(RuntimeCommandAdmission.id == command.admission_id)
                .values(command_expires_at_ms=1)
            )
        wait_until(lambda: a.commands(execution_id)[0].status == "expired", 60)
        assert a.writes(record) == []
        a.runtime("runtime-b")  # the retry, within its bounds
        a.settled(execution_id, seconds=180)
        assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 2
    finally:
        hand.close()


def test_act_10_a_lost_request_without_a_target_key_is_never_resent(reviewed):
    """No `target_key`: a lost request is read back, and absent only once its stop is
    confirmed; then `not_applied`, never a resend, even with attempts left."""
    from .conftest import Actions

    # Its own environment: a connection allows only the writes its package makes.
    once = reviewed.environment_for("submit_once", "No target key")
    a = Actions(reviewed.world, reviewed.operator, once, reviewed.target)
    a.runtime("runtime-once")
    revision, record = a.deploy(operation="submit_once", max_attempts=3)
    execution_id = a.start(revision)
    a.fault("lose_request")
    a.approve(execution_id)
    final = a.settled(execution_id, ("failed",), seconds=240)
    assert final.get("reason") == "not_applied", a.explain(execution_id)
    assert a.writes(record) == []
    assert len(a.commands(execution_id)) == 1, a.explain(execution_id)
