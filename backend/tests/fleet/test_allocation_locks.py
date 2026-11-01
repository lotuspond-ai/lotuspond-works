"""E04 throughput option C, split by caller (owner decision, 30 October 2026).

Runtime authentication shares the workspace, pool and connection rows. Enrollment
issue and exchange keep the pool row exclusive: each counts against a limit under it
(at most 100 outstanding enrollments; at most `max_instances` instances). Each is
raced at its limit minus one: exclusive, one call wins. Shared, to show the race
these tests guard is real, both pass the count and both win.

A barrier between the lock and the count holds the first call until the second
arrives. Exclusive, the second waits for the lock instead, so the barrier times out
and the calls run one after the other.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import rfc8785
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import RuntimeObservation, Scope
from sqlalchemy import select

from lotuspond import models
from lotuspond.fleet import identity
from lotuspond.fleet.authority import FleetDependencies
from lotuspond.security import Principal
from tests.fleet.identity_fixtures import (
    authority_store as authority_store,
)
from tests.fleet.identity_fixtures import (
    enrolled,
    enrollment,
)
from tests.fleet.identity_fixtures import (
    identity_env as identity_env,
)
from tests.fleet.test_management import PIN

pytestmark = pytest.mark.usefixtures("postgresql_only")


@pytest.fixture
def postgresql_only(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks required")


def observation():
    return RuntimeObservation.model_validate({"platform": "linux", "connectors": [PIN]})


def owner(env):
    with env.db() as db:
        user = db.scalar(select(models.User).where(models.User.email == "identity@example.com"))
        pool = db.get(models.RuntimePool, env.pool)
        scope = Scope(
            workspace_id=pool.workspace_id,
            project_id=pool.project_id,
            environment_id=pool.environment_id,
        )
    return Principal(user.id, env.workspace, "owner", user.id), scope


def race(env, monkeypatch, calls, *, shared):
    """Run two calls at once; each result is "ok" or the refusal's code."""
    gate = threading.Barrier(2)
    original = FleetDependencies.now

    def now(self, db, **keywords):
        try:
            gate.wait(timeout=1.5)
        except threading.BrokenBarrierError:
            pass  # exclusive: the other call is still waiting for the pool row
        return original(self, db, **keywords)

    monkeypatch.setattr(FleetDependencies, "now", now)
    if shared:
        locked = identity.lock_pool
        monkeypatch.setattr(
            identity, "lock_pool", lambda *args, **kw: locked(*args, **{**kw, "share": True})
        )

    def run(call):
        try:
            with env.db.begin() as db:
                call(db)
            return "ok"
        except FleetError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        return sorted(workers.map(run, calls))


@pytest.mark.parametrize("shared", [False, True], ids=["exclusive", "shared variant"])
def test_issue_enrollment_at_its_limit_minus_one_admits_one(identity_env, monkeypatch, shared):
    env = identity_env
    principal, scope = owner(env)
    with env.db.begin() as db:
        for n in range(99):
            db.add(
                models.RuntimeEnrollment(
                    **scope.model_dump(),
                    pool_id=env.pool,
                    installation_id="test-installation",
                    recovery_epoch=7,
                    policy_version=1,
                    observation_ceiling=rfc8785.dumps(
                        RuntimeObservation(platform="linux", connectors=()).model_dump(mode="json")
                    ),
                    token_digest=f"{n:064x}",
                    status="issued",
                    expires_at_ms=env.clock.now + 600000,
                )
            )

    def issue(db):
        identity.issue_enrollment(db, principal, scope, env.pool, observation(), env.dependencies)

    results = race(env, monkeypatch, [issue, issue], shared=shared)
    with env.db() as db:
        outstanding = len(
            list(
                db.scalars(
                    select(models.RuntimeEnrollment.id).where(
                        models.RuntimeEnrollment.status == "issued"
                    )
                )
            )
        )
    if shared:
        assert (results, outstanding) == (["ok", "ok"], 101)  # the limit, overrun
    else:
        assert (results, outstanding) == (["conflict", "ok"], 100)


@pytest.mark.parametrize("shared", [False, True], ids=["exclusive", "shared variant"])
def test_exchange_enrollment_at_its_limit_minus_one_admits_one(identity_env, monkeypatch, shared):
    env = identity_env  # the fixture policy allows one instance, and none exists yet
    tokens = []
    for _ in range(2):
        issued = enrollment(env)
        assert issued.status_code == 201, issued.text
        tokens.append(issued.json()["token"])

    def exchanger(token):
        return lambda db: identity.exchange_enrollment(db, token, observation(), env.dependencies)

    results = race(env, monkeypatch, [exchanger(token) for token in tokens], shared=shared)
    with env.db() as db:
        instances = len(list(db.scalars(select(models.RuntimeInstance.id))))
    if shared:
        assert (results, instances) == (["ok", "ok"], 2)  # max_instances, overrun
    else:
        assert (results, instances) == (["conflict", "ok"], 1)


def polling(env, credential, stop):
    """Four runtime authentications on one pool, each held 0.2 s, without a gap."""
    from lotuspond.fleet.authentication import authenticate_runtime

    def poll(offset):
        time.sleep(offset)
        while not stop.is_set():
            with env.db.begin() as db:
                authenticate_runtime(
                    db,
                    credential["token"],
                    session_secret=None,
                    require_session=False,
                    dependencies=env.dependencies,
                )
                time.sleep(0.2)  # held, as a request holds it to its commit

    return [lambda offset=offset: poll(offset) for offset in (0, 0.05, 0.1, 0.15)]


def bounded(env, calls):
    """Each call's wait under sustained polling; the owner's bound is 2 s."""
    credential = enrolled(env)
    stop, waits = threading.Event(), []
    with ThreadPoolExecutor(max_workers=4) as workers:
        loops = [workers.submit(loop) for loop in polling(env, credential, stop)]
        time.sleep(0.5)
        try:
            for call in calls:
                started = time.monotonic()
                try:
                    with env.db.begin() as db:
                        call(db)
                except FleetError:
                    pass  # refused after its locks: what is measured is the wait
                waits.append(time.monotonic() - started)
                time.sleep(0.1)
        finally:
            stop.set()
        for loop in loops:
            loop.result(timeout=10)
    return waits


def test_an_enrollment_is_issued_within_2s_under_sustained_runtime_polling(identity_env):
    env = identity_env
    principal, scope = owner(env)

    def issue(db):
        identity.issue_enrollment(db, principal, scope, env.pool, observation(), env.dependencies)

    waits = bounded(env, [issue] * 5)
    print("issue_enrollment waits (s):", [round(wait, 3) for wait in waits])
    assert max(waits) < 2.0, waits


def test_an_enrollment_is_exchanged_within_2s_under_sustained_runtime_polling(identity_env):
    env = identity_env
    tokens = []
    for _ in range(5):
        issued = enrollment(env)
        assert issued.status_code == 201, issued.text
        tokens.append(issued.json()["token"])

    def exchanger(token):
        return lambda db: identity.exchange_enrollment(db, token, observation(), env.dependencies)

    waits = bounded(env, [exchanger(token) for token in tokens])
    print("exchange_enrollment waits (s):", [round(wait, 3) for wait in waits])
    assert max(waits) < 2.0, waits
