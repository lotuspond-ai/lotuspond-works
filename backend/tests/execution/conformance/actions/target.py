"""The synthetic target (E05 Task 12): one owned test process serving loopback HTTPS.

The runtime's synthetic-target providers (`lotuspond_runtime.action_fixture`) reach it
through `DestinationTransport`. Its protocol is fixed:

- `POST <prefix>/write` with `{"operation", "idempotency_key", "target", "input"}`
  answers `{"status": "applied" | "duplicate", "record": {...}}`;
- `POST <prefix>/read` with `{"operation", "target"}` answers `{"present": bool}`.

Its store lives in its own directory, outside every database cluster and backup: a
restore never rolls the target back. Every applied write is one line of
`writes.jsonl`; tests count writes only from that log, never from receipts.

`faults.json`, read on every request, holds one-shot faults for the next write:
`lose_response` applies it and closes without answering, `lose_request` closes
without applying it, `slow` applies it and answers 4 s later, and `hang_after_apply` applies it and never answers (the
runtime is then SIGKILLed, spike checks 20-22). `reads.json` overrides the answer
reads of one record get (`Store.read_override`); `latency.json` delays every request
(tier 3, `Store.delay`). Test-only: never product code.

    python -I target.py <directory> --cert CERT --key KEY
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOCK = threading.Lock()
MAX_BODY = 65536


def _private_append(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        self.log = directory / "writes.jsonl"
        self.calls = directory / "calls.jsonl"
        self.faults = directory / "faults.json"

    def applied(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def delay(self) -> None:
        """`latency.json` (tier 3): each request waits exp(N(mu, sigma)) s, capped."""
        path = self.directory / "latency.json"
        if path.exists():
            import random

            spec = json.loads(path.read_text())
            time.sleep(min(random.lognormvariate(spec["mu"], spec["sigma"]), spec["cap_s"]))

    def read_override(self, target) -> dict | None:
        """`reads.json` maps a record ID to the answer its reads get instead of the
        store's (an undecidable or contradicting read), until it is removed."""
        path = self.directory / "reads.json"
        if not path.exists():
            return None
        return json.loads(path.read_text()).get(str(target.get("record_id")))

    def take_fault(self) -> str | None:
        """The next one-shot write fault, consumed under the store lock."""
        if not self.faults.exists():
            return None
        pending = json.loads(self.faults.read_text())
        if not pending:
            return None
        fault, rest = pending[0], pending[1:]
        _write_json(self.faults, rest)
        return fault


class Handler(BaseHTTPRequestHandler):
    server_version = "synthetic-target"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler's name
        pass

    def setup(self):
        # The TLS handshake runs here, in this connection's own thread: a slow or
        # abandoned one never holds up the accepting thread (and the next client).
        self.request.settimeout(30)
        self.request.do_handshake()
        super().setup()

    def _answer(self, status, value):
        content = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _drop(self):
        """Close the connection with no answer at all."""
        self.close_connection = True
        try:
            self.connection.shutdown(2)
        except OSError:
            pass

    def do_POST(self):  # noqa: N802 - the http.server interface
        store: Store = self.server.store
        length = int(self.headers.get("content-length", "0"))
        if not 0 < length <= MAX_BODY:
            return self._answer(413, {"error": "too_large"})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._answer(400, {"error": "invalid"})
        route = self.path.rsplit("/", 1)[-1]
        store.delay()
        _private_append(
            store.calls, json.dumps({"route": route, "at": time.time()}, sort_keys=True)
        )
        if route == "read":
            target = body.get("target") or {}
            override = store.read_override(target)
            if override is not None:
                return self._answer(200, override)
            with LOCK:
                present = any(w["target"] == target for w in store.applied())
            return self._answer(200, {"present": present})
        if route != "write":
            return self._answer(404, {"error": "not_found"})
        key, target = body.get("idempotency_key"), body.get("target")
        with LOCK:
            fault = store.take_fault()
            if fault == "lose_request":
                return self._drop()
            previous = next((w for w in store.applied() if w["idempotency_key"] == key), None)
            if previous is None:
                record = {
                    "idempotency_key": key,
                    "operation": body.get("operation"),
                    "target": target,
                    "input": body.get("input"),
                    "at": time.time(),
                }
                _private_append(store.log, json.dumps(record, sort_keys=True))
                status = "applied"
            else:
                record, status = previous, "duplicate"
        if fault == "lose_response":
            return self._drop()
        if fault == "slow":
            time.sleep(4)  # applied; the answer comes late (a write in flight)
        if fault == "hang_after_apply":
            time.sleep(3600)  # the runtime is killed meanwhile
            return self._drop()
        return self._answer(200, {"status": status, "record": {"record_id": target["record_id"]}})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    directory = Path(args.directory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.store = Store(directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    server.socket = context.wrap_socket(
        server.socket, server_side=True, do_handshake_on_connect=False
    )
    _write_json(directory / "target.json", {"port": server.server_address[1], "pid": os.getpid()})
    server.serve_forever()


if __name__ == "__main__":
    main()
