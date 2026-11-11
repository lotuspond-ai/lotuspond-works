"""Inert orchestration adapter contracts (ADR-0011).

No database session, worker or provider is imported here. Worker identity and
supported interpreter versions are operator-owned routing; they grant nothing.
None of these values carries payloads, customer names or caller-chosen routing.
"""

from collections.abc import Callable
from typing import Annotated, Literal, Protocol

from lotuspond_runtime.protocol import CanonicalUUID, OpaqueId, SafeInt
from pydantic import BeforeValidator, Field

from .contracts import Contract, array

INTERPRETER_V1 = "lotuspond.execution-interpreter.v1"

ExecutionState = Literal[
    "pending",
    "running",
    "waiting",
    "paused",
    "cancelled",
    "succeeded",
    "failed",
    "reconciliation_required",
]
RequestedState = Literal["running", "paused", "cancelled"]
Reason = Literal[
    "capacity",
    "budget",
    "entitlement_unavailable",
    "authority_revoked",
    "deadline_exceeded",
    "signal",
    "control",
    "provider_unavailable",
    "uncertain",
    "completed",
    "invalid_result",
]
Count = Annotated[int, Field(ge=0, le=2147483647)]


class WorkerIdentity(Contract):
    worker_id: OpaqueId
    role: Literal["orchestrator", "sweeper"]
    interpreter_ids: Annotated[
        tuple[OpaqueId, ...], BeforeValidator(array), Field(min_length=1, max_length=16)
    ]


class AdvanceOutcome(Contract):
    execution_id: CanonicalUUID
    transitions: Count
    state: ExecutionState
    runnable: bool
    reason: Reason | None


class ProgressReport(Contract):
    claimed: Count
    transitions: Count
    deferred: Count


class SweepReport(Contract):
    timers: Count
    expired_waits: Count
    deadlines: Count
    lapsed_leases: Count
    expired_deliveries: Count
    # Amendment in Task 11, for the owner's acknowledgement: reserved attempts whose
    # reserved active time ran out before dispatch (Task 9), and attempts in flight
    # under an older recovery epoch after a restore.
    retired_reservations: Count = 0
    restored_attempts: Count = 0
    # Amendment in Task 13, for the owner's acknowledgement: attempts the sweeper
    # reserved through Task 5's scheduler. Nothing else drove `schedule_batch`, so no
    # attempt could reach a runtime outside tests.
    scheduled_attempts: Count = 0
    # E05 Task 5: human tasks escalated at half their timeout, and expired claims released.
    escalated_tasks: Count = 0
    released_claims: Count = 0
    # Amendment for RESTORE-1 (owner decision, 2026-10-28): executions admitted under
    # an older recovery epoch, held for reconciliation after a restore.
    restored_executions: Count = 0


class EngineObservation(Contract):
    execution_id: CanonicalUUID
    interpreter_id: OpaqueId
    state: ExecutionState
    requested_state: RequestedState
    runnable: bool
    reason: Reason | None


class InterpreterCheck(Contract):
    compatible: bool
    code: Literal["supported", "unsupported_interpreter"]


Sessions = Callable[[], object]


class ExecutionEngine(Protocol):
    interpreter_ids: frozenset[str]

    def progress(
        self, sessions: Sessions, worker: WorkerIdentity, *, limit: int
    ) -> ProgressReport: ...

    def sweep(self, sessions: Sessions, worker: WorkerIdentity, *, limit: int) -> SweepReport: ...

    def describe(self, db: object, execution_id: str) -> EngineObservation: ...

    def check_interpreter(self, interpreter_id: str) -> InterpreterCheck: ...


class Progression(Protocol):
    """Task 8. Pure transitions under the held execution row lock; no network call."""

    def advance_execution(
        self, db: object, worker: WorkerIdentity, execution: object, *, bound: int, epoch: int
    ) -> AdvanceOutcome: ...

    def apply_capacity(
        self, db: object, worker: WorkerIdentity, execution_id: str, *, epoch: int
    ) -> AdvanceOutcome: ...


class Sweeper(Protocol):
    """Task 11. Each due item is swept in its own transaction."""

    def sweep(self, sessions: Sessions, worker: WorkerIdentity, *, limit: int) -> SweepReport: ...


class RecoveryFence(Protocol):
    """Task 11. Current epoch from the independent RecoveryAuthority, never restored rows."""

    def current(self, db: object) -> SafeInt: ...
