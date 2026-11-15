"""Explicit private runtime entrypoint; never accepts secrets in argv or environment."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from pydantic import SecretStr

from .client import RuntimeClient
from .control_transport import ControlTransport
from .errors import FleetError
from .pacing import PollPacer, run_paced
from .protocol import CredentialState, RuntimeConfig, validate_token
from .state import CredentialStore, read_private_file
from .supervisor import RuntimeSupervisor


def _pass(worker_run):
    """One worker pass's status; a refusal for rate is told apart from other failures."""
    try:
        return worker_run()
    except FleetError as error:
        if error.code == "unsafe_storage":
            raise
        return "rate_limited" if error.code == "rate_limited" else "unavailable"


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


def _serve_actions(config, setup, actions, journal, clock, supervisor, stop, failed):
    """One action worker on its own transport, beside the activity worker (E05).

    `actions` is the configuration's write providers (the synthetic target's in test
    mode, or `https-json`'s) and their command trust: the operator's
    (`OperatorCommandTrust`) outside test mode, a test file inside it. It shares the activity worker's journal (one holder per
    process) and server-anchored clock, and the supervisor's live session.
    """
    from .action_client import ActionClient
    from .action_fixture import FixtureCommandTrust
    from .action_invocation import WriteProviderSet
    from .action_worker import ActionDependencies, ActionWorker
    from .trust import OperatorCommandTrust

    trust_file, descriptors = actions
    # A real write provider's connection secrets resolve through the mounted resolver
    # of the operator configuration; the synthetic target's configuration has none.
    secrets = None
    if getattr(setup, "resolver", None) is not None:
        from .action_worker import MountedActionSecrets

        secrets = MountedActionSecrets(setup.resolver)

    def live():
        shared = supervisor.activity_session()
        return None if shared is None else shared[:2]

    def work():
        transport = None
        try:
            transport = ControlTransport(config.origin, Path(config.ca_file), mode=config.mode)
            providers = WriteProviderSet(descriptors, test_mode=setup.test_mode)
            trust = (
                FixtureCommandTrust(trust_file, clock)
                if setup.test_mode
                else OperatorCommandTrust(trust_file, clock)
            )
            state = {"worker": None, "current": None}

            def once():
                shared = supervisor.activity_session()
                if shared is None:
                    return None
                if shared is not state["current"]:
                    token, secret, identity = shared
                    deps = ActionDependencies(
                        identity=identity,
                        client=ActionClient(transport, token, secret, live=live),
                        trust=trust,
                        providers=providers,
                        journal=journal,
                        clock=clock,
                        secrets=secrets,
                    )
                    if state["worker"] is None:
                        state["worker"] = ActionWorker(deps)
                    else:
                        state["worker"].deps = deps
                    state["current"] = shared
                return _pass(state["worker"].run_once)

            # Paced under the control plane's `action-poll:<instance>` allowance.
            run_paced(stop, PollPacer(), once)
        except Exception as error:  # noqa: BLE001 - reported as a fixed exit code only
            failed.append(error)
            stop.set()
        finally:
            if transport is not None:
                transport.close()

    thread = threading.Thread(target=work, name="action-worker", daemon=True)
    thread.start()
    return thread


def _serve_activities(config, client, store, setup, stop, actions=None) -> int:
    """The E03 session loop, plus one activity worker on its own transport.

    `setup` is test mode's synthetic fixture or an operator's real providers
    (`provider_config`); nothing else differs between them.

    The worker polls with the supervisor's live credential and session: every request,
    including one from an invocation in flight across a rotation, carries the current
    pair, and the client is rebuilt whenever rotation or reacquisition replaces it. Its clock is re-anchored
    by every session and heartbeat response. A worker failure stops both.
    """
    from .activity_client import ActivityClient, ControlPlaneSources
    from .activity_state import InvocationJournal
    from .activity_worker import ActivityDependencies, ActivityWorker, ServerClock
    from .providers import ProviderSet

    journal_directory, descriptors = setup.journal_directory, setup.descriptors
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

    # One journal holder per process: the action worker, when configured, shares it.
    journal = InvocationJournal(journal_directory)

    def work():
        transport = None
        try:
            transport = ControlTransport(config.origin, Path(config.ca_file), mode=config.mode)
            providers = ProviderSet(descriptors, test_mode=setup.test_mode)
            state = {"worker": None, "current": None}

            def once():
                shared = supervisor.activity_session()
                if shared is None:
                    return None
                if shared is not state["current"]:
                    token, secret, identity = shared
                    activity_client = ActivityClient(transport, token, secret, live=live)
                    sources = ControlPlaneSources(activity_client)
                    deps = ActivityDependencies(
                        identity=identity,
                        client=activity_client,
                        records=sources,
                        values=sources,
                        trust=setup.trust,
                        providers=providers,
                        journal=journal,
                        clock=clock,
                        context=sources,
                        secrets=setup.secrets,
                    )
                    if state["worker"] is None:
                        state["worker"] = ActivityWorker(deps)
                    else:
                        state["worker"].deps = deps
                    state["current"] = shared
                return _pass(state["worker"].run_once)

            # Paced under the control plane's `activity-poll:<instance>` allowance:
            # nothing for it now polls again soon, backing off while idle, never faster
            # than the allowance, and a refusal for rate backs off further.
            run_paced(stop, PollPacer(), once)
        except Exception as error:  # noqa: BLE001 - reported as a fixed exit code only
            failed.append(error)
            stop.set()
        finally:
            if transport is not None:
                transport.close()

    threads = [threading.Thread(target=work, name="activity-worker", daemon=True)]
    threads[0].start()
    try:
        if actions is not None:
            threads.append(
                _serve_actions(config, setup, actions, journal, clock, supervisor, stop, failed)
            )
        result = supervisor.run(stop)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=60)
        if not any(thread.is_alive() for thread in threads):
            journal.close()
    if any(thread.is_alive() for thread in threads):
        return 3
    if failed:
        error = failed[0]
        return 4 if isinstance(error, FleetError) and error.code == "unsafe_storage" else 3
    return result


def _execute(argv, clients) -> int:
    try:
        parser = _Parser(
            prog="lotuspond-runtime", description="Private LotusPond Works Linux runtime"
        )
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
        fixture = actions = None
        if args.command == "activity-worker":
            if args.activity_config is None:
                # Without an operator's provider configuration nothing can be served.
                raise FleetError("authority_unavailable")
            raw = read_private_file(Path(args.activity_config).absolute(), 1048576)
            try:
                operator_format = "secrets" in json.loads(raw)
                if config.mode == "test" and operator_format:
                    # A test-mode runtime may run real providers against local stand-ins.
                    from .provider_config import worker_config as operator_config

                    fixture = operator_config(raw, test_mode=True)
                    actions = fixture.actions
                elif config.mode == "test":
                    # Otherwise test mode runs the packaged synthetic providers (Task 13),
                    # and the synthetic target's write providers when it names them (E05).
                    from .activity_fixture import FixtureTrust, worker_config
                    from .provider_config import ActivitySetup

                    data = json.loads(raw)
                    if type(data) is dict and "actions" in data:
                        from .action_fixture import worker_config as action_config

                        actions = action_config(data.pop("actions"))
                        raw = json.dumps(data).encode()
                    trust_file, journal, descriptors = worker_config(raw)
                    fixture = ActivitySetup(
                        FixtureTrust(trust_file), journal, descriptors, None, True
                    )
                else:
                    from .provider_config import worker_config as operator_config

                    fixture = operator_config(raw)
                    actions = fixture.actions
            except (ValueError, TypeError, KeyError):
                raise FleetError("invalid_request") from None
        # A runtime reports the connectors its write providers serve (E05), and its
        # worker refuses to start reporting any connector none of them serves: a
        # declaration cannot invent installed capability, and the control plane admits
        # only connectors its operator installed. Only enrollment and the worker report
        # them, in either mode.
        if config.observation.connectors:
            if args.command not in ("enroll", "activity-worker"):
                raise FleetError("invalid_request")
            if args.command == "activity-worker":
                served = {d.connector for d in actions[1]} if actions is not None else set()
                if not set(config.observation.connectors) <= served:
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
                        result = _serve_activities(
                            config, client, store, fixture, stop, actions=actions
                        )
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
