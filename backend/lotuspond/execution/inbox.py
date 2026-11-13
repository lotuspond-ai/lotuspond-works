"""Wait-bound, typed encrypted inbox; acceptance never satisfies a wait.

Only persisted open waits are eligible. Future trusted progression consumes these
records and rechecks their exact identity/expiry; it cannot infer authority from
an inbox token. Transactions/commit-before-HTTP-ack belong to the caller.
"""

from hashlib import sha256

from lotuspond_sdk.executable.input import project_json
from lotuspond_sdk.executable.types import validate_value

from ..fleet.authority import database_now_ms
from ..models import (
    Execution,
    ExecutionInbox,
    ExecutionOutbox,
    ExecutionStepInstance,
    ExecutionWait,
    new_id,
)
from .action_contracts import INTERPRETER_V2
from .admission import _current_deployment
from .artifacts import ArtifactPolicy
from .authorization import lock, mark_replay, reauthorize, record, scoped
from .contracts import SignalReceipt, SignalRequest, canonical_value
from .control import wait_retained
from .deployments import execution_gates
from .errors import ExecutionError, execution_service
from .orchestrator import mark_runnable
from .program import ProgramError, program_from, resolve_occurrence


class InboxService:
    def __init__(self, *, budgets, artifacts, artifact_policy):
        self.budgets, self.artifacts = budgets, artifacts
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        if self.policy.source_id is not None:
            raise ValueError("Signals require workspace response policy")

    def _deployment(self, db, actor, revision_id):
        return _current_deployment(
            db, actor, revision_id, dependencies=self.budgets.deployments, operation="signal"
        )

    @staticmethod
    def _node(package, sources, step, *, tasked=False, v2=False):
        # The exact persisted occurrence path selects one node of one package;
        # an unresolved path is refused, never guessed from caller data.
        try:
            ref = resolve_occurrence(program_from(package, sources), step.occurrence_path)
        except ProgramError:
            raise ExecutionError("dependency_unavailable") from None
        if ref.selector is not None or ref.step_id != step.node_id:
            raise ExecutionError("dependency_unavailable")
        package, node = ref.package, ref.step
        if node.kind not in {"external_wait", "human_task"}:
            raise ExecutionError("forbidden")
        if node.kind == "human_task" and node.mode != "form":
            raise ExecutionError("unsupported_step")
        if node.kind == "human_task" and v2 and not tasked:
            # A v2 form is a human task: submitted only through its task rules.
            raise ExecutionError("forbidden")
        declaration = next(
            (p for p in package.policies if p.id == node.assignment_policy_ref), None
        )
        policies = [
            s
            for s in sources.values()
            if s.get("kind") == "policy"
            and declaration is not None
            and s.get("name") == declaration.ref.name
            and s.get("version") == declaration.ref.version
        ]
        if len(policies) != 1:
            raise ExecutionError("unsupported_policy")
        # E04's one form assignment; a task-checked (v2) form uses its task's instead.
        if not tasked and policies[0]["definition"]["assignment"] != "workspace_admins":
            raise ExecutionError("unsupported_policy")
        return package, node

    @execution_service
    def accept_signal(
        self, db, actor, execution_id: str, request: SignalRequest, *, task_check=None
    ) -> SignalReceipt:
        """E04's signal path. With `task_check` (E05: a v2 form submitted through its
        human task) the task's claim and eligibility replace E04's owner/admin rule,
        under the `review` operation; the typed, encrypted inbox path is the same."""
        operation = "review" if task_check is not None else "signal"
        # The fair gates first, before any row lock (`authority_gate`).
        execution_gates(db, actor.scope, execution_id, operation, [actor.user_id])
        actor = reauthorize(db, actor, operation)  # first row lock: the workspace row
        request = SignalRequest.model_validate(request)
        # Own the nested JSON graph before provider waits, preserving exact numeric
        # types until the declared wait schema validates them (including replays).
        request = SignalRequest(
            signal_id=request.signal_id,
            wait_id=request.wait_id,
            payload=project_json(request.payload),
        )
        hint = record(db, Execution, actor.scope, execution_id)
        package, sources = self._deployment(db, actor, hint.deployment_revision_id)
        execution = record(db, Execution, actor.scope, execution_id, locked=True)
        wait_hint = record(db, ExecutionWait, actor.scope, request.wait_id)
        if wait_hint.execution_id != execution.id:
            raise ExecutionError("not_found")
        step = record(
            db, ExecutionStepInstance, actor.scope, wait_hint.step_instance_id, locked=True
        )
        wait = record(db, ExecutionWait, actor.scope, request.wait_id, locked=True)
        if (
            step.execution_id != execution.id
            or wait.execution_id != execution.id
            or wait.step_instance_id != step.id
        ):
            raise ExecutionError("not_found")
        digest = sha256(
            canonical_value(
                {
                    "operation": "signal",
                    "execution_id": execution.id,
                    **request.model_dump(mode="json"),
                }
            )
        ).hexdigest()

        def current(*, typed=True, state=True):
            package, sources = self._deployment(db, actor, execution.deployment_revision_id)
            source_package, node = self._node(
                package,
                sources,
                step,
                tasked=task_check is not None,
                v2=execution.interpreter_id == INTERPRETER_V2,
            )
            if task_check is not None:
                current_actor = reauthorize(db, actor, "review")
                task_check(current_actor)
            else:
                current_actor = reauthorize(
                    db, actor, "human_form" if node.kind == "human_task" else "signal"
                )
                if current_actor.role not in {"owner", "admin"}:
                    raise ExecutionError("forbidden")
            now = database_now_ms(db)
            # E04 finding 2 (owner decision, 2 November 2026): an open, unexpired wait of
            # a live execution that the capacity step has not yet retained is listed
            # nowhere, and its signal is a 409 to retry, never a 403. Read under the
            # execution, step and wait locks that `retain_wait` also takes.
            if (
                state
                and execution.requested_state != "cancelled"
                and execution.state in {"pending", "running", "waiting", "paused"}
                and wait.state == "open"
                and min(wait.deadline_ms, execution.deadline_ms) > now
                and not wait_retained(db, wait)
            ):
                raise ExecutionError("conflict")
            # A paused execution still accepts signals; progression consumes them on
            # resume, judged by their acceptance time (Task 11).
            if state and (
                execution.requested_state == "cancelled"
                or execution.state not in {"running", "waiting", "paused"}
                or wait.state != "open"
                or min(wait.deadline_ms, execution.deadline_ms) <= now
            ):
                raise ExecutionError("forbidden")
            if typed:
                schema = node.form_schema if node.kind == "human_task" else node.signal_schema
                validate_value(request.payload, schema, source_package.schemas)
            self.artifacts._current_time(db, self.policy, None)

        # Current authority first, in the same lock order as before the inbox row.
        current(typed=False, state=False)
        existing = lock(
            db,
            scoped(ExecutionInbox, actor.scope).where(
                ExecutionInbox.execution_id == execution.id,
                ExecutionInbox.signal_id == request.signal_id,
            ),
        )
        if existing is not None:
            if existing.digest != digest or existing.wait_id != wait.id:
                raise ExecutionError("conflict")
            # A signal that already took effect replays its receipt whatever the wait's
            # state now: a retry after a lost answer learns it was accepted (Task 13).
            # One not yet consumed is still judged by the wait, so a late replay of
            # an undelivered signal stays refused (Task 6).
            current(state=existing.status != "delivered")
            mark_replay(db)
            return SignalReceipt(id=existing.id, status=existing.status)
        current()
        handle = self.artifacts._put_signal(
            db, actor, canonical_value(request.payload), self.policy, check_current=current
        )
        row = ExecutionInbox(
            **actor.scope.model_dump(),
            id=new_id(),
            execution_id=execution.id,
            wait_id=wait.id,
            signal_id=request.signal_id,
            digest=digest,
            payload_artifact_id=handle.id,
            payload_artifact_version=handle.version,
            payload_sha256=handle.sha256,
            expires_at_ms=min(wait.deadline_ms, execution.deadline_ms),
            available_at_ms=database_now_ms(db),
        )
        db.add(row)
        db.flush()
        wakeup = ExecutionOutbox(
            **actor.scope.model_dump(),
            execution_id=execution.id,
            message_id=row.id,
            kind="engine_signal",
            digest=digest,
            available_at_ms=database_now_ms(db),
        )
        db.add(wakeup)
        db.flush()
        mark_runnable(db, execution, wakeup.available_at_ms)
        current()
        return SignalReceipt(id=row.id, status=row.status)


@execution_service
def accept_signal(db, actor, execution_id, request, *, service=None):
    if service is None:
        raise ExecutionError("authority_unavailable")
    return service.accept_signal(db, actor, execution_id, request)
