import importlib

import pytest

from lotuspond_runtime.errors import FleetError


def test_default_authority_denies():
    m = importlib.import_module("lotuspond_runtime.invocation")
    with pytest.raises(FleetError, match="forbidden"):
        m.DenyInvocationAuthority().authorize(None)


def test_production_has_no_fixture_selector():
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "lotuspond_runtime"
    for name in ("supervisor.py", "cli.py", "client.py", "__main__.py"):
        assert "fixture_connector" not in (root / name).read_text()
        assert "invoke_fixture" not in (root / name).read_text()


def invocation_setup(tmp_path, origin, ca, *, timeout=2000, response_limit=1048576):
    import json

    from lotuspond_runtime.destination import DestinationTransport, EgressPolicy
    from lotuspond_runtime.invocation import InvocationRequest, ResolvedSecretBinding, fixture_pin
    from lotuspond_runtime.protocol import ConnectionConfig, RuntimeIdentity
    from lotuspond_runtime.resolver import MountedSecretResolver

    tmp_path.chmod(0o700)
    value = tmp_path / "secret-v1"
    value.write_bytes(b"SYNTHETIC_FIXTURE_VALUE")
    value.chmod(0o600)
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps(
            {"bindings": [{"binding_id": "binding-a", "version": 1, "filename": "secret-v1"}]}
        )
    )
    mapping.chmod(0o600)
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
            "secrets": [{"reference_id": "00000000-0000-0000-0000-000000000007", "version": 1}],
            "data_handling": {"timeout_ms": timeout, "max_response_bytes": response_limit},
        }
    )
    identity = RuntimeIdentity.model_validate(
        {
            "scope": {
                "workspace_id": "00000000-0000-0000-0000-000000000001",
                "project_id": "00000000-0000-0000-0000-000000000002",
                "environment_id": "00000000-0000-0000-0000-000000000003",
            },
            "installation_id": "test-install",
            "recovery_epoch": 1,
            "pool_id": "00000000-0000-0000-0000-000000000004",
            "instance_id": "00000000-0000-0000-0000-000000000005",
            "credential_version": 1,
            "generation": 1,
        }
    )
    request = InvocationRequest(
        identity,
        "b" * 64,
        config,
        "inspect",
        "/allowed/x",
        b"input",
        ResolvedSecretBinding(config.secrets[0].reference_id, 1, "binding-a"),
    )
    destination = DestinationTransport._for_test(
        EgressPolicy(("127.0.0.0/8", "::1/128"), (), ()), ca
    )
    return request, destination, MountedSecretResolver(tmp_path, mapping)


class Allow:
    def __init__(self, deny_at=0):
        self.calls = 0
        self.deny_at = deny_at

    def authorize(self, request):
        self.calls += 1
        if self.calls == self.deny_at:
            raise FleetError("forbidden")


def test_isolated_child_real_tls_and_sanitized_environment(tmp_path, monkeypatch):
    import os

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    events, children, envelopes = [], [], []
    monkeypatch.setenv("LOTUSPOND_SYNTHETIC_CONTROL_TOKEN", "CONTROL_SENTINEL_NO_CHILD")
    monkeypatch.setenv("HTTPS_PROXY", "PROXY_SENTINEL_NO_CHILD")
    popen, send = m.subprocess.Popen, m._send

    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        assert kwargs["env"] == {}
        assert b"CONTROL_SENTINEL_NO_CHILD" not in open(f"/proc/{child.pid}/environ", "rb").read()
        assert "CONTROL_SENTINEL_NO_CHILD" not in repr(args)
        return child

    def ipc(channel, value, deadline):
        envelopes.append(value)
        return send(channel, value, deadline)

    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m, "_send", ipc)
    monkeypatch.setattr(m, "_observe", events.append)
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok") as (
        origin,
        ca,
        requests,
    ):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca)
        authority = Allow()
        result = m.invoke_fixture(
            request, authority=authority, destination=destination, resolver=resolver
        )
        assert result == m.FixtureResult("observed", 5, 2)
        assert authority.calls >= 2
        assert events.count("secret_open") > 0
        assert len(requests) == 1
        assert b"SYNTHETIC_FIXTURE_VALUE" in requests[0]
        assert "SYNTHETIC_FIXTURE_VALUE" not in repr(result) + repr(envelopes)
        assert "CONTROL_SENTINEL_NO_CHILD" not in repr(envelopes)
        assert all(child.poll() is not None for child in children)
        with pytest.raises(ProcessLookupError):
            os.killpg(children[0].pid, 0)


@pytest.mark.parametrize(
    "denial", ["identity", "authority", "connection", "destination", "ca", "tls"]
)
def test_no_secret_read_until_authority_and_tls_pass(tmp_path, monkeypatch, denial):
    from dataclasses import replace

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m
    from lotuspond_runtime.destination import DestinationTransport, EgressPolicy

    events = []
    monkeypatch.setattr(m, "_observe", events.append)
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n") as (
        origin,
        ca,
        requests,
    ):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca)
        authority = Allow(1 if denial == "authority" else 0)
        if denial == "identity":

            class ScopedAuthority:
                def authorize(self, current):
                    if (
                        current.identity.scope.environment_id
                        != "00000000-0000-0000-0000-000000000003"
                    ):
                        raise FleetError("forbidden")

            authority = ScopedAuthority()
            scope = request.identity.scope.model_copy(
                update={"environment_id": "00000000-0000-0000-0000-000000000099"}
            )
            request = replace(
                request, identity=request.identity.model_copy(update={"scope": scope})
            )
        elif denial == "connection":
            request = replace(request, operation="unknown")
        elif denial == "destination":
            destination = DestinationTransport(EgressPolicy((), (), ()), ca)
        elif denial == "ca":
            destination.ca_file = tmp_path / "missing-ca"
        elif denial == "tls":
            from tls_fixtures import certificates

            other = tmp_path / "other"
            other.mkdir()
            destination.ca_file = certificates(other)[0]
        result = m.invoke_fixture(
            request, authority=authority, destination=destination, resolver=resolver
        )
        assert result.status == "denied"
        assert events.count("secret_open") == 0
        assert requests == []


@pytest.mark.parametrize(
    "failure", ["fresh-authority", "secret", "oversized", "slow", "redirect", "crash"]
)
def test_child_failures_cleanup_and_uncertainty(tmp_path, monkeypatch, failure):
    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    reply = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
    if failure == "oversized":
        reply = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 100
    elif failure == "redirect":
        reply = b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1:1/\r\nContent-Length: 0\r\n\r\n"
    elif failure == "slow":
        reply = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 100
    if failure == "crash":
        popen = m.subprocess.Popen

        def crash(args, **kwargs):
            return popen([args[0], "-I", "-c", "import os;os._exit(17)"], **kwargs)

        monkeypatch.setattr(m.subprocess, "Popen", crash)
    with tls_server(tmp_path, reply, body_trickle=failure == "slow") as (origin, ca, requests):
        request, destination, resolver = invocation_setup(
            tmp_path,
            origin,
            ca,
            timeout=600 if failure == "slow" else 2000,
            response_limit=10 if failure == "oversized" else 1048576,
        )
        if failure == "secret":
            (tmp_path / "secret-v1").unlink()
        authority = Allow(2 if failure == "fresh-authority" else 0)
        result = m.invoke_fixture(
            request, authority=authority, destination=destination, resolver=resolver
        )
        assert result.status == (
            "uncertain" if failure in ("oversized", "slow", "redirect") else "denied"
        )
        assert result.response_bytes == 0
        assert len(requests) == (1 if result.status == "uncertain" else 0)
        assert m._active == 0
        assert m._pools == {}


def test_ipc_bound_refused_before_spawn(tmp_path, monkeypatch):
    from dataclasses import replace

    import lotuspond_runtime.invocation as m

    request, destination, resolver = invocation_setup(
        tmp_path, "https://localhost:443", tmp_path / "ca"
    )
    request = replace(request, input_bytes=b"x" * 65536)
    monkeypatch.setattr(m.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    result = m.invoke_fixture(
        request, authority=Allow(), destination=destination, resolver=resolver
    )
    assert result.status == "denied"


@pytest.mark.parametrize("operator,pool,different_pool", [(2, 1, False), (1, 2, True)])
def test_concurrent_child_limits_and_reconfiguration(
    tmp_path, monkeypatch, operator, pool, different_pool
):
    import threading
    from dataclasses import replace

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    entered, release = threading.Event(), threading.Event()
    children = []
    popen = m.subprocess.Popen

    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def observe(event):
        if event == "sending":
            # Parent is paused after fresh authority returned: its authority slot is free.
            entered.set()
            release.wait(2)

    def reply(request):
        release.wait(2)
        return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m, "_observe", observe)
    with tls_server(tmp_path, reply) as (origin, ca, requests):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca)
        results = []
        with m._configured_limits(m.InvocationLimits(operator, pool)):
            worker = threading.Thread(
                target=lambda: results.append(
                    m.invoke_fixture(
                        request, authority=Allow(), destination=destination, resolver=resolver
                    )
                )
            )
            worker.start()
            assert entered.wait(1)
            try:
                second = request
                if different_pool:
                    second = replace(
                        request,
                        identity=request.identity.model_copy(
                            update={"pool_id": "00000000-0000-0000-0000-000000000099"}
                        ),
                    )
                assert (
                    m.invoke_fixture(
                        second, authority=Allow(), destination=destination, resolver=resolver
                    ).status
                    == "denied"
                )
                assert len(children) == 1
                assert children[0].poll() is None
                with pytest.raises(FleetError, match="conflict"):
                    with m._configured_limits(m.InvocationLimits(1, 1)):
                        pass
            finally:
                release.set()
                worker.join(3)
            assert not worker.is_alive()
        assert results[0].status == "observed"


def test_hung_authority_is_bounded_and_does_not_accumulate_threads(tmp_path):
    import threading
    import time

    import lotuspond_runtime.invocation as m

    release = threading.Event()
    calls = []

    class Hung:
        def authorize(self, request):
            calls.append(1)
            release.wait(3)

    request, destination, resolver = invocation_setup(
        tmp_path, "https://localhost:443", tmp_path / "ca", timeout=30
    )
    start = time.monotonic()
    try:
        for _ in range(3):
            assert (
                m.invoke_fixture(
                    request, authority=Hung(), destination=destination, resolver=resolver
                ).status
                == "denied"
            )
        assert len(calls) == 1
        assert time.monotonic() - start < 0.4
    finally:
        release.set()
        time.sleep(0.03)


def test_lost_authority_stops_already_sent_slow_request(tmp_path):
    import time

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    reply = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 100
    with tls_server(tmp_path, reply, body_trickle=True) as (origin, ca, requests):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca, timeout=2500)
        authority = Allow(3)
        start = time.monotonic()
        result = m.invoke_fixture(
            request, authority=authority, destination=destination, resolver=resolver
        )
        assert result.status == "uncertain"
        assert time.monotonic() - start < 1
        assert len(requests) == 1


@pytest.mark.parametrize("stage", ["before-grant", "proven-presend", "observed", "uncertain"])
def test_cleanup_failure_fences_further_children(tmp_path, monkeypatch, stage):
    import os

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    kill = os.killpg

    def failed_probe(pid, sig):
        if sig == 0:
            raise PermissionError("synthetic acknowledgement failure")
        return kill(pid, sig)

    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok") as (
        origin,
        ca,
        requests,
    ):
        request, destination, resolver = invocation_setup(
            tmp_path, origin, ca, response_limit=1 if stage == "uncertain" else 10
        )
        if stage == "before-grant":
            (tmp_path / "secret-v1").unlink()
        elif stage == "proven-presend":
            (tmp_path / "secret-v1").write_bytes(b"invalid-header-value\n")
        monkeypatch.setattr(os, "killpg", failed_probe)
        try:
            result = m.invoke_fixture(
                request, authority=Allow(), destination=destination, resolver=resolver
            )
            exposed = stage in ("observed", "uncertain")
            assert len(requests) == int(exposed)
            if exposed:
                assert b"SYNTHETIC_FIXTURE_VALUE" in requests[0]
            assert result.status == ("uncertain" if exposed else "denied")
            assert result.request_bytes == (len(request.input_bytes) if exposed else 0)
            assert result.response_bytes == (2 if stage == "observed" else 0)
            assert m._cleanup_failed
            assert (
                m.invoke_fixture(
                    request, authority=Allow(), destination=destination, resolver=resolver
                ).status
                == "denied"
            )
        finally:
            m._cleanup_failed = False


@pytest.mark.parametrize(
    "field", ["reference", "version", "constructed-config", "constructed-identity", "artifact"]
)
def test_exact_pins_and_constructed_models_revalidated(tmp_path, monkeypatch, field):
    from dataclasses import replace

    import lotuspond_runtime.invocation as m

    request, destination, resolver = invocation_setup(
        tmp_path, "https://localhost:443", tmp_path / "ca"
    )
    if field == "reference":
        request = replace(
            request,
            secret_binding=replace(
                request.secret_binding, reference_id="00000000-0000-0000-0000-000000000099"
            ),
        )
    elif field == "version":
        request = replace(request, secret_binding=replace(request.secret_binding, version=2))
    elif field == "constructed-config":
        request = replace(
            request, configuration=request.configuration.model_copy(update={"unknown": "value"})
        )
    elif field == "constructed-identity":
        request = replace(
            request, identity=request.identity.model_copy(update={"generation": True})
        )
    else:
        pin = request.configuration.connector.model_copy(update={"artifact_sha256": "a" * 64})
        request = replace(
            request, configuration=request.configuration.model_copy(update={"connector": pin})
        )
    monkeypatch.setattr(m.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    assert (
        m.invoke_fixture(
            request, authority=Allow(), destination=destination, resolver=resolver
        ).status
        == "denied"
    )


def test_installed_fixture_child(installed_runtime, tmp_path):
    import inspect
    from pathlib import Path

    from conftest import artifact_run

    venv, env = installed_runtime
    # Only test setup and TLS service are copied; runtime imports must come from the installed wheel.
    script = (
        Path(__file__).with_name("tls_fixtures.py").read_text()
        + "\n"
        + inspect.getsource(invocation_setup)
        + """
from pathlib import Path
import sys
import lotuspond_runtime.invocation as m
assert Path(m.__file__).is_relative_to(Path(sys.prefix))
class Authority:
 def authorize(self, request): pass
root=Path.cwd()
with tls_server(root,b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nok') as (origin,ca,requests):
 request,destination,resolver=invocation_setup(root,origin,ca)
 assert m.invoke_fixture(request,authority=Authority(),destination=destination,resolver=resolver)==m.FixtureResult('observed',5,2)
 assert len(requests)==1
 assert b'SYNTHETIC_FIXTURE_VALUE' in requests[0]
print('installed fixed child -I/-m observed; no source overrides')
"""
    )
    artifact_run([str(venv / "bin/python"), "-I", "-c", script], tmp_path, env)


@pytest.mark.parametrize("failure", ["timeout", "crash"])
def test_real_child_and_dns_descendant_cleanup(tmp_path, monkeypatch, failure):
    import json
    import os
    import signal
    import time
    from pathlib import Path

    from tls_fixtures import certificates

    import lotuspond_runtime.invocation as m

    tmp_path.chmod(0o700)
    ca, _ = certificates(tmp_path)
    request, destination, resolver = invocation_setup(
        tmp_path, "https://localhost:443", ca, timeout=600
    )
    marker = tmp_path / "helper-observation.json"
    dns_script = (
        "import json,os,time\nfrom pathlib import Path\n"
        f"marker=Path({str(marker)!r})\n"
        "start=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])\n"
        "with marker.with_suffix('.tmp').open('w') as observation:\n"
        " json.dump([os.getpid(),os.getpgrp(),start],observation)\n"
        "marker.with_suffix('.tmp').replace(marker)\n"
        "time.sleep(10)\n"
    )
    bootstrap = (
        "import runpy,threading,time,os\n"
        "import lotuspond_runtime.destination as d\n"
        f"d._DNS={dns_script!r}\n"
    )
    if failure == "crash":
        bootstrap += (
            "def crash():\n"
            f" while not os.path.exists({str(marker)!r}): time.sleep(.005)\n"
            " os._exit(19)\nthreading.Thread(target=crash,daemon=True).start()\n"
        )
    bootstrap += 'runpy.run_module("lotuspond_runtime.fixture_connector",run_name="__main__")\n'
    popen = m.subprocess.Popen
    children = []

    def spawn(args, **kwargs):
        # Test-local injection into the real isolated fixed module; product argv stays fixed.
        child = popen([args[0], "-I", "-c", bootstrap, args[-1]], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m, "_cleanup_failed", False)
    start = time.monotonic()
    deadline = start + 1.5
    try:
        result = m.invoke_fixture(
            request, authority=Allow(), destination=destination, resolver=resolver
        )
        assert result.status == "denied"
        if m._cleanup_failed:
            assert (
                m.invoke_fixture(
                    request, authority=Allow(), destination=destination, resolver=resolver
                ).status
                == "denied"
            )
            assert len(children) == 1
        assert time.monotonic() < deadline
        helper_pid, helper_group, helper_start = json.loads(marker.read_text())
        assert helper_group == children[0].pid
        assert children[0].poll() is not None
        state = Path(f"/proc/{helper_pid}/stat")
        while True:
            try:
                fields = state.read_text().rsplit(")", 1)[1].split()
            except (FileNotFoundError, ProcessLookupError):
                # The helper can disappear before open (ENOENT) or during read (ESRCH).
                break
            assert int(fields[19]) == helper_start, "helper PID was reused"
            if int(fields[2]) <= 0:
                # Mid-exit the task can already be detached from its process group, and
                # /proc then reports no group (CI read -1): no group, never another one,
                # so termination is observable.
                break
            assert int(fields[2]) == helper_group
            if fields[0] in ("Z", "X"):
                # Termination is observable; this does not claim the orphan was reaped.
                break
            # SIGKILL is asynchronous; waiting for the leader does not wait for descendants.
            assert time.monotonic() < deadline, f"DNS helper still alive: {fields[0]}"
            time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        assert time.monotonic() < deadline
    finally:
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=0.5)


def test_versions_and_invocations_never_share_secret_values(tmp_path):
    import json
    from dataclasses import replace

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m
    from lotuspond_runtime.protocol import SecretPin

    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n") as (
        origin,
        ca,
        requests,
    ):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca)
        other = tmp_path / "secret-v2"
        other.write_bytes(b"SECOND_SYNTHETIC_VALUE")
        other.chmod(0o600)
        resolver.mapping_file.write_text(
            json.dumps(
                {
                    "bindings": [
                        {"binding_id": "binding-a", "version": 1, "filename": "secret-v1"},
                        {"binding_id": "binding-a", "version": 2, "filename": "secret-v2"},
                    ]
                }
            )
        )
        assert (
            m.invoke_fixture(
                request, authority=Allow(), destination=destination, resolver=resolver
            ).status
            == "observed"
        )
        second = replace(
            request,
            configuration=request.configuration.model_copy(
                update={
                    "secrets": (
                        SecretPin(reference_id=request.secret_binding.reference_id, version=2),
                    )
                }
            ),
            secret_binding=replace(request.secret_binding, version=2),
        )
        assert (
            m.invoke_fixture(
                second, authority=Allow(), destination=destination, resolver=resolver
            ).status
            == "observed"
        )
        assert len(requests) == 2
        assert (
            b"SYNTHETIC_FIXTURE_VALUE" in requests[0]
            and b"SECOND_SYNTHETIC_VALUE" not in requests[0]
        )
        assert (
            b"SECOND_SYNTHETIC_VALUE" in requests[1]
            and b"SYNTHETIC_FIXTURE_VALUE" not in requests[1]
        )


def test_authority_thread_start_failure_is_bounded_and_releases_slot(tmp_path, monkeypatch):
    import threading

    import lotuspond_runtime.invocation as m

    request, destination, resolver = invocation_setup(
        tmp_path, "https://localhost:443", tmp_path / "ca"
    )
    start = threading.Thread.start

    def failed_start(self):
        raise RuntimeError("synthetic OS thread failure")

    monkeypatch.setattr(threading.Thread, "start", failed_start)
    try:
        assert (
            m.invoke_fixture(
                request, authority=Allow(), destination=destination, resolver=resolver
            ).status
            == "denied"
        )
        assert m._authority_slot.acquire(blocking=False)
        m._authority_slot.release()
    finally:
        monkeypatch.setattr(threading.Thread, "start", start)
        if not m._authority_slot._value:
            m._authority_slot.release()


def test_redirect_receiver_never_sees_secret(tmp_path, monkeypatch):
    import tls_fixtures

    import lotuspond_runtime.invocation as m

    ca, key = tls_fixtures.certificates(tmp_path)
    monkeypatch.setattr(tls_fixtures, "certificates", lambda directory: (ca, key))
    reply = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
    with tls_fixtures.tls_server(tmp_path, reply) as (receiver_origin, _, received):
        redirect = (
            f"HTTP/1.1 302 Found\r\nLocation: {receiver_origin}/allowed/x\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode("ascii")
        with tls_fixtures.tls_server(tmp_path, redirect) as (origin, _, requests):
            request, destination, resolver = invocation_setup(tmp_path, origin, ca)
            result = m.invoke_fixture(
                request, authority=Allow(), destination=destination, resolver=resolver
            )
            assert result.status == "uncertain"
            assert len(requests) == 1
            assert b"SYNTHETIC_FIXTURE_VALUE" in requests[0]
            assert received == []


def test_parent_deadline_after_fresh_grant_retains_possible_send(tmp_path, monkeypatch):
    import os
    import threading
    import time

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    received = threading.Event()
    children = []
    popen, send = m.subprocess.Popen, m._send

    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def delayed(channel, value, deadline):
        send(channel, value, deadline)
        if value == {"authorized": True}:
            assert received.wait(max(0, (deadline - time.monotonic_ns()) / 1_000_000_000))
            time.sleep(max(0, (deadline - time.monotonic_ns()) / 1_000_000_000) + 0.04)

    def reply(request):
        received.set()
        return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m, "_send", delayed)
    with tls_server(tmp_path, reply) as (origin, ca, requests):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca, timeout=800)
        start = time.monotonic()
        result = m.invoke_fixture(
            request, authority=Allow(), destination=destination, resolver=resolver
        )
        assert len(requests) == 1
        assert result == m.FixtureResult("uncertain", len(request.input_bytes), 0)
        assert time.monotonic() - start < 1.5
        assert children[0].poll() is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(children[0].pid, 0)
        assert not m._cleanup_failed
        assert m._active == 0 and m._pools == {}


@pytest.mark.parametrize("delivery", ["not-delivered", "delivered"])
def test_ambiguous_grant_delivery_is_uncertain(tmp_path, monkeypatch, delivery):
    import os
    import threading
    import time

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    received = threading.Event()
    children = []
    popen, send = m.subprocess.Popen, m._send

    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def ambiguous(channel, value, deadline):
        if value == {"authorized": True}:
            if delivery == "delivered":
                send(channel, value, deadline)
                assert received.wait(max(0, (deadline - time.monotonic_ns()) / 1_000_000_000))
            raise OSError("synthetic ambiguous grant write")
        return send(channel, value, deadline)

    def reply(request):
        received.set()
        return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    monkeypatch.setattr(m, "_send", ambiguous)
    with tls_server(tmp_path, reply) as (origin, ca, requests):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca)
        result = m.invoke_fixture(
            request, authority=Allow(), destination=destination, resolver=resolver
        )
        assert len(requests) == int(delivery == "delivered")
        assert result == m.FixtureResult("uncertain", len(request.input_bytes), 0)
        assert children[0].poll() is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(children[0].pid, 0)
        assert not m._cleanup_failed
        assert m._active == 0 and m._pools == {}


def test_unexpected_error_propagates_through_cleanup_fence(tmp_path, monkeypatch):
    import os
    import threading

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    received = threading.Event()
    kill = os.killpg

    class Unexpected(RuntimeError):
        pass

    def observe(event):
        if event == "sending":
            assert received.wait(1)
            raise Unexpected("synthetic unexpected observer failure")

    def failed_probe(pid, sig):
        if sig == 0:
            raise PermissionError("synthetic acknowledgement failure")
        return kill(pid, sig)

    def reply(request):
        received.set()
        return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    monkeypatch.setattr(m, "_observe", observe)
    monkeypatch.setattr(os, "killpg", failed_probe)
    with tls_server(tmp_path, reply) as (origin, ca, requests):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca)
        try:
            with pytest.raises(Unexpected):
                m.invoke_fixture(
                    request, authority=Allow(), destination=destination, resolver=resolver
                )
            assert len(requests) == 1
            assert m._cleanup_failed
            assert m._active == 0 and m._pools == {}
        finally:
            m._cleanup_failed = False


def test_socket_close_error_retains_uncertainty_and_fences(tmp_path, monkeypatch):
    import socket

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    close, send = socket.socket.close, m._send
    parent_ids = set()

    def record_parent(channel, value, deadline):
        parent_ids.add(id(channel))
        return send(channel, value, deadline)

    def failed_close(channel):
        close(channel)
        if id(channel) in parent_ids:
            raise OSError("synthetic private IPC close failure")

    monkeypatch.setattr(m, "_send", record_parent)
    monkeypatch.setattr(socket.socket, "close", failed_close)
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n") as (
        origin,
        ca,
        requests,
    ):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca)
        try:
            result = m.invoke_fixture(
                request, authority=Allow(), destination=destination, resolver=resolver
            )
            assert len(requests) == 1
            assert result == m.FixtureResult("uncertain", len(request.input_bytes), 0)
            assert m._cleanup_failed
            assert (
                m.invoke_fixture(
                    request, authority=Allow(), destination=destination, resolver=resolver
                ).status
                == "denied"
            )
            assert m._active == 0 and m._pools == {}
        finally:
            m._cleanup_failed = False


@pytest.mark.parametrize("late", [False, True], ids=["timely-proof", "late-proof"])
def test_clean_child_completion_must_be_observed_before_deadline(tmp_path, monkeypatch, late):
    import os
    import time

    from tls_fixtures import tls_server

    import lotuspond_runtime.invocation as m

    popen, send = m.subprocess.Popen, m._send
    grant_deadline = None
    completion_observed_ns = None
    children = []

    def record_grant(channel, value, deadline):
        nonlocal grant_deadline
        if value == {"authorized": True}:
            grant_deadline = deadline
        return send(channel, value, deadline)

    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        wait = child.wait
        first = True

        def observe_completion(*args, **kwargs):
            nonlocal first, completion_observed_ns
            if first:
                first = False
                assert grant_deadline is not None
                if late:
                    time.sleep(
                        max(0, (grant_deadline - time.monotonic_ns()) / 1_000_000_000) + 0.04
                    )
                result = wait(*args, **kwargs)
                completion_observed_ns = time.monotonic_ns()
                return result
            return wait(*args, **kwargs)

        child.wait = observe_completion
        children.append(child)
        return child

    monkeypatch.setattr(m, "_send", record_grant)
    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n") as (
        origin,
        ca,
        requests,
    ):
        request, destination, resolver = invocation_setup(tmp_path, origin, ca, timeout=800)
        # The actual fixed child receives the grant and refuses this header before sending.
        (tmp_path / "secret-v1").write_bytes(b"invalid-header-value\n")
        authority = Allow()
        start = time.monotonic()
        result = m.invoke_fixture(
            request, authority=authority, destination=destination, resolver=resolver
        )
        assert grant_deadline is not None and authority.calls >= 2
        assert (completion_observed_ns > grant_deadline) is late
        assert requests == []
        assert children[0].returncode == 0
        expected = (
            m.FixtureResult("uncertain", len(request.input_bytes), 0)
            if late
            else m.FixtureResult("denied", 0, 0)
        )
        assert result == expected
        assert time.monotonic() - start < 1.5
        with pytest.raises(ProcessLookupError):
            os.killpg(children[0].pid, 0)
        assert not m._cleanup_failed
        assert m._active == 0 and m._pools == {}
