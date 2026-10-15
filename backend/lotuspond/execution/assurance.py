"""Real release evidence plus an independent exact executable/configuration bridge."""

from lotuspond_runtime.protocol import CanonicalUUID, Digest, SafeInt, Scope
from sqlalchemy import select

from ..fleet.authority import database_now_ms
from ..models import (
    Approval,
    Membership,
    Policy,
    Release,
    ReleaseDecision,
    Run,
    RunResult,
    User,
    serialize,
)
from ..policy import evaluate
from ..security import canonical, digest
from .authorization import lock, reauthorize
from .contracts import Contract
from .errors import ExecutionError, execution_service
from .publication import DEFAULT_DEPENDENCIES, load_publication


class AssuranceEvidence(Contract):
    """Returned by independently managed evidence; never admitted from request DTOs."""

    scope: Scope
    publication_sha256: Digest
    configuration_sha256: Digest
    release_id: CanonicalUUID
    candidate_run_id: CanonicalUUID
    decision_id: CanonicalUUID
    decision_sha256: Digest
    expires_at_ms: SafeInt


def _workspace_record(db, model, scope, identifier):
    row = lock(
        db, select(model).where(model.workspace_id == scope.workspace_id, model.id == identifier)
    )
    if row is None:
        raise ExecutionError("assurance_unavailable")
    return row


def _run(db, scope, identifier):
    row = _workspace_record(db, Run, scope, identifier)
    if row.project_id != scope.project_id or row.environment_id != scope.environment_id:
        raise ExecutionError("assurance_unavailable")
    results = list(
        db.scalars(
            select(RunResult)
            .where(RunResult.workspace_id == scope.workspace_id, RunResult.run_id == row.id)
            .order_by(RunResult.scenario_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    return {
        **serialize(row, exclude={"lease_expires_at"}),
        "results": [serialize(r) for r in results],
    }


def release_binding(
    db,
    actor,
    publication,
    package,
    release_id,
    configuration_sha256,
    snapshot,
    dependencies,
    sources=None,
):
    assurances = [
        package.assurance.model_dump(),
        *[
            source["assurance"]
            for source in (sources or {}).values()
            if source.get("schema_version") == "lotuspond.workflow-package.v2"
        ],
    ]
    required = snapshot.release_required or any(
        a["evaluation_refs"] or a["outcome_rule_refs"] for a in assurances
    )
    if release_id is None and not required:
        return None
    if release_id is None or configuration_sha256 is None:
        raise ExecutionError("assurance_unavailable")
    try:
        raw = dependencies._assurance_slot.call(
            dependencies.assurance,
            "binding",
            (actor.scope, publication.package_sha256, configuration_sha256, release_id),
            dependencies.timeout_seconds,
            "assurance_unavailable",
        )
        evidence = AssuranceEvidence.model_validate(raw)
        if (
            evidence.scope != actor.scope
            or evidence.publication_sha256 != publication.package_sha256
            or evidence.configuration_sha256 != configuration_sha256
            or evidence.release_id != release_id
        ):
            raise ValueError
        release = _workspace_record(db, Release, actor.scope, release_id)
        if (
            release.project_id != actor.scope.project_id
            or release.candidate_run_id != evidence.candidate_run_id
        ):
            raise ValueError
        policy = serialize(_workspace_record(db, Policy, actor.scope, release.policy_id))
        candidate = _run(db, actor.scope, release.candidate_run_id)
        baseline = (
            _run(db, actor.scope, release.baseline_run_id) if release.baseline_run_id else None
        )
        approvals = list(
            db.scalars(
                select(Approval)
                .where(
                    Approval.workspace_id == actor.scope.workspace_id,
                    Approval.release_id == release.id,
                    Approval.action == "release",
                    Approval.status == "approved",
                )
                .order_by(Approval.created_at, Approval.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        for approval in approvals:
            member = db.scalar(
                select(Membership)
                .where(
                    Membership.workspace_id == actor.scope.workspace_id,
                    Membership.user_id == approval.decided_by,
                )
                .execution_options(populate_existing=True)
            )
            user = db.scalar(
                select(User)
                .where(User.id == approval.decided_by)
                .execution_options(populate_existing=True)
            )
            if (
                approval.requested_by == approval.decided_by
                or approval.decided_at is None
                or member is None
                or member.role not in {"owner", "admin"}
                or user is None
                or user.disabled_at is not None
            ):
                raise ValueError
        decision = evaluate(policy, candidate, baseline, bool(approvals))
        if decision["verdict"] != "pass":
            raise ValueError
        current = {
            "release": serialize(release),
            "policy": policy,
            "candidate": candidate,
            "baseline": baseline,
            "approvals": [serialize(a) for a in approvals],
            **decision,
        }
        stored = _workspace_record(db, ReleaseDecision, actor.scope, evidence.decision_id)
        if (
            stored.release_id != release.id
            or stored.verdict != "pass"
            or stored.evidence_sha256 != evidence.decision_sha256
            or digest(canonical(stored.evidence)) != stored.evidence_sha256
            or canonical(current) != canonical(stored.evidence)
            or evidence.expires_at_ms <= database_now_ms(db)
        ):
            raise ValueError
        return release.id, release.candidate_run_id
    except Exception:
        raise ExecutionError("assurance_unavailable") from None


@execution_service
def check_release_binding(
    db,
    actor,
    publication_id: str,
    release_id: str | None,
    *,
    configuration_sha256: str | None = None,
    dependencies=DEFAULT_DEPENDENCIES,
) -> None:
    actor = reauthorize(db, actor, "manage")
    snapshot = dependencies.current(db, actor.scope)
    publication, package, _, _, sources = load_publication(
        db, actor.scope, publication_id, snapshot
    )
    release_binding(
        db,
        actor,
        publication,
        package,
        release_id,
        configuration_sha256,
        snapshot,
        dependencies,
        sources,
    )
