"""E05 Task 6: dispatch under fresh authority, through E03's signed command path."""

import json
from hashlib import sha256

import pytest
from lotuspond_sdk.executable.wire import validate_runtime_command, wire_digest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.models import (
    ExecutionActionCommand,
    ExecutionActionIntent,
    ExecutionAttempt,
    RuntimeCommandAdmission,
)

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import (
    action_commands,
    dispatch_attempt,
    intent_row,
    receipt_for,
    report,
)
from .dispatch_fixtures import actions as actions  # noqa: F401
from .test_tasks import tasks as tasks  # noqa: F401


def admissions(h):
    with Session(h.engine) as db:
        return db.scalar(select(func.count()).select_from(RuntimeCommandAdmission))


def test_command_bytes_expiry_and_recipient(actions):
    h = actions
    execution_id = h.reviewed("bytes")
    delivery = h.poll()
    assert delivery is not None
    command = validate_runtime_command(json.loads(delivery.command_bytes))
    intent = intent_row(h, execution_id)
    assert command.intent_sha256 == intent.intent_sha256
    assert command.action_id == intent.action_id
    assert command.expires_at_ms - command.issued_at_ms <= 300000
    assert (command.worker_id, command.fencing_generation) == (
        h.runtime.identity.instance_id,
        h.runtime.identity.generation,
    )
    (row,) = action_commands(h)
    assert row.command_digest == wire_digest(command) == sha256(delivery.command_bytes).hexdigest()
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, row.attempt_id).state == "dispatched"
        assert db.get(ExecutionActionIntent, intent.id).effect_state == "dispatching"
    # A second poll by the same session gets the same pending command back.
    assert h.poll().command_bytes == delivery.command_bytes
    assert dispatch_attempt(h, execution_id) == row.attempt_id


def no_command_for(h, execution_id):
    with Session(h.engine) as db:
        return {
            row.id: row
            for row in db.scalars(
                select(ExecutionActionCommand).where(
                    ExecutionActionCommand.execution_id == execution_id
                )
            )
        }


def _tamper_intent(h, db, execution_id):
    row = db.get(ExecutionActionIntent, intent_row(h, execution_id).id)
    row.binding_bytes = row.binding_bytes.replace(b'"submit"', b'"submiT"')


def _tamper_decision(h, db, execution_id):
    from lotuspond.models import ExecutionActionDecision

    row = db.scalar(
        select(ExecutionActionDecision).where(ExecutionActionDecision.execution_id == execution_id)
    )
    row.decision_bytes = row.decision_bytes.replace(b'"allow"', b'"allOw"')


def _disable_connection(h, db, execution_id):
    from lotuspond.models import Connection

    db.get(Connection, intent_row(h, execution_id).connection_id).status = "disabled"


def _demote_initiator(h, db, execution_id):
    from lotuspond.models import Membership

    db.scalar(select(Membership).where(Membership.user_id == h.actor.user_id)).role = "viewer"


def _demote_reviewer(h, db, execution_id):
    from lotuspond.models import ExecutionActionDecision, Membership

    decision = db.scalar(
        select(ExecutionActionDecision).where(ExecutionActionDecision.execution_id == execution_id)
    )
    db.scalar(
        select(Membership).where(Membership.user_id == decision.actor_user_id)
    ).role = "viewer"


def _disable_revision(h, db, execution_id):
    from lotuspond.models import Execution, ExecutionDeploymentRevision

    revision = db.get(
        ExecutionDeploymentRevision, db.get(Execution, execution_id).deployment_revision_id
    )
    revision.status = "disabled"


def _pause(h, db, execution_id):
    from lotuspond.models import Execution

    db.get(Execution, execution_id).requested_state = "paused"


def _drop_pool_connection(h, db, execution_id):
    from lotuspond.models import RuntimePoolPolicyConnection

    for row in db.scalars(select(RuntimePoolPolicyConnection)):
        db.delete(row)


CHANGES = {
    "intent_bytes": _tamper_intent,
    "decision_bytes": _tamper_decision,
    "connection_status": _disable_connection,
    "initiator_execute": _demote_initiator,
    "reviewer_role": _demote_reviewer,
    "deployment_revision": _disable_revision,
    "requested_state": _pause,
    "pool_policy_connection": _drop_pool_connection,
}


@pytest.mark.parametrize("change", sorted(CHANGES) + ["epoch", "entitlement"])
def test_approval_alone_is_insufficient(actions, change, monkeypatch):
    h = actions
    control = h.reviewed("control-" + change)
    delivery = h.poll()
    assert delivery is not None and no_command_for(h, control)  # positive control first
    report(h, receipt_for(h, control, h.claim(delivery)))  # done: its slot is free again
    execution_id = h.reviewed("changed-" + change)
    if change == "epoch":
        h.fleet_recovery.epoch = 2  # a restore advanced the recovery epoch
    elif change == "entitlement":

        class Lost:
            def current(self, *args):
                raise RuntimeError("PRIVATE_PROVIDER_OUTAGE")

        monkeypatch.setattr(h.budgets, "entitlement", Lost())
    else:
        with Session(h.engine) as db, db.begin():
            CHANGES[change](h, db, execution_id)
    # Each candidate is refused under the locks and skipped; the poll returns nothing.
    assert h.poll() is None
    admitted_commands_after_change = no_command_for(h, execution_id)
    assert admitted_commands_after_change == {}


def test_generic_command_claim_is_refused(actions):
    from lotuspond_runtime.errors import FleetError

    h = actions
    h.reviewed("generic")
    delivery = h.poll()
    command = json.loads(delivery.command_bytes)
    with Session(h.engine) as db, db.begin(), pytest.raises(FleetError) as error:
        h.commands.claim(
            db, h.runtime, command["command_id"], sha256(delivery.command_bytes).hexdigest()
        )
    generic_claim_error = error.value
    assert generic_claim_error.code == "conflict"
    assert h.claim(delivery).command_id == command["command_id"]  # the action path claims


def test_each_authority_check_runs_after_locks(actions):
    """A reviewer demoted while dispatch waits for the execution row is refused."""
    from threading import Event, Thread

    from lotuspond.models import Execution, ExecutionActionDecision, Membership

    h = actions
    execution_id = h.reviewed("after-locks")
    locked, released = Event(), Event()

    def holder():
        with Session(h.engine) as db, db.begin():
            db.scalar(select(Execution).where(Execution.id == execution_id).with_for_update())
            locked.set()
            released.wait(10)
            decision = db.scalar(
                select(ExecutionActionDecision).where(
                    ExecutionActionDecision.execution_id == execution_id
                )
            )
            db.scalar(
                select(Membership).where(Membership.user_id == decision.actor_user_id)
            ).role = "viewer"

    outcome = {}

    def poller():
        locked.wait(10)
        outcome["delivery"] = h.poll()

    threads = [Thread(target=holder), Thread(target=poller)]
    for thread in threads:
        thread.start()
    released.set()
    for thread in threads:
        thread.join(30)
    assert outcome == {"delivery": None}
    assert no_command_for(h, execution_id) == {}
