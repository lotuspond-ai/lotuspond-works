#!/usr/bin/env python3
"""Deterministic protocol fixture for the reviewed real-xterm agent loop.

This is an integration-test adapter, not an AI model or a benchmark result.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_REQUEST_BYTES = 4 * 1024 * 1024
EXPECTED_TEXT = "Hello from Lotuspond"


class Handler(BaseHTTPRequestHandler):
    server_version = "LotuspondDeterministicAgent/1"

    def do_POST(self) -> None:
        try:
            action = self._action()
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            self.send_error(400)
            return
        body = json.dumps(action, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _action(self) -> dict[str, str]:
        if self.path != "/v1/action":
            raise ValueError("unexpected path")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("missing content length")
        length = int(raw_length)
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("invalid request length")
        value: Any = json.loads(self.rfile.read(length))
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != "lotuspond.gym-agent-request.v1"
            or value.get("task")
            != {
                "environment_id": "example.basic_x11@0.1",
                "task_id": "hello@1",
                "prompt": "Type a greeting in the terminal window.",
            }
        ):
            raise ValueError("unexpected task")
        observation = value["observation"]
        encoded = observation["screen"]["base64"]
        screen = base64.b64decode(encoded, validate=True)
        if (
            len(screen) != observation["screen"]["bytes"]
            or hashlib.sha256(screen).hexdigest()
            != observation["screen"]["sha256"]
            or "Gym Anything Hello" not in observation["window_tree"]
        ):
            raise ValueError("invalid observation")
        step = value["step"]
        if step == 0:
            return {"type": "type_text", "text": EXPECTED_TEXT}
        if step == 1:
            return {"type": "key", "key": "ENTER"}
        return {"type": "finish"}

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
