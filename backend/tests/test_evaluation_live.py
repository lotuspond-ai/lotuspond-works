from __future__ import annotations

import unittest

from lotuspond.evaluation import (
    EvaluationInput,
    EvaluationProvenance,
    EvaluationResult,
    LiveAssessmentAdapter,
    LiveTranscriptBuffer,
    TranscriptSegment,
)
from lotuspond.evaluation.transcript import TranscriptConflictError


class RecordingEvaluator:
    def __init__(self) -> None:
        self.calls: list[EvaluationInput] = []

    async def evaluate(self, evidence: EvaluationInput) -> EvaluationResult:
        self.calls.append(evidence)
        return EvaluationResult(
            status="passed",
            score=1.0,
            reasons=("recorded",),
            provider="test",
            provenance=EvaluationProvenance(source="test"),
        )


class LiveAssessmentTests(unittest.IsolatedAsyncioTestCase):
    def segment(self, segment_id: str, **values: object) -> TranscriptSegment:
        return TranscriptSegment(
            segment_id=segment_id,
            speaker=values.pop("speaker", "caller"),
            text=values.pop("text", segment_id),
            **values,
        )

    async def test_partial_transcript_returns_unknown_without_delegate_call(self) -> None:
        delegate = RecordingEvaluator()
        result = await LiveAssessmentAdapter(delegate).evaluate(
            EvaluationInput(
                criteria="Respond appropriately",
                modality="voice",
                transcript=(self.segment("one", is_final=False),),
            )
        )
        self.assertEqual(result.status, "unknown")
        self.assertEqual(delegate.calls, [])

    async def test_final_transcript_is_ordered_before_delegate_call(self) -> None:
        delegate = RecordingEvaluator()
        result = await LiveAssessmentAdapter(delegate).evaluate(
            EvaluationInput(
                criteria="Respond appropriately",
                modality="voice",
                transcript=(
                    self.segment("two", sequence=2),
                    self.segment("one", sequence=1),
                ),
            )
        )
        self.assertEqual(result.status, "passed")
        self.assertEqual([item.segment_id for item in delegate.calls[0].transcript], ["one", "two"])

    async def test_buffer_allows_partial_revision_but_not_final_mutation(self) -> None:
        buffer = LiveTranscriptBuffer()
        buffer.add(self.segment("one", text="hel", is_final=False))
        buffer.add(self.segment("one", text="hello", is_final=True))
        self.assertEqual(buffer.snapshot()[0].text, "hello")
        with self.assertRaises(TranscriptConflictError):
            buffer.add(self.segment("one", text="changed", is_final=True))


if __name__ == "__main__":
    unittest.main()
