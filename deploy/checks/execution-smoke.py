"""Exercise a verified customer-side execution archive.

This uses synthetic loopback endpoints and the reviewed xterm task only. It never
connects to a customer agent, speech provider, or existing database, and it does
not publish a port. It is a local packaging drill, not a clean-machine or
production acceptance test.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from verify_airgap_bundle import MAX_BUNDLE_BYTES, verify_bundle

IMAGE_KEYS = {"LOTUSPOND_EXECUTION_IMAGE", "LOTUSPOND_GYM_REAL_APP_IMAGE"}
LABEL = "com.lotuspond.execution-drill"
SESSION = "12345678-1234-4234-8234-123456789abc"
MAX_GYM_KIT_BYTES = 20 * 1024 * 1024
HTTP_PROBE = r"""
import { createServer } from 'node:http';
import { evaluate } from '/app/protocol-adapters/src/http-runner.mjs';
const server = createServer((request, response) => {
  const chunks = [];
  request.on('data', (chunk) => chunks.push(chunk));
  request.on('end', () => {
    const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ text: parsed.input }));
  });
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
const result = await evaluate(
  { prompt: 'hello-from-execution-image' },
  { contains: 'hello-from-execution-image' },
  {},
  { env: { LOTUSPOND_HTTP_ENDPOINT: `http://127.0.0.1:${port}/v1` } },
);
server.close();
if (result.passed !== true || result.evidence.protocol !== 'http') process.exit(1);
process.stdout.write(JSON.stringify({ passed: true, protocol: 'http' }));
"""
A2A_PROBE = r"""
import { createServer } from 'node:http';
import { evaluate } from '/app/protocol-adapters/src/a2a-runner.mjs';
const server = createServer((request, response) => {
  const chunks = [];
  request.on('data', (chunk) => chunks.push(chunk));
  request.on('end', () => {
    const call = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const text = call.params.message.parts[0].text;
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ jsonrpc: '2.0', id: call.id, result: {
      kind: 'task', id: 'task-1', contextId: 'context-1', status: { state: 'completed' },
      artifacts: [{ parts: [{ kind: 'text', text }] }] } }));
  });
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
const result = await evaluate(
  { prompt: 'hello-from-execution-image' },
  { contains: 'hello-from-execution-image' },
  {},
  { env: { LOTUSPOND_A2A_ENDPOINT: `http://127.0.0.1:${port}/a2a` } },
);
server.close();
if (result.passed !== true || result.evidence.protocol !== 'a2a' || result.evidence.task_state !== 'completed')
  process.exit(1);
process.stdout.write(JSON.stringify({ passed: true, protocol: 'a2a' }));
"""

MCP_PROBE = r"""
import { createServer } from 'node:http';
import { evaluate } from '/app/protocol-adapters/src/mcp-runner.mjs';
const server = createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : undefined;
  if (request.method === 'DELETE' || body?.id === undefined) {
    response.writeHead(body?.id === undefined ? 202 : 200).end();
    return;
  }
  let result;
  if (body.method === 'initialize') {
    result = {
      protocolVersion: body.params.protocolVersion,
      capabilities: { tools: {} },
      serverInfo: { name: 'execution-drill', version: '1.0.0' },
    };
  } else if (body.method === 'tools/call') {
    result = { content: [{ type: 'text', text: 'approved-from-execution-image' }] };
  } else {
    response.writeHead(404).end();
    return;
  }
  const encoded = JSON.stringify({ jsonrpc: '2.0', id: body.id, result });
  response.writeHead(200, {
    'content-type': 'application/json',
    'content-length': Buffer.byteLength(encoded),
  });
  response.end(encoded);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
const result = await evaluate(
  { arguments: { prompt: 'hello' } },
  { contains: 'approved-from-execution-image' },
  {},
  {
    env: {
      LOTUSPOND_MCP_ENDPOINT: `http://127.0.0.1:${port}/mcp`,
      LOTUSPOND_MCP_TOOL: 'approved.tool',
      LOTUSPOND_MCP_TIMEOUT_MS: '4000',
    },
  },
);
server.close();
if (result.passed !== true || result.evidence.protocol !== 'mcp') process.exit(1);
process.stdout.write(JSON.stringify({ passed: true, protocol: 'mcp' }));
"""


def command(*args, data=None, timeout=60, check=True):
    result = subprocess.run(
        args, input=data, capture_output=True, timeout=timeout, check=False
    )
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result


def image_inventory(path: Path) -> dict[str, str]:
    if path.stat().st_size > 4096:
        raise ValueError("image inventory exceeds its bound")
    inventory = json.loads(path.read_text())
    if not isinstance(inventory, dict) or set(inventory) != {"schema", "images"}:
        raise ValueError("invalid image inventory")
    images = inventory["images"]
    if (
        inventory["schema"] != "lotuspond.offline-execution-images.v1"
        or not isinstance(images, dict)
        or set(images) != IMAGE_KEYS
        or not all(
            isinstance(image, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            for image in images.values()
        )
    ):
        raise ValueError("image inventory requires exactly two immutable image IDs")
    return images


def verified_inputs(archive: Path, checksum: Path, private: Path):
    copied = private / "input.tar.gz"
    size = 0
    with archive.open("rb") as source, copied.open("xb") as target:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BUNDLE_BYTES:
                raise ValueError("bundle exceeds the compressed-size limit")
            target.write(chunk)
    proof = verify_bundle(copied, checksum, kind="execution")
    gym_bytes = 0
    with tarfile.open(copied, "r:gz") as bundle:
        for member in bundle.getmembers():
            if not member.isfile():
                continue
            relative = member.name[len(proof.root) + 1 :]
            if relative in {"images.json", "images.tar"}:
                limit = 4096 if relative == "images.json" else MAX_BUNDLE_BYTES
                if member.size > limit:
                    raise ValueError("required bundle input is missing or oversized")
                destination = private / relative
            elif relative.startswith("gym-kit/"):
                gym_bytes += member.size
                if gym_bytes > MAX_GYM_KIT_BYTES:
                    raise ValueError("gym kit exceeds its payload bound")
                destination = private / relative
            else:
                continue
            resolved = destination.resolve()
            if not resolved.is_relative_to(private.resolve()) or destination.exists():
                raise ValueError("bundle extraction path is not confined")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError("required bundle input cannot be read")
            with source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    copied.unlink()
    return proof


def docker_run(
    image: str,
    run: str,
    args,
    *,
    data=None,
    timeout=30,
    entrypoint=None,
    environment=None,
    check=True,
):
    command_args = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--label",
        f"{LABEL}={run}",
    ]
    if data is not None:
        command_args.append("-i")
    if entrypoint is not None:
        command_args.extend(["--entrypoint", entrypoint])
    for key, value in (environment or {}).items():
        command_args.extend(["-e", f"{key}={value}"])
    command_args.append(image)
    command_args.extend(args)
    return command(*command_args, data=data, timeout=timeout, check=check)


def inspect_execution_image(image: str, run: str) -> dict[str, str]:
    inspected = json.loads(command("docker", "image", "inspect", image).stdout)[0]
    config = inspected["Config"]
    if config.get("Entrypoint") != ["node", "/app/scripts/execution-cli.mjs"]:
        raise RuntimeError("execution image entrypoint is not the reviewed dispatcher")
    identity = docker_run(
        image,
        run,
        ["-c", 'test "$(id -u)" = 10001 && test "$(id -g)" = 10001'],
        entrypoint="/bin/sh",
        timeout=20,
    )
    if identity.returncode:
        raise RuntimeError("execution image is not the reviewed nonroot user")
    help_result = docker_run(image, run, ["help"], timeout=20)
    usage = help_result.stdout.decode()
    if any(entry not in usage for entry in ("runner-http", "runner-a2a", "Docker socket")):
        raise RuntimeError("execution image help is incomplete")
    missing = docker_run(
        image,
        run,
        [
            "-c",
            (
                "command -v python3 >/dev/null && exit 1; "
                "command -v docker >/dev/null && exit 1; "
                "test ! -e /var/run/docker.sock"
            ),
        ],
        entrypoint="/bin/sh",
        timeout=20,
        check=False,
    )
    if missing.returncode:
        raise RuntimeError("execution image unexpectedly contains Python, Docker, or a socket")
    return {"user": "10001", "help": True}


def protocol_probe(image: str, run: str, source: str, protocol: str) -> dict[str, object]:
    result = docker_run(
        image,
        run,
        ["--input-type=module", "-"],
        data=source.encode(),
        entrypoint="node",
        timeout=30,
    )
    try:
        payload = json.loads(result.stdout.decode())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{protocol} probe did not emit JSON (exit {result.returncode})"
        ) from exc
    if payload.get("passed") is not True or payload.get("protocol") != protocol:
        raise RuntimeError(f"{protocol} probe did not retain a passing synthetic result")
    combined = result.stdout + result.stderr
    if b"token" in combined.lower() or b"secret" in combined.lower():
        raise RuntimeError(f"{protocol} probe output was not credential-free")
    return payload


def voice_probe(image: str, run: str) -> dict[str, object]:
    result = docker_run(
        image,
        run,
        ["voice", "--session", SESSION, "--channel-map", "0:user"],
        environment={"LOTUSPOND_NETWORK_MODE": "airgap"},
        timeout=20,
        check=False,
    )
    stderr = result.stderr.decode()
    if result.returncode == 0 or "code=network_disabled" not in stderr:
        raise RuntimeError("voice command did not refuse airgap operation")
    if "DEEPGRAM" in stderr or "LOTUSPOND_API_KEY" in stderr:
        raise RuntimeError("voice refusal leaked credential names")
    return {"airgap_refused": True, "credentials_required": False}


def gym_probe(private: Path, gym_image: str) -> dict[str, object]:
    output = private / "gym-runs"
    output.mkdir(mode=0o700)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(private / "gym-kit" / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lotuspond_sandbox.real_app",
            "--bundle",
            str(private / "gym-kit" / "real-app"),
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        timeout=120,
        check=False,
        env=environment,
        cwd=private / "gym-kit",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace")[:400]
        raise RuntimeError(
            "extracted gym kit did not complete the reviewed xterm task"
            + (f": {detail}" if detail else "")
        )
    receipt = json.loads(result.stdout.decode())
    if (
        receipt.get("status") != "passed"
        or receipt.get("cleanup_confirmed") is not True
        or receipt.get("runtime_image") != gym_image
    ):
        raise RuntimeError("gym receipt did not confirm a cleaned reviewed pass")
    listing = command(
        "docker",
        "container",
        "ls",
        "--all",
        "--filter",
        f"label=com.lotuspond.sandbox.run={receipt['run_id']}",
        "--format",
        "{{.ID}}",
        check=False,
    )
    if listing.stdout.strip():
        raise RuntimeError("gym application container remained after the extracted kit finished")
    return {
        "status": "passed",
        "cleanup_confirmed": True,
        "task_id": receipt.get("task_id"),
        "run_id": receipt.get("run_id"),
    }


def remaining_owned(run: str) -> str:
    containers = command(
        "docker",
        "container",
        "ls",
        "--all",
        "--filter",
        f"label={LABEL}={run}",
        "--format",
        "{{.Names}}",
        check=False,
    )
    return containers.stdout.decode().strip()


def confirm_cleanup(run: str) -> None:
    leftover = remaining_owned(run)
    if leftover:
        command("docker", "container", "rm", "-f", *leftover.split(), check=False)
    if remaining_owned(run):
        message = "drill cleanup could not be confirmed"
        if active_error := sys.exception():
            active_error.add_note(message)
        else:
            raise RuntimeError(message)


def image_drill(image: str) -> dict[str, object]:
    started = time.monotonic()
    run = secrets.token_hex(8)
    report: dict[str, object] = {
        "schema": "lotuspond.execution-image-smoke.v1",
        "run": run,
        "execution_image": image,
        "cleanup_confirmed": False,
    }
    try:
        inspected = json.loads(command("docker", "image", "inspect", image).stdout)[0]
        report["execution_image"] = inspected["Id"]
        report["image"] = inspect_execution_image(inspected["Id"], run)
        report["http"] = protocol_probe(inspected["Id"], run, HTTP_PROBE, "http")
        report["mcp"] = protocol_probe(inspected["Id"], run, MCP_PROBE, "mcp")
        report["a2a"] = protocol_probe(inspected["Id"], run, A2A_PROBE, "a2a")
        report["voice"] = voice_probe(inspected["Id"], run)
    finally:
        confirm_cleanup(run)
    report["cleanup_confirmed"] = True
    report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return report


def drill(archive: Path, checksum: Path) -> dict[str, object]:
    started = time.monotonic()
    run = secrets.token_hex(8)
    report: dict[str, object] = {
        "schema": "lotuspond.execution-smoke.v1",
        "run": run,
        "http": None,
        "mcp": None,
        "voice": None,
        "gym": None,
        "cleanup_confirmed": False,
    }
    with tempfile.TemporaryDirectory(prefix="lotuspond-execution-drill-") as temporary:
        private = Path(temporary)
        try:
            proof = verified_inputs(archive, checksum, private)
            images = image_inventory(private / "images.json")
            command("docker", "image", "load", "-i", str(private / "images.tar"), timeout=180)
            execution = images["LOTUSPOND_EXECUTION_IMAGE"]
            gym = images["LOTUSPOND_GYM_REAL_APP_IMAGE"]
            report["archive_root"] = proof.root
            report["execution_image"] = execution
            report["gym_image"] = gym
            report["image"] = inspect_execution_image(execution, run)
            report["http"] = protocol_probe(execution, run, HTTP_PROBE, "http")
            report["mcp"] = protocol_probe(execution, run, MCP_PROBE, "mcp")
            report["a2a"] = protocol_probe(execution, run, A2A_PROBE, "a2a")
            report["voice"] = voice_probe(execution, run)
            report["gym"] = gym_probe(private, gym)
        finally:
            confirm_cleanup(run)
    report["cleanup_confirmed"] = True
    report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", type=Path)
    parser.add_argument(
        "checksum",
        nargs="?",
        type=Path,
        help="SHA-256 delivered through a trusted channel",
    )
    parser.add_argument(
        "--image",
        help="run HTTP, MCP, and voice probes against an already loaded execution image",
    )
    args = parser.parse_args()
    os.umask(0o077)
    if args.image:
        if args.archive or args.checksum:
            parser.error("image probes do not take an archive")
        print(json.dumps(image_drill(args.image), indent=2))
    else:
        if args.archive is None or args.checksum is None:
            parser.error("archive and checksum are required unless --image is set")
        print(json.dumps(drill(args.archive, args.checksum), indent=2))
