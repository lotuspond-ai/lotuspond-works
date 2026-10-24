"""Task 11: the recovery fence after a restore, and owner reconciliation of an execution."""

import json
from hashlib import sha256

import pytest
from lotuspond_runtime.errors import FleetError
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.sweeper import queue_depths
from lotuspond.fleet.authority import RecoverySnapshot, database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionReconciliationObservation,
    ExecutionUsageReservation,
)

from .activity_fixtures import activity as activity
from .control_fixtures import controlled as controlled
from .control_fixtures import until
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_dispatch import second_member
from .test_leases import renew
from .test_progression import flows as flows
from .test_publication import harness as harness
from .test_sweeper import lease_of


class Restored:
    """The budget services' recovery authority, at the epoch a restore moved it to."""

    test_only = True

    def __init__(self, epoch, active=True):
        self.epoch, self.active = epoch, active

    def current(self, installation_id):
        if not self.active:
            raise RuntimeError("PRIVATE_RESTORE_IN_PROGRESS")
        return RecoverySnapshot(installation_id, self.epoch, True)


def test_restore_cannot_restore_epoch_or_grant(controlled):
    h = controlled
    # Work in flight when the backup was taken: one claimed, one only delivered.
    _, claimed = h.ready("activity_read", capacity=3)
    lease = h.claim(h.dispatch(claimed))
    _, delivered = h.ready("activity_read", capacity=3)
    delivery = h.dispatch(delivered)
    _, reserved = h.ready("activity_read", capacity=3)
    # During the restore the authority answers nothing: no orchestration, sweeping
    # or dispatch, whatever the restored rows say.
    h.fleet_recovery.available = False
    h.budgets.recovery = Restored(1, active=False)
    with Session(h.engine) as db:
        with pytest.raises(ExecutionError) as refused:
            h.fence.current(db)
    restored_old_epoch_error = refused.value
    assert restored_old_epoch_error.code == "authority_unavailable"
    with pytest.raises(ExecutionError, match="authority_unavailable"):
        h.run()
    with pytest.raises(ExecutionError, match="authority_unavailable"):
        h.sweep()
    with pytest.raises((ExecutionError, FleetError)) as refused:
        h.dispatch(reserved)
    assert refused.value.code in ("authority_unavailable", "recovery_unavailable")
    # The authority returns at a new epoch. The rows still carry the old one.
    h.fleet_recovery.available, h.fleet_recovery.epoch = True, 2
    h.budgets.recovery = Restored(2)
    with Session(h.engine) as db:
        assert h.fence.current(db) == 2
        assert {d.recovery_epoch for d in db.scalars(select(ExecutionActivityDelivery))} == {1}
    # Even once expired, an old epoch's unclaimed delivery is not "never started".
    delivery_id = json.loads(delivery.envelope)["delivery_id"]
    with Session(h.engine) as db:
        until(h, db.get(ExecutionActivityDelivery, delivery_id).expires_at_ms)
    with Session(h.engine) as db, db.begin():
        assert h.leases.expire_delivery(db, delivery_id, epoch=2) is False
    report = h.sweep()
    # In flight before the restore: uncertain, never "never started", never retried.
    assert report.restored_attempts == 2 and report.expired_deliveries == 0
    with Session(h.engine) as db:
        for attempt_id in (claimed, delivered):
            assert db.get(ExecutionAttempt, attempt_id).state == "uncertain"
            reservation = db.scalar(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.attempt_id == attempt_id
                )
            )
            assert reservation.state == "uncertain"  # liability kept, never zero
            execution = db.get(Execution, db.get(ExecutionAttempt, attempt_id).execution_id)
            assert execution.state == "reconciliation_required"
        epochs = {
            e.recovery_epoch
            for e in db.scalars(
                select(ExecutionEvent).where(ExecutionEvent.code == "attempt_uncertain")
            )
        }
        assert epochs == {2}  # new events carry the authority's epoch, never a row's
    # The old epoch's delivery and lease grant nothing now.
    for refused_call in (lambda: h.claim(delivery), lambda: renew(h, lease)):
        with pytest.raises((ExecutionError, FleetError)) as refused:
            refused_call()
        assert refused.value.code in ("unauthenticated", "authority_unavailable", "conflict")
    # A runtime identified under the old epoch is refused before anything is sent.
    with pytest.raises(FleetError, match="authority_unavailable"):
        h.dispatch(reserved)
    # Nor can restored rows restore a grant: the entitlement was issued for epoch 1,
    # the authority is at 2, so new work is refused until the grant is reissued.
    with pytest.raises(ExecutionError, match="entitlement_unavailable"):
        h.start("scoped", [1, 2, 3], key="after-restore")
    assert h.sweep().restored_attempts == 0  # swept once


def confirm_stopped(h, attempt_id):
    """A separately authorized confirmation that the attempt's compute stopped."""
    with Session(h.engine) as db, db.begin():
        h.budgets._cursor(db)
        attempt = db.get(ExecutionAttempt, attempt_id)
        observation = ExecutionReconciliationObservation(
            workspace_id=attempt.workspace_id,
            project_id=attempt.project_id,
            environment_id=attempt.environment_id,
            execution_id=attempt.execution_id,
            attempt_id=attempt_id,
            source_id="operator",
            observation_id="instance-revoked",
            digest=sha256(b"instance-revoked:" + attempt_id.encode()).hexdigest(),
            code="compute_stopped",
            observed_at_ms=database_now_ms(db),
        )
        db.add(observation)
        db.flush()
        h.budgets.confirm_stopped(db, attempt_id, observation.id)


def test_reconciliation_requires_admin_and_stopped_compute(controlled):
    h = controlled
    execution_id, attempt_id = h.ready("activity_read", active_ms=1500)
    h.claim(h.dispatch(attempt_id))
    until(h, lease_of(h, attempt_id).expires_at_ms)  # the runtime went silent
    assert h.sweep().lapsed_leases == 1

    def reconcile(actor):
        with Session(h.engine) as db, db.begin():
            return h.controls.reconcile_execution(db, actor, execution_id)

    _, editor = second_member(h, role="editor")
    with pytest.raises(ExecutionError, match="forbidden"):
        reconcile(editor)  # only an owner or admin reconciles
    with pytest.raises(ExecutionError, match="conflict"):
        reconcile(h.actor)  # compute may still run: no confirmed stop yet
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "reconciliation_required"
    confirm_stopped(h, attempt_id)
    view = reconcile(h.actor)
    assert view.state == "running"
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "failed"
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            )
        )
        assert reservation.state == "uncertain"  # unverifiable usage is never zero
    # Progression decides: `uncertain` is not this step's retry class, so it fails.
    h.run()
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        assert execution.state == "failed" and execution.result_artifact_id is None
    assert json.loads(json.dumps(view.model_dump(mode="json")))["state"] == "running"


def test_a_restored_delivery_its_attempt_disowns_is_closed_once(controlled):
    h = controlled
    _, attempt_id = h.ready("activity_read")
    delivery_id = json.loads(h.dispatch(attempt_id).envelope)["delivery_id"]
    # A restore left rows disagreeing: the delivery pending, its attempt not in flight.
    with Session(h.engine) as db, db.begin():
        db.get(ExecutionAttempt, attempt_id, with_for_update=True).state = "cancelled"
    h.fleet_recovery.epoch = 2
    h.budgets.recovery = Restored(2)
    assert h.sweep().restored_attempts == 0
    with Session(h.engine) as db:
        # Never claimable under the new epoch, and never selected by a pass again.
        assert db.get(ExecutionActivityDelivery, delivery_id).status == "conflict"
        assert db.get(ExecutionAttempt, attempt_id).state == "cancelled"
    with Session(h.engine) as db, db.begin():
        assert queue_depths(db, 2).restored_attempts == 0
