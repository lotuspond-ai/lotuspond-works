"""Synthetic authority lives outside API database state, in a separate process."""

import multiprocessing
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from lotuspond import authority_gate, models
from lotuspond.app import create_app
from lotuspond.database import sessions_for
from tests.fleet.test_management import PIN, POLICY, create, post


@pytest.fixture(scope="module")
def authority_store():
    with multiprocessing.Manager() as manager:
        yield manager.dict()


@pytest.fixture
def identity_env(engine, settings, authority_store):
    from lotuspond_runtime.protocol import ConnectorPin

    from lotuspond.fleet.authority import FleetDependencies, RecoverySnapshot
    from lotuspond.fleet.catalog import InstalledCatalog

    authority_store.update(installation_id="test-installation", epoch=7, active=True)

    class Authority:
        def current(self, installation_id):
            return RecoverySnapshot(**dict(authority_store))

    class Clock:
        now = time.time_ns() // 1_000_000
        skew = 0

        def database(self, db):
            return self.now

        def application(self):
            return self.now + self.skew

    clock = Clock()
    settings.fleet_enabled = True
    settings.fleet_installation_id = "test-installation"
    dependencies = FleetDependencies(
        recovery=Authority(),
        catalog=InstalledCatalog.fixture(ConnectorPin(**PIN), test_mode=True),
        database_clock=clock.database,
        application_clock=clock.application,
    )
    application = create_app(settings, engine=engine, fleet_dependencies=dependencies)
    factory = sessions_for(engine)
    with TestClient(application, headers={"origin": "http://testserver"}) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "identity@example.com",
                "name": "Owner",
                "password": "a-valid-test-password",
                "account_type": "organization",
                "workspace": "Identity",
            },
        )
        assert response.status_code == 201, response.text
        workspace = response.json()["workspaceId"]
        client.headers["x-lotuspond-workspace"] = workspace
        project, environment = str(uuid4()), str(uuid4())
        with factory.begin() as db:
            db.add(models.Project(id=project, workspace_id=workspace, name="Identity"))
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
        pool = create(client, prefix, "runtime-pools")
        assert (
            post(
                client, f"{prefix}/runtime-pools/{pool['id']}/policies", POLICY, "policy"
            ).status_code
            == 201
        )
        yield SimpleNamespace(
            app=application,
            client=client,
            db=factory,
            clock=clock,
            store=authority_store,
            prefix=prefix,
            pool=pool["id"],
            workspace=workspace,
            dependencies=application.state.fleet_dependencies,
        )


def enrollment(env):
    return env.client.post(
        f"{env.prefix}/runtime-pools/{env.pool}/enrollments",
        json={"platform": "linux", "connectors": [PIN]},
    )


def exchange(env, token):
    return env.client.post(
        "/api/v1/runtime/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"platform": "linux", "connectors": [PIN]},
    )


def enrolled(env):
    issued = enrollment(env)
    assert issued.status_code == 201, issued.text
    exchanged = exchange(env, issued.json()["token"])
    assert exchanged.status_code == 201, exchanged.text
    return exchanged.json()


def auth(credential, secret=None):
    headers = {"Authorization": f"Bearer {credential['token']}"}
    if secret:
        headers["X-Lotuspond-Runtime-Session"] = secret
    return headers


def acquire(env, credential):
    return env.client.post("/api/v1/runtime/session", headers=auth(credential), json={})


def race_runtime(env, path, headers, bodies):
    """Hold the parent until two independent request connections reach its lock."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Condition

    from sqlalchemy import event, select

    gate, reached = Barrier(2), Condition()
    connections = set()
    engine = env.db.kw["bind"]

    def before_cursor(connection, cursor, statement, parameters, context, many):
        if "pg_advisory_xact_lock" in statement:  # its first lock: the fair gate
            with reached:
                connections.add(id(connection.connection.driver_connection))
                reached.notify_all()

    def run(n):
        worker = TestClient(env.app)
        gate.wait(timeout=5)
        return worker.post(path, headers=headers[n], json=bodies[n])

    with ThreadPoolExecutor(max_workers=2) as workers:
        with env.db.begin() as holder:
            # An authority change holds the parent: its gate first, exclusive.
            authority_gate.workspace(holder, env.workspace, exclusive=True)
            holder.scalar(
                select(models.Workspace)
                .where(models.Workspace.id == env.workspace)
                .with_for_update()
            )
            event.listen(engine, "before_cursor_execute", before_cursor)
            try:
                futures = [workers.submit(run, n) for n in range(2)]
                with reached:
                    assert reached.wait_for(lambda: len(connections) == 2, timeout=5), (
                        "Two independent requests did not reach the held parent lock"
                    )
            finally:
                event.remove(engine, "before_cursor_execute", before_cursor)
        return [future.result(timeout=10) for future in futures]
