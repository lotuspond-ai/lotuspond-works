import pytest
from sqlalchemy.orm import Session

from lotuspond.execution.authorization import reauthorize
from lotuspond.execution.errors import ExecutionError

from .test_budgets import budget_harness as budget_harness
from .test_budgets import demand
from .test_budgets import harness as harness


def test_missing_entitlement_refuses_new_work_but_read_survives(budget_harness):
    h = budget_harness
    h.budgets.entitlement = None
    with Session(h.engine) as db, db.begin():
        with pytest.raises(ExecutionError, match="entitlement_unavailable"):
            h.budgets.reserve_admission(db, h.actor, h.revision_id)
        assert reauthorize(db, h.actor, "read").user_id == h.actor.user_id


def test_missing_price_refuses_dispatch(budget_harness):
    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
        h.budgets.charges = None
        with pytest.raises(ExecutionError, match="price_unavailable"):
            h.budgets.reserve_attempt(db, h.actor, attempt, demand())


def test_expired_entitlement_after_lock_wait(budget_harness):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic

    from sqlalchemy import select, text

    from lotuspond.fleet.authority import database_now_ms
    from lotuspond.models import ExecutionCapacity

    h = budget_harness
    started = Event()
    pid = []

    def reserve():
        with Session(h.engine) as db, db.begin():
            pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                h.budgets.reserve_admission(db, h.actor, h.revision_id)
            except ExecutionError as error:
                return error.code

    with ThreadPoolExecutor(1) as pool:
        with Session(h.engine) as blocker, blocker.begin():
            blocker.scalar(select(ExecutionCapacity).with_for_update())
            blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
            future = pool.submit(reserve)
            assert started.wait(5)
            deadline = monotonic() + 5
            with h.engine.connect() as observer:
                while monotonic() < deadline:
                    if observer.scalar(
                        text("SELECT :blocker = ANY(pg_blocking_pids(:pid))"),
                        {"blocker": blocker_pid, "pid": pid[0]},
                    ):
                        break
                else:
                    pytest.fail("Reservation never waited on actual capacity row")
            h.provider.expiry = database_now_ms(blocker)
        assert future.result(timeout=10) == "entitlement_unavailable"
    assert pid[0] != blocker_pid


@pytest.mark.parametrize(
    "provider,code",
    [("tenant_policy", "authority_unavailable"), ("recovery", "recovery_unavailable")],
)
def test_missing_required_providers_deny(budget_harness, provider, code):
    h = budget_harness
    setattr(h.budgets, provider, None)
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match=code):
        h.budgets.reserve_admission(db, h.actor, h.revision_id)


def test_entitlement_expiry_after_charge_wait(budget_harness):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from lotuspond.fleet.authority import database_now_ms

    h = budget_harness
    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
    entered, release = Event(), Event()

    class Charges:
        def bound(self, dependency, demand):
            entered.set()
            assert release.wait(5)
            return demand

    h.budgets.charges = Charges()

    def reserve():
        with Session(h.engine) as db, db.begin():
            try:
                h.budgets.reserve_attempt(db, h.actor, attempt, demand())
            except ExecutionError as error:
                return error.code

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(reserve)
        try:
            assert entered.wait(5)
            with Session(h.engine) as db:
                h.provider.expiry = database_now_ms(db)
        finally:
            release.set()
        assert future.result(timeout=10) == "entitlement_unavailable"


def test_provider_failure_is_fixed_and_synthetic_selection_is_constructor_only(
    budget_harness, caplog
):
    from lotuspond.execution.budgets import BudgetService

    h = budget_harness
    with pytest.raises(ValueError, match="Synthetic providers"):
        BudgetService(
            entitlement=h.provider,
            tenant_policy=h.provider,
            charges=h.provider,
            recovery=h.budgets.recovery,
            installation_id="test-installation",
            deployments=h.dependencies,
        )

    class Broken:
        def bound(self, dependency, demand):
            raise RuntimeError("PRIVATE_PRICE_SENTINEL")

    with Session(h.engine) as db, db.begin():
        attempt, _ = h.add(db)
        h.budgets.charges = Broken()
        with pytest.raises(ExecutionError) as error:
            h.budgets.reserve_attempt(db, h.actor, attempt, demand())
        assert str(error.value) == "price_unavailable"
        assert "PRIVATE_PRICE_SENTINEL" not in caplog.text
