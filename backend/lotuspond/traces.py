"""Read-only access to stored production traces.

Trace ingestion, monitors and trace assessments were retired by ADR-0013. Traces
recorded before then stay readable and exportable; nothing writes new ones.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import schemas as s
from .deps import DB, record, require
from .models import Assessment, Project, Trace, serialize
from .security import Principal, canonical

router = APIRouter(prefix="/api/v1")

EXPORT_LIMIT = 5000


def _project(db: Session, principal: Principal, identifier: str) -> Project:
    return record(db, Project, principal.workspace_id, identifier)


@router.get("/traces")
def traces(
    project_id: s.Identifier | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    query = select(Trace).where(Trace.workspace_id == principal.workspace_id)
    if project_id:
        _project(db, principal, project_id)
        query = query.where(Trace.project_id == project_id)
    rows = db.scalars(query.order_by(Trace.occurred_at.desc(), Trace.id.desc()).limit(limit))
    return {"items": [serialize(row) for row in rows]}


@router.get("/traces/export")
def export_traces(
    response: Response,
    project_id: s.Identifier | None = None,
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    """Export retained traces as canonical JSON with its own digest."""
    query = select(Trace).where(Trace.workspace_id == principal.workspace_id)
    if project_id:
        _project(db, principal, project_id)
        query = query.where(Trace.project_id == project_id)
    rows = list(
        db.scalars(query.order_by(Trace.created_at, Trace.id).limit(EXPORT_LIMIT + 1))
    )
    truncated = len(rows) > EXPORT_LIMIT
    traces = [serialize(row) for row in rows[:EXPORT_LIMIT]]
    payload = {
        "product": "Lotuspond",
        "schemaVersion": 1,
        "canonicalization": "sorted-json-utf8-v1",
        "project_id": project_id,
        "traces": traces,
        "truncated": truncated,
    }
    response.headers["content-disposition"] = (
        'attachment; filename="lotuspond-traces.json"'
    )
    return {**payload, "export_sha256": hashlib.sha256(canonical(payload).encode()).hexdigest()}


@router.get("/traces/{identifier}")
def trace_detail(
    identifier: s.Identifier,
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    trace = record(db, Trace, principal.workspace_id, identifier)
    assessments = db.scalars(
        select(Assessment)
        .where(Assessment.workspace_id == principal.workspace_id, Assessment.trace_id == trace.id)
        .order_by(Assessment.created_at.desc())
    )
    return {
        "trace": serialize(trace),
        "assessments": [serialize(row) for row in assessments],
    }
