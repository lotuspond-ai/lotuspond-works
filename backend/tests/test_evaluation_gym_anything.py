from __future__ import annotations

import unittest

from pydantic import ValidationError

from lotuspond.evaluation.gym_anything import (
    GYM_ANYTHING_REPOSITORY,
    GymAnythingLaunchSpec,
    build_gym_anything_launch_spec,
    parse_gym_anything_catalog,
)

REVISION = "f3dc04649641dba69d5032f7bb6adaa9785c150d"


def manifest() -> dict[str, object]:
    return {
        "schema_version": "lotuspond.gym-anything.catalog.v1",
        "repository": GYM_ANYTHING_REPOSITORY,
        "revision": REVISION,
        "environments": [
            {
                "environment_id": "moodle",
                "cli_name": "moodle",
                "path": "benchmarks/cua_world/environments/moodle_env",
                "tasks": ["enroll_student"],
            }
        ],
    }


class GymAnythingTests(unittest.TestCase):
    def test_catalog_builds_non_interactive_argv_without_shell(self) -> None:
        catalog = parse_gym_anything_catalog(manifest())
        spec = build_gym_anything_launch_spec(
            catalog,
            environment_id="moodle",
            task_id="enroll_student",
            use_cache=True,
        )
        self.assertEqual(
            spec.argv(),
            (
                "gym-anything",
                "run",
                "moodle",
                "--task",
                "enroll_student",
                "--use-cache",
                "--cache-level",
                "default",
            ),
        )
        self.assertFalse(spec.interactive)
        self.assertFalse(spec.open_vnc)

    def test_repository_revision_paths_and_ids_are_strict(self) -> None:
        cases = []
        bad_repo = manifest()
        bad_repo["repository"] = "https://example.invalid/repository"
        cases.append(bad_repo)
        bad_revision = manifest()
        bad_revision["revision"] = "main"
        cases.append(bad_revision)
        bad_path = manifest()
        bad_path["environments"][0]["path"] = "benchmarks/cua_world/environments/../unsafe"
        cases.append(bad_path)
        bad_task = manifest()
        bad_task["environments"][0]["tasks"] = ["task;rm"]
        cases.append(bad_task)

        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValidationError):
                    parse_gym_anything_catalog(case)

    def test_unreviewed_task_cannot_launch(self) -> None:
        catalog = parse_gym_anything_catalog(manifest())
        with self.assertRaises(ValueError):
            build_gym_anything_launch_spec(catalog, environment_id="moodle", task_id="unreviewed")

    def test_launch_spec_cannot_bypass_catalog_identifier_rules(self) -> None:
        with self.assertRaises(ValidationError):
            GymAnythingLaunchSpec(
                repository=GYM_ANYTHING_REPOSITORY,
                revision=REVISION,
                environment_id="moodle",
                cli_name="moodle;unsafe",
                environment_path="benchmarks/cua_world/environments/moodle_env",
                task_id="enroll_student",
            )


if __name__ == "__main__":
    unittest.main()
