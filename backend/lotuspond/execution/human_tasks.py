"""E05 human tasks: assignment, claim, decision, submission, escalation and expiry (Task 5).

A task backs one interpreter-v2 `human_task` step's wait. Every action rechecks
current authority after its locks, in the frozen lock order: the workspace and the
actor's account rows (`reauthorize`), then the execution, then the task, then its
intent. Eligibility is the actor's current membership role in the task's
assignment group, plus its escalation group once escalated; API keys, runtimes,
viewers and platform administrators never claim, decide or submit (the `review`
operation refuses keys and viewers). A task is visible and actionable only once
its wait is retained (E04 finding 2): before that it is listed nowhere, `get` and
`proposal` answer `not_found`, and actions answer `conflict`.
"""

from __future__ import annotations

import json

from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.actions import (
    HumanDecision,
    canonical_decision_json,
    check_decision,
    decision_digest,
)
from sqlalchemy import exists, select

from ..fleet.authority import database_now_ms
from ..models import (
    Execution,
    ExecutionActionDecision,
    ExecutionActionIntent,
    ExecutionDeploymentRevision,
    ExecutionHumanTask,
    ExecutionInbox,
    ExecutionStepInstance,
    ExecutionWait,
    new_id,
)
from .action_contracts import (
    TASK_CLAIM_MS,
    DecisionRequest,
    FormView,
    HumanTaskView,
    ProposalView,
    SubmissionRequest,
    assignment_roles,
    decision_context,
)
from .actions import load_intent, record_sdk_event
from .artifacts import ArtifactPolicy
from .authorization import mark_replay, reauthorize, record, scoped
from .contracts import SignalRequest
from .control import retained_clause
from .deployments import evaluation_only, execution_gates
from .errors import ExecutionError
from .orchestrator import TERMINAL, mark_runnable
from .progression import record_event


def _scope(row):
    from lotuspond_runtime.protocol import Scope

    return Scope(
        workspace_id=row.workspace_id, project_id=row.project_id, environment_id=row.environment_id
    )


def _retained(db, task) -> bool:
    return bool(
        db.scalar(
            select(ExecutionWait.id)
            .where(ExecutionWait.id == task.wait_id)
            .where(retained_clause())
        )
    )


def _live_claim(task, now):
    return task.claim_user_id is not None and task.claim_expires_at_ms > now


def eligible(task, role) -> bool:
    groups = [task.assignment]
    if task.escalated_at_ms is not None and task.escalation_assignment is not None:
        groups.append(task.escalation_assignment)
    return any(role in assignment_roles(group) for group in groups)


def task_view(task, actor, now) -> HumanTaskView:
    claim = "none"
    if _live_claim(task, now):
        claim = "caller" if task.claim_user_id == actor.user_id else "other"
    intent_sha256 = None
    if task.intent_id is not None:
        intent_sha256 = task.intent_sha256
    return HumanTaskView(
        id=task.id,
        execution_id=task.execution_id,
        kind=task.kind,
        state=task.state,
        deadline_ms=task.deadline_ms,
        escalated=task.escalated_at_ms is not None,
        escalate_at_ms=task.escalate_at_ms,
        claim=claim,
        claim_expires_at_ms=task.claim_expires_at_ms if claim != "none" else None,
        intent_sha256=intent_sha256,
    )


class HumanTaskService:
    def __init__(
        self, *, artifacts, budgets, recovery, artifact_policy, installation_id, inbox=None
    ):
        if any(item is None for item in (artifacts, budgets, recovery)):
            raise ValueError("Artifacts, budgets and recovery are required")
        self.artifacts, self.budgets, self.recovery = artifacts, budgets, recovery
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        self.installation_id = installation_id
        self.inbox = inbox

    # ------------------------------------------------------------------ loading

    def _epoch(self, db):
        try:
            epoch = self.recovery.current(db)
            if type(epoch) is not int or epoch < 0:
                raise ValueError
            return epoch
        except Exception:
            raise ExecutionError("recovery_unavailable") from None

    @staticmethod
    def _gates(db, actor, task_id, operation="review"):
        """The fair gates first, before any row lock (`authority_gate`), as signals do."""
        hint = record(db, ExecutionHumanTask, actor.scope, task_id)
        execution_gates(db, actor.scope, hint.execution_id, operation, [actor.user_id])

    def _locked(self, db, actor, task_id, *, actionable=True):
        """Execution, then task, then intent, each locked; the actor reauthorized first.

        Lock order (plan, "Decisions lock the task before the intent's execution row
        is reloaded under lock"): every task, decision and intent row is taken only
        while the execution row is held, so no two paths can invert them.
        """
        hint = record(db, ExecutionHumanTask, actor.scope, task_id)
        execution = record(db, Execution, actor.scope, hint.execution_id, locked=True)
        task = record(db, ExecutionHumanTask, actor.scope, task_id, locked=True)
        if task.execution_id != execution.id:
            raise ExecutionError("not_found")
        if not _retained(db, task):
            raise ExecutionError("conflict" if actionable else "not_found")
        intent = None
        if task.intent_id is not None:
            intent = record(db, ExecutionActionIntent, actor.scope, task.intent_id, locked=True)
            task.intent_sha256 = intent.intent_sha256
        return execution, task, intent

    def _event(self, db, execution, code):
        record_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            self._epoch(db),
            code,
        )

    @staticmethod
    def _open_check(execution, task, now):
        if (
            execution.requested_state == "cancelled"
            or execution.state in TERMINAL
            or execution.state not in {"running", "waiting", "paused"}
        ):
            raise ExecutionError("forbidden")
        if task.state != "open" or task.deadline_ms <= now:
            raise ExecutionError("conflict")

    # ------------------------------------------------------------------ reads

    def list(self, db, actor, *, state="open", after_id=None, limit=50):
        actor = reauthorize(db, actor, "read")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ExecutionError("invalid_request")
        retained = (
            select(ExecutionWait.id)
            .where(ExecutionWait.id == ExecutionHumanTask.wait_id)
            .where(retained_clause())
        )
        query = scoped(ExecutionHumanTask, actor.scope).where(exists(retained))
        if state is not None:
            query = query.where(ExecutionHumanTask.state == state)
        if after_id is not None:
            if db.scalar(query.where(ExecutionHumanTask.id == after_id)) is None:
                raise ExecutionError("not_found")
            query = query.where(ExecutionHumanTask.id > after_id)
        rows = list(db.scalars(query.order_by(ExecutionHumanTask.id).limit(limit + 1)))
        now = database_now_ms(db)
        items = []
        for task in rows[:limit]:
            if task.intent_id is not None:
                task.intent_sha256 = db.scalar(
                    select(ExecutionActionIntent.intent_sha256).where(
                        ExecutionActionIntent.id == task.intent_id
                    )
                )
            items.append(task_view(task, actor, now))
        return {"items": items, "next_after_id": rows[limit - 1].id if len(rows) > limit else None}

    def get(self, db, actor, task_id) -> HumanTaskView:
        actor = reauthorize(db, actor, "read")
        task = record(db, ExecutionHumanTask, actor.scope, task_id)
        if not _retained(db, task):
            raise ExecutionError("not_found")
        if task.intent_id is not None:
            task.intent_sha256 = record(
                db, ExecutionActionIntent, actor.scope, task.intent_id
            ).intent_sha256
        return task_view(task, actor, database_now_ms(db))

    def _values(self, db, actor, intent):
        """Both proposal values, read under the reviewer's current access."""
        values = []
        for prefix in ("target", "input"):
            handle = ArtifactHandle(
                id=getattr(intent, f"{prefix}_artifact_id"),
                version=getattr(intent, f"{prefix}_artifact_version"),
                sha256=getattr(intent, f"{prefix}_sha256"),
            )
            values.append(json.loads(self.artifacts.get(db, actor, handle)))
        return values

    def proposal(self, db, actor, task_id) -> ProposalView:
        """Both values, to a person who may read the execution (Task 11: viewers read
        proposals too) and can currently read both proposal artifacts. A read: no row
        locks, as `get`; never to a key, which holds no person's review."""
        actor = reauthorize(db, actor, "read")
        if actor.api_key_id is not None or actor.user_id is None:
            raise ExecutionError("forbidden")
        task = record(db, ExecutionHumanTask, actor.scope, task_id)
        if not _retained(db, task) or task.kind != "review" or task.intent_id is None:
            raise ExecutionError("not_found")
        execution = record(db, Execution, actor.scope, task.execution_id)
        intent = record(db, ExecutionActionIntent, actor.scope, task.intent_id)
        verified = load_intent(db, actor.scope, intent.id, artifacts=self.artifacts)
        target, value = self._values(db, actor, intent)
        b = verified.intent.binding
        policy = self._policy(db, actor, execution, b.policy_sha256)
        return ProposalView(
            intent_sha256=intent.intent_sha256,
            connector_name=b.connector_name,
            connector_version=b.connector_version,
            operation=b.operation,
            policy_name=policy.name,
            policy_version=policy.version,
            approval_mode=b.approval_mode,
            independent_approval=b.independent_approval,
            requester_id=b.requester_id,
            issued_at_ms=b.issued_at_ms,
            expires_at_ms=b.expires_at_ms,
            target=target,
            input=value,
        )

    def form(self, db, actor, task_id) -> FormView:
        """The declared schema of a form task, to anyone who may read the execution. The
        schema is the pinned package's, so a person sees exactly what a submission must
        be; it carries no execution value. A read: no row locks, as `get`."""
        from .program import ProgramError, load_program, resolve_occurrence

        actor = reauthorize(db, actor, "read")
        task = record(db, ExecutionHumanTask, actor.scope, task_id)
        if not _retained(db, task) or task.kind != "form":
            raise ExecutionError("not_found")
        execution = record(db, Execution, actor.scope, task.execution_id)
        step = record(db, ExecutionStepInstance, actor.scope, task.step_instance_id)
        try:
            ref = resolve_occurrence(load_program(db, actor.scope, execution), step.occurrence_path)
        except ProgramError:
            raise ExecutionError("dependency_unavailable") from None
        node = ref.step
        if (
            ref.selector is not None
            or ref.step_id != step.node_id
            or node.kind != "human_task"
            or node.mode != "form"
        ):
            raise ExecutionError("dependency_unavailable")
        return FormView(
            form_schema=node.form_schema.model_dump(mode="json"),
            definitions=[item.model_dump(mode="json") for item in ref.package.schemas],
        )

    @staticmethod
    def _policy(db, actor, execution, policy_sha256):
        from .program import load_program

        program = load_program(db, actor.scope, execution)
        document = program.catalog.get(policy_sha256)
        if document is None or document.kind != "policy":
            raise ExecutionError("dependency_unavailable")
        return document

    # ------------------------------------------------------------------ claims

    def claim(self, db, actor, task_id) -> HumanTaskView:
        self._gates(db, actor, task_id)
        actor = reauthorize(db, actor, "review")
        execution, task, _ = self._locked(db, actor, task_id)
        now = database_now_ms(db)
        self._open_check(execution, task, now)
        actor = reauthorize(db, actor, "review")  # current role after the lock waits
        if not eligible(task, actor.role):
            raise ExecutionError("forbidden")
        if _live_claim(task, now) and task.claim_user_id != actor.user_id:
            raise ExecutionError("conflict")
        task.claim_user_id = actor.user_id
        task.claim_expires_at_ms = min(now + TASK_CLAIM_MS, task.deadline_ms)
        task.claim_revision = (task.claim_revision or 0) + 1
        db.flush()
        self._event(db, execution, "task_claimed")
        return task_view(task, actor, now)

    def release(self, db, actor, task_id) -> HumanTaskView:
        self._gates(db, actor, task_id)
        actor = reauthorize(db, actor, "review")
        execution, task, _ = self._locked(db, actor, task_id)
        now = database_now_ms(db)
        actor = reauthorize(db, actor, "review")
        if task.claim_user_id is None:
            mark_replay(db)  # nothing held: nothing changes
            return task_view(task, actor, now)
        if task.claim_user_id != actor.user_id and actor.role not in {"owner", "admin"}:
            raise ExecutionError("forbidden")
        task.claim_user_id = task.claim_expires_at_ms = task.claim_revision = None
        db.flush()
        self._event(db, execution, "task_released")
        return task_view(task, actor, now)

    def _holder_check(self, execution, task, actor, now):
        self._open_check(execution, task, now)
        if not eligible(task, actor.role):
            raise ExecutionError("forbidden")
        if not _live_claim(task, now) or task.claim_user_id != actor.user_id:
            raise ExecutionError("conflict")

    # ------------------------------------------------------------------ decisions

    def decide(self, db, actor, task_id, request: DecisionRequest) -> HumanTaskView:
        request = DecisionRequest.model_validate(request)
        self._gates(db, actor, task_id)
        actor = reauthorize(db, actor, "review")
        execution, task, intent = self._locked(db, actor, task_id)
        if task.kind != "review" or intent is None:
            raise ExecutionError("conflict")
        existing = db.scalar(
            scoped(ExecutionActionDecision, actor.scope).where(
                ExecutionActionDecision.task_id == task.id
            )
        )
        now = database_now_ms(db)
        if existing is not None:
            # An equal retry replays the original; anything else on a decided task conflicts.
            if (
                existing.decision_id == request.decision_id
                and existing.decision == request.decision
                and existing.actor_user_id == actor.user_id
                and intent.intent_sha256 == request.intent_sha256
            ):
                mark_replay(db)
                return task_view(task, actor, now)
            raise ExecutionError("conflict")
        # 1. current role after every lock wait; 2. live claim and eligibility.
        actor = reauthorize(db, actor, "review")
        self._holder_check(execution, task, actor, now)
        # 3. independence, and current access to both proposal values.
        if intent.independent_approval and actor.user_id == intent.requester_user_id:
            raise ExecutionError("forbidden")  # SDK `independence_required`
        self._values(db, actor, intent)
        # An evaluation deployment proves its package and never writes: its reviews are
        # recorded and cancelled by the package evaluation, and none may be allowed.
        if request.decision == "allow" and evaluation_only(
            record(db, ExecutionDeploymentRevision, actor.scope, execution.deployment_revision_id)
        ):
            raise ExecutionError("forbidden")
        # 4. the intent awaits review, unexpired, and the quoted digest is the stored one.
        if intent.authorization_state != "awaiting_review" or intent.expires_at_ms <= now:
            raise ExecutionError("conflict")
        if request.intent_sha256 != intent.intent_sha256:
            raise ExecutionError("conflict")
        verified = load_intent(
            db,
            actor.scope,
            intent.id,
            artifacts=self.artifacts,
            installation_id=self.installation_id,
        )
        # 5. the SDK decision, checked against an authoritative binding context.
        decision = HumanDecision(
            decision_id=request.decision_id,
            intent_sha256=intent.intent_sha256,
            decision=request.decision,
            actor_id=actor.user_id,
            actor_kind="human",
            policy_sha256=verified.intent.binding.policy_sha256,
            issued_at_ms=now,
            expires_at_ms=intent.expires_at_ms,
        )
        context = decision_context(verified.intent, now).model_copy(
            update={"recovery_epoch": self._epoch(db)}
        )
        report = check_decision(verified.intent, decision, context)
        expected = "binding_valid" if request.decision == "allow" else "approval_denied"
        if report.code != expected:
            if report.code in {"independence_required", "invalid_approver"}:
                raise ExecutionError("forbidden")
            raise ExecutionError("conflict")
        # 6. persist, close the task, the SDK event, and make the execution runnable.
        row = ExecutionActionDecision(
            **actor.scope.model_dump(),
            id=new_id(),
            execution_id=execution.id,
            task_id=task.id,
            intent_id=intent.id,
            intent_expires_at_ms=intent.expires_at_ms,
            decision_id=request.decision_id,
            decision_bytes=canonical_decision_json(decision),
            decision_sha256=decision_digest(decision),
            decision=request.decision,
            actor_user_id=actor.user_id,
            issued_at_ms=now,
            expires_at_ms=intent.expires_at_ms,
        )
        db.add(row)
        task.state = "decided"
        task.claim_user_id = task.claim_expires_at_ms = task.claim_revision = None
        db.flush()
        epoch = self._epoch(db)
        record_sdk_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "decision_recorded",
            {"decision": json.loads(decision.model_dump_json())},
        )
        self._event(db, execution, "task_decided")
        mark_runnable(db, execution, now)
        return task_view(task, actor, now)

    # ------------------------------------------------------------------ forms (v2)

    def submit(self, db, actor, task_id, request: SubmissionRequest) -> HumanTaskView:
        request = SubmissionRequest.model_validate(request)
        if self.inbox is None:
            raise ExecutionError("authority_unavailable")
        # The inbox takes the fair gates and then reauthorizes; nothing locks before it.
        hint = record(db, ExecutionHumanTask, actor.scope, task_id)
        if hint.kind != "form":
            raise ExecutionError("conflict")
        state = {}

        def task_check(current_actor):
            # Called by the inbox under its execution, step and wait locks.
            if "task" not in state:
                state["task"] = record(db, ExecutionHumanTask, actor.scope, task_id, locked=True)
            task = state["task"]
            if not _retained(db, task):
                raise ExecutionError("conflict")
            execution = record(db, Execution, actor.scope, task.execution_id)
            now = database_now_ms(db)
            if task.state == "submitted":
                # Only a replay of the submission that closed the task; the inbox then
                # judges it by its own record (digest and wait).
                replay = db.scalar(
                    scoped(ExecutionInbox, actor.scope).where(
                        ExecutionInbox.execution_id == task.execution_id,
                        ExecutionInbox.signal_id == request.submission_id,
                    )
                )
                if replay is None:
                    raise ExecutionError("conflict")
                return
            self._holder_check(execution, task, current_actor, now)

        receipt = self.inbox.accept_signal(
            db,
            actor,
            hint.execution_id,
            SignalRequest(
                signal_id=request.submission_id, wait_id=hint.wait_id, payload=request.payload
            ),
            task_check=task_check,
        )
        task = state.get("task") or record(
            db, ExecutionHumanTask, actor.scope, task_id, locked=True
        )
        now = database_now_ms(db)
        if task.state == "open":
            task.state = "submitted"
            task.claim_user_id = task.claim_expires_at_ms = task.claim_revision = None
            db.flush()
            execution = record(db, Execution, actor.scope, task.execution_id, locked=True)
            self._event(db, execution, "task_submitted")
        del receipt
        return task_view(task, actor, now)


# ---------------------------------------------------------------- sweeper hooks


def _claim_execution(db, task_id):
    from .sweeper import ExecutionSweeper

    hint = db.scalar(select(ExecutionHumanTask).where(ExecutionHumanTask.id == task_id))
    if hint is None:
        return None, None
    scope = _scope(hint)
    execution = ExecutionSweeper._claim(db, scope, hint.execution_id)
    if execution is None or execution.state in TERMINAL:
        return None, None
    task = record(db, ExecutionHumanTask, scope, task_id, locked=True)
    return execution, task


def escalate_task(db, task_id, *, epoch, event):
    """At half the timeout: the escalation group becomes eligible and any claim ends."""
    execution, task = _claim_execution(db, task_id)
    if task is None:
        return False
    now = database_now_ms(db)
    if (
        task.state != "open"
        or task.escalate_at_ms is None
        or task.escalated_at_ms is not None
        or task.escalate_at_ms > now
    ):
        return False
    task.escalated_at_ms = now
    task.claim_user_id = task.claim_expires_at_ms = task.claim_revision = None
    db.flush()
    event(execution, "task_escalated")
    mark_runnable(db, execution, now)
    return True


def expire_claim(db, task_id, *, epoch, event):
    execution, task = _claim_execution(db, task_id)
    if task is None:
        return False
    now = database_now_ms(db)
    if task.claim_user_id is None or task.claim_expires_at_ms > now:
        return False
    task.claim_user_id = task.claim_expires_at_ms = task.claim_revision = None
    db.flush()
    event(execution, "task_released")
    mark_runnable(db, execution, now)
    return True


def expire_task(db, execution, wait, *, event):
    """With its wait: an open task past its deadline expires, and its intent's review."""
    task = db.scalar(
        scoped(ExecutionHumanTask, _scope(wait)).where(ExecutionHumanTask.wait_id == wait.id)
    )
    if task is None:
        return False
    task = record(db, ExecutionHumanTask, _scope(wait), task.id, locked=True)
    if task.state != "open":
        return False
    task.state = "expired"
    task.claim_user_id = task.claim_expires_at_ms = task.claim_revision = None
    if task.intent_id is not None:
        intent = record(db, ExecutionActionIntent, _scope(wait), task.intent_id, locked=True)
        if intent.authorization_state == "awaiting_review":
            intent.authorization_state = "review_expired"
    db.flush()
    event(execution, "task_expired")
    return True
