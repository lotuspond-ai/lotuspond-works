"""E05 Task 6: dispatch racing revocation, cancellation and renewal; the cursor a leaf."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.models import Execution, ExecutionActionCommand, ExecutionDeploymentRevision

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..cursor_checks import without_cursor
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import receipt_for, report
from .test_tasks import tasks as tasks  # noqa: F401


def race(*operations):
    barrier = Barrier(len(operations))

    def run(operation):
        barrier.wait(timeout=10)
        try:
            return operation()
        except Exception as error:
            return error

    with ThreadPoolExecutor(len(operations)) as pool:
        return list(pool.map(run, operations))


def no_deadlock(results):
    assert not any(isinstance(r, Exception) and "deadlock" in str(r).lower() for r in results)


def test_dispatch_races_revocation(actions):
    """Either dispatched first, or no command exists: never a deadlock."""
    h = actions
    revoked = h.reviewed("revoke")

    def revoke():
        with Session(h.engine) as db, db.begin():
            execution = db.get(Execution, revoked)
            db.get(
                ExecutionDeploymentRevision, execution.deployment_revision_id
            ).status = "disabled"

    no_deadlock(race(h.poll, revoke))


def test_dispatch_races_cancellation(actions):
    h = actions
    cancelled = h.reviewed("cancel")
    no_deadlock(race(h.poll, lambda: h.control(cancelled, "cancel")))


def test_renewal_races_a_receipt(actions):
    h = actions
    renewed = h.reviewed("renew")
    lease = h.claim(h.poll())

    def renew():
        with Session(h.engine) as db, db.begin():
            return h.dispatcher.renew(db, h.runtime, lease)

    no_deadlock(race(renew, lambda: report(h, receipt_for(h, renewed, lease))))


def test_action_paths_keep_the_cursor_a_leaf(actions):
    h = actions
    execution_id = h.reviewed("cursor")
    delivery = without_cursor(h.engine, h.poll)
    assert delivery is not None
    lease = without_cursor(h.engine, lambda: h.claim(delivery))

    def renew():
        with Session(h.engine) as db, db.begin():
            return h.dispatcher.renew(db, h.runtime, lease)

    without_cursor(h.engine, renew)
    without_cursor(h.engine, lambda: report(h, receipt_for(h, execution_id, lease)))
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionActionCommand)).status == "stopped"
