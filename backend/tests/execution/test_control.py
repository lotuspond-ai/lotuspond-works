"""Task 11: controls serialize with dispatch, never fake an observed state, keep limits.

Time moves only in real database time; no deadline, lease or expiry column is edited.
"""

import json
from threading import Barrier, Event, Thread
from time import sleep

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution import progression
from lotuspond.execution.admission import AdmissionService
from lotuspond.execution.budgets import liability
from lotuspond.execution.contracts import AdmissionRequest, SignalRequest
from lotuspond.execution.deployments import set_deployment_status
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.inbox import InboxService
from lotuspond.execution.orchestrator import mark_runnable
from lotuspond.execution.program import ProgramError
from lotuspond.execution.projections import project_execution
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionDeploymentRevision,
    ExecutionEvent,
    ExecutionInbox,
    ExecutionOutbox,
    ExecutionUsageReservation,
    ExecutionWait,
    Membership,
)

from .activity_fixtures import activity as activity
from .activity_fixtures import attempt_at
from .control_fixtures import controlled as controlled
from .control_fixtures import own_sweeper, reserve, until
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_dispatch import poll, second_member
from .test_leases import renew
from .test_progression import flows as flows
from .test_publication import harness as harness
from .test_sweeper import execution, wait_of

UNKNOWN = dict(input_tokens=None, output_tokens=None, cost_microusd=None)
FIELDS = ("input_tokens", "output_tokens", "cost_microusd", "active_ms")


def count(h, model, execution_id, *conditions):
    with Session(h.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.execution_id == execution_id, *conditions)
        )


def codes(h, execution_id):
    with Session(h.engine) as db:
        return list(
            db.scalars(
                select(ExecutionEvent.code)
                .where(ExecutionEvent.execution_id == execution_id)
                .order_by(ExecutionEvent.sequence)
            )
        )


def attempt(h, attempt_id):
    with Session(h.engine) as db:
        return db.get(ExecutionAttempt, attempt_id)


def active(h):
    with Session(h.engine) as db:
        return db.scalar(select(ExecutionCapacity.active))


def interrupted(h, attempt_id):
    """The attempt ended `cancelled` with the interrupted record."""
    with Session(h.engine) as db:
        row = db.get(ExecutionAttempt, attempt_id)
        if row.state != "cancelled" or row.result_artifact_id is None:
            return False
        from lotuspond_sdk.activity import ArtifactHandle

        content = h.store._get_internal(
            db,
            h.scope,
            ArtifactHandle(
                id=row.result_artifact_id,
                version=row.result_artifact_version,
                sha256=row.result_sha256,
            ),
        )
        return json.loads(content)["failure"] == "interrupted"


def usage(h, **values):
    with Session(h.engine) as db:
        return dict(values, completed_at_ms=database_now_ms(db) - 1)


def test_cancel_serializes_before_new_dispatch(controlled):
    h = controlled
    execution_id, attempt_id = h.ready("activity_read")
    calls_at_cancel_commit = count(h, ExecutionActivityDelivery, execution_id)
    view = h.control(execution_id, "cancel")
    assert (view.state, view.requested_state) == ("running", "cancelled")
    # Nothing is dispatched once the cancellation committed: no delivery, no claim.
    with pytest.raises(ExecutionError, match="forbidden"):
        h.dispatch(attempt_id)
    assert poll(h) is None
    calls_after_cancel_commit = count(h, ExecutionActivityDelivery, execution_id)
    assert calls_after_cancel_commit == calls_at_cancel_commit
    # The orchestrator cancels the reserved attempt, then observes the end.
    h.run()
    with Session(h.engine) as db:
        execution_row = db.get(Execution, execution_id)
        assert (execution_row.state, execution_row.reason) == ("cancelled", "control")
        assert db.get(ExecutionAttempt, attempt_id).state == "cancelled"
        capacity = db.scalar(select(ExecutionCapacity))
        assert (capacity.active, capacity.pending) == (0, 0)
    assert codes(h, execution_id)[-1] == "cancelled"


def test_a_cancellation_waiting_on_the_execution_lock_still_precedes_dispatch(controlled):
    h = controlled
    execution_id, attempt_id = h.ready("activity_read")
    locked, release, outcome = Event(), Event(), {}

    def hold():
        with Session(h.engine) as db, db.begin():
            db.get(Execution, execution_id, with_for_update=True)
            locked.set()
            release.wait(10)

    def cancel():
        outcome["view"] = h.control(execution_id, "cancel")

    holder = Thread(target=hold)
    holder.start()
    locked.wait(10)
    canceller = Thread(target=cancel)
    canceller.start()
    canceller.join(0.5)
    assert canceller.is_alive()  # waiting on the execution row
    release.set()
    holder.join(10)
    canceller.join(10)
    assert outcome["view"].requested_state == "cancelled"
    with pytest.raises(ExecutionError, match="forbidden"):
        h.dispatch(attempt_id)


def test_requested_state_is_not_observed_terminal(controlled):
    h = controlled
    execution_id, attempt_id = h.ready("activity_unhurried")
    lease = h.claim(h.dispatch(attempt_id))  # a runtime is working on it
    view = h.control(execution_id, "cancel")
    assert (view.state, view.requested_state) == ("running", "cancelled")
    h.run()
    with Session(h.engine) as db, db.begin():
        projected = project_execution(db, h.actor, execution_id)
    # The claimed attempt may still run: cancelled is requested, not observed.
    assert (projected.state, projected.requested_state) == ("running", "cancelled")
    # Cancellation reaches the runtime cooperatively: its renewal is refused.
    with pytest.raises(ExecutionError, match="forbidden"):
        renew(h, lease)
    # Task 10's runtime then stops its provider and reports `cancelled` under the
    # still-live lease, with what it observed; the end is observed once contained.
    h.result(attempt_id, lease, code="cancelled")
    h.run()
    with Session(h.engine) as db, db.begin():
        projected = project_execution(db, h.actor, execution_id)
    assert (projected.state, projected.requested_state) == ("cancelled", "cancelled")
    assert not interrupted(h, attempt_id)  # a cancellation is final: nothing resumes


def test_pause_resume_cannot_reset_deadline_or_budget(controlled):
    h = controlled
    execution_id = h.start("waits", None, "waits.policy")
    h.run()
    original_deadline_ms = wait_of(h, execution_id, "pause").deadline_ms
    with Session(h.engine) as db:
        execution_deadline_ms = db.get(Execution, execution_id).deadline_ms
        retained = db.scalar(select(ExecutionCapacity.retained_waits))
    reservations = count(h, ExecutionUsageReservation, execution_id)
    view = h.control(execution_id, "pause")
    assert (view.state, view.requested_state) == ("waiting", "paused")
    h.run()
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "paused"
    # Repeats: an equal one changes nothing; another action under that ID conflicts;
    # a second pause under a new ID is recorded and changes nothing either.
    wakeups = count(h, ExecutionOutbox, execution_id)
    assert h.control(execution_id, "pause").state == "paused"
    with pytest.raises(ExecutionError, match="conflict"):
        h.control(execution_id, "resume", request_id=f"pause-{execution_id}")
    h.control(execution_id, "pause", request_id="pause-again")
    assert count(h, ExecutionOutbox, execution_id) == wakeups
    # Resume needs current grants and current authority.
    original = h.budgets.entitlement

    class Lost:
        def current(self, scope, now_ms):
            raise RuntimeError("PRIVATE_GRANT_LOST")

    h.budgets.entitlement = Lost()
    with pytest.raises(ExecutionError, match="entitlement_unavailable"):
        h.control(execution_id, "resume")
    h.budgets.entitlement = original
    with Session(h.engine) as db, db.begin():
        db.scalar(
            select(Membership).where(Membership.user_id == h.actor.user_id).with_for_update()
        ).role = "viewer"
    with pytest.raises(ExecutionError, match="forbidden"):
        h.control(execution_id, "resume")
    with Session(h.engine) as db, db.begin():
        db.scalar(
            select(Membership).where(Membership.user_id == h.actor.user_id).with_for_update()
        ).role = "owner"
    assert h.control(execution_id, "resume").requested_state == "running"
    h.run()
    resumed_wait = wait_of(h, execution_id, "pause")
    with Session(h.engine) as db:
        execution_row = db.get(Execution, execution_id)
        assert execution_row.state == "waiting"  # back to its retained wait
        assert execution_row.deadline_ms == execution_deadline_ms
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == retained
    assert resumed_wait.deadline_ms == original_deadline_ms
    assert count(h, ExecutionUsageReservation, execution_id) == reservations
    assert codes(h, execution_id).count("paused") == codes(h, execution_id).count("resumed") == 1
    # An equal repeat of the recorded resume is answered even once grants are lost.
    h.budgets.entitlement = Lost()
    assert h.control(execution_id, "resume").requested_state == "running"
    h.budgets.entitlement = original
    # Cancellation is final.
    h.control(execution_id, "cancel")
    with pytest.raises(ExecutionError, match="conflict"):
        h.control(execution_id, "resume", request_id="after-cancel")
    h.run()
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "cancelled"
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 0


def test_resume_needs_the_remaining_deadline(controlled):
    h = controlled
    execution_id = h.start("waits_deadline", None, "waits.policy")
    h.run()
    h.control(execution_id, "pause")
    h.run()
    until(h, execution(h, execution_id).deadline_ms)
    with pytest.raises(ExecutionError, match="conflict"):
        h.control(execution_id, "resume")
    # The deadline still ends it while paused.
    h.sweep()
    h.run()
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "deadline_exceeded")


def test_resume_needs_budget_left_and_a_deployment_that_may_run(controlled):
    h = controlled
    # One attempt ran and cost this deployment's whole 10 microdollar budget; its
    # failure is retryable, so the execution goes on, with nothing left to spend.
    spent, attempt_id = h.ready("activity_budget")
    lease = h.claim(h.dispatch(attempt_id))
    h.result(
        attempt_id,
        lease,
        code="unavailable",
        usage=usage(h, input_tokens=1, output_tokens=1, cost_microusd=10),
    )
    h.control(spent, "pause")
    h.run()
    assert execution(h, spent).state == "paused"
    with pytest.raises(ExecutionError, match="budget_exceeded"):
        h.control(spent, "resume")
    assert execution(h, spent).requested_state == "paused"
    # A disabled deployment revision cannot be resumed into either.
    other = h.start("waits", None, "waits.policy", key="disabled")
    h.run()
    h.control(other, "pause")
    h.run()
    with Session(h.engine) as db, db.begin():
        revision = db.get(ExecutionDeploymentRevision, execution(h, other).deployment_revision_id)
        set_deployment_status(
            db,
            h.actor,
            revision.deployment_id,
            revision.id,
            "disabled",
            "disable-for-resume",
            dependencies=h.dependencies,
        )
    with pytest.raises(ExecutionError) as refused:
        h.control(other, "resume")
    assert refused.value.code in ("deployment_unavailable", "forbidden", "conflict")
    assert execution(h, other).requested_state == "paused"


def test_controls_follow_roles_and_ownership(controlled):
    h = controlled
    _, editor = second_member(h, role="editor")
    viewer_id, viewer = second_member(h, role="editor")
    with Session(h.engine) as db, db.begin():
        db.scalar(
            select(Membership).where(Membership.user_id == viewer_id).with_for_update()
        ).role = "viewer"  # reauthorization sees the current role
    owners = h.start("waits", None, "waits.policy", key="owners")
    service = AdmissionService(
        budgets=h.budgets,
        artifacts=h.store,
        artifact_policy=h.policy,
        interpreter_id="lotuspond.execution-interpreter.v1",
    )
    with Session(h.engine) as db, db.begin():
        own = service.admit_execution(
            db,
            editor,
            AdmissionRequest(deployment_revision_id=h.deploy("waits", "waits.policy"), input=None),
            "editors",
        ).id
    for actor in (editor, viewer):
        with pytest.raises(ExecutionError, match="forbidden"):
            h.control(owners, "pause", actor=actor)
    assert h.control(own, "pause", actor=editor).requested_state == "paused"  # their own
    assert h.control(own, "resume", actor=h.actor).requested_state == "running"  # any


def test_a_paused_execution_that_never_started_keeps_and_returns_its_slot(controlled):
    h = controlled
    execution_id = h.start("waits", None, "waits.policy")  # admitted: pending, holds a slot
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "pending"
        assert db.scalar(select(ExecutionCapacity.pending)) == 1
    h.control(execution_id, "pause")
    h.run()
    with Session(h.engine) as db:
        # Pending keeps its admission slot, so it stays pending while paused.
        assert db.get(Execution, execution_id).state == "pending"
    h.control(execution_id, "cancel")
    h.run()
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "cancelled"
        assert db.scalar(select(ExecutionCapacity.pending)) == 0  # the slot came back


def test_pause_releases_unstarted_work_and_resume_spends_no_attempt(controlled):
    h = controlled
    execution_id, reserved = h.ready("activity_read", active_ms=1000, capacity=2)
    other, dispatched = h.ready("activity_read", capacity=2)
    delivery = h.dispatch(dispatched)
    assert active(h) == 2
    h.control(execution_id, "pause")
    h.control(other, "pause")
    h.run()
    # Neither had started: both are released, their compute slots freed at once, and
    # their reservations charge nothing, so pausing never spends the budget.
    assert interrupted(h, reserved) and interrupted(h, dispatched)
    assert active(h) == 0
    with Session(h.engine) as db:
        rows = list(
            db.scalars(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.attempt_id.in_([reserved, dispatched])
                )
            )
        )
        now = database_now_ms(db)
        assert [row.state for row in rows] == ["released", "released"]
        assert {liability(row, f, now) for row in rows for f in FIELDS} == {0}
    with Session(h.engine) as db:
        envelope = json.loads(delivery.envelope)
        assert db.get(ExecutionActivityDelivery, envelope["delivery_id"]).status == "conflict"
    with pytest.raises(ExecutionError):
        h.claim(delivery)  # the released delivery can never be claimed
    # A pause longer than the reserved active time retires nothing: none is held.
    sleep(1.2)
    assert h.sweep().retired_reservations == 0
    # Pause and resume three times: the step allows three attempts and spends none.
    for cycle in range(3):
        h.control(execution_id, "resume", request_id=f"resume-{cycle}")
        h.run()
        fresh = attempt_at(h, execution_id, "read", ordinal=cycle + 2)
        assert attempt(h, fresh).state == "reserved"
        if cycle < 2:
            h.control(execution_id, "pause", request_id=f"pause-{cycle}")
            h.run()
            assert interrupted(h, fresh)
    # Ordinal 4 runs; its retryable failure is the step's first spent attempt.
    reserve(h, fresh)
    h.result(fresh, h.claim(h.dispatch(fresh)), code="unavailable")
    h.run()
    assert attempt(h, attempt_at(h, execution_id, "read", ordinal=5)).state == "reserved"


def test_pause_stops_claimed_work_and_resume_runs_it_anew(controlled):
    h = controlled
    execution_id, attempt_id = h.ready("activity_unhurried")
    lease = h.claim(h.dispatch(attempt_id))
    h.control(execution_id, "pause")
    h.run()
    # Observed paused; the claimed attempt is asked to stop through its renewal.
    assert (execution(h, execution_id).state, attempt(h, attempt_id).state) == (
        "paused",
        "claimed",
    )
    with pytest.raises(ExecutionError, match="forbidden"):
        renew(h, lease)
    # Task 10's runtime stops the provider and reports `cancelled` with its usage:
    # interrupted, so the step starts over on resume without spending an attempt.
    h.result(attempt_id, lease, code="cancelled")
    assert interrupted(h, attempt_id)
    assert codes(h, execution_id)[-1] == "cancelled"
    h.control(execution_id, "resume")
    h.run()
    assert attempt(h, attempt_at(h, execution_id, "read", ordinal=2)).state == "reserved"


def test_a_pause_stopped_attempt_with_unknown_usage_reconciles_then_resumes(controlled):
    h = controlled
    execution_id, attempt_id = h.ready("activity_unhurried")
    lease = h.claim(h.dispatch(attempt_id))
    h.control(execution_id, "pause")
    h.run()
    # Stopped mid-call: the outcome is known, what it cost is not.
    h.result(attempt_id, lease, code="cancelled", usage=usage(h, **UNKNOWN))
    row = execution(h, execution_id)
    assert (row.state, attempt(h, attempt_id).state) == ("reconciliation_required", "uncertain")
    with Session(h.engine) as db:
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            )
        )
        assert reservation.state == "uncertain"  # liability kept, never zero
    # The runtime's report confirmed the stop: an owner reconciles at once, and the
    # attempt stays interrupted rather than failing its step.
    with Session(h.engine) as db, db.begin():
        view = h.controls.reconcile_execution(db, h.actor, execution_id)
    assert view.state == "paused" and interrupted(h, attempt_id)
    h.control(execution_id, "resume")
    h.run()
    assert attempt(h, attempt_at(h, execution_id, "read", ordinal=2)).state == "reserved"


def test_signals_are_accepted_while_paused(controlled):
    h = controlled
    execution_id = h.start("waits_fast", None, "waits.policy")
    h.run()
    until(h, wait_of(h, execution_id, "pause").deadline_ms)
    h.sweep()
    h.run()
    callback = wait_of(h, execution_id, "callback")
    h.control(execution_id, "pause")
    h.run()
    assert execution(h, execution_id).state == "paused"
    inbox = InboxService(budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy)
    with Session(h.engine) as db, db.begin():
        inbox.accept_signal(
            db, h.actor, execution_id, SignalRequest(signal_id="s", wait_id=callback.id, payload=7)
        )
    # The wait's deadline kept running through the pause, and it expires.
    until(h, callback.deadline_ms)
    assert h.sweep().expired_waits == 1
    h.control(execution_id, "resume")
    h.run()
    # Accepted in time, so it still counts: the step has its value; the form is next.
    with Session(h.engine) as db:
        assert db.get(ExecutionWait, callback.id).state == "satisfied"
    assert wait_of(h, execution_id, "review").state == "open"
    # A cancellation refuses signals.
    h.control(execution_id, "cancel")
    review = wait_of(h, execution_id, "review")
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        inbox.accept_signal(
            db,
            h.actor,
            execution_id,
            SignalRequest(signal_id="f", wait_id=review.id, payload={"approved": True}),
        )


def test_a_pause_and_a_claim_serialize(controlled):
    h = controlled
    execution_id, attempt_id = h.ready("activity_read")
    delivery = h.dispatch(attempt_id)
    start, outcome = Barrier(2), {}

    def claim():
        start.wait(10)
        try:
            outcome["lease"] = h.claim(delivery)
        except ExecutionError as error:
            outcome["refused"] = error.code

    def pause():
        start.wait(10)
        outcome["view"] = h.control(execution_id, "pause")

    threads = [Thread(target=claim), Thread(target=pause)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    h.run()
    assert outcome["view"].requested_state == "paused"
    if "lease" in outcome:
        # The claim committed first: the attempt runs until its renewal is refused.
        assert attempt(h, attempt_id).state == "claimed"
        with pytest.raises(ExecutionError, match="forbidden"):
            renew(h, outcome["lease"])
    else:
        # The pause committed first: no claim, and the delivery was released.
        assert outcome["refused"] == "forbidden"
        assert interrupted(h, attempt_id)


def test_an_unverifiable_program_is_fenced_and_controls_still_apply(controlled, monkeypatch):
    h = controlled
    fenced = h.start("waits_fast", None, "waits.policy", key="fenced")
    cancelled = h.start("waits", None, "waits.policy", key="cancelled")
    paused = h.start("waits", None, "waits.policy", key="paused")
    h.run()

    def unverifiable(db, scope, execution_row):
        raise ProgramError("integrity")  # say, a later compiler's different plan bytes

    monkeypatch.setattr(progression, "load_program", unverifiable)
    h.control(cancelled, "cancel")
    h.control(paused, "pause")
    with Session(h.engine) as db, db.begin():
        mark_runnable(db, db.get(Execution, fenced, with_for_update=True), database_now_ms(db))
    h.run()
    # Retrying could never help: fenced for reconciliation, with a fixed reason.
    row = execution(h, fenced)
    assert (row.state, row.reason, row.runnable_at_ms) == (
        "reconciliation_required",
        "uncertain",
        None,
    )
    assert codes(h, fenced)[-1] == "reconciliation_required"
    # Cancellation and pause need only the execution row, never the program.
    assert execution(h, cancelled).state == "cancelled"
    assert execution(h, paused).state == "paused"
    # While fenced, its due timer is held, never re-marked or swept pass after pass.
    until(h, wait_of(h, fenced, "pause").deadline_ms)
    assert h.sweep().timers == 0
    assert (execution(h, fenced).runnable_at_ms, h.sweeper.depths.due_waits) == (None, 0)
    # Once the build verifies the program again, an owner reconciles: back to its wait,
    # whose timer then fires at the deadline it always had.
    monkeypatch.undo()
    with Session(h.engine) as db, db.begin():
        view = h.controls.reconcile_execution(db, h.actor, fenced)
    assert view.state == "waiting"
    assert h.sweep().timers == 1
    h.run()
    assert wait_of(h, fenced, "callback").state == "open"


def test_a_signal_racing_the_wait_expiry_is_counted_only_in_time(controlled):
    h = controlled
    execution_id = h.start("waits_fast", None, "waits.policy")
    h.run()
    until(h, wait_of(h, execution_id, "pause").deadline_ms)
    h.sweep()
    h.run()
    callback = wait_of(h, execution_id, "callback")
    inbox = InboxService(budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy)
    other = own_sweeper(h)
    until(h, callback.deadline_ms - 30)
    start, outcome = Barrier(2), {}

    def signal():
        start.wait(10)
        try:
            with Session(h.engine) as db, db.begin():
                outcome["receipt"] = inbox.accept_signal(
                    db,
                    h.actor,
                    execution_id,
                    SignalRequest(signal_id="race", wait_id=callback.id, payload=7),
                )
        except ExecutionError as error:
            outcome["refused"] = error.code

    def sweep():
        start.wait(10)
        until(h, callback.deadline_ms)
        outcome["report"] = h.sweep(sweeper=other)

    threads = [Thread(target=signal), Thread(target=sweep)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    h.sweep()
    h.run()
    with Session(h.engine) as db:
        wait = db.get(ExecutionWait, callback.id)
        if "receipt" in outcome:
            # Accepted before the deadline: it counts, even after the wait expired.
            inbox_row = db.get(ExecutionInbox, outcome["receipt"].id)
            assert inbox_row.available_at_ms < callback.deadline_ms
            assert wait.state == "satisfied"
        else:
            # Too late: refused at acceptance, and the step follows its expiry path.
            assert outcome["refused"] == "forbidden"
            assert db.get(Execution, execution_id).state == "failed"


def test_pause_restarts_stay_within_the_root_attempt_range(controlled):
    h = controlled
    execution_id, first = h.ready("activity_read")
    h.control(execution_id, "pause", request_id="pause-0")
    h.run()
    assert interrupted(h, first)
    # Each pause and resume restarts the step; root ordinals stay 1..10, as an
    # agent's children take those above, so restarts are bounded too.
    for cycle in range(1, 10):
        h.control(execution_id, "resume", request_id=f"resume-{cycle}")
        h.run()
        assert attempt(h, attempt_at(h, execution_id, "read", ordinal=cycle + 1)).state == (
            "reserved"
        )
        h.control(execution_id, "pause", request_id=f"pause-{cycle}")
        h.run()
    h.control(execution_id, "resume", request_id="resume-last")
    h.run()
    assert attempt_at(h, execution_id, "read", ordinal=11) is None
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "provider_unavailable")  # retries_exhausted


def test_only_a_pause_makes_a_stop_an_interruption(controlled):
    h = controlled
    # Paused and resumed before the orchestrator saw either: the stop still came
    # from the pause, so it is an interruption.
    quick, quick_attempt = h.ready("activity_unhurried", capacity=2)
    quick_lease = h.claim(h.dispatch(quick_attempt))
    h.control(quick, "pause")
    with pytest.raises(ExecutionError, match="forbidden"):
        renew(h, quick_lease)
    h.control(quick, "resume")
    h.result(quick_attempt, quick_lease, code="cancelled")
    assert interrupted(h, quick_attempt)
    # A renewal refused because the initiator lost `execute` is no pause: the
    # runtime's `cancelled` report is a failure its step counts, never a free restart.
    revoked, revoked_attempt = h.ready("activity_unhurried", capacity=2)
    revoked_lease = h.claim(h.dispatch(revoked_attempt))
    with Session(h.engine) as db, db.begin():
        db.scalar(
            select(Membership).where(Membership.user_id == h.actor.user_id).with_for_update()
        ).role = "viewer"
    with pytest.raises(ExecutionError, match="forbidden"):
        renew(h, revoked_lease)
    with Session(h.engine) as db, db.begin():
        db.scalar(
            select(Membership).where(Membership.user_id == h.actor.user_id).with_for_update()
        ).role = "owner"
    h.result(revoked_attempt, revoked_lease, code="cancelled")
    assert attempt(h, revoked_attempt).state == "failed" and not interrupted(h, revoked_attempt)
    h.run()
    assert attempt_at(h, revoked, "read", ordinal=2) is None
    assert attempt(h, attempt_at(h, quick, "read", ordinal=2)).state == "reserved"
    assert execution(h, revoked).state == "failed"


def test_repeated_pauses_never_spend_the_budget(controlled):
    h = controlled
    # Ten 10 s reservations would spend the 100 s active budget, were they charged.
    execution_id, first = h.ready("activity_unhurried")
    for cycle in range(9):
        h.control(execution_id, "pause", request_id=f"pause-{cycle}")
        h.run()
        h.control(execution_id, "resume", request_id=f"resume-{cycle}")
        h.run()
        reserve(h, attempt_at(h, execution_id, "read", ordinal=cycle + 2))
    with Session(h.engine) as db:
        rows = list(
            db.scalars(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.execution_id == execution_id
                )
            )
        )
        now = database_now_ms(db)
        assert sorted(row.state for row in rows) == ["released"] * 9 + ["reserved"]
        assert sum(liability(row, "active_ms", now) for row in rows) == 10000
    # The last attempt runs and succeeds: the budget was never spent by the pauses.
    last = attempt_at(h, execution_id, "read", ordinal=10)
    h.result(last, h.claim(h.dispatch(last)))
    h.run()
    assert execution(h, execution_id).state == "succeeded"


def test_a_pause_with_a_queued_child_spends_nothing_of_it(controlled):
    """An agent paused while its tool child is queued: the child never ran, so its
    reservation is released at zero (owner decision, 2026-10-26)."""
    from lotuspond_runtime.activity_protocol import tool_name

    from .test_dispatch import released, request_child, tools_ready

    h = controlled
    execution_id, parent, lease = tools_ready(h)
    arguments = h.store_value(parent, {"target": None, "input": None})
    tool = request_child(
        h, parent, "tool-1", "connector_read", tool_name("connector-fixture", "read"), arguments
    )
    h.control(execution_id, "pause")
    h.run()
    with Session(h.engine) as db:
        now = database_now_ms(db)
    usage = dict(input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=now - 1)
    h.result(parent, lease, code="cancelled", usage=usage)  # stopped by the pause
    h.run()
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, tool).state == "cancelled"
        assert released(db, tool)
        parent_reservation = db.scalar(
            select(ExecutionUsageReservation).where(ExecutionUsageReservation.attempt_id == parent)
        )
        assert parent_reservation.state == "settled"  # it ran: charged as observed
