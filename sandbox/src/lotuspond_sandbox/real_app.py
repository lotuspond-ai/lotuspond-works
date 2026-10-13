from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_FIELDS = {
    "schema_version",
    "manifest_id",
    "upstream_repository",
    "upstream_revision",
    "environment_id",
    "task_id",
    "task_sha256",
    "verifier_sha256",
    "runtime_image",
    "action_text",
    "limits",
}
EXPECTED_LIMIT_FIELDS = {
    "cpus",
    "memory_mb",
    "pids",
    "timeout_seconds",
    "tmpfs_mb",
}
UPSTREAM_REPOSITORY = "https://github.com/cmu-l3/gym-anything"
UPSTREAM_REVISION = "f3dc04649641dba69d5032f7bb6adaa9785c150d"
TASK_ID = "hello@1"
ENVIRONMENT_ID = "example.basic_x11@0.1"
TASK_SHA256 = "3bf803ced81563661a8b7acb4163d8028e975c674ea7b4399d813e4595d07156"
VERIFIER_SHA256 = "cc464c142074d4e22a23eeb05a35fa07bf57130211b90fea11f5edc087b2a4a6"


class RealAppError(RuntimeError):
    pass


@dataclass(frozen=True)
class RealAppManifest:
    manifest_id: str
    task_sha256: str
    verifier_sha256: str
    runtime_image: str
    action_text: str
    cpus: int
    memory_mb: int
    pids: int
    timeout_seconds: int
    tmpfs_mb: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_real_app_manifest(bundle: Path) -> RealAppManifest:
    if bundle.is_symlink() or not bundle.is_dir():
        raise RealAppError("real-app bundle must be a non-symbolic-link directory")
    manifest_path = bundle / "manifest.json"
    task_path = bundle / "upstream" / "task.json"
    verifier_path = bundle / "upstream" / "verifier.py"
    for path in (manifest_path, task_path, verifier_path):
        if path.is_symlink() or not path.is_file():
            raise RealAppError(
                f"required reviewed file is missing or unsafe: {path.name}"
            )
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            raise RealAppError(f"required reviewed file is not regular: {path.name}")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        set(raw) != EXPECTED_FIELDS
        or set(raw.get("limits", {})) != EXPECTED_LIMIT_FIELDS
    ):
        raise RealAppError("real-app manifest fields do not match the reviewed schema")
    if raw["schema_version"] != "lotuspond.gym-real-app-manifest.v1":
        raise RealAppError("unsupported real-app manifest schema")
    if (
        raw["upstream_repository"] != UPSTREAM_REPOSITORY
        or raw["upstream_revision"] != UPSTREAM_REVISION
        or raw["task_id"] != TASK_ID
        or raw["environment_id"] != ENVIRONMENT_ID
    ):
        raise RealAppError("real-app manifest does not select the reviewed Gym task")
    if (
        raw["task_sha256"] != TASK_SHA256
        or raw["verifier_sha256"] != VERIFIER_SHA256
    ):
        raise RealAppError(
            "real-app manifest hashes differ from the reviewed Gym inputs"
        )
    if _sha256(task_path) != raw["task_sha256"]:
        raise RealAppError("reviewed Gym task hash does not match")
    if _sha256(verifier_path) != raw["verifier_sha256"]:
        raise RealAppError("reviewed Gym verifier hash does not match")
    image = raw["runtime_image"]
    if (
        not isinstance(image, str)
        or not image.startswith("sha256:")
        or len(image) != 71
    ):
        raise RealAppError("real-app image must be an immutable local image id")
    action_text = raw["action_text"]
    if action_text != "Hello from Lotuspond":
        raise RealAppError("real-app action differs from the reviewed action")
    limits = raw["limits"]
    expected_limits = {
        "cpus": 1,
        "memory_mb": 512,
        "pids": 128,
        "timeout_seconds": 45,
        "tmpfs_mb": 128,
    }
    if limits != expected_limits:
        raise RealAppError("real-app limits differ from the reviewed limits")
    return RealAppManifest(
        manifest_id=raw["manifest_id"],
        task_sha256=raw["task_sha256"],
        verifier_sha256=raw["verifier_sha256"],
        runtime_image=image,
        action_text=action_text,
        **limits,
    )


def build_run_argv(
    manifest: RealAppManifest, container_name: str, run_id: str
) -> tuple[str, ...]:
    return (
        "docker",
        "run",
        "--detach",
        "--pull=never",
        "--name",
        container_name,
        "--label",
        f"com.lotuspond.sandbox.run={run_id}",
        "--label",
        "com.lotuspond.sandbox.kind=gym-real-app",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user=10001:10001",
        f"--pids-limit={manifest.pids}",
        f"--memory={manifest.memory_mb}m",
        f"--cpus={manifest.cpus}",
        "--ipc=private",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={manifest.tmpfs_mb}m,mode=1777",
        "--tmpfs",
        "/tmp/.X11-unix:rw,noexec,nosuid,nodev,size=1m,uid=0,gid=0,mode=1777",
        "--tmpfs",
        "/home/app:rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700",
        manifest.runtime_image,
    )


class DockerClient:
    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
        binary: bool = False,
    ) -> subprocess.CompletedProcess[Any]:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=not binary,
            timeout=timeout,
            shell=False,
        )


def _checked(
    docker: DockerClient,
    argv: tuple[str, ...],
    *,
    timeout: float,
    binary: bool = False,
) -> subprocess.CompletedProcess[Any]:
    try:
        result = docker.run(argv, timeout=timeout, binary=binary)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RealAppError(f"Docker command failed: {argv[1]}") from error
    if result.returncode != 0:
        raise RealAppError(f"Docker command failed: {argv[1]}")
    return result


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RealAppError("real application task exceeded its reviewed timeout")
    return min(remaining, 10.0)


def _load_verifier(path: Path, expected_hash: str) -> Callable[..., dict[str, Any]]:
    if _sha256(path) != expected_hash:
        raise RealAppError("reviewed Gym verifier changed before execution")
    spec = importlib.util.spec_from_file_location(
        "lotuspond_reviewed_gym_verifier", path
    )
    if spec is None or spec.loader is None:
        raise RealAppError("reviewed Gym verifier could not be loaded")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    verifier = getattr(module, "check_greeting", None)
    if not callable(verifier):
        raise RealAppError("reviewed Gym verifier entrypoint is missing")
    return verifier


def _inspect_security(
    docker: DockerClient,
    container_name: str,
    manifest: RealAppManifest,
    deadline: float,
) -> dict[str, Any]:
    result = _checked(
        docker,
        ("docker", "inspect", container_name),
        timeout=_remaining(deadline),
    )
    inspected = json.loads(result.stdout)[0]
    host = inspected["HostConfig"]
    config = inspected["Config"]
    tmpfs = host["Tmpfs"] or {}
    expected_tmpfs = {"/tmp", "/tmp/.X11-unix", "/home/app"}
    checks = {
        "image": inspected["Image"] == manifest.runtime_image,
        "network_none": host["NetworkMode"] == "none",
        "read_only": host["ReadonlyRootfs"] is True,
        "cap_drop_all": "ALL" in (host["CapDrop"] or []),
        "no_new_privileges": "no-new-privileges:true" in (host["SecurityOpt"] or []),
        "nonroot": config["User"] == "10001:10001",
        "pids": host["PidsLimit"] == manifest.pids,
        "memory": host["Memory"] == manifest.memory_mb * 1024 * 1024,
        "nano_cpus": host["NanoCpus"] == manifest.cpus * 1_000_000_000,
        "private_ipc": host["IpcMode"] == "private",
        "tmpfs_targets": set(tmpfs) == expected_tmpfs,
        "tmpfs_noexec": all("noexec" in options for options in tmpfs.values()),
        "no_devices": not host["Devices"],
        "not_privileged": host["Privileged"] is False,
        "no_binds": not host["Binds"],
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RealAppError(f"runtime isolation inspection failed: {', '.join(failed)}")
    return checks


def _cleanup(docker: DockerClient, container_name: str, run_id: str) -> None:
    listing = _checked(
        docker,
        (
            "docker",
            "container",
            "ls",
            "--all",
            "--quiet",
            "--filter",
            f"name=^/{container_name}$",
        ),
        timeout=10,
    )
    if not listing.stdout.strip():
        return
    label = _checked(
        docker,
        (
            "docker",
            "inspect",
            "--format",
            "{{ index .Config.Labels \"com.lotuspond.sandbox.run\" }}",
            container_name,
        ),
        timeout=10,
    ).stdout.strip()
    if label != run_id:
        raise RealAppError(
            "refusing to remove a container without the expected run label"
        )
    _checked(docker, ("docker", "rm", "--force", container_name), timeout=30)
    remaining = _checked(
        docker,
        (
            "docker",
            "container",
            "ls",
            "--all",
            "--quiet",
            "--filter",
            f"name=^/{container_name}$",
        ),
        timeout=10,
    )
    if remaining.stdout.strip():
        raise RealAppError("real-app container cleanup could not be confirmed")


def run_real_app(
    bundle: Path,
    output_root: Path,
    *,
    docker: DockerClient | None = None,
) -> dict[str, Any]:
    manifest = load_real_app_manifest(bundle)
    if output_root.is_symlink() or not output_root.is_dir():
        raise RealAppError(
            "output root must be an existing non-symbolic-link directory"
        )
    run_id = f"real-{secrets.token_hex(8)}"
    run_dir = output_root.resolve(strict=True) / run_id
    run_dir.mkdir(mode=0o700)
    container_name = f"lotuspond-gym-{run_id}"
    client = docker or DockerClient()
    argv = build_run_argv(manifest, container_name, run_id)
    command_sha256 = hashlib.sha256("\0".join(argv).encode()).hexdigest()
    started_at = int(time.time())
    deadline = time.monotonic() + manifest.timeout_seconds
    status = "failed"
    failure: str | None = None
    verifier_result: dict[str, Any] | None = None
    independent = {
        "window_present": False,
        "transcript_exact": False,
        "screen_captured": False,
    }
    launch_attempted = False
    cleanup_confirmed = False
    try:
        image = _checked(
            client,
            (
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                manifest.runtime_image,
            ),
            timeout=_remaining(deadline),
        ).stdout.strip()
        if image != manifest.runtime_image:
            raise RealAppError(
                "local real-app image id does not match the reviewed manifest"
            )
        launch_attempted = True
        _checked(client, argv, timeout=_remaining(deadline))
        security_checks = _inspect_security(client, container_name, manifest, deadline)
        window_id = ""
        while time.monotonic() < deadline:
            try:
                found = client.run(
                    (
                        "docker",
                        "exec",
                        container_name,
                        "xdotool",
                        "search",
                        "--name",
                        "^Gym Anything Hello$",
                    ),
                    timeout=_remaining(deadline),
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RealAppError("Docker readiness check failed") from error
            if found.returncode == 0 and found.stdout.strip():
                window_id = found.stdout.splitlines()[0].strip()
                break
            time.sleep(0.2)
        if not window_id.isdigit():
            raise RealAppError("real xterm application window did not become ready")
        action = {"keyboard": {"text": manifest.action_text, "keys": ["ENTER"]}}
        _checked(
            client,
            (
                "docker",
                "exec",
                container_name,
                "xdotool",
                "windowactivate",
                "--sync",
                window_id,
            ),
            timeout=_remaining(deadline),
        )
        _checked(
            client,
            (
                "docker",
                "exec",
                container_name,
                "xdotool",
                "type",
                "--clearmodifiers",
                "--delay",
                "10",
                manifest.action_text,
            ),
            timeout=_remaining(deadline),
        )
        _checked(
            client,
            (
                "docker",
                "exec",
                container_name,
                "xdotool",
                "key",
                "--clearmodifiers",
                "Return",
            ),
            timeout=_remaining(deadline),
        )
        transcript = ""
        while time.monotonic() < deadline:
            try:
                captured = client.run(
                    (
                        "docker",
                        "exec",
                        container_name,
                        "cat",
                        "/tmp/app-transcript.txt",
                    ),
                    timeout=_remaining(deadline),
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RealAppError("Docker transcript check failed") from error
            if captured.returncode == 0:
                transcript = captured.stdout.rstrip("\n")
                break
            time.sleep(0.1)
        window_tree = _checked(
            client,
            ("docker", "exec", container_name, "xwininfo", "-root", "-tree"),
            timeout=_remaining(deadline),
        ).stdout
        screenshot = _checked(
            client,
            ("docker", "exec", container_name, "xwd", "-root", "-silent"),
            timeout=_remaining(deadline),
            binary=True,
        ).stdout
        if len(transcript.encode()) > 1024 or len(window_tree.encode()) > 1_048_576:
            raise RealAppError(
                "real application text artifacts exceeded reviewed bounds"
            )
        if len(screenshot) > 2_500_000:
            raise RealAppError(
                "real application screen artifact exceeded reviewed bounds"
            )
        independent = {
            "window_present": "Gym Anything Hello" in window_tree,
            "transcript_exact": transcript == manifest.action_text,
            "screen_captured": len(screenshot) > 4096,
        }
        (run_dir / "app-transcript.txt").write_text(transcript + "\n", encoding="utf-8")
        (run_dir / "window-tree.txt").write_text(window_tree, encoding="utf-8")
        (run_dir / "screen.xwd").write_bytes(screenshot)
        trajectory = {"steps": [{"event": "step", "action": action}]}
        (run_dir / "trajectory.json").write_text(
            json.dumps(trajectory, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        task_path = bundle / "upstream" / "task.json"
        if _sha256(task_path) != TASK_SHA256:
            raise RealAppError("reviewed Gym task changed before verification")
        task_info = json.loads(task_path.read_text(encoding="utf-8"))
        verifier = _load_verifier(
            bundle / "upstream" / "verifier.py", manifest.verifier_sha256
        )
        verifier_result = verifier(trajectory, {"id": ENVIRONMENT_ID}, task_info)
        (run_dir / "verifier-result.json").write_text(
            json.dumps(verifier_result, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if not all(independent.values()):
            raise RealAppError("independent application-state verification failed")
        if (
            verifier_result.get("passed") is not True
            or verifier_result.get("score") != 100
        ):
            raise RealAppError("reviewed upstream Gym verifier did not pass")
        status = "passed"
    except (RealAppError, json.JSONDecodeError) as error:
        failure = str(error)
        security_checks = locals().get("security_checks", {})
    finally:
        if launch_attempted:
            try:
                _cleanup(client, container_name, run_id)
                cleanup_confirmed = True
            except RealAppError as cleanup_error:
                status = "cleanup_failed"
                failure = f"{failure + '; ' if failure else ''}{cleanup_error}"
    receipt = {
        "schema_version": "lotuspond.gym-real-app-receipt.v1",
        "run_id": run_id,
        "manifest_id": manifest.manifest_id,
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_revision": UPSTREAM_REVISION,
        "environment_id": ENVIRONMENT_ID,
        "task_id": TASK_ID,
        "task_sha256": manifest.task_sha256,
        "verifier_sha256": manifest.verifier_sha256,
        "runtime_image": manifest.runtime_image,
        "command_sha256": command_sha256,
        "status": status,
        "failure": failure,
        "upstream_verifier": verifier_result,
        "independent_application_state": independent,
        "runtime_security_checks": security_checks,
        "cleanup_confirmed": cleanup_confirmed,
        "started_at": started_at,
        "finished_at": int(time.time()),
    }
    (run_dir / "receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the reviewed real Gym terminal task"
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    receipt = run_real_app(args.bundle, args.output_dir)
    print(json.dumps(receipt, sort_keys=True))
    if receipt["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
