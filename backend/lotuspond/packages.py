"""Tenant-scoped immutable package admission, never execution authorization."""

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .deps import DB, audit, record, require
from .models import PackagePublicationRequest, Project, WorkflowPackageVersion, serialize
from .package_contract import (
    MAX_PACKAGE_BYTES,
    SCHEMA_VERSION,
    canonical_package_json,
    package_digest,
    validate_package,
)
from .package_input import (
    READ_ERRORS,
    PackageReadError,
    decode_package_bytes,
    safe_validation_errors,
)
from .schemas import Identifier
from .security import Principal, fail

router = APIRouter(prefix="/api/v1")
VALIDATOR_REVISION = "lotuspond.workflow-package.validator.v1.1"
CANONICALIZATION = "lotuspond.workflow-package.canonical-json.v1"
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


def _conflict():
    fail(
        409,
        "package_conflict",
        "The immutable version or request key already identifies different content.",
    )


def _publish(
    db: Session, p: Principal, project_id: str, data: bytes, key: str | None, response: Response
):
    record(db, Project, p.workspace_id, project_id)
    try:
        package = validate_package(decode_package_bytes(data))
        content = canonical_package_json(package)
        sha256 = package_digest(package)
    except PackageReadError as error:
        raise HTTPException(
            422,
            {
                "error": "invalid_package",
                "message": READ_ERRORS[error.code],
                "errors": [{"path": [], "code": error.code, "message": READ_ERRORS[error.code]}],
            },
        ) from None
    except ValidationError as error:
        raise HTTPException(
            422,
            {
                "error": "invalid_package",
                "message": "The package does not satisfy the supported contract.",
                "errors": safe_validation_errors(error),
            },
        ) from None
    request_hash = hashlib.sha256(
        b"lotuspond.package-publication.v1\0" + project_id.encode("ascii") + b"\0" + content
    ).hexdigest()
    if key is not None:
        previous = db.scalar(
            select(PackagePublicationRequest).where(
                PackagePublicationRequest.workspace_id == p.workspace_id,
                PackagePublicationRequest.request_key == key,
            )
        )
        if previous:
            if previous.request_hash != request_hash:
                _conflict()
            row = record(db, WorkflowPackageVersion, p.workspace_id, previous.package_version_id)
            if row.project_id != project_id or row.canonical_content != content:
                _conflict()
            response.status_code = 200
            return metadata(row)
    row = WorkflowPackageVersion(
        workspace_id=p.workspace_id,
        project_id=project_id,
        package_id=package.package_id,
        version=package.version,
        schema_version=package.schema_version,
        validator_revision=VALIDATOR_REVISION,
        canonicalization=CANONICALIZATION,
        sha256=sha256,
        canonical_content=content,
        content_bytes=len(content),
        publisher_id=p.actor_id,
        publisher_kind="user" if p.user_id else "api_key",
    )
    created = False
    # Nested uniqueness recovery preserves the outer request transaction. The
    # winner's version, key and audit become visible together after its commit.
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        created = True
    except IntegrityError:
        row = db.scalar(
            select(WorkflowPackageVersion).where(
                WorkflowPackageVersion.workspace_id == p.workspace_id,
                WorkflowPackageVersion.project_id == project_id,
                WorkflowPackageVersion.package_id == package.package_id,
                WorkflowPackageVersion.version == package.version,
            )
        )
        if row is None or row.sha256 != sha256 or row.canonical_content != content:
            _conflict()
    if key is not None:
        try:
            with db.begin_nested():
                db.add(
                    PackagePublicationRequest(
                        workspace_id=p.workspace_id,
                        request_key=key,
                        request_hash=request_hash,
                        package_version_id=row.id,
                    )
                )
                db.flush()
        except IntegrityError:
            previous = db.scalar(
                select(PackagePublicationRequest).where(
                    PackagePublicationRequest.workspace_id == p.workspace_id,
                    PackagePublicationRequest.request_key == key,
                )
            )
            if (
                previous is None
                or previous.request_hash != request_hash
                or previous.package_version_id != row.id
            ):
                # Raising rolls back even a just-created version; no orphan remains.
                _conflict()
    if created:
        # Do not queue an audit before begin_nested(), which flushes pending state.
        audit(
            db,
            p,
            "packages.published",
            row.id,
            {
                "project_id": project_id,
                "package_id": row.package_id,
                "version": row.version,
                "sha256": row.sha256,
            },
        )
    response.status_code = 201 if created else 200
    return metadata(row)


@router.post(
    "/projects/{project_id}/packages",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
)
async def publish_package(
    project_id: Identifier,
    request: Request,
    response: Response,
    p: Principal = Depends(require("write")),
    db: Session = DB,
):
    keys = request.headers.getlist("idempotency-key")
    key = keys[0] if keys else None
    if len(keys) > 1 or (key is not None and re.fullmatch(r"[!-~]{1,128}", key) is None):
        fail(
            422, "invalid_idempotency_key", "Use one request key of 1–128 visible ASCII characters."
        )
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_PACKAGE_BYTES:
            fail(413, "body_too_large", "The package exceeds the byte limit.")
        data.extend(chunk)
    return await run_in_threadpool(_publish, db, p, project_id, bytes(data), key, response)


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
    query = select(WorkflowPackageVersion).where(
        WorkflowPackageVersion.workspace_id == p.workspace_id,
        WorkflowPackageVersion.project_id == project_id,
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
    return metadata(record(db, WorkflowPackageVersion, p.workspace_id, id))


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


@router.get("/package-contracts")
def package_contracts(p: Principal = Depends(require("read"))):
    return {
        "contracts": [
            {
                "schema_version": SCHEMA_VERSION,
                "validator_revision": VALIDATOR_REVISION,
                "canonicalization": CANONICALIZATION,
                "max_package_bytes": MAX_PACKAGE_BYTES,
            }
        ]
    }
