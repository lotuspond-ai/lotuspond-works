"""Wait-bound, typed encrypted inbox; acceptance never satisfies a wait.

Only persisted open waits are eligible. Future trusted progression consumes these
records and rechecks their exact identity/expiry; it cannot infer authority from
an inbox token. Transactions/commit-before-HTTP-ack belong to the caller.
"""

from hashlib import sha256

from lotuspond_sdk.executable.input import project_json
from lotuspond_sdk.executable.types import validate_value
from lotuspond_sdk.executable.validation import validate_executable_package

from ..fleet.authority import database_now_ms
from ..models import (
    Execution,
    ExecutionInbox,
    ExecutionOutbox,
    ExecutionStepInstance,
    ExecutionWait,
    new_id,
)
from .admission import _current_deployment
from .artifacts import ArtifactPolicy
from .authorization import lock, reauthorize, record, scoped
from .contracts import SignalReceipt, SignalRequest, canonical_value
from .deployments import _body_steps
from .errors import ExecutionError, execution_service
from .orchestrator import mark_runnable


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
    def _node(package, sources, step):
        # Task8 supplies the exact occurrence resolver. Until then ambiguous node
        # IDs across regions/subworkflows are denied, never guessed from caller data.
        packages = [package]
        for source in sources.values():
            if source.get("schema_version") == "lotuspond.workflow-package.v2":
                packages.append(validate_executable_package(source))
        matches = [
            (p, node) for p in packages for node in _body_steps(p.body) if node.id == step.node_id
        ]
        if len(matches) != 1:
            raise ExecutionError("dependency_unavailable")
        package, node = matches[0]
        if node.kind not in {"external_wait", "human_task"}:
            raise ExecutionError("forbidden")
        if node.kind == "human_task" and node.mode != "form":
            raise ExecutionError("unsupported_step")
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
        if len(policies) != 1 or policies[0]["definition"]["assignment"] != "workspace_admins":
            raise ExecutionError("unsupported_policy")
        return package, node

    @execution_service
    def accept_signal(self, db, actor, execution_id: str, request: SignalRequest) -> SignalReceipt:
        self.budgets._cursor(db)
        actor = reauthorize(db, actor, "signal")
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

        def current(*, typed=True):
            package, sources = self._deployment(db, actor, execution.deployment_revision_id)
            source_package, node = self._node(package, sources, step)
            current_actor = reauthorize(
                db, actor, "human_form" if node.kind == "human_task" else "signal"
            )
            if current_actor.role not in {"owner", "admin"}:
                raise ExecutionError("forbidden")
            now = database_now_ms(db)
            if (
                execution.requested_state != "running"
                or execution.state not in {"running", "waiting"}
                or wait.state != "open"
                or min(wait.deadline_ms, execution.deadline_ms) <= now
            ):
                raise ExecutionError("forbidden")
            if typed:
                schema = node.form_schema if node.kind == "human_task" else node.signal_schema
                validate_value(request.payload, schema, source_package.schemas)
            self.artifacts._current_time(db, self.policy, None)

        current(typed=False)
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
            current()
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
