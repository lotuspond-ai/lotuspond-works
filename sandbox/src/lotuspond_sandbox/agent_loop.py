from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .real_app import (
    ENVIRONMENT_ID,
    TASK_ID,
    TASK_SHA256,
    UPSTREAM_REPOSITORY,
    UPSTREAM_REVISION,
    VERIFIER_SHA256,
    DockerClient,
    RealAppError,
    _checked,
    _cleanup,
    _inspect_security,
    _load_verifier,
    _remaining,
    _sha256,
    build_run_argv,
    load_real_app_manifest,
)

AGENT_RESPONSE_BYTES = 4096
HTTP_BODY_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_SCREEN_BYTES = 2_500_000
MAX_TREE_BYTES = 1_048_576
MAX_TEXT_BYTES = 256
MAX_STEPS = 6
ALLOWED_KEYS = {"ENTER": "Return", "ESCAPE": "Escape", "TAB": "Tab"}


class AgentProtocolError(RealAppError):
    pass


@dataclass(frozen=True)
class AgentAction:
    type: str
    text: str | None = None
    key: str | None = None


def validate_agent_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise AgentProtocolError("agent URL has an invalid port") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or port is None
        or not 1 <= port <= 65535
    ):
        raise AgentProtocolError(
            "agent URL must be explicit loopback HTTP without credentials or query"
        )
    return value


def parse_agent_action(raw: bytes) -> AgentAction:
    if len(raw) > AGENT_RESPONSE_BYTES:
        raise AgentProtocolError("agent response exceeds the configured byte limit")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgentProtocolError("agent response is not valid JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise AgentProtocolError("agent response must be an action object")
    action_type = value["type"]
    if action_type == "finish" and set(value) == {"type"}:
        return AgentAction(type="finish")
    if action_type == "key" and set(value) == {"type", "key"}:
        key = value["key"]
        if key not in ALLOWED_KEYS:
            raise AgentProtocolError("agent key is not allowlisted")
        return AgentAction(type="key", key=key)
    if action_type == "type_text" and set(value) == {"type", "text"}:
        text = value["text"]
        if (
            not isinstance(text, str)
            or not text
            or len(text.encode("utf-8")) > MAX_TEXT_BYTES
            or not text.isprintable()
        ):
            raise AgentProtocolError("agent text is empty, non-printable, or too large")
        return AgentAction(type="type_text", text=text)
    raise AgentProtocolError("agent action shape is not allowlisted")


def request_agent_action(
    agent_url: str, payload: dict[str, Any], *, timeout: float
) -> tuple[AgentAction, bytes]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    if len(body) > HTTP_BODY_BYTES:
        raise AgentProtocolError("agent observation request exceeds its byte limit")
    try:
        result = subprocess.run(
            (
                sys.executable,
                "-m",
                "lotuspond_sandbox.agent_http",
                agent_url,
                str(AGENT_RESPONSE_BYTES),
            ),
            input=body,
            capture_output=True,
            timeout=max(0.001, min(timeout, 5.0)),
            check=False,
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            },
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AgentProtocolError("loopback agent request failed") from error
    if result.returncode != 0:
        raise AgentProtocolError("loopback agent request failed")
    return parse_agent_action(result.stdout), result.stdout


def _find_window(
    client: DockerClient, container_name: str, deadline: float
) -> str:
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
            raise RealAppError("Docker window readiness check failed") from error
        if found.returncode == 0 and found.stdout.strip():
            window_id = found.stdout.splitlines()[0].strip()
            if window_id.isdigit():
                return window_id
        time.sleep(0.2)
    raise RealAppError("real xterm application window did not become ready")


def _capture_observation(
    client: DockerClient,
    container_name: str,
    observation_dir: Path,
    step: int,
    deadline: float,
) -> dict[str, Any]:
    tree = _checked(
        client,
        ("docker", "exec", container_name, "xwininfo", "-root", "-tree"),
        timeout=_remaining(deadline),
    ).stdout
    screen = _checked(
        client,
        ("docker", "exec", container_name, "xwd", "-root", "-silent"),
        timeout=_remaining(deadline),
        binary=True,
    ).stdout
    if len(tree.encode()) > MAX_TREE_BYTES or len(screen) > MAX_SCREEN_BYTES:
        raise RealAppError("agent observation exceeds the reviewed artifact limits")
    screen_hash = hashlib.sha256(screen).hexdigest()
    stem = f"{step:02d}"
    (observation_dir / f"{stem}.xwd").write_bytes(screen)
    metadata = {
        "schema_version": "lotuspond.gym-observation.v1",
        "step": step,
        "screen": {
            "media_type": "application/x-xwindowdump",
            "bytes": len(screen),
            "sha256": screen_hash,
        },
        "window_tree": tree,
    }
    (observation_dir / f"{stem}.json").write_text(
        json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return {
        **metadata,
        "screen": {
            **metadata["screen"],
            "base64": base64.b64encode(screen).decode("ascii"),
        },
    }


def _inject_action(
    client: DockerClient,
    container_name: str,
    window_id: str,
    action: AgentAction,
    deadline: float,
) -> None:
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
    if action.type == "type_text":
        argv = (
            "docker",
            "exec",
            container_name,
            "xdotool",
            "type",
            "--clearmodifiers",
            "--delay",
            "10",
            action.text or "",
        )
    elif action.type == "key":
        argv = (
            "docker",
            "exec",
            container_name,
            "xdotool",
            "key",
            "--clearmodifiers",
            ALLOWED_KEYS[action.key or ""],
        )
    else:
        raise AgentProtocolError("finish cannot be injected as an application action")
    _checked(client, argv, timeout=_remaining(deadline))


def _optional_transcript(
    client: DockerClient, container_name: str, deadline: float
) -> str:
    try:
        result = client.run(
            ("docker", "exec", container_name, "cat", "/tmp/app-transcript.txt"),
            timeout=_remaining(deadline),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RealAppError("Docker transcript observation failed") from error
    if result.returncode != 0:
        return ""
    transcript = result.stdout.rstrip("\n")
    if len(transcript.encode()) > 1024:
        raise RealAppError("application transcript exceeds the reviewed byte limit")
    return transcript


def _artifact_bytes(run_dir: Path) -> int:
    total = 0
    for path in run_dir.rglob("*"):
        if path.is_symlink():
            raise RealAppError("agent-loop artifacts must not contain symbolic links")
        if path.is_file():
            total += path.stat(follow_symlinks=False).st_size
    if total > MAX_ARTIFACT_BYTES:
        raise RealAppError("agent-loop artifacts exceed the reviewed total limit")
    return total


def run_agent_loop(
    bundle: Path,
    output_root: Path,
    agent_url: str,
    *,
    docker: DockerClient | None = None,
) -> dict[str, Any]:
    endpoint = validate_agent_url(agent_url)
    manifest = load_real_app_manifest(bundle)
    if output_root.is_symlink() or not output_root.is_dir():
        raise RealAppError(
            "output root must be an existing non-symbolic-link directory"
        )
    run_id = f"agent-{secrets.token_hex(8)}"
    run_dir = output_root.resolve(strict=True) / run_id
    run_dir.mkdir(mode=0o700)
    observations = run_dir / "observations"
    observations.mkdir(mode=0o700)
    container_name = f"lotuspond-gym-{run_id}"
    client = docker or DockerClient()
    argv = build_run_argv(manifest, container_name, run_id)
    command_sha256 = hashlib.sha256("\0".join(argv).encode()).hexdigest()
    deadline = time.monotonic() + manifest.timeout_seconds
    started_at = int(time.time())
    status = "infrastructure_failed"
    failure: str | None = None
    launch_attempted = False
    cleanup_confirmed = False
    security_checks: dict[str, Any] = {}
    verifier_result: dict[str, Any] | None = None
    independent = {
        "window_present": False,
        "transcript_exact": False,
        "screen_captured": False,
        "agent_finished": False,
    }
    trajectory: dict[str, list[dict[str, Any]]] = {"steps": []}
    actions: list[dict[str, Any]] = []
    observation_hashes: list[str] = []
    final_observation: dict[str, Any] | None = None
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
            raise RealAppError("local app image differs from the reviewed manifest")
        launch_attempted = True
        _checked(client, argv, timeout=_remaining(deadline))
        security_checks = _inspect_security(client, container_name, manifest, deadline)
        window_id = _find_window(client, container_name, deadline)
        task_path = bundle / "upstream" / "task.json"
        if _sha256(task_path) != TASK_SHA256:
            raise RealAppError("reviewed Gym task changed before agent execution")
        task_info = json.loads(task_path.read_text(encoding="utf-8"))
        prompt = task_info["natural_language"]["prompt"]
        for step in range(MAX_STEPS):
            observation = _capture_observation(
                client, container_name, observations, step, deadline
            )
            final_observation = observation
            observation_hashes.append(observation["screen"]["sha256"])
            request_payload = {
                "schema_version": "lotuspond.gym-agent-request.v1",
                "run_id": run_id,
                "step": step,
                "max_steps": MAX_STEPS,
                "task": {
                    "environment_id": ENVIRONMENT_ID,
                    "task_id": TASK_ID,
                    "prompt": prompt,
                },
                "observation": observation,
                "allowed_actions": {
                    "type_text": {"max_utf8_bytes": MAX_TEXT_BYTES},
                    "key": sorted(ALLOWED_KEYS),
                    "finish": {},
                },
            }
            action, raw = request_agent_action(
                endpoint, request_payload, timeout=_remaining(deadline)
            )
            action_record = {"step": step, "action": asdict(action)}
            actions.append(action_record)
            with (run_dir / "agent-actions.jsonl").open("ab") as target:
                target.write(raw.rstrip() + b"\n")
            if action.type == "finish":
                independent["agent_finished"] = True
                break
            _inject_action(client, container_name, window_id, action, deadline)
            gym_action: dict[str, Any]
            if action.type == "type_text":
                gym_action = {"keyboard": {"text": action.text}}
            else:
                gym_action = {"keyboard": {"keys": [action.key]}}
            trajectory["steps"].append(
                {"event": "step", "step": step, "action": gym_action}
            )
        final_observation = _capture_observation(
            client, container_name, observations, len(actions), deadline
        )
        observation_hashes.append(final_observation["screen"]["sha256"])
        transcript = _optional_transcript(client, container_name, deadline)
        (run_dir / "app-transcript.txt").write_text(
            transcript + "\n", encoding="utf-8"
        )
        (run_dir / "trajectory.json").write_text(
            json.dumps(trajectory, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        verifier = _load_verifier(
            bundle / "upstream" / "verifier.py", VERIFIER_SHA256
        )
        verifier_result = verifier(trajectory, {"id": ENVIRONMENT_ID}, task_info)
        (run_dir / "verifier-result.json").write_text(
            json.dumps(verifier_result, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        independent.update(
            {
                "window_present": "Gym Anything Hello"
                in final_observation["window_tree"],
                "transcript_exact": transcript == manifest.action_text,
                "screen_captured": final_observation["screen"]["bytes"] > 4096,
            }
        )
        infrastructure_healthy = (
            independent["window_present"] and independent["screen_captured"]
        )
        if not infrastructure_healthy:
            raise RealAppError("real application observation could not be verified")
        passed = (
            independent["agent_finished"]
            and independent["transcript_exact"]
            and verifier_result.get("passed") is True
            and verifier_result.get("score") == 100
        )
        status = "passed" if passed else "task_failed"
        if not passed:
            failure = "agent did not satisfy the pinned task and independent verifier"
        _artifact_bytes(run_dir)
    except (AgentProtocolError, RealAppError, json.JSONDecodeError) as error:
        failure = str(error)
        status = "infrastructure_failed"
    finally:
        if launch_attempted:
            try:
                _cleanup(client, container_name, run_id)
                cleanup_confirmed = True
            except RealAppError as cleanup_error:
                status = "cleanup_failed"
                failure = f"{failure + '; ' if failure else ''}{cleanup_error}"
    receipt = {
        "schema_version": "lotuspond.gym-agent-receipt.v1",
        "run_id": run_id,
        "manifest_id": manifest.manifest_id,
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_revision": UPSTREAM_REVISION,
        "environment_id": ENVIRONMENT_ID,
        "task_id": TASK_ID,
        "task_sha256": TASK_SHA256,
        "verifier_sha256": VERIFIER_SHA256,
        "runtime_image": manifest.runtime_image,
        "command_sha256": command_sha256,
        "agent_transport": "loopback_http",
        "agent_endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
        "limits": {
            "steps": MAX_STEPS,
            "wall_seconds": manifest.timeout_seconds,
            "agent_response_bytes": AGENT_RESPONSE_BYTES,
            "http_body_bytes": HTTP_BODY_BYTES,
            "artifact_bytes": MAX_ARTIFACT_BYTES,
            "screen_bytes": MAX_SCREEN_BYTES,
            "window_tree_bytes": MAX_TREE_BYTES,
            "text_bytes": MAX_TEXT_BYTES,
        },
        "status": status,
        "failure": failure,
        "actions": actions,
        "agent_actions_sha256": (
            _sha256(run_dir / "agent-actions.jsonl")
            if (run_dir / "agent-actions.jsonl").is_file()
            else None
        ),
        "observation_screen_sha256": observation_hashes,
        "upstream_verifier": verifier_result,
        "independent_application_state": independent,
        "runtime_security_checks": security_checks,
        "cleanup_confirmed": cleanup_confirmed,
        "started_at": started_at,
        "finished_at": int(time.time()),
    }
    receipt_path = run_dir / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    receipt_hash = _sha256(receipt_path)
    result = {
        "schema_version": "lotuspond.gym-agent-result.v1",
        "classification": (
            "pass"
            if status == "passed"
            else "task_failure"
            if status == "task_failed"
            else "infrastructure_failure"
        ),
        "receipt_path": str(receipt_path),
        "receipt_sha256": receipt_hash,
        "receipt": receipt,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the reviewed Gym agent loop")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--agent-url", required=True)
    args = parser.parse_args()
    result = run_agent_loop(args.bundle, args.output_dir, args.agent_url)
    print(json.dumps(result, sort_keys=True))
    if result["classification"] == "infrastructure_failure":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
