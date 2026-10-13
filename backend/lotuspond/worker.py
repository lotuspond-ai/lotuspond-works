"""Durable evaluation-job leases and fail-closed assessment execution."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .evaluation import (
    CompositeEvaluator,
    DeterministicEvaluator,
    EvaluationInput,
    EvaluationProvenance,
    EvaluationResult,
    JevConfig,
    JevEvaluator,
    LiveAssessmentAdapter,
    TranscriptSegment,
)
from .evaluation.models import unknown_result
from .incident_service import destination_names, incident_for_assessment
from .models import (
    Assessment,
    EvaluationJob,
    Trace,
    VoiceSegment,
    VoiceSession,
    Workspace,
    new_id,
    now,
)
from .security import canonical, digest, new_token, utc
from .worker_observation import (
    close_observation_client,
    mark_poll,
    observation_client,
)


@dataclass(frozen=True)
class ClaimedEvaluationJob:
    id: str
    workspace_id: str
    lease_token: str
    attempt: int


class WorkspaceSuspendedError(RuntimeError):
    pass


class UnavailableSemanticEvaluator:
    def __init__(self, reason: str, source: str) -> None:
        self.reason = reason
        self.source = source

    async def evaluate(self, _: EvaluationInput) -> EvaluationResult:
        return unknown_result(
            self.reason,
            provider="lotuspond-policy",
            source=self.source,
        )


def _unknown_assessment(
    db: Session,
    job: EvaluationJob,
    reason: str,
    *,
    provider: str = "lotuspond-worker",
    source: str = "worker",
) -> Assessment:
    assessment = Assessment(
        id=new_id(),
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        voice_session_id=job.voice_session_id,
        trace_id=job.trace_id,
        status="unknown",
        score=None,
        criteria=job.criteria,
        reason=reason,
        provider=provider,
        model=None,
        provenance={"source": source, "job_id": job.id},
    )
    db.add(assessment)
    return assessment


def _finalize_exhausted(
    db: Session,
    *,
    max_attempts: int,
    alert_destinations: tuple[str, ...] = (),
) -> int:
    current = now()
    rows = db.scalars(
        select(EvaluationJob)
        .join(Workspace, Workspace.id == EvaluationJob.workspace_id)
        .where(
            Workspace.status == "active",
            EvaluationJob.status == "running",
            EvaluationJob.attempts >= max_attempts,
            EvaluationJob.lease_expires_at.is_not(None),
            EvaluationJob.lease_expires_at < current,
        )
        .with_for_update(skip_locked=True)
        .limit(100)
    )
    count = 0
    for job in rows:
        assessment = _unknown_assessment(db, job, "Evaluation lease attempts were exhausted.")
        db.flush()
        incident_for_assessment(db, job, assessment, destinations=alert_destinations)
        job.status = "failed"
        job.completed_at = current
        job.lease_expires_at = None
        job.lease_token_hash = None
        count += 1
    return count


def claim_next_job(
    db: Session,
    *,
    lease_seconds: int = 60,
    max_attempts: int = 3,
    alert_destinations: tuple[str, ...] = (),
) -> ClaimedEvaluationJob | None:
    """Atomically claim a queued job or reclaim an expired lease."""

    _finalize_exhausted(
        db,
        max_attempts=max_attempts,
        alert_destinations=alert_destinations,
    )
    current = now()
    claimable = or_(
        EvaluationJob.status == "queued",
        and_(
            EvaluationJob.status == "running",
            EvaluationJob.lease_expires_at.is_not(None),
            EvaluationJob.lease_expires_at < current,
        ),
    )
    job = db.scalar(
        select(EvaluationJob)
        .join(Workspace, Workspace.id == EvaluationJob.workspace_id)
        .where(
            claimable,
            EvaluationJob.attempts < max_attempts,
            Workspace.status == "active",
        )
        .order_by(EvaluationJob.created_at, EvaluationJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    token = new_token()
    job.status = "running"
    job.attempts += 1
    job.lease_token_hash = digest(token)
    job.lease_expires_at = current + timedelta(seconds=lease_seconds)
    db.flush()
    return ClaimedEvaluationJob(
        id=job.id,
        workspace_id=job.workspace_id,
        lease_token=token,
        attempt=job.attempts,
    )


def _latest_voice_segments(
    db: Session,
    workspace_id: str,
    session_id: str,
) -> list[VoiceSegment]:
    rows = db.scalars(
        select(VoiceSegment)
        .where(
            VoiceSegment.workspace_id == workspace_id,
            VoiceSegment.session_id == session_id,
        )
        .order_by(VoiceSegment.sequence, VoiceSegment.revision)
    )
    latest: dict[int, VoiceSegment] = {}
    for row in rows:
        latest[row.sequence] = row
    return [latest[key] for key in sorted(latest)]


def _trace_input(job: EvaluationJob, trace: Trace) -> EvaluationInput:
    attributes = trace.attributes if isinstance(trace.attributes, Mapping) else {}
    supplied_context = attributes.get("context")
    trace_context = {
        "external_id": trace.external_id,
        "name": trace.name,
        "status": trace.status,
        "latency_ms": trace.latency_ms,
        "cost_usd": float(trace.cost_usd) if trace.cost_usd is not None else None,
        "cost_measurement": trace.cost_measurement,
    }
    if supplied_context is None:
        context: object = {"trace": trace_context}
    elif isinstance(supplied_context, Mapping):
        context = {**supplied_context, "trace": trace_context}
    else:
        context = {"provided": supplied_context, "trace": trace_context}
    return EvaluationInput(
        criteria=job.criteria,
        input=attributes.get("input"),
        context=context,
        output=attributes.get("output", attributes),
        expected=attributes.get("expected"),
        modality="text",
    )


def _voice_input(
    job: EvaluationJob,
    session: VoiceSession,
    segments: list[VoiceSegment],
) -> EvaluationInput:
    transcript = tuple(
        TranscriptSegment(
            segment_id=f"{segment.sequence}:{segment.revision}",
            speaker=segment.speaker,
            text=segment.text,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            sequence=segment.sequence,
            is_final=segment.is_final,
        )
        for segment in segments
    )
    return EvaluationInput(
        criteria=job.criteria,
        input={"voice_session_id": session.id, "provider": session.provider},
        context={"session_status": session.status},
        output={"transcript_segments": len(transcript)},
        modality="voice",
        transcript=transcript,
    )


def load_evaluation_input(db: Session, claim: ClaimedEvaluationJob) -> EvaluationInput:
    job = db.scalar(
        select(EvaluationJob).where(
            EvaluationJob.id == claim.id,
            EvaluationJob.workspace_id == claim.workspace_id,
        )
    )
    if job is None:
        raise ValueError("evaluation job is missing")
    workspace = db.get(Workspace, job.workspace_id)
    if workspace is None or workspace.status != "active":
        raise WorkspaceSuspendedError("workspace is not active")
    if bool(job.trace_id) == bool(job.voice_session_id):
        raise ValueError("evaluation job must reference exactly one evidence source")
    if job.trace_id:
        trace = db.scalar(
            select(Trace).where(
                Trace.id == job.trace_id,
                Trace.workspace_id == job.workspace_id,
                Trace.project_id == job.project_id,
            )
        )
        if trace is None:
            raise ValueError("trace evidence is missing or outside the job project")
        return _trace_input(job, trace)
    session = db.scalar(
        select(VoiceSession).where(
            VoiceSession.id == job.voice_session_id,
            VoiceSession.workspace_id == job.workspace_id,
            VoiceSession.project_id == job.project_id,
        )
    )
    if session is None:
        raise ValueError("voice evidence is missing or outside the job project")
    return _voice_input(
        job,
        session,
        _latest_voice_segments(db, job.workspace_id, session.id),
    )


def evaluator_for(settings: Settings, evidence: EvaluationInput):
    deterministic = DeterministicEvaluator()
    if settings.network_mode == "airgap":
        semantic = UnavailableSemanticEvaluator(
            "Semantic evaluation is disabled in airgap mode.",
            "network-policy",
        )
    else:
        semantic = JevEvaluator(
            JevConfig(
                base_url=settings.typesafe_base_url,
                api_key=(
                    settings.typesafe_api_key.get_secret_value()
                    if settings.typesafe_api_key
                    else None
                ),
                model=settings.typesafe_model,
                timeout_seconds=settings.typesafe_timeout_seconds,
            )
        )
    evaluator = CompositeEvaluator(deterministic, semantic)
    return LiveAssessmentAdapter(evaluator) if evidence.modality == "voice" else evaluator


def _attach_evidence_provenance(
    result: EvaluationResult,
    evidence: EvaluationInput,
) -> EvaluationResult:
    serialized = evidence.model_dump(mode="json")
    evidence_sha256 = hashlib.sha256(canonical(serialized).encode()).hexdigest()
    details = {
        **result.provenance.details,
        "input_sha256": evidence_sha256,
        "transcript_segment_revisions": [segment.segment_id for segment in evidence.transcript],
    }
    provenance = EvaluationProvenance(
        source=result.provenance.source,
        request_id=result.provenance.request_id,
        details=details,
    )
    return result.model_copy(update={"provenance": provenance})


def complete_job(
    db: Session,
    claim: ClaimedEvaluationJob,
    result: EvaluationResult,
    *,
    alert_destinations: tuple[str, ...] = (),
) -> Assessment | None:
    current = now()
    job = db.scalar(
        select(EvaluationJob)
        .join(Workspace, Workspace.id == EvaluationJob.workspace_id)
        .where(
            EvaluationJob.id == claim.id,
            EvaluationJob.workspace_id == claim.workspace_id,
            EvaluationJob.status == "running",
            EvaluationJob.lease_token_hash == digest(claim.lease_token),
            Workspace.status == "active",
        )
        .with_for_update()
    )
    if job is None or job.lease_expires_at is None or utc(job.lease_expires_at) <= current:
        return None
    assessment = Assessment(
        id=new_id(),
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        voice_session_id=job.voice_session_id,
        trace_id=job.trace_id,
        status=result.status,
        score=result.score,
        criteria=job.criteria,
        reason=" ".join(result.reasons),
        provider=result.provider,
        model=result.model,
        provenance={
            **result.provenance.model_dump(mode="json"),
            "provider_version": result.version,
            "attempt": job.attempts,
            "job_id": job.id,
        },
    )
    db.add(assessment)
    db.flush()
    incident_for_assessment(db, job, assessment, destinations=alert_destinations)
    job.status = "completed"
    job.completed_at = current
    job.lease_expires_at = None
    job.lease_token_hash = None
    db.flush()
    return assessment


async def run_once(
    sessions: Callable[[], Session],
    settings: Settings,
    *,
    lease_seconds: int = 60,
    max_attempts: int = 3,
) -> str | None:
    with sessions() as db:
        claim = claim_next_job(
            db,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            alert_destinations=destination_names(settings),
        )
        db.commit()
    if claim is None:
        return None

    try:
        with sessions() as db:
            evidence = load_evaluation_input(db, claim)
        result = await evaluator_for(settings, evidence).evaluate(evidence)
        result = _attach_evidence_provenance(result, evidence)
    except WorkspaceSuspendedError:
        return None
    except (ValueError, ValidationError):
        result = unknown_result(
            "Evaluation evidence was invalid or incomplete.",
            provider="lotuspond-worker",
            source="worker",
        )
    except Exception:
        result = unknown_result(
            "Evaluation failed inside the worker.",
            provider="lotuspond-worker",
            source="worker",
        )

    with sessions() as db:
        assessment = complete_job(
            db,
            claim,
            result,
            alert_destinations=destination_names(settings),
        )
        db.commit()
        return assessment.id if assessment else None


async def run_worker(
    sessions: Callable[[], Session],
    settings: Settings,
    *,
    poll_seconds: float = 1.0,
    observation=None,
) -> None:
    while True:
        mark_poll(observation, "evaluation")
        completed = await run_once(sessions, settings)
        if completed is None:
            await asyncio.sleep(min(max(poll_seconds, 0.05), 60.0))


def main() -> None:
    from .database import engine_for, sessions_for

    settings = Settings()
    engine = engine_for(settings)
    observation = observation_client(settings)
    try:
        asyncio.run(run_worker(sessions_for(engine), settings, observation=observation))
    finally:
        close_observation_client(observation)
        engine.dispose()


if __name__ == "__main__":
    main()
