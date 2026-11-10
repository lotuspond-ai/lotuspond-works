"""One-use, parent-bound enrollment with serialized allocation."""

import pytest
from sqlalchemy import select

from lotuspond import authority_gate, models
from tests.fleet.identity_fixtures import (
    enrolled,
    enrollment,
    exchange,
    race_runtime,
)
from tests.fleet.test_management import PIN, POLICY, post


def test_enrollment_exact_lifetime_and_one_time(identity_env):
    env = identity_env
    issued = enrollment(env)
    assert issued.status_code == 201, issued.text
    item = issued.json()
    assert item["expires_at_ms"] - env.clock.now == 600000
    assert len(item["token"]) == 47 and item["token"].startswith("lpe_")
    result = exchange(env, item["token"])
    assert result.status_code == 201, result.text
    credential = result.json()
    assert credential["state"]["credential_expires_at_ms"] - env.clock.now == 900000
    assert credential["state"]["identity"]["generation"] == 0
    assert exchange(env, item["token"]).status_code == 401
    with env.db() as db:
        assert db.scalar(select(models.RuntimeEnrollment)).status == "consumed"
        assert item["token"] not in repr(db.scalar(select(models.RuntimeEnrollment)).__dict__)


def test_enrollment_single_winner(identity_env, engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL race qualification required")
    env = identity_env
    token = enrollment(env).json()["token"]
    results = race_runtime(
        env,
        "/api/v1/runtime/enroll",
        [{"Authorization": f"Bearer {token}"}] * 2,
        [{"platform": "linux", "connectors": [PIN]}] * 2,
    )
    assert sorted(r.status_code for r in results) == [201, 401]
    with env.db() as db:
        assert len(list(db.scalars(select(models.RuntimeInstance)))) == 1
        assert len(list(db.scalars(select(models.RuntimeCredential)))) == 1


@pytest.mark.parametrize("change", ["expired", "pool", "workspace", "ceiling", "capacity"])
def test_enrollment_current_parent_capacity_ceiling(identity_env, change):
    env = identity_env
    token = enrollment(env).json()["token"]
    if change == "expired":
        env.clock.now += 600000
    elif change == "ceiling":
        assert (
            post(
                env.client,
                f"{env.prefix}/runtime-pools/{env.pool}/policies",
                dict(POLICY, connectors=[]),
                "narrow",
            ).status_code
            == 201
        )
    elif change == "capacity":
        enrolled(env)
    else:
        with env.db.begin() as db:
            row = db.get(
                models.Workspace if change == "workspace" else models.RuntimePool,
                env.workspace if change == "workspace" else env.pool,
            )
            row.status = "suspended" if change == "workspace" else "revoked"
    assert exchange(env, token).status_code in {401, 403, 409}


def test_capacity_race_independent_enrollments(identity_env, engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL capacity qualification required")
    env = identity_env
    tokens = [enrollment(env).json()["token"] for _ in range(2)]
    results = race_runtime(
        env,
        "/api/v1/runtime/enroll",
        [{"Authorization": f"Bearer {token}"} for token in tokens],
        [{"platform": "linux", "connectors": [PIN]}] * 2,
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    with env.db() as db:
        assert len(list(db.scalars(select(models.RuntimeInstance)))) == 1


def test_enrollment_and_instance_management_inventory_revoke(identity_env):
    env = identity_env
    issued = enrollment(env).json()
    path = f"{env.prefix}/runtime-pools/{env.pool}/enrollments"
    inventory = env.client.get(path)
    assert inventory.status_code == 200
    assert "token" not in inventory.text and "digest" not in inventory.text
    assert len(inventory.json()["items"]) == 1
    revoke = f"{path}/{issued['enrollment_id']}/revoke"
    assert post(env.client, revoke, {}, "revoke-enrollment").status_code == 200
    assert post(env.client, revoke, {}, "revoke-enrollment").status_code == 200
    assert exchange(env, issued["token"]).status_code == 401
    credential = enrolled(env)
    instance = credential["state"]["identity"]["instance_id"]
    instances = f"{env.prefix}/runtime-instances"
    result = env.client.get(instances)
    assert result.status_code == 200
    assert "token" not in result.text and "digest" not in result.text
    assert env.client.get(f"{instances}/{instance}").json() == result.json()["items"][0]
    assert (
        post(env.client, f"{instances}/{instance}/revoke", {}, "revoke-instance").status_code == 200
    )
    assert (
        post(env.client, f"{instances}/{instance}/revoke", {}, "revoke-instance").status_code == 200
    )
    assert (
        env.client.get(
            "/api/v1/runtime/self", headers={"Authorization": "Bearer " + credential["token"]}
        ).status_code
        == 401
    )


def test_enrollment_outstanding_capacity(identity_env):
    import rfc8785
    from lotuspond_runtime.protocol import RuntimeObservation

    env = identity_env
    with env.db.begin() as db:
        policy = db.scalar(select(models.RuntimePoolPolicy))
        for n in range(100):
            db.add(
                models.RuntimeEnrollment(
                    workspace_id=env.workspace,
                    project_id=policy.project_id,
                    environment_id=policy.environment_id,
                    pool_id=env.pool,
                    installation_id="test-installation",
                    recovery_epoch=7,
                    policy_version=1,
                    observation_ceiling=rfc8785.dumps(
                        RuntimeObservation(platform="linux", connectors=()).model_dump(mode="json")
                    ),
                    token_digest=f"{n:064x}",
                    status="issued",
                    expires_at_ms=env.clock.now + 1,
                )
            )
    assert enrollment(env).status_code == 409
    env.clock.now += 1
    assert enrollment(env).status_code == 201


def test_enrollment_pool_rate(identity_env):
    env = identity_env
    for _ in range(60):
        assert enrollment(env).status_code == 201
    assert enrollment(env).status_code == 429


def test_enrollment_scope_and_admin_routes_reject_foreign_ids(identity_env):
    from uuid import uuid4

    env = identity_env
    issued = enrollment(env).json()
    credential = enrolled(env)
    instance = credential["state"]["identity"]["instance_id"]
    wrong = env.prefix.replace(env.prefix.split("/")[6], str(uuid4()))
    assert env.client.get(f"{wrong}/runtime-instances/{instance}").status_code == 404
    assert (
        post(env.client, f"{wrong}/runtime-instances/{instance}/revoke", {}, "wrong").status_code
        == 404
    )
    with env.db.begin() as db:
        db.scalar(
            select(models.Membership).where(models.Membership.workspace_id == env.workspace)
        ).role = "viewer"
    assert enrollment(env).status_code == 403
    assert env.client.get(f"{env.prefix}/runtime-instances").status_code == 403
    assert (
        post(
            env.client,
            f"{env.prefix}/runtime-pools/{env.pool}/enrollments/{issued['enrollment_id']}/revoke",
            {},
            "wrong",
        ).status_code
        == 403
    )


def test_identity_pool_indexes_support_bounded_queries(identity_env, engine):
    from sqlalchemy import inspect, text

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL pool index qualification required")
    env = identity_env
    indexes = {
        item["name"]: item
        for table in ("runtime_enrollments", "runtime_instances")
        for item in inspect(engine).get_indexes(table)
    }
    required = {
        "runtime_enrollments_inventory_idx": [
            "workspace_id",
            "project_id",
            "environment_id",
            "pool_id",
            "id",
        ],
        "runtime_enrollments_live_idx": [
            "workspace_id",
            "project_id",
            "environment_id",
            "pool_id",
            "expires_at_ms",
        ],
        "runtime_instances_active_idx": [
            "workspace_id",
            "project_id",
            "environment_id",
            "pool_id",
            "id",
        ],
    }
    assert set(required) <= set(indexes)
    for name, columns in required.items():
        assert indexes[name]["column_names"] == columns
    queries = {
        "runtime_enrollments_inventory_idx": "SELECT id FROM runtime_enrollments WHERE workspace_id=:w AND project_id=:p AND environment_id=:e AND pool_id=:pool AND id>:cursor ORDER BY id LIMIT 51",
        "runtime_enrollments_live_idx": "SELECT count(*) FROM runtime_enrollments WHERE workspace_id=:w AND project_id=:p AND environment_id=:e AND pool_id=:pool AND status='issued' AND expires_at_ms>:now",
        "runtime_instances_active_idx": "SELECT count(*) FROM runtime_instances WHERE workspace_id=:w AND project_id=:p AND environment_id=:e AND pool_id=:pool AND status='active'",
    }
    with env.db() as db:
        db.execute(text("SET LOCAL enable_seqscan=off"))
        params = {
            "w": env.workspace,
            "p": env.prefix.split("/")[4],
            "e": env.prefix.split("/")[6],
            "pool": env.pool,
            "cursor": "00000000-0000-0000-0000-000000000000",
            "now": env.clock.now,
        }
        for name, query in queries.items():
            plan = "\n".join(db.execute(text("EXPLAIN " + query), params).scalars())
            assert name in plan, plan


def test_enrollment_ceiling_intersection_cannot_be_expanded(identity_env):
    env = identity_env
    narrower = env.client.post(
        f"{env.prefix}/runtime-pools/{env.pool}/enrollments",
        json={"platform": "linux", "connectors": []},
    )
    assert narrower.status_code == 201
    assert exchange(env, narrower.json()["token"]).status_code == 401
    response = env.client.post(
        "/api/v1/runtime/enroll",
        json={"platform": "linux", "connectors": []},
        headers={"Authorization": "Bearer " + narrower.json()["token"]},
    )
    assert response.status_code == 201


def test_enrollment_expiry_is_rechecked_after_parent_wait(identity_env, engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient
    from sqlalchemy import event

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL enrollment expiry-after-wait qualification required")
    env = identity_env
    issued = enrollment(env).json()
    reached = Event()

    def before_cursor(connection, cursor, statement, parameters, context, many):
        if "pg_advisory_xact_lock" in statement:  # its first lock: the fair gate
            reached.set()

    def request():
        return TestClient(env.app).post(
            "/api/v1/runtime/enroll",
            json={"platform": "linux", "connectors": [PIN]},
            headers={"Authorization": "Bearer " + issued["token"]},
        )

    with ThreadPoolExecutor(max_workers=1) as workers:
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
                future = workers.submit(request)
                assert reached.wait(5)
                env.clock.now = issued["expires_at_ms"]
            finally:
                event.remove(engine, "before_cursor_execute", before_cursor)
        assert future.result(timeout=10).status_code == 401


def test_instance_revocation_does_not_load_unbounded_revoked_history(identity_env):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from lotuspond.fleet.authentication import machine_fields
    from tests.fleet.identity_fixtures import acquire

    env = identity_env
    credential = enrolled(env)
    acquire(env, credential)
    identifier = credential["state"]["identity"]["instance_id"]
    with env.db.begin() as db:
        instance = db.get(models.RuntimeInstance, identifier)
        active = db.scalar(select(models.RuntimeCredential))
        session = db.scalar(select(models.RuntimeSession))
        instance.generation, session.generation, active.version = 101, 101, 101
        db.flush()
        for n in range(1, 101):
            db.add(
                models.RuntimeSession(
                    **machine_fields(instance),
                    instance_id=instance.id,
                    generation=n,
                    token_digest=f"{n:064x}",
                    status="revoked",
                    expires_at_ms=0,
                )
            )
            db.add(
                models.RuntimeCredential(
                    **machine_fields(instance),
                    instance_id=instance.id,
                    version=n,
                    token_digest=f"{n:064x}",
                    status="revoked",
                    expires_at_ms=0,
                )
            )
    loaded = []

    def observe(db, item):
        if isinstance(item, (models.RuntimeSession, models.RuntimeCredential)):
            loaded.append(item.id)

    event.listen(Session, "loaded_as_persistent", observe)
    try:
        response = post(
            env.client, f"{env.prefix}/runtime-instances/{identifier}/revoke", {}, "revoke"
        )
        assert response.status_code == 200
    finally:
        event.remove(Session, "loaded_as_persistent", observe)
    assert len(loaded) <= 3
