"""Release qualification: actual PostgreSQL/API and external installed artifacts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CHECKS = ROOT / "scripts/checks"
sys.path.insert(0, str(CHECKS))
from fleet_process import run_owned, wait_owned  # noqa: E402


def outer_resource_outcome(result, report):
    """Retain an explicit negative outcome if controller escalation lost its report."""
    try:
        child = json.loads(report.read_text())
    except (OSError, ValueError):
        child = {}
    outcome = {
        "timed_out": result.timed_out,
        "controller_group_absent": result.cleanup_confirmed,
        "cleanup_confirmed": result.cleanup_confirmed and child.get("cleanup_confirmed") is True,
        "child_outcome": child.get("outcome", "unconfirmed"),
    }
    (report.parent / "outer-resource-outcome.json").write_text(json.dumps(outcome, indent=2) + "\n")
    return outcome


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
    result = run_owned(
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
        timeout=420,
        grace=45,
        receipt=tmp_path / "outer-process-receipt.json",
    )
    outer_resource_outcome(result, report)
    assert not result.timed_out and result.cleanup_confirmed
    assert result.returncode == 0, (result.stdout + result.stderr).decode()
    data = json.loads(report.read_text())
    required_postgres_skips = data["required_postgres_skips"]
    assert required_postgres_skips == 0
    assert data["cleanup_confirmed"] is True
    for journey in data["journeys"]:
        assert journey["installed_runtime_version"] == "0.1.0"
        assert journey["installed_sdk_version"] == "0.3.0"
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


def test_build_command_timeout_does_not_leave_owned_descendant(tmp_path):
    """The same command runner used for uv must own descendants on timeout."""
    import importlib.util
    import signal
    import time

    spec = importlib.util.spec_from_file_location(
        "fleet_smoke_probe", ROOT / "scripts/checks/fleet-smoke.py"
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    marker = tmp_path / "owned-build-probe.json"
    program = """
import json,os,subprocess,sys,time
if os.getpgrp()!=os.getpid(): os.setsid()
child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])
with open(sys.argv[1],"w") as f: json.dump({"pid":os.getpid(),"group":os.getpgrp(),"child":child.pid},f)
time.sleep(60)
"""
    try:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
            smoke.run([sys.executable, "-c", program, str(marker)], tmp_path, timeout=0.5)
        owned = json.loads(marker.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(owned["group"], 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("timed-out build command left its exact owned process group alive")
    finally:
        if marker.exists():
            owned = json.loads(marker.read_text())
            try:
                os.killpg(owned["group"], signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("trigger", ["timeout", "cancel", "leader-exit"])
def test_owned_group_escalates_after_leader_closes_pipes(tmp_path, trigger):
    """An isolated subreaper owns the exact TERM-resistant probe descendant."""
    program = r'''
import ctypes,json,os,signal,subprocess,sys,threading,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from fleet_process import Cancelled,cancellation_signals,wait_owned
root=Path(sys.argv[2]); trigger=sys.argv[3]
assert ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0
child_program="""
import os,signal,sys,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(60)
"""
leader_program="""
import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]+'/child'],
                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print('leader stdout',flush=True)
print('leader stderr',file=sys.stderr,flush=True)
while not Path(sys.argv[1]+'/exit').exists(): time.sleep(.01)
"""
leader=subprocess.Popen([sys.executable,'-c',leader_program,str(root),child_program],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
child=None; reaped=threading.Event(); timer=None; observation={}
def absent():
    try: os.killpg(leader.pid,0)
    except ProcessLookupError: return True
    return False
def reap_child():
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try:
            if os.waitpid(child,os.WNOHANG)[0]==child:
                reaped.set(); return
        except ChildProcessError: pass  # Wait for this exact child to be adopted.
        time.sleep(.01)
try:
    deadline=time.monotonic()+3
    while not (root/'child').exists() and time.monotonic()<deadline: time.sleep(.01)
    child=int((root/'child').read_text())
    assert os.getpgid(child)==leader.pid
    reaper=threading.Thread(target=reap_child); reaper.start()
    if trigger=='leader-exit': (root/'exit').touch()
    started=time.monotonic()
    with cancellation_signals():
        if trigger=='cancel':
            timer=threading.Timer(.1,lambda: os.kill(os.getpid(),signal.SIGTERM))
            timer.start()
        try:
            wait_owned(leader,timeout=.1 if trigger=='timeout' else 3,grace=.2,
                       receipt=root/'runner.json')
            assert trigger!='cancel'
        except Cancelled:
            assert trigger=='cancel'
    observation={'group':leader.pid,'child':child,'group_absent':absent(),
                 'child_reaped':reaped.is_set(),'elapsed':time.monotonic()-started}
    (root/'before-test-cleanup.json').write_text(json.dumps(observation,indent=2)+'\n')
finally:
    if timer is not None: timer.cancel(); timer.join()
    if not absent(): os.killpg(leader.pid,signal.SIGKILL)
    leader.communicate(timeout=2)
    if child is not None: reaper.join(timeout=3)
    (root/'test-cleanup.json').write_text(json.dumps(
        {'group_absent':absent(),'child_reaped':reaped.is_set()},indent=2)+'\n')
receipt=json.loads((root/'runner.json').read_text())
assert observation['group_absent'] and observation['child_reaped'],observation
assert receipt['cleanup_confirmed'] and receipt['escalated'],receipt
assert receipt['timed_out']==(trigger=='timeout'),receipt
assert receipt['cancelled']==(trigger!='leader-exit'),receipt
assert receipt['exit']==(0 if trigger=='leader-exit' else -signal.SIGTERM),receipt
assert (root/'runner.stdout').read_bytes()==b'leader stdout\n'
assert (root/'runner.stderr').read_bytes()==b'leader stderr\n'
assert observation['elapsed']<2,observation
'''
    probe = subprocess.run(
        [sys.executable, "-c", program, str(CHECKS), str(tmp_path), trigger],
        capture_output=True,
        timeout=15,
    )
    assert probe.returncode == 0, (probe.stdout + probe.stderr).decode()


def test_installed_restore_consumer_measures_live_sink(tmp_path):
    database = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    if not database:
        pytest.skip("Verified localhost PostgreSQL required")
    expected = os.environ["LOTUSPOND_FLEET_TEST_DATA_DIRECTORY"]
    private = tmp_path / "database-url"
    private.write_text(database)
    private.chmod(0o600)
    control = tmp_path / "test-control.json"
    control.write_text(json.dumps({"scenario": "restore"}))
    control.chmod(0o600)
    report = tmp_path / "fleet-report.json"
    result = run_owned(
        [
            sys.executable,
            str(CHECKS / "fleet-smoke.py"),
            "--database-url-file",
            str(private),
            "--expected-data-directory",
            expected,
            "--report",
            str(report),
            "--test-control-file",
            str(control),
        ],
        cwd=tmp_path,
        timeout=180,
        grace=45,
        receipt=tmp_path / "outer-process-receipt.json",
    )
    outer_resource_outcome(result, report)
    assert result.returncode == 0, (result.stdout + result.stderr).decode()
    data = json.loads(report.read_text())
    assert data["scenario"] == "restore" and data["cleanup_confirmed"] is True
    assert len(data["journeys"]) == 2
    for journey in data["journeys"]:
        assert journey["restore_positive_effect_count"] == 1
        assert (
            journey["restored_state_new_effect_count"]
            == (journey["restore_final_effect_count"] - journey["restore_positive_effect_count"])
            == 0
        )
        assert len(journey["restore_phase_observations"]) == 3


def test_outer_deadline_cancels_active_harness_and_owned_schema(tmp_path):
    import importlib.util
    import socket
    import time

    from sqlalchemy import text

    database = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    if not database:
        pytest.skip("Verified localhost PostgreSQL required")
    expected = os.environ["LOTUSPOND_FLEET_TEST_DATA_DIRECTORY"]
    private = tmp_path / "database-url"
    private.write_text(database)
    private.chmod(0o600)
    control = tmp_path / "test-control.json"
    control.write_text(json.dumps({"scenario": "restore", "pause_at": "api"}))
    control.chmod(0o600)
    report = tmp_path / "fleet-report.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(CHECKS / "fleet-smoke.py"),
            "--database-url-file",
            str(private),
            "--expected-data-directory",
            expected,
            "--report",
            str(report),
            "--test-control-file",
            str(control),
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 180
        ready = []
        while time.monotonic() < deadline:
            ready = list(
                tmp_path.glob("lotuspond-fleet-artifacts-*/case-runtime/interrupt-ready.json")
            )
            if ready or process.poll() is not None:
                break
            time.sleep(0.02)
        assert ready and process.poll() is None, "owned API/schema checkpoint not reached"
        case = ready[0].parent
        checkpoint = json.loads(ready[0].read_text())
        outcome = wait_owned(
            process, timeout=0.1, grace=45, receipt=tmp_path / "outer-process-receipt.json"
        )
        assert outcome.timed_out and outcome.returncode != 0 and outcome.cleanup_confirmed
        resource_outcome = outer_resource_outcome(outcome, report)
        assert resource_outcome["cleanup_confirmed"] is True
        safe = json.loads(report.read_text())
        assert safe["outcome"] == "cancelled" and safe["cleanup_confirmed"] is True
        assert json.loads((case / "cleanup.json").read_text())["cleanup_confirmed"] is True
        child = json.loads((case / "process-receipt.json").read_text())
        assert child["cancelled"] is True and child["cleanup_confirmed"] is True
        for group in (process.pid, child["group"]):
            with pytest.raises(ProcessLookupError):
                os.killpg(group, 0)
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", checkpoint["api_port"])) != 0
        spec = importlib.util.spec_from_file_location("fleet_db_guard", CHECKS / "runner-smoke.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        _, admin = guard._database_target(database, Path(expected))
        owned = json.loads((case / "schema-ownership.json").read_text())
        try:
            with admin.connect() as db:
                assert (
                    db.scalar(
                        text("SELECT 1 FROM pg_namespace WHERE nspname=:schema"),
                        {"schema": owned["schema"]},
                    )
                    is None
                )
        finally:
            admin.dispose()
    finally:
        if process.poll() is None:
            wait_owned(
                process, timeout=0, grace=45, receipt=tmp_path / "outer-finally-receipt.json"
            )
