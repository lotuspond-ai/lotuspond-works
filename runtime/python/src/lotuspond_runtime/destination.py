"""Direct, single-resolution pinned TLS egress. No ambient proxy or redirects."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import selectors
import signal
import socket
import ssl
import stat
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import FleetError
from .protocol import DestinationRule

_DNS = """import json,socket,sys
rows=socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)
print(json.dumps([[r[0],r[4]] for r in rows[:65]]))
"""


def remaining(deadline_ns: int) -> float:
    value = (deadline_ns - time.monotonic_ns()) / 1_000_000_000
    if value <= 0:
        raise FleetError("transport_failed")
    return value


def normalize_path(path: str, prefix: str) -> str:
    # An intentionally narrow resource grammar eliminates all decoding ambiguity.
    if (
        type(path) is not str
        or not path.isascii()
        or len(path) > 1024
        or not path.startswith("/")
        or any(c in path for c in "%\\?#")
        or any(ord(c) <= 32 or ord(c) >= 127 for c in path)
        or "//" in path
        or any(s in (".", "..") for s in path.split("/"))
        or not (prefix == "/" or path == prefix or path.startswith(prefix.rstrip("/") + "/"))
    ):
        raise FleetError("destination_denied")
    return path


@dataclass(frozen=True)
class EgressPolicy:
    allowed_private_cidrs: tuple[str, ...]
    denied_cidrs: tuple[str, ...]
    denied_hosts: tuple[str, ...]

    def __post_init__(self):
        try:
            for values in (self.allowed_private_cidrs, self.denied_cidrs, self.denied_hosts):
                if type(values) is not tuple or len(values) > 256:
                    raise ValueError
            for value in self.allowed_private_cidrs + self.denied_cidrs:
                if type(value) is not str:
                    raise ValueError
                ipaddress.ip_network(value, strict=True)
            for host in self.denied_hosts:
                DestinationRule(host=host, port=443, path_prefix="/")
        except (ValueError, TypeError):
            raise FleetError("destination_denied") from None

    def check_host(self, host: str):
        if host in self.denied_hosts:
            raise FleetError("destination_denied")

    def check_address(self, value: str, *, _test_loopback=False):
        try:
            address = ipaddress.ip_address(value)
            denied = any(address in ipaddress.ip_network(c) for c in self.denied_cidrs)
            allowed = any(address in ipaddress.ip_network(c) for c in self.allowed_private_cidrs)
            if (
                denied
                or "%" in value
                or getattr(address, "ipv4_mapped", None) is not None
                or address.is_link_local
                or address.is_unspecified
                or address.is_multicast
                or (
                    address.is_reserved and not (address.is_loopback and _test_loopback and allowed)
                )
                or (address.is_loopback and not (_test_loopback and allowed))
                or (
                    not address.is_global
                    and not address.is_loopback
                    and not (
                        allowed
                        and (
                            address in ipaddress.ip_network("10.0.0.0/8")
                            or address in ipaddress.ip_network("172.16.0.0/12")
                            or address in ipaddress.ip_network("192.168.0.0/16")
                            or address in ipaddress.ip_network("fc00::/7")
                        )
                    )
                )
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise FleetError("destination_denied") from None


class DestinationTransport:
    def __init__(self, policy: EgressPolicy, ca_file: Path):
        if type(policy) is not EgressPolicy or not isinstance(ca_file, Path):
            raise FleetError("destination_denied")
        EgressPolicy(policy.allowed_private_cidrs, policy.denied_cidrs, policy.denied_hosts)
        self.policy, self.ca_file = policy, ca_file
        self._test_loopback = False
        self._worker_process = False
        self._closed = False

    @classmethod
    def _for_test(cls, policy: EgressPolicy, ca_file: Path):
        transport = cls(policy, ca_file)
        transport._test_loopback = True
        return transport

    def _resolve(self, host, port, deadline):
        helper = subprocess.Popen(
            [sys.executable, "-I", "-c", _DNS, host, str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            close_fds=True,
            start_new_session=not self._worker_process,
        )
        data = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(helper.stdout, selectors.EVENT_READ)
                while True:
                    if not selector.select(remaining(deadline)):
                        raise FleetError("transport_failed")
                    chunk = os.read(helper.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 16384:
                        raise FleetError("transport_failed")
            helper.wait(timeout=remaining(deadline))
            if helper.returncode:
                raise FleetError("transport_failed")
            rows = json.loads(data)
            if not rows or len(rows) > 64:
                raise FleetError("transport_failed")
            return rows
        finally:
            try:
                if self._worker_process:
                    # This helper remains inside the fixed child's parent-owned group.
                    helper.kill()
                else:
                    try:
                        os.killpg(helper.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                helper.wait(timeout=0.5)
                if not self._worker_process:
                    try:
                        os.killpg(helper.pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise FleetError("transport_failed")
            except (OSError, subprocess.SubprocessError, FleetError):
                self._closed = True
                raise FleetError("transport_failed") from None
            finally:
                helper.stdout.close()

    def _context(self, deadline):
        fd = os.open(self.ca_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise FleetError("transport_failed")
            data = bytearray()
            while True:
                remaining(deadline)
                chunk = os.read(fd, min(8192, 1048577 - len(data)))
                data.extend(chunk)
                if len(data) > 1048576:
                    raise FleetError("transport_failed")
                if not chunk:
                    break
            context = ssl.create_default_context(cadata=data.decode("ascii"))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            remaining(deadline)
            return context
        finally:
            os.close(fd)

    def connect(self, rule: DestinationRule, path: str, deadline_ns: int) -> ConnectedDestination:
        stream = None
        try:
            if self._closed:
                raise FleetError("transport_failed")
            rule = DestinationRule.model_validate(rule)
            path = normalize_path(path, rule.path_prefix)
            self.policy.check_host(rule.host)
            remaining(deadline_ns)
            rows = self._resolve(rule.host, rule.port, deadline_ns)
            if not rows or len(rows) > 64:
                raise FleetError("destination_denied")
            for family, address in rows:
                if family not in (socket.AF_INET, socket.AF_INET6) or address[1] != rule.port:
                    raise FleetError("destination_denied")
                self.policy.check_address(address[0], _test_loopback=self._test_loopback)
            family, address = next((r for r in rows if r[0] == socket.AF_INET), rows[0])
            context = self._context(deadline_ns)
            stream = socket.socket(family, socket.SOCK_STREAM)
            stream.settimeout(remaining(deadline_ns))
            stream.connect(tuple(address))
            if ipaddress.ip_address(stream.getpeername()[0]) != ipaddress.ip_address(address[0]):
                raise FleetError("destination_denied")
            stream.settimeout(remaining(deadline_ns))
            stream = context.wrap_socket(stream, server_hostname=rule.host)
            return ConnectedDestination(stream, rule, path, deadline_ns)
        except (OSError, ValueError, TypeError, subprocess.SubprocessError, UnicodeError):
            if stream is not None:
                stream.close()
            raise FleetError("transport_failed") from None
        except BaseException:
            if stream is not None:
                stream.close()
            raise


class ConnectedDestination:
    def __init__(self, stream, rule, path, deadline):
        self._socket, self._rule, self._path, self._deadline = stream, rule, path, deadline
        self.peer_ip, self.server_hostname = stream.getpeername()[0], rule.host
        self.sent = self._used = False

    def send(
        self,
        method: Literal["GET", "POST"],
        body: bytes,
        headers: Mapping[str, str],
        max_response_bytes: int,
    ) -> bytes:
        try:
            if (
                self._used
                or method not in ("GET", "POST")
                or type(body) is not bytes
                or len(body) > 65536
                or type(max_response_bytes) is not int
                or not 1 <= max_response_bytes <= 1048576
            ):
                raise FleetError("invalid_request")
            self._used = True
            fields = []
            # Only fixed connector credential headers are supported: the fixture's, or a
            # bearer token for the https-json connector. No origin/framing override.
            for key, value in headers.items():
                if (
                    key not in ("X-Lotuspond-Fixture-Secret", "Authorization")
                    or type(value) is not str
                    or len(value) > 65536
                    or not re.fullmatch(
                        r"Bearer [\x21-\x7e]+" if key == "Authorization" else r"[\x21-\x7e]*",
                        value,
                    )
                ):
                    raise FleetError("invalid_request")
                fields.append(f"{key}: {value}")
            host = f"[{self._rule.host}]" if ":" in self._rule.host else self._rule.host
            wire = (
                "\r\n".join(
                    [
                        f"{method} {self._path} HTTP/1.1",
                        f"Host: {host}:{self._rule.port}",
                        "Connection: close",
                        "Accept-Encoding: identity",
                        "Content-Type: application/octet-stream",
                        f"Content-Length: {len(body)}",
                        *fields,
                    ]
                )
                + "\r\n\r\n"
            ).encode("ascii") + body
            self._socket.settimeout(remaining(self._deadline))
            self.sent = True  # Even a failed send may have emitted a prefix.
            self._socket.sendall(wire)
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                self._socket.settimeout(remaining(self._deadline))
                chunk = self._socket.recv(1)
                if not chunk or len(header) >= 16384:
                    raise FleetError("transport_failed")
                header.extend(chunk)
            lines = bytes(header).decode("ascii").split("\r\n")
            if not re.fullmatch(r"HTTP/1\.[01] 2[0-9][0-9](?: [\x20-\x7e]*)?", lines[0]):
                raise FleetError("transport_failed")
            values = {}
            for line in lines[1:-2]:
                key, sep, value = line.partition(":")
                key = key.lower()
                if not sep or key in values or not re.fullmatch(r"[a-z0-9-]+", key):
                    raise FleetError("transport_failed")
                values[key] = value.strip()
            length = values.get("content-length", "")
            if (
                "transfer-encoding" in values
                or values.get("content-encoding", "identity") != "identity"
                or not re.fullmatch(r"0|[1-9][0-9]{0,9}", length)
                or int(length) > max_response_bytes
            ):
                raise FleetError("transport_failed")
            result = bytearray()
            while len(result) < int(length):
                self._socket.settimeout(remaining(self._deadline))
                chunk = self._socket.recv(min(8192, int(length) - len(result)))
                if not chunk:
                    raise FleetError("transport_failed")
                result.extend(chunk)
            return bytes(result)
        except (OSError, ValueError, UnicodeError):
            raise FleetError("transport_failed") from None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._socket.close()
