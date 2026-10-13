from __future__ import annotations

import unittest

from lotuspond.evaluation import DeterministicEvaluator, EvaluationInput


class DeterministicEvaluatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_assertions_remains_unknown(self) -> None:
        result = await DeterministicEvaluator().evaluate(
            EvaluationInput(criteria="Be correct", output={"ok": True})
        )
        self.assertEqual(result.status, "unknown")
        self.assertIsNone(result.score)

    async def test_explicit_assertions_can_pass(self) -> None:
        result = await DeterministicEvaluator().evaluate(
            EvaluationInput(
                criteria="Match the expected payload",
                output={"ok": True, "id": "a"},
                context={
                    "deterministic_assertions": {
                        "expected_output": {"ok": True, "id": "a"},
                        "required_output_fields": ["ok", "id"],
                        "forbidden_output_fields": ["secret"],
                    }
                },
            )
        )
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.score, 1.0)

    async def test_ui_expected_contains_contract_is_deterministic(self) -> None:
        passed = await DeterministicEvaluator().evaluate(
            EvaluationInput(
                criteria="Mention the release id",
                input={"prompt": "Summarize release rel-42"},
                output="Release rel-42 is ready for review.",
                expected={"contains": "rel-42"},
            )
        )
        failed = await DeterministicEvaluator().evaluate(
            EvaluationInput(
                criteria="Mention the release id",
                input={"prompt": "Summarize release rel-42"},
                output="No release was found.",
                expected={"contains": "rel-42"},
            )
        )
        self.assertEqual(passed.status, "passed")
        self.assertEqual(failed.status, "failed")

    async def test_failed_assertion_never_becomes_unknown_or_passed(self) -> None:
        result = await DeterministicEvaluator().evaluate(
            EvaluationInput(
                criteria="Match the expected payload",
                output={"ok": False},
                context={
                    "deterministic_assertions": {
                        "expected_output": {"ok": True},
                        "required_output_fields": ["id"],
                    }
                },
            )
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.score, 0.0)

    async def test_malformed_assertion_is_unknown(self) -> None:
        result = await DeterministicEvaluator().evaluate(
            EvaluationInput(
                criteria="Validate fields",
                output={"ok": True},
                context={"deterministic_assertions": {"required_output_fields": "ok"}},
            )
        )
        self.assertEqual(result.status, "unknown")


if __name__ == "__main__":
    unittest.main()
