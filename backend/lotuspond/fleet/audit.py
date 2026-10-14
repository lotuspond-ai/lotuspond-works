"""Closed fleet audit vocabulary with IDs and revision only."""

from lotuspond_runtime.protocol import CanonicalUUID, Scope
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from ..models import AuditEvent

CODES = frozenset(
    {
        "fleet.connection.created",
        "fleet.connection.published",
        "fleet.connection.status",
        "fleet.pool.created",
        "fleet.pool.published",
        "fleet.pool.status",
        "fleet.secret.registered",
        "fleet.secret.revoked",
    }
)


def fleet_audit(
    db: Session, actor_id: str, scope: Scope, code: str, resource_id: str, revision: int | None
) -> None:
    scope = Scope.model_validate(scope)
    if code not in CODES or (
        revision is not None and (type(revision) is not int or not 1 <= revision <= 2147483647)
    ):
        raise ValueError("Invalid fleet audit")
    adapter = TypeAdapter(CanonicalUUID)
    actor_id = adapter.validate_python(actor_id, strict=True)
    resource_id = adapter.validate_python(resource_id, strict=True)
    details = {"project_id": scope.project_id, "environment_id": scope.environment_id}
    if revision is not None:
        details["revision"] = revision
    db.add(
        AuditEvent(
            workspace_id=scope.workspace_id,
            actor_id=actor_id,
            action=code,
            resource_id=resource_id,
            details=details,
        )
    )
