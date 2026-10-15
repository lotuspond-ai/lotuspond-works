#!/usr/bin/env python3
"""Test-only installed-artifact journey. Never a production bootstrap command."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_RECEIPTS = None
_SEQUENCE = 0


def run(args, root, *, env=None, timeout=240):
    result = subprocess.run(
        args, cwd=root, env=env, capture_output=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError("qualification subprocess failed")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-file", type=Path, required=True)
    parser.add_argument("--expected-data-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    # Reuse the existing independently reviewed guard without changing its harness.
    spec = importlib.util.spec_from_file_location(
        "runner_guard", ROOT / "scripts/checks/runner-smoke.py"
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    from lotuspond_runtime.state import read_private_file

    report = args.report.absolute()
    # Refuse symlink/private-parent attacks and overwriting prior evidence.
    read_private_file(args.database_url_file.absolute(), 4096)
    from lotuspond_runtime.state import private_directory

    descriptor = private_directory(report.parent)
    os.close(descriptor)
    with guard._private_file(report) as output:
        output.write(b"{}\n")
    evidence = Path(
        tempfile.mkdtemp(prefix="lotuspond-fleet-artifacts-", dir=report.parent)
    )
    os.chmod(evidence, 0o700)
    global _RECEIPTS
    _RECEIPTS = evidence
    data = {
        "source_commit": run(["git", "rev-parse", "HEAD"], ROOT).decode().strip(),
        "source_dirty": bool(
            run(["git", "status", "--porcelain", "--untracked-files=normal"], ROOT)
        ),
        "required_postgres_skips": 0,
        "journeys": [],
        "cleanup_confirmed": False,
        "outcome": "failed",
    }
    administration = None
    try:
        raw = (
            read_private_file(args.database_url_file.absolute(), 4096).decode().strip()
        )
        _, administration = guard._database_target(raw, args.expected_data_directory)
        administration.dispose()
        administration = None
        source = {}
        for directory in (
            "backend/lotuspond",
            "runtime/python",
            "sdk/python",
            "scripts/checks",
        ):
            for path in sorted((ROOT / directory).rglob("*")):
                if path.is_file() and not any(
                    part
                    in {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}
                    for part in path.parts
                ):
                    source[str(path.relative_to(ROOT))] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
        (evidence / "source-manifest.json").write_text(
            json.dumps(source, indent=2) + "\n"
        )
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
        fixture = evidence / "fixture"
        fixture.mkdir(mode=0o700)
        for src, dst in (
            ("backend/tests/fleet/acceptance_harness.py", "harness.py"),
            ("backend/tests/fleet/acceptance_worker.py", "worker.py"),
            ("backend/tests/fleet/test_migration.py", "legacy_fixture.py"),
            ("runtime/python/tests/command_fixtures.py", "command_fixture.py"),
            ("runtime/python/tests/tls_fixtures.py", "tls_fixture.py"),
            ("backend/tests/fleet/fixtures/schema-v12.sql", "schema-v12.sql"),
        ):
            shutil.copyfile(ROOT / src, fixture / dst)
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
            # URLs remain private files, never argv/environment/logs.
            config = case / "private-database-url"
            config.write_text(raw)
            config.chmod(0o600)
            process = subprocess.Popen(
                [
                    python,
                    "-I",
                    str(fixture / "harness.py"),
                    str(config),
                    str(args.expected_data_directory),
                    str(case),
                ],
                cwd=case,
                env=clean,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=210)
            except subprocess.TimeoutExpired:
                timed_out = True
                # Test harness handles TERM by running its owned-resource finally block.
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    stdout, stderr = process.communicate(timeout=3)
            (case / "process-receipt.json").write_text(
                json.dumps(
                    {
                        "pid": process.pid,
                        "exit": process.returncode,
                        "timed_out": timed_out,
                    }
                )
                + "\n"
            )
            # Harness emits fixed assertion locations only. Preserve actual failed evidence privately.
            (case / "harness.stdout").write_bytes(stdout)
            (case / "harness.stderr").write_bytes(stderr)
            if process.returncode or timed_out:
                raise RuntimeError("installed journey failed")
            item = json.loads((case / "result.json").read_text())
            item["artifact_variant"] = variant
            data["journeys"].append(item)
        data["artifacts"] = {
            str(p.relative_to(evidence)): hashlib.sha256(p.read_bytes()).hexdigest()
            for d in ("sdk", "runtime", "rebuilt", "api")
            for p in sorted((evidence / d).iterdir())
        }
        data["source_manifest_sha256"] = hashlib.sha256(
            (evidence / "source-manifest.json").read_bytes()
        ).hexdigest()
        data["cleanup_confirmed"] = all(
            item["cleanup_confirmed"] for item in data["journeys"]
        )
        data["outcome"] = "passed"
    except Exception:  # noqa: BLE001 - sanitize all failures at this private harness boundary
        cases = list(evidence.glob("case-*"))
        data["cleanup_confirmed"] = bool(cases) and all(
            (case / "cleanup.json").is_file()
            and json.loads((case / "cleanup.json").read_text()).get("cleanup_confirmed")
            is True
            for case in cases
        )
        print("fleet qualification failed; private evidence retained", file=sys.stderr)
    finally:
        if administration is not None:
            administration.dispose()
        report.write_text(json.dumps(data, indent=2) + "\n")
    return 0 if data["outcome"] == "passed" and data["cleanup_confirmed"] else 1


if __name__ == "__main__":
    try:
        outcome = main()
    except Exception:  # noqa: BLE001 - sanitize all failures at this private harness boundary
        print("fleet qualification input refused", file=sys.stderr)
        outcome = 1
    raise SystemExit(outcome)
