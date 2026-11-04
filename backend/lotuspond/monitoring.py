"""Tenant-scoped production traces and monitor APIs."""

from __future__ import annotations

import hashlib
import json
import re
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import schemas as s
from .deps import DB, audit, record, require
from .models import (
    Assessment,
    EvaluationJob,
    Monitor,
    Project,
    Trace,
    new_id,
    now,
    serialize,
)
from .otlp_protobuf import ProtobufError, decode_trace_request, encode_trace_response
from .security import Principal, canonical, fail, redact

router = APIRouter(prefix="/api/v1")


def _project(db: Session, principal: Principal, identifier: str) -> Project:
    return record(db, Project, principal.workspace_id, identifier)


def _sampled(monitor: Monitor, trace: Trace) -> bool:
    if monitor.sample_basis_points <= 0:
        return False
    if monitor.sample_basis_points >= 10_000:
        return True
    digest = hashlib.sha256(f"{monitor.id}:{trace.external_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 10_000 < monitor.sample_basis_points


def _enqueue_monitors(db: Session, trace: Trace) -> list[str]:
    jobs: list[str] = []
    monitors = db.scalars(
        select(Monitor).where(
            Monitor.workspace_id == trace.workspace_id,
            Monitor.project_id == trace.project_id,
            Monitor.enabled.is_(True),
        )
    )
    for monitor in monitors:
        if not _sampled(monitor, trace):
            continue
        job = EvaluationJob(
            id=new_id(),
            workspace_id=trace.workspace_id,
            project_id=monitor.project_id,
            monitor_id=monitor.id,
            trace_id=trace.id,
            voice_session_id=None,
            criteria=monitor.criteria,
        )
        db.add(job)
        jobs.append(job.id)
    return jobs


def _create_trace(
    db: Session,
    principal: Principal,
    body: s.TraceInput,
) -> tuple[Trace, list[str], bool]:
    if body.project_id:
        _project(db, principal, body.project_id)
    cost_measurement = "reported" if body.cost_usd is not None else "unavailable"
    duplicate = db.scalar(
        select(Trace).where(
            Trace.workspace_id == principal.workspace_id,
            Trace.external_id == body.external_id,
        )
    )
    if duplicate:
        same = (
            duplicate.project_id == body.project_id
            and duplicate.name == body.name
            and duplicate.status == body.status
            and duplicate.latency_ms == body.latency_ms
            and duplicate.cost_usd == body.cost_usd
            and duplicate.cost_measurement == cost_measurement
            and duplicate.attributes == redact(body.attributes)
        )
        if body.occurred_at is not None:
            supplied_time = body.occurred_at.replace(tzinfo=body.occurred_at.tzinfo or UTC)
            stored_time = duplicate.occurred_at.replace(tzinfo=duplicate.occurred_at.tzinfo or UTC)
            same = same and supplied_time == stored_time
        if same:
            return duplicate, [], True
        fail(409, "trace_exists", "A trace with this external id already exists.")
    trace = Trace(
        id=new_id(),
        workspace_id=principal.workspace_id,
        project_id=body.project_id,
        external_id=body.external_id,
        name=body.name,
        status=body.status,
        latency_ms=body.latency_ms,
        cost_usd=body.cost_usd,
        cost_measurement=cost_measurement,
        attributes=redact(body.attributes),
        occurred_at=body.occurred_at or now(),
    )
    db.add(trace)
    db.flush()
    jobs = _enqueue_monitors(db, trace) if trace.project_id else []
    return trace, jobs, False


@router.get("/traces")
def traces(
    project_id: s.Identifier | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    query = select(Trace).where(Trace.workspace_id == principal.workspace_id)
    if project_id:
        _project(db, principal, project_id)
        query = query.where(Trace.project_id == project_id)
    rows = db.scalars(query.order_by(Trace.occurred_at.desc(), Trace.id.desc()).limit(limit))
    return {"items": [serialize(row) for row in rows]}


@router.post("/traces", status_code=201)
def create_trace(
    body: s.TraceInput,
    principal: Principal = Depends(require("ingest")),
    db: Session = DB,
):
    trace, jobs, replayed = _create_trace(db, principal, body)
    audit(db, principal, "traces.ingested", trace.id, {"evaluation_jobs": len(jobs)})
    return {
        **serialize(trace),
        "evaluation_job_ids": jobs,
        "idempotent_replay": replayed,
    }


EXPORT_LIMIT = 5000


@router.get("/traces/export")
def export_traces(
    response: Response,
    project_id: s.Identifier | None = None,
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    """Export retained traces as canonical JSON with its own digest."""
    query = select(Trace).where(Trace.workspace_id == principal.workspace_id)
    if project_id:
        _project(db, principal, project_id)
        query = query.where(Trace.project_id == project_id)
    rows = list(
        db.scalars(query.order_by(Trace.created_at, Trace.id).limit(EXPORT_LIMIT + 1))
    )
    truncated = len(rows) > EXPORT_LIMIT
    traces = [serialize(row) for row in rows[:EXPORT_LIMIT]]
    payload = {
        "product": "Lotuspond",
        "schemaVersion": 1,
        "canonicalization": "sorted-json-utf8-v1",
        "project_id": project_id,
        "traces": traces,
        "truncated": truncated,
    }
    response.headers["content-disposition"] = (
        'attachment; filename="lotuspond-traces.json"'
    )
    return {**payload, "export_sha256": hashlib.sha256(canonical(payload).encode()).hexdigest()}


@router.get("/traces/{identifier}")
def trace_detail(
    identifier: s.Identifier,
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    trace = record(db, Trace, principal.workspace_id, identifier)
    assessments = db.scalars(
        select(Assessment)
        .where(Assessment.workspace_id == principal.workspace_id, Assessment.trace_id == trace.id)
        .order_by(Assessment.created_at.desc())
    )
    return {
        "trace": serialize(trace),
        "assessments": [serialize(row) for row in assessments],
    }


@router.post("/traces/{identifier}/assess", status_code=202)
def assess_trace(
    identifier: s.Identifier,
    body: s.AssessmentInput,
    principal: Principal = Depends(require("write")),
    db: Session = DB,
):
    trace = record(db, Trace, principal.workspace_id, identifier)
    if not trace.project_id:
        fail(409, "project_required", "Assign the trace to a project before assessment.")
    job = EvaluationJob(
        id=new_id(),
        workspace_id=principal.workspace_id,
        project_id=trace.project_id,
        monitor_id=None,
        trace_id=trace.id,
        voice_session_id=None,
        criteria=body.criteria,
    )
    db.add(job)
    audit(db, principal, "traces.assessment_queued", job.id, {"trace_id": trace.id})
    db.flush()
    return serialize(job)


def _otlp_value(value: object) -> object:
    if not isinstance(value, Mapping):
        return None
    for key in (
        "stringValue",
        "boolValue",
        "intValue",
        "doubleValue",
        "bytesValue",
    ):
        if key in value:
            return value[key]
    if isinstance(value.get("arrayValue"), Mapping):
        values = value["arrayValue"].get("values", [])
        return [_otlp_value(item) for item in values[:100]] if isinstance(values, list) else []
    if isinstance(value.get("kvlistValue"), Mapping):
        values = value["kvlistValue"].get("values", [])
        return _otlp_attributes(values)
    return None


def _otlp_attributes(values: object) -> dict[str, object]:
    if not isinstance(values, list):
        return {}
    result: dict[str, object] = {}
    for item in values[:200]:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key or len(key) > 200:
            continue
        result[key] = _otlp_value(item.get("value"))
    return redact(result)


def _otlp_spans(
    payload: Mapping[str, object],
) -> Iterable[tuple[dict[str, object], dict[str, object]]]:
    resource_spans = payload.get("resourceSpans", [])
    if not isinstance(resource_spans, list):
        return
    emitted = 0
    for resource_entry in resource_spans:
        if not isinstance(resource_entry, Mapping):
            continue
        resource = resource_entry.get("resource", {})
        resource_attributes = _otlp_attributes(
            resource.get("attributes", []) if isinstance(resource, Mapping) else []
        )
        scopes = resource_entry.get("scopeSpans", [])
        if not isinstance(scopes, list):
            continue
        for scope_entry in scopes:
            if not isinstance(scope_entry, Mapping):
                continue
            spans = scope_entry.get("spans", [])
            if not isinstance(spans, list):
                continue
            for span in spans:
                if emitted >= 1_000:
                    return
                if isinstance(span, dict):
                    emitted += 1
                    yield span, resource_attributes


def _parse_nanos(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _otlp_cost(span: Mapping[str, object]) -> object | None:
    values = span.get("attributes", [])
    if not isinstance(values, list):
        return None
    matches = [
        item
        for item in values
        if isinstance(item, Mapping) and item.get("key") == "lotuspond.cost_usd"
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("duplicate cost attribute")
    wrapped = matches[0].get("value")
    if not isinstance(wrapped, Mapping):
        raise ValueError("invalid cost attribute")
    present = [key for key in ("stringValue", "doubleValue", "intValue") if key in wrapped]
    if len(present) != 1:
        raise ValueError("invalid cost attribute")
    value = wrapped[present[0]]
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("invalid cost attribute")
    return value


PROTOBUF = "application/x-protobuf"


@dataclass(frozen=True)
class OtlpExport:
    """A trace export in its OTLP/JSON form, and whether it arrived as protobuf."""

    payload: dict[str, Any]
    protobuf: bool


async def otlp_payload(request: Request) -> OtlpExport:
    """Accept what OTLP/HTTP exporters send by default: protobuf or JSON, often gzipped.

    A compressed body can expand far past its wire size, so the decompressed form is
    bounded explicitly instead of trusting the ratio a client happens to send. A protobuf
    body is decoded into the same OTLP/JSON form, so both encodings meet one validator.
    """
    encoding = request.headers.get("content-encoding", "").strip().lower()
    if encoding not in {"", "identity", "gzip"}:
        fail(415, "unsupported_encoding", "The request content encoding is not supported.")
    body = await request.body()
    if encoding == "gzip":
        limit = request.app.state.settings.max_request_bytes
        try:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decoded = decompressor.decompress(body, limit + 1)
            if len(decoded) > limit:
                fail(413, "body_too_large", "The request body exceeds the size limit.")
            if not decompressor.eof or decompressor.unused_data:
                fail(400, "invalid_body", "The compressed request body is not a single member.")
        except zlib.error:
            fail(400, "invalid_body", "The compressed request body could not be decoded.")
        body = decoded
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type == PROTOBUF:
        try:
            return OtlpExport(decode_trace_request(body), protobuf=True)
        except ProtobufError:
            fail(400, "invalid_body", "The request body is not a valid OTLP protobuf trace export.")
    try:
        value = json.loads(body)
    except ValueError:
        fail(400, "invalid_body", "The request body must be JSON.")
    if not isinstance(value, dict):
        fail(400, "invalid_body", "The request body must be a JSON object.")
    return OtlpExport(value, protobuf=False)


def _partial_success(rejected: int) -> str:
    if not rejected:
        return ""
    return f"Spans not stored: {rejected} malformed or conflicting with a recorded span."


@router.post("/telemetry/otlp", status_code=202)
def ingest_otlp(
    export: OtlpExport = Depends(otlp_payload),
    project_id: s.Identifier | None = None,
    principal: Principal = Depends(require("ingest")),
    db: Session = DB,
):
    if project_id:
        _project(db, principal, project_id)
    accepted = 0
    rejected = 0
    job_ids: list[str] = []
    for span, resource_attributes in _otlp_spans(export.payload):
        trace_id = span.get("traceId")
        span_id = span.get("spanId")
        name = span.get("name")
        if not all(isinstance(value, str) and value for value in (trace_id, span_id, name)):
            rejected += 1
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{32}", trace_id) or not re.fullmatch(
            r"[0-9a-fA-F]{16}", span_id
        ):
            rejected += 1
            continue
        start = _parse_nanos(span.get("startTimeUnixNano"))
        end = _parse_nanos(span.get("endTimeUnixNano"))
        if start is None or end is None or end < start:
            rejected += 1
            continue
        status = span.get("status", {})
        status_code = status.get("code") if isinstance(status, Mapping) else None
        is_error = status_code in {2, "2", "STATUS_CODE_ERROR"}
        attributes = {
            "otel": {
                "trace_id": trace_id[:160],
                "span_id": span_id[:160],
                "kind": span.get("kind"),
                "resource": resource_attributes,
                "span": _otlp_attributes(span.get("attributes", [])),
            }
        }
        external_id = f"{trace_id}:{span_id}"[:160]
        try:
            occurred_at = datetime.fromtimestamp(start / 1_000_000_000, UTC)
        except (OSError, OverflowError, ValueError):
            rejected += 1
            continue
        try:
            body = s.TraceInput(
                project_id=project_id,
                external_id=external_id,
                name=name[:160],
                status="error" if is_error else "ok",
                latency_ms=min((end - start) // 1_000_000, 86_400_000),
                cost_usd=_otlp_cost(span),
                attributes=attributes,
                occurred_at=occurred_at,
            )
        except (ValidationError, ValueError):
            rejected += 1
            continue
        try:
            trace, jobs, _ = _create_trace(db, principal, body)
        except Exception as error:
            # A duplicate span is rejected without exposing database details.
            if getattr(error, "status_code", None) == 409:
                rejected += 1
                continue
            raise
        accepted += 1
        job_ids.extend(jobs)
    if accepted == 0 and rejected == 0:
        fail(422, "invalid_otlp", "The payload did not contain any OTLP spans.")
    audit(
        db,
        principal,
        "telemetry.otlp_ingested",
        details={
            "encoding": "protobuf" if export.protobuf else "json",
            "accepted": accepted,
            "rejected": rejected,
            "evaluation_jobs": len(job_ids),
        },
    )
    if export.protobuf:
        # The standard OTLP answer: 200 with an ExportTraceServiceResponse, whose partial
        # success tells the exporter how many spans were not kept and why.
        return Response(
            encode_trace_response(rejected, _partial_success(rejected)),
            status_code=200,
            media_type=PROTOBUF,
        )
    return {
        "rejected_spans": rejected,
        "accepted_spans": accepted,
        "evaluation_job_ids": job_ids,
    }


@router.get("/evaluation-jobs/{identifier}")
def evaluation_job_detail(
    identifier: s.Identifier,
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    job = record(db, EvaluationJob, principal.workspace_id, identifier)
    candidates = db.scalars(
        select(Assessment)
        .where(
            Assessment.workspace_id == principal.workspace_id,
            Assessment.project_id == job.project_id,
            Assessment.created_at >= job.created_at,
            Assessment.provenance["job_id"].as_string() == job.id,
        )
        .order_by(Assessment.created_at.desc())
        .limit(10)
    )
    assessments = [
        row
        for row in candidates
        if isinstance(row.provenance, dict) and row.provenance.get("job_id") == job.id
    ]
    return {
        "job": serialize(job),
        "assessments": [serialize(row) for row in assessments],
    }


@router.get("/monitors")
def monitors(
    project_id: s.Identifier | None = None,
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    query = select(Monitor).where(Monitor.workspace_id == principal.workspace_id)
    if project_id:
        _project(db, principal, project_id)
        query = query.where(Monitor.project_id == project_id)
    rows = db.scalars(query.order_by(Monitor.created_at.desc()))
    return {"items": [serialize(row) for row in rows]}


@router.post("/monitors", status_code=201)
def create_monitor(
    body: s.MonitorInput,
    principal: Principal = Depends(require("write")),
    db: Session = DB,
):
    _project(db, principal, body.project_id)
    monitor = Monitor(
        id=new_id(),
        workspace_id=principal.workspace_id,
        **body.model_dump(),
    )
    db.add(monitor)
    audit(db, principal, "monitors.created", monitor.id)
    db.flush()
    return serialize(monitor)


@router.get("/monitors/{identifier}")
def monitor_detail(
    identifier: s.Identifier,
    principal: Principal = Depends(require()),
    db: Session = DB,
):
    return serialize(record(db, Monitor, principal.workspace_id, identifier))


@router.put("/monitors/{identifier}")
def update_monitor(
    identifier: s.Identifier,
    body: s.MonitorInput,
    principal: Principal = Depends(require("write")),
    db: Session = DB,
):
    monitor = record(db, Monitor, principal.workspace_id, identifier, lock=True)
    _project(db, principal, body.project_id)
    if body.project_id != monitor.project_id:
        fail(409, "monitor_project_immutable", "A monitor cannot move between projects.")
    for key, value in body.model_dump().items():
        setattr(monitor, key, value)
    audit(db, principal, "monitors.updated", monitor.id)
    return serialize(monitor)


@router.delete("/monitors/{identifier}", status_code=204)
def delete_monitor(
    identifier: s.Identifier,
    principal: Principal = Depends(require("write")),
    db: Session = DB,
):
    monitor = record(db, Monitor, principal.workspace_id, identifier, lock=True)
    jobs = db.scalar(
        select(func.count())
        .select_from(EvaluationJob)
        .where(
            EvaluationJob.workspace_id == principal.workspace_id,
            EvaluationJob.monitor_id == monitor.id,
        )
    )
    if jobs:
        fail(409, "monitor_has_evidence", "Disable monitors that already have evaluation evidence.")
    audit(db, principal, "monitors.deleted", monitor.id)
    db.delete(monitor)
