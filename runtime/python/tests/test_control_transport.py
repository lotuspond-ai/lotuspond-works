import importlib
import os
import time

import pytest
from pydantic import SecretStr
from tls_fixtures import certificates, tls_server

from lotuspond_runtime.errors import FleetError

TOKEN = SecretStr("lpr_" + "A" * 43)


def transport_module():
    assert importlib.util.find_spec("lotuspond_runtime.control_transport"), (
        "missing bounded control transport"
    )
    return importlib.import_module("lotuspond_runtime.control_transport")


def test_control_tls_and_bounds(tmp_path, monkeypatch):
    module = transport_module()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    for size in (65536, 65537):
        response = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(size).encode()
            + b"\r\n\r\n"
            + b"x" * size
        )
        with tls_server(tmp_path, response) as (origin, ca, requests):
            client = module.ControlTransport(origin, ca)
            if size == 65536:
                assert client.request("GET", "/self", None, TOKEN) == b"x" * size
                assert b"Authorization: Bearer " in requests[0]
            else:
                with pytest.raises(FleetError):
                    client.request("GET", "/self", None, TOKEN)
            client.close()
    for headers in (
        b"Location: https://localhost/\r\n",
        b"Content-Encoding: gzip\r\n",
        b"Content-Length: 0\r\nContent-Length: 0\r\n",
        b"X-Big: " + b"a" * 16384 + b"\r\n",
        b"X: a\r\n" * 65,
    ):
        status = b"302 Found" if headers.startswith(b"Location") else b"200 OK"
        with tls_server(tmp_path, b"HTTP/1.1 " + status + b"\r\n" + headers + b"\r\n") as (
            origin,
            ca,
            _,
        ):
            with pytest.raises(FleetError):
                module.ControlTransport(origin, ca).request("GET", "/self", None, TOKEN)


def test_ca_hostname_origin_and_input_refusal(tmp_path):
    module = transport_module()
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}") as (origin, ca, _):
        other = tmp_path / "other"
        other.mkdir()
        wrong, _ = certificates(other)
        for url, trust in ((origin, wrong), (origin.replace("localhost", "127.0.0.1"), ca)):
            with pytest.raises(FleetError):
                module.ControlTransport(url, trust).request("GET", "/self", None, TOKEN)
        for suffix in ("/path", "?a=b", "#fragment", "/"):
            with pytest.raises(FleetError):
                module.ControlTransport(origin + suffix, ca)
        for url in ("https://name:secret@localhost", "http://example.com", "http://127.0.0.1"):
            with pytest.raises(FleetError):
                module.ControlTransport(url, ca)
        t = module.ControlTransport(origin, ca)
        for route in ("https://localhost", "/self?token=bad", "/commands/poll", "/../self"):
            with pytest.raises(FleetError):
                t.request("GET", route, None, TOKEN)
        for token in (
            SecretStr("wrong"),
            SecretStr("lpr_" + "A" * 43 + "\r\nX: bad"),
            TOKEN.get_secret_value(),
        ):
            with pytest.raises(FleetError):
                t.request("GET", "/self", None, token)


def test_whole_attempt_slow_trickle(tmp_path, monkeypatch):
    module = transport_module()
    monkeypatch.setattr(module, "ATTEMPT_SECONDS", 0.3)
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}", trickle=True) as (
        origin,
        ca,
        _,
    ):
        started = time.monotonic()
        with pytest.raises(FleetError):
            module.ControlTransport(origin, ca).request("GET", "/self", None, TOKEN)
        assert time.monotonic() - started < 1.5


def test_owned_dns_process_deadline_and_secret_absence(tmp_path, monkeypatch):
    module = transport_module()
    monkeypatch.setattr(module, "ATTEMPT_SECONDS", 0.2)
    monkeypatch.setattr(module, "_DNS_PROGRAM", "import time; time.sleep(60)")
    client = module.ControlTransport("https://localhost", tmp_path / "unused")
    # TLS configuration occurs after bounded resolution; a stuck helper is killed/reaped.
    children = []
    original = module.subprocess.Popen

    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        assert TOKEN.get_secret_value() not in str(args) + str(kwargs)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", record)
    started = time.monotonic()
    with pytest.raises(FleetError):
        client.request("GET", "/self", None, TOKEN)
    assert time.monotonic() - started < 1.5
    assert len(children) == 1 and children[0].poll() is not None
    assert not os.path.exists(f"/proc/{children[0].pid}")
    client.close()


@pytest.mark.parametrize(
    "origin",
    ["https://localhost:0", "https://localhost:", "HTTPS://localhost", "https://LOCALHOST"],
)
def test_noncanonical_origin_denied(tmp_path, origin):
    with pytest.raises(FleetError):
        transport_module().ControlTransport(origin, tmp_path / "ca")


def test_ca_nonregular_file_cannot_block_attempt(tmp_path):
    transport_module()
    fifo = tmp_path / "ca-fifo"
    os.mkfifo(fifo, 0o600)
    with tls_server(tmp_path, b"") as (origin, _, _):
        started = time.monotonic()
        # A subprocess protects the test itself against a broken blocking CA loader.
        import subprocess
        import sys

        script = """
from pathlib import Path
from pydantic import SecretStr
from lotuspond_runtime.control_transport import ControlTransport
from lotuspond_runtime.errors import FleetError
import sys
try: ControlTransport(sys.argv[1], Path(sys.argv[2])).request('GET','/self',None,SecretStr('lpr_'+'A'*43))
except FleetError: raise SystemExit(3)
raise SystemExit(99)
"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", script, origin, str(fifo)], capture_output=True, timeout=1
            )
        except subprocess.TimeoutExpired:
            pytest.fail("control CA input blocked the whole attempt")
        assert result.returncode == 3
        assert time.monotonic() - started < 1.5


def test_development_http_is_loopback_at_actual_socket(tmp_path, monkeypatch):
    module = transport_module()
    monkeypatch.setattr(module, "_DNS_PROGRAM", "print('[[2,[\"192.0.2.1\",80]]]')")
    client = module.ControlTransport("http://localhost", tmp_path / "unused", mode="development")

    def forbidden_open(*args, **kwargs):
        pytest.fail("non-loopback HTTP socket was attempted")

    monkeypatch.setattr(module.socket, "socket", forbidden_open)
    with pytest.raises(FleetError):
        client.request("GET", "/self", None, TOKEN)


def test_dns_cleanup_unconfirmed_refuses_future_attempts(tmp_path, monkeypatch):
    module = transport_module()
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}") as (origin, ca, _):
        client = module.ControlTransport(origin, ca)
        original_reap = client._reap

        def reap_without_acknowledgement():
            if client._helper is not None:
                process = client._helper
                original_wait = process.wait

                def uncertain(*args, **kwargs):
                    original_wait(*args, **kwargs)
                    raise module.subprocess.TimeoutExpired("synthetic-dns", 0.5)

                monkeypatch.setattr(process, "wait", uncertain)
            return original_reap()

        monkeypatch.setattr(client, "_reap", reap_without_acknowledgement)
        with pytest.raises(FleetError):
            client.request("GET", "/self", None, TOKEN)
        assert client._closed
        with pytest.raises(FleetError):
            client.request("GET", "/self", None, TOKEN)
        client.close()


def test_owned_dns_descendant_is_killed_and_uncertainty_fences(tmp_path, monkeypatch):
    import signal
    from pathlib import Path

    module = transport_module()
    monkeypatch.setattr(module, "ATTEMPT_SECONDS", 0.25)
    pid_file = tmp_path / "synthetic-descendant-pid"
    program = (
        "import subprocess,sys; from pathlib import Path; "
        'p=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"]); '
        f"Path({str(pid_file)!r}).write_text(str(p.pid)); "
        "print('[[2,[\"127.0.0.1\",443]]]',flush=True)"
    )
    monkeypatch.setattr(module, "_DNS_PROGRAM", program)
    client = module.ControlTransport("https://localhost", tmp_path / "unused")
    helpers = []
    original = module.subprocess.Popen

    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        helpers.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", record)
    try:
        with pytest.raises(FleetError):
            client.request("GET", "/self", None, TOKEN)
        child = Path("/proc") / pid_file.read_text() / "stat"
        deadline = time.monotonic() + 1
        while (
            child.exists() and child.read_text().split()[2] != "Z" and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert not child.exists() or child.read_text().split()[2] == "Z", (
            "owned DNS descendant survived"
        )
        assert helpers[0].poll() is not None
    finally:
        try:
            os.killpg(helpers[0].pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            client.close()
        except FleetError:
            pass


def test_dns_tls_and_body_share_one_deadline(tmp_path, monkeypatch):
    module = transport_module()
    monkeypatch.setattr(module, "ATTEMPT_SECONDS", 0.35)
    monkeypatch.setattr(
        module, "_DNS_PROGRAM", "import time; time.sleep(.15)\n" + module._DNS_PROGRAM
    )
    with tls_server(
        tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 40\r\n\r\n" + b"x" * 40, body_trickle=True
    ) as (origin, ca, _):
        started = time.monotonic()
        with pytest.raises(FleetError):
            module.ControlTransport(origin, ca).request("GET", "/self", None, TOKEN)
        assert time.monotonic() - started < 0.65


def test_request_bound_and_error_body_never_exposed(tmp_path):
    module = transport_module()
    sentinel = b"synthetic-private-provider-body"
    with tls_server(
        tmp_path,
        b"HTTP/1.1 503 Unavailable\r\nContent-Length: "
        + str(len(sentinel)).encode()
        + b"\r\n\r\n"
        + sentinel,
    ) as (origin, ca, requests):
        client = module.ControlTransport(origin, ca)
        with pytest.raises(FleetError) as error:
            client.request("POST", "/enroll", b"x" * 65537, SecretStr("lpe_" + "A" * 43))
        assert error.value.code == "input_limit"
        assert not requests
        with pytest.raises(FleetError) as error:
            client.request("GET", "/self", None, TOKEN)
        assert str(error.value) == "authority_unavailable"
        assert sentinel.decode() not in repr(error.value)
