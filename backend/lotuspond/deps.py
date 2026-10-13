from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent
from .security import Principal, authenticate, fail, redact


def database_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as db:
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


DB = Depends(database_session, scope="function")


@lru_cache
def require(scope: str = "read"):
    def dependency(request: Request, db: Session = DB) -> Principal:
        principal = authenticate(db, request, scope)
        request.app.state.limiter.check(f"workspace:{principal.workspace_id}:{scope}", 1000)
        return principal

    return dependency


def record(db: Session, model, workspace: str, identifier: str, *, lock=False):
    query = select(model).where(model.workspace_id == workspace, model.id == identifier)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        fail(404, "not_found", "The requested record was not found in this workspace.")
    return row


def audit(
    db: Session, principal: Principal, action: str, resource_id: str | None = None, details=None
):
    db.add(
        AuditEvent(
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            action=action,
            resource_id=resource_id,
            details=redact(details or {}),
        )
    )
