"""Throughput option C, the user gate (owner decision, 1 November 2026).

Execution and runtime writes share their initiating user's row under that user's
gate, shared; every user-row locker takes the gate exclusive first. While one user's
executions make constant runtime calls, that user's login and password reset each finish within the owner's 2 s bound, and a login racing an execution
write for the same user never deadlocks.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.models import User
from lotuspond.security import password_hasher

from .activity_fixtures import activity as activity
from .api_fixtures import api as api
from .control_fixtures import controlled as controlled
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_progression import flows as flows
from .test_publication import harness as harness

PASSWORD = "a-valid-test-password"


def account(engine, user_id):
    """The user as a person who can log in and reset a password. Returns the email."""
    with Session(engine) as db, db.begin():
        user = db.get(User, user_id)
        # The fixtures' `.test` addresses are reserved names, which login refuses.
        user.email = f"{user_id}@example.com"
        user.password_hash = password_hasher.hash(PASSWORD)
        return user.email


@pytest.fixture
def busy(api):
    """One user's execution, its runtime calling under the user's shared gate."""
    h = api
    if h.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks required")
    _, attempt_id = h.ready("activity_read")
    h.email = account(h.engine, h.actor.user_id)
    h.attempt_id = attempt_id
    return h


def runtime_call(h, hold):
    """A runtime call's locks: its gates (workspace, initiator, pool), the runtime's
    rows, the initiator's account rows shared, the attempt's rows (`_locked`)."""
    with Session(h.engine) as db, db.begin():
        h.activities._locked(db, h.principal_runtime, h.attempt_id)
        time.sleep(hold)


def timed(call):
    started = time.monotonic()
    call()
    return round(time.monotonic() - started, 3)


def under_load(h, call, times=3):
    """Each call's wait while four runtime calls overlap without a gap."""
    stop, waits = threading.Event(), []

    def calls(offset):
        time.sleep(offset)
        while not stop.is_set():
            try:
                runtime_call(h, 0.2)
            except ExecutionError:
                pass  # refused once the identity is revoked: still a caller

    with ThreadPoolExecutor(max_workers=4) as workers:
        loops = [workers.submit(calls, offset) for offset in (0, 0.05, 0.1, 0.15)]
        time.sleep(0.5)
        try:
            for _ in range(times):
                waits.append(timed(call))
                time.sleep(0.1)
        finally:
            stop.set()
        for loop in loops:
            loop.result(timeout=10)
    return waits


def test_a_login_finishes_within_2s_while_the_users_runtime_calls_run(busy):
    h = busy

    def login():
        response = h.client.post(
            "/api/v1/auth/login", json={"email": h.email, "password": PASSWORD}
        )
        assert response.status_code in (200, 202), response.text

    idle = timed(login)  # the call's own cost, uncontended
    waits = under_load(h, login)
    print("login waits (s):", waits, "idle:", idle)
    assert max(waits) < 2.0, waits


def test_a_password_reset_finishes_within_2s_while_the_users_runtime_calls_run(busy):
    h = busy

    def reset():
        response = h.client.post("/api/v1/auth/password/reset/request", json={"email": h.email})
        assert response.status_code == 202, response.text

    idle = timed(reset)  # the call's own cost, uncontended
    waits = under_load(h, reset)
    print("password reset waits (s):", waits, "idle:", idle)
    assert max(waits) < 2.0, waits


def test_a_login_and_an_execution_write_for_one_user_never_deadlock(busy):
    h = busy
    deadlocks, stop = [], threading.Event()

    def guarded(call):
        try:
            call()
        except OperationalError as error:
            if "deadlock" in str(error.orig).lower():
                deadlocks.append(str(error.orig))
            else:
                raise

    def writes():
        while not stop.is_set():
            guarded(lambda: runtime_call(h, 0.01))

    def logins():
        for _ in range(10):
            guarded(
                lambda: h.client.post(
                    "/api/v1/auth/login", json={"email": h.email, "password": PASSWORD}
                )
            )
        stop.set()

    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = [workers.submit(writes) for _ in range(3)] + [workers.submit(logins)]
        for future in futures:
            future.result(timeout=120)
    assert deadlocks == []
