"""Sweeper for due execution work (ADR-0011, Task 11).

Each due item is swept in its own transaction with database time, so one item never
blocks or rolls back another, and a sweeper killed mid-batch leaves every finished
item committed. Sweeping again changes nothing. Candidates are read without locks
through the v15 due indexes; each item then takes its rows in the documented order
(an execution row `SKIP LOCKED`, as the orchestrator's claim does) and rechecks that
it is still due. The recovery epoch is fenced before every item's locks.

One pass, in this order:

1. attempts in flight under an older recovery epoch become uncertain (restore);
2. executions admitted under an older recovery epoch are held for reconciliation
   until an owner releases them (`recovery.hold_restored`, owner decision,
   2026-10-28);
3. lapsed leases go to Task 9's `lapse_lease`;
4. expired unclaimed deliveries go to Task 9's `expire_delivery`;
5. reserved attempts whose reserved active time ran out before dispatch go to
   Task 9's `expire_reservation`, except a restored execution's: its rows prove
   nothing about dispatch since the backup;
6. due waits: a timer is satisfied, an external or form wait expires, and
   progression follows the step's declared expiry path (an execution fenced for
   reconciliation keeps its waits until it resumes);
7. an overdue execution with nothing due (or due only later than any engine
   back-off) is marked due, so progression stops its work at the next boundary.

No item changes a capacity counter except through those Task 9 services, in Task
5's lock order; wait quota is returned by the orchestrator's capacity step. Each
item's transaction waits at most `ITEM_LOCK_TIMEOUT` for a row lock; a blocked
item is deferred to a later pass.
"""

import json
import logging
from typing import Annotated

from lotuspond_runtime.protocol import Scope
from lotuspond_sdk.executable.package import TimerStep
from pydantic import Field
from sqlalchemy import func, or_, select, text, union_all

from .. import authority_gate
from ..fleet.authority import database_now_ms
from ..models import (
    Execution,
    ExecutionActionCommand,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionHumanTask,
    ExecutionLease,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    ExecutionWait,
    RuntimeCommandAdmission,
    Workspace,
)
from .authorization import record, scoped
from .budgets import installation_active
from .contracts import Contract
from .engine import MAX_RETRY_MS
from .engine_contracts import SweepReport, WorkerIdentity
from .errors import ExecutionError
from .orchestrator import OUT_OF_TIME, TERMINAL, clear_runnable, mark_runnable
from .program import ProgramError, load_program, resolve_occurrence
from .recovery import fence_restored, hold_restored, restored

logger = logging.getLogger("lotuspond.execution")

OVERDUE = ("pending", "running", "waiting", "paused")
# Execution states that may hold a reserved attempt.
LIVE = (*OVERDUE, "reconciliation_required")
# A sweep item never waits long on a row another transaction holds: it is retried
# by a later pass instead, so one tenant's long transaction cannot stall a pass.
# It only ever lowers the worker's own lock timeout, which an operator may already
# have set lower (configurable downward only); 0, PostgreSQL's "none", is higher.
ITEM_LOCK_TIMEOUT = "500ms"
BOUND_LOCK_WAIT = text(
    "SELECT set_config('lock_timeout', :bound, true)"
    " WHERE current_setting('lock_timeout') = '0'"
    " OR current_setting('lock_timeout')::interval > CAST(:bound AS interval)"
)
# Queue depths are counted up to this bound; a value at the bound means "at least".
DEPTH_BOUND = 10000
Depth = Annotated[int, Field(ge=0, le=DEPTH_BOUND)] | None


class QueueDepths(Contract):
    """Due work by kind. `None` means the count is unknown, never that it is zero."""

    due_executions: Depth
    spent_reservations: Depth
    due_waits: Depth
    overdue_executions: Depth
    restored_attempts: Depth
    restored_executions: Depth
    lapsing_leases: Depth
    expiring_deliveries: Depth


def _code(error):
    return error.code if isinstance(error, ExecutionError) else "unexpected"


def _scope(row) -> Scope:
    return Scope(
        workspace_id=row.workspace_id, project_id=row.project_id, environment_id=row.environment_id
    )


def _reserved_until():
    """When a reservation's reserved active time runs out, in database milliseconds."""
    return (
        func.floor(func.extract("epoch", ExecutionUsageReservation.created_at) * 1000)
        + ExecutionUsageReservation.active_ms
    )


def _spent():
    """Reserved, undelivered attempts with unstopped reservations, in active environments.

    v15 has no state index on attempts or reservations. The scan is driven instead
    from capacity rows that count active work, then that environment's
    non-terminal executions (`execution_queue_idx`), then each one's attempts and
    reservation by `(scope, execution_id)`: bounded by the environment's live
    executions and their attempts, never by retained history.
    """

    def same(model, other):
        return (
            (model.workspace_id == other.workspace_id)
            & (model.project_id == other.project_id)
            & (model.environment_id == other.environment_id)
        )

    return (
        select(ExecutionAttempt.id)
        .select_from(ExecutionCapacity)
        .join(Execution, same(Execution, ExecutionCapacity) & Execution.state.in_(LIVE))
        .join(
            ExecutionAttempt,
            same(ExecutionAttempt, Execution) & (ExecutionAttempt.execution_id == Execution.id),
        )
        .join(
            ExecutionUsageReservation,
            same(ExecutionUsageReservation, ExecutionAttempt)
            & (ExecutionUsageReservation.execution_id == ExecutionAttempt.execution_id)
            & (ExecutionUsageReservation.attempt_id == ExecutionAttempt.id),
        )
        .where(
            ExecutionCapacity.active > 0,
            ExecutionAttempt.state == "reserved",
            ExecutionUsageReservation.state == "reserved",
            ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
            ~select(ExecutionActivityDelivery.id)
            .where(
                same(ExecutionActivityDelivery, ExecutionAttempt),
                ExecutionActivityDelivery.execution_id == ExecutionAttempt.execution_id,
                ExecutionActivityDelivery.attempt_id == ExecutionAttempt.id,
            )
            .exists(),
        )
    )


def _restored(epoch):
    """Pending deliveries and active leases signed under an older recovery epoch."""
    delivery = ExecutionActivityDelivery
    pending = select(delivery.id).where(
        delivery.status == "pending", delivery.recovery_epoch < epoch
    )
    leased = (
        select(delivery.id)
        .join(ExecutionLease, ExecutionLease.delivery_id == delivery.id)
        .where(ExecutionLease.status == "active", delivery.recovery_epoch < epoch)
    )
    return pending, leased


def _restored_executions(epoch):
    """Non-terminal executions admitted or released under an older recovery epoch.

    The predicate is `execution_restored_idx`'s, written out so the planner can use
    that partial index whatever the epoch.
    """
    return select(Execution.id).where(
        Execution.recovery_epoch < epoch,
        text("executions.state NOT IN ('succeeded','failed','cancelled')"),
    )


def _late(marker, now):
    """A marker that would wake an overdue execution later than any engine back-off.

    Progression caps its wake-ups at the deadline; only the engine's own back-off
    (at most `MAX_RETRY_MS`) may lie beyond it, and it is never cut short.
    """
    return marker > now + MAX_RETRY_MS


def _due_waits(now):
    """Open waits past their deadline, except those of an execution fenced for
    reconciliation: nothing progresses there, and they are swept once it resumes."""
    fenced = (
        select(Execution.id)
        .where(
            Execution.workspace_id == ExecutionWait.workspace_id,
            Execution.project_id == ExecutionWait.project_id,
            Execution.environment_id == ExecutionWait.environment_id,
            Execution.id == ExecutionWait.execution_id,
            Execution.state == "reconciliation_required",
        )
        .exists()
    )
    return select(ExecutionWait.id).where(
        ExecutionWait.state == "open", ExecutionWait.deadline_ms <= now, ~fenced
    )


def _overdue(now):
    """Overdue executions with nothing due, or due only later than any back-off.

    Not one held after a restore: its release makes it due, and its deadline then ends
    it (`recovery.hold_restored`).
    """
    return select(Execution.id).where(
        Execution.state.in_(OVERDUE),
        Execution.recovery_epoch.is_not(None),
        Execution.deadline_ms <= now,
        or_(Execution.runnable_at_ms.is_(None), _late(Execution.runnable_at_ms, now)),
    )


def queue_depths(db, epoch: int) -> QueueDepths:
    """Bounded counts of due work; a count that cannot be read is unknown."""
    now = database_now_ms(db)
    queries = {
        "due_executions": lambda: select(Execution.id).where(
            Execution.runnable_at_ms.is_not(None), Execution.runnable_at_ms <= now
        ),
        "spent_reservations": lambda: _spent().where(_reserved_until() <= now),
        "due_waits": lambda: _due_waits(now),
        "overdue_executions": lambda: _overdue(now),
        "restored_attempts": lambda: union_all(*_restored(epoch)),
        "restored_executions": lambda: _restored_executions(epoch),
        "lapsing_leases": lambda: select(ExecutionLease.id).where(
            ExecutionLease.status == "active", ExecutionLease.expires_at_ms <= now
        ),
        "expiring_deliveries": lambda: select(ExecutionActivityDelivery.id).where(
            ExecutionActivityDelivery.status == "pending",
            ExecutionActivityDelivery.expires_at_ms <= now,
        ),
    }
    depths = {}
    for name, query in queries.items():
        try:
            with db.begin_nested():
                depths[name] = db.scalar(
                    select(func.count()).select_from(query().limit(DEPTH_BOUND).subquery())
                )
        except Exception:
            depths[name] = None  # unknown, never reported as zero
    return QueueDepths(**depths)


def _schedulable(db) -> bool:
    """Whether any attempt waits for a reservation: a plain read, without locks."""
    now = database_now_ms(db)
    return bool(
        db.scalar(
            select(
                select(ExecutionAttempt.id)
                .join(Execution, Execution.id == ExecutionAttempt.execution_id)
                .where(
                    ExecutionAttempt.state == "reserved",
                    Execution.requested_state == "running",
                    Execution.state.in_(("pending", "running")),
                    Execution.deadline_ms > now,  # never a head (`schedule_batch`)
                    or_(Execution.reason.is_(None), Execution.reason != OUT_OF_TIME),
                    ~select(ExecutionUsageReservation.id)
                    .where(ExecutionUsageReservation.attempt_id == ExecutionAttempt.id)
                    .exists(),
                )
                .exists()
            )
        )
    )


class ExecutionSweeper:
    """Task 11's sweeper; explicitly constructed with Task 9's lease service and the fence."""

    def __init__(self, *, leases, recovery, scheduler=None):
        if leases is None or recovery is None:
            raise ValueError("Lease service and recovery fence are required")
        self.leases, self.recovery = leases, recovery
        # Task 5's scheduler, run after each pass's items in batches (Task 13).
        self.scheduler = scheduler
        self.test_only = bool(getattr(recovery, "test_only", False))
        # The last pass's queue depths, for the worker's metrics; None until read.
        self.depths = None

    def _fence(self, db):
        try:
            epoch = self.recovery.current(db)
            if type(epoch) is not int or epoch < 0:
                raise ValueError
            return epoch
        except Exception:
            raise ExecutionError("authority_unavailable") from None

    def _candidates(self, sessions, limit):
        with sessions() as db, db.begin():
            epoch, now = self._fence(db), database_now_ms(db)
            delivery = ExecutionActivityDelivery
            pending, leased = _restored(epoch)
            restored = list(
                db.scalars(pending.order_by(delivery.expires_at_ms, delivery.id).limit(limit))
            ) + list(
                db.scalars(
                    leased.order_by(ExecutionLease.expires_at_ms, ExecutionLease.id).limit(limit)
                )
            )
            return {
                "restored_attempts": restored,
                "restored_executions": list(
                    db.scalars(
                        _restored_executions(epoch)
                        .order_by(Execution.recovery_epoch, Execution.id)
                        .limit(limit)
                    )
                ),
                "lapsed_leases": list(
                    db.scalars(
                        select(ExecutionLease.id)
                        .where(
                            ExecutionLease.status == "active", ExecutionLease.expires_at_ms <= now
                        )
                        .order_by(ExecutionLease.expires_at_ms, ExecutionLease.id)
                        .limit(limit)
                    )
                ),
                "expired_deliveries": list(
                    db.scalars(
                        select(delivery.id)
                        .where(
                            delivery.status == "pending",
                            delivery.expires_at_ms <= now,
                            delivery.recovery_epoch == epoch,
                        )
                        .order_by(delivery.expires_at_ms, delivery.id)
                        .limit(limit)
                    )
                ),
                "retired_reservations": list(
                    db.scalars(
                        _spent()
                        .where(_reserved_until() <= now, Execution.recovery_epoch == epoch)
                        .order_by(ExecutionAttempt.id)
                        .limit(limit)
                    )
                ),
                "waits": list(
                    db.scalars(
                        _due_waits(now)
                        .order_by(ExecutionWait.deadline_ms, ExecutionWait.id)
                        .limit(limit)
                    )
                ),
                "deadlines": list(
                    db.scalars(
                        _overdue(now).order_by(Execution.deadline_ms, Execution.id).limit(limit)
                    )
                ),
                # E05 Task 5, on the v16 due indexes.
                "escalated_tasks": list(
                    db.scalars(
                        select(ExecutionHumanTask.id)
                        .where(
                            ExecutionHumanTask.state == "open",
                            ExecutionHumanTask.escalated_at_ms.is_(None),
                            ExecutionHumanTask.escalate_at_ms.is_not(None),
                            ExecutionHumanTask.escalate_at_ms <= now,
                        )
                        .order_by(ExecutionHumanTask.escalate_at_ms, ExecutionHumanTask.id)
                        .limit(limit)
                    )
                ),
                "lapsed_action_leases": list(
                    db.scalars(
                        select(ExecutionActionCommand.id)
                        .where(
                            ExecutionActionCommand.status == "claimed",
                            ExecutionActionCommand.lease_expires_at_ms <= now,
                        )
                        .order_by(
                            ExecutionActionCommand.lease_expires_at_ms, ExecutionActionCommand.id
                        )
                        .limit(limit)
                    )
                ),
                "expired_action_commands": list(
                    db.scalars(
                        select(ExecutionActionCommand.id)
                        .join(
                            RuntimeCommandAdmission,
                            RuntimeCommandAdmission.id == ExecutionActionCommand.admission_id,
                        )
                        .where(
                            ExecutionActionCommand.status == "admitted",
                            RuntimeCommandAdmission.command_expires_at_ms <= now,
                        )
                        .order_by(
                            RuntimeCommandAdmission.command_expires_at_ms, ExecutionActionCommand.id
                        )
                        .limit(limit)
                    )
                ),
                "released_claims": list(
                    db.scalars(
                        select(ExecutionHumanTask.id)
                        .where(
                            ExecutionHumanTask.claim_user_id.is_not(None),
                            ExecutionHumanTask.claim_expires_at_ms <= now,
                        )
                        .order_by(ExecutionHumanTask.claim_expires_at_ms, ExecutionHumanTask.id)
                        .limit(limit)
                    )
                ),
            }

    @staticmethod
    def _claim(db, scope, execution_id):
        """The execution row as the orchestrator takes it; None while another holds it.

        The fair gate first, shared and tried only: while an authority change holds or
        awaits it, the item waits for a later pass (`authority_gate`)."""
        if not authority_gate.try_shared(db, authority_gate.WORKSPACE, scope.workspace_id):
            return None
        authority_gate.taken(db, authority_gate.WORKSPACE, scope.workspace_id)
        workspace = db.scalar(
            select(Workspace.id)
            .where(Workspace.id == scope.workspace_id)
            .with_for_update(read=True, key_share=True, skip_locked=True)
        )
        if workspace is None:
            return None
        return db.scalar(
            scoped(Execution, scope)
            .where(Execution.id == execution_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )

    def _wait(self, db, wait_id, epoch):
        """Satisfy a due timer or expire a due external or form wait."""
        hint = db.scalar(select(ExecutionWait).where(ExecutionWait.id == wait_id))
        if hint is None:
            return None
        scope = _scope(hint)
        execution = self._claim(db, scope, hint.execution_id)
        if execution is None or execution.state in TERMINAL:
            return None
        wait = record(db, ExecutionWait, scope, wait_id, locked=True)
        now = database_now_ms(db)
        if wait.state != "open" or wait.deadline_ms > now:
            return None
        instance = record(db, ExecutionStepInstance, scope, wait.step_instance_id)
        try:
            program = load_program(db, scope, execution)
            step = resolve_occurrence(program, instance.occurrence_path).step
        except ProgramError:
            # Unverifiable: due, so the orchestrator fences it for reconciliation; a
            # back-off already set is never cut short.
            if execution.runnable_at_ms is None:
                mark_runnable(db, execution, now)
            return None
        timer = isinstance(step, TimerStep)
        wait.state = "satisfied" if timer else "expired"
        db.flush()
        if not timer:
            from .human_tasks import expire_task

            # A human task (E05) expires with its wait, and so does its intent's review.
            expire_task(
                db,
                execution,
                wait,
                event=lambda e, code: self.leases._event(db, e, epoch, code, None, None),
            )
        mark_runnable(db, execution, now)
        return "timers" if timer else "expired_waits"

    def _hold(self, db, execution_id, epoch):
        """Hold an execution admitted before a restore (`recovery.hold_restored`)."""
        hint = db.scalar(select(Execution).where(Execution.id == execution_id))
        if hint is None:
            return None
        execution = self._claim(db, _scope(hint), execution_id)
        if execution is None or not restored(execution, epoch):
            return None
        held = hold_restored(
            db,
            execution,
            epoch,
            lambda code, attempt_id: self.leases._event(
                db, execution, epoch, code, attempt_id, "restored"
            ),
        )
        return "restored_executions" if held else None

    def _deadline(self, db, execution_id, epoch):
        """An overdue execution with nothing due is made due; progression stops it.

        So is one marked for later than any engine back-off: a wake-up past the
        deadline, which progression never sets now that it caps them.
        """
        hint = db.scalar(select(Execution).where(Execution.id == execution_id))
        if hint is None:
            return None
        execution = self._claim(db, _scope(hint), execution_id)
        now = database_now_ms(db)
        if (
            execution is None
            or execution.state not in OVERDUE
            or execution.recovery_epoch is None
            or execution.deadline_ms > now
            or (execution.runnable_at_ms is not None and not _late(execution.runnable_at_ms, now))
        ):
            return None
        clear_runnable(db, execution)
        mark_runnable(db, execution, now)
        return "deadlines"

    def _item(self, kind, db, identifier, epoch):
        leases = self.leases
        if kind == "restored_attempts":
            changed = fence_restored(leases, db, identifier, epoch=epoch)
        elif kind == "lapsed_leases":
            changed = leases.lapse_lease(db, identifier, epoch=epoch)
        elif kind == "expired_deliveries":
            changed = leases.expire_delivery(db, identifier, epoch=epoch)
        elif kind == "retired_reservations":
            changed = leases.expire_reservation(db, identifier, epoch=epoch)
        elif kind == "restored_executions":
            return self._hold(db, identifier, epoch)
        elif kind == "waits":
            return self._wait(db, identifier, epoch)
        elif kind == "lapsed_action_leases":
            from .action_dispatch import lapse

            changed = lapse(
                db,
                identifier,
                epoch=epoch,
                event=lambda e, code, attempt_id: leases._event(db, e, epoch, code, attempt_id),
            )
        elif kind == "expired_action_commands":
            from .action_dispatch import expire_command

            changed = expire_command(db, identifier, epoch=epoch, leases=leases)
        elif kind in {"escalated_tasks", "released_claims"}:
            from .human_tasks import escalate_task, expire_claim

            hook = escalate_task if kind == "escalated_tasks" else expire_claim
            changed = hook(
                db,
                identifier,
                epoch=epoch,
                event=lambda e, code: self.leases._event(db, e, epoch, code, None, None),
            )
        else:
            return self._deadline(db, identifier, epoch)
        return kind if changed else None

    def _schedule(self, sessions, limit) -> int:
        """Reserve the next eligible attempts, under the fence, up to `limit` per pass.

        One head per transaction (`Scheduler.schedule_batch`), so a transaction holds
        one tenant's rows and takes the installation cursor last, for a few
        statements: it never waits behind another tenant's batch, and nothing waits
        long behind it. Before each, plain reads without locks check that some
        attempt waits for a reservation and that the installation is not visibly
        full, so an idle or saturated installation takes no lock at all.
        Transactions repeat until `limit` reservations, or until as many in a row as
        there are environments reserve nothing. Each one that reserves nothing moves
        the round-robin position past every environment it looked at, so by then
        every environment's head has had its turn: one environment's refused head
        never stops the pass for the others. A refusal is retried by a later pass
        and never stops the sweep.
        """
        budgets = self.scheduler.budgets
        total = misses = 0
        while total < limit:
            try:
                with sessions() as db:
                    if not _schedulable(db) or installation_active(db) >= (
                        budgets._installation(db).active_limit
                    ):
                        break
                    environments = db.scalar(select(func.count()).select_from(ExecutionCapacity))
                with sessions() as db, db.begin():
                    scheduled = self.scheduler.schedule_batch(
                        db, budgets.installation_id, 1, epoch=self._fence(db)
                    )
            except Exception as error:
                logger.warning("Execution scheduling deferred: %s", _code(error))
                break
            if scheduled:
                total, misses = total + len(scheduled), 0
                continue
            misses += 1
            if misses >= min(environments, limit):
                break
        return total

    def sweep(self, sessions, worker, *, limit: int = 100) -> SweepReport:
        worker = WorkerIdentity.model_validate(worker)
        if worker.role != "sweeper":
            raise ExecutionError("forbidden")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ExecutionError("invalid_request")
        counts = dict.fromkeys(SweepReport.model_fields, 0)
        for kind, identifiers in self._candidates(sessions, limit).items():
            for identifier in identifiers:
                try:
                    with sessions() as db, db.begin():
                        db.execute(BOUND_LOCK_WAIT, {"bound": ITEM_LOCK_TIMEOUT})
                        changed = self._item(kind, db, identifier, self._fence(db))
                except Exception as error:
                    # One item never stops the others; it is retried by a later pass.
                    logger.warning("Execution sweep item deferred: %s", _code(error))
                    continue
                if changed:
                    counts[changed] += 1
        if self.scheduler is not None:
            counts["scheduled_attempts"] = self._schedule(sessions, limit)
        self.depths = None
        try:
            with sessions() as db, db.begin():
                self.depths = queue_depths(db, self._fence(db))
            logger.info(
                "Execution queue depths: %s", json.dumps(self.depths.model_dump(), sort_keys=True)
            )
        except Exception as error:
            logger.warning("Execution queue depths unavailable: %s", _code(error))
        return SweepReport(**counts)
