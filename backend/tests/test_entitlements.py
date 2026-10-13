import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from lotuspond import entitlements, retention
from lotuspond.entitlements import PLANS, Plan, month_start, plan_for
from lotuspond.models import AuditEvent, Trace, User, Workspace, now
from lotuspond.security import canonical, utc


def small_plan(monkeypatch, quota: int = 2, retention_days: int = 7):
    """Keep the ladder's shape while making the allowance reachable in a test."""
    monkeypatch.setitem(
        PLANS, "developer", Plan("developer", trace_retention_days=retention_days, monthly_trace_quota=quota)
    )


def trace_body(external_id: str, **overrides) -> dict:
    return {
        "external_id": external_id,
        "name": "Quota trace",
        "status": "ok",
        "latency_ms": 5,
        **overrides,
    }


def span(trace_id: str, span_id: str) -> dict:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "quota span",
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000000100000000",
    }


def test_the_ladder_is_static_and_unknown_tiers_fall_back_to_the_smallest():
    assert tuple(PLANS) == ("developer", "team", "enterprise")
    assert [PLANS[name].monthly_trace_quota for name in PLANS] == sorted(
        PLANS[name].monthly_trace_quota for name in PLANS
    )
    assert [PLANS[name].trace_retention_days for name in PLANS] == sorted(
        PLANS[name].trace_retention_days for name in PLANS
    )
    # A record written before the database constraint existed must not widen anything.
    assert plan_for("platinum").name == "developer"
    assert plan_for(None).name == "developer"
    assert plan_for("enterprise").name == "enterprise"


def test_the_monthly_allowance_is_enforced_and_a_replay_does_not_spend_it(
    client, account, app, monkeypatch
):
    small_plan(monkeypatch, quota=2)
    assert client.post("/api/v1/traces", json=trace_body("one")).status_code == 201
    replay = client.post("/api/v1/traces", json=trace_body("one"))
    assert replay.status_code == 201
    assert replay.json()["idempotent_replay"] is True
    assert client.post("/api/v1/traces", json=trace_body("two")).status_code == 201

    refused = client.post("/api/v1/traces", json=trace_body("three"))
    assert refused.status_code == 402
    assert refused.json()["error"] == "trace_quota_exceeded"
    billing = client.get("/api/v1/settings").json()["billing"]
    assert billing["usage"] == {"traces_this_month": 2, "traces_remaining": 0}
    assert billing["entitlements"]["monthly_trace_quota"] == 2
    assert billing["payment"]["status"] == "not_collected"


def test_a_backdated_span_cannot_be_ingested_outside_the_month_it_arrived(
    client, account, app, monkeypatch, db_factory
):
    small_plan(monkeypatch, quota=1)
    old = (month_start() - timedelta(days=40)).isoformat()
    assert client.post(
        "/api/v1/traces", json=trace_body("backdated", occurred_at=old)
    ).status_code == 201
    # The allowance counts server receipt, so the backdated trace still consumed it.
    refused = client.post("/api/v1/traces", json=trace_body("current"))
    assert refused.status_code == 402
    with db_factory() as db:
        stored = db.scalar(select(Trace).where(Trace.external_id == "backdated"))
        assert utc(stored.occurred_at) < month_start()
        assert utc(stored.created_at) >= month_start()


def test_otlp_reports_quota_rejections_separately_and_refuses_an_empty_result(
    client, account, app, monkeypatch
):
    small_plan(monkeypatch, quota=1)
    payload = {
        "resourceSpans": [
            {"scopeSpans": [{"spans": [span("0a" * 16, "0b" * 8), span("0c" * 16, "0d" * 8)]}]}
        ]
    }
    partial = client.post("/api/v1/telemetry/otlp", json=payload)
    assert partial.status_code == 202, partial.text
    body = partial.json()
    assert body["accepted_spans"] == 1
    assert body["quota_rejected_spans"] == 1
    assert body["rejected_spans"] == 0

    exhausted = client.post(
        "/api/v1/telemetry/otlp",
        json={"resourceSpans": [{"scopeSpans": [{"spans": [span("0e" * 16, "0f" * 8)]}]}]},
    )
    assert exhausted.status_code == 402
    assert exhausted.json()["error"] == "trace_quota_exceeded"


def test_only_an_operator_can_widen_a_subscription_and_the_change_is_audited(
    client, account, app, db_factory
):
    refused = client.patch(
        f"/api/v1/admin/workspaces/{account}/plan",
        json={"plan": "enterprise", "reason": "self-service upgrade attempt"},
    )
    assert refused.status_code == 403

    user_id = client.get("/api/v1/me").json()["user"]["id"]
    with db_factory.begin() as db:
        db.get(User, user_id).is_platform_admin = True
    granted = client.patch(
        f"/api/v1/admin/workspaces/{account}/plan",
        json={"plan": "enterprise", "reason": "provisioned for the pilot agreement"},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["plan"] == "enterprise"
    assert granted.json()["entitlements"]["monthly_trace_quota"] == PLANS["enterprise"].monthly_trace_quota
    with db_factory() as db:
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "admin.workspace_plan")
        )
        assert event.details["from"] == "developer"
        assert event.details["to"] == "enterprise"
        assert event.details["reason"] == "provisioned for the pilot agreement"
    assert client.get("/api/v1/settings").json()["billing"]["plan"] == "enterprise"


def test_an_unknown_tier_is_refused_by_the_api_and_the_database(client, account, db_factory):
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    with db_factory.begin() as db:
        db.get(User, user_id).is_platform_admin = True
    assert client.patch(
        f"/api/v1/admin/workspaces/{account}/plan",
        json={"plan": "platinum", "reason": "a tier that does not exist"},
    ).status_code == 422
    with db_factory() as db:
        with pytest.raises(IntegrityError):
            db.execute(
                Workspace.__table__.update()
                .where(Workspace.id == account)
                .values(plan="platinum")
            )
        db.rollback()


def test_every_plan_can_export_its_own_evidence_with_a_verifiable_digest(
    client, account, app, monkeypatch
):
    small_plan(monkeypatch, quota=5)
    assert client.post("/api/v1/traces", json=trace_body("exported")).status_code == 201
    exported = client.get("/api/v1/traces/export")
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    digest = payload.pop("export_sha256")
    assert payload["plan"] == "developer"
    assert [item["external_id"] for item in payload["traces"]] == ["exported"]
    assert payload["truncated"] is False
    assert digest == hashlib.sha256(canonical(payload).encode()).hexdigest()
    assert "attachment" in exported.headers["content-disposition"]


def test_retention_reports_before_it_deletes_and_refuses_an_oversized_run(
    client, account, app, settings, db_factory, monkeypatch
):
    small_plan(monkeypatch, quota=10, retention_days=7)
    for index in range(3):
        assert client.post("/api/v1/traces", json=trace_body(f"kept-{index}")).status_code == 201
    with db_factory.begin() as db:
        for index, trace in enumerate(db.scalars(select(Trace))):
            if index < 2:
                trace.created_at = now() - timedelta(days=30)
    reported = retention.run(settings, sessions=db_factory)
    assert reported["applied"] is False
    assert reported["expired_traces"] == 2
    assert reported["workspaces"][0]["plan"] == "developer"
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Trace)) == 3

    with pytest.raises(ValueError, match="ceiling"):
        retention.run(settings, apply=True, max_deletions=1, sessions=db_factory)
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Trace)) == 3

    applied = retention.run(settings, apply=True, sessions=db_factory)
    assert applied == {**applied, "applied": True, "deleted_traces": 2}
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Trace)) == 1
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "traces.retention_deleted")
        )
        assert event.details["deleted_traces"] == 2
        assert event.details["trace_retention_days"] == 7
        assert event.actor_id == "retention"


def test_retention_keeps_evidence_readable_until_it_is_actually_deleted(
    client, account, app, monkeypatch, db_factory
):
    small_plan(monkeypatch, quota=10, retention_days=1)
    assert client.post("/api/v1/traces", json=trace_body("aged")).status_code == 201
    with db_factory.begin() as db:
        db.scalar(select(Trace)).created_at = now() - timedelta(days=30)
    # A plan change never hides evidence; only the retention job removes it.
    listed = client.get("/api/v1/traces")
    assert listed.status_code == 200
    assert [item["external_id"] for item in listed.json()["items"]] == ["aged"]
    assert len(client.get("/api/v1/traces/export").json()["traces"]) == 1


def test_the_retention_cutoff_follows_the_plan():
    reference = now()
    assert entitlements.PLANS["developer"].retention_cutoff(reference) == reference - timedelta(days=7)
    assert entitlements.PLANS["enterprise"].retention_cutoff(reference) == reference - timedelta(
        days=365
    )
