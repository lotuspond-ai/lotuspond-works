from __future__ import annotations

import asyncio
import gzip
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx2
from pydantic import ValidationError
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from lotuspond.evaluation import EvaluationInput, JevConfig, JevEvaluator, TranscriptSegment, jev


class ProviderStream(httpx2.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay: float = 0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


class FakeClient:
    def __init__(self, response: object = None, error: Exception | None = None, **_: Any) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def system_one(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class JevEvaluatorTests(unittest.IsolatedAsyncioTestCase):
    def config(self, **overrides: object) -> JevConfig:
        return JevConfig(
            base_url="https://api.typesafe.ai",
            api_key="operator-secret",
            **overrides,
        )

    async def evaluate_probability(self, probability: float) -> tuple[object, FakeClient]:
        client = FakeClient(
            response=SimpleNamespace(
                model="jev-1.13.0",
                request_id="req-1",
                nouls={"meets_criteria": SimpleNamespace(noul=probability)},
            )
        )
        evaluator = JevEvaluator(self.config(), client_factory=lambda **_: client)
        result = await evaluator.evaluate(
            EvaluationInput(
                criteria="The agent completes the requested action safely.",
                input={"request": "update a profile"},
                context={"policy": "require confirmation"},
                output={"confirmed": True},
            )
        )
        return result, client

    async def test_probability_maps_to_explicit_verdict_bands(self) -> None:
        passed, _ = await self.evaluate_probability(0.91)
        failed, _ = await self.evaluate_probability(0.08)
        uncertain, _ = await self.evaluate_probability(0.5)
        self.assertEqual(passed.status, "passed")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(uncertain.status, "unknown")
        self.assertEqual(passed.version, "jev-1.13.0")

    async def test_full_evidence_and_official_question_shape_are_sent(self) -> None:
        _, client = await self.evaluate_probability(0.9)
        call = client.calls[0]
        state = call["state"]
        questions = call["questions"]
        self.assertEqual(state["output"], {"confirmed": True})
        self.assertEqual(questions["meets_criteria"]["type"], "noul")
        self.assertIn("criteria", questions["meets_criteria"]["instructions"])

    async def test_unconfigured_adapter_does_not_create_client(self) -> None:
        called = False

        def factory(**_: object) -> object:
            nonlocal called
            called = True
            raise AssertionError("must not be called")

        result = await JevEvaluator(JevConfig(), client_factory=factory).evaluate(
            EvaluationInput(criteria="Be correct", output="value")
        )
        self.assertEqual(result.status, "unknown")
        self.assertFalse(called)

    def test_provider_url_must_be_https_without_credentials(self) -> None:
        for value in (
            "http://api.typesafe.ai",
            "https://user:secret@api.typesafe.ai",
            "https://api.typesafe.ai?token=secret",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    JevConfig(base_url=value, api_key="key")

    async def test_timeout_and_provider_error_are_unknown(self) -> None:
        for error in (TimeoutError(), RuntimeError("provider body must stay private")):
            client = FakeClient(error=error)
            result = await JevEvaluator(self.config(), client_factory=lambda **_: client).evaluate(
                EvaluationInput(criteria="Be correct", output="value")
            )
            self.assertEqual(result.status, "unknown")
            self.assertNotIn("provider body", " ".join(result.reasons))

    async def test_total_deadline_also_cancels_injected_sdk_client(self) -> None:
        class SlowClient(FakeClient):
            cancelled = False

            async def system_one(self, **kwargs):
                try:
                    await asyncio.sleep(60)
                finally:
                    self.cancelled = True

        client = SlowClient()
        result = await JevEvaluator(
            self.config(timeout_seconds=0.02), client_factory=lambda **_: client
        ).evaluate(EvaluationInput(criteria="Be correct", output="value"))
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.provenance.details["failure"], "timeout")
        self.assertTrue(client.cancelled)

    async def evaluate_default_transport(self, handler, **config):
        client = jev._BoundedJevHTTPClient(
            transport=httpx2.MockTransport(handler), trust_env=False, follow_redirects=False
        )
        with patch.object(jev, "_jev_http_client", return_value=client):
            result = await JevEvaluator(self.config(**config)).evaluate(
                EvaluationInput(criteria="Be correct", output="value")
            )
        self.assertTrue(client.is_closed)
        return result

    async def test_default_sdk_client_rejects_redirect_without_second_request(self) -> None:
        requests = []
        stream = ProviderStream([b"private provider body"])

        def handler(request):
            requests.append(request)
            return httpx2.Response(
                307, headers={"location": "https://other.example.test"}, stream=stream
            )

        result = await self.evaluate_default_transport(handler)
        self.assertEqual(result.status, "unknown")
        self.assertEqual(len(requests), 1)
        self.assertTrue(stream.closed)
        self.assertNotIn("private", " ".join(result.reasons))

    async def test_default_sdk_client_limits_streamed_response_before_parsing(self) -> None:
        stream = ProviderStream([b"x" * (1024 * 1024)] * 2 + [b"x"])
        result = await self.evaluate_default_transport(
            lambda _: httpx2.Response(200, stream=stream)
        )
        self.assertEqual(result.status, "unknown")
        self.assertTrue(stream.closed)

    async def test_response_bound_applies_to_decompressed_bytes(self) -> None:
        stream = ProviderStream([gzip.compress(b" " * (jev.MAX_RESPONSE_BYTES + 1))])
        result = await self.evaluate_default_transport(
            lambda _: httpx2.Response(200, stream=stream, headers={"content-encoding": "gzip"})
        )
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.provenance.details["failure"], "provider_error")
        self.assertTrue(stream.closed)

    async def test_default_sdk_client_decodes_compressed_response_once(self) -> None:
        payload = json.dumps(
            {
                "model": "jev-1.13.0",
                "answers": {"meets_criteria": {"type": "noul", "noul": 0.92}},
                "usage": {"input_tokens": 12, "output_tokens": 2},
            }
        ).encode()
        stream = ProviderStream([gzip.compress(payload)])
        result = await self.evaluate_default_transport(
            lambda _: httpx2.Response(
                200,
                stream=stream,
                headers={"content-encoding": "gzip", "x-typesafe-request-id": "req-gzip"},
            )
        )
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.provenance.request_id, "req-gzip")
        self.assertTrue(stream.closed)

    async def test_total_deadline_stops_slow_response_and_closes_stream(self) -> None:
        stream = ProviderStream([b" "] * 100, delay=0.01)
        started = asyncio.get_running_loop().time()
        result = await self.evaluate_default_transport(
            lambda _: httpx2.Response(200, stream=stream), timeout_seconds=0.04
        )
        self.assertLess(asyncio.get_running_loop().time() - started, 0.5)
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.provenance.details["failure"], "timeout")
        self.assertTrue(stream.closed)

    async def test_total_deadline_includes_waiting_for_response_headers(self) -> None:
        cancelled = False

        async def handler(_):
            nonlocal cancelled
            try:
                await asyncio.sleep(60)
            finally:
                cancelled = True

        result = await self.evaluate_default_transport(handler, timeout_seconds=0.02)
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.provenance.details["failure"], "timeout")
        self.assertTrue(cancelled)

    async def test_default_sdk_does_not_retry_rejected_request(self) -> None:
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(503, json={"error": "private provider error"})

        result = await self.evaluate_default_transport(handler)
        self.assertEqual(result.status, "unknown")
        self.assertEqual(len(requests), 1)

    def test_http_client_disables_ambient_proxies_and_redirects(self) -> None:
        with patch.object(jev, "_BoundedJevHTTPClient") as constructor:
            jev._jev_http_client(15)
        constructor.assert_called_once_with(
            timeout=httpx2.Timeout(15), follow_redirects=False, trust_env=False
        )

    async def test_partial_voice_transcript_never_calls_provider(self) -> None:
        called = False

        def factory(**_: object) -> object:
            nonlocal called
            called = True
            return FakeClient()

        result = await JevEvaluator(self.config(), client_factory=factory).evaluate(
            EvaluationInput(
                criteria="Call was handled well",
                modality="voice",
                transcript=(
                    TranscriptSegment(
                        segment_id="one",
                        speaker="caller",
                        text="hello",
                        is_final=False,
                    ),
                ),
            )
        )
        self.assertEqual(result.status, "unknown")
        self.assertFalse(called)

    async def test_official_sdk_contract_with_mock_transport(self) -> None:
        requests: list[httpx2.Request] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(
                200,
                json={
                    "model": "jev-1.13.0",
                    "answers": {"meets_criteria": {"type": "noul", "noul": 0.92}},
                    "usage": {"input_tokens": 12, "output_tokens": 2},
                },
                headers={"x-typesafe-request-id": "req-sdk"},
            )

        transport = httpx2.MockTransport(handler)

        def factory(**kwargs: object) -> AsyncTypeSafeClient:
            return AsyncTypeSafeClient(
                **kwargs,
                retry=RetryPolicy(max_retries=0),
                transport=transport,
            )

        result = await JevEvaluator(self.config(), client_factory=factory).evaluate(
            EvaluationInput(criteria="Be correct", output="correct")
        )
        self.assertEqual(result.status, "passed")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/v1/systemone")
        self.assertEqual(requests[0].headers["authorization"], "Bearer operator-secret")


if __name__ == "__main__":
    unittest.main()
