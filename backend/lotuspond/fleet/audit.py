"""Closed fleet audit vocabulary with IDs and revision only."""

from dataclasses import dataclass

from lotuspond_runtime.protocol import CanonicalUUID, Scope
from pydantic import TypeAdapter
from sqlalchemy import text
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
        "fleet.enrollment.issued",
        "fleet.enrollment.exchanged",
        "fleet.enrollment.revoked",
        "fleet.instance.revoked",
        "fleet.session.acquired",
        "fleet.identity.clock_fence",
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


@dataclass(frozen=True)
class ClockFenceEvent:
    scope: Scope
    actor_id: str
    resource_id: str

    def __post_init__(self):
        object.__setattr__(self, "scope", Scope.model_validate(self.scope))
        adapter = TypeAdapter(CanonicalUUID)
        adapter.validate_python(self.actor_id, strict=True)
        adapter.validate_python(self.resource_id, strict=True)


_CLOCK_FENCE = object()


def queue_clock_fence(db, event: ClockFenceEvent):
    if type(event) is not ClockFenceEvent:
        raise ValueError("Invalid clock fence")
    # One fixed, typed event per denied request. No arbitrary details or secrets.
    db.info[_CLOCK_FENCE] = event


def take_clock_fence(db):
    event = db.info.pop(_CLOCK_FENCE, None)
    return event if type(event) is ClockFenceEvent else None


def persist_clock_fence(db, event):
    if db.get_bind().dialect.name == "postgresql":
        # This is an audit-only transaction after rollback: local timeouts
        # cannot leak into a later business transaction or hold denial open.
        db.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
    fleet_audit(
        db, event.actor_id, event.scope, "fleet.identity.clock_fence", event.resource_id, None
    )
