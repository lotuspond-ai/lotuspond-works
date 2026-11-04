import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from test_verify_airgap_bundle import EXECUTION_ROOT, BundleFixture, manifest_for
from verify_airgap_bundle import VerificationError

spec = importlib.util.spec_from_file_location(
    "execution_smoke", Path(__file__).with_name("execution-smoke.py")
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class ExecutionBundleInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inventory = {
            "schema": "lotuspond.offline-execution-images.v1",
            "images": {key: "sha256:" + "a" * 64 for key in smoke.IMAGE_KEYS},
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_rejects_mutable_missing_or_service_images(self):
        path = self.root / "images.json"
        path.write_text(json.dumps(self.inventory))
        self.assertEqual(smoke.image_inventory(path), self.inventory["images"])
        invalid = [
            {**self.inventory["images"], "LOTUSPOND_EXECUTION_IMAGE": "lotuspond-execution:latest"},
            {},
            {**self.inventory["images"], "LOTUSPOND_GYM_REAL_APP_IMAGE": "sha256:" + "c" * 64},
            {
                **self.inventory["images"],
                "LOTUSPOND_API_IMAGE": "sha256:" + "b" * 64,
            },
        ]
        for images in invalid:
            with self.subTest(images=sorted(images)):
                path.write_text(json.dumps({**self.inventory, "images": images}))
                with self.assertRaises(ValueError):
                    smoke.image_inventory(path)

    def test_extracts_only_the_image_inputs(self):
        fixture = BundleFixture(self.root)
        files = {
            "images.json": json.dumps(self.inventory).encode(),
            "images.tar": b"synthetic-image-not-loaded",
            "INSTALL.md": b"This archive content is never executed by the drill.\n",
            "gym-kit/src/lotuspond_sandbox/real_app.py": b"print('not executed')\n",
        }
        manifest = manifest_for(files)
        members = [(fixture.directory_member(EXECUTION_ROOT), b"")]
        members.extend(
            (fixture.file_member(f"{EXECUTION_ROOT}/{name}", data), data)
            for name, data in files.items()
        )
        members.append(
            (fixture.file_member(f"{EXECUTION_ROOT}/MANIFEST.sha256", manifest), manifest)
        )
        fixture.write(members)
        private = self.root / "private"
        private.mkdir(mode=0o700)
        proof = smoke.verified_inputs(fixture.archive, fixture.checksum, private)
        self.assertEqual(proof.root, EXECUTION_ROOT)
        self.assertEqual(
            {path.relative_to(private).as_posix() for path in private.rglob("*") if path.is_file()},
            {"images.json", "images.tar"},
        )
        self.assertFalse((private / "INSTALL.md").exists())
        self.assertEqual((private / "images.tar").read_bytes(), files["images.tar"])

    def test_rejects_bad_checksum_before_copying_runtime_inputs(self):
        fixture = BundleFixture(self.root)
        files = {
            "images.json": json.dumps(self.inventory).encode(),
            "images.tar": b"synthetic-image-not-loaded",
        }
        manifest = manifest_for(files)
        members = [(fixture.directory_member(EXECUTION_ROOT), b"")]
        members.extend(
            (fixture.file_member(f"{EXECUTION_ROOT}/{name}", data), data)
            for name, data in files.items()
        )
        members.append(
            (fixture.file_member(f"{EXECUTION_ROOT}/MANIFEST.sha256", manifest), manifest)
        )
        fixture.write(members)
        fixture.checksum.write_text("0" * 64 + "\n")
        private = self.root / "private"
        private.mkdir(mode=0o700)
        with self.assertRaises(VerificationError):
            smoke.verified_inputs(fixture.archive, fixture.checksum, private)
        self.assertFalse((private / "images.tar").exists())

    def test_embedded_probes_are_valid_javascript_modules(self):
        for name, source in (("http", smoke.HTTP_PROBE), ("mcp", smoke.MCP_PROBE)):
            with self.subTest(name=name):
                self.assertIn("127.0.0.1", source)
                self.assertNotIn("docker.sock", source)
                self.assertNotIn("LOTUSPOND_API_KEY", source)
