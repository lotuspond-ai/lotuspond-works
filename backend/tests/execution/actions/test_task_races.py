"""E05 Task 5: decisions racing each other and authority changes, on real connections."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Thread

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.models import ExecutionActionDecision, ExecutionHumanTask, Membership

from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .test_tasks import allow  # noqa: F401
from .test_tasks import reviewing as reviewing
from .test_tasks import tasks as tasks


def test_two_decisions_by_the_holder_record_one(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    barrier = Barrier(2)

    def decide(args):
        decision_id, decision = args
        barrier.wait(timeout=10)
        try:
            return allow(h, reviewer, h.task, decision_id, decision)
        except ExecutionError as error:
            return error

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(decide, [("d-allow", "allow"), ("d-deny", "deny")]))
    assert sum(not isinstance(r, ExecutionError) for r in results) == 1
    assert [r.code for r in results if isinstance(r, ExecutionError)] == ["conflict"]
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionActionDecision)) == 1


def test_a_reviewer_demoted_while_waiting_for_the_task_lock_is_refused(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    locked, released = Event(), Event()
    outcome = {}

    def holder():
        with Session(h.engine) as db, db.begin():
            db.scalar(
                select(ExecutionHumanTask)
                .where(ExecutionHumanTask.id == h.task.id)
                .with_for_update()
            )
            locked.set()
            released.wait(10)
            # Demoted in the same transaction that held the decision back.
            db.scalar(
                select(Membership).where(Membership.user_id == reviewer.user_id)
            ).role = "editor"

    def decider():
        locked.wait(10)
        try:
            outcome["value"] = allow(h, reviewer, h.task)
        except ExecutionError as error:
            outcome["error"] = error.code

    threads = [Thread(target=holder), Thread(target=decider)]
    for thread in threads:
        thread.start()
    released.set()
    for thread in threads:
        thread.join(30)
    assert outcome == {"error": "forbidden"}
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionActionDecision)) == 0
        assert db.get(ExecutionHumanTask, h.task.id).state == "open"
