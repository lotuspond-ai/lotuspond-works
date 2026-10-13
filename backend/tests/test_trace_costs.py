from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from lotuspond.models import Trace, new_id, now
from lotuspond.worker import claim_next_job, load_evaluation_input
from lotuspond.workflows import _utc_month_window


def trace_body(external_id, *, cost="missing", project_id=None):
    body = {
        "external_id": external_id,
        "name": "Cost evidence",
        "status": "ok",
        "latency_ms": 1,
    }
    if project_id is not None:
        body["project_id"] = project_id
    if cost != "missing":
        body["cost_usd"] = cost
    return body


def test_utc_month_window_rolls_december_and_normalizes_offsets():
    reference = datetime(2027, 1, 1, 1, 30, tzinfo=timezone(timedelta(hours=2)))
    start, end = _utc_month_window(reference)
    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_overview_cost_window_excludes_adjacent_month_boundaries(
    client, account, db_factory
):
    month_start, next_month_start = _utc_month_window()
    with db_factory.begin() as db:
        for external_id, occurred_at, cost in (
            ("previous-month-cost", month_start - timedelta(microseconds=1), "10.000000"),
            ("current-month-cost", month_start, "1.000001"),
            ("next-month-cost", next_month_start, "20.000000"),
        ):
            db.add(
                Trace(
                    id=new_id(),
                    workspace_id=account,
                    external_id=external_id,
                    name=external_id,
                    status="ok",
                    latency_ms=1,
                    cost_usd=Decimal(cost),
                    cost_measurement="reported",
                    attributes={},
                    occurred_at=occurred_at,
                )
            )
    overview = client.get("/api/v1/overview").json()
    assert overview["monthly_cost_usd"] == 1.000001
    assert overview["monthly_cost_counts"] == {
        "reported": 1,
        "unavailable": 0,
        "unknown": 0,
    }


def test_trace_cost_null_zero_precision_idempotency_and_overview(client, account, db_factory):
    empty = client.get("/api/v1/overview").json()
    assert empty["monthly_cost_usd"] is None
    assert empty["monthly_cost_counts"] == {
        "reported": 0,
        "unavailable": 0,
        "unknown": 0,
    }

    project = client.post("/api/v1/projects", json={"name": "Cost project"}).json()
    unavailable_response = client.post(
        "/api/v1/traces",
        json=trace_body("cost-unavailable", project_id=project["id"]),
    )
    assert unavailable_response.status_code == 201
    unavailable = unavailable_response.json()
    assert unavailable["cost_usd"] is None
    assert unavailable["cost_measurement"] == "unavailable"
    replay = client.post(
        "/api/v1/traces",
        json=trace_body("cost-unavailable", cost=None, project_id=project["id"]),
    )
    assert replay.status_code == 201
    assert replay.json()["idempotent_replay"] is True

    zero_response = client.post("/api/v1/traces", json=trace_body("cost-zero", cost=0))
    assert zero_response.status_code == 201
    zero = zero_response.json()
    assert zero["cost_usd"] == 0
    assert zero["cost_measurement"] == "reported"
    assert client.post(
        "/api/v1/traces", json=trace_body("cost-zero", cost=0)
    ).json()["idempotent_replay"] is True
    assert (
        client.post("/api/v1/traces", json=trace_body("cost-zero")).status_code == 409
    )

    precise_response = client.post(
        "/api/v1/traces", json=trace_body("cost-precise", cost=0.123456)
    )
    assert precise_response.status_code == 201
    precise = precise_response.json()
    assert precise["cost_usd"] == 0.123456
    assert precise["cost_measurement"] == "reported"
    assert (
        client.post(
            "/api/v1/traces", json=trace_body("cost-overprecision", cost=0.1234567)
        ).status_code
        == 422
    )
    claimed_provenance = trace_body("cost-client-provenance", cost=1)
    claimed_provenance["cost_measurement"] = "unknown"
    assert client.post("/api/v1/traces", json=claimed_provenance).status_code == 422

    overview = client.get("/api/v1/overview").json()
    assert overview["monthly_cost_usd"] == 0.123456
    assert overview["monthly_cost_counts"] == {
        "reported": 2,
        "unavailable": 1,
        "unknown": 0,
    }

    queued = client.post(
        f"/api/v1/traces/{unavailable['id']}/assess",
        json={"criteria": "Assess the output."},
    )
    assert queued.status_code == 202
    with db_factory.begin() as db:
        claim = claim_next_job(db)
    with db_factory() as db:
        evidence = load_evaluation_input(db, claim)
    assert evidence.context["trace"]["cost_usd"] is None
    assert evidence.context["trace"]["cost_measurement"] == "unavailable"

    with db_factory.begin() as db:
        db.add(
            Trace(
                id=new_id(),
                workspace_id=account,
                external_id="legacy-unknown-cost",
                name="Legacy cost",
                status="ok",
                latency_ms=1,
                cost_usd=Decimal("9.654321"),
                cost_measurement="unknown",
                attributes={},
                occurred_at=now(),
            )
        )
    legacy_overview = client.get("/api/v1/overview").json()
    assert legacy_overview["monthly_cost_usd"] == 0.123456
    assert legacy_overview["monthly_cost_counts"] == {
        "reported": 2,
        "unavailable": 1,
        "unknown": 1,
    }


def test_otlp_cost_is_explicit_span_evidence_and_invalid_values_are_rejected(client, account):
    project = client.post("/api/v1/projects", json={"name": "OTLP costs"}).json()

    def span(trace_byte, span_byte, name, attributes=None):
        return {
            "traceId": trace_byte * 32,
            "spanId": span_byte * 16,
            "name": name,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1001000000",
            "attributes": attributes or [],
        }

    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            span("a", "b", "No reported cost"),
                            span(
                                "c",
                                "d",
                                "Reported cost",
                                [
                                    {
                                        "key": "lotuspond.cost_usd",
                                        "value": {"stringValue": "0.000001"},
                                    }
                                ],
                            ),
                            span(
                                "e",
                                "f",
                                "Invalid cost",
                                [
                                    {
                                        "key": "lotuspond.cost_usd",
                                        "value": {"stringValue": "not-a-decimal"},
                                    }
                                ],
                            ),
                        ]
                    }
                ]
            }
        ]
    }
    response = client.post(
        f"/api/v1/telemetry/otlp?project_id={project['id']}", json=payload
    )
    assert response.status_code == 202
    assert response.json()["accepted_spans"] == 2
    assert response.json()["rejected_spans"] == 1
    replay = client.post(
        f"/api/v1/telemetry/otlp?project_id={project['id']}", json=payload
    )
    assert replay.status_code == 202
    assert replay.json()["accepted_spans"] == 2
    assert replay.json()["rejected_spans"] == 1
    traces = client.get(f"/api/v1/traces?project_id={project['id']}").json()["items"]
    assert len(traces) == 2
    by_name = {item["name"]: item for item in traces}
    assert by_name["No reported cost"]["cost_usd"] is None
    assert by_name["No reported cost"]["cost_measurement"] == "unavailable"
    assert by_name["Reported cost"]["cost_usd"] == 0.000001
    assert by_name["Reported cost"]["cost_measurement"] == "reported"
    assert "Invalid cost" not in by_name
