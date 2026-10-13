"""Live voice transcript collection and final-evidence assessment."""

from __future__ import annotations

from .deterministic import AsyncEvaluator
from .models import EvaluationInput, EvaluationResult, TranscriptSegment, unknown_result
from .transcript import TranscriptConflictError, normalize_transcript


class LiveTranscriptBuffer:
    """Accept revisions to partial segments while keeping finals immutable."""

    def __init__(self) -> None:
        self._segments: list[TranscriptSegment] = []

    def add(self, segment: TranscriptSegment) -> None:
        # normalize_transcript enforces final immutability and speaker stability.
        candidate = [*self._segments, segment]
        normalize_transcript(candidate, include_partials=True)
        for index, existing in enumerate(self._segments):
            if existing.segment_id == segment.segment_id:
                if existing.is_final:
                    return
                self._segments[index] = segment
                return
        self._segments.append(segment)

    def snapshot(self) -> tuple[TranscriptSegment, ...]:
        normalized = normalize_transcript(self._segments, include_partials=True)
        return normalized.segments


class LiveAssessmentAdapter:
    """Call a full-transcript evaluator only after every segment is final."""

    provider = "lotuspond-live-assessment"

    def __init__(self, evaluator: AsyncEvaluator) -> None:
        self._evaluator = evaluator

    async def evaluate(self, evidence: EvaluationInput) -> EvaluationResult:
        if evidence.modality != "voice":
            return unknown_result(
                "Live assessment accepts voice evidence only.",
                provider=self.provider,
                source="live-transcript",
            )
        try:
            normalized = normalize_transcript(evidence.transcript, include_partials=True)
        except TranscriptConflictError:
            return unknown_result(
                "Live transcript contains conflicting final evidence.",
                provider=self.provider,
                source="live-transcript",
            )
        if normalized.partial_count:
            return unknown_result(
                "Live transcript still contains partial segments.",
                provider=self.provider,
                source="live-transcript",
                details={"partial_segments": normalized.partial_count},
            )
        if not normalized.segments:
            return unknown_result(
                "Live transcript has no final segments.",
                provider=self.provider,
                source="live-transcript",
            )
        finalized = evidence.model_copy(update={"transcript": normalized.segments})
        return await self._evaluator.evaluate(finalized)
