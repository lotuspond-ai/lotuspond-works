"""Task 13 conformance: admission (spike checks 11–14, ENG-7)."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from driver import Refused
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.admission import request_identity
from lotuspond.models import AuditEvent, Execution, ExecutionDeploymentRevision, ExecutionRequest


def count(world, model, *where):
    with Session(world.engine) as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def test_concurrent_equal_admissions_make_one_execution(system):
    """Spike 11–13: 25 concurrent admissions with one key; a changed payload conflicts."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(customer, "waits_fast", dependencies=["waits.policy"])
    with ThreadPoolExecutor(25) as pool:
        views = list(
            pool.map(lambda _: operator.start(customer, revision, key="same-key"), range(25))
        )
    assert len({view["id"] for view in views}) == 1  # one execution, 24 originals returned
    assert count(w, Execution) == 1
    # One admission row for the key, and one audited start: the other 24 were replays.
    key = request_identity("same-key")
    assert count(w, ExecutionRequest, ExecutionRequest.request_key == key) == 1
    started = (AuditEvent.action == "executions.started", AuditEvent.resource_id == views[0]["id"])
    assert count(w, AuditEvent, *started) == 1
    with pytest.raises(Refused) as changed:
        operator.start(customer, revision, value=1, key="same-key")
    assert (changed.value.status, changed.value.code) == (409, "conflict")
    assert count(w, Execution) == 1


def test_an_equal_retry_after_a_new_revision_returns_the_original(system):
    """Spike 14 (replacement): a retry after revision 2 is enabled stays on revision 1."""
    w = system
    operator, customer = w.operator, w.customer
    first = operator.deploy(customer, "waits_fast", dependencies=["waits.policy"])
    original = operator.start(customer, first, key="retry-key")
    with Session(w.engine) as db:
        deployment_id = db.get(ExecutionDeploymentRevision, first).deployment_id
    second = operator.revise(customer, deployment_id, "waits_fast", dependencies=["waits.policy"])
    assert second != first
    again = operator.start(customer, first, key="retry-key")
    assert again["id"] == original["id"]
    with Session(w.engine) as db:
        assert db.get(Execution, original["id"]).deployment_revision_id == first
    fresh = operator.start(customer, second)
    with Session(w.engine) as db:
        assert db.get(Execution, fresh["id"]).deployment_revision_id == second


def test_an_unregistered_interpreter_is_refused_and_never_claimed(system):
    """ENG-7: no admission service, worker or claim for an interpreter this build lacks."""
    from lotuspond.execution.admission import AdmissionService

    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    graph = w.graph()
    with pytest.raises(ValueError):
        AdmissionService(
            budgets=graph.budgets,
            artifacts=graph.store,
            artifact_policy=graph.policy,
            interpreter_id="lotuspond.execution-interpreter.v9",
        )
    # A worker asked to serve it refuses to start, and claims nothing.
    refused = processes.start_worker(
        "orchestrator",
        "orchestrator-v9",
        interpreters=["lotuspond.execution-interpreter.v9"],
        wait=False,
    )
    assert refused.wait(timeout=30) == 2
    log = (w.root / "logs/orchestrator-v9.stderr").read_text()
    assert "unsupported_interpreter" in log
    # Integrity case (labelled): a stored pin this build does not register. Live
    # workers never claim it, and a new worker refuses to start over it.
    revision = operator.deploy(customer, "waits_fast", dependencies=["waits.policy"])
    execution_id = operator.start(customer, revision)["id"]
    twin = operator.start(customer, revision)["id"]  # the same work on the registered pin
    operator.open_wait(customer, execution_id, "pause")
    operator.open_wait(customer, twin, "pause")
    with Session(w.engine) as db, db.begin():
        db.get(Execution, execution_id).interpreter_id = "lotuspond.execution-interpreter.v9"
    # Positive control: the live orchestrator advances the twin past its timer...
    operator.open_wait(customer, twin, "callback", seconds=60)
    # ...and never claims the execution it cannot interpret.
    paths = {item["occurrence_path"] for item in operator.waits(customer, execution_id)}
    assert "callback" not in paths, paths
    blocked = processes.start_worker("orchestrator", "orchestrator-new", wait=False)
    assert blocked.wait(timeout=30) == 2
    assert "unsupported_interpreter" in (w.root / "logs/orchestrator-new.stderr").read_text()
