"""Operator-started orchestrator or sweeper worker for durable execution (ADR-0011).

Starting a worker is an explicit operator action, never a side effect of importing
the API. Every worker connection carries statement, lock and idle-in-transaction
timeouts, so PostgreSQL ends and rolls back a stalled worker's transaction and
another worker can claim the execution. Roles and interpreter versions are
operator routing only; they grant no customer authority.
"""

import argparse
import logging
import os
import signal
import sys
import threading
from typing import Annotated, Literal

from lotuspond_runtime.protocol import OpaqueId
from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from .execution.engine_contracts import ProgressReport, SweepReport, WorkerIdentity
from .execution.errors import ExecutionError
from .schema_version import SCHEMA_VERSION

logger = logging.getLogger("lotuspond.execution")

TIMEOUT_SETTINGS = (
    ("statement_timeout", "statement_timeout_ms"),
    ("lock_timeout", "lock_timeout_ms"),
    ("idle_in_transaction_session_timeout", "idle_in_transaction_timeout_ms"),
)
# Initial engineering bound; configurable downward only.
Timeout = Annotated[int, Field(ge=100, le=15000)]
# libpq connection parameters an operator may set in the URL query. Host, port,
# database and user come from the URL itself; nothing else may reroute it.
URL_PARAMETERS = frozenset(
    {
        "options",
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
        "sslcrl",
        "sslcrldir",
        "sslsni",
        "channel_binding",
        "connect_timeout",
    }
)


class WorkerRefused(Exception):
    """Fixed startup refusal; never carries configuration or provider text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ExecutionWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOTUSPOND_EXECUTION_WORKER_", extra="ignore", hide_input_in_errors=True
    )
    database_url: SecretStr
    # Bounded so the session's application_name keeps the whole identifier.
    worker_id: Annotated[OpaqueId, Field(max_length=40)]
    role: Literal["orchestrator", "sweeper"]
    interpreter_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=16)
    batch_limit: int = Field(default=25, ge=1, le=100)
    poll_min_ms: int = Field(default=200, ge=50, le=2000)
    poll_max_ms: int = Field(default=2000, ge=50, le=2000)
    statement_timeout_ms: Timeout = 15000
    lock_timeout_ms: Timeout = 15000
    idle_in_transaction_timeout_ms: Timeout = 15000
    shutdown_seconds: float = Field(default=20.0, gt=0, le=60)

    @model_validator(mode="after")
    def bounded(self):
        if self.poll_min_ms > self.poll_max_ms:
            raise ValueError("Polling bounds are inverted")
        return self


def _database(settings: ExecutionWorkerSettings):
    try:
        url = make_url(settings.database_url.get_secret_value())
    except Exception:
        raise WorkerRefused("database_unsupported") from None
    if (
        url.drivername != "postgresql+psycopg"
        or set(url.query) - URL_PARAMETERS
        or any(type(value) is not str for value in url.query.values())
    ):
        raise WorkerRefused("database_unsupported")
    # Our timeouts follow any operator-supplied options, and are verified below.
    options = " ".join(
        [
            *([url.query["options"]] if "options" in url.query else []),
            *(f"-c {name}={getattr(settings, field)}" for name, field in TIMEOUT_SETTINGS),
        ]
    )
    parameters = {key: value for key, value in url.query.items() if key != "options"}
    return create_engine(
        url.set(query={}),
        connect_args={
            **parameters,
            "options": options,
            # Operator identifiers only, for observing and cleaning up owned sessions.
            "application_name": application_name(settings),
        },
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        hide_parameters=True,
    )


def application_name(settings):
    """At most 63 bytes: `lotuspond-<role>:` plus the whole bounded worker ID."""
    return f"lotuspond-{settings.role}:{settings.worker_id}"


def _verify_session(database, settings):
    try:
        with database.connect() as connection:
            versions = list(connection.scalars(text("SELECT version FROM schema_version")))
            if versions != [SCHEMA_VERSION]:
                raise WorkerRefused("schema_unavailable")
            for name, field in TIMEOUT_SETTINGS:
                value = connection.scalar(
                    text("SELECT setting::bigint FROM pg_settings WHERE name = :name"),
                    {"name": name},
                )
                if value != getattr(settings, field):
                    raise WorkerRefused("timeouts_unavailable")
    except WorkerRefused:
        raise
    except Exception:
        raise WorkerRefused("database_unavailable") from None


def production_engine(settings: ExecutionWorkerSettings):
    """Production wiring requires the Task 8 interpreter and Task 11 sweeper/fence."""
    raise WorkerRefused("engine_unavailable")


def _cycle(engine, sessions, worker, limit) -> bool:
    if worker.role == "orchestrator":
        report = ProgressReport.model_validate(engine.progress(sessions, worker, limit=limit))
        return report.claimed > 0
    report = SweepReport.model_validate(engine.sweep(sessions, worker, limit=limit))
    return sum(report.model_dump().values()) > 0


def run_execution_worker(settings: ExecutionWorkerSettings, *, engine=None, stop=None) -> None:
    """Run until `stop` is set. The first fenced cycle must succeed, or startup refuses."""
    if type(settings) is not ExecutionWorkerSettings:
        raise WorkerRefused("invalid_configuration")
    # Revalidate a deliberately mutated instance before any connection is made.
    settings = ExecutionWorkerSettings.model_validate(settings.model_dump())
    stop = stop or threading.Event()
    database = _database(settings)
    try:
        _verify_session(database, settings)
        if engine is None:
            engine = production_engine(settings)
        if not set(settings.interpreter_ids) <= set(engine.interpreter_ids):
            raise WorkerRefused("unsupported_interpreter")
        worker = WorkerIdentity(
            worker_id=settings.worker_id,
            role=settings.role,
            interpreter_ids=settings.interpreter_ids,
        )
        sessions = sessionmaker(database, expire_on_commit=False)
        try:
            busy = _cycle(engine, sessions, worker, settings.batch_limit)
        except ExecutionError as error:
            code = error.code if error.code == "authority_unavailable" else "engine_unavailable"
            raise WorkerRefused(code) from None
        except Exception:
            raise WorkerRefused("database_unavailable") from None
        delay = settings.poll_min_ms
        while not stop.is_set():
            if busy:
                delay = settings.poll_min_ms
            else:
                stop.wait(delay / 1000)
                delay = min(delay * 2, settings.poll_max_ms)
                if stop.is_set():
                    break
            try:
                busy = _cycle(engine, sessions, worker, settings.batch_limit)
            except Exception as error:
                # Fixed codes only: database and provider text never reach logs.
                code = error.code if isinstance(error, ExecutionError) else "unexpected"
                logger.warning("Execution worker cycle refused: %s", code)
                busy = False
    finally:
        database.dispose()


def serve(settings: ExecutionWorkerSettings, *, engine=None) -> int:
    """Install supervised shutdown, then run. A stuck shutdown exits after its bound."""
    stop = threading.Event()
    watchdog = threading.Timer(settings.shutdown_seconds, os._exit, args=(3,))
    watchdog.daemon = True

    def shutdown(*_):
        # The bound starts first. Event.set runs on a helper thread: the handler can
        # interrupt the main thread while it holds the Event's lock in Event.wait.
        if watchdog.is_alive() or watchdog.finished.is_set():
            return
        watchdog.start()
        threading.Thread(target=stop.set, name="execution-worker-stop", daemon=True).start()

    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    for signum in previous:
        signal.signal(signum, shutdown)
    try:
        run_execution_worker(settings, engine=engine, stop=stop)
    except WorkerRefused as error:
        print(f"Execution worker refused to start: {error.code}", file=sys.stderr)
        return 2
    finally:
        watchdog.cancel()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    print("Execution worker stopped.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="execution-worker",
        description="Run one durable execution orchestrator or sweeper worker.",
    )
    parser.add_argument("--role", choices=("orchestrator", "sweeper"), required=True)
    args = parser.parse_args(argv)
    try:
        settings = ExecutionWorkerSettings(role=args.role)
    except ValidationError:
        print("Execution worker refused to start: invalid_configuration", file=sys.stderr)
        return 2
    return serve(settings)


if __name__ == "__main__":
    sys.exit(main())
