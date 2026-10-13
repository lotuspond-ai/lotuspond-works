"""Exercise the local CLI against real files without API or database fixtures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class PackageCliTests(unittest.TestCase):
    backend = Path(__file__).resolve().parents[1]
    root = backend.parent

    def invoke(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "lotuspond.package_cli", "validate", str(path)],
            cwd=self.backend,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PYTHONPATH": str(self.backend)},
        )

    def invalid_file(self, data: bytes) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_bytes(data)
            result = self.invoke(path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertFalse(report["ok"])
        self.assertGreater(len(report["errors"]), 0)
        return report

    def test_example_validates_from_backend_directory(self):
        result = self.invoke(self.root / "contracts/examples/reviewed-action.v1.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertRegex(report["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(result.stderr, "")

    def test_duplicate_keys_including_nested_keys_are_rejected(self):
        for data in (b'{"x":1,"x":2}', b'{"x":{"nested":1,"nested":2}}'):
            with self.subTest(data=data):
                report = self.invalid_file(data)
                self.assertEqual(report["errors"][0]["code"], "duplicate_key")

    def test_nonfinite_numbers_are_rejected_even_when_json_parser_accepts_them(self):
        for number in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(number=number):
                report = self.invalid_file(f'{{"value":{number}}}'.encode())
                self.assertEqual(report["errors"][0]["code"], "nonfinite_number")

    def test_invalid_utf8_json_and_deep_input_fail_safely(self):
        for data, expected in (
            (b"\xff", "invalid_utf8"),
            (b"{", "invalid_json"),
            (b"[" * 40 + b"0" + b"]" * 40, "input_too_deep"),
            (b'{"value":' + b"9" * 5000 + b"}", "number_too_large"),
        ):
            with self.subTest(expected=expected):
                report = self.invalid_file(data)
                self.assertEqual(report["errors"][0]["code"], expected)

    def test_escaped_lone_surrogate_is_rejected_without_a_traceback(self):
        report = self.invalid_file(b'{"name":"\\ud800"}')
        self.assertEqual(report["errors"][0]["code"], "invalid_unicode")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Named pipes are unavailable on this platform")
    def test_named_pipe_is_rejected_without_waiting_for_a_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input-pipe"
            os.mkfifo(path)
            result = self.invoke(path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)["errors"][0]["code"], "file_unreadable")

    def test_brackets_and_escaped_quotes_inside_strings_do_not_count_as_depth(self):
        example = self.root / "contracts/examples/reviewed-action.v1.json"
        raw = json.loads(example.read_text())
        raw["nodes"][2]["instructions"] = 'Check the literal string "[{". ' * 40
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(raw))
            result = self.invoke(path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_oversized_file_is_rejected_before_parsing(self):
        report = self.invalid_file(b" " * (1024 * 1024 + 1))
        self.assertEqual(report["errors"][0]["code"], "input_too_large")

    def test_missing_or_nonregular_file_does_not_echo_path(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in (Path(directory), Path(directory) / "sensitive-unreadable-name"):
                with self.subTest(path_type="directory" if path.is_dir() else "missing"):
                    result = self.invoke(path)
                    self.assertEqual(result.returncode, 2)
                    report = json.loads(result.stdout)
                    self.assertFalse(report["ok"])
                    self.assertNotIn(str(path), result.stdout + result.stderr)

    def test_validation_errors_never_echo_raw_values_or_unknown_field_names(self):
        example = self.root / "contracts/examples/reviewed-action.v1.json"
        self.assertTrue(example.is_file(), "The contract example must exist.")
        raw = json.loads(example.read_text())
        marker = "sensitive-value-not-for-output"
        raw["nodes"][2]["limits"]["max_steps"] = marker
        raw[marker] = {"api_key": marker}
        report = self.invalid_file(json.dumps(raw).encode())
        self.assertNotIn(marker, json.dumps(report))
        self.assertTrue(any("max_steps" in error["path"] for error in report["errors"]))

    def test_invalid_node_discriminator_does_not_echo_its_value(self):
        raw = json.loads((self.root / "contracts/examples/reviewed-action.v1.json").read_text())
        marker = "secret-placed-in-a-node-kind"
        raw["nodes"][2]["kind"] = marker
        report = self.invalid_file(json.dumps(raw).encode())
        self.assertNotIn(marker, json.dumps(report))

    def test_schema_export_check_runs_without_a_database(self):
        result = subprocess.run(
            [sys.executable, "export_package_schema.py", "--check"],
            cwd=self.backend,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
