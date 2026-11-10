"""Exercise the customer-side execution image.

This uses synthetic loopback endpoints only. It never connects to a customer
agent or an existing database, and it does not publish a port. It is a local packaging drill, not a clean-machine or
production acceptance test.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time

LABEL = "com.lotuspond.execution-drill"
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

def command(*args, data=None, timeout=60, check=True):
    result = subprocess.run(
        args, input=data, capture_output=True, timeout=timeout, check=False
    )
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result


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
    if any(entry not in usage for entry in ("runner-http", "gate", "Docker socket")):
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
    finally:
        confirm_cleanup(run)
    report["cleanup_confirmed"] = True
    report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="run the HTTP probe against an already loaded execution image",
    )
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(image_drill(args.image), indent=2))
