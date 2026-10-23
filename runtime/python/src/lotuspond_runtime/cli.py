"""Explicit private runtime entrypoint; never accepts secrets in argv or environment."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

from pydantic import SecretStr

from .client import RuntimeClient
from .control_transport import ControlTransport
from .errors import FleetError
from .protocol import CredentialState, RuntimeConfig, validate_token
from .state import CredentialStore, read_private_file
from .supervisor import RuntimeSupervisor


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise FleetError("invalid_request")


def _token(path):
    if path == "-":
        if sys.stdin.isatty():
            raise FleetError("invalid_request")
        content = sys.stdin.buffer.read(129)
        if len(content) > 128:
            raise FleetError("input_limit")
    else:
        content = read_private_file(Path(path).absolute(), 128)
    try:
        text = content.decode("ascii")
    except UnicodeError:
        raise FleetError("invalid_request") from None
    return validate_token(SecretStr(text.strip()), "lpe_")


def _execute(argv, clients) -> int:
    try:
        parser = _Parser(prog="lotuspond-runtime", description="Private Lotuspond Linux runtime")
        commands = parser.add_subparsers(dest="command", required=True)
        enroll = commands.add_parser("enroll")
        enroll.add_argument("--config", required=True)
        enroll.add_argument("--token-file", required=True)
        run = commands.add_parser("run")
        run.add_argument("--config", required=True)
        activity = commands.add_parser("activity-worker")
        activity.add_argument("--config", required=True)
        args = parser.parse_args(argv)
        config = RuntimeConfig.model_validate_json(
            read_private_file(Path(args.config).absolute(), 65536)
        )
        if args.command == "activity-worker":
            # No real read or model provider is installed in this distribution, and
            # activity records and values come from control-plane routes that are not
            # yet served. Report unavailable rather than claim work it cannot do.
            raise FleetError("authority_unavailable")
        # No executable connector is registered in this foundation. Operator
        # declarations cannot invent installed capability; Task7 owns that catalog.
        if config.observation.connectors:
            raise FleetError("invalid_request")
        client = RuntimeClient(
            ControlTransport(config.origin, Path(config.ca_file), mode=config.mode)
        )
        clients.append(client)
        with CredentialStore(Path(config.state_directory)) as store:
            if args.command == "enroll":
                token = _token(args.token_file)
                # Refuse a second enrollment before consuming the one-use remote token.
                try:
                    os.stat("state.json", dir_fd=store._directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise FleetError("unsafe_storage")
            else:
                stop = threading.Event()
                previous = {}
                if threading.current_thread() is threading.main_thread():
                    for sig in (signal.SIGTERM, signal.SIGINT):
                        previous[sig] = signal.signal(sig, lambda signum, frame: stop.set())
                try:
                    result = RuntimeSupervisor(
                        client, store, config.observation, installation_id=config.installation_id
                    ).run(stop)
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
                print(
                    "stopped"
                    if result == 0
                    else ("unsafe_storage" if result == 4 else "unavailable")
                )
                return result
            issued = client.enroll(token, config.observation)
            if issued.state.identity.installation_id != config.installation_id:
                raise FleetError("forbidden")
            store.save(
                None,
                CredentialState(
                    revision=0,
                    instance_id=issued.state.identity.instance_id,
                    active_token=issued.token,
                    pending_token=None,
                    pending_version=None,
                ),
            )
            print(
                f"enrolled instance={issued.state.identity.instance_id} generation={issued.state.identity.generation}"
            )
        return 0
    except FleetError as error:
        result = (
            4
            if error.code == "unsafe_storage"
            else 2
            if error.code in ("invalid_request", "input_limit")
            else 3
        )
        print({2: "invalid_input", 3: "unavailable", 4: "unsafe_storage"}[result], file=sys.stderr)
        return result
    except OSError:
        print("unsafe_storage", file=sys.stderr)
        return 4


def main(argv=None) -> int:
    clients = []
    result = 3
    try:
        result = _execute(argv, clients)
    finally:
        for client in clients:
            try:
                client.close()
            except (FleetError, OSError):
                print("unavailable", file=sys.stderr)
                result = 3
    return result
