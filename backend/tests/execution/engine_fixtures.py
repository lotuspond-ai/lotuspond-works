"""Test-only orchestration fixtures and a subprocess driver; production cannot select them.

`python -m tests.execution.engine_fixtures <behavior> <state-directory>` runs the
real worker loop (`serve`) with these fixtures, so tests can stop, kill and
observe a separate worker process.
"""

import json
import sys
import time
from pathlib import Path

from sqlalchemy import select, text

from lotuspond.execution.engine import PostgresExecutionEngine
from lotuspond.execution.engine_contracts import INTERPRETER_V1, AdvanceOutcome, SweepReport
from lotuspond.models import ExecutionOutbox

CAPACITY_RELATIONS = (
    "execution_scheduler_cursors",
    "execution_capacities",
    "runtime_pools",
    "connections",
    "execution_usage_reservations",
)


def relation_locks(db, relations=CAPACITY_RELATIONS):
    """Relations this backend holds any lock on; a plain read takes AccessShareLock."""
    return set(
        db.scalars(
            text(
                "SELECT c.relname FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
                "WHERE l.pid = pg_backend_pid() AND l.locktype = 'relation' "
                "AND c.relname = ANY(:names)"
            ),
            {"names": list(relations)},
        )
    )


class FixtureProgression:
    """Records each call; one pure transition per claim unless told otherwise."""

    test_only = True

    def __init__(self, *, runnable=False, reason=None, fail=False, stall=None, transitions=1):
        self.runnable, self.reason, self.fail = runnable, reason, fail
        self.stall, self.transitions = stall, transitions
        self.calls, self.capacity_calls, self.locks = [], [], []

    def advance_execution(self, db, worker, execution, *, bound):
        self.calls.append(
            {
                "execution_id": execution.id,
                "worker_id": worker.worker_id,
                "bound": bound,
                "txid": db.scalar(text("SELECT txid_current()")),
                "pending_wakeups": db.scalar(
                    select(ExecutionOutbox.id)
                    .where(
                        ExecutionOutbox.execution_id == execution.id,
                        ExecutionOutbox.status == "pending",
                    )
                    .limit(1)
                ),
            }
        )
        self.locks.append(relation_locks(db))
        if self.stall is not None:
            self.stall(execution)
        if self.fail:
            raise RuntimeError("PRIVATE_PROGRESSION_FAILURE")
        return AdvanceOutcome(
            execution_id=execution.id,
            transitions=self.transitions,
            state=execution.state,
            runnable=self.runnable,
            reason=self.reason,
        )

    def apply_capacity(self, db, worker, execution_id):
        self.capacity_calls.append(
            {"execution_id": execution_id, "txid": db.scalar(text("SELECT txid_current()"))}
        )
        from lotuspond.models import Execution

        row = db.get(Execution, execution_id)
        return AdvanceOutcome(
            execution_id=execution_id,
            transitions=0,
            state=row.state,
            runnable=row.runnable_at_ms is not None,
            reason=None,
        )


class FixtureSweeper:
    test_only = True

    def __init__(self):
        self.calls = 0

    def sweep(self, sessions, worker, *, limit):
        self.calls += 1
        return SweepReport(
            timers=0, expired_waits=0, deadlines=0, lapsed_leases=0, expired_deliveries=0
        )


class FixtureFence:
    test_only = True

    def __init__(self, epoch=1):
        self.epoch = epoch

    def current(self, db):
        if self.epoch is None:
            raise RuntimeError("PRIVATE_RECOVERY_FAILURE")
        return self.epoch


def fixture_engine(progression=None, *, fence=None, interpreter_ids=(INTERPRETER_V1,)):
    return PostgresExecutionEngine(
        progression or FixtureProgression(),
        FixtureSweeper(),
        fence or FixtureFence(),
        interpreter_ids=interpreter_ids,
        test_mode=True,
    )


def _driver(behavior, state):
    from lotuspond.execution_worker import ExecutionWorkerSettings, serve

    state = Path(state)
    settings = ExecutionWorkerSettings(**json.loads((state / "settings.json").read_text()))

    def stall(execution):
        # Holding the execution row: announce, then stall until stopped or killed.
        (state / "holding.json").write_text(json.dumps({"execution_id": execution.id}))
        time.sleep(3600)

    progression = FixtureProgression(stall=stall if behavior == "stall" else None)
    return serve(settings, engine=fixture_engine(progression))


if __name__ == "__main__":
    sys.exit(_driver(sys.argv[1], sys.argv[2]))
