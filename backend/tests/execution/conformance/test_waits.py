"""Task 13 conformance: waits and signals (spike check 34, REG-5)."""

import pytest
from driver import Refused, external_waits_package, form_then_read, serving
from processes import Processes
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.models import ExecutionInbox, ExecutionWait


def test_an_expired_review_follows_its_expiry_path_with_no_provider_call(system, linux):
    """Spike 34: a form past its deadline is expired by the sweeper; nothing is called.

    A read follows the form and runs only on approval: had the expiry been taken as
    approval, it would have been called. Positive control: approved in time, the same
    read runs on the same runtime.
    """
    w = system
    operator, customer = w.operator, w.customer
    dependencies = [operator.documents[0], "waits.policy"]
    expiring = operator.deploy(
        customer, "form-2s", package=form_then_read(2), dependencies=dependencies
    )
    approving = operator.deploy(
        customer, "form-1h", package=form_then_read(3600), dependencies=dependencies
    )
    runtime = operator.runtime(customer, "runtime-a")
    serving(w, runtime)
    execution_id = operator.start(customer, expiring)["id"]
    wait = operator.open_wait(customer, execution_id, "review")
    view = operator.settled(customer, execution_id, {"succeeded", "failed", "cancelled"})
    assert view["state"] == "failed", view
    with Session(w.engine) as db:
        assert db.get(ExecutionWait, wait["id"]).state == "expired"
    with pytest.raises(Refused):
        operator.signal(customer, execution_id, wait["id"], "form", {"approved": True})
    assert Processes.calls(runtime["counter"]) == 0
    control = operator.start(customer, approving)["id"]
    approval = operator.open_wait(customer, control, "review")
    operator.signal(customer, control, approval["id"], "form", {"approved": True})
    assert operator.settled(customer, control, {"succeeded", "failed"}, 60)["state"] == "succeeded"
    assert Processes.calls(runtime["counter"]) == 1


def test_duplicate_conflicting_out_of_order_and_late_signals(system):
    """REG-5: one satisfied wait; the conflict, the stale and the late signal are refused."""
    w = system
    operator, customer = w.operator, w.customer
    revision = operator.deploy(
        customer,
        "external",
        package=external_waits_package([120, 3]),
        dependencies=["waits.policy"],
    )
    execution_id = operator.start(customer, revision)["id"]
    first = operator.open_wait(customer, execution_id, "signal0")
    receipt = operator.signal(customer, execution_id, first["id"], "a", 7)
    # Duplicate: an equal retry returns the same receipt, and nothing changes.
    assert operator.signal(customer, execution_id, first["id"], "a", 7) == receipt
    # Conflicting: the same signal ID with another payload.
    with pytest.raises(Refused) as conflict:
        operator.signal(customer, execution_id, first["id"], "a", 8)
    assert conflict.value.status == 409
    second = operator.open_wait(customer, execution_id, "signal1")
    # Out of order: a new signal for the first wait after its successor opened.
    with pytest.raises(Refused):
        operator.signal(customer, execution_id, first["id"], "b", 9)
    # Late: the second wait expires unsignalled; a signal after that is refused.
    view = operator.settled(customer, execution_id, {"succeeded", "failed", "cancelled"})
    with pytest.raises(Refused):
        operator.signal(customer, execution_id, second["id"], "c", 10)
    assert view["state"] == "failed", view  # no completion is inferred from a signal
    with Session(w.engine) as db:
        states = {
            row.id: row.state
            for row in db.scalars(
                select(ExecutionWait).where(ExecutionWait.execution_id == execution_id)
            )
        }
        accepted = db.scalar(
            select(func.count())
            .select_from(ExecutionInbox)
            .where(ExecutionInbox.execution_id == execution_id)
        )
    assert states == {first["id"]: "satisfied", second["id"]: "expired"}
    assert accepted == 1
