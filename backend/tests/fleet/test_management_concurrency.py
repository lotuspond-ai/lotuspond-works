"""Real PostgreSQL transactions serialize immutable publication and request keys."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from lotuspond import models
from tests.fleet.test_management import CONFIG, create


def race(app, client, path, bodies, keys):
    barrier = Barrier(2)
    cookies = dict(client.cookies)
    headers = dict(client.headers)

    def run(n):
        with TestClient(app, headers=headers, cookies=cookies) as worker:
            barrier.wait(timeout=10)
            return worker.post(path, json=bodies[n], headers={"Idempotency-Key": keys[n]})

    with ThreadPoolExecutor(max_workers=2) as workers:
        return list(workers.map(run, range(2)))


@pytest.mark.parametrize("same_key,changed", [(False, False), (True, False), (True, True)])
def test_version_and_request_key_races(
    app, client, fleet, catalog, engine, db_factory, same_key, changed
):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock qualification required")
    item = create(client, fleet)
    bodies = [
        CONFIG,
        dict(CONFIG, data_handling=dict(CONFIG["data_handling"], timeout_ms=50))
        if changed
        else CONFIG,
    ]
    responses = race(
        app,
        client,
        f"{fleet}/connections/{item['id']}/versions",
        bodies,
        ["race", "race" if same_key else "other"],
    )
    assert sorted(r.status_code for r in responses) == ([201, 409] if changed else [201, 201])
    with db_factory() as db:
        versions = list(
            db.scalars(select(models.ConnectionVersion).order_by(models.ConnectionVersion.version))
        )
        assert len(versions) == (1 if same_key else 2)
        assert [v.version for v in versions] == list(range(1, len(versions) + 1))
        assert db.scalar(select(func.count()).select_from(models.FleetMutationRequest)) == (
            2 if same_key else 3
        )
    if same_key and not changed:
        assert responses[0].json() == responses[1].json()


def test_request_rechecks_membership_after_workspace_lock_wait(
    app, client, fleet, engine, db_factory, account
):
    from threading import Event

    from sqlalchemy import event

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock qualification required")
    item = create(client, fleet)
    reached = Event()
    cookies, headers = dict(client.cookies), dict(client.headers)

    def before_cursor(connection, cursor, statement, parameters, context, many):
        if "workspaces" in statement and "FOR UPDATE" in statement:
            reached.set()

    def replay():
        with TestClient(app, headers=headers, cookies=cookies) as worker:
            return worker.post(
                f"{fleet}/connections", json={"name": "Test"}, headers={"Idempotency-Key": "create"}
            )

    with ThreadPoolExecutor(max_workers=1) as workers:
        with db_factory.begin() as holder:
            holder.scalar(
                select(models.Workspace).where(models.Workspace.id == account).with_for_update()
            )
            event.listen(engine, "before_cursor_execute", before_cursor)
            try:
                future = workers.submit(replay)
                assert reached.wait(10), "Request did not reach parent lock"
                holder.scalar(
                    select(models.Membership).where(models.Membership.workspace_id == account)
                ).role = "viewer"
            finally:
                event.remove(engine, "before_cursor_execute", before_cursor)
        assert future.result(timeout=10).status_code == 403
    with db_factory() as db:
        assert db.scalar(select(models.Connection)).id == item["id"]


def test_pool_policy_and_secret_registration_races(app, client, fleet, catalog, engine, db_factory):
    from tests.fleet.test_management import POLICY

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock qualification required")
    pool = create(client, fleet, "runtime-pools", "pool")
    results = race(
        app,
        client,
        f"{fleet}/runtime-pools/{pool['id']}/policies",
        [POLICY, POLICY],
        ["one", "two"],
    )
    assert sorted(r.status_code for r in results) == [201, 201]
    with db_factory() as db:
        assert db.get(models.RuntimePool, pool["id"]).current_policy_version == 2
        assert sorted(db.scalars(select(models.RuntimePoolPolicy.version))) == [1, 2]
    payload = {"binding_id": "fixture-binding", "version": 1}
    results = race(app, client, f"{fleet}/secret-references", [payload, payload], ["same", "same"])
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json() == results[1].json()


@pytest.mark.parametrize(
    "target,operation,denial",
    [
        ("workspace", "publish", "forbidden"),
        ("connection", "publish", "conflict"),
        ("pool", "publish", "conflict"),
        ("secret", "publish", "conflict"),
        ("connection", "activate", "conflict"),
        ("pool", "activate", "conflict"),
        ("secret", "register", "conflict"),
    ],
)
def test_preloaded_authority_refreshes_under_service_lock(
    client, fleet, catalog, app, engine, db_factory, account, target, operation, denial
):
    from lotuspond_runtime.errors import FleetError
    from lotuspond_runtime.protocol import ConnectionConfig, PoolPolicy, Scope

    from lotuspond.fleet.management import ConnectionService, PoolService
    from tests.fleet.test_management import POLICY, post

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL identity-map/row-lock qualification required")
    connection = create(client, fleet)
    pool = create(client, fleet, "runtime-pools", "pool")
    reference = post(
        client,
        f"{fleet}/secret-references",
        {"binding_id": "fixture-binding", "version": 1},
        "reference",
    ).json()
    scope = Scope(
        workspace_id=account, project_id=fleet.split("/")[4], environment_id=fleet.split("/")[6]
    )
    with db_factory() as caller:
        actor = caller.scalar(select(models.User)).id
        model, identifier = {
            "workspace": (models.Workspace, account),
            "connection": (models.Connection, connection["id"]),
            "pool": (models.RuntimePool, pool["id"]),
            "secret": (models.SecretReference, reference["id"]),
        }[target]
        # A strong reference keeps the earlier object in the Session identity map.
        # No locks are acquired until the independent change has committed.
        cached = caller.get(model, identifier)
        assert cached.status == "active"
        with db_factory.begin() as other:
            other.get(model, identifier).status = (
                "suspended" if target == "workspace" else "revoked"
            )
        assert cached.status == "active"
        service_type = PoolService if target == "pool" else ConnectionService
        service = service_type(caller, catalog=app.state.fleet_dependencies.catalog, actor_id=actor)
        with pytest.raises(FleetError, match=f"^{denial}$"):
            if operation == "activate":
                service.set_status(scope, identifier, "active", "reactivate")
            elif operation == "register":
                service.register_secret(scope, "fixture-binding", 1, "reference")
            elif target == "pool":
                service.publish(scope, pool["id"], PoolPolicy.model_validate(POLICY), "publish")
            else:
                config = dict(CONFIG)
                if target == "secret":
                    config["secrets"] = [{"reference_id": reference["id"], "version": 1}]
                service.publish(
                    scope, connection["id"], ConnectionConfig.model_validate(config), "publish"
                )
        assert cached.status == ("suspended" if target == "workspace" else "revoked")
    with db_factory() as check:
        assert list(check.scalars(select(models.ConnectionVersion))) == []
        assert list(check.scalars(select(models.RuntimePoolPolicy))) == []
        assert check.get(model, identifier).status == (
            "suspended" if target == "workspace" else "revoked"
        )


def test_preloaded_pool_policy_pointer_refreshes_before_publication(
    client, fleet, catalog, app, engine, db_factory, account
):
    from lotuspond_runtime.protocol import PoolPolicy, Scope

    from lotuspond.fleet.management import PoolService
    from tests.fleet.test_management import POLICY, post

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL identity-map/row-lock qualification required")
    pool = create(client, fleet, "runtime-pools", "pool")
    path = f"{fleet}/runtime-pools/{pool['id']}/policies"
    assert post(client, path, POLICY, "initial").status_code == 201
    scope = Scope(
        workspace_id=account, project_id=fleet.split("/")[4], environment_id=fleet.split("/")[6]
    )
    with db_factory() as caller:
        actor = caller.scalar(select(models.User)).id
        cached = caller.get(models.RuntimePool, pool["id"])
        assert cached.current_policy_version == 1
        # The second HTTP request uses a separate caller transaction and commits.
        assert post(client, path, dict(POLICY, max_instances=2), "second").status_code == 201
        assert cached.current_policy_version == 1
        published = PoolService(
            caller, catalog=app.state.fleet_dependencies.catalog, actor_id=actor
        ).publish(
            scope, pool["id"], PoolPolicy.model_validate(dict(POLICY, max_instances=3)), "third"
        )
        assert published.version == 3
        assert cached.current_policy_version == 3
        # A service may flush, but leaves both revision and pointer under caller control.
        caller.rollback()
    with db_factory() as check:
        assert check.get(models.RuntimePool, pool["id"]).current_policy_version == 2
        assert sorted(check.scalars(select(models.RuntimePoolPolicy.version))) == [1, 2]
