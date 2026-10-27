"""Row-locked orchestration claims over product PostgreSQL (ADR-0011).

The execution row lock is the orchestration fence. A claim takes the workspace row
FOR KEY SHARE, then the execution row FOR UPDATE, both SKIP LOCKED, and never
acquires the installation cursor, environment capacity, runtime pool, connection or
usage reservation rows afterwards. A candidate whose workspace an API transaction
holds is skipped, never waited on, and the claim reads past that workspace's due
work, so one tenant cannot stall every worker.
Wake-up outbox rows are consumed in the transaction that applies them, so a
rollback leaves both the marker and the rows pending. Callers own the
transaction; nothing here commits or calls the network.
"""

from sqlalchemy import select

from ..fleet.authority import database_now_ms
from ..models import Execution, ExecutionOutbox, Workspace
from .engine_contracts import WorkerIdentity
from .errors import ExecutionError, execution_service

MAX_SAFE = 9007199254740991
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
WAKEUP_KINDS = ("engine_start", "engine_signal", "control")
# Due candidates read without locks per round. Each is tried under its own
# savepoint, so a skipped candidate's workspace lock is released before the next.
CANDIDATES = 25
# A round reads past every workspace and execution an earlier round skipped, so one
# tenant's due work filling a round cannot hide another tenant's (whole-branch
# review, 27 October 2026). Bounded: a claim reads at most ROUNDS * CANDIDATES.
ROUNDS = 4


def _held(db, execution):
    """Take or re-take the execution row FOR UPDATE without waiting.

    Callers must already hold it FOR UPDATE (re-taking our own lock never blocks).
    This cannot prove ownership: it detects another transaction's lock, failing
    fast instead of waiting out of the documented lock order.
    """
    if type(execution) is not Execution:
        raise ExecutionError("invalid_request")
    held = db.scalar(
        select(Execution.id)
        .where(
            Execution.workspace_id == execution.workspace_id,
            Execution.project_id == execution.project_id,
            Execution.environment_id == execution.environment_id,
            Execution.id == execution.id,
        )
        .with_for_update(nowait=True)
    )
    if held is None:
        raise ExecutionError("not_found")


def _instant(at_ms):
    if type(at_ms) is not int or not 0 <= at_ms <= MAX_SAFE:
        raise ExecutionError("invalid_request")
    return at_ms


@execution_service
def mark_runnable(db, execution: Execution, at_ms: int) -> None:
    """Record orchestrator work due at or after `at_ms`; the earliest request wins.

    The only setter of `executions.runnable_at_ms`. Its caller already holds the
    execution row lock (admission, signal acceptance, controls, activity results,
    expired deliveries, reconciliation, the sweeper and self-continuation). A lapsed
    lease does not call it: only reconciliation can decide what happened.
    """
    at_ms = _instant(at_ms)
    _held(db, execution)
    if execution.state in TERMINAL:
        raise ExecutionError("conflict")
    if execution.runnable_at_ms is None or at_ms < execution.runnable_at_ms:
        execution.runnable_at_ms = at_ms
    db.flush()


@execution_service
def clear_runnable(db, execution: Execution) -> None:
    """No further transition is possible without an external event or due time."""
    _held(db, execution)
    execution.runnable_at_ms = None
    db.flush()


def _worker(worker):
    worker = WorkerIdentity.model_validate(worker)
    if worker.role != "orchestrator":
        raise ExecutionError("forbidden")
    return worker


@execution_service
def claim_runnable(db, worker: WorkerIdentity) -> Execution | None:
    """Lock the first due execution this worker's interpreters implement, or None."""
    worker = _worker(worker)
    now = database_now_ms(db)
    skipped_workspaces, skipped_executions = set(), set()
    for _ in range(ROUNDS):
        query = select(Execution.id, Execution.workspace_id).where(
            Execution.runnable_at_ms.is_not(None),
            Execution.runnable_at_ms <= now,
            Execution.interpreter_id.in_(worker.interpreter_ids),
        )
        if skipped_workspaces:
            query = query.where(Execution.workspace_id.not_in(skipped_workspaces))
        if skipped_executions:
            query = query.where(Execution.id.not_in(skipped_executions))
        candidates = db.execute(
            query.order_by(Execution.runnable_at_ms, Execution.id).limit(CANDIDATES)
        ).all()
        for execution_id, workspace_id in candidates:
            if workspace_id in skipped_workspaces:
                continue
            savepoint = db.begin_nested()
            try:
                parent = db.scalar(
                    select(Workspace.id)
                    .where(Workspace.id == workspace_id)
                    .with_for_update(read=True, key_share=True, skip_locked=True)
                )
                if parent is None:
                    savepoint.rollback()
                    skipped_workspaces.add(workspace_id)
                    continue
                row = db.scalar(
                    select(Execution)
                    .where(
                        Execution.workspace_id == workspace_id,
                        Execution.id == execution_id,
                        Execution.interpreter_id.in_(worker.interpreter_ids),
                    )
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                # Recheck against wall time after the lock; a later marker or a
                # concurrent completion means another transaction already advanced it.
                if (
                    row is not None
                    and row.runnable_at_ms is not None
                    and row.runnable_at_ms <= database_now_ms(db)
                    and row.state not in TERMINAL
                ):
                    savepoint.commit()
                    return row
                savepoint.rollback()
                skipped_executions.add(execution_id)
            except BaseException:
                if savepoint.is_active:
                    savepoint.rollback()
                raise
        if len(candidates) < CANDIDATES:
            break  # nothing further is due
    return None


@execution_service
def consume_wakeups(db, execution: Execution) -> tuple[str, ...]:
    """Deliver all of this execution's pending start, signal and control rows.

    Consumed with the transition, whatever their availability time, so no pending
    wake-up is left without a marker. Rows leased by an external consumer through
    `lease_outbox`/`ack_outbox` belong to that protocol and are not taken; nothing
    in this tree leases them. Kinds are causes only; progression rereads the rows.
    """
    _held(db, execution)
    rows = list(
        db.scalars(
            select(ExecutionOutbox)
            .where(
                ExecutionOutbox.workspace_id == execution.workspace_id,
                ExecutionOutbox.project_id == execution.project_id,
                ExecutionOutbox.environment_id == execution.environment_id,
                ExecutionOutbox.execution_id == execution.id,
                ExecutionOutbox.kind.in_(WAKEUP_KINDS),
                ExecutionOutbox.status == "pending",
            )
            .order_by(ExecutionOutbox.available_at_ms, ExecutionOutbox.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for row in rows:
        if row.delivery_count >= 2147483647:
            raise ExecutionError("conflict")
        row.status = "delivered"
        row.delivery_count += 1
    db.flush()
    return tuple(row.kind for row in rows)
