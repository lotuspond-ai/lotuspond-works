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
from .dispatch_fixtures import action_runtime, dispatch_attempt, intent_row, reserve
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


def held_paths(h, execution_id):
    """The occurrence path each `intent_held` event names, in event order."""
    from lotuspond_sdk.activity import ArtifactHandle

    with Session(h.engine) as db:
        paths = []
        for row in sorted(held_events(h, execution_id), key=lambda e: e.sequence):
            content = h.store._get_internal(
                db,
                _scope_of(h, execution_id),
                ArtifactHandle(
                    id=row.event_artifact_id,
                    version=row.event_artifact_version,
                    sha256=row.digest,
                ),
            )
            paths.append(json.loads(content)["occurrence_path"])
        return paths


def test_each_intent_held_event_names_its_intent(actions):
    """The hold names each held intent by its intent step's occurrence, so an owner
    can tell which actions a restore held without reading the intents table."""
    h = actions
    accepted = accepted_before_verification(h, "held-named", Target())
    restored(h)
    h.sweep()
    h.run2()
    assert held_paths(h, accepted) == ["intent"]


def test_cancel_after_the_release_still_reads_back_an_accepted_intent(actions):
    """Decision 12 under decision 13: a held intent whose write was accepted holds an
    existing effect, so a cancellation requested after the owner's release still
    reads it back. The execution ends cancelled only once the read-back settles, and
    the write is never resent or verified."""
    h = actions
    target = Target()
    execution_id = accepted_before_verification(h, "release-then-cancel", target)
    restored(h)
    h.sweep()
    h.run2()
    release(h, execution_id)
    h.control(execution_id, "cancel")
    h.run2()
    assert execution(h, execution_id).state not in ("cancelled", "failed", "succeeded")
    attempt = pending_readback(h, execution_id)
    read(h, attempt.id, present_for(target, h, attempt.id))
    h.run2()
    assert intent_row(h, execution_id).effect_state == "observed"
    assert execution(h, execution_id).state == "cancelled"
    assert all(a.state != "reserved" for a in verification_reads(h, execution_id))
    assert target.writes == 1 and len(commands(h, execution_id)) == 1


def two_intents_package():
    """Two reviewed actions in one region; both intents exist before either review."""
    from copy import deepcopy

    from .readback_fixtures import readback_package

    package, documents = readback_package()
    intent, review, dispatch, pause, verify = package["body"]["steps"]
    intent2 = deepcopy(intent)
    intent2["id"] = "intent2"
    intent2["target"]["value"]["record_id"] = "R-200"
    review2, dispatch2, verify2 = deepcopy(review), deepcopy(dispatch), deepcopy(verify)
    review2["id"], dispatch2["id"], verify2["id"] = "review2", "dispatch2", "verify2"
    review2["intent"]["source"]["id"] = dispatch2["intent"]["source"]["id"] = "intent2"
    dispatch2["decision"]["source"]["id"] = "review2"
    verify2["receipt"]["source"]["id"] = "dispatch2"
    verify2["target"]["value"]["record_id"] = "R-200"
    package["body"]["steps"] = [
        intent,
        intent2,
        review,
        dispatch,
        review2,
        dispatch2,
        pause,
        verify,
        verify2,
    ]
    return package, documents


def test_every_held_intent_of_a_region_is_read_back_before_any_settles(actions):
    """Both held intents are read back at the release, together; the first one's
    qualified absence does not end the execution before the second is read back."""
    h = actions
    package, documents = two_intents_package()
    execution_id = h.reviewed("two-held", package, documents, decide=False)
    restored(h)
    h.sweep()
    h.run2()
    assert sorted(held_paths(h, execution_id)) == ["intent", "intent2"]
    release(h, execution_id)
    h.run2()
    pending = [a for a in readbacks(h, execution_id) if a.state == "reserved"]
    assert len(pending) == 2
    read(h, pending[0].id, {"present": False})
    h.run2()
    row = execution(h, execution_id)
    assert row.state not in ("failed", "cancelled", "succeeded")
    read(h, pending[1].id, {"present": False})
    h.run2()
    from lotuspond.models import ExecutionActionIntent

    with Session(h.engine) as db:
        states = {
            (i.authorization_state, i.effect_state)
            for i in db.scalars(
                select(ExecutionActionIntent).where(
                    ExecutionActionIntent.execution_id == execution_id
                )
            )
        }
    assert states == {("superseded", "absent")}
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "recovery_epoch_changed")
    assert commands(h, execution_id) == []


def nested_dispatch_package(pause=True):
    """The intent and its review in the root region; the dispatch and its
    verification inside a parallel branch, which names them across regions."""
    from .readback_fixtures import readback_package

    package, documents = readback_package()
    intent, review, dispatch, timer, verify = package["body"]["steps"]
    branch = {
        "id": "act",
        "body": {
            "steps": [dispatch, timer, verify] if pause else [dispatch, verify],
            "result": {"kind": "literal", "schema": {"kind": "null"}, "value": None},
            "result_schema": {"kind": "data", "schema": {"kind": "null"}},
        },
    }
    parallel = {"id": "apply", "kind": "parallel", "join": "all", "branches": [branch]}
    package["body"]["steps"] = [intent, review, parallel]
    return package, documents


def approve(h, execution_id, key, run=True):
    from lotuspond.execution.action_contracts import DecisionRequest

    from .dispatch_fixtures import task_id

    task = h.call("get", h.actor, task_id(h, execution_id))
    reviewer = h.reviewer()
    h.call("claim", reviewer, task.id)
    h.call(
        "decide",
        reviewer,
        task.id,
        DecisionRequest(
            decision_id="d-" + key,
            intent_sha256=intent_row(h, execution_id).intent_sha256,
            decision="allow",
        ),
    )
    if run:
        h.run2()


def test_a_held_intent_dispatched_from_another_region_is_read_back(actions):
    """The dispatch names an intent created in an enclosing region: its restored
    write is read back at the release like any other, never resent, and its
    qualified absence supersedes the intent."""
    h = actions
    execution_id = nested_reviewed(h, "nested-held")
    restored(h)
    h.sweep()
    h.run2()
    assert held_paths(h, execution_id) == ["intent"]
    release(h, execution_id)
    h.run2()
    assert h.poll() is None and commands(h, execution_id) == []
    read_back = pending_readback(h, execution_id)
    read(h, read_back.id, {"present": False})
    h.run2()
    intent = intent_row(h, execution_id)
    assert (intent.authorization_state, intent.effect_state) == ("superseded", "absent")
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "recovery_epoch_changed")
    assert commands(h, execution_id) == []


def nested_reviewed(h, key, pause=True):
    package, documents = nested_dispatch_package(pause)
    execution_id = h.reviewed(key, package, documents, decide=False)
    approve(h, execution_id, key)
    for _ in range(3):  # the root records the decision, then the branch region starts
        if dispatch_attempt(h, execution_id) is not None:
            break
        h.run2()
    attempt = dispatch_attempt(h, execution_id)
    assert attempt is not None
    reserve(h, attempt, 20000)
    return execution_id


def test_a_dispatch_in_a_nested_region_runs_its_reviewed_action(actions):
    """The branch reads the intent and decision handles of its enclosing region: the
    approved write is sent once, accepted and verified."""
    h = actions
    target = Target()
    execution_id = nested_reviewed(h, "nested-run", pause=False)
    delivery = h.poll()
    lease = h.claim(delivery)
    write_at(h, target, delivery)
    report(h, lease, "accepted")
    h.run2()
    read(h, pending_read(h, execution_id, "apply:act/verify").id, {"present": True})
    for _ in range(3):
        h.run2()
    assert intent_row(h, execution_id).effect_state == "observed"
    assert execution(h, execution_id).state == "succeeded"
    assert target.writes == 1 and len(commands(h, execution_id)) == 1


def test_a_cancel_finds_a_nested_read_back_of_an_intent_that_never_wrote(actions):
    """The decision is recorded and the database restored before the branch dispatched:
    the release reads the held intent back from the branch, and a cancellation then
    waits for that read-back, though no write of the branch exists to point at it."""
    h = actions
    package, documents = nested_dispatch_package(pause=False)
    execution_id = h.reviewed("nested-cancel", package, documents, decide=False)
    approve(h, execution_id, "nested-cancel", run=False)
    restored(h)
    h.sweep()
    h.run2()
    release(h, execution_id)
    h.run2()
    assert dispatch_attempt(h, execution_id) is None
    attempt = pending_readback(h, execution_id)
    h.control(execution_id, "cancel")
    h.run2()
    assert execution(h, execution_id).state not in ("cancelled", "failed", "succeeded")
    read(h, attempt.id, {"present": False})
    h.run2()
    intent = intent_row(h, execution_id)
    assert (intent.authorization_state, intent.effect_state) == ("superseded", "absent")
    assert execution(h, execution_id).state == "cancelled"
    assert commands(h, execution_id) == []


def test_a_cancel_while_an_owner_decides_waits_for_them(actions):
    """Unknown read-backs of a held intent bring an owner in; a cancellation requested
    then waits for the owner instead of asking again (a round that changes nothing)."""
    from lotuspond.execution.action_contracts import MAX_READBACKS

    from .test_tasks import ORCHESTRATOR

    h = actions
    execution_id = accepted_before_verification(h, "owner-cancel", Target())
    restored(h)
    h.sweep()
    h.run2()
    release(h, execution_id)
    h.run2()
    for _ in range(MAX_READBACKS + 1):
        found = [a for a in readbacks(h, execution_id) if a.state == "reserved"]
        if not found:
            break
        read(h, found[0].id, {"unrelated": True})
        h.run2()
    assert execution(h, execution_id).state == "reconciliation_required"
    h.control(execution_id, "cancel")
    h.run2()
    assert h.engine_v2.progress(h.sessions, ORCHESTRATOR, limit=10).claimed == 0
    row = execution(h, execution_id)
    assert (row.state, row.requested_state) == ("reconciliation_required", "cancelled")
