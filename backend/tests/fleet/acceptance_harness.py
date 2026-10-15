"""Explicit fixture application, copied outside checkout by fleet-smoke.

Never imported by the production API. All authorities below are test-only.
"""

from __future__ import annotations

import hmac
import importlib.metadata as metadata
import ipaddress
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

# Explicitly supplied test utilities, outside every installed production package.
sys.path.insert(0, str(Path(__file__).parent))
import command_fixture as commands
import legacy_fixture as legacy
import tls_fixture as tls
import uvicorn
from fastapi.testclient import TestClient
from lotuspond_runtime.invocation import fixture_pin
from lotuspond_runtime.protocol import RuntimeIdentity, RuntimeObservation
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from lotuspond import initialize, models
from lotuspond.app import create_app
from lotuspond.config import Settings
from lotuspond.database import sessions_for
from lotuspond.fleet.authority import FleetDependencies, RecoverySnapshot
from lotuspond.fleet.catalog import InstalledCatalog
from lotuspond.fleet.commands import CommandDependencies, CommandService


def wait_for(probe, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("fixture condition deadline")


def private(path, value):
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(json.dumps(value) if not isinstance(value, str) else value)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


class Recovery:
    """Not stored in, or restored with, the application schema."""

    epoch = 7
    active = True

    def current(self, installation_id):
        return RecoverySnapshot("test-installation", self.epoch, self.active)


class Records:
    pinned = None

    def load(self, db, command):
        assert command.intent_sha256 == self.case.command.intent_sha256
        return self.pinned


class Dispatch:
    """One independent private grant source shared with the installed worker."""

    def __init__(self, path):
        self.path = path
        self.allowed = True

    @property
    def allowed(self):
        return json.loads(self.path.read_text())["allowed"] is True

    @allowed.setter
    def allowed(self, value):
        temporary = self.path.with_suffix(".tmp")
        private(temporary, {"allowed": value})
        temporary.replace(self.path)

    def authorize(self, identity, digest, records):
        from lotuspond_runtime.errors import FleetError

        if not self.allowed:
            raise FleetError("forbidden")


class Trust:
    def current(self, installation_id):
        from lotuspond_runtime.protocol import CommandTrustSnapshot

        return CommandTrustSnapshot(1, self.case.trust, time.time_ns() // 1000000 + 60000)


class ObserveAndLose:
    """Only the explicit test server records milestones/drops a committed response."""

    def __init__(self, app):
        self.app = app
        self.lose_activate = False
        self.events = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        status = None
        chunks = []
        lose = self.lose_activate and path.endswith("/credentials/activate")

        async def observed(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            if lose:
                chunks.append(message)
            else:
                await send(message)

        await self.app(scope, receive, observed)
        self.events.append((path, status, time.monotonic()))
        if lose:
            assert status == 200
            self.lose_activate = False
            # The route transaction has committed. The one-time body never reaches client.
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})


def main():
    root = Path(sys.argv[3])
    control_file = root / "test-control.json"
    control = json.loads(control_file.read_text()) if control_file.exists() else {}
    scenario = control.get("scenario", "full")
    raw = Path(sys.argv[1]).read_text().strip()
    url = make_url(raw)
    assert url.host == "127.0.0.1" and url.database == "lotuspond_platform_test" and not url.query
    admin = create_engine(url, hide_parameters=True)
    with admin.connect() as db:
        name, address, port, directory = db.execute(
            text(
                "SELECT current_database(), inet_server_addr()::text, inet_server_port(), current_setting('data_directory')"
            )
        ).one()
        assert name == "lotuspond_platform_test" and ipaddress.ip_interface(address).ip.is_loopback
        assert (
            port == (url.port or 5432) and Path(directory).resolve() == Path(sys.argv[2]).resolve()
        )
    schema, marker = "fleet_smoke_" + uuid4().hex, uuid4().hex
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
        db.execute(text(f"COMMENT ON SCHEMA \"{schema}\" IS '{marker}'"))
    with admin.connect() as db:
        namespace = db.execute(
            text("SELECT oid, nspowner FROM pg_namespace WHERE nspname=:schema"), {"schema": schema}
        ).one()
    private(
        root / "schema-ownership.json",
        {"schema": schema, "oid": namespace.oid, "owner": namespace.nspowner},
    )
    scoped_url = url.set(query={"options": f"-c search_path={schema}"})
    engine = create_engine(scoped_url, hide_parameters=True)
    processes = []
    server = thread = listener = None
    cleanup = False
    cases = {}
    try:
        for package, version in (
            ("lotuspond-api", "0.2.0"),
            ("lotuspond-runtime", "0.1.0"),
            ("lotuspond-sdk", "0.2.0"),
        ):
            distribution = metadata.distribution(package)
            assert distribution.version == version
            direct = distribution.read_text("direct_url.json")
            assert not direct or not json.loads(direct).get("dir_info", {}).get("editable", False)
            assert Path(distribution.locate_file("")).is_relative_to(Path(sys.prefix))
        private(
            root / "installed-origins.json",
            {
                name: {
                    "version": metadata.version(name),
                    "location": str(metadata.distribution(name).locate_file("")),
                    "direct_url": json.loads(
                        metadata.distribution(name).read_text("direct_url.json") or "{}"
                    ),
                }
                for name in ("lotuspond-api", "lotuspond-sdk", "lotuspond-runtime")
            },
        )
        cases["noneditable_installed_api_sdk_runtime"] = "passed"
        with engine.connect() as db:
            db.exec_driver_sql((Path(__file__).parent / "schema-v12.sql").read_text())
        with engine.begin() as db:
            legacy.seed(db)
            before = legacy.rows(db)
        settings = Settings(
            env="test",
            database_url=scoped_url.render_as_string(hide_password=False),
            fleet_enabled=True,
            fleet_installation_id="test-installation",
            registration_enabled=True,
            origin="http://testserver",
        )
        initialize.migrate(settings)
        with engine.connect() as db:
            after = legacy.rows(db)
            assert all(after[key] == value for key, value in before.items())
            assert db.scalar(select(models.SchemaVersion.version)) == 13
        cases["v12_upgrade_preserves_environment_evaluation_registry_mfa"] = "passed"
        recovery, records, dispatch, trust = (
            Recovery(),
            Records(),
            Dispatch(root / "independent-grant.json"),
            Trust(),
        )
        deps = FleetDependencies(
            recovery,
            InstalledCatalog.fixture(
                fixture_pin(), test_mode=True, secret_bindings=frozenset({("fixture-binding", 1)})
            ),
        )
        app = create_app(
            settings,
            engine=engine,
            fleet_dependencies=deps,
            command_dependencies=CommandDependencies(dispatch, records, trust),
        )
        production = settings.model_copy(update={"env": "production"})
        for kwargs in (
            {"fleet_dependencies": deps},
            {"command_dependencies": CommandDependencies(dispatch, records, trust)},
        ):
            try:
                create_app(production, engine=engine, **kwargs)
            except ValueError:
                pass
            else:
                raise AssertionError("production accepted fixture hooks")
        cases["production_fixture_hooks_refused"] = "passed"
        factory = sessions_for(engine)
        observer = ObserveAndLose(app)
        ca, key = tls.certificates(root)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(32)
        origin = f"https://localhost:{listener.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(
                observer,
                log_level="critical",
                access_log=False,
                ssl_certfile=str(ca),
                ssl_keyfile=str(key),
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        wait_for(lambda: server.started)
        if control.get("pause_at") == "api":
            private(
                root / "interrupt-ready.json",
                {"stage": "active-api", "pid": os.getpid(), "api_port": listener.getsockname()[1]},
            )
            # Explicit bounded test-only cancellation checkpoint, never a production hook.
            threading.Event().wait(180)
            raise TimeoutError("fixture cancellation checkpoint expired")
        with TestClient(app, headers={"origin": "http://testserver"}) as management:
            response = management.post(
                "/api/v1/auth/register",
                json={
                    "email": "fleet-acceptance@example.com",
                    "name": "Owner",
                    "password": "synthetic-qualification-password",
                    "account_type": "organization",
                    "workspace": "Fixture",
                },
            )
            assert response.status_code == 201
            workspace = response.json()["workspaceId"]
            management.headers["x-lotuspond-workspace"] = workspace
            project, environment = str(uuid4()), str(uuid4())
            with factory.begin() as db:
                db.add(models.Project(id=project, workspace_id=workspace, name="Fixture"))
                db.flush()
                db.add(
                    models.Environment(
                        id=environment,
                        workspace_id=workspace,
                        project_id=project,
                        name="Local",
                        kind="local",
                    )
                )
            prefix = f"/api/v1/projects/{project}/environments/{environment}"

            def post(path, body, status=201):
                response = management.post(
                    prefix + path, json=body, headers={"Idempotency-Key": uuid4().hex}
                )
                assert response.status_code == status
                return response.json()

            pool = post("/runtime-pools", {"name": "Fixture"})["id"]
            post(
                f"/runtime-pools/{pool}/policies",
                {
                    "connectors": [fixture_pin().model_dump()],
                    "platforms": ["linux"],
                    "connection_ids": [],
                    "max_instances": 8,
                    "max_concurrent_invocations": 1,
                },
            )
            empty = {"platform": "linux", "connectors": []}

            def config_for(label):
                state = root / (label + "-state")
                state.mkdir(mode=0o700)
                cfg = root / (label + "-config.json")
                private(
                    cfg,
                    {
                        "origin": origin,
                        "ca_file": str(ca),
                        "state_directory": str(state),
                        "installation_id": "test-installation",
                        "observation": empty,
                        "mode": "test",
                    },
                )
                enrollment = post(f"/runtime-pools/{pool}/enrollments", empty)
                token = root / (label + "-enrollment")
                private(token, enrollment["token"])
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "lotuspond_runtime",
                        "enroll",
                        "--config",
                        str(cfg),
                        "--token-file",
                        str(token),
                    ],
                    cwd=root,
                    capture_output=True,
                    timeout=12,
                )
                assert result.returncode == 0
                # No token reaches retained process stdout.
                assert enrollment["token"].encode() not in result.stdout + result.stderr
                instance = json.loads((state / "state.json").read_text())["instance_id"]
                return cfg, state, instance

            def start_cli(cfg):
                process = subprocess.Popen(
                    [sys.executable, "-I", "-m", "lotuspond_runtime", "run", "--config", str(cfg)],
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                processes.append(process)
                return process

            def seen(path, after):
                return any(
                    p == "/api/v1/runtime/" + path and status == 200 and when > after
                    for p, status, when in observer.events
                )

            if scenario == "full":
                normal_cfg, _, normal_id = config_for("normal")
                started = time.monotonic()
                normal = start_cli(normal_cfg)
                wait_for(lambda: seen("commands/poll", started))
                wait_for(lambda: seen("heartbeat", started), seconds=28)
                assert normal.poll() is None
                cases["ordinary_cli_acquire_poll_first_heartbeat"] = "passed"
                post(f"/runtime-instances/{normal_id}/revoke", {}, 200)
                assert normal.wait(timeout=25) == 3
                assert normal.communicate(timeout=1) == (b"unavailable\n", b"")
                cases["ordinary_cli_observes_revocation"] = "passed"
                rotate_cfg, rotate_state, rotate_id = config_for("rotation")
                with factory.begin() as db:
                    credential = db.scalar(
                        select(models.RuntimeCredential).where(
                            models.RuntimeCredential.instance_id == rotate_id,
                            models.RuntimeCredential.status == "active",
                        )
                    )
                    original_expiry = credential.expires_at_ms
                    credential.expires_at_ms = time.time_ns() // 1000000 + 240000
                    assert credential.expires_at_ms < original_expiry
                observer.lose_activate = True
                failed = start_cli(rotate_cfg)
                assert failed.wait(timeout=15) == 3
                failed.communicate(timeout=1)
                pending = json.loads((rotate_state / "state.json").read_text())
                assert pending["pending_token"] is not None and pending["pending_version"] == 2
                with factory() as db:
                    credentials = list(
                        db.scalars(
                            select(models.RuntimeCredential).where(
                                models.RuntimeCredential.instance_id == rotate_id
                            )
                        )
                    )
                    assert {c.version: c.status for c in credentials} == {1: "revoked", 2: "active"}
                    expiry = next(c.expires_at_ms for c in credentials if c.version == 2)
                    old_session = db.scalar(
                        select(models.RuntimeSession).where(
                            models.RuntimeSession.instance_id == rotate_id,
                            models.RuntimeSession.status == "active",
                        )
                    )
                    old_generation, lease_expiry = old_session.generation, old_session.expires_at_ms
                restarted_at = time.monotonic()
                restart = start_cli(rotate_cfg)
                time.sleep(0.25)
                assert restart.poll() is None
                with factory() as db:
                    assert (
                        db.scalar(
                            select(models.RuntimeSession.generation).where(
                                models.RuntimeSession.instance_id == rotate_id,
                                models.RuntimeSession.status == "active",
                            )
                        )
                        == old_generation
                    )
                wait_for(lambda: seen("heartbeat", restarted_at), seconds=90)
                assert restart.poll() is None
                with factory() as db:
                    session = db.scalar(
                        select(models.RuntimeSession).where(
                            models.RuntimeSession.instance_id == rotate_id,
                            models.RuntimeSession.status == "active",
                        )
                    )
                    assert session.generation == old_generation + 1
                    assert time.time_ns() // 1000000 >= lease_expiry
                    assert (
                        db.scalar(
                            select(models.RuntimeCredential.expires_at_ms).where(
                                models.RuntimeCredential.instance_id == rotate_id,
                                models.RuntimeCredential.version == 2,
                            )
                        )
                        == expiry
                    )
                recovered = json.loads((rotate_state / "state.json").read_text())
                assert (
                    recovered["active_token"] == pending["pending_token"]
                    and recovered["pending_token"] is None
                )
                post(f"/runtime-instances/{rotate_id}/revoke", {}, 200)
                assert restart.wait(timeout=25) == 3
                restart.communicate(timeout=1)
                cases[
                    "ordinary_cli_forced_startup_rotation_lost_activation_response_restart_natural_lease"
                ] = "passed"

            # Fixture capability is a distinct instance; never widen either CLI ceiling.
            observation = {"platform": "linux", "connectors": [fixture_pin().model_dump()]}
            enrollment = post(f"/runtime-pools/{pool}/enrollments", observation)
            worker_cfg = root / "worker-private.json"
            private(
                worker_cfg,
                {
                    "origin": origin,
                    "ca_file": str(ca),
                    "token": enrollment["token"],
                    "observation": observation,
                    "grant_file": str(dispatch.path),
                },
            )
            ready, proceed = root / "worker-ready.json", root / "worker-proceed.json"
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).with_name("worker.py")),
                    str(worker_cfg),
                    str(root),
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            processes.append(worker)
            wait_for(ready.exists)
            identity = RuntimeIdentity.model_validate(json.loads(ready.read_text()))
            connection = post("/connections", {"name": "Fixture"})["id"]
            configuration = commands.command_case().configuration.model_copy(
                update={"connector": fixture_pin()}
            )
            post(f"/connections/{connection}/versions", configuration.model_dump(mode="json"))
            post(
                f"/runtime-pools/{pool}/policies",
                {
                    "connectors": [fixture_pin().model_dump()],
                    "platforms": ["linux"],
                    "connection_ids": [connection],
                    "max_instances": 8,
                    "max_concurrent_invocations": 1,
                },
            )
            case = commands.command_case(
                identity=identity,
                now=time.time_ns() // 1000000,
                connection_id=connection,
                configuration=configuration,
            )
            records.case = trust.case = case
            records.pinned = commands.records(case)
            value = commands.delivery(case)
            service = app.state.command_service
            assert isinstance(service, CommandService)
            with factory.begin() as db:
                admitted = service.admit(db, identity, value.command_bytes, value.signature_bytes)
                assert admitted.status == "admitted"
            private(
                proceed,
                {
                    "identity": identity.model_dump(),
                    "connection_id": connection,
                    "configuration": configuration.model_dump(mode="json"),
                    "now": case.now,
                    "trust": case.trust.model_dump(mode="json"),
                },
            )
            positive = root / "worker-positive.json"
            wait_for(lambda: positive.exists() or worker.poll() is not None)
            assert worker.poll() is None and positive.exists(), (
                "restore worker exited before live-sink checkpoint"
            )
            positive_result = json.loads(positive.read_text())
            assert positive_result["effect_count"] == 1
            assert positive_result["secret_resolution_count"] >= 1

            def denied_effect(number, phase, proof_value, identity_value, digest):
                private(
                    root / f"worker-phase-{number}.json",
                    {
                        "phase": phase,
                        "token": proof_value["token"],
                        "session": proof_value["session"],
                        "identity": identity_value.model_dump(mode="json"),
                        "command_sha256": digest,
                    },
                )
                result_path = root / f"worker-phase-{number}-result.json"
                wait_for(lambda: result_path.exists() or worker.poll() is not None)
                assert worker.poll() is None and result_path.exists()
                observed = json.loads(result_path.read_text())
                assert observed["status"] == "denied" and observed["sink_live"] is True
                assert (
                    observed["sink_after"]
                    == observed["sink_before"]
                    == positive_result["effect_count"]
                )
                assert observed["secret_resolutions_after"] == observed["secret_resolutions_before"]
                assert observed["authority_denial"] == ("dispatch" if number == 2 else "api")
                return observed

            with factory() as db:
                admission = db.scalar(
                    select(models.RuntimeCommandAdmission).where(
                        models.RuntimeCommandAdmission.instance_id == identity.instance_id
                    )
                )
                assert admission.status == "claimed"
                snapshot = {
                    model: [dict(row) for row in db.execute(select(model.__table__)).mappings()]
                    for model in (
                        models.RuntimeInstance,
                        models.RuntimeCredential,
                        models.RuntimeSession,
                        models.RuntimeCommandAdmission,
                    )
                }
            cases["separate_fixture_installed_verify_claim_and_child_secret_tls"] = "passed"
            # Fence before restoring durable machine/admission state. Authority/grants stay outside.
            recovery.active = False
            dispatch.allowed = False
            post(f"/runtime-instances/{identity.instance_id}/revoke", {}, 200)
            with factory.begin() as db:
                for model, rows in snapshot.items():
                    for row in rows:
                        db.execute(
                            model.__table__.update()
                            .where(model.__table__.c.id == row["id"])
                            .values(**row)
                        )
            worker_proof = root / "worker-proof.json"
            proof = json.loads(worker_proof.read_text())
            from lotuspond_runtime.client import RuntimeClient
            from lotuspond_runtime.control_transport import ControlTransport
            from lotuspond_runtime.errors import FleetError
            from lotuspond_sdk.executable.wire import wire_digest
            from pydantic import SecretStr

            client = RuntimeClient(ControlTransport(origin, ca, mode="test"))
            restore_observations = []
            for number, phase in enumerate(("fenced", "fresh_epoch_without_grants")):
                if phase != "fenced":
                    recovery.epoch += 1
                    recovery.active = True
                try:
                    client.claim(
                        SecretStr(proof["token"]),
                        SecretStr(proof["session"]),
                        case.command.command_id,
                        wire_digest(case.command),
                    )
                except FleetError:
                    pass
                else:
                    raise AssertionError("restored proof accepted")
                restore_observations.append(
                    denied_effect(number, phase, proof, identity, wire_digest(case.command))
                )
            # Fresh enrollment/session under the new epoch still lacks a business grant.
            fresh_enrollment = post(f"/runtime-pools/{pool}/enrollments", observation)
            fresh_credential = client.enroll(
                SecretStr(fresh_enrollment["token"]), RuntimeObservation.model_validate(observation)
            )
            fresh_session = client.acquire(fresh_credential.token)
            fresh = fresh_session.identity
            assert fresh.recovery_epoch == recovery.epoch
            fresh_case = commands.command_case(
                identity=fresh,
                now=time.time_ns() // 1000000,
                connection_id=connection,
                configuration=configuration,
            )
            records.case = trust.case = fresh_case
            records.pinned = commands.records(fresh_case)
            fresh_delivery = commands.delivery(fresh_case)
            with factory.begin() as db:
                try:
                    service.admit(
                        db, fresh, fresh_delivery.command_bytes, fresh_delivery.signature_bytes
                    )
                except FleetError as error:
                    assert error.code == "forbidden"
                else:
                    raise AssertionError("new epoch authorized business effect")
            assert client.poll(fresh_credential.token, fresh_session.secret) is None
            restore_observations.append(
                denied_effect(
                    2,
                    "fresh_identity_without_grant",
                    {
                        "token": fresh_credential.token.get_secret_value(),
                        "session": fresh_session.secret.get_secret_value(),
                    },
                    fresh,
                    wire_digest(fresh_case.command),
                )
            )
            private(root / "worker-finish.json", {"finish": True})
            assert worker.wait(timeout=8) == 0
            output, error = worker.communicate(timeout=1)
            assert output == b"fixture_worker_passed\n" and not error
            measured = json.loads((root / "worker-result.json").read_text())
            restored_state_new_effect_count = (
                measured["effect_count"] - positive_result["effect_count"]
            )
            assert restored_state_new_effect_count == 0
            assert measured["secret_resolution_count"] == positive_result["secret_resolution_count"]
            assert len(restore_observations) == 3
            client.close()
            cases["restored_machine_records_fenced_new_epoch_no_regrant_no_new_effect"] = "passed"
    finally:
        process_cleanup = True
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            process.communicate(timeout=1)
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                process_cleanup = False
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(5)
            process_cleanup = process_cleanup and not thread.is_alive()
        if listener is not None:
            listener.close()
        engine.dispose()
        with admin.begin() as db:
            observed = db.scalar(
                text(
                    "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname=:schema"
                ),
                {"schema": schema},
            )
            current_namespace = db.execute(
                text("SELECT oid, nspowner FROM pg_namespace WHERE nspname=:schema"),
                {"schema": schema},
            ).one()
            assert current_namespace == namespace
            assert hmac.compare_digest(observed, marker)
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        with admin.connect() as db:
            absent = (
                db.scalar(
                    text("SELECT 1 FROM pg_namespace WHERE nspname=:schema"), {"schema": schema}
                )
                is None
            )
        admin.dispose()
        cleanup = process_cleanup and absent
        private(root / "cleanup.json", {"cleanup_confirmed": cleanup})
    assert cleanup
    private(
        root / "result.json",
        {
            "installed_runtime_version": metadata.version("lotuspond-runtime"),
            "installed_sdk_version": metadata.version("lotuspond-sdk"),
            "installed_api_version": metadata.version("lotuspond-api"),
            "python_version": sys.version,
            "cases": cases,
            "scenario": scenario,
            "restore_positive_effect_count": positive_result["effect_count"],
            "restore_final_effect_count": measured["effect_count"],
            "restore_phase_observations": restore_observations,
            "restored_state_new_effect_count": restored_state_new_effect_count,
            "cleanup_confirmed": cleanup,
        },
    )


if __name__ == "__main__":

    def interrupted(signum, frame):
        raise TimeoutError("fixture interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except Exception as error:
        # Never format exception values: they may contain a private URL/token/body.
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        print(
            "fixture failure: "
            + type(error).__name__
            + " at "
            + ",".join(f"{Path(f.filename).name}:{f.lineno}" for f in frames),
            file=sys.stderr,
        )
        raise SystemExit(1)
