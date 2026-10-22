"""PostgreSQL implementation of the orchestration adapter (ADR-0011).

Each claimed execution and each capacity step gets its own transaction from the
caller's session factory. Progression, sweeping and the recovery fence are
explicitly constructed dependencies; synthetic fixtures require test mode and
production wiring refuses to start without the real implementations.

Liveness: work that made no progress (a refused transition, a pending capacity
step, or a pass with no transitions) is re-marked for later, never "now", so a
blocked execution cannot spin a worker. Only `mark_runnable` sets the marker;
deferral clears it and marks the later time while the row lock is held.
"""

import logging
from collections import OrderedDict

from lotuspond_runtime.protocol import CanonicalUUID, OpaqueId
from pydantic import TypeAdapter
from sqlalchemy import select

from ..fleet.authority import database_now_ms
from ..models import Execution
from .engine_contracts import (
    AdvanceOutcome,
    EngineObservation,
    InterpreterCheck,
    ProgressReport,
    SweepReport,
    WorkerIdentity,
)
from .errors import ExecutionError, execution_service
from .orchestrator import (
    TERMINAL,
    claim_runnable,
    clear_runnable,
    consume_wakeups,
    mark_runnable,
)

logger = logging.getLogger("lotuspond.execution")

BOUND = 64
# A refused transition or pending capacity step is retried later, never in a hot
# loop. Repeated refusals of one execution back off exponentially in this worker.
RETRY_MS = 1000
MAX_RETRY_MS = 60000
_FAILURES = 1024


def _code(error):
    """Fixed codes only: database, provider and caller text never reach logs."""
    return error.code if isinstance(error, ExecutionError) else "unexpected"


@execution_service
def _defer(db, execution, delay_ms):
    """Make already-due, locked work due again only after `delay_ms` of database time."""
    clear_runnable(db, execution)
    mark_runnable(db, execution, database_now_ms(db) + delay_ms)


class PostgresExecutionEngine:
    def __init__(self, progression, sweeper, recovery, *, interpreter_ids, test_mode=False):
        identifiers = (
            tuple(interpreter_ids)
            if type(interpreter_ids) in (tuple, list, set, frozenset)
            else None
        )
        if not identifiers:
            raise ValueError("Interpreter versions are required")
        self.interpreter_ids = frozenset(
            TypeAdapter(OpaqueId).validate_python(value, strict=True) for value in identifiers
        )
        if any(item is None for item in (progression, sweeper, recovery)):
            raise ValueError("Progression, sweeper and recovery fence are required")
        if not test_mode and any(
            getattr(item, "test_only", False) for item in (progression, sweeper, recovery)
        ):
            raise ValueError("Synthetic providers require test mode")
        self.progression, self.sweeper, self.recovery = progression, sweeper, recovery
        # Operational back-off only; never input to progression, lost on restart.
        self._failures = OrderedDict()

    def _fence(self, db):
        try:
            epoch = self.recovery.current(db)
            if type(epoch) is not int or epoch < 0:
                raise ValueError
            return epoch
        except Exception:
            raise ExecutionError("authority_unavailable") from None

    def _worker(self, worker, role):
        worker = WorkerIdentity.model_validate(worker)
        if worker.role != role or not set(worker.interpreter_ids) <= self.interpreter_ids:
            raise ExecutionError("forbidden")
        return worker

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ExecutionError("invalid_request")
        return limit

    def _backoff(self, execution_id):
        """Count one more refusal; return the delay before the next try."""
        count = self._failures.pop(execution_id, 0) + 1
        self._failures[execution_id] = count
        while len(self._failures) > _FAILURES:
            self._failures.popitem(last=False)
        return self._delay(execution_id)

    def _delay(self, execution_id):
        count = self._failures.get(execution_id, 0)
        return min(RETRY_MS * 2 ** min(max(count - 1, 0), 16), MAX_RETRY_MS)

    def check_interpreter(self, interpreter_id: str) -> InterpreterCheck:
        if type(interpreter_id) is str and interpreter_id in self.interpreter_ids:
            return InterpreterCheck(compatible=True, code="supported")
        return InterpreterCheck(compatible=False, code="unsupported_interpreter")

    @execution_service
    def describe(self, db, execution_id: str) -> EngineObservation:
        execution_id = TypeAdapter(CanonicalUUID).validate_python(execution_id, strict=True)
        row = db.scalar(
            select(Execution)
            .where(Execution.id == execution_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ExecutionError("not_found")
        return EngineObservation(
            execution_id=row.id,
            interpreter_id=row.interpreter_id,
            state=row.state,
            requested_state=row.requested_state,
            runnable=row.runnable_at_ms is not None,
            reason=row.reason,
        )

    def _advance(self, db, worker, execution):
        """Apply pure transitions under a savepoint; a refusal keeps the wake-ups pending."""
        try:
            with db.begin_nested():
                consume_wakeups(db, execution)
                clear_runnable(db, execution)
                outcome = AdvanceOutcome.model_validate(
                    self.progression.advance_execution(db, worker, execution, bound=BOUND)
                )
                db.flush()
                if (
                    outcome.execution_id != execution.id
                    or outcome.transitions > BOUND
                    or outcome.state != execution.state
                    or (outcome.runnable and execution.state in TERMINAL)
                ):
                    raise ExecutionError("conflict")
                capacity = outcome.reason == "capacity"
                if outcome.runnable and not capacity and outcome.transitions:
                    # Self-continuation joins the back of the due queue.
                    mark_runnable(db, execution, database_now_ms(db))
                elif outcome.runnable:
                    # A capacity step, or a pass that changed nothing, is retried later;
                    # a capacity step that keeps failing backs off like a refusal.
                    _defer(db, execution, self._delay(execution.id) if capacity else RETRY_MS)
        except Exception as error:
            # The savepoint restored the marker and the pending wake-ups.
            logger.warning("Execution transition deferred: %s", _code(error))
            _defer(db, execution, self._backoff(execution.id))
            return None
        if outcome.reason != "capacity":
            self._failures.pop(execution.id, None)
        return outcome

    def _capacity(self, sessions, worker, execution_id):
        """Capacity counters change only in a separate transaction in Task 5's order.

        The pure transaction already deferred the marker; a step that made progress
        makes the execution due again at once (the earliest request wins).
        """
        try:
            with sessions() as db, db.begin():
                self._fence(db)
                outcome = AdvanceOutcome.model_validate(
                    self.progression.apply_capacity(db, worker, execution_id)
                )
                if outcome.execution_id != execution_id:
                    raise ExecutionError("conflict")
                row = db.get(Execution, execution_id)
                if outcome.transitions and row is not None and row.state not in TERMINAL:
                    mark_runnable(db, row, database_now_ms(db))
            if outcome.transitions:
                self._failures.pop(execution_id, None)
        except Exception as error:
            logger.warning("Execution capacity step deferred: %s", _code(error))
            self._backoff(execution_id)

    def progress(self, sessions, worker, *, limit: int) -> ProgressReport:
        worker = self._worker(worker, "orchestrator")
        limit = self._limit(limit)
        claimed = transitions = deferred = 0
        for _ in range(limit):
            capacity = None
            with sessions() as db, db.begin():
                self._fence(db)
                execution = claim_runnable(db, worker)
                if execution is None:
                    break
                claimed += 1
                outcome = self._advance(db, worker, execution)
                if outcome is None:
                    deferred += 1
                else:
                    transitions += outcome.transitions
                    if outcome.runnable:
                        deferred += 1
                        if outcome.reason == "capacity":
                            capacity = execution.id
            if capacity is not None:
                self._capacity(sessions, worker, capacity)
        return ProgressReport(claimed=claimed, transitions=transitions, deferred=deferred)

    def sweep(self, sessions, worker, *, limit: int) -> SweepReport:
        worker = self._worker(worker, "sweeper")
        limit = self._limit(limit)
        with sessions() as db, db.begin():
            self._fence(db)
        try:
            return SweepReport.model_validate(self.sweeper.sweep(sessions, worker, limit=limit))
        except ExecutionError:
            raise
        except Exception:
            raise ExecutionError("invalid_request") from None
