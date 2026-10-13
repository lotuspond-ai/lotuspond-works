from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from lotuspond_sandbox.real_app import (
    RealAppError,
    build_run_argv,
    load_real_app_manifest,
    run_real_app,
)

BUNDLE = Path(__file__).parents[1] / "real-app"


class FailingLaunchDocker:
    def __init__(self) -> None:
        self.exists = False
        self.run_id = ""
        self.removed = False

    def run(
        self, argv: tuple[str, ...], *, timeout: float, binary: bool = False
    ) -> subprocess.CompletedProcess:
        del timeout, binary
        if argv[1:3] == ("image", "inspect"):
            return subprocess.CompletedProcess(
                argv, 0, stdout=argv[-1] + "\n", stderr=""
            )
        if argv[1] == "run":
            self.exists = True
            label = argv[argv.index("--label") + 1]
            self.run_id = label.removeprefix("com.lotuspond.sandbox.run=")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="failed")
        if argv[1:3] == ("container", "ls"):
            output = "created-container\n" if self.exists else ""
            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
        if argv[1:3] == ("inspect", "--format"):
            return subprocess.CompletedProcess(
                argv, 0, stdout=self.run_id + "\n", stderr=""
            )
        if argv[1:3] == ("rm", "--force"):
            self.exists = False
            self.removed = True
            return subprocess.CompletedProcess(argv, 0, stdout="removed\n", stderr="")
        raise AssertionError(f"unexpected Docker argv: {argv}")


class RealAppManifestTests(unittest.TestCase):
    def test_manifest_pins_reviewed_task_verifier_and_image(self) -> None:
        manifest = load_real_app_manifest(BUNDLE)
        argv = build_run_argv(manifest, "lotuspond-gym-test", "test")
        self.assertEqual(argv[-1], manifest.runtime_image)
        for required in (
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--user=10001:10001",
            "--pull=never",
        ):
            self.assertIn(required, argv)
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("--device", argv)
        self.assertNotIn("--volume", argv)
        self.assertNotIn("--mount", argv)

    def test_manifest_cannot_replace_verifier_and_hash_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(BUNDLE, bundle)
            verifier = bundle / "upstream" / "verifier.py"
            verifier.write_text("def check_greeting(*args): return {'passed': True}\n")
            raw = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            raw["verifier_sha256"] = hashlib.sha256(verifier.read_bytes()).hexdigest()
            (bundle / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(RealAppError, "reviewed Gym inputs"):
                load_real_app_manifest(bundle)

    def test_failed_launch_reconciles_created_container(self) -> None:
        fake = FailingLaunchDocker()
        with tempfile.TemporaryDirectory() as directory:
            receipt = run_real_app(BUNDLE, Path(directory), docker=fake)
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(receipt["cleanup_confirmed"])
        self.assertTrue(fake.removed)
        self.assertFalse(fake.exists)


if __name__ == "__main__":
    unittest.main()
