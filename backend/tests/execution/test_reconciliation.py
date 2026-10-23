"""Task 9: historical evidence needs separate owner/admin authority and current recovery."""

import json
from hashlib import sha256

import pytest
from lotuspond_runtime.activity_protocol import HistoricalObservation
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionEvent,
    ExecutionLease,
    ExecutionReconciliationObservation,
    ExecutionUsageReservation,
    Membership,
)

from .activity_fixtures import activity as activity
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_leases import until
from .test_progression import flows as flows
from .test_publication import harness as harness


def lapsed(h):
    execution_id, attempt_id = h.ready("activity_read")
    delivery = h.dispatch(attempt_id)
    lease = h.claim(delivery)
    until(h, lease.expires_at_ms)
    with Session(h.engine) as db, db.begin():
        h.leases.lapse_lease(db, db.scalar(select(ExecutionLease.id)))
    return execution_id, attempt_id, sha256(delivery.envelope.encode()).hexdigest()


def reconcile(h, observation, actor=None):
    with Session(h.engine) as db, db.begin():
        h.reconciliation.reconcile_activity(db, actor or h.actor, observation)


def test_historical_evidence_requires_separate_authority(activity, monkeypatch):
    h = activity
    execution_id, attempt_id, digest = lapsed(h)
    with Session(h.engine) as db, db.begin():
        claimed_result = h.store._put_internal(db, h.scope, b"null", h.policy)
        now = database_now_ms(db)
    evidence = dict(
        observation_id="journal-1",
        attempt_id=attempt_id,
        delivery_sha256=digest,
        result=claimed_result.model_dump(),
        code="succeeded",
        usage=dict(input_tokens=7, output_tokens=8, cost_microusd=9, completed_at_ms=now),
    )
    observation = HistoricalObservation(**evidence)
    # A runtime identity is not an execution actor.
    with pytest.raises(ExecutionError, match="forbidden"):
        reconcile(h, observation, actor=h.principal_runtime)
    # An editor, who may execute, cannot reconcile: owner or admin only.
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Membership).with_for_update()).role = "editor"
    with pytest.raises(ExecutionError, match="forbidden"):
        reconcile(h, observation)
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Membership).with_for_update()).role = "owner"
    # Without current recovery authority nothing is closed.
    original = h.budgets.recovery.current

    def unavailable(installation_id):
        raise RuntimeError("PRIVATE_RECOVERY_OUTAGE")

    monkeypatch.setattr(h.budgets.recovery, "current", unavailable)
    with pytest.raises(ExecutionError, match="recovery_unavailable"):
        reconcile(h, observation)
    monkeypatch.setattr(h.budgets.recovery, "current", original)
    # Evidence for another delivery is refused.
    with pytest.raises(ExecutionError, match="conflict"):
        reconcile(h, HistoricalObservation(**{**evidence, "delivery_sha256": "0" * 64}))
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "uncertain"
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExecutionReconciliationObservation)
                .where(ExecutionReconciliationObservation.source_id == "historical")
            )
            == 0
        )
    reconcile(h, observation)
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, attempt_id)
        execution = db.get(Execution, execution_id)
        reservation = db.scalar(select(ExecutionUsageReservation))
        failure = json.loads(
            h.store._get_internal(
                db,
                h.scope,
                {
                    "id": attempt.result_artifact_id,
                    "version": attempt.result_artifact_version,
                    "sha256": attempt.result_sha256,
                },
            )
        )
        # Claimed success is evidence only: it never becomes a successful step.
        assert attempt.state == "failed" and failure["failure"] == "uncertain"
        assert attempt.result_artifact_id != claimed_result.id
        # Known usage settles and the compute slot is returned.
        assert (reservation.state, reservation.observed_cost_microusd) == ("settled", 9)
        assert reservation.compute_stopped_at_ms is not None
        assert db.scalar(select(ExecutionCapacity.active)) == 0
        assert db.scalar(select(ExecutionLease.status)) == "uncertain"
        assert (execution.state, execution.reason) == ("running", None)
        assert execution.runnable_at_ms is not None
        assert "reconciled" in set(
            db.scalars(
                select(ExecutionEvent.code).where(ExecutionEvent.execution_id == execution_id)
            )
        )
    # Immutable: an equal observation changes nothing, a changed one conflicts.
    reconcile(h, observation)
    with pytest.raises(ExecutionError, match="conflict"):
        reconcile(
            h, HistoricalObservation(**{**evidence, "code": "provider_failed", "result": None})
        )
    # Progression decides what the failed attempt means: no retry for `uncertain`.
    h.drive()
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "failed"
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExecutionAttempt)
                .where(ExecutionAttempt.execution_id == execution_id)
            )
            == 1
        )


def test_unknown_historical_usage_stays_reserved(activity):
    h = activity
    _, attempt_id, digest = lapsed(h)
    with Session(h.engine) as db:
        now = database_now_ms(db)
    reconcile(
        h,
        HistoricalObservation(
            observation_id="journal-unknown",
            attempt_id=attempt_id,
            delivery_sha256=digest,
            result=None,
            code="provider_failed",
            usage=dict(
                input_tokens=None, output_tokens=None, cost_microusd=None, completed_at_ms=now
            ),
        ),
    )
    with Session(h.engine) as db:
        reservation = db.scalar(select(ExecutionUsageReservation))
        # Never measured zero: the reservation keeps its uncertain reserved liability.
        assert reservation.state == "uncertain"
        assert reservation.observed_cost_microusd is None
        assert reservation.cost_microusd == 10
        assert db.get(ExecutionAttempt, attempt_id).state == "failed"


def test_reconciling_a_root_closes_its_unstarted_children(activity):
    from lotuspond_runtime.activity_protocol import ChildActivityRequest, tool_name

    from .activity_fixtures import FLOW

    h = activity
    model = json.loads((FLOW / "agent_tools.model.json").read_text())
    execution_id, parent = h.ready("agent_tools", model=model, path="think", active_ms=2000)
    delivery = h.dispatch(parent)
    lease = h.claim(delivery)
    arguments = h.store_value(parent, {"target": None, "input": None})
    with Session(h.engine) as db, db.begin():
        h.activities.lock_cursor(db)
        child = h.activities.request_child_activity(
            db,
            h.principal_runtime,
            ChildActivityRequest(
                request_id="tool-1",
                parent_attempt_id=parent,
                kind="connector_read",
                declaration_name=tool_name("connector-fixture", "read"),
                arguments=arguments.model_dump(),
            ),
        )
    until(h, lease.expires_at_ms)
    with Session(h.engine) as db, db.begin():
        h.leases.lapse_lease(
            db,
            db.scalar(select(ExecutionLease.id).where(ExecutionLease.attempt_id == parent)),
        )
        now = database_now_ms(db)
    reconcile(
        h,
        HistoricalObservation(
            observation_id="journal-root",
            attempt_id=parent,
            delivery_sha256=sha256(delivery.envelope.encode()).hexdigest(),
            result=None,
            code="provider_failed",
            usage=dict(input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=now),
        ),
    )
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, child).state == "cancelled"
        assert db.scalar(
            select(ExecutionUsageReservation.compute_stopped_at_ms).where(
                ExecutionUsageReservation.attempt_id == child
            )
        )
        assert db.scalar(select(ExecutionCapacity.active)) == 0
        assert db.get(Execution, execution_id).runnable_at_ms is not None
