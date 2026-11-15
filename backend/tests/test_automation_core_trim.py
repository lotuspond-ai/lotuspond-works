"""ADR-0013: retired assurance features refuse new work and keep stored records readable."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lotuspond.models import (
    AlertDelivery,
    Assessment,
    Incident,
    IncidentEvent,
    Receipt,
    Trace,
    User,
    new_id,
    now,
)

UNKNOWN = "00000000-0000-4000-8000-000000000000"


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("POST", "/api/v1/traces", 405),
        ("POST", "/api/v1/telemetry/otlp", 405),
        ("POST", f"/api/v1/traces/{UNKNOWN}/assess", 405),
        ("POST", "/api/v1/monitors", 405),
        ("GET", "/api/v1/monitors", 404),
        ("GET", f"/api/v1/evaluation-jobs/{UNKNOWN}", 404),
        ("POST", "/api/v1/incidents", 405),
        ("POST", f"/api/v1/incidents/{UNKNOWN}/resolve", 405),
        ("POST", f"/api/v1/incidents/{UNKNOWN}/reopen", 405),
        ("POST", "/api/v1/receipts", 405),
        ("POST", "/api/v1/integrations", 405),
        ("GET", "/api/v1/integrations", 404),
        # ADR-0013 step 3: v1 publishing; stored versions stay readable (test_packages).
        ("POST", f"/api/v1/projects/{UNKNOWN}/packages", 405),
        ("GET", "/api/v1/package-contracts", 404),
    ],
)
def test_retired_routes_refuse_requests(client, account, method, path, status):
    response = client.request(method, path, json={})
    assert response.status_code == status, response.text


def test_new_api_keys_cannot_take_the_retired_ingest_scope(client, account):
    refused = client.post("/api/v1/api-keys", json={"name": "Telemetry", "scopes": ["ingest"]})
    assert refused.status_code == 422
    accepted = client.post("/api/v1/api-keys", json={"name": "Reader", "scopes": ["read"]})
    assert accepted.status_code == 201, accepted.text


def test_approval_requests_apply_to_a_release_only(client, account):
    missing = client.post("/api/v1/approvals", json={"action": "release", "reason": "Ship it"})
    assert missing.status_code == 400
    assert missing.json()["error"] == "release_required"
    other = client.post(
        "/api/v1/approvals",
        json={"release_id": UNKNOWN, "action": "refund.create", "reason": "High-risk tool"},
    )
    assert other.status_code == 400
    assert other.json()["error"] == "invalid_action"
    unknown_release = client.post(
        "/api/v1/approvals",
        json={"release_id": UNKNOWN, "action": "release", "reason": "Ship it"},
    )
    assert unknown_release.status_code == 404


def store_history(db_factory, workspace_id, project_id):
    """Store a trace, its assessment, an incident with history and an alert, as before ADR-0013."""
    with db_factory.begin() as db:
        trace = Trace(
            id=new_id(),
            workspace_id=workspace_id,
            project_id=project_id,
            external_id="stored-trace",
            name="refund.create",
            status="ok",
            latency_ms=12,
            cost_usd=None,
            cost_measurement="unavailable",
            attributes={},
            occurred_at=now(),
        )
        db.add(trace)
        db.flush()
        assessment = Assessment(
            id=new_id(),
            workspace_id=workspace_id,
            project_id=project_id,
            trace_id=trace.id,
            status="unknown",
            criteria="Cites the refund policy",
            reason="No semantic evaluator is configured.",
            provider="semantic-evaluator",
        )
        db.add(assessment)
        db.flush()
        incident = Incident(
            id=new_id(),
            workspace_id=workspace_id,
            project_id=project_id,
            trace_id=trace.id,
            assessment_id=assessment.id,
            title="Assessment unavailable for refund.create",
            severity="medium",
            category="assessment_unavailable",
            source="trace_assessment",
            status="open",
        )
        db.add(incident)
        db.flush()
        event = IncidentEvent(
            id=new_id(),
            workspace_id=workspace_id,
            incident_id=incident.id,
            action="created",
            actor_id="worker",
        )
        db.add(event)
        db.flush()
        db.add(
            AlertDelivery(
                id=new_id(),
                workspace_id=workspace_id,
                incident_id=incident.id,
                incident_event_id=event.id,
                destination="operator",
                event="incident.opened",
                payload={"private": "never returned"},
                status="queued",
            )
        )
        return trace.id, incident.id


def test_stored_traces_and_incidents_stay_readable_within_their_workspace(
    app, client, account, db_factory
):
    project = client.post("/api/v1/projects", json={"name": "Refunds"}).json()
    trace_id, incident_id = store_history(db_factory, account, project["id"])

    trace = client.get(f"/api/v1/traces/{trace_id}").json()
    assert trace["trace"]["name"] == "refund.create"
    assert [item["status"] for item in trace["assessments"]] == ["unknown"]
    assert [item["id"] for item in client.get("/api/v1/traces").json()["items"]] == [trace_id]

    listed = client.get("/api/v1/incidents?status=open").json()["items"]
    assert [item["id"] for item in listed] == [incident_id]
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["incident"]["category"] == "assessment_unavailable"
    assert detail["trace"]["id"] == trace_id
    assert [item["action"] for item in detail["history"]] == ["created"]
    assert [item["status"] for item in detail["alerts"]] == ["queued"]
    assert "payload" not in detail["alerts"][0]

    with TestClient(app, headers={"origin": "http://testserver"}) as other:
        registered = other.post(
            "/api/v1/auth/register",
            json={
                "email": "other@example.com",
                "name": "Other",
                "password": "another-valid-password",
                "account_type": "individual",
            },
        )
        assert registered.status_code == 201, registered.text
        other.headers["x-lotuspond-workspace"] = registered.json()["workspaceId"]
        assert other.get(f"/api/v1/traces/{trace_id}").status_code == 404
        assert other.get(f"/api/v1/incidents/{incident_id}").status_code == 404
        assert other.get("/api/v1/incidents").json()["items"] == []


def test_summaries_no_longer_count_retired_incidents(client, account, db_factory):
    project = client.post("/api/v1/projects", json={"name": "Refunds"}).json()
    store_history(db_factory, account, project["id"])
    assert "open_incidents" not in client.get("/api/v1/overview").json()
    with db_factory.begin() as db:
        db.scalar(select(User).where(User.email == "owner@example.com")).is_platform_admin = True
    overview = client.get("/api/v1/admin/overview")
    assert overview.status_code == 200, overview.text
    assert "incidents" not in overview.json()
    assert overview.json()["projects"] == 1


def test_release_evidence_export_keeps_stored_receipts(client, account, db_factory):
    project = client.post("/api/v1/projects", json={"name": "Assistant"}).json()
    scenario = client.post(
        "/api/v1/scenarios",
        json={
            "project_id": project["id"],
            "name": "Greeting",
            "input": {"prompt": "Hello"},
            "expected": {"contains": "Hello"},
        },
    ).json()
    run = client.post(
        "/api/v1/runs",
        json={"project_id": project["id"], "name": "Candidate", "revision": "build-1"},
    ).json()
    runner = client.post("/api/v1/runners", json={"name": "Runner"}).json()
    claim = client.post(f"/api/v1/runners/{runner['id']}/claim", json={}).json()
    submitted = client.post(
        f"/api/v1/runs/{run['id']}/results",
        json={
            "lease_token": claim["lease_token"],
            "results": [
                {
                    "scenario_id": scenario["id"],
                    "passed": True,
                    "output": {"text": "Hello"},
                    "latency_ms": 5,
                }
            ],
        },
    )
    assert submitted.status_code == 200, submitted.text
    policy = client.post(
        "/api/v1/policies", json={"name": "No approval", "require_approval": False}
    ).json()
    release = client.post(
        "/api/v1/releases",
        json={
            "project_id": project["id"],
            "name": "Release",
            "revision": "build-1",
            "candidate_run_id": run["id"],
            "policy_id": policy["id"],
        },
    ).json()
    assert client.post(f"/api/v1/releases/{release['id']}/evaluate", json={}).status_code == 200
    with db_factory.begin() as db:
        db.add(
            Receipt(
                id=new_id(),
                workspace_id=account,
                release_id=release["id"],
                external_id="deploy-41",
                outcome="success",
                evidence={"pipeline": "stored before ADR-0013"},
            )
        )
    export = client.get(f"/api/v1/releases/{release['id']}/evidence").json()
    assert export["schemaVersion"] == 2
    assert [item["external_id"] for item in export["receipts"]] == ["deploy-41"]
    assert [item["external_id"] for item in client.get("/api/v1/receipts").json()["items"]] == [
        "deploy-41"
    ]
