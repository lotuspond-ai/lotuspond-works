"""Explicit v2 CLI admission, privacy, output and producer integration."""

import json
import os
from pathlib import Path

import pytest

from lotuspond_sdk.cli import main


@pytest.fixture
def sources(tmp_path, case):
    def write(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return path

    return write


def call(capsys, *args):
    status = main(list(map(str, args)))
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.isascii()
    return status, json.loads(captured.out)


def test_explicit_validation_and_literal_plan(capsys, sources, case, tmp_path):
    path = sources("package", case("minimal"))
    catalog = sources("catalog", {"documents": []})
    status, result = call(capsys, "v2", "validate", path)
    assert status == 0
    assert result["result"]["compilation_status"] == "not_checked"
    assert result["result"]["authority_granted"] is False
    assert call(capsys, "validate", path)[0] == 2
    output = tmp_path / "plan"
    status, report = call(capsys, "v2", "compile", path, "--catalog", catalog, "--output", output)
    assert status == 0
    expected = Path(__file__).parents[1] / "fixtures/executable-v2/plan_minimal.jcs"
    assert output.read_bytes() == expected.read_bytes()
    assert report["result"]["authority_granted"] is False
    assert call(capsys, "v2", "compile", path, "--catalog", catalog, "--output", output)[0] == 2


@pytest.mark.parametrize(
    "command",
    [
        "validate",
        "digest",
        "canonicalize",
        "compile",
        "diff",
        "sign",
        "verify",
        "compatibility",
        "schema",
    ],
)
def test_unknown_arguments_are_private(command, capsys):
    status, report = call(capsys, "v2", command, "--SENTINEL_PRIVATE")
    assert status == 2
    assert "SENTINEL" not in json.dumps(report)
    assert report["errors"][0]["code"] == "invalid_arguments"


@pytest.mark.parametrize("content", [b'{"x":1,"x":2}', b"{", b" " * 1048577])
def test_bad_inputs_are_bounded(content, tmp_path, capsys):
    path = tmp_path / "SENTINEL_PRIVATE"
    path.write_bytes(content)
    status, report = call(capsys, "v2", "validate", path)
    assert status == 2
    assert "SENTINEL" not in json.dumps(report)


def test_fifo_does_not_wait(tmp_path, capsys):
    path = tmp_path / "pipe"
    os.mkfifo(path)
    assert call(capsys, "v2", "validate", path)[0] == 2


def test_canonical_diff_and_dependency_limit(sources, case, tmp_path, capsys):
    path = sources("package", case("minimal"))
    canonical = tmp_path / "canonical"
    assert call(capsys, "v2", "canonicalize", path, "--output", canonical)[0] == 0
    assert call(capsys, "v2", "digest", canonical)[0] == 0
    changed = case("minimal")
    changed["name"] = "PRIVATE_SENTINEL"
    other = sources("other", changed)
    status, report = call(capsys, "v2", "diff", path, other)
    assert status == 0 and report["result"]["changes"]
    assert "PRIVATE_SENTINEL" not in json.dumps(report)
    assert call(capsys, "v2", "diff", path, other, "--include-values")[0] == 0
    args = ["v2", "compile", path, "--catalog", path, "--output", canonical]
    for _ in range(64):
        args.extend(["--dependency", path])
    assert call(capsys, *args)[0] == 2


def test_partial_write_retained(sources, case, tmp_path, capsys, monkeypatch):
    from lotuspond_sdk import files

    path = sources("package", case("minimal"))
    output = tmp_path / "partial"

    def failed(_):
        raise OSError("PRIVATE_SENTINEL")

    monkeypatch.setattr(files.os, "fsync", failed)
    status, report = call(capsys, "v2", "canonicalize", path, "--output", output)
    assert status == 2 and report["errors"][0]["code"] == "output_incomplete"
    assert output.is_file() and output.stat().st_size > 0
    assert "PRIVATE_SENTINEL" not in json.dumps(report)


def test_full_ascii_wrapper_bound(sources, case, capsys, monkeypatch):
    from lotuspond_sdk.executable import cli
    from lotuspond_sdk.executable._validation import report_bytes

    path = sources("package", case("minimal"))
    # The same complete wrapper must fit its newline; one fewer byte refuses.
    result = cli.ValidationResult(sha256="1" * 64)
    output = cli.Output(ok=True, result=result)
    data = report_bytes(
        output, model_type=cli.Output, model_types=cli.OUTPUT_MODELS, code="invalid_json_value"
    )

    def bounded(raw, **kwargs):
        return report_bytes(raw, **kwargs, max_bytes=len(data) - 1)

    monkeypatch.setattr(cli, "report_bytes", bounded)
    status, report = call(capsys, "v2", "validate", path)
    assert status == 2 and report["errors"][0]["code"] == "report_too_large"


def test_mutated_producer_report_cannot_bypass_admission(case, capsys, monkeypatch):
    from lotuspond_sdk.executable import cli
    from lotuspond_sdk.executable.diff import diff_executable_packages

    report = diff_executable_packages(case("minimal"), case("minimal"))
    report.authority_granted = 0
    monkeypatch.setattr(cli, "_execute", lambda args: (report, 0))
    assert call(capsys, "v2", "digest", "unused")[0] == 2
