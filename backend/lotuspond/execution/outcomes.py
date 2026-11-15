"""Verified outcomes and traceability (E06, first part).

docs/superpowers/specs/2026-11-15-verified-outcomes-design.md. Read models over committed
rows, without locks, as every execution projection is: an execution's trace (input →
deployed version → action → decision → dispatch → read-back → outcome) and its business
outcome, one rule over the execution's state and its actions' effect and outcome states.

Digests and identifiers only: a proposal's values stay behind the audited proposal route.
The rule never turns uncertainty into success: a write without a verified outcome record
is `unknown`, never `verified`.
"""

from __future__ import annotations

import json

from lotuspond_runtime.protocol import CanonicalUUID
from pydantic import TypeAdapter
from sqlalchemy import select

from ..models import (
    DeploymentAssuranceBinding,
    ExecutablePublication,
    Execution,
    ExecutionActionCommand,
    ExecutionActionDecision,
    ExecutionActionIntent,
    ExecutionActionObservation,
    ExecutionActionOutcome,
    ExecutionActionReceipt,
    ExecutionDeploymentRevision,
    ExecutionHumanTask,
    ExecutionStepInstance,
    User,
)
from ..security import canonical, digest
from .authorization import reauthorize, record, scoped
from .errors import ExecutionError, execution_service

TRACE_FORMAT = "lotuspond.execution-trace.v1"
OUTCOMES = (
    "in_progress",
    "unknown",
    "not_verified",
    "verified",
    "completed",
    "partial",
    "no_effect",
)
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
# A write was made (the target changed, as far as anyone knows).
WRITTEN = frozenset({"accepted", "observed"})
# Whether a write was made is not known.
UNCERTAIN = frozenset({"dispatching", "unknown"})
SUMMARY_LIMIT = 10_000


def business_outcome(state: str, actions) -> tuple[str, bool]:
    """`(outcome, exception)` from the execution's state and each action's
    `(effect_state, outcome_state)`. See the design's decision 2 for the order."""
    actions = list(actions)
    if state == "reconciliation_required":
        return "unknown", True
    if state not in TERMINAL:
        return "in_progress", False
    written = [outcome for effect, outcome in actions if effect in WRITTEN]
    if any(effect in UNCERTAIN for effect, _ in actions) or "unknown" in written:
        return "unknown", True
    if "not_verified" in written:
        return "not_verified", True
    if state == "succeeded":
        return ("verified" if written else "completed"), False
    if written:  # failed or cancelled after verified writes
        return "partial", True
    return "no_effect", state == "failed"


def _ms(value) -> int | None:
    return None if value is None else int(value.timestamp() * 1000)


def _person(db, user_id):
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return {"user_id": user_id, "name": None if user is None else user.name}


class OutcomeProjection:
    """An execution's trace, and the outcome counts of an environment."""

    @execution_service
    def trace(self, db, actor, execution_id: str) -> dict:
        actor = reauthorize(db, actor, "read")
        scope = actor.scope
        execution_id = TypeAdapter(CanonicalUUID).validate_python(execution_id, strict=True)
        execution = record(db, Execution, scope, execution_id)
        revision = record(db, ExecutionDeploymentRevision, scope, execution.deployment_revision_id)
        publication = record(db, ExecutablePublication, scope, revision.publication_id)
        assurance = db.scalar(
            scoped(DeploymentAssuranceBinding, scope).where(
                DeploymentAssuranceBinding.deployment_revision_id == revision.id
            )
        )
        intents = list(
            db.scalars(
                scoped(ExecutionActionIntent, scope)
                .where(ExecutionActionIntent.execution_id == execution.id)
                .order_by(ExecutionActionIntent.issued_at_ms, ExecutionActionIntent.id)
            )
        )
        actions = [self._action(db, scope, intent) for intent in intents]
        outcome, exception = business_outcome(
            execution.state, [(i.effect_state, i.outcome_state) for i in intents]
        )
        trace = {
            "format": TRACE_FORMAT,
            "execution": {
                "id": execution.id,
                "state": execution.state,
                "requested_state": execution.requested_state,
                "reason": execution.reason,
                "admitted_at_ms": execution.admitted_at_ms,
                "deadline_ms": execution.deadline_ms,
                "input_sha256": execution.input_sha256,
                "result_sha256": execution.result_sha256,
                "admission_digest": execution.admission_digest,
                "initiated_by": _person(db, execution.initiating_user_id),
                "initiating_api_key_id": execution.initiating_api_key_id,
            },
            "version": {
                "deployment_id": revision.deployment_id,
                "deployment_revision_id": revision.id,
                "revision": revision.revision,
                "package": publication.name,
                "package_version": publication.version,
                "package_sha256": publication.package_sha256,
                "plan_sha256": publication.plan_sha256,
                "configuration_sha256": revision.configuration_sha256,
                "release_id": None if assurance is None else assurance.release_id,
                "candidate_run_id": None if assurance is None else assurance.candidate_run_id,
            },
            "actions": actions,
            "outcome": {"state": outcome, "exception": exception},
        }
        trace["trace_sha256"] = digest(canonical(trace))
        return trace

    @staticmethod
    def _action(db, scope, intent) -> dict:
        binding = json.loads(intent.binding_bytes)
        step = record(db, ExecutionStepInstance, scope, intent.step_instance_id)
        task = db.scalar(
            scoped(ExecutionHumanTask, scope).where(ExecutionHumanTask.intent_id == intent.id)
        )
        decision = (
            None
            if task is None
            else db.scalar(
                scoped(ExecutionActionDecision, scope).where(
                    ExecutionActionDecision.task_id == task.id
                )
            )
        )
        commands = list(
            db.scalars(
                scoped(ExecutionActionCommand, scope)
                .where(ExecutionActionCommand.intent_id == intent.id)
                .order_by(ExecutionActionCommand.created_at, ExecutionActionCommand.id)
            )
        )
        receipts = list(
            db.scalars(
                scoped(ExecutionActionReceipt, scope)
                .where(ExecutionActionReceipt.intent_id == intent.id)
                .order_by(ExecutionActionReceipt.created_at, ExecutionActionReceipt.id)
            )
        )
        observations = list(
            db.scalars(
                scoped(ExecutionActionObservation, scope)
                .where(ExecutionActionObservation.intent_id == intent.id)
                .order_by(ExecutionActionObservation.created_at, ExecutionActionObservation.id)
            )
        )
        outcome = db.scalar(
            scoped(ExecutionActionOutcome, scope).where(
                ExecutionActionOutcome.intent_id == intent.id
            )
        )
        return {
            "action_id": intent.action_id,
            "step_id": step.node_id,
            "occurrence_path": step.occurrence_path,
            "connector": {
                "name": binding.get("connector_name"),
                "version": binding.get("connector_version"),
                "artifact_sha256": binding.get("connector_artifact_sha256"),
            },
            "operation": binding.get("operation"),
            "intent_sha256": intent.intent_sha256,
            "target_sha256": intent.target_sha256,
            "input_sha256": intent.input_sha256,
            "policy_sha256": binding.get("policy_sha256"),
            "approval_mode": intent.approval_mode,
            "independent_approval": intent.independent_approval,
            "requested_by": _person(db, intent.requester_user_id),
            "issued_at_ms": intent.issued_at_ms,
            "expires_at_ms": intent.expires_at_ms,
            "authorization": intent.authorization_state,
            "effect": intent.effect_state,
            "outcome": intent.outcome_state,
            "review": None
            if task is None
            else {
                "task_id": task.id,
                "state": task.state,
                "decision": None
                if decision is None
                else {
                    "decision": decision.decision,
                    "decided_by": _person(db, decision.actor_user_id),
                    "issued_at_ms": decision.issued_at_ms,
                    "decision_sha256": decision.decision_sha256,
                },
            },
            "dispatches": [
                {
                    "command_id": row.command_id,
                    "command_digest": row.command_digest,
                    "status": row.status,
                    "claimed_at_ms": row.claimed_at_ms,
                }
                for row in commands
            ],
            "receipts": [
                {
                    "status": row.status,
                    "source": row.source,
                    "receipt_sha256": row.receipt_sha256,
                    "recorded_at_ms": _ms(row.created_at),
                }
                for row in receipts
            ],
            "readbacks": [
                {
                    "result": row.result,
                    "qualified": row.qualified,
                    "source": row.source,
                    "rule_id": row.rule_id,
                    "rule_version": row.rule_version,
                    "payload_sha256": row.payload_sha256,
                    "resolved_by": _person(db, row.actor_user_id),
                    "recorded_at_ms": _ms(row.created_at),
                }
                for row in observations
            ],
            "verification": None
            if outcome is None
            else {
                "state": outcome.state,
                "rule_id": outcome.rule_id,
                "rule_version": outcome.rule_version,
                "observed_at_ms": outcome.observed_at_ms,
                "outcome_sha256": outcome.outcome_sha256,
            },
        }

    @execution_service
    def summary(self, db, actor, *, since_ms: int, deployment_id: str | None = None) -> dict:
        """Executions admitted since `since_ms` by business outcome (the newest 10,000)."""
        actor = reauthorize(db, actor, "read")
        scope = actor.scope
        query = scoped(Execution, scope).where(Execution.admitted_at_ms >= since_ms)
        if deployment_id is not None:
            deployment_id = TypeAdapter(CanonicalUUID).validate_python(deployment_id, strict=True)
            revisions = select(ExecutionDeploymentRevision.id).where(
                ExecutionDeploymentRevision.workspace_id == scope.workspace_id,
                ExecutionDeploymentRevision.deployment_id == deployment_id,
            )
            query = query.where(Execution.deployment_revision_id.in_(revisions))
        rows = list(
            db.execute(
                query.with_only_columns(Execution.id, Execution.state)
                .order_by(Execution.admitted_at_ms.desc(), Execution.id.desc())
                .limit(SUMMARY_LIMIT + 1)
            ).all()
        )
        truncated = len(rows) > SUMMARY_LIMIT
        rows = rows[:SUMMARY_LIMIT]
        effects: dict[str, list] = {row.id: [] for row in rows}
        if effects:
            for execution_id, effect, outcome in db.execute(
                scoped(ExecutionActionIntent, scope)
                .where(ExecutionActionIntent.execution_id.in_(list(effects)))
                .with_only_columns(
                    ExecutionActionIntent.execution_id,
                    ExecutionActionIntent.effect_state,
                    ExecutionActionIntent.outcome_state,
                )
            ).all():
                effects[execution_id].append((effect, outcome))
        counts = dict.fromkeys(OUTCOMES, 0)
        exceptions = 0
        for row in rows:
            outcome, exception = business_outcome(row.state, effects[row.id])
            counts[outcome] += 1
            exceptions += exception
        return {
            "since_ms": since_ms,
            "deployment_id": deployment_id,
            "total": len(rows),
            "truncated": truncated,
            "outcomes": counts,
            "exceptions": exceptions,
        }


def parse_since(value) -> int:
    if value is None or not value.isdigit() or len(value) > 16:
        raise ExecutionError("invalid_request")
    return int(value)
