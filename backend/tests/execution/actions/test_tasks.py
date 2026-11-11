"""E05 Task 5: review tasks under current authority, on real PostgreSQL and real time.

Time moves only in real database time (`until`); no deadline, claim or escalation
column is edited.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import INTERPRETER_V2, DecisionRequest
from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.contracts import AdmissionRequest
from lotuspond.execution.engine import PostgresExecutionEngine
from lotuspond.execution.engine_contracts import INTERPRETER_V1, WorkerIdentity
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.human_tasks import HumanTaskService
from lotuspond.execution.inbox import InboxService
from lotuspond.execution.progression import InterpreterV2
from lotuspond.models import (
    Execution,
    ExecutionActionCommand,
    ExecutionActionDecision,
    ExecutionActionIntent,
    ExecutionHumanTask,
    ExecutionOutbox,
    ExecutionRequest,
    Membership,
)
from lotuspond.security import Principal

from ..activity_fixtures import activity as activity  # noqa: F401
from ..api_fixtures import member
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..control_fixtures import until
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .test_admission import deploy, doc, enable, publish

ORCHESTRATOR = WorkerIdentity(
    worker_id="orchestrator-v2",
    role="orchestrator",
    interpreter_ids=[INTERPRETER_V1, INTERPRETER_V2],
)


@pytest.fixture
def tasks(controlled):
    h = controlled
    h.v2 = InterpreterV2(
        artifacts=h.store,
        artifact_policy=h.policy,
        budgets=h.budgets,
        recovery=h.fence,
        installation_id="test-installation",
        test_mode=True,
    )
    h.engine_v2 = PostgresExecutionEngine(
        h.v2, h.sweeper, h.fence, interpreter_ids=[INTERPRETER_V1, INTERPRETER_V2], test_mode=True
    )
    h.inbox = InboxService(budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy)
    h.tasks = HumanTaskService(
        artifacts=h.store,
        budgets=h.budgets,
        recovery=h.fence,
        artifact_policy=h.policy,
        installation_id="test-installation",
        inbox=h.inbox,
    )

    def run(rounds=50):
        for _ in range(rounds):
            if h.engine_v2.progress(h.sessions, ORCHESTRATOR, limit=10).claimed == 0:
                break

    def start(package, key, documents=None, value=None):
        documents = documents or [doc("connector"), doc("reviewers")]
        with Session(h.engine) as db, db.begin():
            publication = publish(h, db, package, documents, key)
            view = deploy(h, db, publication, documents, key, package=package)
            enable(h, db, view, f"{key}-on")
        with Session(h.engine) as db, db.begin():
            admitted = h.service.admit_execution(
                db,
                h.actor,
                AdmissionRequest(deployment_revision_id=view.revision_id, input=value),
                key,
            )
        run()
        return admitted.id

    def reviewer(role="admin"):
        user_id = member(h, role)
        return actor_for(h, user_id, role)

    def call(method, actor, *args):
        with Session(h.engine) as db, db.begin():
            return getattr(h.tasks, method)(db, actor, *args)

    h.run2, h.start2, h.reviewer, h.call = run, start, reviewer, call
    return h


def actor_for(h, user_id, role):
    with Session(h.engine) as db:
        return authorize_execution(
            db,
            Principal(user_id, h.scope.workspace_id, role, user_id),
            h.scope,
            "read",
            locked=False,
        )


def review_package(timeout=600, policy="reviewers", escalation=None):
    package = doc("reviewed_action")
    steps = package["body"]["steps"]
    steps[1]["timeout_seconds"] = timeout
    package["body"]["steps"] = steps[:2] + [{"kind": "timer", "id": "pause", "delay_seconds": 600}]
    documents = [doc("connector"), doc(policy)]
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    package["policies"][0]["ref"].update(
        name=documents[1]["name"], sha256=catalog_document_digest(documents[1])
    )
    if escalation is not None:
        documents.append(doc(escalation))
        package["policies"].append(
            {
                "id": "escalation",
                "ref": {
                    "kind": "policy",
                    "name": documents[2]["name"],
                    "version": "1.0.0",
                    "sha256": catalog_document_digest(documents[2]),
                },
            }
        )
        steps[1]["escalation_policy_ref"] = "escalation"
    return package, documents


def task_of(h, execution_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionHumanTask).where(ExecutionHumanTask.execution_id == execution_id)
        )


def intent_of(h, execution_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionActionIntent).where(ExecutionActionIntent.execution_id == execution_id)
        )


def state_of(h, execution_id):
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        return execution.state, execution.reason


def commands(h):
    with Session(h.engine) as db:
        return db.scalar(select(func.count()).select_from(ExecutionActionCommand))


def allow(h, actor, task, decision_id="d-1", decision="allow", digest=None):
    request = DecisionRequest(
        decision_id=decision_id,
        intent_sha256=digest or intent_of(h, task.execution_id).intent_sha256,
        decision=decision,
    )
    return h.call("decide", actor, task.id, request)


def code_of(callable_, *args):
    with pytest.raises(ExecutionError) as error:
        callable_(*args)
    return error.value.code


@pytest.fixture
def reviewing(tasks):
    h = tasks
    package, documents = review_package()
    h.execution_id = h.start2(package, "review", documents)
    h.task = task_of(h, h.execution_id)
    assert h.task is not None and h.task.state == "open"
    return h


def test_claim_race_has_one_holder(reviewing):
    h = reviewing
    first, second = h.reviewer(), h.reviewer()
    barrier = Barrier(2)

    def claim(actor):
        barrier.wait(timeout=10)
        try:
            return h.call("claim", actor, h.task.id)
        except ExecutionError as error:
            return error

    with ThreadPoolExecutor(2) as pool:
        concurrent_claims = list(pool.map(claim, [first, second]))
    oks = [r for r in concurrent_claims if not isinstance(r, ExecutionError)]
    assert sum(1 for _ in oks) == 1
    assert [r.code for r in concurrent_claims if isinstance(r, ExecutionError)] == ["conflict"]
    task = task_of(h, h.execution_id)
    assert task.claim_expires_at_ms - task.claim_expires_at_ms % 1 <= min(
        task.claim_expires_at_ms, task.deadline_ms
    )
    # 900,000 ms or the deadline, whichever is first (the review times out in 600 s).
    assert task.claim_expires_at_ms == task.deadline_ms


def test_decision_requires_live_claim_and_current_role_after_lock_wait(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    assert code_of(allow, h, reviewer, h.task) == "conflict"  # no claim
    h.call("claim", reviewer, h.task.id)
    # Demoted while waiting: the decision rechecks the current role after its locks.
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Membership).where(Membership.user_id == reviewer.user_id)).role = "editor"
    assert code_of(allow, h, reviewer, h.task) == "forbidden"
    viewer = h.reviewer("viewer")
    assert code_of(h.call, "claim", viewer, h.task.id) == "forbidden"
    # Positive control: a current admin with a live claim decides.
    admin = h.reviewer()
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Membership).where(Membership.user_id == reviewer.user_id)).role = "admin"
    h.call("release", reviewer, h.task.id)
    h.call("claim", admin, h.task.id)
    assert allow(h, admin, h.task).state == "decided"


def test_independence_and_actor_kind(reviewing):
    h = reviewing
    requester = h.actor  # the owner started the execution
    h.call("claim", requester, h.task.id)
    requester_decision_error_code = code_of(allow, h, requester, h.task)
    assert requester_decision_error_code == "forbidden"
    from ..test_admission import key_actor

    key = key_actor(h, ["execute", "read", "execution_signal"])
    assert code_of(h.call, "claim", key, h.task.id) == "forbidden"  # keys never review


def test_quoted_digest_must_match(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    assert code_of(allow, h, reviewer, h.task, "d-1", "allow", "0" * 64) == "conflict"
    assert task_of(h, h.execution_id).state == "open"


def test_decision_idempotency_and_conflict(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    first = allow(h, reviewer, h.task)
    assert allow(h, reviewer, h.task) == first  # an equal retry replays
    assert code_of(allow, h, reviewer, h.task, "d-2") == "conflict"
    assert code_of(allow, h, reviewer, h.task, "d-1", "deny") == "conflict"
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionActionDecision)) == 1


def test_proposal_requires_current_source_access(reviewing, monkeypatch):
    h = reviewing
    reviewer = h.reviewer()
    proposal = h.call("proposal", reviewer, h.task.id)
    assert proposal.target == {"record_id": "R-100"} and proposal.operation == "submit"
    assert proposal.intent_sha256 == intent_of(h, h.execution_id).intent_sha256
    h.call("claim", reviewer, h.task.id)

    def no_access(*args, **kwargs):
        raise ExecutionError("forbidden")

    monkeypatch.setattr(h.store, "_current", no_access)
    assert code_of(h.call, "proposal", reviewer, h.task.id) == "forbidden"
    assert code_of(allow, h, reviewer, h.task) == "forbidden"


def test_escalation_releases_claim_and_widens_eligibility(tasks):
    h = tasks
    package, documents = review_package(timeout=6, policy="escalating", escalation="owners")
    execution_id = h.start2(package, "escalate", documents)
    task = task_of(h, execution_id)
    assert task.escalate_at_ms is not None and task.assignment == "workspace_admins"
    admin = h.reviewer()
    h.call("claim", admin, task.id)
    with Session(h.engine) as db:
        outbox_before = db.scalar(select(func.count()).select_from(ExecutionOutbox))
    until(h, task.escalate_at_ms)
    report = h.sweep()
    assert report.escalated_tasks == 1
    escalated_task = h.call("get", admin, task.id)
    assert escalated_task.escalated is True and escalated_task.claim == "none"
    with Session(h.engine) as db:
        # No notification: nothing is delivered to anyone.
        assert db.scalar(select(func.count()).select_from(ExecutionOutbox)) == outbox_before
    # The owner (escalation group) may now claim; so may the admin again.
    assert h.call("claim", h.actor, task.id).claim == "caller"


def test_task_expiry_fails_without_a_command(tasks):
    h = tasks
    package, documents = review_package(timeout=2)
    execution_id = h.start2(package, "expire", documents)
    task = task_of(h, execution_id)
    until(h, task.deadline_ms)
    h.sweep()
    h.run2()
    assert state_of(h, execution_id)[0] == "failed"
    assert task_of(h, execution_id).state == "expired"
    assert intent_of(h, execution_id).authorization_state == "review_expired"
    action_commands_after_expiry = commands(h)
    assert action_commands_after_expiry == 0


def test_denial_fails_without_a_command(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    assert allow(h, reviewer, h.task, decision="deny").state == "decided"
    h.run2()
    assert state_of(h, h.execution_id)[0] == "failed"
    assert intent_of(h, h.execution_id).authorization_state == "denied"
    assert commands(h) == 0


def test_allow_approves_the_intent_and_continues(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    allow(h, reviewer, h.task)
    h.run2()
    assert intent_of(h, h.execution_id).authorization_state == "approved"
    assert state_of(h, h.execution_id)[0] == "waiting"  # on to the timer


def test_paused_accepts_and_cancelled_refuses(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    h.control(h.execution_id, "pause")
    h.run2()
    assert state_of(h, h.execution_id)[0] == "paused"
    assert allow(h, reviewer, h.task).state == "decided"
    package, documents = review_package()
    other = h.start2(package, "cancelled", documents)
    task = task_of(h, other)
    h.call("claim", reviewer, task.id)
    h.control(other, "cancel")
    assert code_of(allow, h, reviewer, task) == "forbidden"


def test_unretained_task_is_unlisted_and_refuses_with_conflict(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    listed = h.call("list", reviewer)
    assert [t.id for t in listed["items"]] == [h.task.id]
    # Model the moment before the capacity step retains the task's wait.
    with Session(h.engine) as db, db.begin():
        record = db.scalar(
            select(ExecutionRequest).where(
                ExecutionRequest.request_key == "capacity.wait:" + h.task.wait_id
            )
        )
        saved = {c.name: getattr(record, c.name) for c in ExecutionRequest.__table__.columns}
        db.delete(record)
    assert h.call("list", reviewer)["items"] == []
    assert code_of(h.call, "get", reviewer, h.task.id) == "not_found"
    unretained_claim_error_code = code_of(h.call, "claim", reviewer, h.task.id)
    assert unretained_claim_error_code == "conflict"
    assert task_of(h, h.execution_id).claim_user_id is None
    with Session(h.engine) as db, db.begin():
        db.execute(ExecutionRequest.__table__.insert().values(**saved))
    assert h.call("claim", reviewer, h.task.id).claim == "caller"


def test_task_views_carry_no_values(reviewing):
    h = reviewing
    view = h.call("get", h.reviewer(), h.task.id)
    dumped = json.dumps(view.model_dump())
    assert "R-100" not in dumped and "EUR" not in dumped
    assert deepcopy(view) == view


def test_a_reviewer_removed_from_the_workspace_during_a_claim_is_refused(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    with Session(h.engine) as db, db.begin():
        db.delete(db.scalar(select(Membership).where(Membership.user_id == reviewer.user_id)))
    assert code_of(allow, h, reviewer, h.task) == "forbidden"
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionActionDecision)) == 0


def test_a_sweeper_killed_mid_batch_leaves_the_rest_for_the_next_pass(tasks, monkeypatch):
    from lotuspond.execution import human_tasks

    h = tasks
    package, documents = review_package(timeout=6, policy="escalating", escalation="owners")
    first = task_of(h, h.start2(package, "kill-1", documents))
    second = task_of(h, h.start2(deepcopy(package), "kill-2", documents))
    until(h, max(first.escalate_at_ms, second.escalate_at_ms))
    real = human_tasks.escalate_task
    calls = []

    def dies_on_the_second(db, task_id, **kwargs):
        calls.append(task_id)
        if len(calls) == 2:
            raise RuntimeError("sweeper process killed")
        return real(db, task_id, **kwargs)

    monkeypatch.setattr(human_tasks, "escalate_task", dies_on_the_second)
    assert h.sweep().escalated_tasks == 1  # each item in its own transaction
    monkeypatch.setattr(human_tasks, "escalate_task", real)
    assert h.sweep().escalated_tasks == 1  # the next pass finishes the other
    assert task_of(h, first.execution_id).escalated_at_ms is not None
    assert task_of(h, second.execution_id).escalated_at_ms is not None


@pytest.mark.parametrize("change", ["superseded", "withdrawn"])
def test_a_decision_consumed_after_its_intent_closed_authorizes_nothing(reviewing, change):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    allow(h, reviewer, h.task)
    with Session(h.engine) as db, db.begin():
        # Recovery (say) closes the intent between the decision and its consumption.
        db.get(ExecutionActionIntent, intent_of(h, h.execution_id).id).authorization_state = change
    h.run2()
    assert state_of(h, h.execution_id)[0] == "failed"
    assert intent_of(h, h.execution_id).authorization_state == change  # never approved
    assert commands(h) == 0


def test_an_older_epoch_intent_cannot_be_approved(reviewing):
    h = reviewing
    reviewer = h.reviewer()
    h.call("claim", reviewer, h.task.id)
    epoch = h.fence.current
    h.fence.current = lambda db: epoch(db) + 1  # a restore advanced the recovery epoch
    try:
        assert code_of(allow, h, reviewer, h.task) == "conflict"  # SDK `stale_epoch`
    finally:
        h.fence.current = epoch
    assert task_of(h, h.execution_id).state == "open"


def test_only_the_holder_or_an_owner_or_admin_releases_a_claim(reviewing):
    h = reviewing
    holder, editor = h.reviewer(), h.reviewer("editor")
    h.call("claim", holder, h.task.id)
    assert code_of(h.call, "release", editor, h.task.id) == "forbidden"
    assert h.call("release", h.actor, h.task.id).claim == "none"  # the owner may
    h.call("claim", holder, h.task.id)
    assert h.call("release", holder, h.task.id).claim == "none"
