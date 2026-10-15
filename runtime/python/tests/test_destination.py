"""Actual socket observations for connector egress (synthetic local trust only)."""

import importlib
import socket
import time

import pytest
from tls_fixtures import tls_server

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import DestinationRule


def api():
    return importlib.import_module("lotuspond_runtime.destination")


@pytest.mark.parametrize(
    "address,allowed",
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("::1", False),
        ("::ffff:8.8.8.8", False),
        ("169.254.169.254", False),
        ("fe80::1", False),
        ("0.0.0.0", False),
        ("::", False),
        ("224.0.0.1", False),
        ("ff02::1", False),
        ("240.0.0.1", False),
        ("192.0.2.1", False),
        ("10.2.3.4", False),
        ("10.1.2.3", True),
        ("10.1.9.9", False),
        ("fc00::1", False),
    ],
)
def test_destination_matrix(address, allowed):
    m = api()
    policy = m.EgressPolicy(("10.1.0.0/16",), ("10.1.9.0/24",), ("control.example",))
    if allowed:
        policy.check_address(address)
    else:
        with pytest.raises(FleetError, match="destination_denied"):
            policy.check_address(address)
    with pytest.raises(FleetError):
        policy.check_host("control.example")


@pytest.mark.parametrize(
    "path",
    [
        "//evil/x",
        "/allowed/../x",
        "/allowed\\x",
        "/allowed/%2e",
        "/allowed/%2f",
        "/allowed/%5c",
        "/allowed/%252f",
        "/allowed/x#f",
        "/allowed/x?q",
        "https://u:p@evil:443/allowed",
        "/allowed-other",
        "/allowed//x",
        "/allowed/.",
        "/allowed/%41",
        "/allowed/\r\nx",
    ],
)
def test_path_and_redirect_refusal(path):
    m = api()
    with pytest.raises(FleetError, match="destination_denied"):
        m.normalize_path(path, "/allowed")


def test_pinned_socket_survives_dns_rebinding(tmp_path, monkeypatch):
    m = api()
    calls = []

    def resolve(self, host, port, deadline):
        calls.append(host)
        return [(socket.AF_INET, ("127.0.0.1" if len(calls) == 1 else "169.254.169.254", port))]

    monkeypatch.setattr(m.DestinationTransport, "_resolve", resolve)
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok") as (
        origin,
        ca,
        requests,
    ):
        port = int(origin.rsplit(":", 1)[1])
        transport = m.DestinationTransport._for_test(
            m.EgressPolicy(("127.0.0.0/8", "::1/128"), (), ()), ca
        )
        with transport.connect(
            DestinationRule(host="localhost", port=port, path_prefix="/allowed"),
            "/allowed/x",
            time.monotonic_ns() + 2_000_000_000,
        ) as connected:
            assert connected.peer_ip == "127.0.0.1"
            assert connected.server_hostname == "localhost"
            assert connected.send("POST", b"payload", {}, 20) == b"ok"
        assert len(calls) == 1
        assert len(requests) == 1


def test_mixed_dns_denies_before_socket(tmp_path, monkeypatch):
    m = api()
    t = m.DestinationTransport(m.EgressPolicy((), (), ()), tmp_path / "missing")
    monkeypatch.setattr(
        t,
        "_resolve",
        lambda *args: [(socket.AF_INET, ("8.8.8.8", 443)), (socket.AF_INET, ("127.0.0.1", 443))],
    )
    monkeypatch.setattr(socket, "socket", lambda *args: pytest.fail("socket opened"))
    with pytest.raises(FleetError, match="destination_denied"):
        t.connect(
            DestinationRule(host="example.com", port=443, path_prefix="/"),
            "/",
            time.monotonic_ns() + 1_000_000_000,
        )


def test_redirect_never_followed(tmp_path):
    m = api()
    with tls_server(
        tmp_path, b"HTTP/1.1 302 Found\r\nLocation: https://other/\r\nContent-Length: 0\r\n\r\n"
    ) as (origin, ca, requests):
        t = m.DestinationTransport._for_test(m.EgressPolicy(("127.0.0.0/8", "::1/128"), (), ()), ca)
        rule = DestinationRule(
            host="localhost", port=int(origin.rsplit(":", 1)[1]), path_prefix="/"
        )
        with t.connect(rule, "/", time.monotonic_ns() + 2_000_000_000) as connected:
            with pytest.raises(FleetError):
                connected.send("GET", b"", {}, 30)
        assert len(requests) == 1


def test_real_sni_hostname_verification(tmp_path, monkeypatch):
    import ssl

    m = api()
    seen = []
    original = ssl.SSLContext.load_cert_chain

    def load(context, *args, **kwargs):
        original(context, *args, **kwargs)
        context.set_servername_callback(lambda stream, name, ctx: seen.append(name))

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", load)
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n") as (
        origin,
        ca,
        requests,
    ):
        t = m.DestinationTransport._for_test(m.EgressPolicy(("127.0.0.0/8", "::1/128"), (), ()), ca)
        rule = DestinationRule(
            host="localhost", port=int(origin.rsplit(":", 1)[1]), path_prefix="/allowed/"
        )
        with t.connect(rule, "/allowed/x", time.monotonic_ns() + 2_000_000_000) as stream:
            assert stream.send("GET", b"", {}, 1) == b""
        assert seen == ["localhost"]
        assert requests[0].startswith(b"GET /allowed/x HTTP/1.1")


@pytest.mark.parametrize("path", ["/allowed", "/allowed/", "/allowed/a"])
def test_segment_prefix_positive(path):
    assert api().normalize_path(path, "/allowed") == path


@pytest.mark.parametrize(
    "header",
    [
        {"Host": "evil"},
        {"Authorization": "Bearer secret"},
        {"Content-Length": "0"},
        {"X-Lotuspond-Fixture-Secret": "value\r\nHost: evil"},
    ],
)
def test_raw_header_origin_override_refused(tmp_path, header):
    m = api()
    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n") as (
        origin,
        ca,
        requests,
    ):
        t = m.DestinationTransport._for_test(m.EgressPolicy(("127.0.0.0/8", "::1/128"), (), ()), ca)
        rule = DestinationRule(
            host="localhost", port=int(origin.rsplit(":", 1)[1]), path_prefix="/"
        )
        with t.connect(rule, "/", time.monotonic_ns() + 2_000_000_000) as stream:
            with pytest.raises(FleetError):
                stream.send("POST", b"", header, 1)
        assert requests == []


def test_deadline_includes_dns_helper_and_reaps(tmp_path, monkeypatch):
    import os

    import lotuspond_runtime.destination as m

    monkeypatch.setattr(m, "_DNS", "import time;time.sleep(10)")
    children = []
    popen = m.subprocess.Popen

    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    t = m.DestinationTransport(m.EgressPolicy((), (), ()), tmp_path / "missing")
    start = time.monotonic()
    with pytest.raises(FleetError):
        t.connect(
            DestinationRule(host="example.com", port=443, path_prefix="/"),
            "/",
            time.monotonic_ns() + 50_000_000,
        )
    assert time.monotonic() - start < 1
    assert children[0].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(children[0].pid, 0)


def test_reused_tls_fixture_handles_clean_eof_before_http(tmp_path):
    import ssl

    with tls_server(tmp_path, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n") as (
        origin,
        ca,
        requests,
    ):
        context = ssl.create_default_context(cafile=ca)
        with socket.create_connection(("127.0.0.1", int(origin.rsplit(":", 1)[1]))) as raw:
            with context.wrap_socket(raw, server_hostname="localhost") as stream:
                stream.settimeout(0.05)
                try:
                    stream.unwrap()
                except (TimeoutError, ssl.SSLError):
                    pass
        assert requests == []


def test_worker_dns_stays_in_parent_owned_group(tmp_path, monkeypatch):
    import os

    import lotuspond_runtime.destination as m

    t = m.DestinationTransport(m.EgressPolicy((), (), ()), tmp_path / "missing")
    t._worker_process = True
    children = []
    popen = m.subprocess.Popen
    monkeypatch.setattr(m, "_DNS", 'import time;time.sleep(0.1);print("[[2,[\\"8.8.8.8\\",443]]]")')

    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        assert os.getpgid(child.pid) == os.getpgrp()
        return child

    monkeypatch.setattr(m.subprocess, "Popen", spawn)
    assert t._resolve("example.com", 443, time.monotonic_ns() + 1_000_000_000) == [
        [2, ["8.8.8.8", 443]]
    ]
    assert children[0].poll() is not None
