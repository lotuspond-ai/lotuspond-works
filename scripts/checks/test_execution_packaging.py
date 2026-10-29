"""Task 14: durable execution packaging keeps its boundaries.

No database is needed. With `LOTUSPOND_API_IMAGE` set (CI, after the image build)
the image itself is inspected; required mode
(`LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS=1`) refuses to run without it. Test
selection never depends on what the environment happens to provide.
"""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
REQUIRED = os.environ.get("LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS") == "1"
IMAGE = os.environ.get("LOTUSPOND_API_IMAGE")
ENGINE_DISTRIBUTIONS = ("temporalio", "temporal")

# Runs inside the API image or environment: what importing the API brings with it.
PROBE = """
import importlib.metadata as m, json, shutil, subprocess, sys, threading
before = set(sys.modules)
import lotuspond.app
imported = sorted(set(sys.modules) - before)
worker = shutil.which("execution-worker")
def run(*args):
    done = subprocess.run([worker, *args], capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin"})
    return {"returncode": done.returncode, "stderr": done.stderr[-400:]}
print(json.dumps({
    "distributions": sorted({d.metadata["Name"].lower() for d in m.distributions()}),
    "worker_module_imported": "lotuspond.execution_worker" in imported,
    "engine_modules": [n for n in imported if n.split(".")[0] in ("temporalio", "temporal")],
    "threads": threading.active_count(),
    "worker": worker,
    "without_role": run() if worker else None,
    "without_configuration": run("--role", "orchestrator") if worker else None,
}))
"""


def smoke():
    spec = importlib.util.spec_from_file_location(
        "execution_smoke", ROOT / "scripts/checks/execution-smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts/checks"))
    spec.loader.exec_module(module)
    return module


def probe(image):
    if image:
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
        ]
        command += [image, "-c", PROBE]
    else:
        command = [sys.executable, "-I", "-c", PROBE]
    done = subprocess.run(
        command, capture_output=True, text=True, timeout=300, cwd=BACKEND, check=False
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def locked_production_distributions():
    exported = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-hashes",
            "--project",
            str(BACKEND),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout
    names = set()
    for line in exported.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            names.add(line.split("==")[0].split("[")[0].split(" ")[0].lower())
    return names


def test_image_dependency_and_command_boundary():
    """The API ships no engine client or server; workers start only on request."""
    if REQUIRED and not IMAGE:
        pytest.fail(
            "required mode inspects the built API image: set LOTUSPOND_API_IMAGE"
        )
    locked = locked_production_distributions()
    observed = probe(IMAGE)
    api_installed_distributions = set(observed["distributions"])
    assert "lotuspond-api" in api_installed_distributions  # positive control
    for name in ENGINE_DISTRIBUTIONS:
        assert name not in locked
        assert name not in api_installed_distributions
    assert "temporalio" not in api_installed_distributions
    assert observed["engine_modules"] == []
    # Importing the API never imports, let alone starts, a worker.
    assert observed["worker_module_imported"] is False
    assert observed["threads"] == 1
    # The worker is the same image's explicit `execution-worker` command.
    assert observed["worker"], "execution-worker is not installed"
    worker_without_role = observed["without_role"]
    assert worker_without_role["returncode"] != 0
    assert "--role" in worker_without_role["stderr"]
    refused = observed["without_configuration"]
    assert refused["returncode"] == 2
    assert "refused to start: invalid_configuration" in refused["stderr"]


def test_missing_authority_is_not_readiness_success(monkeypatch, capsys):
    """Configured but unqualified: the worker refuses to start and the API refuses."""
    sys.path.insert(0, str(BACKEND))
    from lotuspond import execution_worker
    from lotuspond.execution.boundary import services
    from lotuspond.execution.errors import ExecutionError
    from lotuspond.execution.publication import ExecutionDependencies

    settings = execution_worker.ExecutionWorkerSettings(
        database_url="postgresql+psycopg://lotuspond@127.0.0.1:1/lotuspond",
        worker_id="packaging-check",
        role="orchestrator",
        interpreter_ids=("lotuspond.execution-interpreter.v1",),
    )
    # Positive control: a complete configuration passes validation, and only the
    # missing qualified engine refuses it. The database check is stubbed so the
    # refusal under test is the engine's, not the connection's.
    monkeypatch.setattr(
        execution_worker, "_verify_session", lambda database, settings: None
    )
    with pytest.raises(execution_worker.WorkerRefused) as refused:
        execution_worker.run_execution_worker(settings)
    assert refused.value.code == "engine_unavailable"
    assert execution_worker.serve(settings) == 2
    assert "refused to start: engine_unavailable" in capsys.readouterr().err

    class Request:
        class app:
            class state:
                execution_services = None

    with pytest.raises(ExecutionError) as unavailable:
        services(Request())
    assert unavailable.value.code == "authority_unavailable"

    class Synthetic:
        test_only = True

    # Packaging cannot select a fixture provider outside test mode.
    with pytest.raises(ValueError):
        ExecutionDependencies(authority=Synthetic(), test_mode=False)


def complete_report(smoke_module):
    journey = {
        "scenarios": [
            {"name": name, "passed": True, "detail": "checked"}
            for name in smoke_module.REQUIRED_SCENARIOS
        ],
        "private_sentinels_found": [],
        "max_provider_calls_per_attempt": 1,
        "cleanup_confirmed": True,
    }
    return {
        "format": smoke_module.REPORT_FORMAT,
        "required_integration_skips": 0,
        "journeys": [
            {**copy.deepcopy(journey), "artifact_variant": variant}
            for variant in ("runtime", "rebuilt")
        ],
        "cleanup_receipt": {"confirmed": True},
        "outcome": "passed",
    }


def test_required_acceptance_cannot_skip(tmp_path):
    smoke_module = smoke()
    report = complete_report(smoke_module)
    assert smoke_module.require_report(report) is report  # positive control
    for scenario in smoke_module.REQUIRED_SCENARIOS:
        missing = copy.deepcopy(report)
        missing["journeys"][0]["scenarios"] = [
            item
            for item in missing["journeys"][0]["scenarios"]
            if item["name"] != scenario
        ]
        with pytest.raises(smoke_module.EvidenceError) as error:
            smoke_module.require_report(missing)
        required_report_missing_error = error.value
        assert required_report_missing_error.code == "missing_required_evidence"
    # Required mode turns a missing prerequisite into a failure, never a skip.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("LOTUSPOND_PLATFORM_TEST_DATABASE_URL",)
    }
    environment["LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS"] = "1"
    for case in (
        "tests/execution/test_installed_acceptance.py",
        "tests/execution/conformance/test_activities.py::test_a_read_runs_once_on_a_real_runtime",
    ):
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", case]
            + ["--basetemp", str(tmp_path / case.split("/")[-1].split(":")[0])],
            cwd=BACKEND,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert done.returncode != 0, done.stdout[-1000:]
        assert "required durable execution prerequisite missing" in done.stdout
        assert " skipped" not in done.stdout.splitlines()[-1]


def test_owned_cleanup_reports_are_required():
    smoke_module = smoke()
    report = complete_report(smoke_module)
    for edit in (
        lambda r: r.update(cleanup_receipt={"confirmed": False}),
        lambda r: r.update(cleanup_receipt={}),
        lambda r: r["journeys"][1].update(cleanup_confirmed=False),
    ):
        unconfirmed = copy.deepcopy(report)
        edit(unconfirmed)
        with pytest.raises(smoke_module.EvidenceError) as error:
            smoke_module.require_report(unconfirmed)
        assert error.value.code == "missing_required_evidence"
    # CI keeps the receipts and fails when any is missing.
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    conformance = jobs["conformance"]
    assert conformance["env"]["LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS"] == "1"
    assert conformance["timeout-minutes"] > 0
    uploads = [
        step
        for step in conformance["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert uploads and all(step["if"] == "always()" for step in uploads)
    assert all(step["with"]["if-no-files-found"] == "error" for step in uploads)
    paths = "\n".join(step["with"]["path"] for step in uploads)
    for receipt in ("execution-report.json", "cleanup.json", "process-receipt.json"):
        assert receipt in paths
    # Tier 3 runs only on a manual dispatch that asks for it, never on a re-run.
    assert (
        jobs["load"]["if"] == "github.event_name == 'workflow_dispatch' && inputs.load"
    )
    disruptive = jobs["disruptive"]
    assert disruptive["env"]["LOTUSPOND_DISRUPTIVE_EXECUTION_TESTS"] == "1"
    assert disruptive["env"]["LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS"] == "1"
    # The broad PostgreSQL suite leaves the conformance tiers out by name, so no
    # job conceals them as skips.
    validate = "\n".join(step.get("run", "") for step in jobs["validate"]["steps"])
    assert "--ignore=tests/execution/conformance" in validate
    assert "--ignore=tests/execution/test_installed_acceptance.py" in validate
