"""A local HTTPS double of the Anthropic Messages API for the reference workflow.

It answers `POST /v1/messages` from a scenario's scripted rounds (`scenarios.json`
`model_rounds`), in the Messages API's documented response shape: a round with
`tool_calls` becomes a `tool_use` reply (with an empty thinking block, as Claude Haiku
5.5 returns by default), and a round with `output` becomes an `end_turn` reply whose
text is that JSON. Which round answers is the number of assistant turns already in the
request. It records every request so a test can check what the provider sent. It is a
test double, not a recording of a live model, and it never calls one.
"""

from __future__ import annotations

import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USAGE = {"input_tokens": 1200, "output_tokens": 90}


def reply_for(rounds, request_body, model):
    index = sum(1 for m in request_body.get("messages", []) if m.get("role") == "assistant")
    step = rounds[min(index, len(rounds) - 1)]
    content = [{"type": "thinking", "thinking": "", "signature": f"double-{index}"}]
    if step.get("tool_calls"):
        for number, call in enumerate(step["tool_calls"]):
            content.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_double_{index}_{number}",
                    "name": call["name"],
                    "input": call["arguments"],
                }
            )
        stop = "tool_use"
    else:
        content.append({"type": "text", "text": json.dumps(step.get("output"))})
        stop = "end_turn"
    return {
        "id": f"msg_double_{index}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": USAGE,
    }


class ModelDouble:
    def __init__(self, cert, key, *, host="127.0.0.1", token="double-key"):
        self.rounds, self.requests, self.token = [{"output": None}], [], token
        double = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if 0 < length <= 4 * 2**20 else {}
                headers = {k.lower(): v for k, v in self.headers.items()}
                double.requests.append({"path": self.path, "headers": headers, "body": body})
                if headers.get("authorization") != f"Bearer {double.token}":
                    return self._send(401, {"type": "error", "error": {"type": "authentication_error"}})
                if self.path != "/v1/messages":
                    return self._send(404, {"type": "error", "error": {"type": "not_found_error"}})
                return self._send(200, reply_for(double.rounds, body, body.get("model", "")))

        self.server = ThreadingHTTPServer((host, 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self):
        return self.server.server_address[1]

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
