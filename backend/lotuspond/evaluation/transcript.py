"""Deterministic transcript normalization with explicit partial handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import TranscriptSegment


class TranscriptConflictError(ValueError):
    """Raised when immutable transcript evidence conflicts."""


@dataclass(frozen=True)
class NormalizedTranscript:
    segments: tuple[TranscriptSegment, ...]
    partial_count: int
    order_source: str
    warnings: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return self.partial_count == 0 and bool(self.segments)


def _same_final(left: TranscriptSegment, right: TranscriptSegment) -> bool:
    return left == right


def normalize_transcript(
    segments: Iterable[TranscriptSegment],
    *,
    include_partials: bool = False,
) -> NormalizedTranscript:
    """Collapse segment revisions and order the retained evidence deterministically.

    A final segment may replace its earlier partial revision. Once final, a segment
    is immutable. When every retained segment has a sequence number, sequence is
    authoritative; otherwise timestamps are preferred and arrival order is the
    final tie breaker.
    """

    by_id: dict[str, tuple[int, TranscriptSegment]] = {}
    for arrival, segment in enumerate(segments):
        previous = by_id.get(segment.segment_id)
        if previous is None:
            by_id[segment.segment_id] = (arrival, segment)
            continue

        previous_arrival, previous_segment = previous
        if previous_segment.is_final:
            if segment.is_final and _same_final(previous_segment, segment):
                continue
            raise TranscriptConflictError(f"final segment {segment.segment_id!r} cannot be changed")
        if previous_segment.speaker != segment.speaker:
            raise TranscriptConflictError(f"segment {segment.segment_id!r} cannot change speaker")
        by_id[segment.segment_id] = (previous_arrival, segment)

    retained = list(by_id.values())
    partial_count = sum(not segment.is_final for _, segment in retained)
    if not include_partials:
        retained = [(arrival, segment) for arrival, segment in retained if segment.is_final]

    sequences = [segment.sequence for _, segment in retained if segment.sequence is not None]
    if len(sequences) != len(set(sequences)):
        raise TranscriptConflictError("transcript sequence numbers must be unique")

    warnings: list[str] = []
    if retained and all(segment.sequence is not None for _, segment in retained):
        order_source = "sequence"
        retained.sort(key=lambda item: (item[1].sequence, item[0]))
        starts = [segment.start_ms for _, segment in retained if segment.start_ms is not None]
        if starts != sorted(starts):
            warnings.append("sequence order conflicts with timestamps")
    elif retained and any(segment.start_ms is not None for _, segment in retained):
        order_source = "timestamp"
        retained.sort(
            key=lambda item: (
                item[1].start_ms is None,
                item[1].start_ms if item[1].start_ms is not None else 0,
                item[1].sequence if item[1].sequence is not None else 2**63 - 1,
                item[0],
            )
        )
        if any(segment.start_ms is None for _, segment in retained):
            warnings.append("segments without timestamps follow timestamped segments")
    else:
        order_source = "arrival"
        retained.sort(key=lambda item: item[0])
        if len(retained) > 1:
            warnings.append("ordering uses arrival because sequence and timestamps are absent")

    return NormalizedTranscript(
        segments=tuple(segment for _, segment in retained),
        partial_count=partial_count,
        order_source=order_source,
        warnings=tuple(warnings),
    )


def render_transcript(transcript: NormalizedTranscript) -> str:
    lines: list[str] = []
    for segment in transcript.segments:
        interval = ""
        if segment.start_ms is not None:
            interval = f"[{segment.start_ms}"
            interval += f"-{segment.end_ms}" if segment.end_ms is not None else ""
            interval += "] "
        lines.append(f"{interval}{segment.speaker}: {segment.text}")
    return "\n".join(lines)
