"""E05 Task 10: pause, cancel, revocation and disconnection around actions.

Real PostgreSQL and real database time. Writes are counted at the fake target,
never from report counters; commands are counted as rows the API admitted.
"""

import json
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import DecisionRequest
from lotuspond.models import (
    ExecutionActionCommand,
    ExecutionDeploymentRevision,
    Membership,
    RuntimeSession,
)

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import dispatch_attempt, intent_row, reserve, task_id
from .readback_fixtures import (
    Target,
    commands,
    execution,
    pending_readback,
    read,
    readbacks,
    report,
)
from .test_tasks import code_of, review_package, task_of
from .test_tasks import tasks as tasks  # noqa: F401
from .test_verification import verification_package, verification_reads


def renew(h, lease):
    with Session(h.engine) as db, db.begin():
        return h.dispatcher.renew(db, h.runtime, lease)


def write_at(h, target, delivery):
    """What the runtime does after its claim: fetch the records and write once."""
    command = json.loads(delivery.command_bytes)
    with Session(h.engine) as db, db.begin():
        records = h.dispatcher.records(
            db, h.runtime, command["command_id"], sha256(delivery.command_bytes).hexdigest()
        )
    target.write(
        records.intent.binding.action_id,
        json.loads(records.actual_target),
        json.loads(records.actual_input),
    )


def full_review():
    """Intent, review, dispatch and verification, with the review left open."""
    from .test_admission import doc

    package, documents = review_package()
    package["body"]["steps"] = doc("reviewed_action")["body"]["steps"]
    return package, documents


def decide(h, execution_id, key):
    reviewer = h.reviewer()
    task = task_of(h, execution_id)
    h.call("claim", reviewer, task.id)
    return h.call(
        "decide",
        reviewer,
        task.id,
        DecisionRequest(
            decision_id="d-" + key,
            intent_sha256=intent_row(h, execution_id).intent_sha256,
            decision="allow",
        ),
    )


@pytest.mark.parametrize("answer", ["lost", "accepted"])
def test_cancel_during_an_in_flight_write_records_a_completed_effect(actions, answer):
    """Decision 12: a claimed write finishes and records; a lost answer is still read
    back after the cancellation; verification never runs; cancelled once contained."""
    h = actions
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("cancel-" + answer, package, documents, active_ms=20000)
    delivery = h.poll()
    lease = h.claim(delivery)
    write_at(h, target, delivery)  # the write is in flight at the target
    h.control(execution_id, "cancel")
    h.run2()
    assert execution(h, execution_id).state != "cancelled"  # the write may still run
    with pytest.raises(Exception) as refused:
        renew(h, lease)  # stopped cooperatively: the renewal is refused
    assert getattr(refused.value, "code", None) is not None
    report(h, lease, "accepted" if answer == "accepted" else "unknown")
    h.run2()
    if answer == "lost":
        # The uncertain write is read back, never left unknown and never resent.
        assert execution(h, execution_id).state != "cancelled"
        read(h, pending_readback(h, execution_id).id, {"present": True})
        h.run2()
    cancelled_in_flight = intent_row(h, execution_id)
    cancelled_in_flight_execution = execution(h, execution_id)
    expected = "observed" if answer == "lost" else "accepted"
    assert cancelled_in_flight.effect_state == expected
    assert cancelled_in_flight_execution.state == "cancelled"
    assert cancelled_in_flight_execution.reason == "control"
    assert cancelled_in_flight.outcome_state == "unknown"  # never verified, never inferred
    assert verification_reads(h, execution_id) == []  # no later step ran
    assert target.writes == 1 and len(commands(h, execution_id)) == 1


def test_cancel_during_review_writes_nothing(actions):
    h = actions
    package, documents = full_review()
    execution_id = h.start2(package, "cancel-review", documents)
    task = task_of(h, execution_id)
    reviewer = h.reviewer()
    h.call("claim", reviewer, task.id)
    h.control(execution_id, "cancel")
    h.run2()
    assert execution(h, execution_id).state == "cancelled"
    assert task_of(h, execution_id).state == "cancelled"
    assert intent_row(h, execution_id).authorization_state == "withdrawn"
    later = DecisionRequest(
        decision_id="d-late",
        intent_sha256=intent_row(h, execution_id).intent_sha256,
        decision="allow",
    )
    assert code_of(h.call, "decide", reviewer, task.id, later) in ("forbidden", "conflict")
    h.run2()
    assert h.poll() is None
    commands_after_review_cancel = len(commands(h, execution_id))
    assert commands_after_review_cancel == 0
    assert intent_row(h, execution_id).effect_state == "not_dispatched"


def test_pause_keeps_review_open_and_blocks_dispatch(actions):
    h = actions
    package, documents = full_review()
    execution_id = h.start2(package, "pause-review", documents)
    h.control(execution_id, "pause")
    h.run2()
    assert execution(h, execution_id).state == "paused"
    assert task_of(h, execution_id).state == "open"
    assert decide(h, execution_id, "pause").state == "decided"
    h.run2()
    assert dispatch_attempt(h, execution_id) is None  # no dispatch while paused
    assert h.poll() is None and commands(h, execution_id) == []
    h.control(execution_id, "resume")
    h.run2()
    reserve(h, dispatch_attempt(h, execution_id))
    assert h.poll() is not None
    assert len(commands(h, execution_id)) == 1


def test_a_pause_before_the_claim_redispatches_once_on_resume(actions):
    """A pause releases a dispatched, unclaimed command attempt (E04's interrupted
    record). On resume the step gets one new attempt under the same intent, and the
    released command can never be claimed."""
    h = actions
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("pause-dispatched", package, documents, active_ms=20000)
    released = h.poll()
    h.control(execution_id, "pause")
    h.run2()
    with Session(h.engine) as db:
        [command] = db.scalars(
            select(ExecutionActionCommand).where(
                ExecutionActionCommand.execution_id == execution_id
            )
        )
        assert command.status != "claimed"
    h.control(execution_id, "resume")
    h.run2()
    assert code_of(h.claim, released) in ("conflict", "forbidden", "not_found")
    attempt = dispatch_attempt(h, execution_id, last=True)
    reserve(h, attempt)
    delivery = h.poll()
    assert delivery is not None and delivery.command_bytes != released.command_bytes
    lease = h.claim(delivery)
    write_at(h, target, delivery)
    report(h, lease, "accepted")
    h.run2()
    assert intent_row(h, execution_id).effect_state == "accepted"
    assert target.writes == 1 and len(commands(h, execution_id)) == 2


def disable_deployment(h, execution_id):
    with Session(h.engine) as db, db.begin():
        revision = db.get(
            ExecutionDeploymentRevision, execution(h, execution_id).deployment_revision_id
        )
        revision.status = "disabled"


def remove_initiator(h, execution_id):
    with Session(h.engine) as db, db.begin():
        db.delete(
            db.scalar(
                select(Membership).where(
                    Membership.user_id == execution(h, execution_id).initiating_user_id,
                    Membership.workspace_id == h.scope.workspace_id,
                )
            )
        )


@pytest.mark.parametrize("revoke", [disable_deployment, remove_initiator])
def test_revocations_between_approval_and_claim(actions, revoke):
    """Approved and dispatched, then a revocation commits: the claim is refused and
    nothing is written."""
    h = actions
    execution_id = h.reviewed("revoke-" + revoke.__name__)
    delivery = h.poll()
    assert delivery is not None
    revoke(h, execution_id)
    with pytest.raises(Exception) as refused:
        h.claim(delivery)
    assert getattr(refused.value, "code", None) in ("forbidden", "conflict", "not_found")
    [command] = commands(h, execution_id)
    assert command.status != "claimed" and command.lease_token_digest is None
    assert intent_row(h, execution_id).effect_state == "dispatching"


def test_disconnected_runtime_cannot_receive_new_commands(actions):
    """A runtime whose session ended is refused before anything is sent; the command
    admitted to that session is never handed to another session."""
    h = actions
    first = h.reviewed("disconnected-1")
    delivery = h.poll()
    assert delivery is not None
    with Session(h.engine) as db, db.begin():
        db.get(RuntimeSession, h.runtime.session_id).status = "revoked"
    with pytest.raises(Exception) as refused:
        h.poll()
    assert getattr(refused.value, "code", None) in ("unauthenticated", "forbidden")
    with pytest.raises(Exception):
        h.claim(delivery)
    with Session(h.engine) as db:
        assert (
            db.scalar(
                select(ExecutionActionCommand).where(ExecutionActionCommand.execution_id == first)
            ).status
            == "admitted"
        )
    assert task_id(h, first) is not None


def test_only_read_backs_of_a_cancelling_execution_may_run(actions):
    """The containment predicate is correlated to each attempt: under a requested
    cancellation exactly the read-backs pass, never another attempt of it or of any
    other execution (the SQL the scheduler and the activity poll use)."""
    from lotuspond.execution.orchestrator import runs_clause
    from lotuspond.models import Execution, ExecutionAttempt

    h = actions
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("contained-sql", package, documents, active_ms=20000)
    delivery = h.poll()
    lease = h.claim(delivery)
    write_at(h, target, delivery)
    h.control(execution_id, "cancel")
    h.run2()
    report(h, lease, "unknown")
    h.run2()
    pending_readback(h, execution_id)  # a read-back exists, reserved
    with Session(h.engine) as db:
        passing = set(
            db.scalars(
                select(ExecutionAttempt.id)
                .join(Execution, Execution.id == ExecutionAttempt.execution_id)
                .where(
                    ExecutionAttempt.execution_id == execution_id, runs_clause(ExecutionAttempt.id)
                )
            )
        )
        everything = set(
            db.scalars(
                select(ExecutionAttempt.id).where(ExecutionAttempt.execution_id == execution_id)
            )
        )
    readback_ids = {a.id for a in readbacks(h, execution_id)}
    assert passing == readback_ids and everything - readback_ids  # the others never pass


def test_a_claimed_write_finishes_and_records_under_a_pause(actions):
    h = actions
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("pause-claimed", package, documents, active_ms=20000)
    delivery = h.poll()
    lease = h.claim(delivery)
    write_at(h, target, delivery)
    h.control(execution_id, "pause")
    h.run2()
    assert execution(h, execution_id).state == "paused"
    with pytest.raises(Exception) as refused:
        renew(h, lease)  # stopped cooperatively
    assert getattr(refused.value, "code", None) is not None
    report(h, lease, "accepted")  # it finished: the receipt is recorded while paused
    assert intent_row(h, execution_id).effect_state == "accepted"
    assert verification_reads(h, execution_id) == []  # nothing new while paused
    h.control(execution_id, "resume")
    h.run2()
    assert len(verification_reads(h, execution_id)) == 1
    assert target.writes == 1 and len(commands(h, execution_id)) == 1


def test_a_cancelled_unclaimed_command_is_never_offered_again(actions):
    h = actions
    execution_id = h.reviewed("cancel-unclaimed")
    assert h.poll() is not None  # admitted to this session, never claimed
    h.control(execution_id, "cancel")
    h.run2()
    assert h.poll() is None  # not handed out again, nor anything else of it
    [command] = commands(h, execution_id)
    assert command.status != "claimed" and command.lease_token_digest is None


def test_a_claimed_write_records_its_receipt_after_the_initiator_is_demoted(actions):
    """Task 12 (REVA-5): a report of a write already claimed records what happened,
    whatever authority changed since; fetching records to start a write does not."""
    h = actions
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("demoted-initiator", package, documents, active_ms=20000)
    delivery = h.poll()
    lease = h.claim(delivery)
    write_at(h, target, delivery)  # the write ran while the initiator could still execute
    with Session(h.engine) as db, db.begin():
        db.scalar(
            select(Membership).where(
                Membership.user_id == execution(h, execution_id).initiating_user_id,
                Membership.workspace_id == h.scope.workspace_id,
            )
        ).role = "viewer"
    command = json.loads(delivery.command_bytes)
    with pytest.raises(Exception) as refused:
        with Session(h.engine) as db, db.begin():
            h.dispatcher.records(
                db, h.runtime, command["command_id"], sha256(delivery.command_bytes).hexdigest()
            )
    assert getattr(refused.value, "code", None) == "forbidden"
    report(h, lease, "accepted")  # recorded, not refused
    assert intent_row(h, execution_id).effect_state == "accepted"
    assert target.writes == 1 and len(commands(h, execution_id)) == 1


def test_an_unclaimed_command_that_expired_is_retried_once_within_policy(actions):
    """Task 12 (ACT-11): nothing ran, so the dispatch step's retry policy may send one
    more command under the same intent; the expired one can never be claimed."""
    from sqlalchemy import update

    from lotuspond.models import RuntimeCommandAdmission

    h = actions
    target = Target()
    package, documents = verification_package()
    retry = package["body"]["steps"][2]["retry"]
    retry.update(max_attempts=2, retry_codes=["timeout_before_dispatch"])
    execution_id = h.reviewed("retried-expiry", package, documents, active_ms=20000)
    first = h.poll()
    [command] = commands(h, execution_id)
    with Session(h.engine) as db, db.begin():
        db.execute(
            update(RuntimeCommandAdmission)
            .where(RuntimeCommandAdmission.id == command.admission_id)
            .values(command_expires_at_ms=1)
        )
    h.sweep()
    h.run2()
    assert [c.status for c in commands(h, execution_id)] == ["expired"]
    reserve(h, dispatch_attempt(h, execution_id, last=True))
    second = h.poll()
    assert second is not None and second.command_bytes != first.command_bytes
    assert code_of(h.claim, first) in ("conflict", "forbidden", "not_found")
    lease = h.claim(second)
    write_at(h, target, second)
    report(h, lease, "accepted")
    assert intent_row(h, execution_id).effect_state == "accepted"
    assert target.writes == 1 and len(commands(h, execution_id)) == 2
