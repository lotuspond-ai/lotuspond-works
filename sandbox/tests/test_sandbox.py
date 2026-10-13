from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from lotuspond_sandbox.auth import issue_grant, load_grant, verify_grant, write_grant
from lotuspond_sandbox.launcher import build_docker_argv, launch
from lotuspond_sandbox.manifest import (
    CONSTRAINTS_SHA256,
    SCHEMA_VERSION,
    UPSTREAM_REPOSITORY,
    UPSTREAM_REVISION,
    ManifestError,
    load_manifest,
    parse_manifest,
)

KEY = bytes.fromhex("12" * 32)
IMAGE = "sha256:" + "ab" * 32


def manifest_data() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": "gym-reviewed",
        "provider": "gym-anything",
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_revision": UPSTREAM_REVISION,
        "constraints_sha256": CONSTRAINTS_SHA256,
        "runtime_image": IMAGE,
        "runner": "local",
        "network": "none",
        "read_only": True,
        "environments": [
            {
                "environment_id": "moodle",
                "cli_name": "moodle",
                "path": "benchmarks/cua_world/environments/moodle_env",
                "tasks": ["enroll_student"],
            }
        ],
        "limits": {
            "cpus": 2,
            "memory_mb": 4096,
            "pids": 256,
            "timeout_seconds": 300,
            "tmpfs_mb": 512,
            "max_artifact_bytes": 16_777_216,
        },
    }


def grant(**updates: object):
    values = {
        "key": KEY,
        "run_id": "run-one",
        "manifest_id": "gym-reviewed",
        "environment_id": "moodle",
        "task_id": "enroll_student",
        "steps": 5,
        "seed": 42,
        "expires_at": int(time.time()) + 120,
        "nonce": "n0123456789abcdef",
    }
    values.update(updates)
    return issue_grant(**values)


class FakeExecutor:
    def __init__(
        self, *, exit_code: int = 0, timeout: bool = False, cleanup_fails: bool = False
    ) -> None:
        self.exit_code = exit_code
        self.timeout = timeout
        self.cleanup_fails = cleanup_fails
        self.argv: tuple[str, ...] | None = None
        self.removed: str | None = None

    def probe(self) -> str:
        return "test-server"

    def run(self, argv: tuple[str, ...], timeout: int) -> int:
        self.argv = argv
        if self.timeout:
            raise TimeoutError
        return self.exit_code

    def remove(self, container_name: str) -> None:
        self.removed = container_name
        if self.cleanup_fails:
            raise RuntimeError("cleanup failed")


class ManifestTests(unittest.TestCase):
    def test_manifest_accepts_reviewed_pins(self) -> None:
        manifest = parse_manifest(manifest_data())
        self.assertEqual(
            manifest.environment("moodle", "enroll_student").cli_name, "moodle"
        )

    def test_manifest_rejects_mutable_image_or_egress(self) -> None:
        for field, value in (
            ("runtime_image", "gym-anything:latest"),
            ("network", "bridge"),
        ):
            data = manifest_data()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ManifestError):
                parse_manifest(data)

    def test_manifest_digest_format_is_exact(self) -> None:
        for image in (
            "sha256:" + "AB" * 32,
            "sha256:" + "ab" * 31,
            "registry.example/team/gym@sha256:" + "ab" * 33,
        ):
            data = manifest_data()
            data["runtime_image"] = image
            with self.subTest(image=image), self.assertRaises(ManifestError):
                parse_manifest(data)
        data = manifest_data()
        data["runtime_image"] = "registry.example/team/gym@sha256:" + "ab" * 32
        self.assertEqual(parse_manifest(data).runtime_image, data["runtime_image"])

    def test_manifest_rejects_symlink_file_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifests = root / "manifests"
            manifests.mkdir()
            target = root / "target.json"
            target.write_text(json.dumps(manifest_data()), encoding="utf-8")
            (manifests / "gym-reviewed.json").symlink_to(target)
            with self.assertRaises(ManifestError):
                load_manifest("gym-reviewed", manifests)
            alias = root / "manifest-alias"
            alias.symlink_to(manifests, target_is_directory=True)
            with self.assertRaises(ManifestError):
                load_manifest("gym-reviewed", alias)

    def test_manifest_rejects_unknown_fields_and_unreviewed_tasks(self) -> None:
        data = manifest_data()
        data["extra"] = True
        with self.assertRaises(ManifestError):
            parse_manifest(data)
        with self.assertRaises(ManifestError):
            parse_manifest(manifest_data()).environment("moodle", "delete_everything")


class AuthorizationTests(unittest.TestCase):
    def test_grant_authentication_is_bound_to_run(self) -> None:
        value = grant()
        verify_grant(value, KEY)
        with self.assertRaises(ManifestError):
            verify_grant(replace(value, task_id="delete_everything"), KEY)

    def test_expired_grant_fails_closed(self) -> None:
        value = grant()
        with self.assertRaises(ManifestError):
            verify_grant(replace(value, expires_at=int(time.time()) - 1), KEY)

    def test_grant_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "grant.json"
            write_grant(target, grant())
            alias = root / "alias.json"
            alias.symlink_to(target)
            with self.assertRaises(ManifestError):
                load_grant(alias)


class LauncherTests(unittest.TestCase):
    def test_argv_is_fixed_non_shell_and_no_egress(self) -> None:
        manifest = parse_manifest(manifest_data())
        argv = build_docker_argv(manifest, grant(), Path("/trusted/artifacts"))
        self.assertEqual(argv[0:2], ("docker", "run"))
        self.assertIn("--network=none", argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--pull=never", argv)
        self.assertIn("GYM_ANYTHING_RUNNER=local", argv)
        self.assertNotIn("sh", argv)
        mount = argv[argv.index("--mount") + 1]
        self.assertEqual(mount, "type=bind,src=/trusted/artifacts,dst=/artifacts")
        self.assertIn(
            "/opt/gym-anything/benchmarks/cua_world/environments/moodle_env", argv
        )
        self.assertEqual(
            argv[-6:],
            (
                "--task",
                "enroll_student",
                "--steps",
                "5",
                "--seed",
                "42",
            ),
        )

    def test_launch_writes_receipt_and_refuses_replay(self) -> None:
        manifest = parse_manifest(manifest_data())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = FakeExecutor()
            receipt = launch(
                manifest, grant(), key=KEY, runs_directory=root, executor=executor
            )
            self.assertEqual(receipt.status, "succeeded")
            saved = json.loads((root / "run-one" / "receipt.json").read_text())
            self.assertEqual(saved["image"], IMAGE)
            self.assertEqual(saved["runner"], "local")
            with self.assertRaises(FileExistsError):
                launch(
                    manifest, grant(), key=KEY, runs_directory=root, executor=executor
                )

    def test_timeout_forces_container_cleanup(self) -> None:
        manifest = parse_manifest(manifest_data())
        with tempfile.TemporaryDirectory() as directory:
            executor = FakeExecutor(timeout=True)
            receipt = launch(
                manifest,
                grant(run_id="run-timeout"),
                key=KEY,
                runs_directory=Path(directory),
                executor=executor,
            )
            self.assertEqual(receipt.status, "timed_out")
            self.assertEqual(executor.removed, "lotuspond-gym-run-timeout")

    def test_timeout_reports_unconfirmed_cleanup(self) -> None:
        manifest = parse_manifest(manifest_data())
        with tempfile.TemporaryDirectory() as directory:
            executor = FakeExecutor(timeout=True, cleanup_fails=True)
            receipt = launch(
                manifest,
                grant(run_id="run-cleanup-failed"),
                key=KEY,
                runs_directory=Path(directory),
                executor=executor,
            )
            self.assertEqual(receipt.status, "cleanup_failed")
            self.assertIn("could not be confirmed", receipt.failure or "")

    def test_runs_directory_rejects_symlink(self) -> None:
        manifest = parse_manifest(manifest_data())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "runs"
            runs.mkdir()
            alias = root / "alias"
            alias.symlink_to(runs, target_is_directory=True)
            with self.assertRaises(ManifestError):
                launch(
                    manifest,
                    grant(run_id="run-symlink-root"),
                    key=KEY,
                    runs_directory=alias,
                    executor=FakeExecutor(),
                )

    def test_artifact_symlink_is_rejected(self) -> None:
        class SymlinkArtifactExecutor(FakeExecutor):
            def run(self, argv: tuple[str, ...], timeout: int) -> int:
                mount = argv[argv.index("--mount") + 1]
                source = Path(mount.split(",")[1].removeprefix("src="))
                outside = source.parent / "outside.txt"
                outside.write_text("outside", encoding="utf-8")
                os.symlink(outside, source / "escaped")
                return 0

        manifest = parse_manifest(manifest_data())
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ManifestError, "symbolic link"),
        ):
            launch(
                manifest,
                grant(run_id="run-artifact-link"),
                key=KEY,
                runs_directory=Path(directory),
                executor=SymlinkArtifactExecutor(),
            )


if __name__ == "__main__":
    unittest.main()
