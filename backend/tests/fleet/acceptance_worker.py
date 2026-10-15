"""External installed-library fixture consumer; never selected by the ordinary CLI."""

import json
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
from lotuspond_runtime.invocation import InvocationRequest, fixture_pin, invoke_fixture
from lotuspond_runtime.protocol import (
    CommandTrustSnapshot,
    ConnectionConfig,
    ResolvedSecretBinding,
    RuntimeObservation,
)
from lotuspond_runtime.resolver import MountedSecretResolver
from lotuspond_sdk.executable.wire import wire_digest
from lotuspond_sdk.trust import TrustSet
from pydantic import SecretStr


def private(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def main():
    root = Path(sys.argv[2])
    settings = json.loads(Path(sys.argv[1]).read_text())
    client = RuntimeClient(
        ControlTransport(settings["origin"], Path(settings["ca_file"]), mode="test")
    )
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
    claimed = client.claim(issued.token, session.secret, command.command_id, wire_digest(command))
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

        class CurrentFixtureAuthority:
            def authorize(self, request):
                # Actual current API proof; independent fixture dispatch was qualified separately.
                fresh = client.heartbeat(issued.token, session.secret)
                assert fresh.identity == session.identity

        destination = DestinationTransport._for_test(
            EgressPolicy(("127.0.0.0/8", "::1/128"), (), ()), ca
        )
        result = invoke_fixture(
            request,
            authority=CurrentFixtureAuthority(),
            destination=destination,
            resolver=MountedSecretResolver(child_root, mapping),
        )
        assert (
            result.status == "observed" and result.request_bytes == 5 and result.response_bytes == 2
        )
        assert len(requests) == 1 and b"SYNTHETIC_FIXTURE_VALUE" in requests[0]
        assert issued.token.get_secret_value().encode() not in requests[0]
        assert session.secret.get_secret_value().encode() not in requests[0]
        effect_count = len(requests)
    private(
        root / "worker-proof.json",
        {"token": issued.token.get_secret_value(), "session": session.secret.get_secret_value()},
    )
    private(root / "worker-result.json", {"effect_count": effect_count})
    client.close()
    print("fixture_worker_passed")


if __name__ == "__main__":
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
