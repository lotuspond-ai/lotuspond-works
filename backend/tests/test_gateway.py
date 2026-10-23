from __future__ import annotations

import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from time import monotonic
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from lotuspond import gateway
from lotuspond.app import create_app
from lotuspond.config import Settings
from lotuspond.database import engine_for, sessions_for
from lotuspond.models import ApiKey, Base, GatewayUsage, Trace, User, Workspace, new_id, now
from lotuspond.security import digest


def response_body(*, prompt_tokens=12, completion_tokens=5):
    return {
        "id": "provider-request-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "A bounded answer."},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


class GatewayApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            env="test",
            database_url="sqlite+pysqlite:///:memory:",
            origin="http://testserver",
            network_mode="connected",
            provider_url="https://models.example.test/v1",
            provider_key="operator-secret",
            provider_models=[
                {
                    "id": "operator-model",
                    "input_usd_per_million": "1",
                    "output_usd_per_million": "2",
                }
            ],
            provider_response_bytes=1024,
        )
        self.engine = engine_for(self.settings)
        Base.metadata.create_all(self.engine)
        self.sessions = sessions_for(self.engine)
        self.workspace_id = new_id()
        self.raw_key = "lp_" + "g" * 40
        self.read_key = "lp_" + "r" * 40
        with self.sessions() as db:
            user = User(
                id=new_id(),
                email="gateway@example.com",
                name="Gateway",
                password_hash="unused",
            )
            db.add(user)
            db.flush()
            db.add(
                Workspace(
                    id=self.workspace_id,
                    name="Gateway workspace",
                    slug="gateway-workspace",
                    owner_id=user.id,
                    monthly_budget_usd=Decimal("1"),
                )
            )
            db.flush()
            for raw, scopes in ((self.raw_key, ["gateway"]), (self.read_key, ["read"])):
                db.add(
                    ApiKey(
                        id=new_id(),
                        workspace_id=self.workspace_id,
                        name="test",
                        token_hash=digest(raw),
                        prefix=raw[:11],
                        scopes=scopes,
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

    def request_body(self):
        return {
            "model": "operator-model",
            "messages": [{"role": "user", "content": "Give a short answer."}],
            "max_tokens": 20,
            "temperature": 0,
            "stream": False,
        }

    def client_for(self, handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)

    def ledger(self):
        with self.sessions() as db:
            return list(db.query(GatewayUsage).all())

    def test_lists_allowlisted_models_and_requires_gateway_scope(self) -> None:
        response = self.client.get("/api/v1/gateway/models", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "object": "list",
                "data": [{"id": "operator-model", "object": "model"}],
                "configured": True,
            },
        )
        forbidden = self.client.get(
            "/api/v1/gateway/models",
            headers={**self.headers, "authorization": f"Bearer {self.read_key}"},
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_executes_non_streaming_completion_and_settles_validated_usage(self) -> None:
        seen = []

        def handler(request: httpx.Request):
            seen.append(request)
            self.assertEqual(request.url, "https://models.example.test/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer operator-secret")
            self.assertFalse(json.loads(request.content)["stream"])
            return httpx.Response(200, json=response_body())

        with patch.object(gateway, "provider_client", return_value=self.client_for(handler)):
            response = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "A bounded answer.")
        self.assertEqual(len(seen), 1)
        rows = self.ledger()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "settled")
        self.assertEqual(rows[0].prompt_tokens, 12)
        self.assertEqual(rows[0].completion_tokens, 5)
        self.assertEqual(rows[0].actual_usd, Decimal("0.000022"))
        self.assertEqual(rows[0].charged_usd, rows[0].actual_usd)

    def test_timeout_keeps_conservative_reservation(self) -> None:
        def handler(request: httpx.Request):
            raise httpx.ReadTimeout("test timeout", request=request)

        with patch.object(gateway, "provider_client", return_value=self.client_for(handler)):
            response = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "provider_unavailable")
        row = self.ledger()[0]
        self.assertEqual(row.status, "uncertain")
        self.assertEqual(row.charged_usd, row.reserved_usd)
        self.assertIsNone(row.actual_usd)

    def test_total_deadline_stops_slow_trickle_and_retains_reservation(self) -> None:
        class TrickleStream(httpx.AsyncByteStream):
            closed = False
            chunks = 0

            async def __aiter__(self):
                # Undeadlined, this drains 1,000 bytes (under the 1,024-byte limit) in 10 s.
                for _ in range(1000):
                    await asyncio.sleep(0.01)
                    self.chunks += 1
                    yield b" "

            async def aclose(self):
                self.closed = True

        stream = TrickleStream()
        self.app.state.settings.provider_timeout_seconds = 0.04
        client = self.client_for(lambda _: httpx.Response(200, stream=stream))
        started = monotonic()
        with patch.object(gateway, "provider_client", return_value=client):
            response = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        # Each chunk waits 10 ms of event-loop time, so the 40 ms total deadline admits at
        # most four however the runner schedules the process. The wall-clock bound only
        # rules out waiting for the drain; a stall elsewhere in the request must not fail it.
        self.assertLessEqual(stream.chunks, 4)
        self.assertLess(monotonic() - started, 5)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "provider_unavailable")
        self.assertTrue(stream.closed)
        self.assertTrue(client.is_closed)
        row = self.ledger()[0]
        self.assertEqual(row.status, "uncertain")
        self.assertEqual(row.charged_usd, row.reserved_usd)
        self.assertIsNone(row.actual_usd)

    def test_total_deadline_includes_waiting_for_response_headers(self) -> None:
        cancelled = False

        async def handler(_):
            nonlocal cancelled
            try:
                await asyncio.sleep(60)
            finally:
                cancelled = True

        self.app.state.settings.provider_timeout_seconds = 0.02
        client = self.client_for(handler)
        with patch.object(gateway, "provider_client", return_value=client):
            response = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 502)
        self.assertTrue(cancelled)
        self.assertTrue(client.is_closed)
        row = self.ledger()[0]
        self.assertEqual(row.status, "uncertain")
        self.assertEqual(row.charged_usd, row.reserved_usd)

    def test_malformed_usage_is_uncertain_and_never_refunded(self) -> None:
        def handler(_):
            return httpx.Response(200, json=response_body(prompt_tokens=1.5))

        with patch.object(gateway, "provider_client", return_value=self.client_for(handler)):
            response = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "provider_response_invalid")
        row = self.ledger()[0]
        self.assertEqual(row.status, "uncertain")
        self.assertEqual(row.charged_usd, row.reserved_usd)

    def test_known_usage_overrun_is_charged_in_full(self) -> None:
        with self.sessions() as db:
            workspace = db.get(Workspace, self.workspace_id)
            workspace.monthly_budget_usd = Decimal("0.020000")
            db.commit()

        def handler(_):
            return httpx.Response(
                200, json=response_body(prompt_tokens=10_000, completion_tokens=10_000)
            )

        with patch.object(gateway, "provider_client", return_value=self.client_for(handler)):
            response = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "provider_usage_exceeded_reservation")
        row = self.ledger()[0]
        self.assertEqual(row.status, "settled")
        self.assertEqual(row.error_code, "budget_overrun")
        self.assertEqual(row.actual_usd, Decimal("0.030000"))
        self.assertEqual(row.charged_usd, row.actual_usd)
        self.assertGreater(row.charged_usd, row.reserved_usd)
        with patch.object(gateway, "provider_client") as provider:
            blocked = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        self.assertEqual(blocked.status_code, 402)
        provider.assert_not_called()

    def test_response_limit_and_redirect_are_fail_closed(self) -> None:
        for provider_response, expected in (
            (httpx.Response(200, content=b"x" * 1025), "provider_response_too_large"),
            (
                httpx.Response(307, headers={"location": "https://other.example/v1"}),
                "provider_redirect_rejected",
            ),
        ):
            with self.subTest(expected=expected):
                with patch.object(
                    gateway,
                    "provider_client",
                    return_value=self.client_for(lambda _: provider_response),
                ):
                    response = self.client.post(
                        "/api/v1/gateway/chat/completions",
                        json=self.request_body(),
                        headers=self.headers,
                    )
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()["error"], expected)
        self.assertTrue(all(row.status == "uncertain" for row in self.ledger()))

    def test_budget_rejects_before_provider_execution(self) -> None:
        with self.sessions() as db:
            workspace = db.get(Workspace, self.workspace_id)
            workspace.monthly_budget_usd = Decimal("0.000010")
            db.commit()
        with patch.object(gateway, "provider_client") as provider:
            response = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["error"], "budget_exceeded")
        provider.assert_not_called()
        self.assertEqual(self.ledger(), [])

    def test_client_submitted_trace_cost_does_not_control_gateway_budget(self) -> None:
        with self.sessions() as db:
            db.add(
                Trace(
                    id=new_id(),
                    workspace_id=self.workspace_id,
                    project_id=None,
                    external_id="spoofed-client-cost",
                    name="Untrusted client trace",
                    status="ok",
                    latency_ms=1,
                    cost_usd=Decimal("999999"),
                    cost_measurement="unknown",
                    attributes={},
                )
            )
            db.commit()

        with patch.object(
            gateway,
            "provider_client",
            return_value=self.client_for(lambda _: httpx.Response(200, json=response_body())),
        ):
            response = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.ledger()[0].status, "settled")

    def test_airgap_and_non_finite_input_never_call_provider(self) -> None:
        self.app.state.settings.network_mode = "airgap"
        with patch.object(gateway, "provider_client") as provider:
            disabled = self.client.post(
                "/api/v1/gateway/chat/completions",
                json=self.request_body(),
                headers=self.headers,
            )
            invalid = self.client.post(
                "/api/v1/gateway/chat/completions",
                content=json.dumps(self.request_body()).replace(
                    '"temperature": 0', '"temperature": NaN'
                ),
                headers={**self.headers, "content-type": "application/json"},
            )
            infinity = self.client.post(
                "/api/v1/gateway/chat/completions",
                content=json.dumps(self.request_body()).replace(
                    '"temperature": 0', '"temperature": Infinity'
                ),
                headers={**self.headers, "content-type": "application/json"},
            )
            coerced_stream = self.client.post(
                "/api/v1/gateway/chat/completions",
                json={**self.request_body(), "stream": 0},
                headers=self.headers,
            )
        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(disabled.json()["error"], "network_disabled")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(infinity.status_code, 422)
        self.assertEqual(coerced_stream.status_code, 422)
        provider.assert_not_called()
        self.assertEqual(self.ledger(), [])


def test_provider_client_disables_proxy_environment_and_redirects():
    settings = Settings(env="test", database_url="sqlite://")
    with patch.object(httpx, "AsyncClient") as constructor:
        gateway.provider_client(settings)
    constructor.assert_called_once_with(
        timeout=httpx.Timeout(settings.provider_timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )


def test_workspace_budget_lock_compiles_for_postgresql():
    statement = gateway._workspace_lock("workspace")
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled


def test_postgresql_budget_reservations_serialize(engine, db_factory, account):
    if engine.dialect.name != "postgresql":
        pytest.skip("Set LOTUSPOND_PLATFORM_TEST_DATABASE_URL for the isolated PostgreSQL check")
    with db_factory() as db:
        workspace = db.get(Workspace, account)
        workspace.monthly_budget_usd = Decimal("1.000000")
        db.commit()

    barrier = Barrier(2)

    def reserve():
        barrier.wait()
        try:
            return gateway.reserve_usage(db_factory, account, "operator-model", Decimal("0.750000"))
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert results.count(402) == 1
    assert sum(isinstance(result, str) for result in results) == 1
