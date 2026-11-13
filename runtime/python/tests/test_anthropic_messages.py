"""The `anthropic-messages` model provider against a local HTTPS Messages API double.

The double answers in the Messages API's documented response shape; it is not a
recording of a live call. No real model or key is used.
"""

import hashlib
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import rfc8785
from lotuspond_sdk.executable.catalog import catalog_document_digest
from lotuspond_sdk.executable.package import ArtifactRef
from tls_fixtures import certificates

from lotuspond_runtime.anthropic_messages import (
    AnthropicModelProvider,
    json_schema,
    messages_for,
    round_cost,
)
from lotuspond_runtime.providers import ActivityFailure, ModelRequest, ProviderDescriptor, ToolSpec

pytest.importorskip("anthropic")

PRICES = {
    "input": 100000,
    "output": 500000,
    "long_context_above": 100000,
    "long_input": 500000,
    "long_output": 2500000,
}
CONFIGURATION = {"model": "claude-haiku-5-5", "effort": "medium", "price_microusd_per_mtok": PRICES}
STR = {"kind": "string", "max_length": 32}
PROPOSAL = {
    "kind": "object",
    "fields": [
        {"name": "resolution", "schema": STR, "required": True},
        {
            "name": "amount",
            "schema": {"kind": "decimal", "precision": 18, "scale": 2},
            "required": True,
        },
    ],
}
TARGET = {"kind": "object", "fields": [{"name": "supplier_id", "schema": STR, "required": True}]}
CONNECTOR = ArtifactRef(kind="connector", name="ap-standin", version="0.1.0", sha256="b" * 64)
TOOL = ToolSpec(
    name="ap-standin-get_supplier_contract",
    dependency=CONNECTOR,
    operation="get_supplier_contract",
    input_schema={"target": TARGET, "input": {"kind": "null"}, "definitions": []},
    output_schema={"schema": {"kind": "null"}, "definitions": []},
)


def model_document(configuration=CONFIGURATION):
    return {
        "format_version": 1,
        "kind": "model",
        "name": "ap-investigator-model",
        "version": "0.1.0",
        "schemas": [],
        "definition": {
            "profile": "anthropic-messages",
            "configuration_revision": "1",
            "configuration_sha256": hashlib.sha256(rfc8785.dumps(configuration)).hexdigest(),
            "input_schema": {"kind": "null"},
            "output_schema": PROPOSAL,
            "read_tools": [],
            "limits": {
                "max_steps": 20,
                "timeout_seconds": 30,
                "max_input_tokens": 60000,
                "max_output_tokens": 4000,
                "max_cost_microusd": 20000,
            },
        },
    }


def ref(document):
    return ArtifactRef(
        kind="model",
        name=document["name"],
        version=document["version"],
        sha256=catalog_document_digest(document),
    )


class Double:
    """A local HTTPS server answering POST /v1/messages from a script, recording requests."""

    def __init__(self, directory, replies):
        self.replies, self.requests = list(replies), []
        self.ca, key = certificates(directory)
        double = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                double.requests.append(
                    (self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                )
                status, reply = double.replies.pop(0)
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.ca, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"https://localhost:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


def message(content, stop_reason, input_tokens=1200, output_tokens=80):
    return {
        "id": "msg_synthetic",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def provider(double, *, document=None, configuration=CONFIGURATION):
    document = document or model_document()
    descriptor = ProviderDescriptor(
        "anthropic-messages",
        ref(document),
        "model",
        settings={
            "model_document": document,
            "configuration": configuration,
            "base_url": double.base_url,
            "ca_file": str(double.ca),
        },
    )
    return AnthropicModelProvider(descriptor, (b"sk-synthetic-key",))


def request(transcript=()):
    return ModelRequest(
        model=CONNECTOR,
        instructions="Investigate the synthetic exception.",
        input={"invoice_id": "INV-1002"},
        context=(),
        output_schema={"schema": PROPOSAL, "definitions": []},
        tools=(TOOL,),
        transcript=tuple(transcript),
        max_input_tokens=60000,
        max_output_tokens=4000,
        max_cost_microusd=20000,
    )


TOOL_ROUND = message(
    [
        {"type": "thinking", "thinking": "", "signature": "sig-synthetic"},
        {
            "type": "tool_use",
            "id": "toolu_synthetic",
            "name": "ap-standin-get_supplier_contract",
            "input": {"target": {"supplier_id": "SUP-BOLT"}, "input": None},
        },
    ],
    "tool_use",
)
ANSWER = {"resolution": "no_action", "amount": "0.00"}
FINAL_ROUND = message(
    [
        {"type": "thinking", "thinking": "", "signature": "sig-2"},
        {"type": "text", "text": json.dumps(ANSWER)},
    ],
    "end_turn",
    1500,
    120,
)


def test_a_tool_round_then_a_final_round_replays_the_models_own_turn(tmp_path):
    with Double(tmp_path, [(200, TOOL_ROUND), (200, FINAL_ROUND)]) as double:
        model = provider(double)
        first = model.generate(request(), 0)
        assert [(c.name, c.arguments) for c in first.tool_calls] == [
            (
                "ap-standin-get_supplier_contract",
                {"target": {"supplier_id": "SUP-BOLT"}, "input": None},
            )
        ]
        assert first.usage.cost_microusd == 160  # 1200 x $0.10 + 80 x $0.50 per million
        transcript = [
            {"model": first.continuation},
            {"tool": TOOL.name, "arguments": first.tool_calls[0].arguments, "result": {"x": 1}},
        ]
        second = model.generate(request(transcript), 0)
    assert second.output == ANSWER and second.tool_calls == () and second.continuation is None
    path, headers, body = double.requests[0]
    assert path == "/v1/messages"
    # SDK 2.x sends the key as a bearer token, on API version 2026-10-21.
    assert headers["authorization"] == "Bearer sk-synthetic-key"
    assert headers["anthropic-version"] == "2026-10-21"
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["schema"] == json_schema(PROPOSAL)
    assert body["tools"][0]["strict"] is True
    assert not {"temperature", "top_p", "top_k"} & set(body)
    replayed = double.requests[1][2]["messages"]
    assert [m["role"] for m in replayed] == ["user", "assistant", "user"]
    assert replayed[1]["content"] == TOOL_ROUND["content"]  # thinking block included, unchanged
    assert replayed[2]["content"][0]["tool_use_id"] == "toolu_synthetic"


def test_a_refusal_or_truncation_is_a_provider_failure_with_its_usage(tmp_path):
    refusal = message([], "refusal")
    with Double(tmp_path, [(200, refusal)]) as double:
        with pytest.raises(ActivityFailure) as failed:
            provider(double).generate(request(), 0)
    assert failed.value.code == "provider_failed"
    assert failed.value.usage.input_tokens == 1200


def test_rate_limits_are_reported_and_never_retried_by_the_sdk(tmp_path):
    error = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    with Double(tmp_path, [(429, error), (429, error)]) as double:
        with pytest.raises(ActivityFailure) as failed:
            provider(double).generate(request(), 0)
    assert failed.value.code == "rate_limited"
    assert len(double.requests) == 1  # max_retries=0: the engine owns retries


def test_settings_must_match_the_pinned_model_and_configuration(tmp_path):
    with Double(tmp_path, []) as double:
        changed = {**CONFIGURATION, "effort": "high"}
        with pytest.raises(ValueError):
            provider(double, configuration=changed)  # same pinned digest, other effort


def test_cached_or_unreported_tokens_make_the_cost_unknown():
    from types import SimpleNamespace

    def usage(**fields):
        base = {"input_tokens": 10, "output_tokens": 10}
        base.update(fields)
        return SimpleNamespace(
            cache_creation_input_tokens=base.get("cache_creation_input_tokens"),
            cache_read_input_tokens=base.get("cache_read_input_tokens"),
            input_tokens=base["input_tokens"],
            output_tokens=base["output_tokens"],
        )

    assert round_cost(usage(input_tokens=1, output_tokens=1), PRICES) == 1  # 0.6, rounded up
    assert round_cost(usage(cache_read_input_tokens=5), PRICES) is None
    assert round_cost(usage(input_tokens=200000), PRICES) == 100025  # the long-prompt card


def test_a_transcript_out_of_order_is_refused():
    with pytest.raises(ActivityFailure):
        messages_for(request([{"tool": TOOL.name, "arguments": {}, "result": {}}]))
