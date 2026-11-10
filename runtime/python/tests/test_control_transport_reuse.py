"""The control transport keeps one verified connection, and never lets it carry trust."""

import shutil
import time

import pytest
from pydantic import SecretStr
from tls_fixtures import certificates, kept_server

from lotuspond_runtime import control_transport as module
from lotuspond_runtime.errors import FleetError

TOKEN = SecretStr("lpr_" + "A" * 43)
REVOKED = SecretStr("lpr_" + "B" * 42 + "A")
SESSION = SecretStr("lps_" + "C" * 42 + "A")


def answer(body=b"{}", status=b"200 OK", extra=b"", version=b"HTTP/1.1"):
    def reply(request):
        if b"Bearer " + REVOKED.get_secret_value().encode() in request:
            return b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n"
        length = str(len(body)).encode()
        head = version + b" " + status + b"\r\n" + extra + b"Content-Length: " + length
        return head + b"\r\n\r\n" + body

    return reply


@pytest.fixture
def spawned(monkeypatch):
    """Every owned DNS helper the transport starts."""
    helpers = []
    original = module.subprocess.Popen

    def record(*args, **kwargs):
        helpers.append(original(*args, **kwargs))
        return helpers[-1]

    monkeypatch.setattr(module.subprocess, "Popen", record)
    return helpers


def settle():
    """Long enough for a peer's close to reach this side's socket."""
    time.sleep(0.2)


def test_requests_share_one_connection_and_one_resolution(tmp_path, spawned):
    with kept_server(tmp_path, answer()) as server:
        client = module.ControlTransport(server.origin, server.ca)
        for _ in range(3):
            assert client.request("GET", "/self", None, TOKEN) == b"{}"
            assert client.request("POST", "/heartbeat", b"{}", TOKEN, SESSION) == b"{}"
        client.close()
    assert server.connections == 1 and len(spawned) == 1
    assert [number for number, _ in server.requests] == [1] * 6
    for _, request in server.requests:  # each request still carries its own proof
        assert b"Connection: close" not in request
        assert request.count(b"Authorization: Bearer " + TOKEN.get_secret_value().encode()) == 1
    heartbeats = [request for _, request in server.requests if b"/heartbeat" in request]
    assert all(b"X-Lotuspond-Runtime-Session: " in request for request in heartbeats)


def test_revocation_applies_on_the_kept_connection(tmp_path):
    with kept_server(tmp_path, answer()) as server:
        client = module.ControlTransport(server.origin, server.ca)
        assert client.request("POST", "/heartbeat", b"{}", TOKEN, SESSION) == b"{}"
        with pytest.raises(FleetError) as error:
            client.request("POST", "/heartbeat", b"{}", REVOKED, SESSION)
        assert error.value.code == "unauthenticated"
        # The refused request travelled on the connection the accepted one used.
        assert [number for number, _ in server.requests] == [1, 1]
        # A refusal ends the connection; the next request is authorized afresh.
        with pytest.raises(FleetError, match="unauthenticated"):
            client.request("POST", "/heartbeat", b"{}", REVOKED, SESSION)
        assert client.request("POST", "/heartbeat", b"{}", TOKEN, SESSION) == b"{}"
        client.close()
    assert [number for number, _ in server.requests] == [1, 1, 2, 3]


def test_server_restart_reconnects_with_fresh_resolution(tmp_path, spawned):
    with kept_server(tmp_path, answer()) as server:
        client = module.ControlTransport(server.origin, server.ca)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        server.drop()
        settle()
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        client.close()
    assert server.connections == 2 and len(spawned) == 2


def test_certificate_rotation_needs_the_rotated_ca(tmp_path):
    with kept_server(tmp_path, answer()) as server:
        ca = tmp_path / "client-ca.pem"
        shutil.copyfile(server.ca, ca)
        client = module.ControlTransport(server.origin, ca)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        rotated = server.rotate()
        settle()
        # The old CA cannot verify the rotated server: no request is sent.
        with pytest.raises(FleetError, match="transport_failed"):
            client.request("GET", "/self", None, TOKEN)
        shutil.copyfile(rotated, ca)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        client.close()
    assert [number for number, _ in server.requests] == [1, 3]


def test_changed_ca_file_ends_a_kept_connection(tmp_path):
    with kept_server(tmp_path, answer()) as server:
        ca = tmp_path / "client-ca.pem"
        shutil.copyfile(server.ca, ca)
        client = module.ControlTransport(server.origin, ca)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        other = tmp_path / "other"
        other.mkdir()
        unrelated, _ = certificates(other)
        shutil.copyfile(unrelated, ca)
        # The live connection was verified, but against trust that is no longer
        # configured: it is not reused, and the new handshake fails.
        with pytest.raises(FleetError, match="transport_failed"):
            client.request("GET", "/self", None, TOKEN)
        ca.unlink()
        with pytest.raises(FleetError, match="transport_failed"):
            client.request("GET", "/self", None, TOKEN)
        shutil.copyfile(server.ca, ca)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        client.close()
    # Connections 2 and 3 ended before a request: a failed handshake, a missing CA.
    assert [number for number, _ in server.requests] == [1, 4]


def test_idle_and_age_bounds_end_reuse(tmp_path, monkeypatch, spawned):
    with kept_server(tmp_path, answer()) as server:
        client = module.ControlTransport(server.origin, server.ca)
        monkeypatch.setattr(module, "KEEPALIVE_IDLE_SECONDS", 0.05)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        time.sleep(0.1)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        monkeypatch.setattr(module, "KEEPALIVE_IDLE_SECONDS", 60)
        monkeypatch.setattr(module, "CONNECTION_MAX_SECONDS", 0.15)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        time.sleep(0.2)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        client.close()
    assert [number for number, _ in server.requests] == [1, 2, 2, 3]
    assert len(spawned) == 3


def test_expired_peer_certificate_ends_reuse(tmp_path, monkeypatch):
    with kept_server(tmp_path, answer()) as server:
        client = module.ControlTransport(server.origin, server.ca)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        later = time.time() + 3 * 86400  # past the synthetic certificate's notAfter
        monkeypatch.setattr(module.time, "time", lambda: later)
        # Reuse is refused; the fresh handshake verifies against the real clock.
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        client.close()
    assert [number for number, _ in server.requests] == [1, 2]


@pytest.mark.parametrize(
    "reply",
    [
        answer(extra=b"Connection: close\r\n"),
        answer(extra=b"Connection: keep-alive, Close\r\n"),
        answer(version=b"HTTP/1.0"),
        answer(status=b"503 Unavailable"),
    ],
)
def test_responses_that_end_the_connection(tmp_path, reply):
    with kept_server(tmp_path, reply) as server:
        client = module.ControlTransport(server.origin, server.ca)
        for _ in range(2):
            try:
                client.request("GET", "/self", None, TOKEN)
            except FleetError as error:
                assert error.code == "authority_unavailable"
        client.close()
    assert [number for number, _ in server.requests] == [1, 2]


def test_bytes_past_or_between_responses_are_never_read_as_one(tmp_path):
    with kept_server(tmp_path, answer(body=b"{}")) as server:
        client = module.ControlTransport(server.origin, server.ca)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        server.push(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nfake")
        settle()
        # Unsolicited bytes end the connection; the next answer is the server's own.
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        client.close()
    assert [number for number, _ in server.requests] == [1, 2]
    (tmp_path / "extra").mkdir()
    past = lambda request: b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}x"  # noqa: E731
    with kept_server(tmp_path / "extra", past) as server:
        client = module.ControlTransport(server.origin, server.ca)
        with pytest.raises(FleetError, match="transport_failed"):
            client.request("GET", "/self", None, TOKEN)
        client.close()


def test_close_ends_the_kept_connection(tmp_path):
    with kept_server(tmp_path, answer()) as server:
        client = module.ControlTransport(server.origin, server.ca)
        assert client.request("GET", "/self", None, TOKEN) == b"{}"
        client.close()
        assert client._socket is None
        with pytest.raises(FleetError, match="transport_failed"):
            client.request("GET", "/self", None, TOKEN)
    assert server.connections == 1
