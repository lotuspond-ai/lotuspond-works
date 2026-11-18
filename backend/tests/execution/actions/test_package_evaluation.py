"""Package evaluation runs: the real package per scenario, nothing ever approved or sent."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.package_evaluation import PackageEvaluations, matches, package_subject
from lotuspond.models import (
    ExecutablePublication,
    Execution,
    ExecutionActionCommand,
    ExecutionDeploymentRevision,
    Scenario,
)

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .test_tasks import tasks as tasks  # noqa: F401


def test_expected_values_match_as_subsets():
    observed = {"state": "review", "proposal": {"operation": "submit", "input": {"a": 1, "b": 2}}}
    assert matches({"state": "review"}, observed)
    assert matches({"proposal": {"input": {"a": 1}}}, observed)
    assert not matches({"proposal": {"input": {"a": 2}}}, observed)
    assert not matches({"result": {"status": "x"}}, observed)
    assert matches({}, observed)


def evaluations(h):
    return PackageEvaluations(admission=h.service, controls=h.controls, artifacts=h.store)


def scenario(h, name, expected, critical=True):
    with Session(h.engine) as db, db.begin():
        row = Scenario(
            workspace_id=h.scope.workspace_id,
            project_id=h.scope.project_id,
            name=name,
            input=None,  # the reviewed-action package takes no input
            expected=expected,
            critical=critical,
        )
        db.add(row)
        db.flush()
        return row.id


def drive(h, service, run_id, steps=20):
    """Advance until completed, letting the engine run between steps."""
    view = None
    for _ in range(steps):
        with Session(h.engine) as db, db.begin():
            view = service.advance(db, h.actor, run_id)
        if view["status"] == "completed":
            return view
        h.run2()
    return view


def deployment_of(h, key):
    first = h.reviewed(key, decide=False)
    with Session(h.engine) as db:
        revision_id = db.get(Execution, first).deployment_revision_id
        revision = db.get(ExecutionDeploymentRevision, revision_id)
        return revision_id, db.get(ExecutablePublication, revision.publication_id).package_sha256


def start(h, service, revision_id, scenario_ids):
    with Session(h.engine) as db, db.begin():
        return service.start(
            db,
            h.actor,
            {
                "deployment_revision_id": revision_id,
                "name": "candidate",
                "scenario_ids": scenario_ids,
            },
        )


def test_a_package_evaluation_records_each_proposal_and_sends_nothing(actions):
    h = actions
    revision_id, package_sha256 = deployment_of(h, "evaluate")
    right = scenario(
        h, "proposes the write", {"state": "review", "proposal": {"operation": "submit"}}
    )
    wrong = scenario(h, "expects another write", {"proposal": {"operation": "delete"}}, False)
    service = evaluations(h)
    run = start(h, service, revision_id, [right, wrong])
    assert run["revision"] == f"{package_subject(package_sha256)}@{revision_id}"
    assert (run["status"], run["results"]) == ("running", [])
    view = drive(h, service, run["id"])
    assert view["status"] == "completed", view
    results = {row["scenario_id"]: row for row in view["results"]}
    assert (results[right]["passed"], results[wrong]["passed"]) == (True, False)
    assert results[right]["output"]["proposal"]["operation"] == "submit"
    h.run2()
    with Session(h.engine) as db:
        executions = [db.get(Execution, row["output"]["execution_id"]) for row in view["results"]]
        # Each was cancelled at its review: an evaluation never approves an action.
        assert {e.requested_state for e in executions} == {"cancelled"}
        assert (
            db.scalar(
                select(ExecutionActionCommand).where(
                    ExecutionActionCommand.execution_id.in_([e.id for e in executions])
                )
            )
            is None
        )
    with Session(h.engine) as db, db.begin():
        again = service.advance(db, h.actor, run["id"])
    assert again["results"] == view["results"]  # advancing a completed run changes nothing


def test_one_step_per_advance_until_every_scenario_has_a_result(actions):
    h = actions
    revision_id, _ = deployment_of(h, "stepwise")
    ids = [scenario(h, f"s{i}", {"state": "review"}) for i in range(3)]
    service = evaluations(h)
    run = start(h, service, revision_id, ids)
    # Three admissions, one per call, before any result.
    for _ in range(3):
        with Session(h.engine) as db, db.begin():
            view = service.advance(db, h.actor, run["id"])
        assert view["results"] == []
    view = drive(h, service, run["id"])
    assert view["status"] == "completed"
    assert len(view["results"]) == 3 and all(r["passed"] for r in view["results"])


def test_a_package_evaluation_records_a_form_and_submits_nothing(actions):
    """A package that stops at a person's form is observed as asking one, then cancelled;
    the evaluation never submits it (late purchase order and invoice triage both end
    some scenarios at a clerk's form)."""
    from .test_forms_v2 import form_package, form_task

    h = actions
    package, documents = form_package("owners")
    first = h.start2(package, "form-subject", documents)
    with Session(h.engine) as db:
        revision_id = db.get(Execution, first).deployment_revision_id
    asks = scenario(h, "asks a clerk", {"state": "form"})
    finishes = scenario(h, "expects a result", {"state": "succeeded"}, False)
    service = evaluations(h)
    run = start(h, service, revision_id, [asks, finishes])
    view = drive(h, service, run["id"])
    assert view["status"] == "completed", view
    results = {row["scenario_id"]: row for row in view["results"]}
    assert (results[asks]["passed"], results[finishes]["passed"]) == (True, False)
    assert results[asks]["output"]["state"] == "form"
    h.run2()
    with Session(h.engine) as db:
        for row in view["results"]:
            execution = db.get(Execution, row["output"]["execution_id"])
            assert execution.requested_state == "cancelled"
            task = form_task(h, execution.id)
            assert task.kind == "form" and task.state != "submitted"
