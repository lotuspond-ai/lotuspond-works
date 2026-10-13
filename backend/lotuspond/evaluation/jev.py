"""Official TypeSafe Python SDK adapter for Jev semantic judgments."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    EvaluationInput,
    EvaluationProvenance,
    EvaluationResult,
    unknown_result,
)
from .transcript import TranscriptConflictError, normalize_transcript, render_transcript

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class _BoundedJevHTTPClient(httpx2.AsyncClient):
    """Use the SDK's public http_client seam without its unbounded body read."""

    async def request(self, method: str, url: Any, **kwargs: Any) -> httpx2.Response:
        kwargs["follow_redirects"] = False
        async with self.stream(method, url, **kwargs) as response:
            if response.is_redirect:
                raise ValueError("Jev redirects are not permitted")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(chunk) > MAX_RESPONSE_BYTES - len(raw):
                    raise ValueError("Jev response exceeds the size limit")
                raw.extend(chunk)
            # aiter_bytes returns decoded data. Do not decode a compressed response twice.
            headers = [
                (name, value)
                for name, value in response.headers.multi_items()
                if name.lower() not in {"content-encoding", "content-length"}
            ]
            return httpx2.Response(
                response.status_code,
                headers=headers,
                content=bytes(raw),
                request=response.request,
                extensions=response.extensions,
            )


def _jev_http_client(timeout_seconds: float) -> httpx2.AsyncClient:
    return _BoundedJevHTTPClient(
        timeout=httpx2.Timeout(timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )


class JevConfig(BaseModel):
    """Operator-owned provider configuration; secrets are never serialized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str | None = None
    api_key: str | None = Field(default=None, repr=False, exclude=True)
    model: str = Field(default="jev-latest", min_length=1, max_length=200)
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)
    pass_threshold: float = Field(default=0.8, ge=0.5, le=1.0)
    fail_threshold: float = Field(default=0.2, ge=0.0, le=0.5)
    max_state_characters: int = Field(default=200_000, ge=1_000, le=1_000_000)

    @model_validator(mode="after")
    def validate_configuration(self) -> "JevConfig":
        if self.fail_threshold >= self.pass_threshold:
            raise ValueError("fail_threshold must be lower than pass_threshold")
        if self.base_url is not None:
            parsed = urlsplit(self.base_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("base_url must be an absolute HTTPS URL")
            if parsed.username or parsed.password:
                raise ValueError("base_url cannot contain credentials")
            if parsed.query or parsed.fragment:
                raise ValueError("base_url cannot contain a query or fragment")
        return self

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.api_key.strip())


def _extract_response(response: object) -> tuple[float | None, str | None, str | None]:
    model = getattr(response, "model", None)
    request_id = getattr(response, "request_id", None)

    nouls = getattr(response, "nouls", None)
    if isinstance(nouls, Mapping):
        answer = nouls.get("meets_criteria")
        value = getattr(answer, "noul", None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (
                float(value),
                str(model) if model else None,
                str(request_id) if request_id else None,
            )

    answers = getattr(response, "answers", None)
    if isinstance(answers, Mapping):
        answer = answers.get("meets_criteria")
        if isinstance(answer, Mapping):
            value = answer.get("noul")
        else:
            value = getattr(answer, "noul", None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (
                float(value),
                str(model) if model else None,
                str(request_id) if request_id else None,
            )
    return None, str(model) if model else None, str(request_id) if request_id else None


class JevEvaluator:
    """Map a Jev Noul probability to pass/fail/unknown with explicit bands."""

    provider = "typesafe-jev"

    def __init__(
        self,
        config: JevConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory

    def _new_client(self) -> Any:
        factory = self._client_factory
        options: dict[str, Any] = {}
        if factory is None:
            from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

            factory = AsyncTypeSafeClient
            options = {
                "http_client": _jev_http_client(self._config.timeout_seconds),
                "retry": RetryPolicy(max_retries=0),
            }
        return factory(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            model=self._config.model,
            timeout=self._config.timeout_seconds,
            **options,
        )

    async def evaluate(self, evidence: EvaluationInput) -> EvaluationResult:
        if not self._config.configured:
            return unknown_result(
                "Jev is not configured by the operator.",
                provider=self.provider,
                model=self._config.model,
                source="typesafe-sdk",
                details={"configured": False},
            )

        try:
            transcript = normalize_transcript(evidence.transcript)
        except TranscriptConflictError:
            return unknown_result(
                "Transcript evidence conflicts and cannot be evaluated.",
                provider=self.provider,
                model=self._config.model,
                source="typesafe-sdk",
            )
        if evidence.modality == "voice" and not transcript.is_complete:
            return unknown_result(
                "Voice evaluation requires at least one final transcript segment and no partial segments.",
                provider=self.provider,
                model=self._config.model,
                source="typesafe-sdk",
                details={"partial_segments": transcript.partial_count},
            )

        transcript_text = render_transcript(transcript)
        state: dict[str, object] = {
            "input": evidence.input,
            "context": evidence.context,
            "output": evidence.output,
            "expected": evidence.expected,
            "modality": evidence.modality,
        }
        if transcript_text:
            state["transcript"] = transcript_text
        state_characters = len(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        if state_characters > self._config.max_state_characters:
            return unknown_result(
                "Evaluation evidence exceeds the configured provider bound.",
                provider=self.provider,
                model=self._config.model,
                source="typesafe-sdk",
            )

        questions = {
            "meets_criteria": {
                "type": "noul",
                "instructions": {
                    "question": "Does the supplied output satisfy the evaluation criteria?",
                    "criteria": evidence.criteria,
                },
                "criteria": {
                    "true": "The supplied output satisfies the criteria.",
                    "false": "The supplied output does not satisfy the criteria.",
                },
            }
        }

        try:
            # Covers connection, body reads, SDK work, retries in injected clients, and cleanup.
            async with asyncio.timeout(self._config.timeout_seconds):
                async with self._new_client() as client:
                    response = await client.system_one(state=state, questions=questions)
        except Exception as error:
            reason = (
                "Jev evaluation timed out."
                if isinstance(error, TimeoutError)
                else "Jev evaluation was unavailable."
            )
            return unknown_result(
                reason,
                provider=self.provider,
                model=self._config.model,
                source="typesafe-sdk",
                details={
                    "failure": "timeout" if isinstance(error, TimeoutError) else "provider_error"
                },
            )

        probability, resolved_model, request_id = _extract_response(response)
        if probability is None or probability < 0.0 or probability > 1.0:
            return unknown_result(
                "Jev returned no valid Noul probability.",
                provider=self.provider,
                model=self._config.model,
                version=resolved_model,
                source="typesafe-sdk",
            )

        if probability >= self._config.pass_threshold:
            status = "passed"
            reason = "Jev probability met the configured pass threshold."
        elif probability <= self._config.fail_threshold:
            status = "failed"
            reason = "Jev probability met the configured fail threshold."
        else:
            status = "unknown"
            reason = "Jev probability fell inside the configured review band."

        return EvaluationResult(
            status=status,
            score=probability,
            reasons=(reason,),
            provider=self.provider,
            model=self._config.model,
            version=resolved_model,
            provenance=EvaluationProvenance(
                source="typesafe-sdk",
                request_id=request_id,
                details={
                    "question_type": "noul",
                    "pass_threshold": self._config.pass_threshold,
                    "fail_threshold": self._config.fail_threshold,
                    "transcript_order": transcript.order_source,
                    "transcript_warnings": list(transcript.warnings),
                },
            ),
        )
