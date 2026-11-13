"""Verify action outcomes and gate execution success on them (E05 Task 9).

An `outcome_verification` step reads the target back through its declared read
operation, as an E04 connector-read attempt on the step's own occurrence record
(`VERIFICATION_READ`: the SDK step declares no retry policy). Progression applies
the step's rules to the typed result (`outcome_state`): the rules' expected values
read the package's input and earlier steps, so they are evaluated in the pure
region, never here. `VerificationService.record` binds and persists the result: one
SDK `OutcomeRecord` per intent naming the exact intent digest, the receipt (or the
effect-present observation acting as receipt evidence when the write settled by
read-back without a receipt row), the rule ID and version, and the read's evidence.

Nothing infers an outcome from a receipt, a model or a projection:

- the rules hold over a read result: `verified` (with that result as evidence);
- they do not hold: `not_verified`, which fails the step `outcome_not_verified`;
- a rule error, or a read failing after its retries: `unknown`, which fails the
  step `outcome_unknown`.

`completion_allowed` is the success gate: an execution that dispatched an action
succeeds only when every dispatched action's outcome is `verified`.
"""

from __future__ import annotations

import json
from hashlib import sha256

from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.package import RetryPolicy
from lotuspond_sdk.executable.wire import OutcomeRecordedPayload, validate_outcome_record
from sqlalchemy import select

from ..fleet.authority import database_now_ms
from ..models import (
    ExecutionActionIntent,
    ExecutionActionObservation,
    ExecutionActionOutcome,
    ExecutionActionReceipt,
    ExecutionAttempt,
    ExecutionStepInstance,
    new_id,
)
from .action_reconciliation import _evidence_ref, _scope, register_evidence
from .action_rules import evaluate_rules
from .artifacts import ArtifactPolicy
from .authorization import record, scoped
from .contracts import canonical_value
from .expressions import ExpressionError
from .program import ProgramError, load_program, resolve_occurrence

OUTCOME_SUFFIX = "#outcome"
VERIFIED, NOT_VERIFIED, UNKNOWN = "verified", "not_verified", "unknown"
FAILURES = {NOT_VERIFIED: "outcome_not_verified", UNKNOWN: "outcome_unknown"}
# Every verification read: bounded, retried only for the transient codes.
VERIFICATION_READ = RetryPolicy(
    max_attempts=3,
    timeout_seconds=30,
    backoff_seconds=1,
    retry_codes=["unavailable", "rate_limited", "timeout_before_dispatch"],
)
# Effects from which an outcome may be verified: the write closed with an effect.
_SETTLED = frozenset({"accepted", "observed"})
# An intent whose effect left `not_dispatched` was dispatched.
_UNDISPATCHED = "not_dispatched"


def outcome_state(rules, readback, expected, definitions=()) -> str:
    """The rules over a typed read result: `verified`, `not_verified` or, for any
    evaluation error (a missing value, mismatched kinds), `unknown`. Never a guess."""
    try:
        return VERIFIED if evaluate_rules(rules, readback, expected, definitions) else NOT_VERIFIED
    except ExpressionError:
        return UNKNOWN


def completion_allowed(intents) -> str | None:
    """None when every dispatched action's outcome is verified; else the failure.

    `intents` are `(effect_state, outcome_state)` pairs of one execution's intents.
    An intent never dispatched constrains nothing (E04's computational meaning).
    """
    refused = None
    for effect, outcome in intents:
        if effect == _UNDISPATCHED or outcome == VERIFIED:
            continue
        if outcome == NOT_VERIFIED:
            return FAILURES[NOT_VERIFIED]
        refused = FAILURES[UNKNOWN]
    return refused


class VerificationService:
    """Binds and records one outcome per intent (pure of providers)."""

    def __init__(self, *, artifacts, artifact_policy, installation_id):
        if artifacts is None:
            raise ValueError("Artifacts are required")
        self.artifacts = artifacts
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        self.installation_id = installation_id

    def _receipt(self, db, scope, execution, receipt_id):
        """The receipt row, or effect-present observation, a dispatch value names."""
        receipt = db.scalar(
            scoped(ExecutionActionReceipt, scope).where(
                ExecutionActionReceipt.execution_id == execution.id,
                ExecutionActionReceipt.id == receipt_id,
            )
        )
        if receipt is not None:
            return receipt, None
        observation = db.scalar(
            scoped(ExecutionActionObservation, scope).where(
                ExecutionActionObservation.execution_id == execution.id,
                ExecutionActionObservation.id == receipt_id,
                ExecutionActionObservation.result == "effect_present",
                # Only what settles a write (Task 8): a read-back or an owner's
                # resolution citing recorded evidence.
                ExecutionActionObservation.source.in_(
                    ("readback", "restore_readback", "resolution")
                ),
            )
        )
        return None, observation

    def record(
        self, db, execution, epoch, *, path, node_id, attempt_id, receipt_id, state, keys=None
    ):
        """Record the outcome verification read `attempt_id` settled to `state`.

        Returns `(outcome row, failure code or None)`; the row is None when the
        binding is refused (`binding_refused`) or the intent already has an outcome
        (`invalid_result`). The read must be this step occurrence's, on the same
        target as the intent; the receipt must be that intent's, from a write that
        closed with an effect, and the read must be the write's own reconciliation
        read on the same connector pin (the compiler's `verification_binding`).

        The caller holds the execution row lock (E04's order: workspace, capacity,
        execution); this locks the read attempt, then the intent, and no cursor.
        """
        from .actions import load_intent, record_sdk_event

        if state not in (VERIFIED, NOT_VERIFIED, UNKNOWN) or not path.endswith(OUTCOME_SUFFIX):
            raise ProgramError("integrity")
        scope = _scope(execution)
        program = load_program(db, scope, execution)
        step_ref = resolve_occurrence(program, path)
        verification = step_ref.step
        if step_ref.selector != OUTCOME_SUFFIX or verification.id != node_id:
            raise ProgramError("integrity")
        attempt = record(db, ExecutionAttempt, scope, attempt_id, locked=True)
        instance = record(db, ExecutionStepInstance, scope, attempt.step_instance_id)
        if (
            attempt.execution_id != execution.id
            or instance.occurrence_path != path[: -len(OUTCOME_SUFFIX)]
            or instance.node_id != node_id
            or attempt.parent_attempt_id is not None
            or attempt.state not in ("succeeded", "failed", "cancelled")
            or (state != UNKNOWN and attempt.state != "succeeded")
        ):
            raise ProgramError("integrity")
        receipt, observation = self._receipt(db, scope, execution, receipt_id)
        if receipt is None and observation is None:
            return None, "binding_refused"
        intent = record(
            db,
            ExecutionActionIntent,
            scope,
            (receipt or observation).intent_id,
            locked=True,
        )
        verified = load_intent(
            db,
            scope,
            intent.id,
            artifacts=self.artifacts,
            keys=keys,
            program=program,
            installation_id=self.installation_id,
        )
        read_input = json.loads(
            self.artifacts._get_internal(
                db,
                scope,
                ArtifactHandle(
                    id=attempt.input_artifact_id,
                    version=attempt.input_artifact_version,
                    sha256=attempt.input_sha256,
                ),
                keys=keys,
            )
        )
        intent_step = resolve_occurrence(
            program,
            record(db, ExecutionStepInstance, scope, intent.step_instance_id).occurrence_path,
        )
        # The exact intent: the read targets what the intent bound, through the
        # write's own reconciliation read on the same connector pin, nothing else.
        if (
            not _same_read(program, intent_step, step_ref)
            or intent.execution_id != execution.id
            or type(read_input) is not dict
            or canonical_value(read_input.get("target")) != canonical_value(verified.target)
            or intent.effect_state not in _SETTLED
        ):
            return None, "binding_refused"
        existing = db.scalar(
            scoped(ExecutionActionOutcome, scope).where(
                ExecutionActionOutcome.intent_id == intent.id
            )
        )
        if existing is not None:
            return None, "invalid_result"  # one outcome per intent; never overwritten
        evidence, handle = [], None
        if attempt.state == "succeeded" and attempt.result_artifact_id is not None:
            handle = ArtifactHandle(
                id=attempt.result_artifact_id,
                version=attempt.result_artifact_version,
                sha256=attempt.result_sha256,
            )
            evidence = [_evidence_ref(handle, "target_readback")]
        if state == VERIFIED and not evidence:
            raise ProgramError("integrity")  # verified always cites its read
        outcome_id, observed_at_ms = new_id(), database_now_ms(db)
        outcome = validate_outcome_record(
            {
                "outcome_id": outcome_id,
                "action_id": intent.action_id,
                "intent_sha256": intent.intent_sha256,
                "receipt_id": (
                    receipt.receipt_id if receipt is not None else observation.observation_id
                ),
                "state": state,
                "rule_id": verification.rule_id,
                "rule_version": verification.version,
                "evidence_refs": evidence,
                "observed_at_ms": observed_at_ms,
            }
        )
        value = outcome.model_dump(mode="json")
        content = canonical_value(value)
        row = ExecutionActionOutcome(
            **scope.model_dump(),
            id=outcome_id,
            execution_id=execution.id,
            intent_id=intent.id,
            receipt_row_id=receipt.id if receipt is not None else None,
            observation_row_id=observation.id if observation is not None else None,
            outcome_bytes=content,
            outcome_sha256=sha256(content).hexdigest(),
            state=state,
            rule_id=verification.rule_id,
            rule_version=verification.version,
            observed_at_ms=observed_at_ms,
        )
        db.add(row)
        intent.outcome_state = state
        if state == VERIFIED:
            intent.effect_state = "observed"  # read-back evidence of the effect
        db.flush()
        if handle is not None:
            register_evidence(db, scope, execution.id, intent.id, handle)
        record_sdk_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "outcome_recorded",
            OutcomeRecordedPayload(outcome=outcome).model_dump(mode="json"),
            attempt_id=attempt.id,
            keys=keys,
        )
        return row, FAILURES.get(state)


def _pin(ref, connector_id):
    return next(d.ref for d in ref.package.connectors if d.id == connector_id)


def _same_read(program, intent_ref, verification_ref):
    """The verification reads the intent's connector pin through the write
    operation's declared read-back read (the compiler's `verification_binding`)."""
    intent, verification = intent_ref.step, verification_ref.step
    if intent_ref.kind != "action_intent":
        return False
    pin = _pin(intent_ref, intent.connector_ref)
    if pin != _pin(verification_ref, verification.connector_ref):
        return False
    operations = program.catalog[pin.sha256].definition.operations
    return any(
        op.id == intent.operation
        and op.reconciliation.mode == "read_back"
        and op.reconciliation.read_operation == verification.read_operation
        for op in operations
    )


def dispatched_outcomes(db, execution):
    """`(effect, outcome)` of every intent of this execution, for `completion_allowed`."""
    return list(
        db.execute(
            select(ExecutionActionIntent.effect_state, ExecutionActionIntent.outcome_state)
            .where(
                ExecutionActionIntent.workspace_id == execution.workspace_id,
                ExecutionActionIntent.project_id == execution.project_id,
                ExecutionActionIntent.environment_id == execution.environment_id,
                ExecutionActionIntent.execution_id == execution.id,
            )
            .order_by(ExecutionActionIntent.id)
        )
    )
