"""Current customer authority under caller-owned PostgreSQL transactions."""

from dataclasses import dataclass, field
from datetime import UTC

from lotuspond_runtime.protocol import CanonicalUUID, Scope
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import authority_gate
from ..fleet.authority import database_now_ms
from ..models import ApiKey, Environment, Membership, Project, User, Workspace
from ..security import Principal, enrollment_pending
from .errors import ExecutionError, execution_service

OPERATIONS = frozenset(
    {
        "publish",
        "manage",
        "execute",
        "signal",
        "human_form",
        "read",
        "control",
        "reconcile",
        # E05: claim, decide or submit a human task. Eligibility is the task's own
        # assignment, checked by the task service; keys never hold it.
        "review",
    }
)
# Operations whose authority rows are only fenced against an authority change: they
# share them (`share`, throughput option C), so two of them no longer queue for each
# other. Each serializes its own limits and idempotency on rows it still locks
# exclusively (capacity, execution, step, attempt, wait). A human form is a signal:
# `accept_signal` authorizes `signal` and then `human_form` in one transaction, so
# the two share alike or the second would upgrade. Every other write keeps the rows
# `FOR UPDATE`.
SHARED_OPERATIONS = frozenset({"execute", "signal", "human_form", "control", "review"})
_SEAL = object()
_UUID = TypeAdapter(CanonicalUUID)
_CHECKED = "lotuspond.execution.checked"


def checked(db) -> dict | None:
    """Checks this transaction made at its top level, for reuse later in it (owner
    decision, 2 November 2026: each lookup and permission check once per item).

    A caller keeps only a success it checked against rows this transaction holds to
    its end: taken at the top level (a savepoint's rollback releases what was locked
    inside it), shared, so this transaction never writes them (`share`), behind the
    gates that fence their unlocked inputs (`authority_gate`). Nothing is kept across
    transactions, and on SQLite, which has neither, nothing is kept at all."""
    transaction = db.get_transaction()
    if transaction is None or db.get_bind().dialect.name != "postgresql":
        return None
    state = db.info.get(_CHECKED)
    if state is None or state[0] is not transaction:
        state = (transaction, {})
        db.info[_CHECKED] = state
    return state[1]


def keep(db, entries, name, value) -> None:
    """Keep a check's success for this transaction, if it was made at its top level."""
    if entries is not None and not db.in_nested_transaction():
        entries[name] = value


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


def share(db, query):
    """`FOR SHARE`: an authority change's `FOR UPDATE` or write waits for this
    transaction, so none commits between a check and this commit, but another share
    does not wait (throughput option C). A transaction never locks or writes a row
    exclusively after sharing it: two that did would deadlock upgrading."""
    return db.scalar(query.with_for_update(read=True).execution_options(populate_existing=True))


def scoped(model, scope):
    return select(model).where(
        *(getattr(model, key) == value for key, value in scope.model_dump().items())
    )


def record(db, model, scope, identifier, *, locked=False, shared=False):
    identifier = _UUID.validate_python(identifier, strict=True)
    query = scoped(model, scope).where(model.id == identifier)
    if shared:
        row = share(db, query)
    elif locked:
        row = lock(db, query)
    else:
        row = db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise ExecutionError("not_found")
    return row


def _read(db, query):
    return db.scalar(query.execution_options(populate_existing=True))


def acting_user(db, principal, scope) -> str | None:
    """The user a principal acts as, by a plain read: grants nothing (`authority_gate`)."""
    if type(principal) is not Principal:
        return None
    if principal.user_id is not None:
        return principal.user_id
    return db.scalar(
        select(ApiKey.created_by_user_id).where(
            ApiKey.workspace_id == scope.workspace_id, ApiKey.id == principal.actor_id
        )
    )


@execution_service
def authorize_execution(
    db: Session, principal: Principal, scope: Scope, operation: str, *, locked=None
) -> ExecutionActor:
    """Current authority for one operation, with the same rules locked or not.

    Writes lock the account rows, so a revocation cannot commit between the check
    and the write: `SHARED_OPERATIONS` share them, others lock them exclusively. A
    read fences no write, so it reads current committed rows
    without locks and never queues behind writers or other readers (Task 12).
    `locked=False` makes any operation such a lock-free pre-check: the API's,
    before a service reauthorizes under its own lock order.

    A shared operation's locked check is made once per transaction (`checked`): its
    rows stay shared and its gates held to the end, so a repeat would read the same
    rows. An API key's expiry is a time, so a repeat checks it again on the clock.
    """
    if locked is None:
        locked = operation != "read"
    get = (share if operation in SHARED_OPERATIONS else lock) if locked else _read
    if type(principal) is not Principal or operation not in OPERATIONS:
        raise ExecutionError("forbidden")
    scope = Scope.model_validate(scope)
    if principal.workspace_id != scope.workspace_id:
        raise ExecutionError("forbidden")
    entries = checked(db) if locked and operation in SHARED_OPERATIONS else None
    name = ("authority", operation, principal, scope.model_dump_json())
    if entries is not None and name in entries:
        actor, expires_ms = entries[name]
        if expires_ms is not None and expires_ms <= database_now_ms(db):
            raise ExecutionError("forbidden")
        return actor
    if locked:
        # The fair gates first, before any row lock (`authority_gate`): the
        # workspace's, then the acting user's, shared with the rows or exclusive, so
        # an authority change's wait stays bounded. The user is found by a plain read
        # (an API key's creator); the rows are checked again under their locks.
        exclusive = operation not in SHARED_OPERATIONS
        authority_gate.workspace(db, scope.workspace_id, exclusive=exclusive)
        authority_gate.users(db, [acting_user(db, principal, scope)], exclusive=exclusive)
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
    actor = ExecutionActor(user.id, key.id if key else None, scope, member.role, principal, _SEAL)
    if locked and operation in SHARED_OPERATIONS:
        keep(db, checked(db), name, (actor, int(expires.timestamp() * 1000) if key else None))
    return actor


@execution_service
def reauthorize(db, actor, operation):
    if type(actor) is not ExecutionActor or actor._seal is not _SEAL or actor._principal is None:
        raise ExecutionError("forbidden")
    current = authorize_execution(db, actor._principal, actor.scope, operation)
    if (current.user_id, current.api_key_id) != (actor.user_id, actor.api_key_id):
        raise ExecutionError("forbidden")
    return current
