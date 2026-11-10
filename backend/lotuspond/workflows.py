from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from . import schemas as s
from .deps import DB, audit, record, require
from .models import (
    AlertDelivery,
    Approval,
    Assessment,
    AuditEvent,
    Environment,
    Incident,
    IncidentEvent,
    Monitor,
    Policy,
    Project,
    Receipt,
    Release,
    ReleaseDecision,
    Run,
    Runner,
    RunResult,
    Scenario,
    Trace,
    VoiceSession,
    now,
    serialize,
)
from .policy import evaluate
from .security import Principal, canonical, digest, fail, new_token, redact, require_admin, utc

router = APIRouter(prefix="/api/v1")

MAX_RUN_SNAPSHOT_BYTES = 4 << 20


def _utc_month_window(reference: datetime | None = None) -> tuple[datetime, datetime]:
    current = utc(reference or now()).astimezone(UTC)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def listing(model):
    def endpoint(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before: datetime | None = None,
        before_id: s.Identifier | None = None,
        project_id: s.Identifier | None = None,
        p: Principal = Depends(require()),
        db: Session = DB,
    ):
        query = select(model).where(model.workspace_id == p.workspace_id)
        if project_id:
            record(db, Project, p.workspace_id, project_id)
            if hasattr(model, "project_id"):
                query = query.where(model.project_id == project_id)
        if before:
            identifier = before_id or "ffffffff-ffff-ffff-ffff-ffffffffffff"
            query = query.where(
                or_(
                    model.created_at < before,
                    and_(model.created_at == before, model.id < identifier),
                )
            )
        rows = list(
            db.scalars(query.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1))
        )
        items = [serialize(row) for row in rows[:limit]]
        return {
            "items": items,
            "next": {"before": items[-1]["created_at"], "before_id": items[-1]["id"]}
            if len(rows) > limit
            else None,
        }

    return endpoint


for _path, _model in {
    "projects": Project,
    "environments": Environment,
    "scenarios": Scenario,
    "runners": Runner,
    "runs": Run,
    "policies": Policy,
    "releases": Release,
    "approvals": Approval,
    "audit-events": AuditEvent,
    "receipts": Receipt,
}.items():
    router.add_api_route(f"/{_path}", listing(_model), methods=["GET"], name=f"list_{_path}")


def insert(db, p, model, body, *, administrator=False):
    if administrator:
        require_admin(p)
    data = body.model_dump()
    if data.get("project_id"):
        record(db, Project, p.workspace_id, data["project_id"])
    row = model(workspace_id=p.workspace_id, **redact(data))
    db.add(row)
    db.flush()
    audit(db, p, f"{model.__tablename__}.created", row.id)
    return serialize(row)


@router.get("/overview")
def overview(p: Principal = Depends(require()), db: Session = DB):
    def count(model, *filters):
        return db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.workspace_id == p.workspace_id, *filters)
        )

    month_start, next_month_start = _utc_month_window()
    cost_counts = {"reported": 0, "unavailable": 0, "unknown": 0}
    reported_cost = None
    for measurement, total, amount in db.execute(
        select(Trace.cost_measurement, func.count(), func.sum(Trace.cost_usd))
        .where(
            Trace.workspace_id == p.workspace_id,
            Trace.occurred_at >= month_start,
            Trace.occurred_at < next_month_start,
        )
        .group_by(Trace.cost_measurement)
    ):
        cost_counts[measurement] = total
        if measurement == "reported":
            reported_cost = amount
    return {
        "releases": count(Release),
        "scenarios": count(Scenario),
        "active_runs": count(Run, Run.status.in_(["queued", "running"])),
        "pending_approvals": count(Approval, Approval.status == "pending"),
        "monthly_cost_usd": float(reported_cost) if reported_cost is not None else None,
        "monthly_cost_counts": cost_counts,
    }


@router.post("/projects", status_code=201)
def project(body: s.ProjectInput, p: Principal = Depends(require("write")), db: Session = DB):
    return insert(db, p, Project, body)


@router.post("/environments", status_code=201)
def environment(
    body: s.EnvironmentInput, p: Principal = Depends(require("write")), db: Session = DB
):
    return insert(db, p, Environment, body)


@router.post("/scenarios", status_code=201)
def scenario(body: s.ScenarioInput, p: Principal = Depends(require("write")), db: Session = DB):
    return insert(db, p, Scenario, body)


@router.post("/runners", status_code=201)
def runner(body: s.RunnerInput, p: Principal = Depends(require("write")), db: Session = DB):
    return insert(db, p, Runner, body)


@router.post("/policies", status_code=201)
def policy(body: s.PolicyInput, p: Principal = Depends(require("write")), db: Session = DB):
    return insert(db, p, Policy, body, administrator=True)


@router.get("/incidents")
def incidents(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: datetime | None = None,
    before_id: s.Identifier | None = None,
    project_id: s.Identifier | None = None,
    status: Annotated[str | None, Query(pattern="^(open|resolved)$")] = None,
    category: Annotated[
        str | None,
        Query(pattern="^(manual|assessment_failed|assessment_unavailable)$"),
    ] = None,
    p: Principal = Depends(require()),
    db: Session = DB,
):
    query = select(Incident).where(Incident.workspace_id == p.workspace_id)
    if project_id:
        record(db, Project, p.workspace_id, project_id)
        query = query.where(Incident.project_id == project_id)
    if status:
        query = query.where(Incident.status == status)
    if category:
        query = query.where(Incident.category == category)
    if before:
        identifier = before_id or "ffffffff-ffff-ffff-ffff-ffffffffffff"
        query = query.where(
            or_(
                Incident.created_at < before,
                and_(Incident.created_at == before, Incident.id < identifier),
            )
        )
    rows = list(
        db.scalars(query.order_by(Incident.created_at.desc(), Incident.id.desc()).limit(limit + 1))
    )
    items = [serialize(row) for row in rows[:limit]]
    return {
        "items": items,
        "next": {"before": items[-1]["created_at"], "before_id": items[-1]["id"]}
        if len(rows) > limit
        else None,
    }


def _linked(db: Session, model, workspace_id: str, identifier: str | None):
    if not identifier:
        return None
    row = db.scalar(select(model).where(model.workspace_id == workspace_id, model.id == identifier))
    return serialize(row) if row else None


@router.get("/incidents/{identifier}")
def incident_detail(
    identifier: s.Identifier,
    p: Principal = Depends(require()),
    db: Session = DB,
):
    row = record(db, Incident, p.workspace_id, identifier)
    history = db.scalars(
        select(IncidentEvent)
        .where(
            IncidentEvent.workspace_id == p.workspace_id,
            IncidentEvent.incident_id == row.id,
        )
        .order_by(IncidentEvent.created_at, IncidentEvent.id)
    )
    alerts = db.scalars(
        select(AlertDelivery)
        .where(
            AlertDelivery.workspace_id == p.workspace_id,
            AlertDelivery.incident_id == row.id,
        )
        .order_by(AlertDelivery.created_at.desc(), AlertDelivery.id.desc())
    )
    return {
        "incident": serialize(row),
        "project": _linked(db, Project, p.workspace_id, row.project_id),
        "trace": _linked(db, Trace, p.workspace_id, row.trace_id),
        "voice_session": _linked(db, VoiceSession, p.workspace_id, row.voice_session_id),
        "assessment": _linked(db, Assessment, p.workspace_id, row.assessment_id),
        "monitor": _linked(db, Monitor, p.workspace_id, row.monitor_id),
        "history": [serialize(item) for item in history],
        "alerts": [
            serialize(item, exclude={"payload", "lease_token_hash", "lease_expires_at"})
            for item in alerts
        ],
    }


def load_run(db, workspace, identifier):
    run = record(db, Run, workspace, identifier)
    results = db.scalars(
        select(RunResult)
        .where(RunResult.workspace_id == workspace, RunResult.run_id == identifier)
        .order_by(RunResult.scenario_id)
    )
    return {
        **serialize(run, exclude={"lease_expires_at"}),
        "results": [serialize(row) for row in results],
    }


def _supports_runner_capabilities(db: Session, capabilities: list[str]):
    """Require every frozen scenario modality to be supported without locking mismatches."""
    if not capabilities:
        return None
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        scenarios = func.json_array_elements(Run.scenario_snapshot).table_valued("value").alias()
        modality = scenarios.c.value.op("->>")("modality")
    else:
        scenarios = func.json_each(Run.scenario_snapshot).table_valued("key", "value").alias()
        modality = func.json_extract(scenarios.c.value, "$.modality")
    unsupported = (
        select(1)
        .select_from(scenarios)
        .where(func.coalesce(modality, "text").not_in(capabilities))
        .correlate(Run)
    )
    return ~exists(unsupported)


@router.post("/runs", status_code=201)
def create_run(body: s.RunInput, p: Principal = Depends(require("run")), db: Session = DB):
    record(db, Project, p.workspace_id, body.project_id)
    if body.environment_id:
        environment = record(db, Environment, p.workspace_id, body.environment_id)
        if environment.project_id != body.project_id:
            fail(400, "project_mismatch", "The environment must belong to the run project.")
    scenarios = db.scalars(
        select(Scenario)
        .where(Scenario.workspace_id == p.workspace_id, Scenario.project_id == body.project_id)
        .order_by(Scenario.id)
        .limit(1001)
        .execution_options(yield_per=10)
    )
    snapshot = []
    snapshot_bytes = 2  # The canonical JSON list's opening and closing brackets.
    for row in scenarios:
        if len(snapshot) == 1000:
            fail(409, "scenario_limit", "A run requires between 1 and 1000 scenarios.")
        serialized = serialize(row)
        item = {
            key: serialized[key]
            for key in ("id", "name", "input", "expected", "critical", "modality")
        }
        snapshot_bytes += (1 if snapshot else 0) + len(canonical(item).encode("utf-8"))
        if snapshot_bytes > MAX_RUN_SNAPSHOT_BYTES:
            fail(
                413,
                "run_snapshot_too_large",
                "The frozen scenario snapshot exceeds the 4 MiB run limit.",
            )
        snapshot.append(item)
    if not snapshot:
        fail(409, "scenario_limit", "A run requires between 1 and 1000 scenarios.")
    run = Run(workspace_id=p.workspace_id, scenario_snapshot=snapshot, **body.model_dump())
    db.add(run)
    db.flush()
    audit(db, p, "runs.created", run.id)
    return serialize(run)


@router.get("/runs/{identifier}")
def get_run(identifier: s.Identifier, p: Principal = Depends(require()), db: Session = DB):
    return load_run(db, p.workspace_id, identifier)


@router.post("/runners/{identifier}/heartbeat")
def heartbeat(identifier: s.Identifier, p: Principal = Depends(require("run")), db: Session = DB):
    row = record(db, Runner, p.workspace_id, identifier)
    row.last_seen_at = now()
    return {"ok": True}


@router.post("/runners/{identifier}/claim")
def claim(identifier: s.Identifier, p: Principal = Depends(require("run")), db: Session = DB):
    runner = record(db, Runner, p.workspace_id, identifier)
    runner.last_seen_at = now()
    eligible = select(Run).where(
        Run.workspace_id == p.workspace_id,
        or_(Run.status == "queued", and_(Run.status == "running", Run.lease_expires_at < now())),
    )
    capability_filter = _supports_runner_capabilities(db, runner.capabilities)
    if capability_filter is not None:
        eligible = eligible.where(capability_filter)
    eligible = eligible.order_by(Run.created_at).with_for_update(skip_locked=True).limit(1)
    for run in db.scalars(eligible):
        lease = new_token()
        run.status = "running"
        run.runner_id = runner.id
        run.lease_token_hash = digest(lease)
        run.lease_expires_at = now() + timedelta(minutes=15)
        audit(db, p, "runs.claimed", run.id, {"runner_id": runner.id})
        return {"run": serialize(run), "lease_token": lease}
    return {"run": None, "lease_token": None}


def leased(db, p, identifier, lease):
    row = record(db, Run, p.workspace_id, identifier, lock=True)
    if (
        row.status != "running"
        or row.lease_token_hash != digest(lease)
        or not row.lease_expires_at
        or utc(row.lease_expires_at) <= now()
    ):
        fail(409, "invalid_lease", "The run lease is no longer active.")
    return row


@router.post("/runs/{identifier}/renew")
def renew(
    identifier: s.Identifier,
    body: s.LeaseInput,
    p: Principal = Depends(require("run")),
    db: Session = DB,
):
    run = leased(db, p, identifier, body.lease_token)
    run.lease_expires_at = now() + timedelta(minutes=15)
    return {"ok": True}


@router.post("/runs/{identifier}/results")
def submit_results(
    identifier: s.Identifier,
    body: s.ResultBatch,
    p: Principal = Depends(require("run")),
    db: Session = DB,
):
    run = leased(db, p, identifier, body.lease_token)
    expected = {row["id"] for row in run.scenario_snapshot}
    actual = {row.scenario_id for row in body.results}
    if expected != actual or len(actual) != len(body.results):
        fail(
            400,
            "incomplete_results",
            "Submit exactly one result for every scenario in the frozen run.",
        )
    for item in body.results:
        payload = redact(item.model_dump(mode="json"))
        payload["evidence"]["cost_measurement"] = (
            "reported"
            if "cost_usd" in item.model_fields_set
            and item.evidence.get("cost_measurement") != "unavailable"
            else "unavailable"
        )
        db.add(RunResult(workspace_id=p.workspace_id, run_id=run.id, **payload))
    run.status = "completed"
    run.completed_at = now()
    run.lease_token_hash = None
    run.lease_expires_at = None
    audit(db, p, "runs.completed", run.id)
    return {"ok": True}


@router.post("/runs/{identifier}/cancel")
def cancel(identifier: s.Identifier, p: Principal = Depends(require("run")), db: Session = DB):
    run = record(db, Run, p.workspace_id, identifier, lock=True)
    if run.status not in {"queued", "running"}:
        fail(409, "run_finished", "Only a queued or running evaluation can be cancelled.")
    run.status = "cancelled"
    run.completed_at = now()
    run.lease_token_hash = None
    run.lease_expires_at = None
    audit(db, p, "runs.cancelled", run.id)
    return {"ok": True}


@router.post("/releases", status_code=201)
def release(body: s.ReleaseInput, p: Principal = Depends(require("write")), db: Session = DB):
    candidate = record(db, Run, p.workspace_id, body.candidate_run_id)
    if candidate.project_id != body.project_id or candidate.revision != body.revision:
        fail(
            400, "candidate_mismatch", "The candidate must match the release project and revision."
        )
    if body.baseline_run_id:
        baseline = record(db, Run, p.workspace_id, body.baseline_run_id)
        if baseline.project_id != body.project_id or baseline.id == candidate.id:
            fail(
                400,
                "baseline_mismatch",
                "The baseline must be a different run in the same project.",
            )
    record(db, Policy, p.workspace_id, body.policy_id)
    return insert(db, p, Release, body)


@router.post("/releases/{identifier}/evaluate")
def evaluate_release(
    identifier: s.Identifier, p: Principal = Depends(require("write")), db: Session = DB
):
    release = record(db, Release, p.workspace_id, identifier, lock=True)
    policy = serialize(record(db, Policy, p.workspace_id, release.policy_id))
    candidate = load_run(db, p.workspace_id, release.candidate_run_id)
    baseline = (
        load_run(db, p.workspace_id, release.baseline_run_id) if release.baseline_run_id else None
    )
    approvals = [
        serialize(row)
        for row in db.scalars(
            select(Approval)
            .where(
                Approval.workspace_id == p.workspace_id,
                Approval.release_id == identifier,
                Approval.action == "release",
                Approval.status == "approved",
            )
            .order_by(Approval.created_at, Approval.id)
        )
    ]
    decision = evaluate(policy, candidate, baseline, bool(approvals))
    evidence = {
        "release": serialize(release),
        "policy": policy,
        "candidate": candidate,
        "baseline": baseline,
        "approvals": approvals,
        **decision,
    }
    row = ReleaseDecision(
        workspace_id=p.workspace_id,
        release_id=identifier,
        verdict=decision["verdict"],
        reasons=decision["reasons"],
        evidence=evidence,
        evidence_sha256=digest(canonical(evidence)),
    )
    db.add(row)
    db.flush()
    audit(db, p, "releases.evaluated", identifier)
    return serialize(row)


@router.get("/releases/{identifier}/evidence")
def evidence(
    identifier: s.Identifier,
    response: Response,
    p: Principal = Depends(require()),
    db: Session = DB,
):
    record(db, Release, p.workspace_id, identifier)
    decisions = [
        serialize(row)
        for row in db.scalars(
            select(ReleaseDecision)
            .where(
                ReleaseDecision.workspace_id == p.workspace_id,
                ReleaseDecision.release_id == identifier,
            )
            .order_by(ReleaseDecision.created_at.desc(), ReleaseDecision.id.desc())
        )
    ]
    if not decisions:
        fail(404, "not_evaluated", "Evaluate this release before exporting its evidence.")
    receipts = [
        serialize(row)
        for row in db.scalars(
            select(Receipt)
            .where(Receipt.workspace_id == p.workspace_id, Receipt.release_id == identifier)
            .order_by(Receipt.created_at, Receipt.id)
        )
    ]
    response.headers["content-disposition"] = (
        f'attachment; filename="lotuspond-release-{identifier}.json"'
    )
    return {
        "product": "Lotuspond",
        "schemaVersion": 2,
        "canonicalization": "sorted-json-utf8-v1",
        "decisions": decisions,
        "receipts": receipts,
    }


@router.post("/approvals", status_code=201)
def request_approval(
    body: s.ApprovalInput, p: Principal = Depends(require("write")), db: Session = DB
):
    if not p.user_id:
        fail(403, "human_required", "A signed-in person must request approval.")
    # Approvals apply to releases only (ADR-0013). Approving other exact actions is
    # planned for human tasks; until then nothing here could authorize them.
    if not body.release_id:
        fail(400, "release_required", "An approval request must name a release.")
    if body.action != "release":
        fail(400, "invalid_action", "Approvals apply to the release action only.")
    record(db, Release, p.workspace_id, body.release_id)
    row = Approval(workspace_id=p.workspace_id, requested_by=p.user_id, **body.model_dump())
    db.add(row)
    db.flush()
    audit(db, p, "approvals.requested", row.id)
    return serialize(row)


@router.post("/approvals/{identifier}/decide")
def decide(
    identifier: s.Identifier,
    body: s.ApprovalDecision,
    p: Principal = Depends(require("write")),
    db: Session = DB,
):
    require_admin(p)
    row = record(db, Approval, p.workspace_id, identifier, lock=True)
    if row.requested_by == p.user_id or row.status != "pending":
        fail(409, "approval_unavailable", "Approval is already decided or was requested by you.")
    row.status = body.status
    row.decided_by = p.user_id
    row.decided_at = now()
    audit(db, p, f"approvals.{body.status}", row.id)
    return serialize(row)
