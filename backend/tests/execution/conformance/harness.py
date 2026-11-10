"""Task 13 conformance harness: real API, worker and runtime processes for one world.

`build(world)` assembles the engine's services from the world's authorities, the
same way in every process. `python -m tests.execution.conformance.harness api|worker
<world>` serves the API over TLS or runs one orchestrator or sweeper worker, for the
parent to start, stop, SIGKILL, SIGSTOP and SIGCONT. Runtimes are the installed
`lotuspond_runtime` CLI, enrolled through the API and reaching it through a TCP proxy
that can stall or sever their connections. Test-only throughout; never imported by
the production API.
"""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import re
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import world as worlds  # noqa: E402

INTERPRETER = "lotuspond.execution-interpreter.v1"


# ------------------------------------------------------------------ the service graph


def scoped_url(world) -> str:
    from sqlalchemy.engine import make_url

    config = world.config
    url = make_url(config["database_url"])
    return url.set(query={"options": f"-c search_path={config['schema']}"}).render_as_string(
        hide_password=False
    )


def settings_for(world):
    from lotuspond.config import Settings

    return Settings(
        env="test",
        database_url=scoped_url(world),
        origin=world.config["origin"],
        redis_url=None,
        fleet_enabled=True,
        fleet_installation_id=worlds.INSTALLATION,
        registration_enabled=True,
    )


def build(world, engine=None):
    """Every execution service of one process, over the world's authorities."""
    from sqlalchemy.orm import sessionmaker

    from lotuspond.config import Settings
    from lotuspond.database import engine_for
    from lotuspond.execution.admission import AdmissionService
    from lotuspond.execution.artifacts import (
        WORKSPACE_ACCESS_POLICY,
        ArtifactPolicy,
        ArtifactStore,
    )
    from lotuspond.execution.boundary import ExecutionServices
    from lotuspond.execution.budgets import BudgetService
    from lotuspond.execution.context import ContextSource
    from lotuspond.execution.control import ControlService
    from lotuspond.execution.dispatch import ActivityService
    from lotuspond.execution.engine import PostgresExecutionEngine
    from lotuspond.execution.inbox import InboxService
    from lotuspond.execution.leases import LeaseService
    from lotuspond.execution.progression import InterpreterV1
    from lotuspond.execution.projections import EventProjection
    from lotuspond.execution.publication import ExecutionDependencies
    from lotuspond.execution.reconciliation import ReconciliationService
    from lotuspond.execution.recovery import RecoveryFence
    from lotuspond.execution.scheduler import Scheduler
    from lotuspond.execution.sweeper import ExecutionSweeper
    from lotuspond.fleet.authority import FleetDependencies
    from lotuspond.fleet.catalog import InstalledCatalog
    from lotuspond.security import RateLimiter

    engine = engine or engine_for(settings_for(world))  # the production API's own pooling
    entitlement = worlds.FileEntitlement(world)
    recovery = worlds.FileRecovery(world)
    dependencies = ExecutionDependencies(authority=worlds.FileAuthority(world), test_mode=True)
    budgets = BudgetService(
        entitlement=entitlement,
        tenant_policy=worlds.FileTenant(entitlement),
        charges=entitlement,
        recovery=recovery,
        installation_id=worlds.INSTALLATION,
        deployments=dependencies,
        test_mode=True,
    )
    keys, access = worlds.FileKeys(world), worlds.FileAccess(world)
    store = ArtifactStore(keys, access, test_mode=True)
    policy = ArtifactPolicy(
        classification="customer-private",
        source_id=None,
        source_version=None,
        retain_until_ms=worlds.MAX_MS,
        access_policy_id=WORKSPACE_ACCESS_POLICY,
        key_id="conformance",
    )
    fleet = FleetDependencies(
        recovery=recovery,
        catalog=InstalledCatalog(),
        installation_id=worlds.INSTALLATION,
        limiter=RateLimiter(Settings(env="test")),
    )
    fence = RecoveryFence(fleet)
    activities = ActivityService(
        fleet=fleet,
        budgets=budgets,
        artifacts=store,
        artifact_policy=policy,
        trust=worlds.FileActivityTrust(world),
        signer=worlds.FileSigner(world),
        demand=worlds.Demand(world),
        installation_id=worlds.INSTALLATION,
        test_mode=True,
    )
    leases = LeaseService(activities)
    interpreter = InterpreterV1(
        artifacts=store,
        artifact_policy=policy,
        budgets=budgets,
        recovery=fence,
        installation_id=worlds.INSTALLATION,
        test_mode=True,
    )
    sweeper = ExecutionSweeper(
        leases=leases,
        recovery=fence,
        scheduler=Scheduler(budgets, worlds.Demand(world), test_mode=True),
    )
    controls = ControlService(
        budgets=budgets,
        artifacts=store,
        artifact_policy=policy,
        installation_id=worlds.INSTALLATION,
        recovery=fence,
    )
    inbox = InboxService(budgets=budgets, artifacts=store, artifact_policy=policy)
    reconciliation = ReconciliationService(activities)
    context = ContextSource(store)
    services = ExecutionServices(
        artifacts=store,
        artifact_policy=policy,
        budgets=budgets,
        dependencies=dependencies,
        admission=AdmissionService(
            budgets=budgets, artifacts=store, artifact_policy=policy, interpreter_id=INTERPRETER
        ),
        inbox=inbox,
        controls=controls,
        activities=activities,
        leases=leases,
        reconciliation=reconciliation,
        context=context,
    )
    return SimpleNamespace(
        engine=engine,
        sessions=sessionmaker(engine, expire_on_commit=False),
        services=services,
        fleet=fleet,
        fence=fence,
        store=store,
        policy=policy,
        budgets=budgets,
        dependencies=dependencies,
        activities=activities,
        leases=leases,
        controls=controls,
        reconciliation=reconciliation,
        events=EventProjection(
            artifacts=store,
            artifact_policy=policy,
            installation_id=worlds.INSTALLATION,
            recovery=fence,
        ),
        execution_engine=PostgresExecutionEngine(
            interpreter, sweeper, fence, interpreter_ids=[INTERPRETER], test_mode=True
        ),
    )


# ------------------------------------------------------------------ TLS


def certificates(directory: Path):
    """An ephemeral synthetic localhost CA and server key; never production trust."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    ca, private = directory / "synthetic-ca.pem", directory / "synthetic-key.pem"
    if ca.exists():
        return ca, private
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-conformance")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    worlds.write_private(
        private,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    worlds.write_private(ca, cert.public_bytes(serialization.Encoding.PEM))
    return ca, private


# ------------------------------------------------------------------ child processes


# ------------------------------------------------------------------ rollback injection

# Which request kind a commit belongs to, in the API process.
POINTS = (
    ("POST", "/executions", "admission"),
    ("POST", "/signals", "signal"),
    ("POST", "/runtime/activities/claim", "claim"),
    ("POST", "/runtime/activities/results", "result"),
)
_point = contextvars.ContextVar("fault_point", default=None)
_lost = contextvars.ContextVar("fault_lost", default=None)


def point_of(method, path):
    for verb, suffix, point in POINTS:
        if method == verb and path.endswith(suffix):
            return point
    return None


def install_faults(world, role):
    """Test-only: one armed commit of one kind fails before it, or is lost after it.

    `faults.json` in the world names the point (`admission`, `signal`, `claim`,
    `result` in the API; `transition` in an orchestrator), `before` or `after`, and how
    many commits remain armed. Before: the commit raises, so the transaction rolls
    back, as a database failure would. After: the commit holds, but the API drops its
    answer (503), or the orchestrator process dies at once, as a crash would.

    An orchestrator commit is a transition only when it writes execution events, so
    an idle poll or an empty pass never takes the fault. Concurrent commits decide
    under a file lock, so one armed commit fires once.
    """
    import fcntl

    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from lotuspond.models import ExecutionEvent

    path, guard = world.path("faults.json"), world.path("faults.lock")

    def writes_events(session):
        return session.info.get("wrote_events") or any(
            isinstance(item, ExecutionEvent) for item in session.new
        )

    def armed(when, session):
        if role == "api":
            point = _point.get()
        elif role == "orchestrator" and writes_events(session):
            point = "transition"
        else:
            point = None
        if point is None or not path.exists():
            return False
        with os.fdopen(os.open(guard, os.O_RDWR | os.O_CREAT, 0o600), "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                spec = json.loads(path.read_text())
            except (OSError, ValueError):  # disarmed meanwhile
                return False
            if (
                spec.get("point") != point
                or spec.get("when") != when
                or spec.get("remaining", 0) < 1
            ):
                return False
            worlds.write_private(
                path,
                {**spec, "remaining": spec["remaining"] - 1, "fired": spec.get("fired", 0) + 1},
            )
            return True

    @event.listens_for(Session, "after_flush")
    def flushed(session, context):
        if any(isinstance(item, ExecutionEvent) for item in session.new):
            session.info["wrote_events"] = True

    @event.listens_for(Session, "before_commit")
    def before(session):
        if armed("before", session):
            session.info.pop("wrote_events", None)
            raise OperationalError("injected rollback before commit", None, Exception())

    @event.listens_for(Session, "after_commit")
    def after(session):
        fire = armed("after", session)
        session.info.pop("wrote_events", None)
        if fire:
            if role == "orchestrator":
                os._exit(9)  # a crash right after the commit
            lost = _lost.get()
            if lost is not None:
                lost.append(True)

    @event.listens_for(Session, "after_rollback")
    def rolled_back(session):
        session.info.pop("wrote_events", None)


class AccessLog:
    """Test-only: method, path, status and closed error code of each request, to stderr.

    Paths carry record IDs only; bodies and headers are never written.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        seen = {}
        _point.set(point_of(scope["method"], scope["path"]))
        lost = []
        _lost.set(lost)

        async def observed(message):
            if lost:
                # The commit held; its answer never reaches the client (test-only).
                if message["type"] == "http.response.start":
                    seen["status"] = 503
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 503,
                            "headers": [(b"content-type", b"application/json")],
                        }
                    )
                elif not message.get("more_body"):
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b'{"error":"authority_unavailable","message":"authority_unavailable"}',
                        }
                    )
                return
            if message["type"] == "http.response.start":
                seen["status"] = message["status"]
            elif message["type"] == "http.response.body" and seen.get("status", 200) >= 400:
                body = message.get("body", b"")[:200]
                try:
                    seen["error"] = json.loads(body).get("error")
                except (ValueError, AttributeError):
                    pass
            await send(message)

        await self.app(scope, receive, observed)
        if seen.get("status", 0) >= 400:
            print(
                f"{scope['method']} {scope['path']} {seen['status']} {seen.get('error')}",
                file=sys.stderr,
                flush=True,
            )


def serve_api(world, port=0, workers=1) -> None:
    """The API over TLS on a localhost port; `api.json` tells the parent.

    Ephemeral at first; a restarted API asks for its earlier port, so clients that
    already know it reach it again, as they would a restarted service. `workers` above
    1 forks that many API processes onto the one listening socket (tier 3), each with
    its own service graph and connection pool, as a multi-process deployment runs.
    """
    certificates(world.root)  # before any fork: every process serves the same pair
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    announce = {"port": listener.getsockname()[1], "pid": os.getpid()}
    if workers == 1:
        _serve_on(world, listener, lambda: world.write("api", announce))
        return
    ready, signalled = os.pipe()
    children = []
    for _ in range(workers):
        child = os.fork()
        if child == 0:  # pragma: no cover - runs in the forked API process
            os.close(ready)

            def report():
                os.write(signalled, b"1")
                os.close(signalled)  # so a sibling that never starts reads as an end

            try:
                _serve_on(world, listener, report)
            finally:
                os._exit(1)
        children.append(child)
    os.close(signalled)
    started = 0
    while started < workers:
        chunk = os.read(ready, workers)
        if not chunk:  # every API process that could still report has ended
            break
        started += len(chunk)

    def end(signum, frame=None):
        """Stop and reap every API process, so none outlives the parent."""
        for child in children:
            try:
                os.kill(child, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for child in children:
            try:
                os.waitpid(child, 0)
            except ChildProcessError:
                pass
        if frame is not None:  # then end by the same signal, as one process would
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, end)
    if started == workers:  # never announce an API missing some of its processes
        world.write("api", {**announce, "workers": children})
    os.wait()  # one API process ending ends the API
    end(signal.SIGTERM)


def _serve_on(world, listener, started) -> None:
    import uvicorn

    from lotuspond.app import create_app

    install_faults(world, "api")
    if os.environ.get(CURSOR_LOG):
        record_cursor_waits("api", Path(os.environ[CURSOR_LOG]))
    graph = build(world)
    app = create_app(
        settings_for(world),
        engine=graph.engine,
        fleet_dependencies=graph.fleet,
        execution_services=graph.services,
    )
    app = AccessLog(app)
    ca, key = certificates(world.root)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="warning",
            access_log=False,
            ssl_certfile=str(ca),
            ssl_keyfile=str(key),
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    while not server.started and thread.is_alive():
        time.sleep(0.02)
    if thread.is_alive():  # never announce a server that failed to start
        started()
    thread.join()


CURSOR_LOG = "LOTUSPOND_CONFORMANCE_CURSOR_LOG"
CYCLE_LOG = "LOTUSPOND_CONFORMANCE_CYCLE_LOG"


def _append(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a") as log:
        log.write(json.dumps(value) + "\n")


def record_cursor_waits(role, path: Path):
    """Test-only (tier 3): how long each statement locking the scheduler cursor took.

    That statement does no other work, so its time is the lock wait. Every
    acquisition is recorded, in every process, including those a lock timeout ends.
    """
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def started(connection, cursor, statement, parameters, context, executemany):
        if "execution_scheduler_cursors" in statement and "FOR UPDATE" in statement.upper():
            connection.info["cursor_lock_started"] = time.monotonic()

    def finished(connection, failed):
        began = connection.info.pop("cursor_lock_started", None)
        if began is not None:
            elapsed = round((time.monotonic() - began) * 1000, 2)
            _append(path, {"role": role, "ms": elapsed, "failed": failed})

    @event.listens_for(Engine, "after_cursor_execute")
    def done(connection, cursor, statement, parameters, context, executemany):
        finished(connection, False)

    @event.listens_for(Engine, "handle_error")
    def refused(context):
        if context.connection is not None:
            finished(context.connection, True)


class FirstCycle:
    """Test-only: tells the parent when this worker's first cycle has completed.

    A worker whose first fenced cycle fails refuses to start, so a case that
    disrupts the database or the authority waits for this before it does.
    """

    def __init__(self, engine, marker: Path):
        self._engine, self._marker = engine, marker

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def _completed(self, report):
        if not self._marker.exists():
            worlds.write_private(self._marker, "ready")
        return report

    def _timed(self, method, args, kwargs):
        began = time.monotonic()
        report = getattr(self._engine, method)(*args, **kwargs)
        if os.environ.get(CYCLE_LOG):  # tier 3: every cycle's duration and its report
            seconds = round(time.monotonic() - began, 4)
            counts = report.model_dump() if hasattr(report, "model_dump") else dict(report)
            entry = {"cycle": method, "at": time.time(), "s": seconds, "report": counts}
            _append(os.environ[CYCLE_LOG], entry)
        return self._completed(report)

    def progress(self, *args, **kwargs):
        return self._timed("progress", args, kwargs)

    def sweep(self, *args, **kwargs):
        return self._timed("sweep", args, kwargs)


PRIVILEGE_LOG = "LOTUSPOND_CONFORMANCE_PRIVILEGE_LOG"
_LOCKING = re.compile(r"\bFOR (?:NO KEY UPDATE|UPDATE|KEY SHARE|SHARE)(?: OF ([\w, ]+))?", re.I)


def record_privileges(role, path: Path):
    """Test-only: every table this worker reads, writes or row-locks.

    Appends `{role, privilege, table}` lines the first time each is seen, so a run
    of the whole suite measures the grants a worker role needs. PostgreSQL requires
    UPDATE privilege on a table whose rows a statement locks, so a lock counts as
    `LOCK` and the operator grant follows from it.
    """
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    seen, guard = set(), threading.Lock()

    @event.listens_for(Engine, "before_cursor_execute")
    def observe(connection, cursor, statement, parameters, context, executemany):
        sql = " ".join(statement.split())
        found = set()
        written = re.match(r"(INSERT INTO|UPDATE|DELETE FROM)\s+(\w+)", sql, re.I)
        if written:
            found.add((written.group(1).split()[0].upper(), written.group(2)))
        tables = re.findall(r"\b(?:FROM|JOIN)\s+(\w+)", sql, re.I)
        found |= {("SELECT", table) for table in tables}
        locking = _LOCKING.search(sql)
        if locking:
            named = locking.group(1)
            locked = [t.strip() for t in named.split(",")] if named else tables
            found |= {("LOCK", table) for table in locked}
        with guard:
            new = found - seen
            seen.update(new)
        for privilege, table in sorted(new):
            _append(path, {"role": role, "privilege": privilege, "table": table})


def serve_worker(world, role, worker_id, interpreters, timeout_ms, user=None) -> int:
    from sqlalchemy.engine import make_url

    from lotuspond.execution_worker import ExecutionWorkerSettings, serve

    install_faults(world, role)
    if os.environ.get(CURSOR_LOG):
        record_cursor_waits(role, Path(os.environ[CURSOR_LOG]))
    if os.environ.get(PRIVILEGE_LOG):
        record_privileges(role, Path(os.environ[PRIVILEGE_LOG]))
    graph = build(world)
    url = scoped_url(world)
    if user is not None:  # ENG-4: a least-privilege worker role on the disposable cluster
        url = make_url(url).set(username=user).render_as_string(hide_password=False)
    settings = ExecutionWorkerSettings(
        database_url=url,
        worker_id=worker_id,
        role=role,
        interpreter_ids=tuple(interpreters),
        poll_min_ms=50,
        poll_max_ms=250,
        statement_timeout_ms=timeout_ms,
        lock_timeout_ms=timeout_ms,
        idle_in_transaction_timeout_ms=timeout_ms,
        shutdown_seconds=10,
    )
    marker = worlds.private_directory(world.root / "ready") / worker_id
    return serve(settings, engine=FirstCycle(graph.execution_engine, marker))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="conformance-harness")
    commands = parser.add_subparsers(dest="command", required=True)
    api = commands.add_parser("api")
    api.add_argument("world")
    api.add_argument("--port", type=int, default=0)
    api.add_argument("--workers", type=int, default=1)
    worker = commands.add_parser("worker")
    worker.add_argument("world")
    worker.add_argument("--role", choices=("orchestrator", "sweeper"), required=True)
    worker.add_argument("--worker-id", required=True)
    worker.add_argument("--interpreter", action="append", default=None)
    worker.add_argument("--timeout-ms", type=int, default=15000)
    worker.add_argument("--database-user")
    args = parser.parse_args(argv)
    import logging

    # Fixed codes only ever reach these logs (the engine never logs values).
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")
    world = worlds.World(args.world)
    if args.command == "api":
        serve_api(world, args.port, args.workers)
        return 0
    return serve_worker(
        world,
        args.role,
        args.worker_id,
        args.interpreter or [INTERPRETER],
        args.timeout_ms,
        args.database_user,
    )


if __name__ == "__main__":
    sys.exit(main())
