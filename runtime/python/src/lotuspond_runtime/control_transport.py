"""Direct verified control TLS with a single whole-attempt deadline and owned DNS."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr

from .errors import FleetError
from .protocol import validate_token

ATTEMPT_SECONDS = 10.0
_ROUTES = {
    "/enroll",
    "/self",
    "/session",
    "/heartbeat",
    "/credentials/prepare",
    "/credentials/activate",
    "/credentials/cancel",
    "/commands/poll",
}
# Activity routes (E04): all POST, all require the runtime session.
_ACTIVITY_ROUTES = {
    "/activities/poll",
    "/activities/claim",
    "/activities/renew",
    "/activities/children",
    "/activities/results",
    "/activities/decline",
}
_ACTIVITY_VALUE = re.compile(
    r"/activities/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/values"
)
# Reads of the runtime's own work (Task 12): its delivery's records, a held agent's
# declarations, a held attempt's input and pinned context.
_ACTIVITY_READ = re.compile(
    r"/activity-artifacts/(?:records|agent|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/(?:input|context))"
)
# Response bounds: a delivery's two 64 KiB documents travel JSON-escaped, and a held
# attempt's records, input and context may reach the 1 MiB artifact bound.
MAX_RESPONSE_BYTES = 65536
MAX_DELIVERY_RESPONSE_BYTES = 131072
MAX_READ_RESPONSE_BYTES = 1048576


def response_bound(route: str) -> int:
    if route == "/activities/poll":
        return MAX_DELIVERY_RESPONSE_BYTES
    if _ACTIVITY_READ.fullmatch(route) is not None:
        return MAX_READ_RESPONSE_BYTES
    return MAX_RESPONSE_BYTES


_DNS_PROGRAM = """import json, socket, sys
rows = socket.getaddrinfo(sys.argv[1], int(sys.argv[2]), type=socket.SOCK_STREAM)
print(json.dumps([(af, list(addr)) for af, kind, proto, name, addr in rows[:64]]))
"""
_STATUS = {
    400: "invalid_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "input_limit",
    429: "rate_limited",
    503: "authority_unavailable",
}


class ControlTransport:
    def __init__(
        self,
        origin: str,
        ca_file: Path,
        *,
        mode: Literal["production", "development", "test"] = "production",
    ):
        self._helper = None
        self._socket = None
        self._closed = False
        self._serial = threading.Lock()
        try:
            if type(origin) is not str or len(origin) > 2048 or not origin.isascii():
                raise ValueError
            parsed = urlsplit(origin)
            if (
                mode not in ("production", "development", "test")
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or any(c in origin for c in ("?", "#", "\\"))
                or any(ord(c) <= 32 or ord(c) == 127 for c in origin)
                or not parsed.hostname
                or parsed.hostname != parsed.hostname.lower()
                or "%" in parsed.hostname
                or parsed.hostname.endswith(".")
            ):
                raise ValueError
            host = parsed.hostname
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if len(host) > 253 or not all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x)
                    for x in host.split(".")
                ):
                    raise ValueError from None
                loopback = host == "localhost"
            else:
                loopback = address.is_loopback
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and mode != "production" and loopback
            ):
                raise ValueError
            self.host = host
            self.port = (
                parsed.port
                if parsed.port is not None
                else (443 if parsed.scheme == "https" else 80)
            )
            authority = ("[" + host + "]") if ":" in host else host
            if parsed.port is not None:
                authority += ":" + str(parsed.port)
            if parsed.netloc != authority or not origin.startswith(parsed.scheme + "://"):
                raise ValueError
            if not 1 <= self.port <= 65535:
                raise ValueError
            self._tls = parsed.scheme == "https"
            self._authority = parsed.netloc
            self._ca_file = ca_file
        except (ValueError, TypeError, AttributeError):
            raise FleetError("invalid_request") from None

    def _remaining(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FleetError("transport_failed")
        return remaining

    def _reap(self):
        helper = self._helper
        if helper is None:
            return
        try:
            # The resolver may have exited while an owned descendant still
            # holds its pipes. Kill the whole group even after leader exit.
            try:
                os.killpg(helper.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            helper.wait(timeout=0.5)
            try:
                os.killpg(helper.pid, 0)
            except ProcessLookupError:
                pass
            else:
                self._closed = True
                raise FleetError("transport_failed")
        except (OSError, subprocess.TimeoutExpired):
            self._closed = True
            raise FleetError("transport_failed") from None
        finally:
            for stream in (helper.stdin, helper.stdout, helper.stderr):
                if stream is not None:
                    stream.close()
            if helper.poll() is not None:
                self._helper = None

    def _resolve(self, deadline):
        self._helper = subprocess.Popen(
            [sys.executable, "-I", "-c", _DNS_PROGRAM, self.host, str(self.port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            close_fds=True,
            start_new_session=True,
        )
        try:
            content, _ = self._helper.communicate(timeout=self._remaining(deadline))
            if self._helper.returncode != 0 or len(content) > 16384:
                raise FleetError("transport_failed")
            rows = json.loads(content)
            if not rows or len(rows) > 64:
                raise FleetError("transport_failed")
            family, address = next((row for row in rows if row[0] == socket.AF_INET), rows[0])
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise FleetError("transport_failed")
            return family, tuple(address)
        finally:
            self._reap()

    def _context(self, deadline):
        fd = os.open(self._ca_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise FleetError("transport_failed")
            content = bytearray()
            while True:
                self._remaining(deadline)
                chunk = os.read(fd, min(8192, 1048577 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > 1048576:
                    raise FleetError("transport_failed")
            context = ssl.create_default_context(cadata=content.decode("ascii"))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            self._remaining(deadline)
            return context
        finally:
            os.close(fd)

    def request(
        self,
        method: Literal["GET", "POST"],
        route: str,
        body: bytes | None,
        token: SecretStr,
        session: SecretStr | None = None,
    ) -> bytes:
        command_claim = (
            type(route) is str
            and re.fullmatch(r"/commands/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/claim", route)
            is not None
        )
        activity = type(route) is str and (
            route in _ACTIVITY_ROUTES
            or _ACTIVITY_VALUE.fullmatch(route) is not None
            or _ACTIVITY_READ.fullmatch(route) is not None
        )
        if (
            (route not in _ROUTES and not command_claim and not activity)
            or method != ("GET" if route == "/self" else "POST")
            or (route == "/self" and body is not None)
            or (route != "/self" and type(body) is not bytes)
        ):
            raise FleetError("invalid_request")
        if body is not None and len(body) > 65536:
            raise FleetError("input_limit")
        validate_token(token, "lpe_" if route == "/enroll" else "lpr_")
        if session is not None:
            validate_token(session, "lps_")
        if (session is not None and route in ("/enroll", "/self", "/session")) or (
            session is None
            and (
                route
                in ("/heartbeat", "/credentials/prepare", "/credentials/cancel", "/commands/poll")
                or command_claim
                or activity
            )
        ):
            raise FleetError("invalid_request")
        if self._closed or not self._serial.acquire(blocking=False):
            raise FleetError("transport_failed")
        deadline = time.monotonic() + ATTEMPT_SECONDS
        try:
            family, address = self._resolve(deadline)
            if not self._tls and not ipaddress.ip_address(address[0]).is_loopback:
                raise FleetError("transport_failed")
            stream = socket.socket(family, socket.SOCK_STREAM)
            self._socket = stream
            stream.settimeout(self._remaining(deadline))
            stream.connect(address)
            if self._tls:
                context = self._context(deadline)
                stream.settimeout(self._remaining(deadline))
                stream = context.wrap_socket(stream, server_hostname=self.host)
                self._socket = stream
            fields = [
                f"{method} /api/v1/runtime{route} HTTP/1.1",
                f"Host: {self._authority}",
                "Connection: close",
                "Accept: application/json",
                "Accept-Encoding: identity",
                "Authorization: Bearer " + token.get_secret_value(),
            ]
            if session is not None:
                fields.append("X-Lotuspond-Runtime-Session: " + session.get_secret_value())
            if body is not None:
                fields += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
            request = ("\r\n".join(fields) + "\r\n\r\n").encode("ascii") + (body or b"")
            stream.settimeout(self._remaining(deadline))
            stream.sendall(request)
            data = bytearray()
            while b"\r\n\r\n" not in data:
                stream.settimeout(self._remaining(deadline))
                chunk = stream.recv(1)
                if not chunk or len(data) >= 16384:
                    raise FleetError("transport_failed")
                data.extend(chunk)
            lines = bytes(data[:-4]).split(b"\r\n")
            if len(lines) > 65 or not re.fullmatch(
                rb"HTTP/1\.[01] [0-9]{3} [\x20-\x7e]*", lines[0]
            ):
                raise FleetError("transport_failed")
            status = int(lines[0].split(b" ")[1])
            headers = {}
            for line in lines[1:]:
                key, separator, value = line.partition(b":")
                key = key.lower()
                if (
                    not separator
                    or not re.fullmatch(rb"[!#$%&\x27*+.^_`|~0-9a-z-]+", key)
                    or key in headers
                ):
                    raise FleetError("transport_failed")
                if any(c < 32 and c != 9 or c == 127 for c in value):
                    raise FleetError("transport_failed")
                headers[key] = value.strip()
            if (
                headers.get(b"content-encoding", b"identity") != b"identity"
                or b"transfer-encoding" in headers
            ):
                raise FleetError("transport_failed")
            if status != (201 if route in ("/enroll", "/session", "/credentials/prepare") else 200):
                raise FleetError(_STATUS.get(status, "transport_failed"))
            size = headers.get(b"content-length", b"")
            if not re.fullmatch(rb"0|[1-9][0-9]{0,6}", size) or int(size) > response_bound(route):
                raise FleetError("transport_failed")
            response = bytearray()
            while len(response) < int(size):
                stream.settimeout(self._remaining(deadline))
                chunk = stream.recv(min(8192, int(size) - len(response)))
                if not chunk:
                    raise FleetError("transport_failed")
                response.extend(chunk)
            self._remaining(deadline)
            return bytes(response)
        except FleetError:
            raise
        except (OSError, ValueError, TypeError, subprocess.SubprocessError, OverflowError):
            raise FleetError("transport_failed") from None
        finally:
            try:
                if self._socket is not None:
                    self._socket.close()
                    self._socket = None
                self._reap()
            finally:
                self._serial.release()

    def close(self):
        self._closed = True
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._reap()
