from __future__ import annotations

import unittest

from pydantic import ValidationError

from lotuspond.evaluation import TranscriptSegment, normalize_transcript, render_transcript
from lotuspond.evaluation.transcript import TranscriptConflictError


class TranscriptTests(unittest.TestCase):
    def segment(self, segment_id: str, **values: object) -> TranscriptSegment:
        return TranscriptSegment(
            segment_id=segment_id,
            speaker=values.pop("speaker", "caller"),
            text=values.pop("text", segment_id),
            **values,
        )

    def test_final_replaces_partial_and_sequence_controls_order(self) -> None:
        normalized = normalize_transcript(
            [
                self.segment("b", sequence=2, start_ms=200),
                self.segment("a", text="hel", sequence=1, start_ms=100, is_final=False),
                self.segment("a", text="hello", sequence=1, start_ms=100, is_final=True),
            ]
        )
        self.assertEqual([item.segment_id for item in normalized.segments], ["a", "b"])
        self.assertEqual(normalized.partial_count, 0)
        self.assertEqual(normalized.order_source, "sequence")
        self.assertIn("caller: hello", render_transcript(normalized))

    def test_unresolved_partial_is_counted_and_excluded(self) -> None:
        normalized = normalize_transcript(
            [self.segment("a", is_final=False), self.segment("b", is_final=True)]
        )
        self.assertEqual(normalized.partial_count, 1)
        self.assertEqual([item.segment_id for item in normalized.segments], ["b"])
        self.assertFalse(normalized.is_complete)

    def test_conflicting_final_is_rejected(self) -> None:
        with self.assertRaises(TranscriptConflictError):
            normalize_transcript(
                [self.segment("a", text="first"), self.segment("a", text="changed")]
            )

    def test_duplicate_sequences_are_rejected(self) -> None:
        with self.assertRaises(TranscriptConflictError):
            normalize_transcript([self.segment("a", sequence=1), self.segment("b", sequence=1)])

    def test_negative_or_reversed_timestamps_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.segment("a", start_ms=-1)
        with self.assertRaises(ValidationError):
            self.segment("a", start_ms=10, end_ms=9)


if __name__ == "__main__":
    unittest.main()
