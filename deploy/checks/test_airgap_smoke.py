import importlib.util
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_verify_airgap_bundle import BundleFixture
from verify_airgap_bundle import VerificationError

spec = importlib.util.spec_from_file_location(
    "airgap_smoke", Path(__file__).with_name("airgap-smoke.py")
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class OfflineInstallationInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inventory = {
            "schema": "lotuspond.offline-images.v1",
            "images": {key: "sha256:" + "a" * 64 for key in smoke.IMAGE_KEYS},
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_rejects_mutable_missing_or_extra_images(self):
        path = self.root / "images.json"
        path.write_text(json.dumps(self.inventory))
        self.assertEqual(smoke.image_inventory(path), self.inventory["images"])
        invalid = [
            {**self.inventory["images"], "LOTUSPOND_API_IMAGE": "lotuspond-api:latest"},
            {
                key: value
                for key, value in self.inventory["images"].items()
                if key != "POSTGRES_IMAGE"
            },
            {**self.inventory["images"], "UNREVIEWED_IMAGE": "sha256:" + "b" * 64},
        ]
        for images in invalid:
            with self.subTest(images=sorted(images)):
                path.write_text(json.dumps({**self.inventory, "images": images}))
                with self.assertRaises(ValueError):
                    smoke.image_inventory(path)

    def test_extracts_only_fixed_verified_inputs(self):
        fixture = BundleFixture(self.root)
        files = {
            "images.json": json.dumps(self.inventory).encode(),
            "images.tar": b"synthetic-image-not-loaded",
            "schema.sql": b"SELECT 1;\n",
            "INSTALL.md": b"This archive content is never executed by the drill.\n",
        }
        fixture.write(fixture.valid_members(files=files))
        private = self.root / "private"
        private.mkdir(mode=0o700)
        proof = smoke.verified_inputs(fixture.archive, fixture.checksum, private)
        self.assertEqual(proof.regular_files, 5)
        self.assertEqual(
            {path.name for path in private.iterdir()},
            {"images.json", "images.tar", "schema.sql"},
        )
        for name in ("images.json", "images.tar", "schema.sql"):
            self.assertEqual((private / name).read_bytes(), files[name])

    def test_rejects_bad_checksum_before_copying_any_runtime_inputs(self):
        fixture = BundleFixture(self.root)
        fixture.write(fixture.valid_members())
        fixture.checksum.write_text("0" * 64 + "\n")
        private = self.root / "private"
        private.mkdir(mode=0o700)
        with self.assertRaises(VerificationError):
            smoke.verified_inputs(fixture.archive, fixture.checksum, private)
        self.assertFalse((private / "images.tar").exists())

    def test_environment_files_are_private_and_reject_line_injection(self):
        path = smoke.environment_file(
            self.root, "runtime.env", {"TEST_VALUE": "synthetic"}
        )
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            smoke.environment_file(
                self.root, "bad.env", {"TEST_VALUE": "one\nANOTHER=value"}
            )

    def test_owned_container_actions_verify_exact_name_and_label(self):
        inspected = {
            "Name": "/lotuspond-offline-run-worker",
            "Config": {"Labels": {smoke.LABEL: "run"}},
        }
        with patch.object(
            smoke,
            "command",
            side_effect=[SimpleNamespace(stdout=json.dumps([inspected]).encode()), SimpleNamespace()],
        ) as called:
            smoke.owned_container_action("lotuspond-offline-run-worker", "run", "stop")
        self.assertEqual(
            called.call_args_list[1].args,
            (
                "docker",
                "container",
                "stop",
                "--time",
                "10",
                "lotuspond-offline-run-worker",
            ),
        )

        inspected["Config"]["Labels"][smoke.LABEL] = "another-run"
        with patch.object(
            smoke,
            "command",
            return_value=SimpleNamespace(stdout=json.dumps([inspected]).encode()),
        ) as called, self.assertRaisesRegex(RuntimeError, "ownership"):
            smoke.owned_container_action(
                "lotuspond-offline-run-worker", "run", "start"
            )
        called.assert_called_once()

    def test_failure_recovery_is_optional_and_metrics_use_distinct_token(self):
        parameter = inspect.signature(smoke.drill).parameters["failure_recovery"]
        self.assertFalse(parameter.default)
        self.assertIn("COLLECTOR_METRICS_TOKEN", Path(smoke.__file__).read_text())
        self.assertIn('data["metrics_token"]', smoke.VERIFY)
        self.assertIn('data["ingest_token"]', smoke.VERIFY)
        self.assertIn('"collector_metrics_text": metrics', smoke.VERIFY)
        self.assertIn('observation["state"] == "stale"', smoke.FAILURE_OBSERVE)
        for program in (
            smoke.SETUP,
            smoke.VERIFY,
            smoke.FAILURE_ENQUEUE,
            smoke.FAILURE_OBSERVE,
            smoke.REVOKE,
        ):
            compile(program, "<embedded-airgap-check>", "exec")


class PackagedSchemaVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "schema.sql"

    def test_reads_the_version_the_bundle_installs(self):
        self.path.write_text(
            "BEGIN;\nCREATE TABLE workspaces (id VARCHAR(36));\n"
            "INSERT INTO schema_version (version, installed_at) VALUES (10, CURRENT_TIMESTAMP);\n"
            "COMMIT;\n"
        )
        self.assertEqual(smoke.packaged_schema_version(self.path), 10)

    def test_refuses_a_missing_or_ambiguous_declaration(self):
        for content in (
            "BEGIN;\nCOMMIT;\n",
            "INSERT INTO schema_version (version, installed_at) VALUES (9, CURRENT_TIMESTAMP);\n"
            "INSERT INTO schema_version (version, installed_at) VALUES (10, CURRENT_TIMESTAMP);\n",
        ):
            self.path.write_text(content)
            with self.assertRaises(ValueError):
                smoke.packaged_schema_version(self.path)

    def test_the_drill_compares_the_running_api_against_the_packaged_version(self):
        # A literal here would silently go stale the next time the model moves on.
        self.assertIn('ready["schema_version"] == data["schema_version"]', smoke.VERIFY)
        self.assertNotIn('ready["schema_version"] == 8', smoke.VERIFY)


class FailureDetailTests(unittest.TestCase):
    def test_failures_report_stderr_with_registered_secrets_removed(self):
        secret = "drill-only-token-value"
        smoke.EPHEMERAL_SECRETS.add(secret)
        self.addCleanup(smoke.EPHEMERAL_SECRETS.discard, secret)
        with self.assertRaises(RuntimeError) as raised:
            smoke.command(
                "python3",
                "-c",
                f'import sys; sys.stderr.write("boom {secret}"); sys.exit(1)',
            )
        message = str(raised.exception)
        self.assertIn("boom", message)
        self.assertIn("[redacted]", message)
        self.assertNotIn(secret, message)


class InstalledSdkProbeTests(unittest.TestCase):
    def test_image_probe_retains_v1_and_literal_v2_oracles(self):
        # This exact script runs in the immutable installed API image during the drill.
        # Here the literal compile portion runs locally; installed image identity is a separate drill gate.
        import subprocess
        import sys
        source = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "-c", smoke.V2_SDK_PROBE],
            input=(source / "contracts/examples/reviewed-action.v1.json").read_bytes(),
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
