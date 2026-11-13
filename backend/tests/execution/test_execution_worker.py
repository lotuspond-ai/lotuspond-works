"""The worker command: unsafe startup refusal, role timeouts and supervised shutdown."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from lotuspond import execution_worker
from lotuspond.execution.engine_contracts import INTERPRETER_V1, WorkerIdentity
from lotuspond.execution_worker import (
    ExecutionWorkerSettings,
    WorkerRefused,
    _verify_session,
    main,
    run_execution_worker,
)
from lotuspond.models import Execution, ExecutionOutbox, SchemaVersion
from lotuspond.schema_version import SCHEMA_VERSION

from .conftest import execution_test_url
from .engine_fixtures import FixtureFence, FixtureProgression, fixture_engine
from .test_admission import admission as admission
from .test_admission import admit
from .test_budgets import budget_harness as budget_harness
from .test_publication import harness as harness

BACKEND = Path(__file__).parents[2]
SECOND = WorkerIdentity(worker_id="second", role="orchestrator", interpreter_ids=[INTERPRETER_V1])


def scoped_url(h, options=""):
    with Session(h.engine) as db:
        schema = db.scalar(text("SELECT current_schema()"))
    url = make_url(execution_test_url()).set(
        query={"options": f"-c search_path={schema} {options}".strip()}
    )
    return url.render_as_string(hide_password=False)


@pytest.fixture
def versioned(admission):
    """A harness schema built from the model carries no ledger row; installs do."""
    with Session(admission.engine) as db, db.begin():
        if db.scalar(select(SchemaVersion.version)) is None:
            db.add(SchemaVersion(version=SCHEMA_VERSION))
    return admission


def settings_for(h, **changes):
    values = dict(
        database_url=scoped_url(h),
        worker_id="worker-test",
        role="orchestrator",
        interpreter_ids=[INTERPRETER_V1],
    )
    return ExecutionWorkerSettings(**(values | changes))


def backends(h, worker_id, role="orchestrator"):
    with Session(h.engine) as db:
        return list(
            db.execute(
                text(
                    "SELECT pid, state FROM pg_stat_activity WHERE application_name = :name "
                    "AND datname = current_database()"
                ),
                {"name": f"lotuspond-{role}:{worker_id}"},
            )
        )


def wait_for(condition, seconds, interval=0.05):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        value = condition()
        if value:
            return value
        sleep(interval)
    return condition()


class Driver:
    """A separately killable worker process running the real `serve` loop."""

    def __init__(self, tmp_path, name, behavior, settings):
        self.state = tmp_path / name
        self.state.mkdir()
        values = settings.model_dump(mode="json")
        values["database_url"] = settings.database_url.get_secret_value()
        (self.state / "settings.json").write_text(json.dumps(values))
        self.stdout = open(self.state / "stdout.txt", "wb")
        self.stderr = open(self.state / "stderr.txt", "wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tests.execution.engine_fixtures", behavior, str(self.state)],
            cwd=BACKEND,
            stdout=self.stdout,
            stderr=self.stderr,
            start_new_session=True,
        )

    def holding(self):
        path = self.state / "holding.json"
        return json.loads(path.read_text()) if path.exists() else None

    def output(self):
        return (self.state / "stderr.txt").read_text()

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGCONT)
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=30)
        self.stdout.close()
        self.stderr.close()
        print(
            f"Owned worker process cleanup observed: {self.process.pid} {self.process.returncode}"
        )


def test_worker_refuses_unsafe_startup(versioned, monkeypatch, capsys):
    h = versioned
    for name in list(os.environ):
        if name.startswith("LOTUSPOND_EXECUTION_WORKER_"):
            monkeypatch.delenv(name)
    for argv in ([], ["--role", "admin"]):
        with pytest.raises(SystemExit) as exited:
            main(argv)
        assert exited.value.code == 2
    assert main(["--role", "orchestrator"]) == 2
    assert "invalid_configuration" in capsys.readouterr().err
    for invalid in (
        dict(statement_timeout_ms=15001),
        dict(idle_in_transaction_timeout_ms=0),
        dict(interpreter_ids=[]),
        dict(poll_min_ms=2000, poll_max_ms=200),
        dict(worker_id="bad id"),
        dict(worker_id="w" * 41),
    ):
        with pytest.raises(ValidationError):
            settings_for(h, **invalid)
    cases = [
        (
            settings_for(h, database_url="sqlite:///unused.db"),
            fixture_engine(),
            "database_unsupported",
        ),
        (
            settings_for(h, database_url=scoped_url(h) + "&host=external.invalid"),
            fixture_engine(),
            "database_unsupported",
        ),
        (
            settings_for(h, database_url=scoped_url(h) + "&options=-c%20statement_timeout%3D0"),
            fixture_engine(),
            "database_unsupported",
        ),
        (
            settings_for(h, interpreter_ids=["lotuspond.execution-interpreter.v9"]),
            fixture_engine(),
            "unsupported_interpreter",
        ),
        (settings_for(h), fixture_engine(fence=FixtureFence(epoch=None)), "authority_unavailable"),
        # Production wiring needs the Task 8 interpreter and Task 11 sweeper and fence.
        (settings_for(h), None, "engine_unavailable"),
    ]
    for settings, engine, code in cases:
        with pytest.raises(WorkerRefused) as refused:
            run_execution_worker(settings, engine=engine)
        assert refused.value.code == code
    with pytest.raises(WorkerRefused) as refused:
        run_execution_worker(object())
    assert refused.value.code == "invalid_configuration"
    # A database that is not at the current schema version is refused before work.
    with Session(h.engine) as db, db.begin():
        db.execute(SchemaVersion.__table__.update().values(version=SCHEMA_VERSION - 1))
    with pytest.raises(WorkerRefused) as refused:
        run_execution_worker(settings_for(h), engine=fixture_engine())
    assert refused.value.code == "schema_unavailable"
    with Session(h.engine) as db, db.begin():
        db.execute(SchemaVersion.__table__.update().values(version=SCHEMA_VERSION))
    url = scoped_url(h)
    monkeypatch.setenv("LOTUSPOND_EXECUTION_WORKER_DATABASE_URL", url)
    monkeypatch.setenv("LOTUSPOND_EXECUTION_WORKER_WORKER_ID", "configured")
    monkeypatch.setenv("LOTUSPOND_EXECUTION_WORKER_INTERPRETER_IDS", json.dumps([INTERPRETER_V1]))
    assert main(["--role", "sweeper"]) == 2
    error = capsys.readouterr().err
    assert error == "Execution worker refused to start: engine_unavailable\n"
    assert wait_for(lambda: backends(h, "configured", "sweeper") == [], 10)


def test_worker_sessions_carry_bounded_role_timeouts(versioned):
    h = versioned
    settings = settings_for(
        h,
        statement_timeout_ms=1500,
        lock_timeout_ms=1200,
        idle_in_transaction_timeout_ms=1100,
    )
    # TLS and connect-timeout parameters are accepted; they cannot reroute the URL.
    tls = settings_for(h, database_url=scoped_url(h) + "&sslmode=disable&connect_timeout=5")
    database = execution_worker._database(tls)
    try:
        _verify_session(database, tls)
    finally:
        database.dispose()
    # Operator options cannot weaken the configured timeouts: ours follow and are verified.
    weakened = settings.model_copy(
        update={
            "database_url": settings_for(
                h, database_url=scoped_url(h, "-c statement_timeout=0")
            ).database_url
        }
    )
    for candidate in (settings, weakened):
        database = execution_worker._database(candidate)
        try:
            _verify_session(database, candidate)
            with database.connect() as connection:
                assert connection.scalar(text("SHOW statement_timeout")) == "1500ms"
                assert connection.scalar(text("SHOW lock_timeout")) == "1200ms"
                assert (
                    connection.scalar(text("SHOW idle_in_transaction_session_timeout")) == "1100ms"
                )
        finally:
            database.dispose()
    # Negative control: a session without the worker options is refused.
    plain = create_engine(scoped_url(h), hide_parameters=True)
    try:
        with pytest.raises(WorkerRefused) as refused:
            _verify_session(plain, settings)
        assert refused.value.code == "timeouts_unavailable"
    finally:
        plain.dispose()


def test_stalled_worker_is_ended_by_role_timeouts(versioned, tmp_path):
    h = versioned
    execution_id = admit(h).id
    settings = settings_for(h, worker_id="stalled", idle_in_transaction_timeout_ms=5000)
    driver = Driver(tmp_path, "stalled", "stall", settings)
    try:
        holding = wait_for(driver.holding, 60)
        assert holding == {"execution_id": execution_id}, driver.output()
        os.killpg(driver.process.pid, signal.SIGSTOP)
        stopped_at = monotonic()
        assert "idle in transaction" in [state for _, state in backends(h, "stalled")]
        progression = FixtureProgression()
        engine = fixture_engine(progression)
        sessions = sessionmaker(h.engine, expire_on_commit=False)
        # Positive control: while the stalled transaction holds the row, nobody claims it.
        assert engine.progress(sessions, SECOND, limit=1).claimed == 0
        report = wait_for(lambda: engine.progress(sessions, SECOND, limit=1).claimed, 30, 0.1)
        claimed_after = monotonic() - stopped_at
        print({"stalled_worker_claimed_after_seconds": round(claimed_after, 3)})
        assert report == 1
        assert progression.calls[0]["execution_id"] == execution_id
        assert claimed_after < 30
        # PostgreSQL ended the stalled session; only an idle pooled connection may remain.
        assert "idle in transaction" not in [state for _, state in backends(h, "stalled")]
        with Session(h.engine) as db:
            start = db.scalar(
                select(ExecutionOutbox).where(ExecutionOutbox.execution_id == execution_id)
            )
            assert (start.status, start.delivery_count) == ("delivered", 1)
            assert db.get(Execution, execution_id).runnable_at_ms is None
        assert driver.process.poll() is None  # still stopped, not exited
    finally:
        driver.close()
    assert wait_for(lambda: backends(h, "stalled") == [], 10)


def test_shutdown_stops_owned_workers(versioned, tmp_path):
    h = versioned
    idle = Driver(tmp_path, "idle", "idle", settings_for(h, worker_id="idle"))
    try:
        assert wait_for(lambda: backends(h, "idle"), 60), idle.output()  # positive control
        os.killpg(idle.process.pid, signal.SIGTERM)
        assert idle.process.wait(timeout=15) == 0
        assert "Execution worker stopped." in idle.output()
        assert wait_for(lambda: backends(h, "idle") == [], 10)
    finally:
        idle.close()
    execution_id = admit(h).id
    stuck = Driver(
        tmp_path,
        "stuck",
        "stall",
        settings_for(h, worker_id="stuck", shutdown_seconds=2.0),
    )
    try:
        assert wait_for(stuck.holding, 60) == {"execution_id": execution_id}, stuck.output()
        signalled = monotonic()
        os.killpg(stuck.process.pid, signal.SIGTERM)
        # The supervised bound ends a worker that cannot finish its transaction.
        assert stuck.process.wait(timeout=15) == 3
        bounded_exit = monotonic() - signalled
        print({"stuck_worker_bounded_exit_seconds": round(bounded_exit, 3)})
        assert 1.5 <= bounded_exit < 15
        assert wait_for(lambda: backends(h, "stuck") == [], 10)
    finally:
        stuck.close()
    with Session(h.engine) as db:
        start = db.scalar(
            select(ExecutionOutbox).where(ExecutionOutbox.execution_id == execution_id)
        )
        assert start.status == "pending"  # the killed transaction rolled back
        assert db.get(Execution, execution_id).runnable_at_ms is not None


def test_in_process_serve_returns_without_a_lingering_watchdog(versioned):
    """A clean stop cancels the shutdown bound; an embedding process is not killed."""
    import threading

    from lotuspond.execution_worker import serve

    h = versioned
    settings = settings_for(h, worker_id="embedded", shutdown_seconds=1.0)
    stopper = threading.Timer(1.0, os.kill, args=(os.getpid(), signal.SIGTERM))
    stopper.start()
    try:
        assert serve(settings, engine=fixture_engine()) == 0
    finally:
        # Never let a late SIGTERM reach pytest after the handlers are restored.
        stopper.cancel()
    sleep(2.0)  # past the bound: still alive here means the watchdog was cancelled
    assert wait_for(lambda: backends(h, "embedded") == [], 10)
