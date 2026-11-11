"""E05 Task 6: action leases: claim once, bounded, renewed, and a lapse made uncertain."""

import pytest
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import ACTION_LEASE_MS
from lotuspond.models import Execution, ExecutionActionIntent, ExecutionAttempt

from ..activity_fixtures import activity as activity  # noqa: F401
from ..activity_fixtures import runtime
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..control_fixtures import until
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import intent_row
from .test_tasks import tasks as tasks  # noqa: F401


def test_claim_once_and_lease_bounds(actions):
    h = actions
    h.reviewed("lease")
    delivery = h.poll()
    lease = h.claim(delivery)
    assert lease.lease_revision == 1
    assert lease.expires_at_ms <= lease.deadline_ms
    with Session(h.engine) as db:
        from sqlalchemy import select

        from lotuspond.models import ExecutionActionCommand

        command = db.scalar(select(ExecutionActionCommand))
        assert command.lease_expires_at_ms <= command.claimed_at_ms + ACTION_LEASE_MS
    assert h.claim(delivery) == lease  # the same session gets the same live lease
    other = runtime(h)
    with pytest.raises(Exception):
        h.claim(delivery, other)  # another session is refused
    with Session(h.engine) as db, db.begin():
        renewed = h.dispatcher.renew(db, h.runtime, lease)
    assert renewed.expires_at_ms <= renewed.deadline_ms


def test_lapse_makes_effect_unknown_without_owner_fence(actions):
    h = actions
    # A short reserved active time: the lease ends with it (no column is edited).
    execution_id = h.reviewed("lapse", active_ms=4000)
    delivery = h.poll()
    lease = h.claim(delivery)
    until(h, lease.expires_at_ms)
    report = h.sweep()
    assert report.lapsed_action_leases == 1
    with Session(h.engine) as db:
        lapsed_execution = db.get(Execution, execution_id)
        assert lapsed_execution.state == "running"
        assert lapsed_execution.reason == "action_uncertain"
        assert db.get(ExecutionAttempt, lease.attempt_id).state == "uncertain"
        intent = db.get(ExecutionActionIntent, intent_row(h, execution_id).id)
        assert intent.effect_state == "unknown"
