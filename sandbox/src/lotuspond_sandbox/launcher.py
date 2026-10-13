from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .auth import RunGrant, verify_grant
from .manifest import ManifestError, SandboxManifest


class Executor(Protocol):
    def probe(self) -> str: ...
    def run(self, argv: tuple[str, ...], timeout: int) -> int: ...
    def remove(self, container_name: str) -> None: ...


class DockerExecutor:
    def probe(self) -> str:
        result = subprocess.run(
            ("docker", "version", "--format", "{{.Server.Version}}"),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Docker daemon is unavailable")
        return result.stdout.strip()[:128]

    def run(self, argv: tuple[str, ...], timeout: int) -> int:
        try:
            result = subprocess.run(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("sandbox exceeded its configured timeout") from error
        return result.returncode

    def remove(self, container_name: str) -> None:
        try:
            subprocess.run(
                ("docker", "rm", "--force", container_name),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            # The daemon can complete removal after the client timeout. The
            # authoritative check below still has to prove the name is absent.
            pass
        try:
            remaining = subprocess.run(
                (
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"name=^/{container_name}$",
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(
                "sandbox container cleanup could not be confirmed"
            ) from error
        if remaining.returncode != 0 or remaining.stdout.strip():
            raise RuntimeError("sandbox container cleanup could not be confirmed")


@dataclass(frozen=True)
class Receipt:
    schema_version: str
    run_id: str
    manifest_id: str
    environment_id: str
    task_id: str
    image: str
    runner: str
    command_sha256: str
    docker_server_version: str
    status: str
    exit_code: int | None
    started_at: int
    finished_at: int
    artifact_bytes: int
    failure: str | None


def build_docker_argv(
    manifest: SandboxManifest,
    grant: RunGrant,
    artifacts: Path,
) -> tuple[str, ...]:
    environment = manifest.environment(grant.environment_id, grant.task_id)
    container_name = f"lotuspond-gym-{grant.run_id}"
    mount = f"type=bind,src={artifacts},dst=/artifacts"
    limits = manifest.limits
    if os.getuid() == 0:
        raise ManifestError("sandbox launcher refuses to run containers as root")
    return (
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        container_name,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--pids-limit",
        str(limits.pids),
        "--memory",
        f"{limits.memory_mb}m",
        "--cpus",
        str(limits.cpus),
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={limits.tmpfs_mb}m",
        "--mount",
        mount,
        "--workdir",
        "/artifacts",
        "--env",
        f"GYM_ANYTHING_RUNNER={manifest.runner}",
        manifest.runtime_image,
        "gym-anything",
        "run",
        f"/opt/gym-anything/{environment.path}",
        "--task",
        grant.task_id,
        "--steps",
        str(grant.steps),
        "--seed",
        str(grant.seed),
    )


def launch(
    manifest: SandboxManifest,
    grant: RunGrant,
    *,
    key: bytes,
    runs_directory: Path,
    executor: Executor | None = None,
) -> Receipt:
    verify_grant(grant, key)
    if grant.manifest_id != manifest.manifest_id:
        raise ManifestError("grant is bound to a different manifest")
    manifest.environment(grant.environment_id, grant.task_id)
    if runs_directory.is_symlink():
        raise ManifestError("runs directory must not be a symbolic link")
    root = runs_directory.resolve(strict=True)
    if not root.is_dir():
        raise ManifestError("runs directory is not a directory")
    if any(character in str(root) for character in (",", "\n", "\r")):
        raise ManifestError(
            "runs directory contains characters unsupported by Docker mounts"
        )
    run_directory = root / grant.run_id
    run_directory.mkdir(mode=0o700)
    artifacts = run_directory / "artifacts"
    artifacts.mkdir(mode=0o700)
    argv = build_docker_argv(manifest, grant, artifacts)
    command_hash = hashlib.sha256("\0".join(argv).encode()).hexdigest()
    _write_json(
        run_directory / "plan.json",
        {
            "schema_version": "lotuspond.sandbox.plan.v1",
            "run_id": grant.run_id,
            "manifest_id": manifest.manifest_id,
            "environment_id": grant.environment_id,
            "task_id": grant.task_id,
            "image": manifest.runtime_image,
            "runner": manifest.runner,
            "command_sha256": command_hash,
            "network": "none",
            "read_only": True,
            "limits": asdict(manifest.limits),
        },
    )

    runner = executor or DockerExecutor()
    docker_version = runner.probe()
    started = int(time.time())
    status = "failed"
    exit_code = None
    failure = None
    try:
        exit_code = runner.run(argv, manifest.limits.timeout_seconds)
        status = "succeeded" if exit_code == 0 else "failed"
        if exit_code != 0:
            failure = "gym-anything process exited unsuccessfully"
    except TimeoutError:
        try:
            runner.remove(f"lotuspond-gym-{grant.run_id}")
        except RuntimeError:
            status = "cleanup_failed"
            failure = "sandbox timed out and container cleanup could not be confirmed"
        else:
            status = "timed_out"
            failure = "sandbox exceeded its configured timeout"
    except KeyboardInterrupt:
        try:
            runner.remove(f"lotuspond-gym-{grant.run_id}")
        except RuntimeError as cleanup_error:
            raise RuntimeError(
                "sandbox was interrupted and container cleanup could not be confirmed"
            ) from cleanup_error
        raise

    artifact_bytes = _artifact_size(artifacts)
    if artifact_bytes > manifest.limits.max_artifact_bytes:
        status = "failed"
        failure = "artifact output exceeded the configured post-run limit"
    receipt = Receipt(
        schema_version="lotuspond.sandbox.receipt.v1",
        run_id=grant.run_id,
        manifest_id=manifest.manifest_id,
        environment_id=grant.environment_id,
        task_id=grant.task_id,
        image=manifest.runtime_image,
        runner=manifest.runner,
        command_sha256=command_hash,
        docker_server_version=docker_version,
        status=status,
        exit_code=exit_code,
        started_at=started,
        finished_at=int(time.time()),
        artifact_bytes=artifact_bytes,
        failure=failure,
    )
    _write_json(run_directory / "receipt.json", asdict(receipt))
    return receipt


def _write_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def _artifact_size(root: Path) -> int:
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    raise ManifestError("artifact output contains a symbolic link")
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(metadata.st_mode):
                    total += metadata.st_size
                else:
                    raise ManifestError("artifact output contains a non-regular file")
    return total
