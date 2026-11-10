from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from lotuspond import monitoring
from lotuspond.models import Trace
from lotuspond.worker import run_once


def otlp_span(trace_id: str, span_id: str, name: str) -> dict:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000000100000000",
    }


def otlp_export(*spans: dict) -> dict:
    return {"resourceSpans": [{"scopeSpans": [{"spans": list(spans)}]}]}


def hide_first_existing_trace(monkeypatch):
    missed = {"done": False}
    real = monitoring._existing_trace

    def hide_first(db, workspace_id, external_id):
        if not missed["done"]:
            missed["done"] = True
            return None
        return real(db, workspace_id, external_id)

    monkeypatch.setattr(monitoring, "_existing_trace", hide_first)


async def test_database_monitor_job_and_retired_voice_unknown(
    client, account, db_factory, settings, retired_voice_job
):
    settings.network_mode = "airgap"
    project = client.post("/api/v1/projects", json={"name": "Monitored assistant"}).json()
    monitor = client.post(
        "/api/v1/monitors",
        json={
            "project_id": project["id"],
            "name": "Check claims",
            "criteria": "The agent must ground factual claims.",
        },
    )
    assert monitor.status_code == 201
    trace = client.post(
        "/api/v1/traces",
        json={
            "project_id": project["id"],
            "external_id": "integration-trace",
            "name": "Answer",
            "status": "ok",
            "latency_ms": 40,
            "attributes": {"input": "Hello", "output": "Hello"},
        },
    ).json()
    assessment = await run_once(db_factory, settings)
    assert assessment
    job_id = trace["evaluation_job_ids"][0]
    job = client.get(f"/api/v1/evaluation-jobs/{job_id}").json()
    assert job["job"]["status"] == "completed"
    assert job["assessments"][0]["status"] == "unknown"
    assert await run_once(db_factory, settings) is None
    assert client.get("/api/v1/voice/sessions").status_code == 404
    _, job_id = retired_voice_job(account, project["id"], "Was the agent helpful?")
    assert await run_once(db_factory, settings)
    assessment = client.get(f"/api/v1/evaluation-jobs/{job_id}").json()["assessments"][0]
    assert assessment["status"] == "unknown"
    assert assessment["reason"] == "Voice evidence is no longer assessed."


def test_otlp_uniqueness_race_is_an_idempotent_replay(client, account, db_factory, monkeypatch):
    project = client.post("/api/v1/projects", json={"name": "OTLP uniqueness"}).json()
    path = f"/api/v1/telemetry/otlp?project_id={project['id']}"
    payload = otlp_export(otlp_span("0a" * 16, "0b" * 8, "shared"))
    first = client.post(path, json=payload)
    assert first.status_code == 202, first.text
    assert first.json()["accepted_spans"] == 1
    hide_first_existing_trace(monkeypatch)
    replay = client.post(path, json=payload)
    assert replay.status_code == 202, replay.text
    assert replay.json()["accepted_spans"] == 1
    assert replay.json()["rejected_spans"] == 0
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Trace)) == 1


def test_otlp_uniqueness_race_keeps_other_spans_in_the_batch(
    client, account, db_factory, monkeypatch
):
    project = client.post("/api/v1/projects", json={"name": "OTLP overlapping"}).json()
    path = f"/api/v1/telemetry/otlp?project_id={project['id']}"
    shared = otlp_span("0a" * 16, "0b" * 8, "shared")
    unique = otlp_span("0c" * 16, "0d" * 8, "unique")
    first = client.post(path, json=otlp_export(shared))
    assert first.status_code == 202, first.text
    hide_first_existing_trace(monkeypatch)
    overlapping = client.post(path, json=otlp_export(shared, unique))
    assert overlapping.status_code == 202, overlapping.text
    assert overlapping.json()["accepted_spans"] == 2
    assert overlapping.json()["rejected_spans"] == 0
    traces = client.get("/api/v1/traces", params={"project_id": project["id"]}).json()["items"]
    assert {item["name"] for item in traces} == {"shared", "unique"}
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Trace)) == 2


def test_otlp_conflicting_uniqueness_race_rejects_only_that_span(
    client, account, db_factory, monkeypatch
):
    project = client.post("/api/v1/projects", json={"name": "OTLP conflict"}).json()
    path = f"/api/v1/telemetry/otlp?project_id={project['id']}"
    original = otlp_span("0a" * 16, "0b" * 8, "original")
    unique = otlp_span("0c" * 16, "0d" * 8, "kept")
    first = client.post(path, json=otlp_export(original))
    assert first.status_code == 202, first.text
    hide_first_existing_trace(monkeypatch)
    conflict = client.post(
        path, json=otlp_export(otlp_span("0a" * 16, "0b" * 8, "changed"), unique)
    )
    assert conflict.status_code == 202, conflict.text
    assert conflict.json()["accepted_spans"] == 1
    assert conflict.json()["rejected_spans"] == 1
    traces = client.get("/api/v1/traces", params={"project_id": project["id"]}).json()["items"]
    by_name = {item["name"] for item in traces}
    assert by_name == {"original", "kept"}
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Trace)) == 2


@pytest.mark.parametrize("mode", ["identical", "overlapping", "conflict"])
def test_postgres_concurrent_otlp_replay(app, engine, client, account, db_factory, mode):
    if engine.dialect.name != "postgresql":
        pytest.skip("OTLP contention requires isolated PostgreSQL")
    project = client.post("/api/v1/projects", json={"name": "OTLP concurrent"}).json()
    path = f"/api/v1/telemetry/otlp?project_id={project['id']}"
    shared = otlp_span("aa" * 16, "bb" * 8, "shared")
    cookie = client.cookies.get("lotuspond_session")
    barrier = Barrier(2)

    def call(index):
        if mode == "identical":
            payload = otlp_export(shared)
        elif mode == "overlapping":
            unique_trace = "cc" * 16 if index else "dd" * 16
            unique_span = "e0" * 8 if index else "e1" * 8
            payload = otlp_export(shared, otlp_span(unique_trace, unique_span, f"unique-{index}"))
        elif mode == "conflict":
            payload = otlp_export(otlp_span("aa" * 16, "bb" * 8, f"name-{index}"))
        else:
            raise AssertionError(mode)
        with TestClient(
            app, headers={"origin": "http://testserver", "x-lotuspond-workspace": account}
        ) as caller:
            caller.cookies.set("lotuspond_session", cookie)
            barrier.wait(timeout=5)
            result = caller.post(path, json=payload)
            return result.status_code, result.json()

    insert_barrier = Barrier(2)
    attempts = []

    def contend(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO traces "):
            attempts.append(statement)
            if len(attempts) <= 2:
                insert_barrier.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", contend)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, [0, 1]))
    finally:
        event.remove(engine, "before_cursor_execute", contend)
    assert len(attempts) >= 2
    assert [status for status, _ in results] == [202, 202]
    bodies = [body for _, body in results]
    traces = client.get("/api/v1/traces", params={"project_id": project["id"]}).json()["items"]
    names = {item["name"] for item in traces}
    with db_factory() as db:
        stored = db.scalar(select(func.count()).select_from(Trace))
    if mode == "identical":
        assert sorted(body["accepted_spans"] for body in bodies) == [1, 1]
        assert [body["rejected_spans"] for body in bodies] == [0, 0]
        assert names == {"shared"}
        assert stored == 1
    elif mode == "overlapping":
        assert sorted(body["accepted_spans"] for body in bodies) == [2, 2]
        assert [body["rejected_spans"] for body in bodies] == [0, 0]
        assert names == {"shared", "unique-0", "unique-1"}
        assert stored == 3
    elif mode == "conflict":
        assert sorted(body["accepted_spans"] for body in bodies) == [0, 1]
        assert sorted(body["rejected_spans"] for body in bodies) == [0, 1]
        assert names <= {"name-0", "name-1"} and len(names) == 1
        assert stored == 1
    else:
        raise AssertionError(mode)
