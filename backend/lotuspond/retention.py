"""Delete traces a workspace's subscription no longer promises to keep.

This is the only code in Lotuspond that removes customer evidence, so it is deliberately
awkward: it reports by default and deletes only when asked, it refuses a run larger than
the caller expected, and every workspace it touches gets an audit record naming the plan
and the cutoff that justified the deletion.

Retention governs deletion, not visibility. A trace stays readable and exportable until
this job removes it, so a plan change never makes evidence silently disappear.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import Settings
from .database import engine_for, sessions_for
from .entitlements import plan_for
from .models import AuditEvent, Trace, Workspace, now

DEFAULT_MAX_DELETIONS = 100_000


def expired_counts(db: Session, reference: datetime | None = None) -> list[dict]:
    """Per workspace, how many traces now sit outside its plan's retention window."""
    rows = []
    for workspace in db.scalars(select(Workspace).order_by(Workspace.created_at, Workspace.id)):
        plan = plan_for(workspace.plan)
        cutoff = plan.retention_cutoff(reference)
        expired = db.scalar(
            select(func.count())
            .select_from(Trace)
            .where(Trace.workspace_id == workspace.id, Trace.created_at < cutoff)
        )
        if expired:
            rows.append(
                {
                    "workspace_id": workspace.id,
                    "plan": plan.name,
                    "trace_retention_days": plan.trace_retention_days,
                    "cutoff": cutoff.isoformat(),
                    "expired_traces": int(expired),
                }
            )
    return rows


def run(
    settings: Settings,
    *,
    apply: bool = False,
    max_deletions: int = DEFAULT_MAX_DELETIONS,
    reference: datetime | None = None,
    sessions=None,
) -> dict:
    """Report or delete expired traces. Pass `sessions` to reuse an existing factory."""
    if max_deletions < 0:
        raise ValueError("The deletion ceiling cannot be negative")
    engine = None
    if sessions is None:
        engine = engine_for(settings)
        sessions = sessions_for(engine)
    try:
        with sessions() as db, db.begin():
            planned = expired_counts(db, reference)
            total = sum(row["expired_traces"] for row in planned)
            if total > max_deletions:
                # Stopping here is the point: an unexpectedly large run is more likely a
                # misconfigured plan than a genuine expiry, and a rollback cannot undo
                # deletion after the fact.
                raise ValueError(
                    f"{total} traces are expired, above the {max_deletions} ceiling; "
                    "review the plans or raise --max-deletions deliberately"
                )
            if not apply:
                return {"applied": False, "expired_traces": total, "workspaces": planned}
            deleted = 0
            for row in planned:
                result = db.execute(
                    delete(Trace).where(
                        Trace.workspace_id == row["workspace_id"],
                        Trace.created_at < datetime.fromisoformat(row["cutoff"]),
                    )
                )
                removed = int(result.rowcount or 0)
                deleted += removed
                db.add(
                    AuditEvent(
                        workspace_id=row["workspace_id"],
                        actor_id="retention",
                        action="traces.retention_deleted",
                        resource_id=row["workspace_id"],
                        details={
                            "plan": row["plan"],
                            "trace_retention_days": row["trace_retention_days"],
                            "cutoff": row["cutoff"],
                            "deleted_traces": removed,
                        },
                    )
                )
            return {
                "applied": True,
                "expired_traces": total,
                "deleted_traces": deleted,
                "workspaces": planned,
                "completed_at": now().isoformat(),
            }
    finally:
        if engine is not None:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete the expired traces; without it nothing is removed",
    )
    parser.add_argument("--max-deletions", type=int, default=DEFAULT_MAX_DELETIONS)
    args = parser.parse_args()
    report = run(Settings(), apply=args.apply, max_deletions=args.max_deletions)
    print(json.dumps(report, indent=2))
    if not report["applied"]:
        print(
            "Reported only. Re-run with --apply to delete these traces.",
            flush=True,
        )


if __name__ == "__main__":
    main()
