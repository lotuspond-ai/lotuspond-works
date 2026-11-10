"""Current independent recovery authority. No cached allow or provider loader."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from time import monotonic, time_ns
from typing import Protocol

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import OpaqueId, SafeInt
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.orm import Session

from .catalog import InstalledCatalog


@dataclass(frozen=True)
class RecoverySnapshot:
    installation_id: str
    epoch: int
    active: bool

    def __post_init__(self):
        TypeAdapter(OpaqueId).validate_python(self.installation_id, strict=True)
        TypeAdapter(SafeInt).validate_python(self.epoch, strict=True)
        if type(self.active) is not bool:
            raise FleetError("authority_unavailable")


class RecoveryAuthority(Protocol):
    def current(self, installation_id: str) -> RecoverySnapshot: ...


class UnavailableRecoveryAuthority:
    def current(self, installation_id: str) -> RecoverySnapshot:
        raise FleetError("authority_unavailable")


def utc_now_ms() -> int:
    return time_ns() // 1_000_000


def database_now_ms(db: Session) -> int:
    """Wall time after locks; transaction-start now() must never decide expiry."""
    if db.get_bind().dialect.name != "postgresql":
        raise FleetError("authority_unavailable")
    return int(
        db.scalar(text("SELECT floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint"))
    )


class _LookupSlot:
    """At most one daemon lookup, including after a deadline or application shutdown.

    A concurrent caller waits for the slot, and its timeout bounds the wait and the
    lookup together. A stuck provider occupies its slot permanently, and every later
    call denies within its own bound. Daemon threads are never joined on shutdown.
    Each result belongs to exactly one call; a late response is discarded, never
    cached for another request.
    """

    def __init__(self):
        self.lock = Lock()
        self.closed = False

    def _wait(self, timeout):
        """Wait for a slot another lookup holds; True once this caller holds it."""
        return self.lock.acquire(timeout=timeout)

    def current(self, recovery, installation_id, timeout):
        if self.closed:
            raise FleetError("authority_unavailable")
        started = monotonic()
        if not (self.lock.acquire(blocking=False) or self._wait(timeout)):
            raise FleetError("authority_unavailable")
        remaining = timeout - (monotonic() - started)
        if self.closed or remaining <= 0:
            self.lock.release()
            raise FleetError("authority_unavailable")
        completed, result = Event(), []

        def run():
            try:
                value = recovery.current(installation_id)
                if type(value) is RecoverySnapshot:
                    result.append(value)
            except Exception:
                pass
            finally:
                completed.set()
                self.lock.release()

        try:
            Thread(target=run, name="fleet-recovery-lookup", daemon=True).start()
        except Exception:
            self.lock.release()
            raise FleetError("authority_unavailable") from None
        if not completed.wait(remaining) or self.closed or not result:
            raise FleetError("authority_unavailable")
        return result[0]

    def close(self):
        self.closed = True


@dataclass(frozen=True)
class FleetDependencies:
    recovery: RecoveryAuthority
    catalog: InstalledCatalog
    installation_id: str | None = None
    limiter: object | None = None
    database_clock: Callable[[Session], int] | None = None
    application_clock: Callable[[], int] | None = None
    authority_timeout_seconds: float = 5.0
    _slot: _LookupSlot = field(default_factory=_LookupSlot, repr=False, compare=False)

    def __post_init__(self):
        if not 0 < self.authority_timeout_seconds <= 5:
            raise ValueError("Invalid authority deadline")

    def current(self, installation_id: str, epoch: int | None = None) -> RecoverySnapshot:
        if self.installation_id is None or installation_id != self.installation_id:
            raise FleetError("authority_unavailable")
        snapshot = self._slot.current(
            self.recovery, installation_id, self.authority_timeout_seconds
        )
        # Reconstruct to revalidate even deliberately mutated dataclass instances.
        try:
            snapshot = RecoverySnapshot(snapshot.installation_id, snapshot.epoch, snapshot.active)
        except Exception:
            raise FleetError("authority_unavailable") from None
        if (
            not snapshot.active
            or snapshot.installation_id != installation_id
            or (epoch is not None and snapshot.epoch != epoch)
        ):
            raise FleetError("authority_unavailable")
        return snapshot

    def now(self, db: Session, *, scope, actor_id: str, resource_id: str) -> int:
        from .audit import ClockFenceEvent, queue_clock_fence

        clock = self.application_clock or utc_now_ms
        before = clock()
        value = (self.database_clock or database_now_ms)(db)
        after = clock()
        if type(value) is not int or not 0 <= value <= 9007199254740991:
            raise FleetError("authority_unavailable")
        if value < before - 30000 or value > after + 30000 or after < before:
            queue_clock_fence(db, ClockFenceEvent(scope, actor_id, resource_id))
            raise FleetError("authority_unavailable")
        return value

    def limit(self, key: str, limit: int):
        from fastapi import HTTPException

        if self.limiter is None:
            raise FleetError("authority_unavailable")
        try:
            self.limiter.check("fleet:" + key, limit)
        except HTTPException as error:
            raise FleetError(
                "rate_limited" if error.status_code == 429 else "authority_unavailable"
            ) from None

    def close(self):
        self._slot.close()
