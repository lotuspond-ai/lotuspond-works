"""Option C, step 1: every lock and write of an authority row, by request path.

Option C of the throughput options (`docs/superpowers/plans/2026-10-30-execution-
throughput-options.md`) shares the authority rows in execution and runtime paths.
This audit records, for each path through the real routes and workers, which
authority rows its statements lock, in which mode, and which they write.

A plain `UPDATE` of a row after `FOR SHARE` of it in one transaction deadlocks like
two `FOR UPDATE` upgrades, so writes count as well as locks. The expected table is
today's, so any change of mode or new write in a path fails here until this test
records it on purpose.
"""

import re
from collections import defaultdict
from contextlib import contextmanager

from lotuspond_runtime.activity_client import canonical_value
from lotuspond_runtime.activity_protocol import ActivityResult
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from lotuspond.execution.scheduler import Scheduler
from lotuspond.models import ApiKey, User

from .activity_fixtures import Demand, attempt_at, deploy
from .activity_fixtures import activity as activity
from .api_fixtures import api as api
from .api_fixtures import key
from .control_fixtures import controlled as controlled
from .control_fixtures import until
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_progression import flows as flows
from .test_publication import harness as harness
from .test_runtime_routes import runtime_client, usage
from .test_sweeper import wait_of

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
_LOCK = re.compile(r"\bFOR (NO KEY UPDATE|KEY SHARE|UPDATE|SHARE)\b(?: OF ([\w, ]+))?", re.I)
_WRITE = re.compile(r"^\s*(UPDATE|DELETE FROM|INSERT INTO)\s+(\w+)", re.I)
_FROM = re.compile(
    r"\bFROM\s+(\w+)(.*?)(?:\bWHERE\b|\bORDER BY\b|\bLIMIT\b|\bFOR\b|$)", re.I | re.S
)
_JOIN = re.compile(r"\bJOIN\s+(\w+)", re.I)


def classify(statement):
    """(table, mode) for each row a statement locks, and each authority row it writes.

    Locks are recorded for every table, so the table shows where queues can form;
    writes only for authority rows, the ones a share would turn into an upgrade.
    """
    found = set()
    write = _WRITE.match(statement)
    if write and write.group(2) in AUTHORITY:
        found.add((write.group(2), write.group(1).split()[0].upper()))
    lock = _LOCK.search(statement)
    if lock and statement.lstrip().upper().startswith("SELECT"):
        mode = "FOR " + lock.group(1).upper()
        named = {name.strip() for name in (lock.group(2) or "").split(",") if name.strip()}
        source = _FROM.search(statement)
        tables = {source.group(1), *_JOIN.findall(source.group(2))} if source else set()
        for table in named or tables:
            found.add((table, mode))
    return found


class Audit:
    """Authority locks and writes, keyed by the path that ran them."""

    def __init__(self, engine):
        self.engine, self.path, self.seen = engine, None, defaultdict(set)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._statement)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._statement)

    @contextmanager
    def on(self, path):
        self.path = path
        try:
            yield
        finally:
            self.path = None

    def _statement(self, conn, cursor, statement, parameters, context, executemany):
        if self.path is not None:
            self.seen[self.path] |= classify(statement)

    def table(self):
        return {path: sorted(found) for path, found in sorted(self.seen.items())}


def test_classify_reads_lock_modes_and_writes():
    assert classify("SELECT workspaces.id FROM workspaces WHERE workspaces.id = %s FOR UPDATE") == {
        ("workspaces", "FOR UPDATE")
    }
    assert classify("SELECT users.id FROM users WHERE users.id = %s FOR SHARE") == {
        ("users", "FOR SHARE")
    }
    assert classify("UPDATE api_keys SET revoked_at=%s WHERE api_keys.id = %s") == {
        ("api_keys", "UPDATE")
    }
    joined = (
        "SELECT 1 FROM executions JOIN workspaces ON 1=1 WHERE x FOR NO KEY UPDATE OF workspaces"
    )
    assert classify(joined) == {("workspaces", "FOR NO KEY UPDATE")}
    assert classify("SELECT workspaces.id FROM workspaces WHERE workspaces.id = %s") == set()
    assert classify("INSERT INTO executions (id) VALUES (%s)") == set()
    assert classify("SELECT 1 FROM executions WHERE executions.id = %s FOR UPDATE") == {
        ("executions", "FOR UPDATE")
    }


# Today's table (8aa0ff9 plus part 7): every execution and runtime path takes its
# authority rows FOR UPDATE, and none writes one. Option C step 2 changes the
# execution and runtime rows to FOR SHARE; authority changes keep FOR UPDATE.
EXCLUSIVE_ACCOUNT = [
    ("api_keys", "FOR UPDATE"),
    ("memberships", "FOR UPDATE"),
    ("users", "FOR UPDATE"),
    ("workspaces", "FOR UPDATE"),
]
RUNTIME = [
    ("connections", "FOR UPDATE"),
    ("runtime_pools", "FOR UPDATE"),
    ("workspaces", "FOR UPDATE"),
]


def test_every_authority_lock_and_write_by_path(api):
    h = api
    revision = deploy(h, "activity_read")
    signal_revision = h.deploy("waits_fast", "waits.policy")
    client = runtime_client(h)
    with Session(h.engine) as db:
        owner = db.get(User, h.actor.user_id)
        owner_id = owner.id
    with Audit(h.engine) as audit:
        with audit.on("admit (route)"):
            response = h.client.post(
                h.M + "/executions",
                json={"deployment_revision_id": revision, "input": None},
                headers={**h.owner, "idempotency-key": "audit-admit"},
            )
            assert response.status_code == 201, response.text
        execution_id = response.json()["id"]
        with audit.on("progress (orchestrator)"):
            h.run()
        attempt_id = attempt_at(h, execution_id, "read")
        scheduler = Scheduler(h.budgets, Demand(10_000), test_mode=True)
        try:
            with audit.on("schedule (sweeper)"), Session(h.engine) as db, db.begin():
                scheduled = scheduler.schedule_batch(db, "test-installation")
                assert [item.attempt_id for item in scheduled] == [attempt_id]
        finally:
            scheduler.close()
        with audit.on("poll (runtime route)"):
            delivery = client.poll()
        with audit.on("claim (runtime route)"):
            lease = client.claim(delivery)
        with audit.on("store value (runtime route)"):
            handle = client.store(attempt_id, canonical_value(None))
        with audit.on("renew (runtime route)"):
            lease = client.renew(lease)
        with audit.on("result (runtime route)"):
            client.result(
                ActivityResult(
                    attempt_id=attempt_id,
                    lease_revision=lease.lease_revision,
                    result=handle,
                    code="succeeded",
                    usage=usage(h),
                )
            )
        with audit.on("progress (orchestrator)"):
            h.run()
        with audit.on("sweep (sweeper)"):
            h.sweep()
        # A signal and a control, on executions of their own.
        waiting = h.start("waits_fast", None, "waits.policy")
        h.run()
        until(h, wait_of(h, waiting, "pause").deadline_ms)
        h.sweep()
        h.run()
        callback = wait_of(h, waiting, "callback")
        with audit.on("signal (route)"):
            response = h.client.post(
                h.M + f"/executions/{waiting}/signals",
                json={"signal_id": "callback", "wait_id": callback.id, "payload": 7},
                headers=key(h, ["execution_signal"]),
            )
            assert response.status_code == 200, response.text
        response = h.client.post(
            h.M + "/executions",
            json={"deployment_revision_id": signal_revision, "input": None},
            headers={**h.owner, "idempotency-key": "audit-control"},
        )
        controlled_id = response.json()["id"]
        with audit.on("control (route)"):
            response = h.client.post(
                h.M + f"/executions/{controlled_id}/control",
                json={"request_id": "audit-cancel", "action": "cancel"},
                headers=h.owner,
            )
            assert response.status_code == 200, response.text
        # Authority changes, last: each keeps its rows exclusive.
        revoked = key(h, ["read"])
        with Session(h.engine) as db:
            prefix = revoked["authorization"].removeprefix("Bearer ")[:11]
            key_id = db.scalar(select(ApiKey.id).where(ApiKey.prefix == prefix))
        with audit.on("revoke key (route)"):
            response = h.client.post(f"/api/v1/api-keys/{key_id}/revoke", json={}, headers=h.owner)
            assert response.status_code == 200, response.text
        with audit.on("disable pool (fleet route)"):
            response = h.client.post(
                h.M + f"/runtime-pools/{h.pool_id}/status",
                json={"status": "disabled"},
                headers={**h.owner, "idempotency-key": "audit-pool"},
            )
            assert response.status_code == 200, response.text
        with Session(h.engine) as db, db.begin():
            db.get(User, owner_id).is_platform_admin = True
        with audit.on("suspend workspace (admin route)"):
            response = h.client.patch(
                f"/api/v1/admin/workspaces/{h.scope.workspace_id}",
                json={"status": "suspended", "reason": "Lock audit"},
                headers=h.owner,
            )
            assert response.status_code == 200, response.text
    table = audit.table()
    print("\nlocks (authority rows marked *) and authority writes by path:")
    for path, found in table.items():
        print(f"  {path}:")
        for name, mode in found:
            print(f"    {'*' if name in AUTHORITY else ' '} {name}: {mode}")
    # No execution or runtime path writes an authority row.
    for path, found in table.items():
        if not path.startswith(("revoke", "disable", "suspend")):
            assert not [row for row in found if not row[1].startswith("FOR ")], (path, found)
