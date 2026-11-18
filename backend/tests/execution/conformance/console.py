"""The built console in front of a world's API, for a browser journey (test-only).

Serves `dist/` (from `npm run build`) on a loopback port and forwards `/api/` to the
world's API over CA-verified TLS, as the Vite dev server's proxy does: a browser write
carries the world's origin, which the API requires. Nothing here is a product server.
"""

from __future__ import annotations

import http.client
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIST = Path(__file__).resolve().parents[4] / "dist"
# Request headers the console sends that the API reads; everything else stays behind.
FORWARDED = ("content-type", "cookie", "idempotency-key", "x-lotuspond-workspace", "accept")


class Console:
    def __init__(self, api_port: int, ca: Path, origin: str):
        if not (DIST / "index.html").exists():
            raise FileNotFoundError("dist/index.html: run `npm run build` first")
        self.api_port, self.origin = api_port, origin
        self.context = ssl.create_default_context(cafile=str(ca))
        console = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output to the test's own
                pass

            def do_GET(self):
                self._route()

            def do_POST(self):
                self._route()

            def do_PUT(self):
                self._route()

            def do_PATCH(self):
                self._route()

            def do_DELETE(self):
                self._route()

            def _route(self):
                if self.path.startswith("/api/"):
                    console.forward(self)
                elif self.command == "GET":
                    console.static(self)
                else:
                    self.send_error(405)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def static(self, handler):
        path = handler.path.split("?", 1)[0].split("#", 1)[0]
        target = (DIST / path.lstrip("/")).resolve()
        if path in ("", "/") or not target.is_file() or DIST.resolve() not in target.parents:
            target = DIST / "index.html"  # the console routes by hash
        body = target.read_bytes()
        handler.send_response(200)
        handler.send_header(
            "content-type", mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def forward(self, handler):
        length = int(handler.headers.get("content-length") or 0)
        body = handler.rfile.read(length) if length else None
        headers = {name: handler.headers[name] for name in FORWARDED if handler.headers.get(name)}
        if handler.headers.get("origin"):
            headers["origin"] = self.origin
        connection = http.client.HTTPSConnection(
            "localhost", self.api_port, context=self.context, timeout=60
        )
        try:
            connection.request(handler.command, handler.path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
            handler.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() in ("content-type", "set-cookie", "cache-control"):
                    handler.send_header(name, value)
            handler.send_header("content-length", str(len(content)))
            handler.end_headers()
            handler.wfile.write(content)
        finally:
            connection.close()


JOURNEY = Path(__file__).with_name("console_journey.mjs")
CONSOLE_JOURNEY = os.environ.get("LOTUSPOND_CONSOLE_JOURNEY") == "1"
SKIP_REASON = "opt-in browser journey: LOTUSPOND_CONSOLE_JOURNEY=1, `npm run build`, Playwright"


def journey(world, *, workspace, project, people, runs):
    """Run `console_journey.mjs` against the world's API and return what it saw.

    `people` maps a name to a signed-in person (their session cookie is all the browser
    gets); `runs` are the plan's executions and tasks. Screenshots go to
    `LOTUSPOND_CONSOLE_JOURNEY_SHOTS` (default: the world's `shots` directory). Raises
    with the script's own report when it fails.
    """
    if shutil.which("node") is None or not (DIST / "index.html").exists():
        raise AssertionError("the console journey needs node and a built console (npm run build)")
    import world as worlds

    console = Console(world.processes.api_port, world.processes.ca, world.config["origin"])
    console.start()
    shots = Path(os.environ.get("LOTUSPOND_CONSOLE_JOURNEY_SHOTS") or world.root / "shots")
    plan, written = world.root / "journey.json", world.root / "journey-result.json"
    written.unlink(missing_ok=True)
    worlds.write_private(
        plan,
        {
            "url": console.url,
            "workspace": workspace,
            "project": project,
            "people": {
                name: person.headers["cookie"].split("=", 1)[1] for name, person in people.items()
            },
            "runs": runs,
            "shots": str(shots),
            "result": str(written),
        },
    )
    try:
        ran = subprocess.run(
            ["node", str(JOURNEY), str(plan)], capture_output=True, timeout=1500, check=False
        )
    finally:
        console.stop()
    if not written.exists():
        raise AssertionError(ran.stderr.decode()[-2000:])
    seen = json.loads(written.read_text())
    seen["shots"] = str(shots)
    if ran.returncode != 0 or not seen["ok"]:
        raise AssertionError((seen, ran.stderr.decode()[-2000:]))
    return seen
