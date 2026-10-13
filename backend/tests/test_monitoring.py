from __future__ import annotations

import asyncio
import gzip
import json
import unittest
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from lotuspond.app import create_app
from lotuspond.config import Settings
from lotuspond.database import engine_for, sessions_for
from lotuspond.evaluation.models import unknown_result
from lotuspond.models import (
    ApiKey,
    Assessment,
    Base,
    EvaluationJob,
    Membership,
    Project,
    User,
    VoiceSegment,
    Workspace,
    new_id,
    now,
)
from lotuspond.security import digest
from lotuspond.worker import claim_next_job, complete_job, run_once


class MonitoringApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            env="test",
            database_url="sqlite+pysqlite:///:memory:",
            origin="http://testserver",
            network_mode="airgap",
        )
        self.engine = engine_for(self.settings)
        Base.metadata.create_all(self.engine)
        self.sessions = sessions_for(self.engine)
        self.workspace_id = new_id()
        self.project_id = new_id()
        self.raw_key = "lp_" + "a" * 40
        with self.sessions() as db:
            user = User(
                id=new_id(),
                email="monitoring@example.com",
                name="Monitoring",
                password_hash="unused-in-api-key-test",
            )
            db.add(user)
            db.flush()
            workspace = Workspace(
                id=self.workspace_id,
                name="Monitoring workspace",
                slug="monitoring-workspace",
                owner_id=user.id,
            )
            db.add(workspace)
            db.flush()
            db.add(
                Membership(
                    workspace_id=self.workspace_id,
                    user_id=user.id,
                    role="owner",
                )
            )
            db.add(
                Project(
                    id=self.project_id,
                    workspace_id=self.workspace_id,
                    name="Production agent",
                    description="",
                )
            )
            db.add(
                ApiKey(
                    id=new_id(),
                    workspace_id=self.workspace_id,
                    name="test",
                    token_hash=digest(self.raw_key),
                    prefix=self.raw_key[:11],
                    scopes=["read", "write", "ingest"],
                    expires_at=now() + timedelta(days=1),
                )
            )
            db.commit()
        self.app = create_app(self.settings, engine=self.engine)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()
        self.headers = {
            "authorization": f"Bearer {self.raw_key}",
            "origin": self.settings.origin,
        }

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()

    def post(self, path: str, body: dict) -> object:
        return self.client.post(path, json=body, headers=self.headers)

    def post_gzip(self, path: str, body: dict, *, encoding: str = "gzip") -> object:
        return self.client.post(
            path,
            content=gzip.compress(json.dumps(body).encode()),
            headers={**self.headers, "content-type": "application/json", "content-encoding": encoding},
        )

    def test_otlp_accepts_gzip_within_the_same_budget(self) -> None:
        span = {
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "0a" * 16,
                                    "spanId": "0b" * 8,
                                    "name": "compressed span",
                                    "startTimeUnixNano": "1700000000000000000",
                                    "endTimeUnixNano": "1700000000100000000",
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        accepted = self.post_gzip(f"/api/v1/telemetry/otlp?project_id={self.project_id}", span)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertEqual(accepted.json()["accepted_spans"], 1)
        # An uppercase header is the same encoding.
        span["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] = "0c" * 16
        again = self.post_gzip(
            f"/api/v1/telemetry/otlp?project_id={self.project_id}", span, encoding="GZIP"
        )
        self.assertEqual(again.status_code, 202, again.text)

    def test_otlp_refuses_unsupported_encodings_and_decompression_bombs(self) -> None:
        path = f"/api/v1/telemetry/otlp?project_id={self.project_id}"
        brotli = self.client.post(
            path,
            content=b"{}",
            headers={**self.headers, "content-type": "application/json", "content-encoding": "br"},
        )
        self.assertEqual(brotli.status_code, 415)
        self.assertEqual(brotli.json()["error"], "unsupported_encoding")

        # Compressible padding stays small on the wire and large once expanded.
        bomb = {"resourceSpans": [], "pad": "a" * (self.settings.max_request_bytes + 1024)}
        compressed = gzip.compress(json.dumps(bomb).encode())
        self.assertLess(len(compressed), self.settings.max_request_bytes)
        oversized = self.client.post(
            path,
            content=compressed,
            headers={
                **self.headers,
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        )
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.json()["error"], "body_too_large")

        corrupt = self.client.post(
            path,
            content=b"not actually gzip",
            headers={
                **self.headers,
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        )
        self.assertEqual(corrupt.status_code, 400)
        self.assertEqual(corrupt.json()["error"], "invalid_body")

        trailing = self.client.post(
            path,
            content=gzip.compress(b'{"resourceSpans":[]}') + b"trailing",
            headers={
                **self.headers,
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        )
        self.assertEqual(trailing.status_code, 400)

    def test_otlp_still_refuses_a_plain_body_that_is_not_a_json_object(self) -> None:
        path = f"/api/v1/telemetry/otlp?project_id={self.project_id}"
        for body in (b"[]", b"not json"):
            response = self.client.post(
                path, content=body, headers={**self.headers, "content-type": "application/json"}
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["error"], "invalid_body")

    def test_monitor_samples_trace_and_worker_persists_deterministic_pass(self) -> None:
        monitor_response = self.post(
            "/api/v1/monitors",
            {
                "project_id": self.project_id,
                "name": "Release id check",
                "criteria": "Mention the release id",
                "sample_basis_points": 10000,
            },
        )
        self.assertEqual(monitor_response.status_code, 201, monitor_response.text)

        trace_body = {
            "project_id": self.project_id,
            "external_id": "trace-1",
            "name": "model response",
            "status": "ok",
            "latency_ms": 42,
            "attributes": {
                "input": {"prompt": "Summarize rel-42"},
                "output": {"text": "Release rel-42 is ready."},
                "expected": {"contains": "rel-42"},
            },
        }
        trace_response = self.post("/api/v1/traces", trace_body)
        self.assertEqual(trace_response.status_code, 201, trace_response.text)
        self.assertEqual(len(trace_response.json()["evaluation_job_ids"]), 1)
        job_id = trace_response.json()["evaluation_job_ids"][0]
        replay = self.post("/api/v1/traces", trace_body)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(replay.json()["id"], trace_response.json()["id"])
        self.assertEqual(replay.json()["evaluation_job_ids"], [])

        detail = self.client.get(
            f"/api/v1/traces/{trace_response.json()['id']}", headers=self.headers
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["trace"]["id"], trace_response.json()["id"])

        assessment_id = asyncio.run(run_once(self.sessions, self.settings))
        self.assertIsNotNone(assessment_id)
        with self.sessions() as db:
            assessment = db.get(Assessment, assessment_id)
            self.assertEqual(assessment.status, "passed")
            self.assertEqual(assessment.provider, "lotuspond-deterministic")
            self.assertEqual(assessment.provenance["job_id"], job_id)
            self.assertEqual(len(assessment.provenance["details"]["input_sha256"]), 64)
            job = db.scalar(select(EvaluationJob))
            self.assertEqual(job.status, "completed")
        polled = self.client.get(f"/api/v1/evaluation-jobs/{job_id}", headers=self.headers)
        self.assertEqual(polled.status_code, 200, polled.text)
        self.assertEqual(polled.json()["job"]["status"], "completed")
        self.assertEqual(polled.json()["assessments"][0]["id"], assessment_id)

    def test_voice_final_segments_are_immutable_and_airgap_assessment_is_unknown(self) -> None:
        response = self.post(
            "/api/v1/voice/sessions",
            {
                "project_id": self.project_id,
                "name": "Support call",
                "provider": "operator-stream",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        session_id = response.json()["id"]

        partial = {
            "segments": [
                {
                    "sequence": 0,
                    "speaker": "user",
                    "text": "hel",
                    "start_ms": 0,
                    "end_ms": 100,
                    "is_final": False,
                }
            ]
        }
        self.assertEqual(
            self.post(f"/api/v1/voice/sessions/{session_id}/segments", partial).status_code,
            202,
        )
        final = {
            "segments": [
                {
                    "sequence": 0,
                    "speaker": "user",
                    "text": "hello",
                    "start_ms": 0,
                    "end_ms": 200,
                    "is_final": True,
                }
            ]
        }
        self.assertEqual(
            self.post(f"/api/v1/voice/sessions/{session_id}/segments", final).status_code,
            202,
        )
        changed = {"segments": [{**final["segments"][0], "text": "changed"}]}
        immutable = self.post(f"/api/v1/voice/sessions/{session_id}/segments", changed)
        self.assertEqual(immutable.status_code, 409)

        closed = self.post(f"/api/v1/voice/sessions/{session_id}/close", {})
        self.assertEqual(closed.status_code, 200, closed.text)
        queued = self.post(
            f"/api/v1/voice/sessions/{session_id}/assess",
            {"criteria": "The agent handled the request well."},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        assessment_id = asyncio.run(run_once(self.sessions, self.settings))
        with self.sessions() as db:
            assessment = db.get(Assessment, assessment_id)
            self.assertEqual(assessment.status, "unknown")
            self.assertEqual(assessment.provenance["source"], "network-policy")
            revisions = list(
                db.scalars(
                    select(VoiceSegment)
                    .where(VoiceSegment.session_id == session_id)
                    .order_by(VoiceSegment.revision)
                )
            )
            self.assertEqual([item.revision for item in revisions], [0, 1])
            self.assertEqual([item.is_final for item in revisions], [False, True])

    def test_otlp_json_is_bounded_and_redacts_sensitive_attributes(self) -> None:
        response = self.post(
            f"/api/v1/telemetry/otlp?project_id={self.project_id}",
            {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {"key": "service.name", "value": {"stringValue": "agent"}},
                                {"key": "api_key", "value": {"stringValue": "secret"}},
                            ]
                        },
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "01" * 16,
                                        "spanId": "02" * 8,
                                        "name": "model.call",
                                        "startTimeUnixNano": "1000000000",
                                        "endTimeUnixNano": "1250000000",
                                        "status": {"code": 2},
                                    }
                                ]
                            }
                        ],
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["accepted_spans"], 1)
        traces = self.client.get("/api/v1/traces", headers=self.headers).json()["items"]
        self.assertEqual(traces[0]["status"], "error")
        resource = traces[0]["attributes"]["otel"]["resource"]
        self.assertEqual(resource["api_key"], "[redacted]")

    def test_stale_lease_cannot_complete_after_reclaim(self) -> None:
        trace_response = self.post(
            "/api/v1/traces",
            {
                "project_id": self.project_id,
                "external_id": "manual-trace",
                "name": "manual",
                "status": "ok",
                "latency_ms": 1,
            },
        )
        job_response = self.post(
            f"/api/v1/traces/{trace_response.json()['id']}/assess",
            {"criteria": "Be correct"},
        )
        self.assertEqual(job_response.status_code, 202)
        with self.sessions() as db:
            claim = claim_next_job(db, lease_seconds=60)
            db.commit()
        stale = claim.__class__(
            id=claim.id,
            workspace_id=claim.workspace_id,
            lease_token="incorrect-token",
            attempt=claim.attempt,
        )
        with self.sessions() as db:
            result = unknown_result(
                "unavailable",
                provider="test",
                source="test",
            )
            self.assertIsNone(complete_job(db, stale, result))

    def test_suspended_workspace_jobs_are_not_claimed(self) -> None:
        trace_response = self.post(
            "/api/v1/traces",
            {
                "project_id": self.project_id,
                "external_id": "suspended-trace",
                "name": "suspended",
                "status": "ok",
                "latency_ms": 1,
            },
        )
        queued = self.post(
            f"/api/v1/traces/{trace_response.json()['id']}/assess",
            {"criteria": "Be correct"},
        )
        self.assertEqual(queued.status_code, 202)
        with self.sessions() as db:
            workspace = db.get(Workspace, self.workspace_id)
            workspace.status = "suspended"
            db.commit()
        self.assertIsNone(asyncio.run(run_once(self.sessions, self.settings)))
        with self.sessions() as db:
            job = db.get(EvaluationJob, queued.json()["id"])
            self.assertEqual(job.status, "queued")
            self.assertEqual(db.scalar(select(Assessment)), None)


if __name__ == "__main__":
    unittest.main()
