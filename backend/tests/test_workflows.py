from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from lotuspond import workflows
from lotuspond.models import Project, Run, Scenario, new_id, now
from lotuspond.security import canonical, digest


def setup_run(client, *, critical=True):
    project = client.post("/api/v1/projects", json={"name": "Assistant"}).json()
    scenario = client.post(
        "/api/v1/scenarios",
        json={
            "project_id": project["id"],
            "name": "Greeting",
            "input": {"prompt": "Hello"},
            "expected": {"contains": "Hello"},
            "critical": critical,
        },
    ).json()
    run = client.post(
        "/api/v1/runs",
        json={"project_id": project["id"], "name": "Candidate", "revision": "build-1"},
    ).json()
    runner = client.post(
        "/api/v1/runners", json={"name": "Test runner", "capabilities": ["text"]}
    ).json()
    claim = client.post(f"/api/v1/runners/{runner['id']}/claim", json={}).json()
    assert claim["run"]["id"] == run["id"]
    return project, scenario, run, claim


def results(client, scenario, run, claim, passed=True):
    return client.post(
        f"/api/v1/runs/{run['id']}/results",
        json={
            "lease_token": claim["lease_token"],
            "results": [
                {
                    "scenario_id": scenario["id"],
                    "passed": passed,
                    "output": {"text": "Hello", "api_key": "must-be-redacted"},
                    "latency_ms": 20,
                    "cost_usd": 0.001,
                }
            ],
        },
    )


def test_release_full_workflow_independent_approval(client, account, app):
    project, scenario, run, claim = setup_run(client)
    assert results(client, scenario, run, claim).status_code == 200
    assert results(client, scenario, run, claim).status_code == 409
    detail = client.get(f"/api/v1/runs/{run['id']}").json()
    assert detail["results"][0]["output"]["api_key"] == "[redacted]"
    policy = client.post(
        "/api/v1/policies", json={"name": "Default policy", "require_approval": True}
    ).json()
    release = client.post(
        "/api/v1/releases",
        json={
            "project_id": project["id"],
            "name": "First release",
            "revision": "build-1",
            "candidate_run_id": run["id"],
            "policy_id": policy["id"],
        },
    ).json()
    endpoint = f"/api/v1/releases/{release['id']}/evaluate"
    assert client.post(endpoint, json={}).json()["verdict"] == "approval_required"
    approval = client.post(
        "/api/v1/approvals",
        json={"release_id": release["id"], "action": "release", "reason": "Validate release"},
    ).json()
    assert (
        client.post(
            f"/api/v1/approvals/{approval['id']}/decide", json={"status": "approved"}
        ).status_code
        == 409
    )
    with TestClient(app, headers={"origin": "http://testserver"}) as reviewer:
        assert (
            reviewer.post(
                "/api/v1/auth/register",
                json={
                    "email": "reviewer@example.com",
                    "name": "Reviewer",
                    "password": "a-valid-test-password",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/v1/members", json={"email": "reviewer@example.com", "role": "admin"}
            ).status_code
            == 201
        )
        reviewer.headers["x-lotuspond-workspace"] = account
        assert (
            reviewer.post(
                f"/api/v1/approvals/{approval['id']}/decide", json={"status": "approved"}
            ).status_code
            == 200
        )
        assert (
            reviewer.post(
                f"/api/v1/approvals/{approval['id']}/decide", json={"status": "rejected"}
            ).status_code
            == 409
        )
    final = client.post(endpoint, json={}).json()
    assert final["verdict"] == "pass"
    assert final["evidence_sha256"] == digest(canonical(final["evidence"]))
    export = client.get(f"/api/v1/releases/{release['id']}/evidence").json()
    assert len(export["decisions"]) == 2
    assert export["schemaVersion"] == 2


def test_unknown_blocks_even_with_zero_required_pass_rate(client, account):
    project, scenario, run, claim = setup_run(client, critical=False)
    assert results(client, scenario, run, claim, None).status_code == 200
    policy = client.post(
        "/api/v1/policies",
        json={"name": "Permissive", "minimum_pass_rate": 0, "require_approval": False},
    ).json()
    release = client.post(
        "/api/v1/releases",
        json={
            "project_id": project["id"],
            "name": "Incomplete",
            "revision": "build-1",
            "candidate_run_id": run["id"],
            "policy_id": policy["id"],
        },
    ).json()
    assert (
        client.post(f"/api/v1/releases/{release['id']}/evaluate", json={}).json()["verdict"]
        == "block"
    )


def test_lease_and_exact_result_coverage(client, account):
    project, scenario, run, claim = setup_run(client)
    bad = {**claim, "lease_token": "incorrect-lease-token-with-required-length"}
    assert results(client, scenario, run, bad).status_code == 409
    duplicate = {"scenario_id": scenario["id"], "passed": True, "output": {}, "latency_ms": 0}
    assert (
        client.post(
            f"/api/v1/runs/{run['id']}/results",
            json={"lease_token": claim["lease_token"], "results": [duplicate, duplicate]},
        ).status_code
        == 400
    )
    assert client.post(f"/api/v1/runs/{run['id']}/cancel", json={}).status_code == 200
    assert results(client, scenario, run, claim).status_code == 409
    other = client.post("/api/v1/projects", json={"name": "Other"}).json()
    assert client.get(f"/api/v1/scenarios?project_id={other['id']}").json()["items"] == []


def test_run_snapshot_is_frozen_and_immutable(client, account):
    project, scenario, run, claim = setup_run(client)
    assert (
        client.post(
            "/api/v1/scenarios",
            json={
                "project_id": project["id"],
                "name": "Later addition",
                "input": {},
                "expected": {},
            },
        ).status_code
        == 201
    )
    assert len(client.get(f"/api/v1/runs/{run['id']}").json()["scenario_snapshot"]) == 1
    assert results(client, scenario, run, claim).status_code == 200


def test_run_snapshot_size_limit_uses_canonical_utf8_bytes(client, account, monkeypatch):
    project = client.post("/api/v1/projects", json={"name": "Snapshot boundary"}).json()
    scenario = client.post(
        "/api/v1/scenarios",
        json={
            "project_id": project["id"],
            "name": "Unicode payload",
            "input": {"prompt": "Lotus 池"},
            "expected": {"contains": "池"},
        },
    ).json()
    snapshot = [
        {
            key: scenario[key]
            for key in ("id", "name", "input", "expected", "critical", "modality")
        }
    ]
    exact_size = len(canonical(snapshot).encode("utf-8"))
    monkeypatch.setattr(workflows, "MAX_RUN_SNAPSHOT_BYTES", exact_size)
    accepted = client.post(
        "/api/v1/runs",
        json={"project_id": project["id"], "name": "Exact boundary", "revision": "one"},
    )
    assert accepted.status_code == 201, accepted.text

    monkeypatch.setattr(workflows, "MAX_RUN_SNAPSHOT_BYTES", exact_size - 1)
    rejected = client.post(
        "/api/v1/runs",
        json={"project_id": project["id"], "name": "Over boundary", "revision": "two"},
    )
    assert rejected.status_code == 413
    assert rejected.json()["error"] == "run_snapshot_too_large"


def test_run_snapshot_rejects_while_streaming_source_rows(
    client, db_factory, account, monkeypatch
):
    with db_factory() as db:
        project = Project(workspace_id=account, name="Streaming snapshot")
        db.add(project)
        db.flush()
        db.add_all(
            Scenario(
                workspace_id=account,
                project_id=project.id,
                name=f"Large {index}",
                input={"payload": "x" * 1000},
                expected={},
            )
            for index in range(50)
        )
        db.commit()
        project_id = project.id

    calls = 0
    original = workflows.serialize

    def observed(row, **kwargs):
        nonlocal calls
        if isinstance(row, Scenario):
            calls += 1
        return original(row, **kwargs)

    monkeypatch.setattr(workflows, "serialize", observed)
    monkeypatch.setattr(workflows, "MAX_RUN_SNAPSHOT_BYTES", 512)
    rejected = client.post(
        "/api/v1/runs",
        json={"project_id": project_id, "name": "Reject early", "revision": "one"},
    )
    assert rejected.status_code == 413
    assert rejected.json()["error"] == "run_snapshot_too_large"
    assert calls == 1


def test_capability_filter_skips_more_than_one_hundred_incompatible_runs(
    client, db_factory, account
):
    started = now()
    with db_factory() as db:
        voice_project = Project(workspace_id=account, name="Voice backlog")
        text_project = Project(workspace_id=account, name="Text work")
        db.add_all([voice_project, text_project])
        db.flush()
        for index in range(101):
            db.add(
                Run(
                    workspace_id=account,
                    project_id=voice_project.id,
                    name=f"Voice {index}",
                    revision="one",
                    scenario_snapshot=[
                        {
                            "id": new_id(),
                            "name": "Voice",
                            "input": {},
                            "expected": {},
                            "critical": False,
                            "modality": "voice",
                        }
                    ],
                    created_at=started,
                )
            )
        compatible = Run(
            workspace_id=account,
            project_id=text_project.id,
            name="Compatible text",
            revision="one",
            scenario_snapshot=[
                {
                    "id": new_id(),
                    "name": "Text",
                    "input": {},
                    "expected": {},
                    "critical": False,
                    "modality": "text",
                }
            ],
            created_at=started + timedelta(seconds=1),
        )
        db.add(compatible)
        db.commit()
        compatible_id = compatible.id

    runner = client.post(
        "/api/v1/runners", json={"name": "Text only", "capabilities": ["text"]}
    ).json()
    claim = client.post(f"/api/v1/runners/{runner['id']}/claim", json={})
    assert claim.status_code == 200, claim.text
    assert claim.json()["run"]["id"] == compatible_id


def test_capability_filter_requires_full_subset_and_empty_is_unrestricted(
    client, db_factory, account
):
    with db_factory() as db:
        project = Project(workspace_id=account, name="Mixed work")
        db.add(project)
        db.flush()
        run = Run(
            workspace_id=account,
            project_id=project.id,
            name="Mixed",
            revision="one",
            scenario_snapshot=[
                {
                    "id": new_id(),
                    "name": "Legacy text",
                    "input": {},
                    "expected": {},
                    "critical": False,
                },
                {
                    "id": new_id(),
                    "name": "Voice",
                    "input": {},
                    "expected": {},
                    "critical": False,
                    "modality": "voice",
                },
            ],
        )
        db.add(run)
        db.commit()
        run_id = run.id

    text_runner = client.post(
        "/api/v1/runners", json={"name": "Text only", "capabilities": ["text"]}
    ).json()
    incompatible = client.post(f"/api/v1/runners/{text_runner['id']}/claim", json={})
    assert incompatible.status_code == 200
    assert incompatible.json()["run"] is None

    unrestricted = client.post(
        "/api/v1/runners", json={"name": "Unrestricted", "capabilities": []}
    ).json()
    claimed = client.post(f"/api/v1/runners/{unrestricted['id']}/claim", json={})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["run"]["id"] == run_id


def test_policy_precision_cannot_silently_weaken_gate(client, account):
    for threshold in ("minimum_pass_rate", "maximum_regression"):
        response = client.post("/api/v1/policies", json={"name": "Overprecise", threshold: 0.50004})
        assert response.status_code == 422
    response = client.post(
        "/api/v1/policies", json={"name": "Exact threshold", "minimum_pass_rate": 0.5001}
    )
    assert response.status_code == 201, response.text
    assert response.json()["minimum_pass_rate"] == 0.5001


@pytest.mark.parametrize(
    ("cost_fields", "marker", "verdict", "metric"),
    [
        ({}, "unavailable", "block", None),
        ({"evidence": {"cost_measurement": "reported"}}, "unavailable", "block", None),
        (
            {"cost_usd": 0, "evidence": {"cost_measurement": "unavailable"}},
            "unavailable",
            "block",
            None,
        ),
        ({"cost_usd": 0}, "reported", "pass", 0),
        ({"cost_usd": 0.001}, "reported", "pass", 0.001),
    ],
)
def test_result_cost_provenance_controls_release_gate(
    client, account, cost_fields, marker, verdict, metric
):
    project, scenario, run, claim = setup_run(client)
    response = client.post(
        f"/api/v1/runs/{run['id']}/results",
        json={
            "lease_token": claim["lease_token"],
            "results": [
                {
                    "scenario_id": scenario["id"],
                    "passed": True,
                    "output": {},
                    "latency_ms": 20,
                    **cost_fields,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    saved = client.get(f"/api/v1/runs/{run['id']}").json()["results"][0]
    assert saved["evidence"]["cost_measurement"] == marker
    policy = client.post(
        "/api/v1/policies",
        json={"name": "Measured cost", "maximum_cost_usd": 1, "require_approval": False},
    ).json()
    release = client.post(
        "/api/v1/releases",
        json={
            "project_id": project["id"],
            "name": "Cost gate",
            "revision": "build-1",
            "candidate_run_id": run["id"],
            "policy_id": policy["id"],
        },
    ).json()
    decision = client.post(f"/api/v1/releases/{release['id']}/evaluate", json={}).json()
    assert decision["verdict"] == verdict
    assert decision["evidence"]["metrics"]["costUsd"] == metric
