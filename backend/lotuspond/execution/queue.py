"""An environment's execution queue (Control Room; docs/superpowers/specs/2026-11-16-control-room-operations-design.md).

A read model over committed rows, without locks: the environment's capacity row (active,
pending and retained waits against their limits, or none before any admission), the count
of executions in each state, and how long the oldest pending admission has waited.
"""

from __future__ import annotations

from sqlalchemy import func, select

from ..models import Execution, ExecutionCapacity
from .authorization import reauthorize, scoped
from .errors import execution_service

STATES = (
    "pending",
    "running",
    "waiting",
    "paused",
    "reconciliation_required",
    "succeeded",
    "failed",
    "cancelled",
)


@execution_service
def queue_view(db, actor) -> dict:
    actor = reauthorize(db, actor, "read")
    scope = actor.scope
    capacity = db.scalar(scoped(ExecutionCapacity, scope))
    counts = dict.fromkeys(STATES, 0)
    for state, count in db.execute(
        select(Execution.state, func.count())
        .where(
            Execution.workspace_id == scope.workspace_id,
            Execution.project_id == scope.project_id,
            Execution.environment_id == scope.environment_id,
        )
        .group_by(Execution.state)
    ).all():
        counts[state] = count
    oldest = db.scalar(
        select(func.min(Execution.admitted_at_ms)).where(
            Execution.workspace_id == scope.workspace_id,
            Execution.project_id == scope.project_id,
            Execution.environment_id == scope.environment_id,
            Execution.state == "pending",
        )
    )
    return {
        "capacity": None
        if capacity is None
        else {
            "active": capacity.active,
            "active_limit": capacity.active_limit,
            "pending": capacity.pending,
            "pending_limit": capacity.pending_limit,
            "retained_waits": capacity.retained_waits,
            "wait_limit": capacity.wait_limit,
        },
        "states": counts,
        "oldest_pending_admitted_at_ms": oldest,
    }
