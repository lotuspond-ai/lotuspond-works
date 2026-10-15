"""Independent PostgreSQL transactions exercise serialized rotation decisions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Condition, Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from lotuspond import models
from tests.fleet.identity_fixtures import auth
from tests.fleet.test_rotation import ROOT, prepare, ready, request, rows


def race(env, operations):
    engine = env.db.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL rotation qualification required")
    gate, reached, connections = Barrier(2), Condition(), set()

    def before(connection, cursor, statement, parameters, context, many):
        if "workspaces" in statement and "FOR UPDATE" in statement:
            with reached:
                connections.add(id(connection.connection.driver_connection))
                reached.notify_all()

    def run(operation):
        path, headers = operation
        gate.wait(timeout=5)
        worker = TestClient(env.app, headers={"origin": "http://testserver"})
        worker.cookies.update(env.client.cookies)
        return worker.post(path, json={}, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as workers:
        with env.db.begin() as holder:
            holder.scalar(
                select(models.Workspace)
                .where(models.Workspace.id == env.workspace)
                .with_for_update()
            )
            event.listen(engine, "before_cursor_execute", before)
            try:
                futures = [workers.submit(run, operation) for operation in operations]
                with reached:
                    assert reached.wait_for(lambda: len(connections) == 2, timeout=5)
            finally:
                event.remove(engine, "before_cursor_execute", before)
        return [f.result(timeout=10) for f in futures]


@pytest.mark.parametrize(
    "pair", ["prepare_prepare", "activate_activate", "cancel_activate", "prepare_activate"]
)
def test_rotation_races_preserve_single_active_and_pending(identity_env, pair):
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    names = pair.split("_")
    responses = race(
        env, [(f"{ROOT}/{name}", auth(p if name == "activate" else c, secret)) for name in names]
    )
    states = rows(env)
    assert sum(r[1] == "active" for r in states) == 1
    assert sum(r[1] == "pending" for r in states) <= 1
    codes = [r.status_code for r in responses]
    if pair == "prepare_prepare":
        assert codes == [201, 201]
        assert [r[0] for r in states] == [1, 2, 3, 4]
        assert [r[1] for r in states] == ["active", "revoked", "revoked", "pending"]
    elif pair == "activate_activate":
        assert codes == [200, 200]
        assert responses[0].json() == responses[1].json()
    elif pair == "cancel_activate":
        assert sorted(codes) == [200, 401]
        assert states[1][1] == ("active" if codes[1] == 200 else "revoked")
    else:
        assert sorted(codes) in ([201, 401], [200, 401])


@pytest.mark.parametrize("operation", ["prepare", "cancel", "activate", "retry"])
@pytest.mark.parametrize(
    "after_wait", ["session_expired", "credential_expired", "epoch_changed", "valid"]
)
def test_final_credential_lock_wait_rechecks_before_mutation(
    identity_env, engine, operation, after_wait
):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL rotation final-lock qualification required")
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    if operation == "retry":
        active_state = request(env, "activate", p, secret).json()
        prepare(env, p, secret)
    with env.db() as db:
        pending_id = db.scalar(
            select(models.RuntimeCredential.id).where(
                models.RuntimeCredential.version == (3 if operation == "retry" else 2)
            )
        )
    before_rows = rows(env)
    reached = Event()

    def before(connection, cursor, statement, parameters, context, many):
        if "FROM runtime_credentials" in statement and "FOR UPDATE" in statement:
            # Prepare/cancel lock C first; detect the later pending query.
            if operation in {"activate", "retry"} or (
                isinstance(parameters, dict)
                and ("pending" in parameters.values() or pending_id in parameters.values())
            ):
                reached.set()

    def run():
        return TestClient(env.app).post(
            f"{ROOT}/{'activate' if operation == 'retry' else operation}",
            json={},
            headers=auth(
                p if operation in {"activate", "retry"} else c,
                None if operation == "retry" else secret,
            ),
        )

    with ThreadPoolExecutor(max_workers=1) as workers:
        with env.db.begin() as holder:
            holder.scalar(
                select(models.RuntimeCredential)
                .where(models.RuntimeCredential.id == pending_id)
                .with_for_update()
            )
            event.listen(engine, "before_cursor_execute", before)
            try:
                future = workers.submit(run)
                assert reached.wait(5), "Rotation did not reach the held final credential lock"
                if after_wait == "session_expired":
                    env.clock.now += 60000
                elif after_wait == "credential_expired":
                    env.clock.now = (active_state if operation == "retry" else c["state"])[
                        "credential_expires_at_ms"
                    ]
                elif after_wait == "epoch_changed":
                    env.store["epoch"] = 8
                else:
                    env.clock.now += 1000
            finally:
                event.remove(engine, "before_cursor_execute", before)
        response = future.result(timeout=10)
    expected = (
        503
        if after_wait == "epoch_changed"
        else (201 if operation == "prepare" else 200)
        if after_wait == "valid" or (operation == "retry" and after_wait == "session_expired")
        else 401
    )
    assert response.status_code == expected, response.text
    if after_wait != "valid":
        assert rows(env) == before_rows
    else:
        assert response.json()["server_time_ms"] == env.clock.now


@pytest.mark.parametrize("operation", ["prepare", "activate", "cancel"])
def test_instance_revoke_race_invalidates_all_live_rows(identity_env, operation):
    env = identity_env
    c, secret = ready(env)
    p = prepare(env, c, secret)
    identifier = c["state"]["identity"]["instance_id"]
    responses = race(
        env,
        [
            (f"{ROOT}/{operation}", auth(p if operation == "activate" else c, secret)),
            (
                f"{env.prefix}/runtime-instances/{identifier}/revoke",
                {
                    "origin": "http://testserver",
                    "x-lotuspond-workspace": env.workspace,
                    "idempotency-key": "race-revoke",
                },
            ),
        ],
    )
    assert responses[1].status_code == 200, responses[1].text
    assert responses[0].status_code in {200, 201, 401}
    assert all(row[1] == "revoked" for row in rows(env))
    with env.db() as db:
        assert db.scalar(select(models.RuntimeSession.status)) == "revoked"
