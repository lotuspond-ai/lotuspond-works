"""The release binding provider: a release proves exactly one package version."""

from hashlib import sha256

import pytest
from sqlalchemy.orm import Session

from lotuspond.execution.assurance import check_release_binding
from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.contracts import canonical_configuration
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.package_evaluation import ReleaseEvidence, package_subject
from lotuspond.execution.publication import ExecutionDependencies
from lotuspond.models import (
    Approval,
    Membership,
    Policy,
    Release,
    ReleaseDecision,
    Run,
    RunResult,
    Scenario,
    User,
    now,
    serialize,
)
from lotuspond.policy import evaluate
from lotuspond.security import canonical, digest

from .test_deployments import deployment_request
from .test_publication import harness as harness  # noqa: F401


def package_release(db, h, package_sha256, *, decide=True, status="completed"):
    """A package evaluation run of `package_sha256`, a policy, an independent
    approval and (by default) the passing stored decision."""
    scenario = Scenario(
        workspace_id=h.scope.workspace_id,
        project_id=h.scope.project_id,
        name="late order",
        input={"po_number": "PO-1"},
        expected={"state": "review"},
        critical=True,
    )
    policy = Policy(workspace_id=h.scope.workspace_id, name="release", require_approval=True)
    reviewer = User(email=f"r-{package_sha256[:8]}@example.test", name="r", password_hash="x")
    db.add_all([scenario, policy, reviewer])
    db.flush()
    db.add(Membership(workspace_id=h.scope.workspace_id, user_id=reviewer.id, role="admin"))
    run = Run(
        **h.scope.model_dump(),
        name="package evaluation",
        revision=package_subject(package_sha256),
        status=status,
        completed_at=now(),
        scenario_snapshot=[
            {
                key: serialize(scenario)[key]
                for key in ("id", "name", "input", "expected", "critical", "modality")
            }
        ],
    )
    db.add(run)
    db.flush()
    result = RunResult(
        workspace_id=h.scope.workspace_id,
        run_id=run.id,
        scenario_id=scenario.id,
        passed=True,
        output={"state": "review"},
        latency_ms=1,
        cost_usd=0,
        evidence={},
    )
    release = Release(
        workspace_id=h.scope.workspace_id,
        project_id=h.scope.project_id,
        name="release",
        revision=package_subject(package_sha256),
        candidate_run_id=run.id,
        policy_id=policy.id,
    )
    db.add_all([result, release])
    db.flush()
    approval = Approval(
        workspace_id=h.scope.workspace_id,
        release_id=release.id,
        action="release",
        reason="independent review",
        requested_by=h.principal.user_id,
        decided_by=reviewer.id,
        decided_at=now(),
        status="approved",
    )
    db.add(approval)
    db.flush()
    db.refresh(policy)
    db.refresh(result)
    candidate = {**serialize(run, exclude={"lease_expires_at"}), "results": [serialize(result)]}
    decision = evaluate(serialize(policy), candidate, None, True)
    if decide:
        evidence = {
            "release": serialize(release),
            "policy": serialize(policy),
            "candidate": candidate,
            "baseline": None,
            "approvals": [serialize(approval)],
            **decision,
        }
        db.add(
            ReleaseDecision(
                workspace_id=h.scope.workspace_id,
                release_id=release.id,
                verdict=decision["verdict"],
                reasons=decision["reasons"],
                evidence=evidence,
                evidence_sha256=digest(canonical(evidence)),
            )
        )
    db.flush()
    return release


def check(h, publication_id, release_id, configuration):
    deps = ExecutionDependencies(
        authority=h.authority, assurance=ReleaseEvidence(h.engine), test_mode=True
    )
    try:
        with Session(h.engine) as db, db.begin():
            actor = authorize_execution(db, h.principal, h.scope, "manage")
            check_release_binding(
                db,
                actor,
                publication_id,
                release_id,
                configuration_sha256=configuration,
                dependencies=deps,
            )
    finally:
        deps.close()


def test_a_release_of_a_package_evaluation_proves_that_package(harness):
    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        request = deployment_request(h, p.id)
        release = package_release(db, h, p.package_sha256)
        ids = (p.id, release.id)
    configuration = sha256(canonical_configuration(request.configuration)).hexdigest()
    # Committed first: the provider reads with its own session, as admission asks it.
    check(h, *ids, configuration)


@pytest.mark.parametrize("case", ["other_package", "undecided", "still_running"])
def test_a_release_proves_nothing_else(harness, case):
    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        request = deployment_request(h, p.id)
        release = package_release(
            db,
            h,
            "f" * 64 if case == "other_package" else p.package_sha256,
            decide=case != "undecided",
            status="running" if case == "still_running" else "completed",
        )
        ids = (p.id, release.id)
    configuration = sha256(canonical_configuration(request.configuration)).hexdigest()
    with pytest.raises(ExecutionError, match="assurance_unavailable"):
        check(h, *ids, configuration)


def test_the_provider_reports_an_expiring_binding_and_grants_nothing(harness):
    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        release = package_release(db, h, p.package_sha256)
        ids = (p.package_sha256, release.id)
    evidence = ReleaseEvidence(h.engine, validity_ms=60_000).binding(
        h.scope, ids[0], "a" * 64, ids[1]
    )
    assert evidence["publication_sha256"] == ids[0]
    assert evidence["configuration_sha256"] == "a" * 64  # admission checks its own
    with Session(h.engine) as db:
        stored = db.get(ReleaseDecision, evidence["decision_id"])
        decided = int(stored.created_at.timestamp() * 1000)
    assert evidence["expires_at_ms"] == decided + 60_000
    with pytest.raises(ValueError):
        ReleaseEvidence(h.engine, validity_ms=1)
