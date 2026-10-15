"""Release qualification: actual PostgreSQL/API and external installed artifacts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_migration_then_installed_journey(tmp_path):
    database = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    if not database:
        pytest.skip("Verified localhost PostgreSQL required")
    expected = os.environ.get("LOTUSPOND_FLEET_TEST_DATA_DIRECTORY")
    assert expected, (
        "Qualification requires an independent test database data-directory expectation"
    )
    private = tmp_path / "database-url"
    private.write_text(database)
    private.chmod(0o600)
    report = tmp_path / "fleet-report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/checks/fleet-smoke.py"),
            "--database-url-file",
            str(private),
            "--expected-data-directory",
            expected,
            "--report",
            str(report),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=420,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text())
    required_postgres_skips = data["required_postgres_skips"]
    assert required_postgres_skips == 0
    assert data["cleanup_confirmed"] is True
    for journey in data["journeys"]:
        assert journey["installed_runtime_version"] == "0.1.0"
        assert journey["installed_sdk_version"] == "0.2.0"
        assert journey["restored_state_new_effect_count"] == 0
        assert all(value == "passed" for value in journey["cases"].values())


def test_one_time_response_has_single_no_store_header(identity_env):
    from tests.fleet.identity_fixtures import enrollment, exchange

    issued = enrollment(identity_env)
    assert issued.status_code == 201
    response = exchange(identity_env, issued.json()["token"])
    assert response.status_code == 201
    assert response.headers.get_list("cache-control") == ["no-store"]
    names = [name.lower() for name, _ in response.headers.raw]
    assert len(names) == len(set(names))


@pytest.mark.parametrize(
    "existing", [[], [(b"Cache-Control", b"public"), (b"cache-control", b"max-age=60")]]
)
@pytest.mark.parametrize("path", ["/api/v1/probe", "/plain"])
async def test_boundary_sets_one_private_cache_policy_preserving_other_headers(existing, path):
    from lotuspond.app import RequestBoundary
    from lotuspond.config import Settings

    cookies = [(b"set-cookie", b"first=1"), (b"set-cookie", b"second=2")]
    sent = []

    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": existing + cookies})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await RequestBoundary(application, Settings(env="test"))(
        {"type": "http", "method": "GET", "path": path, "headers": []}, receive, send
    )
    headers = sent[0]["headers"]
    assert [(key, value) for key, value in headers if key.lower() == b"set-cookie"] == cookies
    assert [(key, value) for key, value in headers if key.lower() == b"cache-control"] == (
        [(b"cache-control", b"no-store")] if path.startswith("/api/") else existing
    )


@pytest.mark.parametrize(
    "target",
    [
        "postgresql+psycopg://localhost/customer",
        "postgresql+psycopg://192.0.2.1/lotuspond_platform_test",
    ],
)
def test_smoke_refuses_non_test_database_before_building(tmp_path, target):
    private = tmp_path / "database"
    private.write_text(target)
    private.chmod(0o600)
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/checks/fleet-smoke.py"),
            "--database-url-file",
            str(private),
            "--expected-data-directory",
            "/unused",
            "--report",
            str(report),
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert target.encode() not in result.stdout + result.stderr
    assert json.loads(report.read_text())["outcome"] == "failed"
    assert not list(tmp_path.rglob("*.whl"))


def test_smoke_preserves_existing_report(tmp_path):
    private = tmp_path / "database"
    private.write_text("not a database URL")
    private.chmod(0o600)
    report = tmp_path / "report.json"
    report.write_bytes(b"retained prior evidence")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/checks/fleet-smoke.py"),
            "--database-url-file",
            str(private),
            "--expected-data-directory",
            "/unused",
            "--report",
            str(report),
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert report.read_bytes() == b"retained prior evidence"
    assert b"not a database URL" not in result.stdout + result.stderr
