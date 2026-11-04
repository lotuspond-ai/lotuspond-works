from lotuspond.worker import run_once


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
