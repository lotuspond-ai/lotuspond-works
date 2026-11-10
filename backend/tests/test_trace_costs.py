from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from lotuspond.models import Trace, new_id, now
from lotuspond.workflows import _utc_month_window


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


def stored_trace(account, external_id, cost, measurement, **values):
    return Trace(
        id=new_id(),
        workspace_id=account,
        external_id=external_id,
        name=external_id,
        status="ok",
        latency_ms=1,
        cost_usd=None if cost is None else Decimal(cost),
        cost_measurement=measurement,
        attributes={},
        occurred_at=now(),
        **values,
    )


def test_stored_trace_costs_keep_null_zero_precision_and_provenance(client, account, db_factory):
    """Ingestion is retired (ADR-0013); stored costs read back exactly as recorded."""
    empty = client.get("/api/v1/overview").json()
    assert empty["monthly_cost_usd"] is None
    assert empty["monthly_cost_counts"] == {"reported": 0, "unavailable": 0, "unknown": 0}
    project = client.post("/api/v1/projects", json={"name": "Cost project"}).json()
    with db_factory.begin() as db:
        rows = [
            stored_trace(account, "cost-unavailable", None, "unavailable", project_id=project["id"]),
            stored_trace(account, "cost-zero", "0", "reported"),
            stored_trace(account, "cost-precise", "0.123456", "reported"),
            stored_trace(account, "legacy-unknown-cost", "9.654321", "unknown"),
        ]
        db.add_all(rows)
        identifiers = {row.external_id: row.id for row in rows}

    unavailable = client.get(f"/api/v1/traces/{identifiers['cost-unavailable']}").json()["trace"]
    assert unavailable["cost_usd"] is None and unavailable["cost_measurement"] == "unavailable"
    zero = client.get(f"/api/v1/traces/{identifiers['cost-zero']}").json()["trace"]
    assert zero["cost_usd"] == 0 and zero["cost_measurement"] == "reported"
    precise = client.get(f"/api/v1/traces/{identifiers['cost-precise']}").json()["trace"]
    assert precise["cost_usd"] == 0.123456

    listed = client.get(f"/api/v1/traces?project_id={project['id']}").json()["items"]
    assert [item["external_id"] for item in listed] == ["cost-unavailable"]
    exported = client.get("/api/v1/traces/export").json()
    assert {item["external_id"] for item in exported["traces"]} == set(identifiers)
    assert exported["truncated"] is False and len(exported["export_sha256"]) == 64

    overview = client.get("/api/v1/overview").json()
    assert overview["monthly_cost_usd"] == 0.123456
    assert overview["monthly_cost_counts"] == {"reported": 2, "unavailable": 1, "unknown": 1}
