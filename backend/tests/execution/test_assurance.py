from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from lotuspond.execution.assurance import check_release_binding
from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.contracts import canonical_configuration
from lotuspond.execution.deployments import create_deployment, set_deployment_status
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.publication import ExecutionDependencies
from lotuspond.models import (
    Approval,
    DeploymentAssuranceBinding,
    Membership,
    Policy,
    Release,
    ReleaseDecision,
    Run,
    RunResult,
    Scenario,
    User,
    Workspace,
    new_id,
    now,
    serialize,
)
from lotuspond.policy import evaluate
from lotuspond.security import canonical, digest

from .test_deployments import deployment_request
from .test_publication import harness as harness


def release_records(db, h, publication, request):
    """A real complete evaluation, policy, independent human approval and decision."""
    scenario = Scenario(
        workspace_id=h.scope.workspace_id,
        project_id=h.scope.project_id,
        name="check",
        input={},
        expected={},
        critical=True,
    )
    policy = Policy(workspace_id=h.scope.workspace_id, name="release", require_approval=True)
    reviewer = User(email="reviewer@example.test", name="reviewer", password_hash="x")
    db.add_all([scenario, policy, reviewer])
    db.flush()
    db.add(Membership(workspace_id=h.scope.workspace_id, user_id=reviewer.id, role="admin"))
    run = Run(
        **h.scope.model_dump(),
        name="candidate",
        revision=publication.package_sha256,
        status="completed",
        completed_at=now(),
        scenario_snapshot=[serialize(scenario)],
    )
    db.add(run)
    db.flush()
    result = RunResult(
        workspace_id=h.scope.workspace_id,
        run_id=run.id,
        scenario_id=scenario.id,
        passed=True,
        output={},
        latency_ms=1,
        cost_usd=0,
        evidence={},
    )
    release = Release(
        workspace_id=h.scope.workspace_id,
        project_id=h.scope.project_id,
        name="release",
        revision=publication.package_sha256,
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
    # Existing release evaluation reads persisted Numeric columns in a later request.
    db.refresh(policy)
    db.refresh(result)
    candidate = {**serialize(run, exclude={"lease_expires_at"}), "results": [serialize(result)]}
    decision = evaluate(serialize(policy), candidate, None, True)
    assert decision["verdict"] == "pass"
    evidence = {
        "release": serialize(release),
        "policy": serialize(policy),
        "candidate": candidate,
        "baseline": None,
        "approvals": [serialize(approval)],
        **decision,
    }
    stored = ReleaseDecision(
        workspace_id=h.scope.workspace_id,
        release_id=release.id,
        verdict="pass",
        reasons=decision["reasons"],
        evidence=evidence,
        evidence_sha256=digest(canonical(evidence)),
    )
    db.add(stored)
    db.flush()
    proof = {
        "scope": h.scope.model_dump(),
        "publication_sha256": publication.package_sha256,
        "configuration_sha256": sha256(canonical_configuration(request.configuration)).hexdigest(),
        "release_id": release.id,
        "candidate_run_id": run.id,
        "decision_id": stored.id,
        "decision_sha256": stored.evidence_sha256,
        "expires_at_ms": 9007199254740991,
    }
    return release, policy, proof


class FixtureAssurance:
    test_only = True

    def __init__(self, proof):
        self.proof = proof

    def binding(self, scope, package_sha256, configuration_sha256, release_id):
        return deepcopy(self.proof)


def test_evaluation_id_is_not_release_authority(harness):
    h = harness
    with Session(h.engine) as db, db.begin():
        publication = h.publish(db)
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        with pytest.raises(ExecutionError, match="assurance_unavailable"):
            check_release_binding(db, actor, publication.id, new_id(), dependencies=h.dependencies)


class SlowAssurance(FixtureAssurance):
    """Answers correctly, after the publisher snapshot it was asked under has expired."""

    def binding(self, scope, package_sha256, configuration_sha256, release_id):
        sleep(2)
        return super().binding(scope, package_sha256, configuration_sha256, release_id)


def test_standalone_release_check_rechecks_the_snapshot_after_its_provider(harness):
    """Carried Minor (Task 14): `check_release_binding` ends with the final expiry check."""
    from lotuspond.fleet.authority import database_now_ms

    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        request = deployment_request(h, p.id)
        release, _, proof = release_records(db, h, p, request)
        configuration = sha256(canonical_configuration(request.configuration)).hexdigest()
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        current = ExecutionDependencies(
            authority=h.authority, assurance=FixtureAssurance(proof), test_mode=True
        )
        # Positive control: a current snapshot and a passing bridge are accepted.
        check_release_binding(
            db,
            actor,
            p.id,
            release.id,
            configuration_sha256=configuration,
            dependencies=current,
        )
        current.close()
        h.authority.snapshot["expires_at_ms"] = database_now_ms(db) + 1000
        slow = ExecutionDependencies(
            authority=h.authority, assurance=SlowAssurance(proof), test_mode=True
        )
        with pytest.raises(ExecutionError, match="trust_unavailable"):
            check_release_binding(
                db,
                actor,
                p.id,
                release.id,
                configuration_sha256=configuration,
                dependencies=slow,
            )
        slow.close()


@pytest.mark.parametrize(
    "mismatch", ["missing_bridge", "configuration", "package", "candidate", "decision"]
)
def test_real_passing_evaluation_still_needs_exact_independent_bridge(harness, mismatch):
    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        request = deployment_request(h, p.id)
        release, _, proof = release_records(db, h, p, request)
        request = request.model_copy(update={"release_id": release.id})
        if mismatch in {"configuration", "package"}:
            proof[
                "configuration_sha256" if mismatch == "configuration" else "publication_sha256"
            ] = "0" * 64
        elif mismatch == "candidate":
            proof["candidate_run_id"] = new_id()
        elif mismatch == "decision":
            proof["decision_sha256"] = "0" * 64
        deps = ExecutionDependencies(
            authority=h.authority,
            test_mode=True,
            assurance=None if mismatch == "missing_bridge" else FixtureAssurance(proof),
        )
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        with pytest.raises(ExecutionError, match="assurance_unavailable"):
            create_deployment(db, actor, request, "create", dependencies=deps)
        deps.close()


def test_real_policy_and_independent_approval_rechecked_on_activation(harness):
    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        request = deployment_request(h, p.id)
        release, policy, proof = release_records(db, h, p, request)
        request = request.model_copy(update={"release_id": release.id})
        deps = ExecutionDependencies(
            authority=h.authority, assurance=FixtureAssurance(proof), test_mode=True
        )
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        view = create_deployment(db, actor, request, "create", dependencies=deps)
        binding = (
            db.query(DeploymentAssuranceBinding)
            .filter_by(deployment_revision_id=view.revision_id)
            .one()
        )
        assert (binding.release_id, binding.candidate_run_id) == (
            release.id,
            proof["candidate_run_id"],
        )
        set_deployment_status(
            db, actor, view.id, view.revision_id, "enabled", "enable", dependencies=deps
        )
        policy.maximum_p95_ms = 0
        db.flush()
        with pytest.raises(ExecutionError, match="assurance_unavailable"):
            set_deployment_status(
                db, actor, view.id, view.revision_id, "enabled", "enable", dependencies=deps
            )
        deps.close()


def test_activation_rechecks_policy_after_actual_lock_wait(harness):
    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        request = deployment_request(h, p.id)
        release, policy, proof = release_records(db, h, p, request)
        request = request.model_copy(update={"release_id": release.id})
        deps = ExecutionDependencies(
            authority=h.authority, assurance=FixtureAssurance(proof), test_mode=True
        )
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        view = create_deployment(db, actor, request, "create", dependencies=deps)
        policy_id = policy.id
    started, pid = Event(), []

    def activate():
        with Session(h.engine) as db, db.begin():
            pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                set_deployment_status(
                    db, actor, view.id, view.revision_id, "enabled", "enable", dependencies=deps
                )
            except ExecutionError as error:
                return error.code
            return "unexpected_allow"

    with ThreadPoolExecutor(max_workers=1) as executor:
        with Session(h.engine) as blocker, blocker.begin():
            blocker.scalar(
                select(Workspace).where(Workspace.id == h.scope.workspace_id).with_for_update()
            )
            future = executor.submit(activate)
            assert started.wait(5)
            deadline = monotonic() + 5
            with h.engine.connect() as observer:
                while monotonic() < deadline:
                    if observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid[0]}
                    ):
                        break
                else:
                    pytest.fail("Activation did not reach the PostgreSQL lock barrier")
            blocker.get(Policy, policy_id).maximum_p95_ms = 0
        assert future.result(timeout=10) == "assurance_unavailable"
    deps.close()


@pytest.mark.parametrize("with_assurance", [False, True])
def test_deployment_assurance_capture_preserves_existing_return_contracts(harness, with_assurance):
    from lotuspond.execution.assurance import AssuranceEvidence, release_binding
    from lotuspond.execution.deployments import load_deployment
    from lotuspond.execution.publication import load_publication

    h = harness
    with Session(h.engine) as db, db.begin():
        publication = h.publish(db)
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        request = deployment_request(h, publication.id)
        proof = None
        if with_assurance:
            release, _, proof = release_records(db, h, publication, request)
            request = request.model_copy(update={"release_id": release.id})
        deps = ExecutionDependencies(
            authority=h.authority,
            assurance=FixtureAssurance(proof) if proof else None,
            test_mode=True,
        )
        try:
            deployed = create_deployment(db, actor, request, "capture", dependencies=deps)
            set_deployment_status(
                db,
                actor,
                deployed.id,
                deployed.revision_id,
                "enabled",
                "enable-capture",
                dependencies=deps,
            )
            original = load_deployment(db, actor, deployed.revision_id, dependencies=deps)
            captured = load_deployment(
                db, actor, deployed.revision_id, dependencies=deps, include_assurance=True
            )
            assert len(original) == 2 and len(captured) == 3
            assert captured[:2] == original
            if proof:
                assert type(captured[2]) is AssuranceEvidence
                assert captured[2].model_dump() == proof
            else:
                assert captured[2] is None
            snapshot = deps.current(db, h.scope)
            row, package, _, _, sources = load_publication(db, h.scope, publication.id, snapshot)
            old_binding = release_binding(
                db,
                actor,
                row,
                package,
                request.release_id,
                original[1].configuration_sha256,
                snapshot,
                deps,
                sources,
            )
            assert old_binding == (
                (proof["release_id"], proof["candidate_run_id"]) if proof else None
            )
        finally:
            deps.close()
