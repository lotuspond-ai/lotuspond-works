"""Proving a workflow package before release (docs/superpowers/specs/2026-11-15-package-proof-design.md).

A package evaluation run is an ordinary evaluation run whose subject is one package
version (`revision = "package:<publication sha256>@<deployment revision>"`). It runs the
real package once per named scenario, through an evaluation deployment of that publication
bound to evaluation connections, and records each
scenario's observed outcome: the execution's terminal state and result, or, when it stops
at a person's review, the exact proposed action, after which the execution is cancelled.
An evaluation never approves an action, so it writes nothing to any system.

`ReleaseEvidence` is the release binding provider deployment admission asks: it returns
the evidence tying a release to a package version when the release's candidate run is a
completed package evaluation of exactly that publication with a passing stored decision.
Admission then re-verifies the whole release pipeline in its own transaction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from lotuspond_runtime.protocol import CanonicalUUID
from pydantic import Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..fleet.authority import database_now_ms
from ..models import (
    ExecutablePublication,
    Execution,
    ExecutionDeploymentRevision,
    ExecutionHumanTask,
    ExecutionRequest,
    Release,
    ReleaseDecision,
    Run,
    RunResult,
    Scenario,
    serialize,
)
from .authorization import reauthorize, record, scoped
from .contracts import AdmissionRequest, Contract, ControlRequest
from .errors import ExecutionError

SUBJECT = "package:"
KEY = "pkgeval."
# A release decision proves a package version for this long.
VALIDITY_MS = 7 * 86_400_000
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
MAX_SCENARIOS = 200


def package_subject(package_sha256: str) -> str:
    return SUBJECT + package_sha256


def run_subject(revision: str) -> str:
    """A package run's subject: its `revision` before the evaluated deployment revision."""
    return revision.partition("@")[0]


def matches(expected, observed) -> bool:
    """Every value `expected` names equals the observed one; objects match as subsets."""
    if type(expected) is dict:
        return type(observed) is dict and all(
            key in observed and matches(value, observed[key]) for key, value in expected.items()
        )
    return expected == observed


class PackageEvaluationRequest(Contract):
    deployment_revision_id: CanonicalUUID
    name: str = Field(min_length=1, max_length=160)
    scenario_ids: list[CanonicalUUID] = Field(min_length=1, max_length=MAX_SCENARIOS)


class PackageEvaluations:
    """Start and advance package evaluation runs, as their owner asks."""

    def __init__(self, *, admission, controls, artifacts):
        self.admission, self.controls, self.artifacts = admission, controls, artifacts

    # ------------------------------------------------------------------ start

    def start(self, db, actor, request) -> dict:
        """Create the run. Its executions are admitted by `advance`, one per call: an
        admission takes the fair gates before any row lock, so a transaction admits one."""
        request = PackageEvaluationRequest.model_validate(request)
        actor = reauthorize(db, actor, "execute")
        scope = actor.scope
        revision = record(db, ExecutionDeploymentRevision, scope, request.deployment_revision_id)
        publication = record(db, ExecutablePublication, scope, revision.publication_id)
        if revision.status != "active":
            raise ExecutionError("conflict")
        wanted = sorted(set(request.scenario_ids))
        rows = list(
            db.scalars(
                select(Scenario)
                .where(
                    Scenario.workspace_id == scope.workspace_id,
                    Scenario.project_id == scope.project_id,
                    Scenario.id.in_(wanted),
                )
                .order_by(Scenario.id)
            )
        )
        if len(rows) != len(wanted):
            raise ExecutionError("not_found")
        snapshot = []
        for row in rows:
            serialized = serialize(row)
            snapshot.append(
                {
                    key: serialized[key]
                    for key in ("id", "name", "input", "expected", "critical", "modality")
                }
            )
        run = Run(
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            environment_id=scope.environment_id,
            runner_id=None,
            name=request.name,
            # The subject, and the deployment revision its scenarios run on.
            revision=f"{package_subject(publication.package_sha256)}@{revision.id}",
            status="running",
            scenario_snapshot=snapshot,
        )
        db.add(run)
        db.flush()
        return self._view(db, run)

    # ------------------------------------------------------------------ advance

    def advance(self, db, actor, run_id) -> dict:
        """One step, safe to repeat; call it until the run is `completed`.

        In order: admit one scenario not yet started; else cancel one execution waiting
        at a review, recording its exact proposal; else record every finished execution
        and complete the run when all have results. The first two take the fair gates,
        so each is the only gated step of its transaction (as admission and control
        require); the last takes none.
        """
        run_id = TypeAdapter(CanonicalUUID).validate_python(run_id, strict=True)
        scope = actor.scope
        run = self._run(db, scope, run_id)
        if run.status == "completed":
            return self._view(db, run)
        done = self._done(db, run)
        pending = [item for item in run.scenario_snapshot if item["id"] not in done]
        # 1. One admission.
        for item in pending:
            if self._execution_id(db, scope, run, item) is None:
                self.admission.admit_execution(
                    db,
                    actor,
                    AdmissionRequest(
                        deployment_revision_id=self._revision_id(db, scope, run),
                        input=item["input"],
                    ),
                    _key(run.id, item["id"]),
                )
                return self._view(db, run)
        # 2. One review: recorded, then cancelled unsent (never approved).
        for item in pending:
            execution = record(db, Execution, scope, self._execution_id(db, scope, run, item))
            if execution.state in TERMINAL or execution.requested_state != "running":
                continue
            task = db.scalar(
                scoped(ExecutionHumanTask, scope)
                .where(
                    ExecutionHumanTask.execution_id == execution.id,
                    ExecutionHumanTask.state == "open",
                    ExecutionHumanTask.kind == "review",
                )
                .order_by(ExecutionHumanTask.id)
                .limit(1)
            )
            if task is None or task.intent_id is None:
                continue
            self.controls.request_control(
                db,
                actor,
                execution.id,
                ControlRequest(request_id=f"evaluation-{execution.id}", action="cancel"),
            )
            from .actions import load_intent

            verified = load_intent(db, scope, task.intent_id, artifacts=self.artifacts)
            binding = verified.intent.binding
            self._record(
                db,
                run,
                item,
                execution,
                {
                    "execution_id": execution.id,
                    "state": "review",
                    "proposal": {
                        "connector": binding.connector_name,
                        "operation": binding.operation,
                        "target": verified.target,
                        "input": verified.input,
                    },
                },
            )
            return self._view(db, run)
        # 3. Every finished execution; no gate is taken.
        actor = reauthorize(db, actor, "execute")
        run = self._run(db, scope, run_id, locked=True)
        for item in pending:
            execution = record(db, Execution, scope, self._execution_id(db, scope, run, item))
            if execution.state not in TERMINAL:
                continue
            outcome = {
                "execution_id": execution.id,
                "state": execution.state,
                "reason": execution.reason,
                "result": self._result(db, execution),
            }
            self._record(db, run, item, execution, outcome)
            done.add(item["id"])
        if len(done) == len(run.scenario_snapshot):
            run.status, run.completed_at = "completed", datetime.now(UTC)
        db.flush()
        return self._view(db, run)

    @staticmethod
    def _run(db, scope, run_id, *, locked=False):
        query = select(Run).where(
            Run.workspace_id == scope.workspace_id,
            Run.id == run_id,
            Run.project_id == scope.project_id,
            Run.environment_id == scope.environment_id,
        )
        run = db.scalar(query.with_for_update() if locked else query)
        if run is None or not run.revision.startswith(SUBJECT):
            raise ExecutionError("not_found")
        return run

    @staticmethod
    def _done(db, run):
        return {
            row.scenario_id
            for row in db.scalars(
                select(RunResult).where(
                    RunResult.workspace_id == run.workspace_id, RunResult.run_id == run.id
                )
            )
        }

    @staticmethod
    def _execution_id(db, scope, run, item):
        # Admission stores the key's identity, never the key itself.
        from .admission import request_identity

        return _requested(db, scope, request_identity(_key(run.id, item["id"])))

    @staticmethod
    def _revision_id(db, scope, run):
        return run.revision.partition("@")[2]

    @staticmethod
    def _record(db, run, item, execution, outcome):
        latency = max(0, database_now_ms(db) - execution.admitted_at_ms)
        db.add(
            RunResult(
                workspace_id=run.workspace_id,
                run_id=run.id,
                scenario_id=item["id"],
                passed=matches(item["expected"], outcome),
                output=outcome,
                latency_ms=latency,
                cost_usd=0,
                evidence={"execution_id": execution.id},
            )
        )
        db.flush()

    def _result(self, db, execution):
        if execution.result_artifact_id is None:
            return None
        from .actions import _scope

        raw = self.artifacts._get_internal(
            db,
            _scope(execution),
            {
                "id": execution.result_artifact_id,
                "version": execution.result_artifact_version,
                "sha256": execution.result_sha256,
            },
        )
        return json.loads(raw)

    @staticmethod
    def _view(db, run):
        results = list(
            db.scalars(
                select(RunResult)
                .where(RunResult.workspace_id == run.workspace_id, RunResult.run_id == run.id)
                .order_by(RunResult.scenario_id)
            )
        )
        return {
            **serialize(run, exclude={"lease_expires_at"}),
            "results": [serialize(r) for r in results],
        }


def _key(run_id, scenario_id) -> str:
    return f"{KEY}{run_id}.{scenario_id}"


def _requested(db, scope, key):
    return db.scalar(
        select(ExecutionRequest.execution_id).where(
            ExecutionRequest.workspace_id == scope.workspace_id,
            ExecutionRequest.project_id == scope.project_id,
            ExecutionRequest.environment_id == scope.environment_id,
            ExecutionRequest.request_key == key,
        )
    )


class ReleaseEvidence:
    """The release binding provider: evidence that a release proves one package version.

    Asked outside the caller's transaction (`ExecutionDependencies.assurance`), it reads
    with its own session and grants nothing: admission re-verifies the whole release in
    its own transaction (`validated_release_binding`).
    """

    def __init__(self, engine, *, validity_ms: int = VALIDITY_MS):
        if not 60_000 <= validity_ms <= 90 * 86_400_000:
            raise ValueError("Invalid release validity")
        self.engine, self.validity_ms = engine, validity_ms

    def binding(self, scope, package_sha256, configuration_sha256, release_id):
        with Session(self.engine) as db:
            release = db.scalar(
                select(Release).where(
                    Release.workspace_id == scope.workspace_id, Release.id == release_id
                )
            )
            if release is None or release.project_id != scope.project_id:
                raise ExecutionError("assurance_unavailable")
            run = db.scalar(
                select(Run).where(
                    Run.workspace_id == scope.workspace_id, Run.id == release.candidate_run_id
                )
            )
            if (
                run is None
                or run.project_id != scope.project_id
                or run.status != "completed"
                or run_subject(run.revision) != package_subject(package_sha256)
            ):
                raise ExecutionError("assurance_unavailable")
            decision = db.scalar(
                select(ReleaseDecision)
                .where(
                    ReleaseDecision.workspace_id == scope.workspace_id,
                    ReleaseDecision.release_id == release.id,
                    ReleaseDecision.verdict == "pass",
                )
                .order_by(ReleaseDecision.created_at.desc(), ReleaseDecision.id.desc())
                .limit(1)
            )
            if decision is None:
                raise ExecutionError("assurance_unavailable")
            decided_ms = int(decision.created_at.timestamp() * 1000)
            return {
                "scope": scope.model_dump(),
                "publication_sha256": package_sha256,
                "configuration_sha256": configuration_sha256,
                "release_id": release.id,
                "candidate_run_id": run.id,
                "decision_id": decision.id,
                "decision_sha256": decision.evidence_sha256,
                "expires_at_ms": decided_ms + self.validity_ms,
            }
