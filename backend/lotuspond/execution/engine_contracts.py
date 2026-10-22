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
        self, db: object, worker: WorkerIdentity, execution: object, *, bound: int
    ) -> AdvanceOutcome: ...

    def apply_capacity(
        self, db: object, worker: WorkerIdentity, execution_id: str
    ) -> AdvanceOutcome: ...


class Sweeper(Protocol):
    """Task 11. Each due item is swept in its own transaction."""

    def sweep(self, sessions: Sessions, worker: WorkerIdentity, *, limit: int) -> SweepReport: ...


class RecoveryFence(Protocol):
    """Task 11. Current epoch from the independent RecoveryAuthority, never restored rows."""

    def current(self, db: object) -> SafeInt: ...
