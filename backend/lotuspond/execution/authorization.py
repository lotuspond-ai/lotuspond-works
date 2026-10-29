"""Current customer authority under caller-owned PostgreSQL transactions."""

from dataclasses import dataclass, field
from datetime import UTC

from lotuspond_runtime.protocol import CanonicalUUID, Scope
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..fleet.authority import database_now_ms
from ..models import ApiKey, Environment, Membership, MfaFactor, Project, User, Workspace
from ..security import Principal, enrollment_pending
from .errors import ExecutionError, execution_service

OPERATIONS = frozenset(
    {"publish", "manage", "execute", "signal", "human_form", "read", "control", "reconcile"}
)
_SEAL = object()


@dataclass(frozen=True)
class ExecutionActor:
    user_id: str
    api_key_id: str | None
    scope: Scope
    role: str
    _principal: Principal | None = field(default=None, repr=False, compare=False)
    _seal: object = field(default=None, repr=False, compare=False)


REPLAY = "lotuspond.execution.replay"


def mark_replay(db) -> None:
    """This transaction answered an equal repeat; the API audits only first-time work."""
    db.info[REPLAY] = True


def replayed(db) -> bool:
    return db.info.pop(REPLAY, False)


def lock(db, query):
    return db.scalar(query.with_for_update().execution_options(populate_existing=True))


def scoped(model, scope):
    return select(model).where(
        *(getattr(model, key) == value for key, value in scope.model_dump().items())
    )


def record(db, model, scope, identifier, *, locked=False):
    identifier = TypeAdapter(CanonicalUUID).validate_python(identifier, strict=True)
    query = scoped(model, scope).where(model.id == identifier)
    row = lock(db, query) if locked else db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise ExecutionError("not_found")
    return row


def _read(db, query):
    return db.scalar(query.execution_options(populate_existing=True))


@execution_service
def authorize_execution(
    db: Session, principal: Principal, scope: Scope, operation: str, *, locked=None
) -> ExecutionActor:
    """Current authority for one operation, with the same rules locked or not.

    Writes lock the account rows, so a revocation cannot commit between the check
    and the write. A read fences no write, so it reads current committed rows
    without locks and never queues behind writers or other readers (Task 12).
    `locked=False` makes any operation such a lock-free pre-check: the API's,
    before a service reauthorizes under its own lock order.
    """
    if locked is None:
        locked = operation != "read"
    get = lock if locked else _read
    if type(principal) is not Principal or operation not in OPERATIONS:
        raise ExecutionError("forbidden")
    scope = Scope.model_validate(scope)
    if principal.workspace_id != scope.workspace_id:
        raise ExecutionError("forbidden")
    workspace = get(db, select(Workspace).where(Workspace.id == scope.workspace_id))
    if workspace is None or workspace.status != "active":
        raise ExecutionError("forbidden")
    project = db.scalar(
        select(Project).where(
            Project.workspace_id == scope.workspace_id, Project.id == scope.project_id
        )
    )
    environment = db.scalar(
        select(Environment).where(
            Environment.workspace_id == scope.workspace_id,
            Environment.project_id == scope.project_id,
            Environment.id == scope.environment_id,
        )
    )
    if project is None or environment is None:
        raise ExecutionError("not_found")
    key = None
    if principal.user_id is None:
        key = db.scalar(
            select(ApiKey)
            .where(ApiKey.workspace_id == scope.workspace_id, ApiKey.id == principal.actor_id)
            .execution_options(populate_existing=True),
        )
        if key is None or key.created_by_user_id is None:
            raise ExecutionError("forbidden")
        user_id = key.created_by_user_id
    else:
        if principal.actor_id != principal.user_id:
            raise ExecutionError("forbidden")
        user_id = principal.user_id
    user = get(db, select(User).where(User.id == user_id))
    if key is not None:
        # Account lifecycle changes lock User before revoking attributed keys.
        # The unlocked creator hint grants nothing; bind it again after both locks.
        key = get(
            db,
            select(ApiKey).where(
                ApiKey.workspace_id == scope.workspace_id, ApiKey.id == principal.actor_id
            ),
        )
        if key is None or key.created_by_user_id != user_id:
            raise ExecutionError("forbidden")
    member = get(
        db,
        select(Membership).where(
            Membership.workspace_id == scope.workspace_id, Membership.user_id == user_id
        ),
    )
    now_ms = database_now_ms(db)
    if (
        user is None
        or user.disabled_at is not None
        or member is None
        or enrollment_pending(db, user_id)
    ):
        raise ExecutionError("forbidden")
    if (
        workspace.require_mfa
        and db.scalar(select(MfaFactor.id).where(MfaFactor.user_id == user_id)) is None
    ):
        raise ExecutionError("forbidden")
    if key:
        expires = (
            key.expires_at.replace(tzinfo=UTC) if key.expires_at.tzinfo is None else key.expires_at
        )
        required = {
            "publish": "write",
            "read": "read",
            "execute": "execute",
            "signal": "execution_signal",
            "control": "execute",
        }.get(operation)
        if (
            required is None
            or required not in key.scopes
            or key.revoked_at is not None
            or int(expires.timestamp() * 1000) <= now_ms
        ):
            raise ExecutionError("forbidden")
    if operation in {"manage", "human_form", "reconcile"} and member.role not in {
        "owner",
        "admin",
    }:
        raise ExecutionError("forbidden")
    if operation != "read" and member.role == "viewer":
        raise ExecutionError("forbidden")
    return ExecutionActor(user.id, key.id if key else None, scope, member.role, principal, _SEAL)


@execution_service
def reauthorize(db, actor, operation):
    if type(actor) is not ExecutionActor or actor._seal is not _SEAL or actor._principal is None:
        raise ExecutionError("forbidden")
    current = authorize_execution(db, actor._principal, actor.scope, operation)
    if (current.user_id, current.api_key_id) != (actor.user_id, actor.api_key_id):
        raise ExecutionError("forbidden")
    return current
