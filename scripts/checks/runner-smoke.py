#!/usr/bin/env python3
"""Run the real API and Node runner against one disposable PostgreSQL schema."""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from lotuspond.models import Base, SchemaVersion
from lotuspond.schema_version import SCHEMA_VERSION

DATABASE_NAME = "lotuspond_platform_test"
MAX_HTTP_BYTES = 8 * 1024 * 1024
PROCESS_TIMEOUT_SECONDS = 20
SCHEMA_PATTERN = re.compile(r"^runner_smoke_[0-9a-f]{32}$")
MARKER_TABLE = "_runner_smoke_ownership"


class SmokeFailure(RuntimeError):
    """A sanitized smoke-check failure."""


def _private_file(path: Path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def _loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        try:
            addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            return False
        return bool(addresses) and all(
            ipaddress.ip_address(item[4][0]).is_loopback for item in addresses
        )
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _database_target(raw: str, expected_data_directory: Path) -> tuple[URL, Any]:
    try:
        url = make_url(raw)
    except Exception as error:
        raise SmokeFailure("invalid test database URL") from error
    if (
        not url.drivername.startswith("postgresql+psycopg")
        or url.database != DATABASE_NAME
        or not _loopback_host(url.host)
        or bool(url.query)
    ):
        raise SmokeFailure("the smoke check requires the exact loopback test database")
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            database, address, port, data_directory = connection.execute(
                text(
                    "SELECT current_database(), inet_server_addr()::text, "
                    "inet_server_port(), current_setting('data_directory')"
                )
            ).one()
    except Exception as error:
        engine.dispose()
        raise SmokeFailure("test database identity could not be verified") from error
    configured_port = url.port or 5432
    try:
        server_is_loopback = ipaddress.ip_interface(address).ip.is_loopback
    except (TypeError, ValueError):
        server_is_loopback = False
    expected_directory = expected_data_directory.expanduser().resolve()
    actual_directory = Path(data_directory).expanduser().resolve()
    if (
        database != DATABASE_NAME
        or not server_is_loopback
        or port != configured_port
        or actual_directory != expected_directory
    ):
        engine.dispose()
        raise SmokeFailure("test database identity does not match the explicit expectation")
    return url, engine


def _schema_url(url: URL, schema: str) -> URL:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise SmokeFailure("invalid disposable schema name")
    return url.set(query={"options": f"-c search_path={schema}"})


def _create_schema(administration, url: URL, schema: str, ownership_token: str):
    quoted = f'"{schema}"'
    with administration.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted}"))
        connection.execute(
            text(
                f"CREATE TABLE {quoted}.{MARKER_TABLE} "
                "(ownership_token VARCHAR(64) PRIMARY KEY)"
            )
        )
        connection.execute(
            text(f"INSERT INTO {quoted}.{MARKER_TABLE} (ownership_token) VALUES (:token)"),
            {"token": ownership_token},
        )
    scoped = create_engine(_schema_url(url, schema), hide_parameters=True, pool_pre_ping=True)
    try:
        Base.metadata.create_all(scoped)
        with scoped.begin() as connection:
            connection.execute(SchemaVersion.__table__.insert().values(version=SCHEMA_VERSION))
    except BaseException as error:
        scoped.dispose()
        if not _drop_owned_schema(administration, schema, ownership_token):
            raise SmokeFailure("partially initialized smoke schema could not be removed") from error
        raise
    return scoped


def _drop_owned_schema(administration, schema: str, ownership_token: str) -> bool:
    if not SCHEMA_PATTERN.fullmatch(schema):
        return False
    quoted = f'"{schema}"'
    try:
        with administration.begin() as connection:
            observed = connection.scalar(
                text(f"SELECT ownership_token FROM {quoted}.{MARKER_TABLE}")
            )
            if not hmac.compare_digest(str(observed), ownership_token):
                return False
            connection.execute(text(f"DROP SCHEMA {quoted} CASCADE"))
        with administration.connect() as connection:
            remains = connection.scalar(
                text("SELECT 1 FROM pg_namespace WHERE nspname=:schema"), {"schema": schema}
            )
        return remains is None
    except SQLAlchemyError:
        return False


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _file, _code, _message, _headers, _new_url):
        return None


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.cookie: str | None = None
        self.workspace: str | None = None
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirectHandler()
        )

    def request(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        expected: int = 200,
    ) -> dict[str, Any]:
        if not path.startswith("/") or path.startswith("//"):
            raise SmokeFailure("invalid smoke API path")
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {"accept": "application/json", "origin": self.base_url.removesuffix("/api/v1")}
        if payload is not None:
            headers["content-type"] = "application/json"
        if self.cookie:
            headers["cookie"] = self.cookie
        if self.workspace:
            headers["x-lotuspond-workspace"] = self.workspace
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers=headers,
            method="GET" if body is None else "POST",
        )
        try:
            response = self.opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        except OSError as error:
            raise SmokeFailure("the local API request failed") from error
        with response:
            content = response.read(MAX_HTTP_BYTES + 1)
            if response.status != expected or len(content) > MAX_HTTP_BYTES:
                raise SmokeFailure(
                    f"the local API returned status {response.status} for {path}; expected {expected}"
                )
            try:
                parsed = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SmokeFailure("the local API returned invalid JSON") from error
            if not isinstance(parsed, dict):
                raise SmokeFailure("the local API returned an invalid object")
            cookies = SimpleCookie()
            for value in response.headers.get_all("set-cookie", []):
                cookies.load(value)
            if "lotuspond_session" in cookies:
                self.cookie = f"lotuspond_session={cookies['lotuspond_session'].value}"
            return parsed


def _clean_environment(temporary: Path) -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(temporary),
        "PYTHONUNBUFFERED": "1",
    }
    for name in ("LANG", "LC_ALL"):
        if value := os.environ.get(name):
            environment[name] = value
    return environment


def _stop_owned_process(process: subprocess.Popen[bytes] | None, timeout: float = 5) -> bool:
    if process is None:
        return True
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=timeout)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    return False
    return process.poll() is not None


def _process_absent(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _group_absent(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _stop_owned_group(pid: int | None) -> bool:
    if pid is None or _group_absent(pid):
        return True
    try:
        if os.getpgid(pid) != pid:
            return False
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not _group_absent(pid):
        time.sleep(0.02)
    if not _group_absent(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            return False
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not _group_absent(pid):
        time.sleep(0.02)
    return _group_absent(pid)


def _wait_for(probe, *, seconds: float = 10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.05)
    raise SmokeFailure("a local smoke condition did not complete before its deadline")


def _start_api(listener: socket.socket, database_url: URL, origin: str, temporary: Path):
    environment = _clean_environment(temporary)
    environment.update(
        {
            "LOTUSPOND_ENV": "test",
            "LOTUSPOND_DATABASE_URL": database_url.render_as_string(hide_password=False),
            "LOTUSPOND_ORIGIN": origin,
            "LOTUSPOND_REGISTRATION_ENABLED": "true",
        }
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "lotuspond.app:app",
            "--fd",
            str(listener.fileno()),
            "--no-access-log",
            "--no-server-header",
            "--log-level",
            "warning",
        ],
        cwd=BACKEND,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(listener.fileno(),),
        start_new_session=True,
    )


def _write_adapter(path: Path, source: str):
    with _private_file(path) as target:
        target.write(source.encode())


def _adapter_source(kind: str, imported: Path | None = None) -> str:
    prelude = """import { writeFileSync } from 'node:fs';
function recordPid() { writeFileSync(process.env.SMOKE_PID_FILE, String(process.pid), { mode: 0o600 }); }
"""
    if kind == "success":
        return prelude + """export function evaluate() {
  recordPid();
  const leaked = Boolean(process.env.LOTUSPOND_API_KEY || process.env.LOTUSPOND_DATABASE_URL);
  return { passed: !leaked, output: { control_credentials_inherited: leaked }, evidence: {} };
}
"""
    if kind == "cpu":
        return prelude + "export function evaluate() { recordPid(); while (true) {} }\n"
    if kind == "crash":
        return prelude + "export function evaluate() { recordPid(); process.exit(19); }\n"
    if kind == "http" and imported is not None:
        return (
            prelude
            + f"import {{ evaluate as invoke }} from {json.dumps(imported.as_uri())};\n"
            + "export async function evaluate(input, expected, context) { "
            + "recordPid(); return invoke(input, expected, context); }\n"
        )
    raise SmokeFailure("unsupported synthetic adapter")


class _SyntheticAgent(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, token: str):
        self.token = token
        super().__init__(("127.0.0.1", 0), _SyntheticAgentHandler)


class _SyntheticAgentHandler(BaseHTTPRequestHandler):
    server: _SyntheticAgent

    def log_message(self, _format, *_args):
        return

    def do_POST(self):
        if self.path != "/agent" or not hmac.compare_digest(
            self.headers.get("authorization", ""), f"Bearer {self.server.token}"
        ):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            if length < 1 or length > 4096:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if body != {"input": "hello"}:
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        response = b"approved answer"
        self.send_response(200)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def _create_key(client: ApiClient, name: str) -> dict[str, str]:
    result = client.request(
        "/api-keys", {"name": name, "scopes": ["read", "run"], "days": 1}, expected=201
    )
    if not isinstance(result.get("id"), str) or not isinstance(result.get("key"), str):
        raise SmokeFailure("the local API did not create a scoped runner key")
    return {"id": result["id"], "key": result["key"]}


def _create_run(
    client: ApiClient,
    label: str,
    *,
    input_value: dict[str, Any] | None = None,
    expected_value: dict[str, Any] | None = None,
) -> str:
    project = client.request("/projects", {"name": f"{label} project"}, expected=201)
    client.request(
        "/scenarios",
        {
            "project_id": project["id"],
            "name": f"{label} scenario",
            "input": input_value or {"prompt": label},
            "expected": expected_value or {"contains": label},
        },
        expected=201,
    )
    run = client.request(
        "/runs",
        {"project_id": project["id"], "name": f"{label} run", "revision": "smoke"},
        expected=201,
    )
    return str(run["id"])


def _runner_process(
    *,
    api_url: str,
    api_key: str,
    runner_id: str,
    adapter: Path,
    pid_file: Path,
    temporary: Path,
    extra_environment: dict[str, str] | None = None,
):
    node = shutil.which("node")
    if not node:
        raise SmokeFailure("Node.js is required for the runner smoke check")
    environment = _clean_environment(temporary)
    environment.update(
        {
            "LOTUSPOND_API_URL": api_url,
            "LOTUSPOND_API_KEY": api_key,
            "LOTUSPOND_RUNNER_ID": runner_id,
            "LOTUSPOND_RUNNER_ONCE": "true",
            "LOTUSPOND_RUNNER_SCENARIO_TIMEOUT_MS": "3000",
            "LOTUSPOND_RUNNER_RENEW_INTERVAL_MS": "1000",
            "LOTUSPOND_RUNNER_ADAPTER_ENV": "SMOKE_PID_FILE",
            "SMOKE_PID_FILE": str(pid_file),
        }
    )
    environment.update(extra_environment or {})
    log_path = temporary / f"runner-{uuid4().hex}.log"
    log = _private_file(log_path)
    process = subprocess.Popen(
        [node, str(ROOT / "scripts" / "runner.mjs"), str(adapter)],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    return process, log_path


def _adapter_pid(pid_file: Path) -> int:
    _wait_for(pid_file.is_file)
    try:
        value = int(pid_file.read_text())
    except (OSError, ValueError) as error:
        raise SmokeFailure("the scenario subprocess ownership marker is invalid") from error
    if value <= 0:
        raise SmokeFailure("the scenario subprocess ownership marker is invalid")
    return value


def _finish_runner(
    process: subprocess.Popen[bytes],
    log_path: Path,
    pid: int,
    forbidden: list[str],
    *,
    require_success: bool = True,
):
    try:
        return_code = process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        _stop_owned_process(process)
        raise SmokeFailure("the Node runner did not exit before its deadline") from error
    if require_success and return_code != 0:
        raise SmokeFailure("the Node runner failed")
    if log_path.stat().st_size > 1024 * 1024:
        raise SmokeFailure("the Node runner log exceeded its bound")
    log = log_path.read_text(errors="replace")
    if any(secret_value and secret_value in log for secret_value in forbidden):
        raise SmokeFailure("the Node runner log exposed a credential")
    _wait_for(lambda: _process_absent(pid) and _group_absent(pid), seconds=3)
    return return_code


def _run_completed_case(
    client: ApiClient,
    *,
    api_url: str,
    runner_id: str,
    api_key: str,
    temporary: Path,
    label: str,
    adapter_kind: str,
    expected_passed: bool | None,
    input_value: dict[str, Any] | None = None,
    expected_value: dict[str, Any] | None = None,
    imported_adapter: Path | None = None,
    extra_environment: dict[str, str] | None = None,
    forbidden: list[str] | None = None,
) -> dict[str, Any]:
    run_id = _create_run(
        client, label, input_value=input_value, expected_value=expected_value
    )
    adapter = temporary / f"adapter-{label}-{uuid4().hex}.mjs"
    pid_file = temporary / f"adapter-{label}-{uuid4().hex}.pid"
    _write_adapter(adapter, _adapter_source(adapter_kind, imported_adapter))
    process, log = _runner_process(
        api_url=api_url,
        api_key=api_key,
        runner_id=runner_id,
        adapter=adapter,
        pid_file=pid_file,
        temporary=temporary,
        extra_environment=extra_environment,
    )
    pid = None
    try:
        pid = _adapter_pid(pid_file)
        _finish_runner(process, log, pid, [api_key, *(forbidden or [])])
    finally:
        if not _stop_owned_process(process) or not _stop_owned_group(pid):
            raise SmokeFailure("the owned Node runner could not be stopped")
    detail = client.request(f"/runs/{run_id}")
    results = detail.get("results")
    if detail.get("status") != "completed" or not isinstance(results, list) or len(results) != 1:
        raise SmokeFailure("the completed run was not persisted exactly once")
    if results[0].get("passed") is not expected_passed:
        raise SmokeFailure("the persisted run verdict was incorrect")
    return results[0]


def _run_interrupted_case(
    client: ApiClient,
    *,
    api_url: str,
    runner_id: str,
    key: dict[str, str],
    temporary: Path,
    label: str,
    revoke: bool,
):
    run_id = _create_run(client, label)
    adapter = temporary / f"adapter-{label}-{uuid4().hex}.mjs"
    pid_file = temporary / f"adapter-{label}-{uuid4().hex}.pid"
    _write_adapter(adapter, _adapter_source("cpu"))
    process, log = _runner_process(
        api_url=api_url,
        api_key=key["key"],
        runner_id=runner_id,
        adapter=adapter,
        pid_file=pid_file,
        temporary=temporary,
    )
    pid = None
    try:
        pid = _adapter_pid(pid_file)
        _wait_for(lambda: client.request(f"/runs/{run_id}").get("status") == "running")
        if revoke:
            client.request(f"/api-keys/{key['id']}/revoke", {})
        else:
            client.request(f"/runs/{run_id}/cancel", {})
        # Both cancellation and key revocation deliberately invalidate the lease;
        # the one-shot runner reports that authoritative loss with a nonzero exit.
        return_code = _finish_runner(
            process, log, pid, [key["key"]], require_success=False
        )
        if return_code == 0:
            raise SmokeFailure("the Node runner did not report authoritative lease loss")
    finally:
        if not _stop_owned_process(process) or not _stop_owned_group(pid):
            raise SmokeFailure("the interrupted Node runner could not be stopped")
    detail = client.request(f"/runs/{run_id}")
    if detail.get("results") != []:
        raise SmokeFailure("an interrupted run persisted scenario results")
    expected_status = "running" if revoke else "cancelled"
    if detail.get("status") != expected_status:
        raise SmokeFailure("the interrupted run status was incorrect")
    if revoke:
        client.request(f"/runs/{run_id}/cancel", {})


def _execute(args) -> dict[str, Any]:
    raw_database = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    if not raw_database:
        raise SmokeFailure("LOTUSPOND_PLATFORM_TEST_DATABASE_URL is required")
    url, administration = _database_target(raw_database, args.expected_data_directory)
    schema = f"runner_smoke_{uuid4().hex}"
    ownership_token = secrets.token_hex(32)
    scoped = None
    api_process = None
    listener = None
    agent = None
    agent_thread = None
    schema_created = False
    process_cleanup = True
    schema_cleanup = False
    report: dict[str, Any] | None = None
    try:
        scoped = _create_schema(administration, url, schema, ownership_token)
        schema_created = True
        with tempfile.TemporaryDirectory(prefix="lotuspond-runner-smoke-") as directory:
            temporary = Path(directory)
            temporary.chmod(0o700)
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            origin = f"http://127.0.0.1:{port}"
            api_url = origin + "/api/v1"
            api_process = _start_api(listener, _schema_url(url, schema), origin, temporary)
            client = ApiClient(api_url)

            def api_ready():
                if api_process.poll() is not None:
                    raise SmokeFailure("the local API exited during startup")
                try:
                    return client.request("/health").get("status") == "ok"
                except SmokeFailure:
                    return False

            _wait_for(api_ready)
            listener.close()
            listener = None
            password = secrets.token_urlsafe(24)
            registration = client.request(
                "/auth/register",
                {
                    "email": f"runner-smoke-{uuid4().hex}@example.com",
                    "name": "Synthetic runner smoke",
                    "password": password,
                    "account_type": "individual",
                },
                expected=201,
            )
            client.workspace = str(registration["workspaceId"])
            runner = client.request(
                "/runners",
                {"name": "Synthetic isolated runner", "capabilities": ["text"]},
                expected=201,
            )
            key = _create_key(client, "Synthetic runner smoke")

            success = _run_completed_case(
                client,
                api_url=api_url,
                runner_id=runner["id"],
                api_key=key["key"],
                temporary=temporary,
                label="success",
                adapter_kind="success",
                expected_passed=True,
                forbidden=[password],
            )
            if success.get("output", {}).get("control_credentials_inherited") is not False:
                raise SmokeFailure("the adapter inherited a control-plane credential")
            _run_completed_case(
                client,
                api_url=api_url,
                runner_id=runner["id"],
                api_key=key["key"],
                temporary=temporary,
                label="cpu-timeout",
                adapter_kind="cpu",
                expected_passed=None,
            )
            _run_completed_case(
                client,
                api_url=api_url,
                runner_id=runner["id"],
                api_key=key["key"],
                temporary=temporary,
                label="crash",
                adapter_kind="crash",
                expected_passed=None,
            )

            agent_token = secrets.token_urlsafe(32)
            agent = _SyntheticAgent(agent_token)
            agent_thread = threading.Thread(target=agent.serve_forever, daemon=True)
            agent_thread.start()
            http_adapter = ROOT / "protocol-adapters" / "src" / "http-runner.mjs"
            if not http_adapter.is_file():
                raise SmokeFailure("the local HTTP runner adapter is unavailable")
            _run_completed_case(
                client,
                api_url=api_url,
                runner_id=runner["id"],
                api_key=key["key"],
                temporary=temporary,
                label="local-http",
                adapter_kind="http",
                imported_adapter=http_adapter,
                input_value={"prompt": "hello"},
                expected_value={"contains": "approved"},
                expected_passed=True,
                extra_environment={
                    "LOTUSPOND_HTTP_ENDPOINT": f"http://127.0.0.1:{agent.server_port}/agent",
                    "LOTUSPOND_HTTP_TOKEN": agent_token,
                    "LOTUSPOND_HTTP_TIMEOUT_MS": "1000",
                },
                forbidden=[agent_token],
            )
            agent.shutdown()
            agent.server_close()
            agent_thread.join(timeout=2)
            agent = None
            agent_thread = None

            cancel_key = _create_key(client, "Synthetic cancellation runner")
            _run_interrupted_case(
                client,
                api_url=api_url,
                runner_id=runner["id"],
                key=cancel_key,
                temporary=temporary,
                label="cancelled",
                revoke=False,
            )
            revoked_key = _create_key(client, "Synthetic revoked runner")
            _run_interrupted_case(
                client,
                api_url=api_url,
                runner_id=runner["id"],
                key=revoked_key,
                temporary=temporary,
                label="revoked",
                revoke=True,
            )
            report = {
                "schema_version": SCHEMA_VERSION,
                "cases": {
                    "persisted_success": True,
                    "cpu_timeout_unknown": True,
                    "crash_unknown": True,
                    "control_key_not_inherited": True,
                    "local_http_adapter": True,
                    "cancellation_no_results": True,
                    "revocation_no_results": True,
                },
                "external_network_used": False,
            }
    finally:
        if agent is not None:
            agent.shutdown()
            agent.server_close()
        if agent_thread is not None:
            agent_thread.join(timeout=2)
            process_cleanup = process_cleanup and not agent_thread.is_alive()
        if listener is not None:
            listener.close()
        process_cleanup = _stop_owned_process(api_process) and process_cleanup
        if scoped is not None:
            scoped.dispose()
        if schema_created:
            schema_cleanup = _drop_owned_schema(administration, schema, ownership_token)
        administration.dispose()
    if not process_cleanup or not schema_cleanup:
        raise SmokeFailure("owned smoke resources were not verifiably cleaned up")
    if report is None:
        raise SmokeFailure("the runner smoke check did not produce a report")
    report["cleanup_confirmed"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-data-directory",
        type=Path,
        required=True,
        help="Exact PostgreSQL data_directory expected for the isolated local test server.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="New file that receives the credential-free JSON result.",
    )
    args = parser.parse_args()
    try:
        report = _execute(args)
    # Never let an unexpected dependency exception print URLs, SQL, or generated credentials.
    except Exception as error:  # noqa: BLE001
        print(f"Runner smoke failed ({type(error).__name__}); no credentials were displayed.", file=sys.stderr)
        return 1
    encoded = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        with _private_file(args.report) as target:
            target.write(encoded)
    except OSError:
        print("Runner smoke passed but its report could not be written.", file=sys.stderr)
        return 1
    print(encoded.decode().strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
