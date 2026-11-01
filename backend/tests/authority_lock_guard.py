"""Fail any test whose transaction breaks option C's lock rules on authority rows.

Throughput option C (`docs/superpowers/plans/2026-10-30-execution-throughput-
options.md`, owner decisions of 30 October 2026) has execution and runtime writes
take the authority rows `FOR SHARE` under a shared fair gate (`lotuspond.
authority_gate`), and authority changes take the gate exclusive before locking or
writing the rows. Every statement on every engine is watched, per transaction, and a
transaction that does any of these is recorded:
- shares an authority table, then locks it `FOR UPDATE` or writes it (an upgrade:
  two such transactions deadlock);
- takes a gate after any row lock, or a gate exclusive after holding it shared;
- shares an authority row without its gate (the workspace gate, or for a pool row the
  pool gate), or locks or writes one exclusively without the gate held exclusive.
  These keep correctness, since the row locks still fence, but lose the bounded wait.

The test then fails at teardown (`conftest.py`). Recording, not raising, so
application code that catches broad errors cannot hide one. Tables are matched, not
rows, which is conservative. Only statements that product code issues are judged:
test fixtures that write authority rows directly are not authority changes.
"""

import re
import sys

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool

from lotuspond.authority_gate import POOL, USER, WORKSPACE

AUTHORITY = frozenset(
    {
        "workspaces",
        "users",
        "api_keys",
        "memberships",
        "runtime_pools",
        "connections",
        "execution_deployments",
        "execution_deployment_revisions",
    }
)
# The owner's gate order: workspace, then users, then pool (1 November 2026).
_RANK = {WORKSPACE: 0, USER: 1, POOL: 2}
_LOCK = re.compile(r"\bFOR (NO KEY UPDATE|KEY SHARE|UPDATE|SHARE)\b(?: OF ([\w, ]+))?", re.I)
_WRITE = re.compile(r"^\s*(UPDATE|DELETE FROM)\s+(\w+)", re.I)
_FROM = re.compile(
    r"\bFROM\s+(\w+)(.*?)(?:\bWHERE\b|\bORDER BY\b|\bLIMIT\b|\bFOR\b|$)", re.I | re.S
)
_JOIN = re.compile(r"\bJOIN\s+(\w+)", re.I)
_GATE = re.compile(r"\bpg_(try_)?advisory_xact_lock(_shared)?\s*\(", re.I)
_SAVEPOINT = re.compile(r"^\s*(SAVEPOINT|ROLLBACK TO SAVEPOINT|RELEASE SAVEPOINT)\s+(\w+)", re.I)

upgrades: list[tuple[str, str]] = []
_state: dict[int, dict] = {}


def _tables(statement):
    lock = _LOCK.search(statement)
    if lock is None or not statement.lstrip().upper().startswith("SELECT"):
        return None, set()
    named = {name.strip() for name in (lock.group(2) or "").split(",") if name.strip()}
    source = _FROM.search(statement)
    tables = {source.group(1), *_JOIN.findall(source.group(2))} if source else set()
    return lock.group(1).upper(), named or tables


def _gated(state, table, exclusive):
    """Whether the transaction holds the gate that bounds this lock's wait."""
    kinds = {WORKSPACE: [], USER: [], POOL: []}
    for (namespace, _), held_exclusive in state["gates"].items():
        kinds.setdefault(namespace, []).append(held_exclusive)
    workspace_exclusive = any(kinds[WORKSPACE])
    if table == "users":  # not workspace-scoped: the user gate alone bounds it
        return any(kinds[USER]) if exclusive else bool(kinds[USER])
    if table == "runtime_pools":
        if exclusive:
            return workspace_exclusive or any(kinds[POOL])
        return workspace_exclusive or bool(kinds[POOL])
    return workspace_exclusive if exclusive else bool(kinds[WORKSPACE])


def _record(rule, statement):
    frames = [
        f
        for f in _product_frames()
        if not f.startswith(("authority_gate.py", "execution/errors.py"))
    ]
    where = " < ".join(frames[:9])
    upgrades.append((rule, where, " ".join(statement.split())[:160]))


def _product_frames():
    frames, frame = [], sys._getframe(3)
    while frame is not None:
        name = frame.f_code.co_filename.replace("\\", "/")
        if "/lotuspond/" in name:
            short = name[name.rindex("/lotuspond/") + len("/lotuspond/") :]
            frames.append(f"{short}:{frame.f_lineno} {frame.f_code.co_name}")
        frame = frame.f_back
    return frames


def summary():
    """One line per (rule, place), with its count, for the teardown assertion."""
    from collections import Counter

    counts = Counter((rule, where) for rule, where, _ in upgrades)
    return "\n".join(f"{n} x {rule} at {where}" for (rule, where), n in counts.most_common())


def _from_product():
    """Whether product code (the `lotuspond` package) issued the current statement:
    the nearest caller frame in `lotuspond/` or in `tests/` decides."""
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_code.co_filename.replace("\\", "/")
        if "/lotuspond/" in name:
            return True  # editable or installed
        if "/tests/" in name and "/site-packages/" not in name:
            return False
        frame = frame.f_back
    return False


def _fresh():
    return {"gates": {}, "rows": False, "shared": set(), "exclusive": set(), "savepoints": []}


def _savepoint(state, verb, name):
    """A savepoint's rollback releases the row locks and gates taken inside it."""
    snapshot = {k: (set(v) if isinstance(v, set) else v) for k, v in state.items()}
    snapshot["gates"] = dict(state["gates"])
    names = [saved for saved, _ in state["savepoints"]]
    if verb == "SAVEPOINT":
        state["savepoints"].append((name, snapshot))
    elif name in names:
        index = names.index(name)
        saved = state["savepoints"][index][1]
        if verb == "ROLLBACK TO SAVEPOINT":
            stack = state["savepoints"][: index + 1]
            state.update({k: v for k, v in saved.items() if k != "savepoints"})
            state["gates"], state["savepoints"] = dict(saved["gates"]), stack
        else:
            del state["savepoints"][index:]


def _statement(conn, cursor, statement, parameters, context, executemany):
    key = id(conn.connection.dbapi_connection)
    state = _state.setdefault(key, _fresh())
    savepoint = _SAVEPOINT.match(statement)
    if savepoint is not None:
        _savepoint(state, " ".join(savepoint.group(1).upper().split()), savepoint.group(2))
        return
    gate = _GATE.search(statement)
    write = _WRITE.match(statement)
    mode, tables = _tables(statement)
    if (gate is None and write is None and mode is None) or not _from_product():
        return
    if gate is not None and isinstance(parameters, dict) and "namespace" in parameters:
        name = (parameters["namespace"], parameters["key"])
        exclusive = gate.group(2) is None
        if state["rows"]:
            _record("gate after a row lock", statement)
        if exclusive and state["gates"].get(name) is False:
            _record("gate shared, then exclusive", statement)
        rank = _RANK.get(name[0], 0)
        if name not in state["gates"] and any(
            _RANK.get(held[0], 0) > rank for held in state["gates"]
        ):
            _record("gate out of order (workspace, user, pool)", statement)
        state["gates"][name] = state["gates"].get(name, False) or exclusive
        return
    if write and write.group(2) in AUTHORITY:
        table = write.group(2)
        if table in state["shared"] and table not in state["exclusive"]:
            if not _gated(state, table, exclusive=True):  # an exclusive gate: no sharer
                _record(f"{table}: shared, then written", statement)
        state["exclusive"].add(table)
        if not _gated(state, table, exclusive=True):
            _record(f"{table}: written without its gate exclusive", statement)
        return
    if mode is None:
        return
    state["rows"] = True
    shared = mode in ("SHARE", "KEY SHARE")
    # SKIP LOCKED and NOWAIT never wait, so no gate bounds them (worker claims).
    waits = not re.search(r"\b(SKIP LOCKED|NOWAIT)\b", statement, re.I)
    for table in tables & AUTHORITY:
        if shared:
            if table not in state["exclusive"]:  # already held exclusive: no upgrade
                state["shared"].add(table)
        else:
            if table in state["shared"] and table not in state["exclusive"]:
                if not _gated(state, table, exclusive=True):  # an exclusive gate: no sharer
                    _record(f"{table}: shared, then locked FOR {mode}", statement)
            state["exclusive"].add(table)
        if waits and not _gated(state, table, exclusive=not shared):
            gate = "shared gate" if shared else "gate exclusive"
            _record(f"{table}: locked FOR {mode} without its {gate}", statement)


def _end(conn):
    _state.pop(id(conn.connection.dbapi_connection), None)


def _checkin(dbapi_connection, record):
    # A pool reset on return rolls back without a Connection event.
    _state.pop(id(dbapi_connection), None)


def install():
    if not event.contains(Engine, "before_cursor_execute", _statement):
        event.listen(Engine, "before_cursor_execute", _statement)
        event.listen(Engine, "commit", _end)
        event.listen(Engine, "rollback", _end)
        event.listen(Pool, "checkin", _checkin)
