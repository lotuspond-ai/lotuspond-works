"""Task 11: the recovery fence after a restore, and owner reconciliation of an execution."""

import json
from hashlib import sha256
from uuid import uuid4

import pytest
from lotuspond_runtime.errors import FleetError
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.activity_records import reserved_until
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
from .activity_fixtures import attempt_at, deploy, runtime
from .control_fixtures import controlled as controlled
from .control_fixtures import reserve, until
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


def test_a_reconciled_restored_attempt_is_never_released(controlled):
    """After a restore, "unclaimed" proves nothing: even once reconciliation fails the
    attempt and confirms its stop, its uncertain reservation keeps its liability."""
    from .test_leases import charged

    h = controlled
    execution_id, delivered = h.ready("activity_read")
    h.dispatch(delivered)
    h.fleet_recovery.epoch = 2
    h.budgets.recovery = Restored(2)
    assert h.sweep().restored_attempts == 1
    confirm_stopped(h, delivered)
    with Session(h.engine) as db, db.begin():
        h.controls.reconcile_execution(db, h.actor, execution_id)
    with Session(h.engine) as db, db.begin():
        assert db.get(ExecutionAttempt, delivered).state == "failed"
        h.budgets.release_unstarted(db, delivered, 2)  # never claimed, failed, stopped
    with Session(h.engine) as db:
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == delivered
            )
        )
        assert reservation.state == "uncertain" and charged(db, reservation) != {0}


def test_a_restored_parent_is_reconciled_only_after_the_hold(controlled):
    """A child dispatched before the restore may have run after the backup. Its parent
    cannot be reconciled before the restore hold (owner decision, 2026-10-28), so the
    child is never closed as never started: the fence makes it uncertain first, and
    then it must be reconciled before its parent. It stays charged."""
    from lotuspond_runtime.activity_protocol import HistoricalObservation, tool_name

    from lotuspond.execution.recovery import fence_restored

    from .test_dispatch import request_child, tools_ready
    from .test_leases import charged

    h = controlled
    execution_id, parent, _ = tools_ready(h)
    arguments = h.store_value(parent, {"target": None, "input": None})
    child = request_child(
        h, parent, "tool-1", "connector_read", tool_name("connector-fixture", "read"), arguments
    )
    with Session(h.engine) as db, db.begin():
        h.activities.poll_activity(db, h.principal_runtime, parent_attempt_id=parent)
    h.fleet_recovery.epoch = 2
    h.budgets.recovery = Restored(2)
    with Session(h.engine) as db:
        parent_delivery = db.scalar(
            select(ExecutionActivityDelivery).where(ExecutionActivityDelivery.attempt_id == parent)
        )
        digest, parent_delivery_id = parent_delivery.digest, parent_delivery.id
    with Session(h.engine) as db, db.begin():
        assert fence_restored(h.leases, db, parent_delivery_id, epoch=2)
        now = database_now_ms(db)

    def reconcile_parent():
        with Session(h.engine) as db, db.begin():
            h.reconciliation.reconcile_activity(
                db,
                h.actor,
                HistoricalObservation(
                    observation_id="journal-root",
                    attempt_id=parent,
                    delivery_sha256=digest,
                    result=None,
                    code="provider_failed",
                    usage=dict(
                        input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=now
                    ),
                ),
            )

    with pytest.raises(ExecutionError, match="conflict"):
        reconcile_parent()  # the hold comes first
    report = h.sweep()
    assert (report.restored_executions, report.restored_attempts) == (1, 1)
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, child).state == "uncertain"
    with pytest.raises(ExecutionError, match="conflict"):
        reconcile_parent()  # a child that may still run is reconciled first
    with Session(h.engine) as db:
        reservation = reservation_of(db, child)
        assert db.get(ExecutionAttempt, child).state == "uncertain"
        assert reservation.state == "uncertain" and charged(db, reservation) != {0}
        child_digest = db.scalar(
            select(ExecutionActivityDelivery.digest).where(
                ExecutionActivityDelivery.attempt_id == child
            )
        )
    # Positive control: with the child's journal evidence first, the parent closes.
    with Session(h.engine) as db, db.begin():
        h.reconciliation.reconcile_activity(
            db,
            h.actor,
            HistoricalObservation(
                observation_id="journal-child",
                attempt_id=child,
                delivery_sha256=child_digest,
                result=None,
                code="provider_failed",
                usage=dict(input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=now),
            ),
        )
    reconcile_parent()
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, child).state == "failed"
        assert db.get(ExecutionAttempt, parent).state == "failed"
        assert db.get(Execution, execution_id).recovery_epoch is None  # still held


def test_a_held_child_never_delivered_closes_with_its_parent(controlled):
    """An agent's tool child requested before the backup and never delivered: the hold
    makes it uncertain. Its parent's journal evidence closes it as cancelled, with its
    uncertain liability kept, so the owner can release the execution without
    inventing evidence for the child."""
    from lotuspond_runtime.activity_protocol import HistoricalObservation, tool_name

    from .test_dispatch import request_child, tools_ready
    from .test_leases import charged

    h = controlled
    execution_id, parent, _ = tools_ready(h)
    arguments = h.store_value(parent, {"target": None, "input": None})
    child = request_child(
        h, parent, "tool-1", "connector_read", tool_name("connector-fixture", "read"), arguments
    )
    restore(h)
    assert h.sweep().restored_executions == 1
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, parent).state == "uncertain"
        assert db.get(ExecutionAttempt, child).state == "uncertain"
        digest = db.scalar(
            select(ExecutionActivityDelivery.digest).where(
                ExecutionActivityDelivery.attempt_id == parent
            )
        )
        now = database_now_ms(db)
    with Session(h.engine) as db, db.begin():
        h.reconciliation.reconcile_activity(
            db,
            h.actor,
            HistoricalObservation(
                observation_id="journal-parent",
                attempt_id=parent,
                delivery_sha256=digest,
                result=None,
                code="provider_failed",
                usage=dict(input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=now),
            ),
        )
    with Session(h.engine) as db:
        reservation = reservation_of(db, child)
        assert db.get(ExecutionAttempt, child).state == "cancelled"
        # Its shared reservation keeps its uncertain liability; its stop is confirmed.
        assert reservation.state == "uncertain" and charged(db, reservation) != {0}
        assert reservation.compute_stopped_at_ms is not None
    with Session(h.engine) as db, db.begin():
        assert h.controls.reconcile_execution(db, h.actor, execution_id).state == "running"
    h.run()
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "failed"  # uncertain: never retried


def test_a_pause_never_releases_a_restored_root_before_the_hold(controlled):
    """A dispatched root that a pause would release before the fence: the restore hold
    comes first (owner decision, 2026-10-28), so the pause never treats "unclaimed" as
    never started. The fence then makes it uncertain, and it stays charged."""
    from .test_leases import charged

    h = controlled
    execution_id, attempt_id = h.ready("activity_read")
    h.dispatch(attempt_id)
    h.control(execution_id, "pause")
    h.fleet_recovery.epoch = 2
    h.budgets.recovery = Restored(2)
    h.run()  # the orchestrator holds it, and fences its attempt, before any sweep
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        assert (execution.state, execution.recovery_epoch) == ("reconciliation_required", None)
        delivery = db.scalar(
            select(ExecutionActivityDelivery).where(
                ExecutionActivityDelivery.attempt_id == attempt_id
            )
        )
        assert delivery.status == "conflict"  # never claimable under the new epoch
        reservation = reservation_of(db, attempt_id)
        assert db.get(ExecutionAttempt, attempt_id).state == "uncertain"  # never released
        assert reservation.compute_stopped_at_ms is None
        assert reservation.state == "uncertain" and charged(db, reservation) != {0}
    assert h.sweep().restored_attempts == 0  # nothing left for the fence
    # Nothing proves it never ran: its owner cannot release it without its stop.
    with Session(h.engine) as db, db.begin():
        with pytest.raises(ExecutionError, match="conflict"):
            h.controls.reconcile_execution(db, h.actor, execution_id)


# ------------------------------------------------------------------ the restore hold


def restore(h, epoch=2):
    """The authorities after a restore: a new epoch, and the grant reissued under it."""
    h.fleet_recovery.epoch = epoch
    h.budgets.recovery = Restored(epoch)
    issued = h.provider.current
    h.provider.current = lambda scope, now_ms: issued(scope, now_ms).model_copy(
        update={"recovery_epoch": epoch}
    )


def reservation_of(db, attempt_id):
    return db.scalar(
        select(ExecutionUsageReservation).where(ExecutionUsageReservation.attempt_id == attempt_id)
    )


def test_a_restore_holds_every_execution_until_its_owner_releases_it(controlled):
    """RESTORE-1's hold (owner decision, 2026-10-28).

    The restored rows prove nothing about what ran after the backup. An attempt they
    show reserved and never delivered becomes uncertain and is never delivered under
    the new epoch. Its owner closes one a runtime's journal shows ran, with the entry
    as evidence, and releases the other, which is then delivered once.
    """
    from lotuspond_runtime.activity_protocol import HistoricalObservation

    h = controlled
    ran, ran_attempt = h.ready("activity_read", capacity=3)
    never, never_attempt = h.ready("activity_read", capacity=3)
    restore(h)
    fresh = runtime(h, epoch=2)
    # Before the sweeper reaches them, nothing reserved before the restore is
    # delivered under the new epoch.
    with pytest.raises(ExecutionError, match="forbidden"):
        h.dispatch(never_attempt, fresh)
    assert h.sweep().restored_executions == 2
    assert h.sweep().restored_executions == 0  # held once
    with Session(h.engine) as db:
        for execution_id, attempt_id in ((ran, ran_attempt), (never, never_attempt)):
            execution = db.get(Execution, execution_id)
            assert (execution.state, execution.reason, execution.recovery_epoch) == (
                "reconciliation_required",
                "uncertain",
                None,
            )
            assert db.get(ExecutionAttempt, attempt_id).state == "uncertain"
            assert reservation_of(db, attempt_id).state == "uncertain"  # never zero
        assert queue_depths(db, 2).restored_executions == 0
    h.run()  # progression derives nothing while it is held
    with pytest.raises(ExecutionError):
        h.dispatch(never_attempt, fresh)
    # A runtime's journal shows `ran_attempt` ran after the backup. The restored rows
    # hold no delivery to check the entry against; its owner closes it all the same.
    with Session(h.engine) as db:
        now = database_now_ms(db)
    with Session(h.engine) as db, db.begin():
        h.reconciliation.reconcile_activity(
            db,
            h.actor,
            HistoricalObservation(
                observation_id="journal-ran",
                attempt_id=ran_attempt,
                delivery_sha256="0" * 64,
                result=None,
                code="provider_failed",
                usage=dict(input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=now),
            ),
        )
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, ran_attempt).state == "failed"
        assert db.get(Execution, ran).state == "reconciliation_required"  # until released
    # With every journal reconciled, the owner releases both.
    with Session(h.engine) as db, db.begin():
        for execution_id in (ran, never):
            assert h.controls.reconcile_execution(db, h.actor, execution_id).state == "running"
    with Session(h.engine) as db:
        assert db.get(Execution, never).recovery_epoch == 2
        assert db.get(ExecutionAttempt, never_attempt).state == "reserved"
        assert reservation_of(db, never_attempt).state == "reserved"
    # The attempt no journal holds is delivered once, under the new epoch.
    h.dispatch(never_attempt, fresh)
    with Session(h.engine) as db:
        assert db.scalars(
            select(ExecutionActivityDelivery.recovery_epoch).where(
                ExecutionActivityDelivery.attempt_id == never_attempt
            )
        ).all() == [2]
    h.run()
    with Session(h.engine) as db:
        assert db.get(Execution, ran).state == "failed"  # uncertain work is never retried


def test_a_held_pending_execution_keeps_its_state_and_admission_slot(controlled):
    """An execution still `pending` at the restore (its attempt waits for capacity) is
    held without leaving `pending`, so its admission slot is counted as before. It is
    never reserved while held; released, it is pending again and can be."""
    from lotuspond.execution.contracts import AdmissionRequest
    from lotuspond.models import ExecutionCapacity

    h = controlled
    revision = deploy(h, "activity_read", capacity=3)
    with Session(h.engine) as db, db.begin():
        execution_id = h.service.admit_execution(
            db,
            h.actor,
            AdmissionRequest(deployment_revision_id=revision, input=None),
            "pending-" + uuid4().hex,
        ).id
    h.drive()
    attempt_id = attempt_at(h, execution_id, "read")
    with Session(h.engine) as db:
        pending = db.scalar(select(ExecutionCapacity.pending))
    restore(h)
    with pytest.raises(ExecutionError, match="recovery_unavailable"):
        reserve(h, attempt_id)  # not yet held: still never reserved under the new epoch
    assert h.sweep().restored_executions == 1
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        assert (execution.state, execution.recovery_epoch) == ("pending", None)
        assert db.get(ExecutionAttempt, attempt_id).state == "uncertain"
        assert reservation_of(db, attempt_id) is None
        assert db.scalar(select(ExecutionCapacity.pending)) == pending
    with Session(h.engine) as db, db.begin():
        assert h.controls.reconcile_execution(db, h.actor, execution_id).state == "pending"
    reserve(h, attempt_id)  # positive control: released, it is reserved
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "running"
        assert db.scalar(select(ExecutionCapacity.pending)) == pending - 1


def test_a_restored_reservation_is_never_retired_as_undispatched(controlled):
    """Its reserved time ran out, but the restored rows cannot show a dispatch after the
    backup: it is not `timeout_before_dispatch`, and is held instead."""
    h = controlled
    _, restored_attempt = h.ready("activity_read", capacity=3, active_ms=100)
    with Session(h.engine) as db:
        until(h, reserved_until(reservation_of(db, restored_attempt)))
    restore(h)
    with Session(h.engine) as db, db.begin():
        assert h.leases.expire_reservation(db, restored_attempt, epoch=2) is False
    assert h.sweep().retired_reservations == 0
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, restored_attempt).state == "uncertain"
    # Positive control: one admitted under the new epoch retires as before.
    _, current_attempt = h.ready("activity_read", capacity=3, active_ms=100)
    with Session(h.engine) as db:
        until(h, reserved_until(reservation_of(db, current_attempt)))
    with Session(h.engine) as db, db.begin():
        assert h.leases.expire_reservation(db, current_attempt, epoch=2) is True
