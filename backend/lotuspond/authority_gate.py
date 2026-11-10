"""Fair authority gates, taken before any row lock (E04 throughput option C).

Execution and runtime writes share their authority rows (`FOR SHARE`), and authority
changes still lock them `FOR UPDATE`. PostgreSQL grants a compatible `FOR SHARE` while
a `FOR UPDATE` waits, so under sustained shared load an authority change could wait
without bound. A gate is a transaction-level advisory lock taken before any row lock:
shared by execution and runtime writes, exclusive by authority changes, user-row
lockers and the enrollment allocators. PostgreSQL's lock manager queues a new shared
request behind a waiting exclusive one, so an exclusive gate waits only for the
transactions that already hold the gate, and its row locks then meet no shared holder
(owner decisions, 30 October 2026: "C with the fair gate"; 1 November 2026: a gate per
user, since user rows are not workspace-scoped).

Keys use the two-integer form, `pg_advisory_xact_lock(namespace, key)`. PostgreSQL
keeps that key space apart from the single-bigint keys used elsewhere (initialization
and offline provisioning). `namespace` is fixed per kind (`WORKSPACE`, `USER`, `POOL`);
`key` is the first four bytes of SHA-256 over `lotuspond.authority-gate.<kind>:<id>`,
read as a signed big-endian integer. Two identifiers that share a key serialize against
each other's exclusive gate: extra waiting, never a missed fence, because the row locks
still fence.

Order: every gate before any row lock; the workspace gate, then user gates (by key),
then the pool gate. A transaction never asks for a gate exclusive after holding it
shared (two that did would deadlock); asking for it shared after holding it exclusive
changes nothing.
"""

from hashlib import sha256

from sqlalchemy import text

WORKSPACE = 0x4C505701
USER = 0x4C505503
POOL = 0x4C505002
_NAMES = {WORKSPACE: "workspace", USER: "user", POOL: "pool"}
_HELD = "lotuspond.authority_gate"


def key(kind: int, identifier: str) -> int:
    digest = sha256(f"lotuspond.authority-gate.{_NAMES[kind]}:{identifier}".encode()).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


def _postgresql(db) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _held(db) -> dict:
    """Gates this transaction holds, by (kind, key): True when exclusive.

    A session that begins on first use has no transaction until then; it is begun
    here, so the gates are remembered for the transaction they belong to."""
    transaction = db.get_transaction() or db.begin()
    state = db.info.get(_HELD)
    if state is None or state[0] is not transaction:
        state = (transaction, {})
        db.info[_HELD] = state
    return state[1]


def gate(db, kind: int, identifier: str, *, exclusive: bool = False) -> None:
    """Hold `kind`'s gate for `identifier` to the end of this transaction."""
    if not _postgresql(db):
        return
    name = (kind, key(kind, identifier))
    held = _held(db)
    if name in held:
        if exclusive and not held[name]:
            raise RuntimeError("an authority gate held shared cannot become exclusive")
        return
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    db.execute(text(f"SELECT {function}(:namespace, :key)"), {"namespace": kind, "key": name[1]})
    if not db.in_nested_transaction():
        # A savepoint's rollback releases what was taken inside it, so only gates
        # taken at the top of the transaction are remembered.
        held[name] = exclusive


def held(db, kind: int, identifier: str) -> bool:
    """Whether this transaction holds `kind`'s gate for `identifier`, in any mode."""
    return not _postgresql(db) or (kind, key(kind, identifier)) in _held(db)


def try_shared(db, kind: int, identifier: str) -> bool:
    """The gate shared, unless it is held or awaited exclusive: then False, at once.

    For a claim that skips what it cannot take now (`orchestrator.claim_runnable`).
    Taken inside the caller's savepoint, so skipping a candidate releases it.
    """
    if not _postgresql(db):
        return True
    if _held(db).get((kind, key(kind, identifier))) is not None:
        return True
    return bool(
        db.scalar(
            text("SELECT pg_try_advisory_xact_lock_shared(:namespace, :key)"),
            {"namespace": kind, "key": key(kind, identifier)},
        )
    )


def taken(db, kind: int, identifier: str) -> None:
    """Remember a gate `try_shared` took inside a savepoint the caller then released
    into its transaction, so a later `gate` call does not take it again."""
    if _postgresql(db) and not db.in_nested_transaction():
        _held(db).setdefault((kind, key(kind, identifier)), False)


def workspace(db, workspace_id: str, *, exclusive: bool = False) -> None:
    gate(db, WORKSPACE, workspace_id, exclusive=exclusive)


def users(db, user_ids, *, exclusive: bool = False) -> None:
    """User gates, in key order, for each distinct user (`None` skipped)."""
    for user_id in sorted({u for u in user_ids if u}, key=lambda u: key(USER, u)):
        gate(db, USER, user_id, exclusive=exclusive)


def pool(db, workspace_id: str, pool_id: str, *, exclusive: bool = False, users_=()) -> None:
    """The workspace gate shared, then user gates shared, then the pool's.

    A transaction that holds the workspace gate exclusive needs no pool gate: no
    other transaction is inside the workspace. User rows span workspaces, so their
    gates are taken whatever the workspace gate's mode."""
    gate(db, WORKSPACE, workspace_id)
    users(db, users_)
    if _postgresql(db) and _held(db).get((WORKSPACE, key(WORKSPACE, workspace_id))):
        return
    gate(db, POOL, pool_id, exclusive=exclusive)
