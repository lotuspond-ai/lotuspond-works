import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from lotuspond.alerts import claim_alert, deliver_alert
from lotuspond.alerts import run_once as run_alert_once
from lotuspond.config import AlertWebhook, Settings
from lotuspond.models import AlertDelivery, AuditEvent, Workspace, now
from lotuspond.worker import run_once as run_evaluation_once


def configure_alert(settings):
    settings.alert_webhooks = [
        AlertWebhook(
            name="operator-ops",
            url="https://alerts.example.test/lotuspond",
            secret=SecretStr("a" * 32),
            enabled=True,
        )
    ]


async def test_failed_trace_and_unknown_voice_create_distinct_incidents(
    client, account, db_factory, settings
):
    project = client.post("/api/v1/projects", json={"name": "Incident project"}).json()
    monitor = client.post(
        "/api/v1/monitors",
        json={
            "project_id": project["id"],
            "name": "Answer contract",
            "criteria": "The answer must contain the expected marker.",
        },
    ).json()
    trace = client.post(
        "/api/v1/traces",
        json={
            "project_id": project["id"],
            "external_id": "failed-incident-trace",
            "name": "Agent answer",
            "status": "ok",
            "latency_ms": 10,
            "attributes": {
                "input": {"prompt": "Answer"},
                "output": {"text": "No marker"},
                "expected": {"contains": "required-marker"},
            },
        },
    ).json()
    failed_assessment_id = await run_evaluation_once(db_factory, settings)
    failed_rows = client.get(
        f"/api/v1/incidents?category=assessment_failed&project_id={project['id']}"
    ).json()["items"]
    assert len(failed_rows) == 1
    failed = failed_rows[0]
    assert failed["source"] == "monitor"
    assert failed["assessment_id"] == failed_assessment_id
    assert failed["trace_id"] == trace["id"]
    assert failed["monitor_id"] == monitor["id"]
    failed_detail = client.get(f"/api/v1/incidents/{failed['id']}").json()
    assert failed_detail["assessment"]["status"] == "failed"
    assert failed_detail["monitor"]["id"] == monitor["id"]
    assert [item["action"] for item in failed_detail["history"]] == ["created"]
    assert failed_detail["alerts"] == []

    voice = client.post(
        "/api/v1/voice/sessions",
        json={"project_id": project["id"], "name": "Support call", "provider": "test"},
    ).json()
    assert client.post(
        f"/api/v1/voice/sessions/{voice['id']}/segments",
        json={
            "segments": [
                {
                    "sequence": 0,
                    "speaker": "agent",
                    "text": "Hello",
                    "start_ms": 0,
                    "end_ms": 100,
                    "is_final": True,
                }
            ]
        },
    ).status_code == 202
    assert client.post(f"/api/v1/voice/sessions/{voice['id']}/close", json={}).status_code == 200
    queued = client.post(
        f"/api/v1/voice/sessions/{voice['id']}/assess",
        json={"criteria": "The agent must be helpful."},
    )
    assert queued.status_code == 202
    settings.network_mode = "airgap"
    unknown_assessment_id = await run_evaluation_once(db_factory, settings)
    unknown_rows = client.get("/api/v1/incidents?category=assessment_unavailable").json()[
        "items"
    ]
    assert len(unknown_rows) == 1
    unknown = unknown_rows[0]
    assert unknown["source"] == "voice_assessment"
    assert unknown["assessment_id"] == unknown_assessment_id
    assert unknown["voice_session_id"] == voice["id"]
    assert unknown["title"] == "Assessment unavailable"
    detail = client.get(f"/api/v1/incidents/{unknown['id']}").json()
    assert detail["voice_session"]["status"] == "closed"
    assert detail["trace"] is None


def test_manual_incident_resolution_and_reopen_are_audited(client, account, db_factory):
    created = client.post(
        "/api/v1/incidents",
        json={"title": "Operator investigation", "severity": "medium"},
    )
    assert created.status_code == 201
    incident = created.json()
    assert incident["category"] == "manual"
    assert incident["project_id"] is None
    resolved = client.post(
        f"/api/v1/incidents/{incident['id']}/resolve",
        json={"reason": "Reviewed the production evidence."},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    reopened = client.post(
        f"/api/v1/incidents/{incident['id']}/reopen",
        json={"reason": "New evidence requires another review."},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    detail = client.get(f"/api/v1/incidents/{incident['id']}").json()
    assert [item["action"] for item in detail["history"]] == [
        "created",
        "resolved",
        "reopened",
    ]
    assert detail["history"][1]["reason"] == "Reviewed the production evidence."
    with db_factory() as db:
        actions = list(
            db.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.resource_id == incident["id"])
                .order_by(AuditEvent.created_at)
            )
        )
        assert actions == ["incidents.created", "incidents.resolved", "incidents.reopened"]


async def test_alert_delivery_is_signed_metadata_only_and_reports_http_acceptance(
    client, account, app, db_factory, settings
):
    configure_alert(app.state.settings)
    response = client.post(
        "/api/v1/incidents",
        json={"title": "Do not send this title", "severity": "high"},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    observed = {}

    async def accept(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        observed["body"] = body
        observed["headers"] = request.headers
        return httpx.Response(204)

    assert await run_alert_once(
        db_factory,
        app.state.settings,
        transport=httpx.MockTransport(accept),
    ) is True
    payload = json.loads(observed["body"])
    encoded = observed["body"].decode()
    assert payload["event"] == "incident.opened"
    assert payload["incident"]["id"] == created["id"]
    assert "Do not send this title" not in encoded
    assert not any(word in encoded for word in ("reason", "prompt", "transcript", "output"))
    timestamp = observed["headers"]["x-lotuspond-timestamp"]
    expected = hmac.new(
        b"a" * 32,
        timestamp.encode() + b"." + observed["body"],
        hashlib.sha256,
    ).hexdigest()
    assert observed["headers"]["x-lotuspond-signature"] == f"v1={expected}"
    detail = client.get(f"/api/v1/incidents/{created['id']}").json()
    assert detail["alerts"][0]["destination"] == "operator-ops"
    assert detail["alerts"][0]["status"] == "delivered"
    assert "payload" not in detail["alerts"][0]


async def test_stale_alert_claim_cannot_overwrite_replacement_lease(
    client, account, app, db_factory, settings
):
    configure_alert(app.state.settings)
    response = client.post(
        "/api/v1/incidents", json={"title": "Lease test", "severity": "low"}
    )
    assert response.status_code == 201, response.text
    with db_factory.begin() as db:
        stale = claim_alert(db)
    with db_factory.begin() as db:
        db.get(AlertDelivery, stale.id).lease_expires_at = now() - timedelta(seconds=1)
    with db_factory.begin() as db:
        replacement = claim_alert(db)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(204)

    assert await deliver_alert(
        db_factory,
        app.state.settings,
        stale,
        transport=httpx.MockTransport(handler),
    ) is False
    assert calls == 0
    with db_factory() as db:
        delivery = db.get(AlertDelivery, replacement.id)
        assert delivery.status == "sending"
        assert delivery.lease_token_hash is not None


async def test_suspended_workspace_cancels_claim_without_http(
    client, account, app, db_factory, settings
):
    configure_alert(app.state.settings)
    response = client.post(
        "/api/v1/incidents", json={"title": "Suspend test", "severity": "low"}
    )
    assert response.status_code == 201, response.text
    with db_factory.begin() as db:
        claim = claim_alert(db)
        db.get(Workspace, account).status = "suspended"
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(204)

    assert await deliver_alert(
        db_factory,
        app.state.settings,
        claim,
        transport=httpx.MockTransport(handler),
    ) is False
    assert calls == 0
    with db_factory.begin() as db:
        delivery = db.get(AlertDelivery, claim.id)
        assert delivery.status == "cancelled"
        assert delivery.last_error_code == "workspace_suspended"
        db.get(Workspace, account).status = "active"


async def test_alert_http_failures_have_bounded_retry_semantics(
    client, account, app, db_factory
):
    configure_alert(app.state.settings)

    retryable = client.post(
        "/api/v1/incidents", json={"title": "Retry alert", "severity": "medium"}
    )
    assert retryable.status_code == 201, retryable.text

    async def unavailable(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"ignored provider response")

    assert await run_alert_once(
        db_factory,
        app.state.settings,
        transport=httpx.MockTransport(unavailable),
    ) is False
    with db_factory.begin() as db:
        delivery = db.scalar(
            select(AlertDelivery).where(AlertDelivery.incident_id == retryable.json()["id"])
        )
        assert delivery.status == "queued"
        assert delivery.attempts == 1
        assert delivery.last_error_code == "http_503"

    terminal = client.post(
        "/api/v1/incidents", json={"title": "Reject alert", "severity": "medium"}
    )
    assert terminal.status_code == 201, terminal.text
    with db_factory.begin() as db:
        first = db.scalar(
            select(AlertDelivery).where(AlertDelivery.incident_id == retryable.json()["id"])
        )
        first.available_at = now() + timedelta(hours=1)

    async def redirect(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://unapproved.example.test"})

    assert await run_alert_once(
        db_factory,
        app.state.settings,
        transport=httpx.MockTransport(redirect),
    ) is False
    with db_factory() as db:
        delivery = db.scalar(
            select(AlertDelivery).where(AlertDelivery.incident_id == terminal.json()["id"])
        )
        assert delivery.status == "failed"
        assert delivery.attempts == 1
        assert delivery.last_error_code == "redirect_refused"


def test_alert_configuration_requires_explicit_connected_https_opt_in():
    secret = "sentinel-alert-secret-which-is-long"
    with pytest.raises(ValidationError) as invalid_url:
        Settings(
            alert_webhooks=[
                {"name": "ops", "url": "http://example.test/hook", "secret": secret, "enabled": True}
            ]
        )
    assert secret not in str(invalid_url.value)
    with pytest.raises(ValidationError, match="airgap"):
        Settings(
            network_mode="airgap",
            alert_webhooks=[
                {
                    "name": "ops",
                    "url": "https://example.test/hook",
                    "secret": secret,
                    "enabled": True,
                }
            ],
        )
