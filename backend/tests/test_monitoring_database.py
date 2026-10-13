from lotuspond.worker import run_once


async def test_database_monitor_job_and_voice_unknown(client, account, db_factory, settings):
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
    voice = client.post(
        "/api/v1/voice/sessions",
        json={"project_id": project["id"], "name": "Integration call", "provider": "local-test"},
    ).json()
    segment_path = f"/api/v1/voice/sessions/{voice['id']}/segments"
    segment = {
        "sequence": 0,
        "speaker": "agent",
        "text": "Hello",
        "start_ms": 0,
        "end_ms": 250,
        "is_final": True,
    }
    assert client.post(segment_path, json={"segments": [segment]}).status_code == 202
    assert (
        client.post(segment_path, json={"segments": [{**segment, "text": "Changed"}]}).status_code
        == 409
    )
    assert client.post(f"/api/v1/voice/sessions/{voice['id']}/close", json={}).status_code == 200
    queued = client.post(
        f"/api/v1/voice/sessions/{voice['id']}/assess", json={"criteria": "Was the agent helpful?"}
    ).json()
    assert await run_once(db_factory, settings)
    assessment = client.get(f"/api/v1/evaluation-jobs/{queued['id']}").json()["assessments"][0]
    assert assessment["status"] == "unknown"
    assert assessment["provenance"]["details"]["input_sha256"]
