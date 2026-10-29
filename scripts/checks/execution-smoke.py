#!/usr/bin/env python3
"""Test-only installed-artifact qualification of durable execution (Task 13).

Builds the SDK, runtime and API wheels, and a runtime wheel rebuilt from its sdist;
installs each set into its own isolated environment; copies the conformance journey
and its fixtures outside the checkout; and runs the journey against an owned schema
on the given test database. Writes `execution-report.json`, with the source and
distribution manifests, the privacy result and the cleanup receipts, into a private
output directory. Never a production bootstrap command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

from execution_process import Cancelled, cancellation_signals, census, run_owned

ROOT = Path(__file__).resolve().parents[2]
REPORT_FORMAT = "lotuspond.execution-report.v1"
CONFORMANCE = ROOT / "backend/tests/execution/conformance"
JOURNEY_FILES = ("world.py", "harness.py", "processes.py", "driver.py", "journey.py")
REQUIRED_SCENARIOS = (
    "noneditable_installed_api_runtime_sdk",
    "tls_authentication",
    "reference_workflow_result_and_calls",
    "decoded_events_and_errors_exclude_payloads",
    *(
        f"rollback_{point}_{when}_commit"
        for point in (
            "admission",
            "signal",
            "claim",
            "result",
            "transition",
            "projection",
        )
        for when in ("before", "after")
    ),
)
SOURCE_DIRECTORIES = (
    "backend/lotuspond",
    "backend/tests/execution/conformance",
    "backend/tests/execution/fixtures/control-flow",
    "runtime/python",
    "sdk/python",
    "scripts/checks",
    "contracts/examples/executable-v2",
)
_RECEIPTS = None
_SEQUENCE = 0


class EvidenceError(Exception):
    """A qualification report that cannot be relied on; a fixed code only."""

    def __init__(self, code: str, problems=()):
        self.code, self.problems = code, tuple(problems)
        super().__init__(code)


def require_report(report: dict) -> dict:
    """The report when it qualifies; `missing_required_evidence` otherwise."""
    problems = verify_report(report)
    if problems:
        raise EvidenceError("missing_required_evidence", problems)
    return report


def verify_report(report: dict) -> list[str]:
    """Every reason a report does not qualify; empty when it does."""
    problems = []
    if report.get("format") != REPORT_FORMAT:
        problems.append("format")
    if report.get("required_integration_skips") != 0:
        problems.append("required_integration_skips")
    journeys = report.get("journeys") or []
    if {item.get("artifact_variant") for item in journeys} != {"runtime", "rebuilt"}:
        problems.append("artifact_variants")
    for item in journeys:
        names = {
            scenario.get("name"): scenario.get("passed")
            for scenario in item.get("scenarios", [])
        }
        missing = [name for name in REQUIRED_SCENARIOS if names.get(name) is not True]
        if missing:
            problems.append(f"{item.get('artifact_variant')}: {','.join(missing)}")
        if item.get("private_sentinels_found") != []:
            problems.append(f"{item.get('artifact_variant')}: private_sentinels_found")
        if item.get("max_provider_calls_per_attempt") != 1:
            problems.append(
                f"{item.get('artifact_variant')}: max_provider_calls_per_attempt"
            )
        # Each journey's own receipt, not only the outer summary of them.
        if item.get("cleanup_confirmed") is not True:
            problems.append(f"{item.get('artifact_variant')}: cleanup_confirmed")
    if (report.get("cleanup_receipt") or {}).get("confirmed") is not True:
        problems.append("cleanup_receipt")
    if report.get("outcome") != "passed":
        problems.append("outcome")
    return problems


def run(args, cwd, *, env=None, timeout=300):
    global _SEQUENCE
    _SEQUENCE += 1
    receipt = None if _RECEIPTS is None else _RECEIPTS / f"command-{_SEQUENCE:02}.json"
    result = run_owned(args, cwd=cwd, env=env, timeout=timeout, receipt=receipt)
    if result.returncode or result.timed_out or not result.cleanup_confirmed:
        raise RuntimeError("qualification subprocess failed")
    return result.stdout


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeError("output directory is not private")
    return path


def _manifest(directories):
    files = {}
    for directory in directories:
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and not any(
                part in {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}
                for part in path.parts
            ):
                files[str(path.relative_to(ROOT))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--journey-timeout", type=int, default=1800)
    args = parser.parse_args()
    output = _private_directory(args.output.absolute())
    report_path = output / "execution-report.json"
    if report_path.exists():
        raise RuntimeError("refusing to overwrite earlier evidence")
    evidence = Path(
        tempfile.mkdtemp(prefix="lotuspond-execution-artifacts-", dir=output)
    )
    os.chmod(evidence, 0o700)
    global _RECEIPTS
    _RECEIPTS = evidence
    raw = args.database_url_file.absolute().read_text().strip()
    report = {
        "format": REPORT_FORMAT,
        "source_commit": None,
        "source_dirty": None,
        "required_integration_skips": 0,
        "journeys": [],
        "cleanup_receipt": {"confirmed": False},
        "outcome": "failed",
    }
    docker = shutil.which("docker") is not None
    before = census(raw, docker=docker)
    try:
        report["source_commit"] = (
            run(["git", "rev-parse", "HEAD"], ROOT).decode().strip()
        )
        report["source_dirty"] = bool(
            run(["git", "status", "--porcelain", "--untracked-files=normal"], ROOT)
        )
        source = _manifest(SOURCE_DIRECTORIES)
        (evidence / "source-manifest.json").write_text(
            json.dumps(source, indent=2) + "\n"
        )
        report["source_manifest_sha256"] = hashlib.sha256(
            (evidence / "source-manifest.json").read_bytes()
        ).hexdigest()
        for name, project in (
            ("sdk", "sdk/python"),
            ("runtime", "runtime/python"),
            ("api", "backend"),
        ):
            run(
                [
                    "uv",
                    "build",
                    "--wheel",
                    "--project",
                    str(ROOT / project),
                    "--out-dir",
                    str(evidence / name),
                ],
                evidence,
            )
        run(
            [
                "uv",
                "build",
                "--sdist",
                "--project",
                str(ROOT / "runtime/python"),
                "--out-dir",
                str(evidence / "runtime"),
            ],
            evidence,
        )
        with tarfile.open(next((evidence / "runtime").glob("*.tar.gz"))) as archive:
            archive.extractall(evidence / "source", filter="data")
        run(
            [
                "uv",
                "build",
                "--wheel",
                "--project",
                str(next((evidence / "source").iterdir())),
                "--out-dir",
                str(evidence / "rebuilt"),
            ],
            evidence,
        )
        requirements = run(
            [
                "uv",
                "export",
                "--frozen",
                "--project",
                str(ROOT / "backend"),
                "--no-emit-project",
                "--no-emit-package",
                "lotuspond-sdk",
                "--no-emit-package",
                "lotuspond-runtime",
            ],
            ROOT,
        )
        (evidence / "requirements.txt").write_bytes(requirements)
        distributions = {
            str(p.relative_to(evidence)): hashlib.sha256(p.read_bytes()).hexdigest()
            for d in ("sdk", "runtime", "rebuilt", "api")
            for p in sorted((evidence / d).iterdir())
        }
        (evidence / "distribution-manifest.json").write_text(
            json.dumps(distributions, indent=2) + "\n"
        )
        report["distribution_manifest"] = distributions
        fixture = evidence / "journey"
        (fixture / "fixtures/control-flow").mkdir(parents=True, mode=0o700)
        for name in JOURNEY_FILES:
            shutil.copyfile(CONFORMANCE / name, fixture / name)
        for path in (CONFORMANCE.parent / "fixtures/control-flow").glob("*.json"):
            shutil.copyfile(path, fixture / "fixtures/control-flow" / path.name)
        shutil.copyfile(
            ROOT / "contracts/examples/executable-v2/catalog.json",
            fixture / "fixtures/catalog.json",
        )
        clean = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "HOME",
                "TMPDIR",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "UV_CACHE_DIR",
                "UV_PYTHON_INSTALL_DIR",
                "UV_SYSTEM_CERTS",
                "UV_NATIVE_TLS",
                "UV_OFFLINE",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
            }
        }
        for variant in ("runtime", "rebuilt"):
            venv = evidence / ("venv-" + variant)
            run(["uv", "venv", "--python", sys.executable, str(venv)], evidence)
            python = str(venv / "bin/python")
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    python,
                    "-r",
                    str(evidence / "requirements.txt"),
                    str(next((evidence / "sdk").glob("*.whl"))),
                    str(next((evidence / variant).glob("*.whl"))),
                    str(next((evidence / "api").glob("*.whl"))),
                ],
                evidence,
            )
            run(["uv", "pip", "check", "--python", python], evidence)
            case = evidence / ("case-" + variant)
            case.mkdir(mode=0o700)
            config = case / "private-database-url"
            config.write_text(raw)
            config.chmod(0o600)
            result = run_owned(
                [python, "-I", str(fixture / "journey.py"), str(config), str(case)],
                cwd=case,
                env=clean,
                timeout=args.journey_timeout,
                grace=60,
                receipt=case / "process-receipt.json",
            )
            item = json.loads((case / "result.json").read_text())
            item["artifact_variant"] = variant
            item["process_cleanup_confirmed"] = (
                result.cleanup_confirmed and not result.timed_out
            )
            report["journeys"].append(item)
            if result.timed_out or not result.cleanup_confirmed:
                raise RuntimeError("installed journey was not contained")
        report["private_sentinels_found"] = sorted(
            {
                kind
                for item in report["journeys"]
                for kind in item.get("private_sentinels_found") or []
            }
        )
        report["max_provider_calls_per_attempt"] = max(
            (item.get("max_provider_calls_per_attempt") or 0)
            for item in report["journeys"]
        )
    except Exception as error:  # noqa: BLE001 - sanitize at this private harness boundary
        report["error"] = type(error).__name__
        if isinstance(error, Cancelled):
            report["outcome"] = "cancelled"
        print(
            "execution qualification failed; private evidence retained", file=sys.stderr
        )
    finally:
        after = census(raw, docker=docker)
        receipts = [
            json.loads(p.read_text()) for p in evidence.glob("command-*.json")
        ] + [
            json.loads(p.read_text())
            for p in evidence.glob("case-*/process-receipt.json")
        ]
        resources = after.receipt(before)
        journeys_clean = (
            all(
                item.get("cleanup_confirmed") is True
                and item.get("process_cleanup_confirmed") is True
                for item in report["journeys"]
            )
            and len(report["journeys"]) == 2
        )
        report["cleanup_receipt"] = {
            "commands": len(receipts),
            "commands_confirmed": all(
                item.get("cleanup_confirmed") is True for item in receipts
            ),
            "resources": resources,
            "journeys_confirmed": journeys_clean,
            "confirmed": resources["cleanup_confirmed"]
            and journeys_clean
            and all(item.get("cleanup_confirmed") is True for item in receipts),
        }
        if report.get("error") is None and report["outcome"] != "cancelled":
            report["outcome"] = (
                "passed"
                if all(item.get("outcome") == "passed" for item in report["journeys"])
                and report["cleanup_receipt"]["confirmed"]
                else "failed"
            )
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        os.chmod(report_path, 0o600)
    problems = verify_report(report)
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        with cancellation_signals():
            outcome = main()
    except Exception:  # noqa: BLE001 - sanitize at this private harness boundary
        print("execution qualification input refused", file=sys.stderr)
        outcome = 1
    raise SystemExit(outcome)
