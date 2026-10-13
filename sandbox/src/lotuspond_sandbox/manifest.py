from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "lotuspond.sandbox.manifest.v1"
PROVIDER = "gym-anything"
UPSTREAM_REPOSITORY = "https://github.com/cmu-l3/gym-anything"
UPSTREAM_REVISION = "f3dc04649641dba69d5032f7bb6adaa9785c150d"
CONSTRAINTS_SHA256 = "0818490024cacfbaa221f21c428bd06a14b1045e2f70f647a3a01f0cbdf54f79"

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_IMAGE = re.compile(
    r"^(?:sha256:[0-9a-f]{64}|[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+@sha256:[0-9a-f]{64})$"
)
_ENVIRONMENT_PATH = re.compile(
    r"^benchmarks/cua_world/environments/[a-z][a-z0-9_-]{0,79}_env$"
)


class ManifestError(ValueError):
    pass


def _strict_object(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ManifestError(
            f"{label} must contain exactly: {', '.join(sorted(expected))}"
        )
    return value


def _integer(value: Any, minimum: int, maximum: int, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ManifestError(
            f"{label} must be an integer from {minimum} through {maximum}"
        )
    return value


@dataclass(frozen=True)
class Environment:
    environment_id: str
    cli_name: str
    path: str
    tasks: tuple[str, ...]


@dataclass(frozen=True)
class Limits:
    cpus: int
    memory_mb: int
    pids: int
    timeout_seconds: int
    tmpfs_mb: int
    max_artifact_bytes: int


@dataclass(frozen=True)
class SandboxManifest:
    manifest_id: str
    runtime_image: str
    runner: str
    environments: tuple[Environment, ...]
    limits: Limits

    def environment(self, environment_id: str, task_id: str) -> Environment:
        for environment in self.environments:
            if environment.environment_id == environment_id:
                if task_id not in environment.tasks:
                    raise ManifestError(
                        "task is not allowlisted by the operator manifest"
                    )
                return environment
        raise ManifestError("environment is not allowlisted by the operator manifest")


def parse_manifest(value: Any) -> SandboxManifest:
    data = _strict_object(
        value,
        {
            "schema_version",
            "manifest_id",
            "provider",
            "upstream_repository",
            "upstream_revision",
            "constraints_sha256",
            "runtime_image",
            "runner",
            "network",
            "read_only",
            "environments",
            "limits",
        },
        "manifest",
    )
    fixed = {
        "schema_version": SCHEMA_VERSION,
        "provider": PROVIDER,
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_revision": UPSTREAM_REVISION,
        "constraints_sha256": CONSTRAINTS_SHA256,
        "runner": "local",
        "network": "none",
        "read_only": True,
    }
    for field, expected in fixed.items():
        if data[field] != expected:
            raise ManifestError(f"{field} must equal the reviewed value {expected!r}")
    if not isinstance(data["manifest_id"], str) or not _IDENTIFIER.fullmatch(
        data["manifest_id"]
    ):
        raise ManifestError("manifest_id is invalid")
    if not isinstance(data["runtime_image"], str) or not _IMAGE.fullmatch(
        data["runtime_image"]
    ):
        raise ManifestError(
            "runtime_image must be an image id or registry image pinned by sha256 digest"
        )

    raw_environments = data["environments"]
    if not isinstance(raw_environments, list) or not 1 <= len(raw_environments) <= 100:
        raise ManifestError("environments must contain 1 through 100 entries")
    environments = []
    seen: set[str] = set()
    for raw in raw_environments:
        entry = _strict_object(
            raw, {"environment_id", "cli_name", "path", "tasks"}, "environment"
        )
        identifiers = (entry["environment_id"], entry["cli_name"])
        if not all(
            isinstance(item, str) and _IDENTIFIER.fullmatch(item)
            for item in identifiers
        ):
            raise ManifestError("environment identifiers are invalid")
        if entry["environment_id"] in seen:
            raise ManifestError("environment ids must be unique")
        seen.add(entry["environment_id"])
        if not isinstance(entry["path"], str) or not _ENVIRONMENT_PATH.fullmatch(
            entry["path"]
        ):
            raise ManifestError(
                "environment path is outside the reviewed upstream catalog"
            )
        tasks = entry["tasks"]
        if (
            not isinstance(tasks, list)
            or not 1 <= len(tasks) <= 5_000
            or len(tasks) != len(set(tasks))
            or not all(
                isinstance(task, str) and _IDENTIFIER.fullmatch(task) for task in tasks
            )
        ):
            raise ManifestError("tasks must be a unique list of valid task ids")
        environments.append(
            Environment(
                environment_id=entry["environment_id"],
                cli_name=entry["cli_name"],
                path=entry["path"],
                tasks=tuple(tasks),
            )
        )

    raw_limits = _strict_object(
        data["limits"],
        {
            "cpus",
            "memory_mb",
            "pids",
            "timeout_seconds",
            "tmpfs_mb",
            "max_artifact_bytes",
        },
        "limits",
    )
    limits = Limits(
        cpus=_integer(raw_limits["cpus"], 1, 8, "limits.cpus"),
        memory_mb=_integer(raw_limits["memory_mb"], 512, 32_768, "limits.memory_mb"),
        pids=_integer(raw_limits["pids"], 16, 1_024, "limits.pids"),
        timeout_seconds=_integer(
            raw_limits["timeout_seconds"], 30, 7_200, "limits.timeout_seconds"
        ),
        tmpfs_mb=_integer(raw_limits["tmpfs_mb"], 64, 4_096, "limits.tmpfs_mb"),
        max_artifact_bytes=_integer(
            raw_limits["max_artifact_bytes"],
            1_048_576,
            10_737_418_240,
            "limits.max_artifact_bytes",
        ),
    )
    return SandboxManifest(
        manifest_id=data["manifest_id"],
        runtime_image=data["runtime_image"],
        runner=data["runner"],
        environments=tuple(environments),
        limits=limits,
    )


def load_manifest(manifest_id: str, manifest_directory: Path) -> SandboxManifest:
    if not _IDENTIFIER.fullmatch(manifest_id):
        raise ManifestError("manifest id is invalid")
    if manifest_directory.is_symlink():
        raise ManifestError("manifest directory must not be a symbolic link")
    root = manifest_directory.resolve(strict=True)
    if not root.is_dir():
        raise ManifestError("manifest directory is not a directory")
    path = root / f"{manifest_id}.json"
    if path.is_symlink():
        raise ManifestError("manifest must not be a symbolic link")
    resolved = path.resolve(strict=True)
    if resolved.parent != root:
        raise ManifestError("manifest escaped the configured directory")
    mode = resolved.stat().st_mode
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ManifestError("manifest must not be group- or world-writable")
    if resolved.stat().st_size > 1_048_576:
        raise ManifestError("manifest exceeds 1 MiB")
    with resolved.open("r", encoding="utf-8") as handle:
        manifest = parse_manifest(json.load(handle))
    if manifest.manifest_id != manifest_id:
        raise ManifestError("manifest id does not match its filename")
    return manifest


def configured_directory(name: str) -> Path:
    raw = os.environ.get(name)
    if not raw:
        raise ManifestError(f"{name} is required")
    path = Path(raw)
    if not path.is_absolute():
        raise ManifestError(f"{name} must be an absolute path")
    return path
