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

# An idle activity worker polls again after IDLE_MIN_S, doubling to IDLE_MAX_S.
IDLE_MIN_S, IDLE_MAX_S = 0.2, 1.0


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


def _serve_activities(config, client, store, fixture, stop) -> int:
    """Test mode only: the E03 session loop, plus one activity worker on its own transport.

    The worker polls with the supervisor's live credential and session: every request,
    including one from an invocation in flight across a rotation, carries the current
    pair, and the client is rebuilt whenever rotation or reacquisition replaces it. Its clock is re-anchored
    by every session and heartbeat response. A worker failure stops both.
    """
    from .activity_client import ActivityClient, ControlPlaneSources
    from .activity_fixture import FixtureTrust
    from .activity_state import InvocationJournal
    from .activity_worker import ActivityDependencies, ActivityWorker, ServerClock
    from .providers import ProviderSet

    trust_file, journal_directory, descriptors = fixture
    clock = ServerClock()
    supervisor = RuntimeSupervisor(
        client,
        store,
        config.observation,
        installation_id=config.installation_id,
        activity_clock=clock,
    )
    failed = []

    def live():
        shared = supervisor.activity_session()
        return None if shared is None else shared[:2]

    def work():
        transport = journal = None
        try:
            transport = ControlTransport(config.origin, Path(config.ca_file), mode=config.mode)
            journal = InvocationJournal(journal_directory)
            providers = ProviderSet(descriptors, test_mode=True)
            worker = current = None
            idle = IDLE_MIN_S
            while not stop.is_set():
                shared = supervisor.activity_session()
                if shared is None:
                    stop.wait(0.2)
                    continue
                if shared is not current:
                    token, secret, identity = shared
                    activity_client = ActivityClient(transport, token, secret, live=live)
                    sources = ControlPlaneSources(activity_client)
                    deps = ActivityDependencies(
                        identity=identity,
                        client=activity_client,
                        records=sources,
                        values=sources,
                        trust=FixtureTrust(trust_file),
                        providers=providers,
                        journal=journal,
                        clock=clock,
                        context=sources,
                    )
                    if worker is None:
                        worker = ActivityWorker(deps)
                    else:
                        worker.deps = deps
                    current = shared
                try:
                    status = worker.run_once()
                except FleetError as error:
                    if error.code == "unsafe_storage":
                        raise
                    status = "unavailable"
                if status == "idle":
                    # Nothing for it now: poll again soon, backing off while idle up
                    # to the E03 command loop's one-second ceiling.
                    stop.wait(idle)
                    idle = min(idle * 2, IDLE_MAX_S)
                    continue
                idle = IDLE_MIN_S
                if status in ("fenced", "unavailable", "refused"):
                    stop.wait(1.0)
        except Exception as error:  # noqa: BLE001 - reported as a fixed exit code only
            failed.append(error)
            stop.set()
        finally:
            if journal is not None:
                journal.close()
            if transport is not None:
                transport.close()

    thread = threading.Thread(target=work, name="activity-worker", daemon=True)
    thread.start()
    try:
        result = supervisor.run(stop)
    finally:
        stop.set()
        thread.join(timeout=60)
    if thread.is_alive():
        return 3
    if failed:
        error = failed[0]
        return 4 if isinstance(error, FleetError) and error.code == "unsafe_storage" else 3
    return result


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
        activity.add_argument("--activity-config")
        args = parser.parse_args(argv)
        config = RuntimeConfig.model_validate_json(
            read_private_file(Path(args.config).absolute(), 65536)
        )
        fixture = None
        if args.command == "activity-worker":
            if config.mode != "test" or args.activity_config is None:
                # No real read or model provider is installed in this distribution.
                # Report unavailable rather than claim work it cannot do; only test
                # mode runs the packaged synthetic providers (Task 13).
                raise FleetError("authority_unavailable")
            from .activity_fixture import worker_config

            try:
                fixture = worker_config(
                    read_private_file(Path(args.activity_config).absolute(), 262144)
                )
            except (ValueError, TypeError):
                raise FleetError("invalid_request") from None
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
                    if fixture is not None:
                        result = _serve_activities(config, client, store, fixture, stop)
                    else:
                        result = RuntimeSupervisor(
                            client,
                            store,
                            config.observation,
                            installation_id=config.installation_id,
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
