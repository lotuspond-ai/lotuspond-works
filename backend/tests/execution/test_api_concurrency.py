"""Task 12: the execution APIs under concurrent requests.

A request holds at most one pooled connection, unauthenticated runtime traffic
never queues on a tenant's rows, no route waits for the installation cursor, and
reads never wait for a writer.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from lotuspond.app import create_app
from lotuspond.models import User, Workspace

from .activity_fixtures import activity as activity
from .api_fixtures import api as api
from .api_fixtures import api_settings
from .control_fixtures import controlled as controlled
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_progression import flows as flows
from .test_publication import harness as harness
from .test_routes import context_body, get, post, start_body


def pooled(h, size):
    """An engine on the test's own schema with exactly `size` connections."""
    with h.engine.connect() as connection:
        path = connection.execute(text("SHOW search_path")).scalar()
    return create_engine(
        h.engine.url,
        connect_args={"options": f"-c search_path={path}"},
        pool_size=size,
        max_overflow=0,
        pool_timeout=3,
        hide_parameters=True,
    )


@contextmanager
def held(h, statement):
    """Another transaction holds the rows `statement` locks while the block runs."""
    ready, release = threading.Event(), threading.Event()

    def hold():
        with Session(h.engine) as db, db.begin():
            db.execute(statement)
            ready.set()
            release.wait(30)

    holder = threading.Thread(target=hold)
    holder.start()
    assert ready.wait(10)
    try:
        yield
    finally:
        release.set()
        holder.join()


def within(seconds, call):
    """The call's result, or `TimeoutError` if it has not answered in time."""
    pool = ThreadPoolExecutor(1)
    future = pool.submit(call)
    pool.shutdown(wait=False)
    return future.result(timeout=seconds)


def test_a_request_holds_one_pooled_connection(api):
    h = api
    revision = h.deploy("waits", "waits.policy")
    execution_id = post(h, "/executions", start_body(revision), h.owner, "p-0").json()["id"]
    engine = pooled(h, 1)
    application = create_app(
        api_settings(), engine=engine, fleet_dependencies=h.fleet, execution_services=h.services
    )
    try:
        with TestClient(application, headers={"origin": "http://testserver"}) as client:

            def read(path):
                return client.get(h.M + path, headers=h.owner).status_code

            paths = ["/executions", f"/executions/{execution_id}", "/deployments"] * 2
            with ThreadPoolExecutor(len(paths)) as pool:
                assert list(pool.map(read, paths)) == [200] * len(paths)
            # A write's authentication, pre-check and service share its one connection.
            response = client.post(
                h.M + "/executions",
                json=start_body(revision),
                headers={**h.owner, "idempotency-key": "p-1"},
            )
            assert response.status_code == 201, response.text
    finally:
        engine.dispose()


def test_unknown_runtime_credentials_never_wait_for_a_tenant_lock(api):
    h = api
    bogus = {
        "authorization": "Bearer lpr_" + "A" * 43,
        "x-lotuspond-runtime-session": "lps_" + "A" * 43,
    }
    wrong_session = {**bogus, "authorization": h.runtime["authorization"]}
    workspace = select(Workspace).where(Workspace.id == h.scope.workspace_id)
    with held(h, workspace.with_for_update()):
        for headers in (bogus, wrong_session, {"authorization": h.runtime["authorization"]}):
            for route in ("children", "results", "decline"):
                call = partial(
                    h.client.post, f"/api/v1/runtime/activities/{route}", json={}, headers=headers
                )
                assert within(2, call).status_code == 401, (route, headers)


def test_reads_never_wait_for_a_writer_on_the_workspace(api):
    h = api
    revision = h.deploy("waits", "waits.policy")
    execution_id = post(h, "/executions", start_body(revision), h.owner, "r-0").json()["id"]
    workspace = select(Workspace).where(Workspace.id == h.scope.workspace_id)
    with held(h, workspace.with_for_update()):
        for path in (
            "/executions",
            f"/executions/{execution_id}",
            f"/executions/{execution_id}/events",
            f"/executions/{execution_id}/waits",
            "/deployments",
            "/executable-packages",
        ):
            assert within(2, partial(get, h, path, h.owner)).status_code == 200, path
        # A write still authorizes under the account locks, so it waits its turn.
        pool = ThreadPoolExecutor(1)
        write = pool.submit(post, h, "/executions", start_body(revision), h.owner, "r-1")
        pool.shutdown(wait=False)
        with pytest.raises(TimeoutError):
            write.result(timeout=1)
    assert write.result(timeout=10).status_code == 201


def test_equal_concurrent_registrations_return_one_source(api):
    h = api
    body = context_body(h)
    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(lambda _: post(h, "/context-sources", body, h.owner, "c-race"), range(2))
        )
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()


def test_an_admission_route_never_waits_for_the_installation_cursor(api):
    """Finding 11: the cursor's holder is a slot taking reservation, never a route."""
    from .cursor_checks import without_cursor

    h = api
    revision = h.deploy("waits", "waits.policy")
    response = without_cursor(
        h.engine, lambda: post(h, "/executions", start_body(revision), h.owner, "o-0")
    )
    assert response.status_code == 201, response.text


def test_a_write_holds_no_account_lock_while_its_service_waits_for_the_workspace(api):
    h = api
    revision = h.deploy("waits", "waits.policy")
    workspace = select(Workspace).where(Workspace.id == h.scope.workspace_id)
    with Session(h.engine) as db, db.begin():
        db.execute(workspace.with_for_update())
        pool = ThreadPoolExecutor(1)
        write = pool.submit(post, h, "/executions", start_body(revision), h.owner, "o-1")
        pool.shutdown(wait=False)
        with pytest.raises(TimeoutError):
            write.result(timeout=1)  # its service waits for the workspace, Task 5's first lock
        # The route's pre-check left no account row locked, so the workspace's holder
        # may still take the account rows next, in that same order, without deadlocking.
        owner = db.scalar(select(Workspace.owner_id).where(Workspace.id == h.scope.workspace_id))
        db.execute(select(User).where(User.id == owner).with_for_update(nowait=True))
    assert write.result(timeout=10).status_code == 201
