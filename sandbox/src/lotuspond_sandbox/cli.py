from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .auth import issue_grant, load_auth_key, load_grant, write_grant
from .launcher import DockerExecutor, launch
from .manifest import ManifestError, configured_directory, load_manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="lotuspond-sandbox")
    commands = result.add_subparsers(dest="command", required=True)
    authorize = commands.add_parser(
        "authorize", help="create a five-minute manifest-bound run grant"
    )
    for command in (authorize,):
        command.add_argument("--manifest-id", required=True)
        command.add_argument("--environment-id", required=True)
        command.add_argument("--task-id", required=True)
        command.add_argument("--run-id", required=True)
        command.add_argument("--steps", type=int, required=True)
        command.add_argument("--seed", type=int, required=True)
    authorize.add_argument("--grant-file", type=Path, required=True)
    run = commands.add_parser("run", help="verify a grant and launch an isolated run")
    run.add_argument("--manifest-id", required=True)
    run.add_argument("--grant-file", type=Path, required=True)
    commands.add_parser(
        "doctor", help="check the Docker runtime without launching a workload"
    )
    return result


def main() -> None:
    arguments = parser().parse_args()
    try:
        if arguments.command == "doctor":
            print(
                json.dumps(
                    {"docker_server_version": DockerExecutor().probe()}, sort_keys=True
                )
            )
            return
        manifests = configured_directory("LOTUSPOND_SANDBOX_MANIFEST_DIR")
        manifest = load_manifest(arguments.manifest_id, manifests)
        key = load_auth_key()
        if arguments.command == "authorize":
            manifest.environment(arguments.environment_id, arguments.task_id)
            grant = issue_grant(
                key=key,
                run_id=arguments.run_id,
                manifest_id=manifest.manifest_id,
                environment_id=arguments.environment_id,
                task_id=arguments.task_id,
                steps=arguments.steps,
                seed=arguments.seed,
                expires_at=int(time.time()) + 300,
                nonce=f"n{secrets.token_hex(16)}",
            )
            write_grant(arguments.grant_file, grant)
            print(
                json.dumps(
                    {
                        "grant_file": str(arguments.grant_file),
                        "expires_at": grant.expires_at,
                    },
                    sort_keys=True,
                )
            )
            return
        grant = load_grant(arguments.grant_file)
        receipt = launch(
            manifest,
            grant,
            key=key,
            runs_directory=configured_directory("LOTUSPOND_SANDBOX_RUNS_DIR"),
        )
        print(json.dumps(asdict(receipt), sort_keys=True))
        if receipt.status != "succeeded":
            raise SystemExit(1)
    except (ManifestError, FileExistsError, FileNotFoundError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
