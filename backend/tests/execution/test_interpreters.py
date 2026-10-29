"""Task 11: interpreter versions are registered, pinned and never claimed otherwise."""

import threading

import pytest
from lotuspond_sdk.executable.metadata import COMPILER_REVISION
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution import interpreters, program
from lotuspond.execution.admission import AdmissionService
from lotuspond.execution.engine import PostgresExecutionEngine
from lotuspond.execution.engine_contracts import INTERPRETER_V1
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.interpreters import (
    COMPILERS,
    REGISTERED,
    require_interpreter,
    unsupported_pins,
)
from lotuspond.execution.program import ProgramError, load_program
from lotuspond.execution_worker import WorkerRefused, run_execution_worker
from lotuspond.models import Execution, SchemaVersion

from .engine_fixtures import FixtureFence, FixtureProgression, FixtureSweeper, fixture_engine
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_execution_worker import settings_for
from .test_progression import flows as flows
from .test_publication import harness as harness

V9 = "lotuspond.execution-interpreter.v9"


def test_unsupported_interpreter_is_never_admitted_or_claimed(flows):
    h = flows
    assert REGISTERED == {INTERPRETER_V1}
    assert require_interpreter(INTERPRETER_V1) == INTERPRETER_V1
    for other in (V9, None, 1, "bad id"):
        with pytest.raises(ExecutionError, match="unsupported_interpreter"):
            require_interpreter(other)
    # Admission cannot be built to pin a version this build does not register.
    with pytest.raises(ValueError):
        AdmissionService(
            budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy, interpreter_id=V9
        )
    # Nor can an engine be built to claim one.
    with pytest.raises(ValueError):
        PostgresExecutionEngine(
            FixtureProgression(),
            FixtureSweeper(),
            FixtureFence(),
            interpreter_ids=[V9],
            test_mode=True,
        )
    # Positive control: the registered version is admitted and runs.
    done = h.start("scoped", [1, 2, 3], key="registered")
    h.drive()
    # A row pinning an unregistered version (written outside admission, as after a
    # downgrade) is never claimed, and the build reports the pin.
    pinned = h.start("scoped", [1, 2, 3], key="pinned")
    with Session(h.engine) as db, db.begin():
        db.get(Execution, pinned, with_for_update=True).interpreter_id = V9
    reports = h.drive()
    assert reports[0].claimed == 0
    with Session(h.engine) as db:
        assert db.get(Execution, done).state == "succeeded"
        row = db.get(Execution, pinned)
        assert row.state == "pending" and row.runnable_at_ms is not None  # still due
        assert unsupported_pins(db) == (V9,)
    # A build lacking a version that a non-terminal execution pins never starts.
    with Session(h.engine) as db, db.begin():
        if db.scalar(select(SchemaVersion.version)) is None:
            db.add(SchemaVersion(version=15))
    # Were it to start, it would stop after 5 s, and the test would fail, not hang.
    started = threading.Event()
    timer = threading.Timer(5, started.set)
    timer.start()
    try:
        with pytest.raises(WorkerRefused) as refused:
            run_execution_worker(settings_for(h), engine=fixture_engine(), stop=started)
    finally:
        timer.cancel()
    assert refused.value.code == "unsupported_interpreter"
    # Once nothing non-terminal pins it, the same build starts.
    with Session(h.engine) as db, db.begin():
        db.get(Execution, pinned, with_for_update=True).interpreter_id = INTERPRETER_V1
    stop = threading.Event()
    stop.set()
    run_execution_worker(settings_for(h), engine=fixture_engine(), stop=stop)


def test_each_version_pins_the_compiler_whose_plans_it_interprets(flows, monkeypatch):
    h = flows
    # v1 interprets plans of this SDK's compiler; a version is registered only then.
    assert COMPILERS[INTERPRETER_V1] == COMPILER_REVISION
    assert REGISTERED == {v for v, c in COMPILERS.items() if c == COMPILER_REVISION}
    execution_id = h.start("scoped", [1, 2, 3], key="compiled")
    with Session(h.engine) as db:
        assert unsupported_pins(db) == ()
        row = db.get(Execution, execution_id)
        scope = row.workspace_id, row.project_id, row.environment_id
    # A build whose v1 would pin another compiler: the execution's plan was compiled
    # by this one, so it is reported, the worker would refuse to start, and the
    # pinned program no longer loads (and is fenced, never reinterpreted).
    other = {INTERPRETER_V1: "lotuspond.workflow-compiler.v9"}
    monkeypatch.setattr(interpreters, "COMPILERS", other)
    monkeypatch.setattr(program, "COMPILERS", other)
    with Session(h.engine) as db:
        assert unsupported_pins(db) == (f"compiler:{COMPILER_REVISION}",)
        from lotuspond_runtime.protocol import Scope

        with pytest.raises(ProgramError, match="integrity"):
            load_program(
                db,
                Scope(workspace_id=scope[0], project_id=scope[1], environment_id=scope[2]),
                db.get(Execution, execution_id),
            )
