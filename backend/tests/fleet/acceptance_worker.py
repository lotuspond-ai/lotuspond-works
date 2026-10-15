"""External installed-library fixture consumer; never selected by the ordinary CLI."""

import ctypes
import json
import os
import signal
import socket
import ssl
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import command_fixture as fixtures
import tls_fixture as tls
from lotuspond_runtime.client import RuntimeClient
from lotuspond_runtime.commands import validate_delivery
from lotuspond_runtime.control_transport import ControlTransport
from lotuspond_runtime.destination import DestinationTransport, EgressPolicy
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.invocation import InvocationRequest, fixture_pin, invoke_fixture
from lotuspond_runtime.protocol import (
    CommandTrustSnapshot,
    ConnectionConfig,
    ResolvedSecretBinding,
    RuntimeIdentity,
    RuntimeObservation,
)
from lotuspond_runtime.resolver import MountedSecretResolver
from lotuspond_runtime.state import read_private_file
from lotuspond_sdk.executable.wire import wire_digest
from lotuspond_sdk.trust import TrustSet
from pydantic import SecretStr


def private(path, value):
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


class SecretOpenObservation:
    """Linux kernel observation of real fixed-child opens; never reads values."""

    def __init__(self, path):
        libc = ctypes.CDLL(None, use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError("fixture observation unavailable")
        self.count = 0
        if libc.inotify_add_watch(self.fd, os.fsencode(path), 0x20) < 0:  # IN_OPEN
            os.close(self.fd)
            raise OSError("fixture observation unavailable")

    def drain(self):
        while True:
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                return self.count
            offset = 0
            while offset < len(data):
                _, mask, _, length = struct.unpack_from("iIII", data, offset)
                self.count += bool(mask & 0x20)
                offset += 16 + length

    def close(self):
        os.close(self.fd)


def main():
    root = Path(sys.argv[2])
    settings = json.loads(Path(sys.argv[1]).read_text())
    client = RuntimeClient(
        ControlTransport(settings["origin"], Path(settings["ca_file"]), mode="test")
    )
    try:
        issued = client.enroll(
            SecretStr(settings["token"]), RuntimeObservation.model_validate(settings["observation"])
        )
        session = client.acquire(issued.token)
        private(root / "worker-ready.json", session.identity.model_dump(mode="json"))
        deadline = time.monotonic() + 12
        while not (root / "worker-proceed.json").exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        setup = json.loads((root / "worker-proceed.json").read_text())
        case = fixtures.command_case(
            identity=session.identity,
            now=setup["now"],
            connection_id=setup["connection_id"],
            configuration=ConnectionConfig.model_validate(setup["configuration"]),
        )
        value = client.poll(issued.token, session.secret)
        assert value is not None
        command = validate_delivery(
            value,
            identity=session.identity,
            records=fixtures.records(case),
            trust=CommandTrustSnapshot(
                1, TrustSet.model_validate(setup["trust"]), setup["now"] + 60000
            ),
            now_ms=time.time_ns() // 1000000,
        )
        claimed = client.claim(
            issued.token, session.secret, command.command_id, wire_digest(command)
        )
        assert claimed.status == "claimed"
        # A separate fixed child resolves the mounted fixture value after fresh authority.
        child_root = root / "child"
        child_root.mkdir(mode=0o700)
        value_file = child_root / "fixture-value"
        value_file.write_bytes(b"SYNTHETIC_FIXTURE_VALUE")
        value_file.chmod(0o600)
        mapping = child_root / "mapping.json"
        private(
            mapping,
            {
                "bindings": [
                    {"binding_id": "fixture-binding", "version": 1, "filename": value_file.name}
                ]
            },
        )
        reference = "00000000-0000-0000-0000-000000000007"
        with tls.tls_server(child_root, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok") as (
            origin,
            ca,
            requests,
        ):
            config = ConnectionConfig.model_validate(
                {
                    "connector": fixture_pin().model_dump(),
                    "operations": ["inspect"],
                    "destinations": [
                        {
                            "host": "localhost",
                            "port": int(origin.rsplit(":", 1)[1]),
                            "path_prefix": "/allowed",
                        }
                    ],
                    "secrets": [{"reference_id": reference, "version": 1}],
                    "data_handling": {"timeout_ms": 2000},
                }
            )
            request = InvocationRequest(
                session.identity,
                wire_digest(command),
                config,
                "inspect",
                "/allowed/x",
                b"input",
                ResolvedSecretBinding(reference, 1, "fixture-binding"),
            )

            active_token, active_session = issued.token, session.secret

            class CurrentFixtureAuthority:
                denial = None

                def authorize(self, request):
                    try:
                        fresh = client.heartbeat(active_token, active_session)
                    except FleetError:
                        self.denial = "api"
                        raise
                    if fresh.identity != request.identity:
                        self.denial = "identity"
                        raise FleetError("forbidden")
                    # Same independent file read by backend Dispatch; no phase supplies allow.
                    grant = json.loads(read_private_file(Path(settings["grant_file"]), 4096))
                    if grant != {"allowed": True}:
                        self.denial = "dispatch"
                        raise FleetError("forbidden")

            destination = DestinationTransport._for_test(
                EgressPolicy(("127.0.0.0/8", "::1/128"), (), ()), ca
            )
            observed = SecretOpenObservation(value_file)
            try:
                result = invoke_fixture(
                    request,
                    authority=CurrentFixtureAuthority(),
                    destination=destination,
                    resolver=MountedSecretResolver(child_root, mapping),
                )
                assert (
                    result.status == "observed"
                    and result.request_bytes == 5
                    and result.response_bytes == 2
                )
                assert len(requests) == 1 and b"SYNTHETIC_FIXTURE_VALUE" in requests[0]
                assert issued.token.get_secret_value().encode() not in requests[0]
                assert session.secret.get_secret_value().encode() not in requests[0]
                positive_secrets = observed.drain()
                assert positive_secrets >= 1
                private(
                    root / "worker-proof.json",
                    {
                        "token": issued.token.get_secret_value(),
                        "session": session.secret.get_secret_value(),
                    },
                )
                private(
                    root / "worker-positive.json",
                    {"effect_count": len(requests), "secret_resolution_count": positive_secrets},
                )
                from dataclasses import replace

                for number in range(3):
                    phase_file = root / f"worker-phase-{number}.json"
                    deadline = time.monotonic() + 15
                    while not phase_file.exists():
                        assert time.monotonic() < deadline
                        time.sleep(0.02)
                    phase = json.loads(read_private_file(phase_file, 8192))
                    active_token, active_session = (
                        SecretStr(phase["token"]),
                        SecretStr(phase["session"]),
                    )
                    attempt = replace(
                        request,
                        identity=RuntimeIdentity.model_validate(phase["identity"]),
                        command_sha256=phase["command_sha256"],
                    )
                    before, before_secrets = len(requests), observed.drain()
                    authority = CurrentFixtureAuthority()
                    result = invoke_fixture(
                        attempt,
                        authority=authority,
                        destination=destination,
                        resolver=MountedSecretResolver(child_root, mapping),
                    )
                    # A verified real handshake proves the same receiver is live during each denial.
                    context = ssl.create_default_context(cafile=str(ca))
                    with socket.create_connection(
                        ("127.0.0.1", int(origin.rsplit(":", 1)[1])), timeout=2
                    ) as raw:
                        with context.wrap_socket(raw, server_hostname="localhost"):
                            pass
                    after_secrets = observed.drain()
                    receipt = {
                        "phase": phase["phase"],
                        "status": result.status,
                        "authority_denial": authority.denial,
                        "sink_live": True,
                        "sink_before": before,
                        "sink_after": len(requests),
                        "secret_resolutions_before": before_secrets,
                        "secret_resolutions_after": after_secrets,
                    }
                    private(root / f"worker-phase-{number}-result.json", receipt)
                deadline = time.monotonic() + 12
                while not (root / "worker-finish.json").exists():
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
                private(
                    root / "worker-result.json",
                    {"effect_count": len(requests), "secret_resolution_count": observed.drain()},
                )
            finally:
                observed.close()

        print("fixture_worker_passed")
    finally:
        client.close()


if __name__ == "__main__":

    def interrupted(signum, frame):
        raise TimeoutError("fixture interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        print(
            "worker failure: "
            + type(error).__name__
            + " at "
            + ",".join(f"{Path(f.filename).name}:{f.lineno}" for f in frames),
            file=sys.stderr,
        )
        raise SystemExit(1)
