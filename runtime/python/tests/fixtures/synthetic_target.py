"""Test-only synthetic target: a separate process serving loopback HTTPS (E05).

`SyntheticTarget(store_dir, tls_dir)` starts this file as its own Python process,
bound to 127.0.0.1 with a per-run self-signed certificate for `localhost` (never
committed key material). It keeps owner-only per-key records and append-only logs
in `store_dir`, outside every database snapshot, so tests count what the target
actually received and applied:

- `requests.log`: one JSON line per request read (route, key, whether the fixture
  secret matched, whether the request was lost);
- `writes.log`: one JSON line per applied write.

Faults are injected through a harness-only channel, `faults.json` in the store,
read on every request: counters `lose_request` (read, then dropped unapplied),
`lose_response` (applied, then no answer), `crash_after_apply` (applied, then the
process exits) and a `delay_ms` before applying. Each counter is decremented and
persisted before it takes effect.

Protocol: `POST <prefix>/write` with `{"operation", "idempotency_key", "target",
"input"}` answers `{"status": "applied" | "duplicate", "record"}`; a repeated key
is never applied twice. `POST <prefix>/read` with `{"operation", "target"}` answers
`{"records": [...]}` for that target.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import sys
import time

_MAX = 65536


def _append(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(value, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(path, content: bytes):
    temporary = path + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)


def _take(store, name):
    """Consume one unit of a fault counter, persisted before it takes effect."""
    path = os.path.join(store, "faults.json")
    try:
        with open(path) as stream:
            faults = json.load(stream)
    except (OSError, ValueError):
        return False
    if name == "delay_ms":
        return faults.get("delay_ms", 0)
    if faults.get(name, 0) <= 0:
        return False
    faults[name] -= 1
    _replace(path, json.dumps(faults).encode())
    return True


def _read_request(stream):
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = stream.recv(4096)
        if not chunk or len(head) > 16384:
            return None
        head += chunk
    header, _, body = head.partition(b"\r\n\r\n")
    lines = header.decode("ascii").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    fields = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    length = int(fields.get("content-length", "0"))
    if length > _MAX:
        return None
    while len(body) < length:
        chunk = stream.recv(length - len(body))
        if not chunk:
            return None
        body += chunk
    return method, path, fields, body


def _respond(stream, status, value):
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    stream.sendall(
        f"HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )


def _records(store):
    return os.path.join(store, "records")


def _apply(store, request):
    key = request.get("idempotency_key")
    directory = _records(store)
    if key is not None:
        path = os.path.join(directory, hashlib.sha256(key.encode()).hexdigest() + ".json")
        if os.path.exists(path):
            with open(path) as stream:
                return "duplicate", json.load(stream)
    count = (
        sum(1 for _ in open(os.path.join(store, "writes.log")))
        if os.path.exists(os.path.join(store, "writes.log"))
        else 0
    )
    record = {
        "id": f"record-{count + 1}",
        "operation": request.get("operation"),
        "target": request.get("target"),
        "input": request.get("input"),
        "idempotency_key": key,
    }
    name = (
        hashlib.sha256(key.encode()).hexdigest() if key is not None else "anonymous-" + record["id"]
    )
    _replace(os.path.join(directory, name + ".json"), json.dumps(record).encode())
    _append(os.path.join(store, "writes.log"), record)
    return "applied", record


def _read(store, request):
    found = []
    for name in sorted(os.listdir(_records(store))):
        with open(os.path.join(_records(store), name)) as stream:
            record = json.load(stream)
        if record.get("target") == request.get("target"):
            found.append(record)
    return {"records": found}


def _secret_ok(store, fields):
    path = os.path.join(store, "secret.sha256")
    if not os.path.exists(path):
        return None
    with open(path) as stream:
        expected = stream.read().strip()
    value = fields.get("x-lotuspond-fixture-secret", "")
    return hashlib.sha256(value.encode()).hexdigest() == expected


def _handle(store, stream):
    parsed = _read_request(stream)
    if parsed is None:
        return
    method, path, fields, body = parsed
    try:
        request = json.loads(body)
    except ValueError:
        request = {}
    route = "write" if path.endswith("/write") else "read" if path.endswith("/read") else path
    secret = _secret_ok(store, fields)
    lost = route == "write" and _take(store, "lose_request")
    _append(
        os.path.join(store, "requests.log"),
        {
            "route": route,
            "key": request.get("idempotency_key") if type(request) is dict else None,
            "secret_ok": secret,
            "lost": bool(lost),
        },
    )
    if lost:
        return  # read and dropped: never applied, never answered
    if method != "POST" or type(request) is not dict or secret is False:
        _respond(stream, 403 if secret is False else 400, {"error": "refused"})
        return
    if route == "read":
        _respond(stream, 200, _read(store, request))
        return
    if route != "write":
        _respond(stream, 404, {"error": "not_found"})
        return
    delay = _take(store, "delay_ms")
    if delay:
        time.sleep(delay / 1000)
    status, record = _apply(store, request)
    if _take(store, "crash_after_apply"):
        os._exit(17)  # applied, never answered: the process is gone
    if _take(store, "lose_response"):
        return
    _respond(stream, 200, {"status": status, "record": record})


def serve(store, cert, key, port):
    os.makedirs(_records(store), mode=0o700, exist_ok=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(16)
    _replace(os.path.join(store, "port"), str(listener.getsockname()[1]).encode())
    while True:
        raw, _ = listener.accept()
        try:
            raw.settimeout(10)
            with context.wrap_socket(raw, server_side=True) as stream:
                _handle(store, stream)
        except (OSError, ValueError, ssl.SSLError):
            pass
        finally:
            raw.close()


# ------------------------------------------------------------------ the harness


class SyntheticTarget:
    """Starts, restarts and observes the target process for one test."""

    def __init__(self, store_dir, tls_dir):
        from pathlib import Path

        self.store, self.tls = Path(store_dir), Path(tls_dir)
        self.store.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.tls.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.process, self.port = None, 0

    def start(self):
        import subprocess

        from tls_fixtures import certificates

        if not (self.tls / "synthetic-ca.pem").exists():
            certificates(self.tls)
        port_file = self.store / "port"
        if port_file.exists():
            port_file.unlink()
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                __file__,
                str(self.store),
                str(self.tls / "synthetic-ca.pem"),
                str(self.tls / "synthetic-key.pem"),
                str(self.port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={},
        )
        deadline = time.monotonic() + 10
        while not port_file.exists():
            if time.monotonic() > deadline or self.process.poll() is not None:
                raise RuntimeError("synthetic target did not start")
            time.sleep(0.02)
        self.port = int(port_file.read_text())
        return self

    @property
    def ca_file(self):
        return self.tls / "synthetic-ca.pem"

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self):
        if self.alive():
            self.process.kill()
        if self.process is not None:
            self.process.wait(5)

    def restart(self):
        self.stop()
        return self.start()

    def require_secret(self, value: str):
        (self.store / "secret.sha256").write_text(hashlib.sha256(value.encode()).hexdigest())

    def fault(self, **counters):
        _replace(str(self.store / "faults.json"), json.dumps(counters).encode())

    def _lines(self, name):
        path = self.store / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def requests(self) -> list[dict]:
        return self._lines("requests.log")

    def writes(self) -> list[dict]:
        return self._lines("writes.log")

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()


if __name__ == "__main__":
    serve(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]))
