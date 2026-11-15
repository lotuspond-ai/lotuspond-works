"""Stored workflow-package v1 publications: read-only history (ADR-0013 step 3).

Publishing v1 is retired (docs/superpowers/plans/2026-11-15-retire-workflow-package-v1.md);
executable-v2 is published through `/executable-packages`. Versions already published stay
readable here, metadata and exact canonical bytes, and nothing is validated or rewritten on
read. The tables are unchanged.
"""

import base64
import binascii
import json
import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, defer

from .deps import DB, record, require
from .models import Project, WorkflowPackageVersion, serialize
from .schemas import Identifier
from .security import Principal, fail

router = APIRouter(prefix="/api/v1")
_METADATA = {
    "id",
    "created_at",
    "workspace_id",
    "project_id",
    "package_id",
    "version",
    "schema_version",
    "validator_revision",
    "canonicalization",
    "sha256",
    "content_bytes",
    "publisher_id",
    "publisher_kind",
}


def metadata(row: WorkflowPackageVersion) -> dict:
    return serialize(row, exclude=set(row.__table__.columns.keys()) - _METADATA)


def _cursor(row: WorkflowPackageVersion, workspace: str, project: str) -> str:
    value = json.dumps(
        [workspace, project, row.created_at.isoformat(), row.id], separators=(",", ":")
    )
    return base64.urlsafe_b64encode(value.encode("ascii")).decode("ascii")


def _decode_cursor(value: str, workspace: str, project: str) -> tuple[datetime, str]:
    try:
        raw = base64.b64decode(value, altchars=b"-_", validate=True)
        parts = json.loads(raw)
        if (
            not isinstance(parts, list)
            or len(parts) != 4
            or parts[:2] != [workspace, project]
            or not all(type(part) is str for part in parts)
            or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", parts[3]) is None
        ):
            raise ValueError()
        time = datetime.fromisoformat(parts[2])
        if time.tzinfo is None:
            # SQLite test timestamps lose UTC, while PostgreSQL retains it.
            from datetime import UTC

            time = time.replace(tzinfo=UTC)
        return time, parts[3]
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        fail(422, "invalid_cursor", "The cursor is invalid for this project and workspace.")


@router.get("/packages")
def list_packages(
    project_id: Identifier,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    p: Principal = Depends(require("read")),
    db: Session = DB,
):
    record(db, Project, p.workspace_id, project_id)
    query = (
        select(WorkflowPackageVersion)
        .options(defer(WorkflowPackageVersion.canonical_content, raiseload=True))
        .where(
            WorkflowPackageVersion.workspace_id == p.workspace_id,
            WorkflowPackageVersion.project_id == project_id,
        )
    )
    if cursor is not None:
        time, identifier = _decode_cursor(cursor, p.workspace_id, project_id)
        query = query.where(
            or_(
                WorkflowPackageVersion.created_at < time,
                and_(
                    WorkflowPackageVersion.created_at == time,
                    WorkflowPackageVersion.id < identifier,
                ),
            )
        )
    rows = list(
        db.scalars(
            query.order_by(
                WorkflowPackageVersion.created_at.desc(), WorkflowPackageVersion.id.desc()
            ).limit(limit + 1)
        )
    )
    return {
        "items": [metadata(row) for row in rows[:limit]],
        "next": _cursor(rows[limit - 1], p.workspace_id, project_id) if len(rows) > limit else None,
    }


@router.get("/packages/{id}")
def get_package(id: Identifier, p: Principal = Depends(require("read")), db: Session = DB):
    row = db.scalar(
        select(WorkflowPackageVersion)
        .options(defer(WorkflowPackageVersion.canonical_content, raiseload=True))
        .where(
            WorkflowPackageVersion.workspace_id == p.workspace_id,
            WorkflowPackageVersion.id == id,
        )
    )
    if row is None:
        fail(404, "not_found", "The requested record was not found in this workspace.")
    return metadata(row)


@router.get("/packages/{id}/content")
def get_content(id: Identifier, p: Principal = Depends(require("read")), db: Session = DB):
    row = record(db, WorkflowPackageVersion, p.workspace_id, id)
    return Response(
        content=row.canonical_content,
        media_type="application/json",
        headers={
            "ETag": f'"{row.sha256}"',
            "X-Package-SHA256": row.sha256,
            "X-Package-Canonicalization": row.canonicalization,
        },
    )
