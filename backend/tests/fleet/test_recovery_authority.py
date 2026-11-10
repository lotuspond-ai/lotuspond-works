"""Independent current authority and durable clock-fence observations."""

import pytest
from sqlalchemy import select

from lotuspond import models
from tests.fleet.identity_fixtures import auth, enrolled


@pytest.mark.parametrize("change", ["inactive", "epoch", "installation", "unavailable"])
def test_authority_fence(identity_env, change):
    from lotuspond.fleet.authority import FleetDependencies, UnavailableRecoveryAuthority

    env = identity_env
    credential = enrolled(env)
    if change == "inactive":
        env.store["active"] = False
    elif change == "epoch":
        env.store["epoch"] = 8
    elif change == "installation":
        env.store["installation_id"] = "other-installation"
    else:
        old = env.dependencies
        env.app.state.fleet_dependencies = FleetDependencies(
            recovery=UnavailableRecoveryAuthority(),
            catalog=old.catalog,
            installation_id=old.installation_id,
            limiter=old.limiter,
            database_clock=env.clock.database,
            application_clock=env.clock.application,
        )
    assert env.client.get("/api/v1/runtime/self", headers=auth(credential)).status_code == 503


@pytest.mark.parametrize(
    "skew,expected", [(30000, 200), (-30000, 200), (30001, 503), (-30001, 503)]
)
def test_authority_fence_and_clock(identity_env, skew, expected):
    env = identity_env
    credential = enrolled(env)
    env.clock.skew = skew
    response = env.client.get("/api/v1/runtime/self", headers=auth(credential))
    assert response.status_code == expected, response.text
    with env.db() as db:
        audits = list(
            db.scalars(
                select(models.AuditEvent).where(
                    models.AuditEvent.action == "fleet.identity.clock_fence"
                )
            )
        )
        assert len(audits) == (1 if expected == 503 else 0)
        if audits:
            assert audits[0].details == {
                "project_id": credential["state"]["identity"]["scope"]["project_id"],
                "environment_id": credential["state"]["identity"]["scope"]["environment_id"],
            }


def test_authority_deadline_late_results_and_hang_are_bounded():
    import threading
    import time

    from lotuspond_runtime.errors import FleetError

    from lotuspond.fleet.authority import FleetDependencies, RecoverySnapshot
    from lotuspond.fleet.catalog import InstalledCatalog

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class Authority:
        def current(self, installation_id):
            calls.append(installation_id)
            entered.set()
            release.wait()
            finished.set()
            return RecoverySnapshot(installation_id, len(calls), True)

    dependencies = FleetDependencies(
        Authority(), InstalledCatalog(), installation_id="test", authority_timeout_seconds=0.02
    )
    with pytest.raises(FleetError, match="authority_unavailable"):
        dependencies.current("test")
    assert entered.is_set()
    for _ in range(20):
        # A waiting caller is refused within its own bound, the wait included.
        start = time.monotonic()
        with pytest.raises(FleetError, match="authority_unavailable"):
            dependencies.current("test")
        assert time.monotonic() - start < 0.02 + 0.05
    assert calls == ["test"]
    # The late allow does not become a cached grant. The next call must do
    # another independent lookup and observe that provider's newer snapshot.
    release.set()
    assert finished.wait(1)
    assert dependencies._slot.lock.acquire(timeout=1)
    dependencies._slot.lock.release()
    assert dependencies.current("test").epoch == 2
    start = time.monotonic()
    dependencies.close()
    assert time.monotonic() - start < 0.1
    with pytest.raises(FleetError, match="authority_unavailable"):
        dependencies.current("test")


def test_default_authority_and_production_injection_guard(settings, engine):
    from lotuspond.app import create_app
    from lotuspond.fleet.authority import FleetDependencies, UnavailableRecoveryAuthority
    from lotuspond.fleet.catalog import InstalledCatalog

    dependencies = FleetDependencies(UnavailableRecoveryAuthority(), InstalledCatalog())
    settings.env = "development"
    with pytest.raises(ValueError, match="test mode"):
        create_app(settings, engine=engine, fleet_dependencies=dependencies)
    settings.env = "test"
    application = create_app(settings, engine=engine)
    with pytest.raises(Exception, match="authority_unavailable"):
        application.state.fleet_dependencies.current("missing")


def test_clock_denial_rolls_back_changes_and_audit_failure_denies(identity_env, monkeypatch):
    from lotuspond.fleet import audit, runtime_routes

    env = identity_env
    credential = enrolled(env)
    original = runtime_routes.self_state

    def changes_then_fences(db, principal, dependencies):
        row = db.get(models.RuntimeInstance, principal.identity.instance_id)
        row.last_seen_at_ms = 123
        db.flush()
        return original(db, principal, dependencies)

    monkeypatch.setattr(runtime_routes, "self_state", changes_then_fences)

    # Authentication fences first, so trigger the clock change only after that
    # succeeds and before the self-state service rechecks its locked state.
    def changes_and_skews(db, principal, dependencies):
        env.clock.skew = 30001
        return changes_then_fences(db, principal, dependencies)

    monkeypatch.setattr(runtime_routes, "self_state", changes_and_skews)
    assert env.client.get("/api/v1/runtime/self", headers=auth(credential)).status_code == 503
    with env.db() as db:
        assert (
            db.get(
                models.RuntimeInstance, credential["state"]["identity"]["instance_id"]
            ).last_seen_at_ms
            == env.clock.now
        )
        assert (
            len(
                list(
                    db.scalars(
                        select(models.AuditEvent).where(
                            models.AuditEvent.action == "fleet.identity.clock_fence"
                        )
                    )
                )
            )
            == 1
        )

    def fail_audit(*args):
        raise RuntimeError("synthetic private provider failure")

    monkeypatch.setattr(audit, "persist_clock_fence", fail_audit)
    response = env.client.get("/api/v1/runtime/self", headers=auth(credential))
    assert response.status_code == 503
    assert "synthetic" not in response.text


def test_restoring_api_rows_does_not_restore_independent_authority(identity_env):
    env = identity_env
    credential = enrolled(env)
    env.store["epoch"] = 8
    with env.db.begin() as db:
        db.get(
            models.RuntimeInstance, credential["state"]["identity"]["instance_id"]
        ).generation = 0
    assert env.client.get("/api/v1/runtime/self", headers=auth(credential)).status_code == 503


def test_database_time_is_post_lock_wall_time(identity_env, engine):
    import time

    from sqlalchemy import text

    from lotuspond.fleet.authority import database_now_ms

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL clock_timestamp qualification required")
    with identity_env.db() as db:
        initial = db.scalar(text("SELECT floor(extract(epoch FROM now()) * 1000)::bigint"))
        # Explicit SQL wait within this owned local transaction, not an expiry
        # assumption: transaction-start now() remains the same after this wait.
        db.execute(text("SELECT pg_sleep(0.02)"))
        fresh = database_now_ms(db)
        assert fresh >= initial + 15
        assert abs(fresh - time.time_ns() // 1_000_000) < 1000
        assert db.scalar(text("SELECT floor(extract(epoch FROM now()) * 1000)::bigint")) == initial


def test_clock_fence_audit_storage_lock_is_bounded(identity_env, engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient
    from sqlalchemy import event, text

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL audit storage lock qualification required")
    env = identity_env
    credential = enrolled(env)
    env.clock.skew = 30001
    reached = Event()

    def before_cursor(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO audit_events"):
            reached.set()

    def request():
        return TestClient(env.app).get("/api/v1/runtime/self", headers=auth(credential))

    with ThreadPoolExecutor(max_workers=1) as workers:
        with env.db.begin() as holder:
            holder.execute(text("LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE"))
            event.listen(engine, "before_cursor_execute", before_cursor)
            try:
                future = workers.submit(request)
                assert reached.wait(5), "Denial did not reach audit storage"
                # A storage lock must not keep an identity denial open indefinitely.
                response = future.result(timeout=3)
                assert response.status_code == 503
                assert response.json()["error"] == "authority_unavailable"
            finally:
                event.remove(engine, "before_cursor_execute", before_cursor)


def test_hung_authority_does_not_grow_workers_or_delay_shutdown():
    import threading
    import time

    from lotuspond_runtime.errors import FleetError

    from lotuspond.fleet.authority import FleetDependencies
    from lotuspond.fleet.catalog import InstalledCatalog

    entered, release = threading.Event(), threading.Event()
    calls = []

    class HungAuthority:
        def current(self, installation_id):
            calls.append(installation_id)
            entered.set()
            release.wait()
            raise RuntimeError("unavailable")

    dependencies = FleetDependencies(
        HungAuthority(), InstalledCatalog(), installation_id="test", authority_timeout_seconds=0.01
    )
    try:
        with pytest.raises(FleetError, match="authority_unavailable"):
            dependencies.current("test")
        assert entered.is_set()
        for _ in range(100):
            with pytest.raises(FleetError, match="authority_unavailable"):
                dependencies.current("test")
        assert len(calls) == 1
        started = time.monotonic()
        dependencies.close()
        assert time.monotonic() - started < 0.1
    finally:
        release.set()


@pytest.mark.parametrize("epoch", [True, 1.0, "7", -1, 9007199254740992])
def test_recovery_epoch_has_exact_safe_integer_type(epoch):
    from lotuspond.fleet.authority import RecoverySnapshot

    with pytest.raises(Exception):
        RecoverySnapshot("test", epoch, True)


def test_a_concurrent_lookup_waits_for_the_slot_within_its_bound():
    """Finding 3 (owner decision, 2026-10-29): a busy slot is waited for, not refused."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from lotuspond_runtime.errors import FleetError

    from lotuspond.fleet.authority import FleetDependencies, RecoverySnapshot
    from lotuspond.fleet.catalog import InstalledCatalog

    entered, release = threading.Event(), threading.Event()
    calls, active, overlap = [], [0], []

    class Authority:
        def current(self, installation_id):
            active[0] += 1
            overlap.append(active[0])
            calls.append(installation_id)
            if len(calls) == 1:
                entered.set()
                release.wait(5)
            active[0] -= 1
            return RecoverySnapshot(installation_id, len(calls), True)

    served = FleetDependencies(
        Authority(), InstalledCatalog(), installation_id="test", authority_timeout_seconds=3
    )
    bounded = FleetDependencies(
        Authority(), InstalledCatalog(), installation_id="test", authority_timeout_seconds=1
    )
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(served.current, "test")
            assert entered.wait(5)
            second = pool.submit(served.current, "test")
            time.sleep(0.2)
            assert not second.done()  # it waits for the slot instead of refusing
            release.set()
            assert first.result(timeout=5).epoch == 1
            assert second.result(timeout=5).epoch == 2
        assert max(overlap) == 1  # still one lookup in flight at a time
        # The bound covers the wait: with the slot held past it, a caller is refused
        # after about one bound, never two.
        entered.clear()
        release.clear()
        calls.clear()
        with ThreadPoolExecutor(1) as pool:
            held = pool.submit(bounded.current, "test")
            assert entered.wait(5)
            start = time.monotonic()
            with pytest.raises(FleetError, match="authority_unavailable"):
                bounded.current("test")
            assert 0.9 <= time.monotonic() - start < 1.9
            release.set()
            # The holder had the same bound; its late answer is dropped either way.
            held.exception(timeout=5)
    finally:
        release.set()
        served.close()
        bounded.close()
