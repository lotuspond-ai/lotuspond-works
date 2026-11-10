#!/usr/bin/env python3
"""Send spans from the official OpenTelemetry Python SDK, left at its defaults, to Lotuspond.

The exporter speaks OTLP/HTTP with binary protobuf, the SDK default, straight to the API, with
and without gzip. The API runs on a throwaway SQLite database in a private temporary directory
and every port is loopback. The check then reads the stored traces back and compares them
with the spans the SDK recorded.

It needs the SDK, which is not a Lotuspond dependency, so run it in a throwaway environment:

    uv run --no-project --with opentelemetry-sdk --with opentelemetry-exporter-otlp-proto-http \\
        scripts/checks/otlp-interop.py --report /private/tmp/otlp-interop.json

The API itself runs under backend/.venv.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "backend" / ".venv" / "bin" / "python"

# Seeds one workspace, project and ingest key, then serves the real API on SQLite.
API = r"""
import os, sys
from datetime import timedelta
import uvicorn
sys.path.insert(0, os.environ["BACKEND"])
from lotuspond.app import create_app
from lotuspond.config import Settings
from lotuspond.database import engine_for
from lotuspond.models import ApiKey, Base, Membership, Project, User, Workspace, new_id, now
from lotuspond.security import digest
settings = Settings(env="test", database_url="sqlite+pysqlite:///" + os.environ["DATABASE"],
                    origin="http://127.0.0.1", network_mode="airgap")
engine = engine_for(settings)
Base.metadata.create_all(engine)
from sqlalchemy.orm import Session
with Session(engine) as db:
    user = User(id=new_id(), email="interop@example.test", name="Interop", password_hash="unused")
    db.add(user); db.flush()
    workspace = Workspace(id=new_id(), name="Interop", slug="interop", owner_id=user.id)
    db.add(workspace); db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    db.add(Project(id=os.environ["PROJECT"], workspace_id=workspace.id, name="Interop", description=""))
    key = os.environ["API_KEY"]
    db.add(ApiKey(id=new_id(), workspace_id=workspace.id, name="interop", token_hash=digest(key),
                  prefix=key[:11], scopes=["ingest", "read"], expires_at=now() + timedelta(hours=1)))
    db.commit()
uvicorn.run(create_app(settings, engine=engine), host="127.0.0.1", port=int(os.environ["PORT"]),
            log_level="warning")
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(url: str, process: subprocess.Popen, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"{url} exited before it was ready")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise SystemExit(f"{url} was not ready in {seconds} seconds")


def record_spans(service: str) -> list:
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = provider.get_tracer("lotuspond.interop", "1.0")
    with tracer.start_as_current_span("agent.turn", kind=SpanKind.SERVER) as turn:
        turn.set_attribute("lotuspond.cost_usd", 0.0125)
        turn.set_attribute("tags", ["refund", "tier-1"])
        with tracer.start_as_current_span("model.call", kind=SpanKind.CLIENT) as call:
            call.set_attribute("gen_ai.usage.output_tokens", 42)
            call.set_status(Status(StatusCode.ERROR, "upstream timeout"))
    provider.shutdown()
    return list(memory.get_finished_spans())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.report.exists():
        raise SystemExit("refusing to overwrite an existing report")

    project = str(uuid4())
    api_key = "lp_" + secrets.token_hex(20)
    report: dict[str, object] = {
        "sdk": {
            "opentelemetry-sdk": version("opentelemetry-sdk"),
            "opentelemetry-exporter-otlp-proto-http": version(
                "opentelemetry-exporter-otlp-proto-http"
            ),
            "opentelemetry-proto": version("opentelemetry-proto"),
            "protocol": "http/protobuf",
        },
        "scenarios": [],
    }
    with ExitStack() as stack:
        workdir = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="otlp-interop-"))
        )
        os.chmod(workdir, 0o700)
        database = workdir / "interop.sqlite3"
        api_port = free_port()
        expected = {}

        def expect(service: str, spans: list) -> None:
            for item in spans:
                context = item.get_span_context()
                expected[f"{context.trace_id:032x}:{context.span_id:016x}"] = (
                    service,
                    item,
                )

        api = subprocess.Popen(
            [str(PYTHON), "-c", API],
            env={
                "PATH": os.environ["PATH"],
                "BACKEND": str(ROOT / "backend"),
                "DATABASE": str(database),
                "PROJECT": project,
                "API_KEY": api_key,
                "PORT": str(api_port),
            },
        )
        stack.callback(api.wait, 10)
        stack.callback(api.terminate)
        wait_for(f"http://127.0.0.1:{api_port}/api/v1/health", api)

        endpoint = f"http://127.0.0.1:{api_port}/api/v1/telemetry/otlp?project_id={project}"
        for compression in (Compression.NoCompression, Compression.Gzip):
            service = f"interop-api-{compression.value or 'none'}"
            spans = record_spans(service)
            exporter = OTLPSpanExporter(
                endpoint=endpoint,
                headers={"authorization": f"Bearer {api_key}"},
                compression=compression,
                timeout=10,
            )
            result = exporter.export(spans)
            exporter.shutdown()
            report["scenarios"].append(
                {
                    "route": "api",
                    "compression": compression.value or "none",
                    "spans": len(spans),
                    "export_result": result.name,
                }
            )
            if result is not SpanExportResult.SUCCESS:
                raise SystemExit(f"api with {compression.value} was not accepted")
            expect(service, spans)

        with sqlite3.connect(database) as db:
            rows = db.execute(
                "SELECT external_id, name, status, latency_ms, cost_usd, attributes FROM traces"
            ).fetchall()
        stored = {row[0]: row for row in rows}
        mismatches = []
        for external_id, (service, item) in expected.items():
            row = stored.get(external_id)
            if row is None:
                mismatches.append(f"{item.name} missing")
                continue
            otel = json.loads(row[5])["otel"]
            error = item.status.status_code is StatusCode.ERROR
            checks = {
                "name": row[1] == item.name,
                "status": row[2] == ("error" if error else "ok"),
                "latency": row[3] == (item.end_time - item.start_time) // 1_000_000,
                "service": otel["resource"].get("service.name") == service,
                "kind": otel["kind"] == item.kind.value + 1,
                # OTLP carries integers as decimal strings; sequences become arrays.
                "attributes": all(
                    otel["span"].get(key)
                    == (
                        list(value)
                        if isinstance(value, tuple)
                        else str(value)
                        if isinstance(value, int) and not isinstance(value, bool)
                        else value
                    )
                    for key, value in item.attributes.items()
                ),
            }
            mismatches += [
                f"{item.name}: {name}" for name, ok in checks.items() if not ok
            ]
        report["stored_traces"] = len(rows)
        report["expected_traces"] = len(expected)
        report["mismatches"] = mismatches
        if len(rows) != len(expected) or mismatches:
            raise SystemExit(
                f"stored traces differ from the recorded spans: {mismatches}"
            )
    report["cleanup"] = {
        "api_exit": api.returncode,
        "workdir_removed": not workdir.exists(),
    }
    with open(arguments.report, "x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
