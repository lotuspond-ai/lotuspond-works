"""Incident lifecycle helpers shared by API and evaluation workers."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AlertDelivery,
    Assessment,
    AuditEvent,
    EvaluationJob,
    Incident,
    IncidentEvent,
    new_id,
    now,
)


def destination_names(settings) -> tuple[str, ...]:
    return tuple(item.name for item in settings.alert_webhooks if item.enabled)


def _source(job: EvaluationJob) -> str:
    if job.monitor_id:
        return "monitor"
    if job.voice_session_id:
        return "voice_assessment"
    return "trace_assessment"


def record_incident_event(
    db: Session,
    incident: Incident,
    *,
    action: str,
    actor_id: str,
    reason: str | None,
    destinations: Iterable[str],
) -> IncidentEvent:
    created_at = now()
    event = IncidentEvent(
        id=new_id(),
        workspace_id=incident.workspace_id,
        incident_id=incident.id,
        action=action,
        actor_id=actor_id,
        reason=reason,
        created_at=created_at,
    )
    db.add(event)
    db.flush()
    event_name = {
        "created": "incident.opened",
        "resolved": "incident.resolved",
        "reopened": "incident.reopened",
    }[action]
    payload = {
        "version": "1",
        "event": event_name,
        "event_id": event.id,
        "occurred_at": created_at.isoformat(),
        "incident": {
            "id": incident.id,
            "workspace_id": incident.workspace_id,
            "project_id": incident.project_id,
            "assessment_id": incident.assessment_id,
            "monitor_id": incident.monitor_id,
            "trace_id": incident.trace_id,
            "voice_session_id": incident.voice_session_id,
            "category": incident.category,
            "source": incident.source,
            "severity": incident.severity,
            "status": incident.status,
        },
    }
    for destination in sorted(set(destinations)):
        db.add(
            AlertDelivery(
                id=new_id(),
                workspace_id=incident.workspace_id,
                incident_id=incident.id,
                incident_event_id=event.id,
                destination=destination,
                event=event_name,
                payload=payload,
                status="queued",
                attempts=0,
                available_at=created_at,
            )
        )
    return event


def incident_for_assessment(
    db: Session,
    job: EvaluationJob,
    assessment: Assessment,
    *,
    destinations: Iterable[str] = (),
) -> Incident | None:
    if assessment.status == "passed":
        return None
    existing = db.scalar(
        select(Incident).where(
            Incident.workspace_id == assessment.workspace_id,
            Incident.assessment_id == assessment.id,
        )
    )
    if existing:
        return existing
    category = (
        "assessment_failed" if assessment.status == "failed" else "assessment_unavailable"
    )
    incident = Incident(
        id=new_id(),
        workspace_id=job.workspace_id,
        project_id=job.project_id,
        trace_id=job.trace_id,
        voice_session_id=job.voice_session_id,
        assessment_id=assessment.id,
        monitor_id=job.monitor_id,
        title=("Agent assessment failed" if assessment.status == "failed" else "Assessment unavailable"),
        severity="high" if assessment.status == "failed" else "medium",
        category=category,
        source=_source(job),
        status="open",
    )
    db.add(incident)
    db.flush()
    record_incident_event(
        db,
        incident,
        action="created",
        actor_id="evaluation-worker",
        reason=None,
        destinations=destinations,
    )
    db.add(
        AuditEvent(
            workspace_id=job.workspace_id,
            actor_id="evaluation-worker",
            action="incidents.auto_created",
            resource_id=incident.id,
            details={"category": category, "source": incident.source},
        )
    )
    return incident
