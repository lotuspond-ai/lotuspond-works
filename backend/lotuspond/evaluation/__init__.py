"""Fail-closed evaluation adapters for Lotuspond."""

from .deterministic import CompositeEvaluator, DeterministicEvaluator
from .gym_anything import (
    GymAnythingCatalog,
    GymAnythingLaunchSpec,
    build_gym_anything_launch_spec,
    parse_gym_anything_catalog,
)
from .jev import JevConfig, JevEvaluator
from .live import LiveAssessmentAdapter, LiveTranscriptBuffer
from .models import (
    EvaluationInput,
    EvaluationProvenance,
    EvaluationResult,
    TranscriptSegment,
)
from .transcript import NormalizedTranscript, normalize_transcript, render_transcript

__all__ = [
    "CompositeEvaluator",
    "DeterministicEvaluator",
    "EvaluationInput",
    "EvaluationProvenance",
    "EvaluationResult",
    "GymAnythingCatalog",
    "GymAnythingLaunchSpec",
    "JevConfig",
    "JevEvaluator",
    "LiveAssessmentAdapter",
    "LiveTranscriptBuffer",
    "NormalizedTranscript",
    "TranscriptSegment",
    "build_gym_anything_launch_spec",
    "normalize_transcript",
    "parse_gym_anything_catalog",
    "render_transcript",
]
