"""Test-only checks of the installation cursor's place in the lock order.

Finding 11's redesign (docs/superpowers/plans/2026-10-29-installation-capacity-lock.md):
only a reservation that takes a compute slot, or a scheduling transaction, takes the
cursor, and it takes it last. Everything else commits while another transaction
holds it. These helpers observe real PostgreSQL connections; they change nothing.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionSchedulerCursor

CURSOR = "execution_scheduler_cursors"
# Lock types that can make a statement wait on another transaction.
WAITABLE = {"relation", "tuple", "advisory", "object", "page", "extend"}


def locks_cursor(statement: str) -> bool:
    return CURSOR in statement and "FOR UPDATE" in statement.upper()


@contextmanager
def cursor_held(engine):
    """Another transaction holds the installation cursor while the block runs."""
    ready, release = threading.Event(), threading.Event()

    def hold():
        with Session(engine) as db, db.begin():
            db.scalar(select(ExecutionSchedulerCursor).with_for_update())
            ready.set()
            release.wait(60)

    holder = threading.Thread(target=hold)
    holder.start()
    assert ready.wait(10)
    try:
        yield
    finally:
        release.set()
        holder.join()


@contextmanager
def statements(engine):
    """Every statement the engine runs while the block runs, from any thread."""
    seen = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def without_cursor(engine, operation, seconds=20):
    """Run `operation` while another transaction holds the cursor, and return its result.

    It must finish within `seconds` and never lock the cursor itself. A regression
    that waits on the cursor fails here instead of hanging: the holder lets go
    before the operation is waited for again.
    """
    pool = ThreadPoolExecutor(1)
    try:
        with cursor_held(engine), statements(engine) as seen:
            future = pool.submit(operation)
            finished = future in wait([future], timeout=seconds).done
        result = future.result(timeout=60)
    finally:
        pool.shutdown(wait=False)
    assert finished, "the operation waited for the installation cursor"
    assert not [s for s in seen if locks_cursor(s)], "the operation locked the cursor"
    return result


def _backend_pid(connection):
    return connection.connection.dbapi_connection.info.backend_pid


def _locks(observer, pid):
    return {
        (row.locktype, row.relation or "", row.mode, row.granted)
        for row in observer.execute(
            text(
                "SELECT locktype, relation::regclass::text AS relation, mode, granted"
                " FROM pg_locks WHERE pid = :pid"
            ),
            {"pid": pid},
        )
    }


@contextmanager
def leaves(engine):
    """Observe every transaction that locks the cursor, at that moment and at commit.

    Yields a list of records, one per committed transaction that took the cursor:
    its locks when the cursor statement returned, its locks just before COMMIT, and
    every statement it ran after the cursor statement. `assert_leaf` checks one.
    """
    records = []
    observer, guard = engine.connect(), threading.Lock()
    watched = {}

    def snapshot(pid):
        with guard:  # one observer connection, shared by concurrent transactions
            return _locks(observer, pid)

    def before(connection, cursor, statement, parameters, context, executemany):
        record = watched.get(id(connection))
        if record is not None:
            record["after"].append(statement)

    def after(connection, cursor, statement, parameters, context, executemany):
        if locks_cursor(statement) and id(connection) not in watched:
            pid = _backend_pid(connection)
            watched[id(connection)] = {"pid": pid, "at": snapshot(pid), "after": []}

    def commit(connection):
        record = watched.pop(id(connection), None)
        if record is not None:
            record["commit"] = snapshot(record["pid"])
            records.append(record)

    def rollback(connection, *args):
        # A rolled-back savepoint released the cursor taken inside it: the cursor is
        # always last, so nothing of this transaction locked it before that savepoint.
        watched.pop(id(connection), None)

    listeners = (
        ("before_cursor_execute", before),
        ("after_cursor_execute", after),
        ("commit", commit),
        ("rollback", rollback),
        ("rollback_savepoint", rollback),
    )
    for name, function in listeners:
        event.listen(engine, name, function)
    try:
        yield records
    finally:
        for name, function in listeners:
            event.remove(engine, name, function)
        observer.close()


def assert_leaf(record):
    """The cursor was the transaction's last lock: nothing waitable was taken after it.

    Every lock is granted. Any lock entry present at commit but not when the cursor
    statement returned is on the cursor's own table or its indexes. After the
    cursor statement, the transaction ran only the installation sum, the cursor's
    own row (relocked or updated) and savepoint releases.
    """
    for state in ("at", "commit"):
        assert all(granted for *_, granted in record[state]), record[state]
    added = {
        lock
        for lock in record["commit"] - record["at"]
        if lock[0] in WAITABLE and not lock[1].startswith(CURSOR)
    }
    assert added == set(), added
    for statement in record["after"]:
        normalized = " ".join(statement.split()).upper()
        assert (
            "SUM(EXECUTION_CAPACITIES.ACTIVE)" in normalized
            or normalized.startswith("UPDATE EXECUTION_SCHEDULER_CURSORS ")
            or locks_cursor(statement)
            or normalized.startswith("RELEASE SAVEPOINT")
        ), statement
