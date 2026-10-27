"""Task 13: the durable-execution report verifier refuses every incomplete report.

`scripts/checks/execution-smoke.py` writes the report; `test_installed_acceptance.py`
produces one for real. These checks need no database: a report qualifies only with
both artifact variants, every named scenario true, zero required skips, no private
sentinel, one provider call per attempt and confirmed cleanup.
"""

import copy
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

CHECKS = Path(__file__).resolve().parents[2] / "scripts/checks"
sys.path.insert(0, str(CHECKS))
spec = importlib.util.spec_from_file_location(
    "execution_smoke", CHECKS / "execution-smoke.py"
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def journey(variant):
    return {
        "artifact_variant": variant,
        "scenarios": [
            {"name": name, "passed": True, "detail": "checked"}
            for name in smoke.REQUIRED_SCENARIOS
        ],
        "private_sentinels_found": [],
        "max_provider_calls_per_attempt": 1,
        "cleanup_confirmed": True,
    }


QUALIFIED = {
    "format": smoke.REPORT_FORMAT,
    "required_integration_skips": 0,
    "journeys": [journey("runtime"), journey("rebuilt")],
    "cleanup_receipt": {"confirmed": True},
    "outcome": "passed",
}


class ReportVerifierTests(unittest.TestCase):
    def test_a_complete_report_qualifies(self):
        self.assertEqual(smoke.verify_report(QUALIFIED), [])

    def test_every_missing_piece_is_refused(self):
        def changed(edit):
            report = copy.deepcopy(QUALIFIED)
            edit(report)
            return report

        cases = {
            "skips": lambda r: r.update(required_integration_skips=1),
            "format": lambda r: r.update(format="other"),
            "variant": lambda r: r["journeys"].pop(),
            "scenario": lambda r: r["journeys"][0]["scenarios"][3].update(passed=False),
            "missing scenario": lambda r: r["journeys"][1]["scenarios"].pop(),
            "sentinel": lambda r: r["journeys"][0].update(
                private_sentinels_found=["INPUT"]
            ),
            "unknown privacy": lambda r: r["journeys"][0].update(
                private_sentinels_found=None
            ),
            "calls": lambda r: r["journeys"][1].update(
                max_provider_calls_per_attempt=2
            ),
            "cleanup": lambda r: r.update(cleanup_receipt={"confirmed": False}),
            "outcome": lambda r: r.update(outcome="failed"),
        }
        for name, edit in cases.items():
            with self.subTest(name):
                self.assertNotEqual(smoke.verify_report(changed(edit)), [])

    def test_every_rollback_point_is_a_named_scenario(self):
        for point in (
            "admission",
            "transition",
            "claim",
            "result",
            "signal",
            "projection",
        ):
            for when in ("before", "after"):
                self.assertIn(
                    f"rollback_{point}_{when}_commit", smoke.REQUIRED_SCENARIOS
                )

    def test_the_output_directory_must_be_private(self):
        with tempfile.TemporaryDirectory() as root:
            shared = Path(root) / "shared"
            shared.mkdir(mode=0o755)
            os.chmod(shared, 0o755)
            # A group- or world-readable directory is tightened, never reused as is.
            self.assertEqual(smoke._private_directory(shared), shared)
            self.assertEqual(shared.stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
