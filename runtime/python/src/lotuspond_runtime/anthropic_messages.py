"""The `anthropic-messages` model provider: one model round on the Anthropic Messages API.

Installed only with the runtime's `anthropic` extra. The operator's settings carry the
pinned model catalog document; its digest must equal the dependency the descriptor
serves, and its `configuration_sha256` must equal the SHA-256 of the canonical
configuration (model ID, effort and price table), so a changed model, effort or price
is a different pin. The key is the descriptor's one credential, resolved by the worker
just before the call.

One round: the instructions as the system prompt, the typed input and context as the
user turn, each earlier round's own content blocks (its continuation) followed by the
results of the tools it proposed, the declared read tools as strict tools, and the
agent's output schema as structured output. The model proposes tools; the runtime
checks and runs them. Egress is the operator's fixed HTTPS base URL with no proxy from
the environment, no redirect and no SDK retry (the engine owns retries); the request
is bounded by the round's deadline and token limits. Prompts, inputs and replies are
never logged.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import ssl

import rfc8785
from lotuspond_sdk.activity import UsageObservation
from lotuspond_sdk.executable.catalog import catalog_document_digest

from .providers import ActivityFailure, ModelReply, ToolProposal

MAX_OUTPUT_TOKENS = 16000  # non-streaming: keeps one round inside an HTTP timeout
_BASE_URL = re.compile(r"https://[a-z0-9.-]{1,253}(?::[0-9]{1,5})?")
_KEY = re.compile(r"[\x21-\x7e]{1,4096}")


# ------------------------------------------------------------------ schemas


def json_schema(schema, definitions=()) -> dict:
    """A Lotuspond data schema as the JSON Schema subset strict tools and outputs accept.

    Only structure is expressed: bounds, lengths and decimal scale are checked by the
    runtime against the exact schema afterwards, so a looser JSON Schema never admits
    a value the step would accept.
    """
    named = {item["name"]: item["schema"] for item in definitions}

    def convert(node, depth=0):
        if depth > 32:
            raise ValueError("Schema too deep")
        kind = node["kind"]
        if kind == "ref":
            return convert(named[node["name"]], depth + 1)
        if kind == "null":
            return {"type": "null"}
        if kind == "boolean":
            return {"type": "boolean"}
        if kind == "integer":
            return {"type": "integer"}
        if kind == "number":
            return {"type": "number"}
        if kind in ("string", "decimal"):
            return {"type": "string"}  # a decimal travels as its exact text
        if kind == "array":
            return {"type": "array", "items": convert(node["items"], depth + 1)}
        if kind == "object":
            fields = node["fields"]
            if any(not item["required"] for item in fields):
                raise ValueError("Strict schemas need every field present")
            return {
                "type": "object",
                "properties": {item["name"]: convert(item["schema"], depth + 1) for item in fields},
                "required": [item["name"] for item in fields],
                "additionalProperties": False,
            }
        raise ValueError(f"Unsupported schema kind {kind}")

    return convert(schema)


def tool_definition(spec) -> dict:
    definitions = spec.input_schema.get("definitions", [])
    return {
        "name": spec.name,
        "description": (
            f"Read `{spec.operation}` from {spec.dependency.name}. Read-only: it changes "
            "nothing. `target` names the record; `input` is always null."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "target": json_schema(spec.input_schema["target"], definitions),
                "input": json_schema(spec.input_schema["input"], definitions),
            },
            "required": ["target", "input"],
            "additionalProperties": False,
        },
    }


# ------------------------------------------------------------------ settings


def configuration_digest(configuration) -> str:
    return hashlib.sha256(rfc8785.dumps(configuration)).hexdigest()


def validate_settings(settings, dependency) -> dict:
    """The pinned model document and configuration; anything that differs denies."""
    if type(settings) is not dict or set(settings) - {
        "model_document",
        "configuration",
        "base_url",
        "ca_file",
    }:
        raise ValueError("Invalid model provider settings")
    document, configuration = settings["model_document"], settings["configuration"]
    if (
        catalog_document_digest(document) != dependency.sha256
        or (document["kind"], document["name"], document["version"])
        != (dependency.kind, dependency.name, dependency.version)
        or document["definition"]["profile"] != "anthropic-messages"
        or configuration_digest(configuration) != document["definition"]["configuration_sha256"]
    ):
        raise ValueError("Settings do not match the pinned model")
    prices = configuration.get("price_microusd_per_mtok")
    if (
        type(configuration.get("model")) is not str
        or configuration.get("effort") not in ("low", "medium", "high")
        or type(prices) is not dict
        or set(prices) != {"input", "output", "long_context_above", "long_input", "long_output"}
        or any(type(value) is not int or value < 0 for value in prices.values())
    ):
        raise ValueError("Invalid pinned configuration")
    base_url = settings.get("base_url", "https://api.anthropic.com")
    if not _BASE_URL.fullmatch(base_url):
        raise ValueError("The model destination must be a plain HTTPS origin")
    return {**settings, "base_url": base_url}


def round_cost(usage, prices):
    """Micro-USD for one round from reported token counts; unknown stays unknown."""
    cached = (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0)
    if cached or usage.input_tokens is None or usage.output_tokens is None:
        return None  # this provider never asks for caching; an unpriced count is unknown
    long = usage.input_tokens > prices["long_context_above"]
    rate_in = prices["long_input"] if long else prices["input"]
    rate_out = prices["long_output"] if long else prices["output"]
    return math.ceil((usage.input_tokens * rate_in + usage.output_tokens * rate_out) / 1_000_000)


# ------------------------------------------------------------------ the round


def messages_for(request) -> list:
    """The user turn, then each earlier round and the results of the tools it proposed."""
    opening = json.dumps({"input": request.input, "context": list(request.context)}, sort_keys=True)
    messages = [{"role": "user", "content": opening}]
    pending = []
    for entry in request.transcript:
        if "model" in entry:
            if pending:
                raise ActivityFailure("provider_failed", reached=False)
            blocks = entry["model"]
            if type(blocks) is not list:
                raise ActivityFailure("provider_failed", reached=False)
            messages.append({"role": "assistant", "content": blocks})
            pending = [block["id"] for block in blocks if block.get("type") == "tool_use"]
            results = []
            messages.append({"role": "user", "content": results})
        else:
            if not pending:
                raise ActivityFailure("provider_failed", reached=False)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": pending.pop(0),
                    "content": json.dumps(entry["result"], sort_keys=True),
                }
            )
    if pending:
        raise ActivityFailure("provider_failed", reached=False)
    return messages


class AnthropicModelProvider:
    """`ModelProvider` for one pinned Anthropic model configuration."""

    def __init__(self, descriptor, secrets):
        self.descriptor = descriptor
        self._settings = validate_settings(descriptor.settings_data(), descriptor.dependency)
        self._secrets = secrets

    def _client(self, timeout_s):
        import anthropic

        if len(self._secrets) != 1:
            raise ActivityFailure("unavailable", reached=False)
        try:
            key = self._secrets[0].decode("ascii").strip()
        except UnicodeError:
            raise ActivityFailure("unavailable", reached=False) from None
        if not _KEY.fullmatch(key):
            raise ActivityFailure("unavailable", reached=False)
        http = anthropic.DefaultHttpxClient(
            follow_redirects=False,
            trust_env=False,  # never an ambient proxy or certificate setting
            verify=(
                ssl.create_default_context(cafile=self._settings["ca_file"])
                if "ca_file" in self._settings
                else True
            ),
            timeout=timeout_s,
        )
        return anthropic.Anthropic(
            api_key=key,
            base_url=self._settings["base_url"],
            max_retries=0,
            timeout=timeout_s,
            http_client=http,
        )

    def generate(self, request, deadline_ms) -> ModelReply:
        import anthropic

        configuration = self._settings["configuration"]
        limits = self._settings["model_document"]["definition"]["limits"]
        max_tokens = min(request.max_output_tokens, MAX_OUTPUT_TOKENS)
        if max_tokens < 1 or request.max_input_tokens < 1 or request.max_cost_microusd < 1:
            raise ActivityFailure("provider_failed", reached=False)
        output = request.output_schema
        body = {
            "model": configuration["model"],
            "max_tokens": max_tokens,
            "system": request.instructions,
            "messages": messages_for(request),
            "output_config": {
                "effort": configuration["effort"],
                "format": {
                    "type": "json_schema",
                    "schema": json_schema(output["schema"], output.get("definitions", [])),
                },
            },
        }
        if request.tools:
            body["tools"] = [tool_definition(spec) for spec in request.tools]
        client = self._client(min(limits["timeout_seconds"], 600))
        try:
            response = client.messages.create(**body)
        except anthropic.RateLimitError:
            raise ActivityFailure("rate_limited") from None
        except (anthropic.APITimeoutError, anthropic.APIConnectionError):
            raise ActivityFailure("unavailable") from None  # it may have been received
        except anthropic.APIStatusError:
            raise ActivityFailure("provider_failed") from None
        finally:
            client.close()
        cost = round_cost(response.usage, configuration["price_microusd_per_mtok"])
        usage = UsageObservation(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_microusd=cost,
            completed_at_ms=0,
        )
        if response.stop_reason == "tool_use":
            blocks = [
                block.model_dump(mode="json", exclude_none=True) for block in response.content
            ]
            calls = tuple(
                ToolProposal(block["name"], block["input"])
                for block in blocks
                if block.get("type") == "tool_use"
            )
            if not calls:
                raise ActivityFailure("invalid_output", usage=usage)
            return ModelReply(None, calls, usage, blocks)
        if response.stop_reason != "end_turn":
            # refusal, max_tokens, pause: no usable answer from this round
            raise ActivityFailure("provider_failed", usage=usage)
        text = [block.text for block in response.content if block.type == "text"]
        try:
            value = json.loads(text[0])
        except (IndexError, ValueError):
            raise ActivityFailure("invalid_output", usage=usage) from None
        return ModelReply(value, (), usage, None)
