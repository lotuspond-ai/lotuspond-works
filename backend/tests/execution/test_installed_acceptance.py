"""Task 13: installed-artifact acceptance of durable execution, through execution-smoke.

The smoke builds and installs the API, runtime and SDK (and a runtime rebuilt from
its sdist), runs the journey outside the checkout over actual TLS, and reports. This
test is its independent outer supervisor: a bounded timeout, and a receipt.
"""

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CHECKS = ROOT / "scripts/checks"
sys.path.insert(0, str(CHECKS))
from execution_process import run_owned  # noqa: E402

REQUIRED = os.environ.get("LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS") == "1"
PRIVATE_SENTINELS = (
    "INPUT",
    "SIGNAL",
    "PROVIDER",
    "PROMPT",
    "DOCUMENT",
    "SOURCE",
    "RUNTIME_CREDENTIAL",
    "CUSTOMER_COOKIE",
)


def smoke_module():
    spec = importlib.util.spec_from_file_location("execution_smoke", CHECKS / "execution-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def missing(reason):
    if REQUIRED:
        pytest.fail(f"required durable execution prerequisite missing: {reason}")
    pytest.skip(reason)


def test_installed_durable_execution_journey(tmp_path):
    database = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    if not database:
        missing("Verified localhost PostgreSQL required")
    if sys.platform != "linux":
        missing("installed runtimes need Linux private state")
    private = tmp_path / "database-url"
    private.write_text(database)
    private.chmod(0o600)
    output = tmp_path / "evidence"
    result = run_owned(
        [
            sys.executable,
            str(CHECKS / "execution-smoke.py"),
            "--database-url-file",
            str(private),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        timeout=4200,
        grace=90,
        receipt=tmp_path / "outer-process-receipt.json",
    )
    assert not result.timed_out and result.cleanup_confirmed
    report = json.loads((output / "execution-report.json").read_text())
    keep = os.environ.get("LOTUSPOND_EXECUTION_EVIDENCE_DIRECTORY")
    if keep:  # a local run keeps the report and its manifests outside the temp tree
        kept = Path(keep)
        kept.mkdir(parents=True, exist_ok=True)
        for path in [output / "execution-report.json", *output.glob("*/*-manifest.json")]:
            shutil.copyfile(path, kept / path.name)
    required_integration_skips = report["required_integration_skips"]
    observed_scenarios = [item for journey in report["journeys"] for item in journey["scenarios"]]
    decoded_events_and_errors = report.get("private_sentinels_found", PRIVATE_SENTINELS)
    max_provider_calls_per_attempt = report.get("max_provider_calls_per_attempt")
    max_target_writes_per_intent = max(
        (journey.get("max_target_writes_per_intent") or 0) for journey in report["journeys"]
    )
    cleanup_receipt = report["cleanup_receipt"]
    assert required_integration_skips == 0
    assert all(item["passed"] for item in observed_scenarios), [
        (item["name"], item["detail"]) for item in observed_scenarios if not item["passed"]
    ]
    assert all(sentinel not in decoded_events_and_errors for sentinel in PRIVATE_SENTINELS)
    assert max_provider_calls_per_attempt == 1
    assert max_target_writes_per_intent == 1
    assert cleanup_receipt["confirmed"] is True
    assert smoke_module().verify_report(report) == []
    assert result.returncode == 0, (result.stdout + result.stderr).decode()[-2000:]
