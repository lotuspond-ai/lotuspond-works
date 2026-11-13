"""E05 Task 10: actions after a database restore (decision 13, restated).

E04's restore hold applies unchanged: a held execution derives, schedules and
delivers nothing until its owner releases it. At the release every unsettled
older-epoch intent is read back under the new epoch, never resent.
"""

import json

import pytest
from lotuspond_sdk.executable.actions import check_decision, validate_human_decision
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import decision_context
from lotuspond.execution.actions import load_intent
from lotuspond.execution.errors import ExecutionError
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    ExecutionActionDecision,
    ExecutionAttempt,
    ExecutionEvent,
)

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from ..test_recovery import restore
from .dispatch_fixtures import action_runtime, dispatch_attempt, intent_row
from .dispatch_fixtures import actions as actions  # noqa: F401
from .readback_fixtures import (
    Target,
    commands,
    execution,
    pending_readback,
    read,
    read_input,
    readbacks,
    report,
)
from .test_action_controls import write_at
from .test_resolution import admin
from .test_resolution import resolving as resolving  # noqa: F401
from .test_tasks import task_of
from .test_tasks import tasks as tasks  # noqa: F401
from .test_verification import pending_read, verification_package, verification_reads

UNSETTLED = ("not_dispatched", "dispatching", "accepted", "unknown")


def held_events(h, execution_id):
    with Session(h.engine) as db:
        return list(
            db.scalars(
                select(ExecutionEvent).where(
                    ExecutionEvent.execution_id == execution_id,
                    ExecutionEvent.code == "intent_held",
                )
            )
        )


def accepted_before_verification(h, key, target):
    """Approved, written and accepted; its verification read reserved, not delivered."""
    package, documents = verification_package()
    execution_id = h.reviewed(key, package, documents, active_ms=20000)
    delivery = h.poll()
    lease = h.claim(delivery)
    write_at(h, target, delivery)
    report(h, lease, "accepted")
    h.run2()
    assert intent_row(h, execution_id).effect_state == "accepted"
    return execution_id


def restored(h):
    """The authorities move to epoch 2; the runtime reconnects under it."""
    restore(h)
    h.runtime = action_runtime(h, h.pin, epoch=2)


def release(h, execution_id, actor=None):
    with Session(h.engine) as db, db.begin():
        return h.controls.reconcile_execution(db, actor or h.actor, execution_id)


def present_for(target, h, attempt_id):
    return {"present": target.present(read_input(h, attempt_id)["target"])}


def test_old_epoch_intents_are_held_and_read_back(actions):
    h = actions
    target = Target()
    accepted = accepted_before_verification(h, "held-accepted", target)
    package, documents = verification_package()  # the same package: still under review
    reviewing = h.reviewed("held-review", package, documents, decide=False)
    unsettled_old_epoch_intents = sum(
        1 for e in (accepted, reviewing) if intent_row(h, e).effect_state in UNSETTLED
    )
    restored(h)
    h.sweep()
    h.run2()
    held_intents = sum(len(held_events(h, e)) for e in (accepted, reviewing))
    assert held_intents == unsettled_old_epoch_intents == 2
    for execution_id in (accepted, reviewing):
        assert execution(h, execution_id).recovery_epoch is None  # held with it
    # Old-epoch commands and leases are refused: nothing is offered under epoch 2.
    assert h.poll() is None
    for execution_id in (accepted, reviewing):
        assert release(h, execution_id).state in ("running", "waiting")
    h.run2()
    # The accepted write is read back under the new epoch, then verified.
    attempt = pending_readback(h, accepted)
    read(h, attempt.id, present_for(target, h, attempt.id))
    h.run2()
    assert intent_row(h, accepted).effect_state == "observed"
    verify = pending_read(h, accepted)
    read(h, verify.id, {"present": True})
    h.run2()
    assert execution(h, accepted).state == "succeeded"
    assert target.writes == 1 and len(commands(h, accepted)) == 1
    # The intent still under review was never dispatched: a qualified absence
    # supersedes it, and its step fails without any re-proposal.
    attempt = pending_readback(h, reviewing)
    # Both executions target one record, which the other wrote: the target's read
    # model answers for this intent's own write (never sent), absent.
    read(h, attempt.id, {"present": False})
    h.run2()
    intent = intent_row(h, reviewing)
    assert (intent.authorization_state, intent.effect_state) == ("superseded", "absent")
    row = execution(h, reviewing)
    assert (row.state, row.reason) == ("failed", "recovery_epoch_changed")
    assert task_of(h, reviewing).state == "cancelled"
    assert commands(h, reviewing) == []


def test_no_readback_before_the_owner_releases(actions):
    h = actions
    target = Target()
    execution_id = accepted_before_verification(h, "unreleased", target)
    restored(h)
    for _ in range(3):
        h.sweep()
        h.run2()
    readback_attempts_before_release = len(readbacks(h, execution_id))
    assert readback_attempts_before_release == 0
    assert all(a.state != "reserved" for a in verification_reads(h, execution_id))
    row = execution(h, execution_id)
    assert (row.state, row.recovery_epoch) == ("reconciliation_required", None)
    assert h.poll() is None


def test_release_refused_until_an_old_epoch_write_stops(resolving):
    """A write claimed under the older epoch stays uncertain: the release is refused
    until its stop is confirmed, then the write is read back, never resent."""
    h = resolving
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("claimed-old", package, documents, active_ms=20000)
    delivery = h.poll()
    h.claim(delivery)
    write_at(h, target, delivery)  # applied; the runtime never reports before the restore
    write = dispatch_attempt(h, execution_id)
    restored(h)
    h.sweep()
    h.run2()
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, write).state == "uncertain"
    with pytest.raises(ExecutionError) as refused:
        release(h, execution_id)
    release_before_stop_error = refused.value
    assert release_before_stop_error.code == "conflict"
    owner = admin(h)
    for kind in ("effect_present", "readback"):
        with pytest.raises(ExecutionError):
            h.resolve(owner, execution_id, kind, resolution_id="early-" + kind)
    h.resolve(owner, execution_id, "compute_stopped", resolution_id="stopped")
    assert release(h, execution_id).state == "running"
    h.run2()
    attempt = pending_readback(h, execution_id)
    read(h, attempt.id, present_for(target, h, attempt.id))
    h.run2()
    assert intent_row(h, execution_id).effect_state == "observed"
    read(h, pending_read(h, execution_id).id, {"present": True})
    h.run2()
    assert execution(h, execution_id).state == "succeeded"
    assert target.writes == 1 and len(commands(h, execution_id)) == 1


def test_restored_grant_or_decision_never_authorizes(actions):
    """An approval restored from before the backup never dispatches under the new
    epoch: no command, and its reserved dispatch attempt is never reserved again."""
    h = actions
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("restored-decision", package, documents, active_ms=20000)
    attempt = dispatch_attempt(h, execution_id)
    restored(h)
    h.sweep()
    h.run2()
    release(h, execution_id)
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt).state == "uncertain"  # never reserved again
        row = db.scalar(
            select(ExecutionActionDecision).where(
                ExecutionActionDecision.execution_id == execution_id
            )
        )
        decision = validate_human_decision(json.loads(row.decision_bytes))
        verified = load_intent(
            db,
            _scope_of(h, execution_id),
            intent_row(h, execution_id).id,
            artifacts=h.store,
            installation_id="test-installation",
        )
        context = decision_context(verified.intent, database_now_ms(db)).model_copy(
            update={"recovery_epoch": 2}
        )
    restored_decision_report = check_decision(verified.intent, decision, context)
    assert restored_decision_report.code == "stale_epoch"
    h.run2()
    assert h.poll() is None and commands(h, execution_id) == []
    # The restored rows show the write reserved: it is read back, and its qualified
    # absence supersedes the intent rather than authorizing a resend.
    read_back = pending_readback(h, execution_id)
    read(h, read_back.id, present_for(target, h, read_back.id))
    h.run2()
    intent = intent_row(h, execution_id)
    assert intent.authorization_state == "superseded"
    assert (execution(h, execution_id).state, execution(h, execution_id).reason) == (
        "failed",
        "recovery_epoch_changed",
    )
    assert commands(h, execution_id) == [] and target.writes == 0


def _scope_of(h, execution_id):
    from lotuspond.execution.progression import _scope

    return _scope(execution(h, execution_id))
