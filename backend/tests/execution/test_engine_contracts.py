"""The orchestration adapter is inert, strict and the only engine behind its protocol."""

import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from lotuspond.execution.engine import PostgresExecutionEngine
from lotuspond.execution.engine_contracts import (
    INTERPRETER_V1,
    AdvanceOutcome,
    EngineObservation,
    ExecutionEngine,
    InterpreterCheck,
    ProgressReport,
    SweepReport,
    WorkerIdentity,
)
from lotuspond.execution.errors import ExecutionError

from .engine_fixtures import FixtureFence, FixtureProgression, FixtureSweeper, fixture_engine

BACKEND = Path(__file__).parents[2]
UUID = "00000000-0000-4000-8000-000000000001"


def imported_after(statement):
    code = f"import sys\n{statement}\nprint('\\n'.join(sorted(sys.modules)))\n"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split())


def test_engine_contracts_are_inert():
    modules = imported_after("import lotuspond.execution.engine_contracts")
    assert "lotuspond.execution.engine_contracts" in modules  # positive control
    forbidden = {
        "sqlalchemy.orm",
        "psycopg",
        "httpx",
        "lotuspond.models",
        "lotuspond.database",
        "lotuspond.execution_worker",
        "lotuspond.execution.engine",
        "lotuspond.execution.orchestrator",
        "temporalio",
    }
    assert not modules & forbidden


def test_api_import_starts_and_imports_no_worker():
    modules = imported_after("import lotuspond.app")
    assert "lotuspond.app" in modules
    assert "lotuspond.execution_worker" not in modules
    assert not any(name.startswith("temporalio") for name in modules)


def test_backend_services_never_import_worker_modules():
    package = BACKEND / "lotuspond"
    importers = sorted(
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if path.name != "execution_worker.py" and "execution_worker" in path.read_text()
    )
    assert importers == []
    assert "temporalio" not in (BACKEND / "pyproject.toml").read_text()


def test_contract_types_refuse_payloads_coercion_and_routing_choices():
    worker = WorkerIdentity(worker_id="w-1", role="orchestrator", interpreter_ids=[INTERPRETER_V1])
    assert worker.interpreter_ids == (INTERPRETER_V1,)
    invalid = [
        dict(worker_id="w-1", role="admin", interpreter_ids=[INTERPRETER_V1]),
        dict(worker_id="w-1", role="orchestrator", interpreter_ids=[]),
        dict(worker_id="w-1", role="orchestrator", interpreter_ids=INTERPRETER_V1),
        dict(worker_id="w 1", role="orchestrator", interpreter_ids=[INTERPRETER_V1]),
        dict(worker_id="w-1", role="orchestrator", interpreter_ids=[INTERPRETER_V1], queue="x"),
    ]
    for raw in invalid:
        with pytest.raises((ValidationError, ExecutionError)):
            WorkerIdentity.model_validate(raw)
    outcome = dict(execution_id=UUID, transitions=1, state="running", runnable=True, reason=None)
    assert AdvanceOutcome.model_validate(outcome).runnable is True
    for change in (
        {"state": "done"},
        {"transitions": -1},
        {"transitions": True},
        {"reason": "PRIVATE"},
        {"payload": "PRIVATE"},
        {"execution_id": "not-a-uuid"},
    ):
        with pytest.raises(ValidationError):
            AdvanceOutcome.model_validate(outcome | change)
    for model, fields in (
        (ProgressReport, dict(claimed=0, transitions=0, deferred=0)),
        (
            SweepReport,
            dict(timers=0, expired_waits=0, deadlines=0, lapsed_leases=0, expired_deliveries=0),
        ),
        (InterpreterCheck, dict(compatible=False, code="unsupported_interpreter")),
        (
            EngineObservation,
            dict(
                execution_id=UUID,
                interpreter_id=INTERPRETER_V1,
                state="waiting",
                requested_state="running",
                runnable=False,
                reason=None,
            ),
        ),
    ):
        model.model_validate(fields)
        with pytest.raises(ValidationError):
            model.model_validate(fields | {"customer": "PRIVATE"})


def test_postgres_engine_is_the_registered_protocol_implementation():
    engine = fixture_engine()
    for name, member in inspect.getmembers(ExecutionEngine, inspect.isfunction):
        if not name.startswith("_"):
            assert inspect.signature(getattr(PostgresExecutionEngine, name)).parameters.keys() == (
                inspect.signature(member).parameters.keys()
            )
    assert engine.interpreter_ids == frozenset({INTERPRETER_V1})
    assert engine.check_interpreter(INTERPRETER_V1) == InterpreterCheck(
        compatible=True, code="supported"
    )
    for other in ("lotuspond.execution-interpreter.v2", None, 1):
        assert engine.check_interpreter(other) == InterpreterCheck(
            compatible=False, code="unsupported_interpreter"
        )


def test_engine_refuses_fixtures_missing_dependencies_and_unknown_routing():
    with pytest.raises(ValueError, match="test mode"):
        PostgresExecutionEngine(
            FixtureProgression(), FixtureSweeper(), FixtureFence(), interpreter_ids=[INTERPRETER_V1]
        )
    for interpreter_ids in ((), INTERPRETER_V1, ("bad id",)):
        with pytest.raises((ValueError, ValidationError)):
            PostgresExecutionEngine(
                FixtureProgression(),
                FixtureSweeper(),
                FixtureFence(),
                interpreter_ids=interpreter_ids,
                test_mode=True,
            )
    with pytest.raises(ValueError, match="required"):
        PostgresExecutionEngine(
            None, FixtureSweeper(), FixtureFence(), interpreter_ids=[INTERPRETER_V1], test_mode=True
        )
    engine = fixture_engine()
    sweeper = WorkerIdentity(worker_id="w", role="sweeper", interpreter_ids=[INTERPRETER_V1])
    other = WorkerIdentity(
        worker_id="w", role="orchestrator", interpreter_ids=["lotuspond.execution-interpreter.v2"]
    )
    for worker in (sweeper, other):
        with pytest.raises(ExecutionError, match="forbidden"):
            engine.progress(lambda: None, worker, limit=1)
    orchestrator = WorkerIdentity(
        worker_id="w", role="orchestrator", interpreter_ids=[INTERPRETER_V1]
    )
    with pytest.raises(ExecutionError, match="forbidden"):
        engine.sweep(lambda: None, orchestrator, limit=1)
    for limit in (0, 101, True, 1.0):
        with pytest.raises(ExecutionError, match="invalid_request"):
            engine.progress(lambda: None, orchestrator, limit=limit)
