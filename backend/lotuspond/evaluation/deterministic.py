"""Code-owned assertions and safe composition with semantic evaluators."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from .models import (
    EvaluationInput,
    EvaluationProvenance,
    EvaluationResult,
    unknown_result,
)


class AsyncEvaluator(Protocol):
    async def evaluate(self, evidence: EvaluationInput) -> EvaluationResult: ...


def _assertion_mapping(evidence: EvaluationInput) -> Mapping[str, object] | None:
    if not isinstance(evidence.context, Mapping):
        return None
    assertions = evidence.context.get("deterministic_assertions")
    return assertions if isinstance(assertions, Mapping) else None


class DeterministicEvaluator:
    """Evaluate explicit assertions only; arbitrary prose stays unknown."""

    provider = "lotuspond-deterministic"

    async def evaluate(self, evidence: EvaluationInput) -> EvaluationResult:
        assertions = _assertion_mapping(evidence)
        expected = evidence.expected if isinstance(evidence.expected, Mapping) else None
        if not assertions and not expected:
            return unknown_result(
                "No deterministic assertions were configured for this evidence.",
                provider=self.provider,
                source="code",
            )

        failures: list[str] = []
        applied: list[str] = []
        misconfigured: list[str] = []

        if expected is not None:
            if "contains" in expected:
                contains = expected["contains"]
                if not isinstance(contains, str) or not contains:
                    misconfigured.append("expected.contains")
                else:
                    applied.append("expected.contains")
                    rendered_output = (
                        evidence.output
                        if isinstance(evidence.output, str)
                        else json.dumps(
                            evidence.output,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    if contains not in rendered_output:
                        failures.append("Output did not contain the expected text.")
            if "equals" in expected:
                applied.append("expected.equals")
                if evidence.output != expected["equals"]:
                    failures.append("Output did not exactly match expected.equals.")
            unsupported_expected = set(expected) - {"contains", "equals"}
            if unsupported_expected:
                misconfigured.append("expected")

        if assertions and "expected_output" in assertions:
            applied.append("expected_output")
            if evidence.output != assertions["expected_output"]:
                failures.append("Output did not exactly match expected_output.")

        for key, should_exist in (
            ("required_output_fields", True),
            ("forbidden_output_fields", False),
        ):
            if not assertions or key not in assertions:
                continue
            configured = assertions[key]
            if (
                not isinstance(configured, Sequence)
                or isinstance(configured, (str, bytes))
                or not all(isinstance(item, str) and item for item in configured)
            ):
                misconfigured.append(key)
                continue
            if not isinstance(evidence.output, Mapping):
                applied.append(key)
                failures.append(f"{key} requires an object output.")
                continue
            applied.append(key)
            for field in configured:
                present = field in evidence.output
                if should_exist and not present:
                    failures.append(f"Required output field {field!r} is missing.")
                if not should_exist and present:
                    failures.append(f"Forbidden output field {field!r} is present.")

        details = {"assertions_applied": applied}
        if misconfigured:
            details["misconfigured_assertions"] = misconfigured
            return unknown_result(
                "Deterministic assertions were malformed.",
                provider=self.provider,
                source="code",
                details=details,
            )
        if not applied:
            return unknown_result(
                "No supported deterministic assertions were configured.",
                provider=self.provider,
                source="code",
                details=details,
            )

        status = "failed" if failures else "passed"
        reasons = (
            tuple(failures) if failures else ("All configured deterministic assertions passed.",)
        )
        return EvaluationResult(
            status=status,
            score=0.0 if failures else 1.0,
            reasons=reasons,
            provider=self.provider,
            model=None,
            version="1",
            provenance=EvaluationProvenance(source="code", details=details),
        )


class CompositeEvaluator:
    """Use deterministic outcomes first, then a semantic fallback for unknowns."""

    def __init__(self, deterministic: AsyncEvaluator, semantic: AsyncEvaluator) -> None:
        self._deterministic = deterministic
        self._semantic = semantic

    async def evaluate(self, evidence: EvaluationInput) -> EvaluationResult:
        deterministic = await self._deterministic.evaluate(evidence)
        if deterministic.status != "unknown":
            return deterministic
        return await self._semantic.evaluate(evidence)
