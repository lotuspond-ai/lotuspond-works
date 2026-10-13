"""Stable public models shared by all evaluation providers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class TranscriptSegment(BaseModel):
    """One replaceable partial or immutable final transcript segment."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    segment_id: str = Field(min_length=1, max_length=160)
    speaker: Literal["caller", "agent", "system"]
    text: str = Field(min_length=1, max_length=20_000)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    sequence: int | None = Field(default=None, ge=0)
    is_final: bool = True

    @model_validator(mode="after")
    def validate_interval(self) -> "TranscriptSegment":
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("end_ms cannot precede start_ms")
        return self


class EvaluationInput(BaseModel):
    """Provider-neutral evidence submitted for one evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    criteria: str = Field(min_length=1, max_length=20_000)
    input: JsonValue = None
    context: JsonValue = None
    output: JsonValue = None
    expected: JsonValue = None
    modality: Literal["text", "voice"] = "text"
    transcript: tuple[TranscriptSegment, ...] = Field(default_factory=tuple, max_length=2_000)


class EvaluationProvenance(BaseModel):
    """Metadata needed to distinguish code, provider, and source evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, max_length=200)
    request_id: str | None = Field(default=None, max_length=200)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    """A verdict that never conflates unavailable evidence with success."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["passed", "failed", "unknown"]
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    reasons: tuple[str, ...] = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=200)
    provenance: EvaluationProvenance


def unknown_result(
    reason: str,
    *,
    provider: str,
    model: str | None = None,
    version: str | None = None,
    score: float | None = None,
    source: str,
    details: dict[str, JsonValue] | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        status="unknown",
        score=score,
        reasons=(reason,),
        provider=provider,
        model=model,
        version=version,
        provenance=EvaluationProvenance(
            source=source,
            details=details or {},
        ),
    )
