"""Evaluation deployments: a package is proven before release, with release decisions
required, and its evaluation deployment can never run anyone's work or write.

docs/superpowers/specs/2026-11-17-evaluation-deployments-design.md
"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import DecisionRequest
from lotuspond.execution.contracts import (
    AdmissionRequest,
    DeploymentConfig,
    canonical_configuration,
)
from lotuspond.execution.deployments import create_deployment
from lotuspond.execution.errors import ExecutionError
from lotuspond.models import ExecutionActionDecision, ExecutionDeploymentRevision

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .test_admission import bind, doc, enable, publish
from .test_package_evaluation import drive, evaluations, scenario, start
from .test_tasks import review_package
from .test_tasks import tasks as tasks  # noqa: F401


def request_for(h, db, publication, documents, package, *, purpose=None, release_id=None):
    from tests.execution.test_deployments import within

    request = within(bind(h, db, publication, documents), package)
    configuration = request.configuration.model_copy(update={"purpose": purpose})
    return request.model_copy(update={"configuration": configuration, "release_id": release_id})


def reviewed_package():
    """Intent, review, dispatch and verify: every write behind a person's review."""
    package, documents = review_package()
    package["body"]["steps"] = doc("reviewed_action")["body"]["steps"]
    return package, documents


def deploy(h, key, *, purpose="evaluation", release_id=None, package=None, documents=None):
    if package is None:
        package, documents = reviewed_package()
    cached = getattr(h, "connection_request", None), getattr(h, "connection_versions", None)
    try:
        with Session(h.engine) as db, db.begin():
            publication = publish(h, db, package, documents, key)
            request = request_for(
                h, db, publication, documents, package, purpose=purpose, release_id=release_id
            )
            view = create_deployment(db, h.actor, request, key, dependencies=h.dependencies)
            enable(h, db, view, f"{key}-on")
    except Exception:
        # The refused transaction rolled back the connection rows `bind` cached.
        h.connection_request, h.connection_versions = cached
        raise
    return view.revision_id


def refused(code, call):
    with pytest.raises(ExecutionError) as caught:
        call()
    assert caught.value.code == code


def test_a_configuration_without_a_purpose_keeps_its_bytes():
    """Every stored revision predates the field; its canonical bytes and digest stand."""
    raw = {
        "bindings": [],
        "limits": {
            "max_steps": 1,
            "active_seconds": 1,
            "elapsed_seconds": 1,
            "retained_waits": 0,
            "max_input_tokens": 0,
            "max_output_tokens": 0,
            "max_cost_microusd": 0,
            "active_capacity": 1,
            "pending_capacity": 1,
        },
    }
    assert b"purpose" not in canonical_configuration(raw)
    evaluation = canonical_configuration({**raw, "purpose": "evaluation"})
    assert b'"purpose":"evaluation"' in evaluation
    assert DeploymentConfig.model_validate_json(evaluation).evaluation
    with pytest.raises(Exception):
        DeploymentConfig.model_validate({**raw, "purpose": "production"})


def test_release_required_admits_only_an_evaluation_deployment_without_a_release(actions):
    h = actions
    h.authority.snapshot["release_required"] = True
    # Production: refused without a release, as before.
    refused("assurance_unavailable", lambda: deploy(h, "production", purpose=None))
    revision_id = deploy(h, "evaluation")
    with Session(h.engine) as db:
        stored = db.get(ExecutionDeploymentRevision, revision_id).canonical_configuration
    assert DeploymentConfig.model_validate_json(stored).evaluation
    # Nothing releases an evaluation deployment.
    refused("assurance_unavailable", lambda: deploy(h, "released", release_id=str(uuid4())))


def test_an_evaluation_deployment_puts_every_write_behind_a_review(actions):
    h = actions
    documents = [doc("connector"), doc("unreviewed")]
    package = doc("reviewed_action")
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    package["policies"][0]["ref"]["sha256"] = catalog_document_digest(documents[1])
    package["policies"][0]["ref"]["name"] = documents[1]["name"]
    intent = next(s for s in package["body"]["steps"] if s["id"] == "intent")
    intent.update(approval="none", independent_review=False)
    package["body"]["steps"] = [s for s in package["body"]["steps"] if s["id"] != "review"]
    next(s for s in package["body"]["steps"] if s["id"] == "dispatch").pop("decision")
    refused(
        "invalid_request",
        lambda: deploy(h, "unreviewed", package=package, documents=documents),
    )
    # The same package deploys for production (its unreviewed write is listed there).
    assert deploy(h, "unreviewed-production", purpose=None, package=package, documents=documents)


def test_only_a_package_evaluation_starts_an_evaluation_deployment(actions):
    h = actions
    h.authority.snapshot["release_required"] = True
    revision_id = deploy(h, "proving")

    def admit(key, evaluation=False):
        with Session(h.engine) as db, db.begin():
            return h.service.admit_execution(
                db,
                h.actor,
                AdmissionRequest(deployment_revision_id=revision_id, input=None),
                key,
                evaluation=evaluation,
            )

    refused("deployment_unavailable", lambda: admit("ordinary"))
    # A package evaluation runs it, records the proposal and sends nothing.
    service = evaluations(h)
    proposes = scenario(h, "proposes", {"state": "review", "proposal": {"operation": "submit"}})
    run = start(h, service, revision_id, [proposes])
    view = drive(h, service, run["id"])
    assert view["status"] == "completed", view
    assert [row["passed"] for row in view["results"]] == [True]


def test_no_review_on_an_evaluation_deployment_can_be_allowed(actions):
    h = actions
    revision_id = deploy(h, "no-allow")
    with Session(h.engine) as db, db.begin():
        execution = h.service.admit_execution(
            db,
            h.actor,
            AdmissionRequest(deployment_revision_id=revision_id, input=None),
            "evaluated",
            evaluation=True,
        )
    h.run2()
    from .dispatch_fixtures import intent_row, task_id

    task = task_id(h, execution.id)
    reviewer = h.reviewer()
    h.call("claim", reviewer, task)
    intent = intent_row(h, execution.id)

    def decide(decision):
        return h.call(
            "decide",
            reviewer,
            task,
            DecisionRequest(
                decision_id=f"d-{decision}", intent_sha256=intent.intent_sha256, decision=decision
            ),
        )

    refused("forbidden", lambda: decide("allow"))
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionActionDecision)) is None
    decide("deny")  # a person may still turn it down
