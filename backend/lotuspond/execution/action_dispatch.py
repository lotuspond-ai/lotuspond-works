"""E05 dispatch: fresh authority, signed E03 commands, action leases and receipts (Task 6).

An approved intent's command attempt is reserved by E04's scheduler like any other
attempt. At runtime poll time the API (never an orchestrator) derives the SDK
`RuntimeCommand`, signs it with the constructor-provided operator command key and
admits it through E03's `CommandService` in the same transaction. E03 calls this
module's `ExecutionPinnedRecordSource` and `ExecutionDispatchAuthority`, which
recheck every current authority under locks: the intent and decision bytes, the
initiator's `execute`, the deployment revision, entitlement, the requested state,
the recovery epoch, expiry and the reviewer's current role. Approval alone is never
enough. Production wiring stays deny-by-default (E03's deny hooks) until a
deployment installs these with a real command key.

Lock order (plan, "Service and lock contracts"): the pool's fair gate and the
initiator's; E03 fleet rows (identity, then the connection, its version, secret
references and the command's admission, in E03's order); then the execution, its
step, attempt, reservation, intent and action command. The one exception is the
sweeper's hooks, which take the admission after the execution, SKIP LOCKED (see
`_swept`). No path here takes the installation cursor.
"""

from __future__ import annotations

import json
import logging
from hashlib import sha256

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    CommandDelivery,
    PinnedCommandRecords,
    ResolvedSecretBinding,
)
from lotuspond_sdk.activity import ArtifactHandle, UsageObservation
from lotuspond_sdk.executable.actions import (
    check_decision,
    decision_digest,
    validate_human_decision,
)
from lotuspond_sdk.executable.signing import command_signature_envelope_json
from lotuspond_sdk.executable.wire import (
    canonical_wire_json,
    validate_action_receipt,
    wire_digest,
)
from sqlalchemy import select

from .. import authority_gate
from ..fleet.authentication import RuntimePrincipal
from ..fleet.authority import database_now_ms
from ..fleet.management import record as fleet_record
from ..fleet.management import stored_config
from ..models import (
    Connection,
    ConnectionVersion,
    ConnectionVersionSecret,
    Execution,
    ExecutionActionCommand,
    ExecutionActionDecision,
    ExecutionActionIntent,
    ExecutionActionReceipt,
    ExecutionAttempt,
    ExecutionDeploymentRevision,
    ExecutionHumanTask,
    ExecutionReconciliationObservation,
    ExecutionRequest,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    Membership,
    RuntimeCommandAdmission,
    SecretReference,
    new_id,
)
from .action_contracts import (
    ACTION_LEASE_MS,
    ACTION_RENEW_MS,
    ActionLease,
    ActionReport,
    ActionStop,
    Recipient,
    command_context,
    decision_context,
    derive_command,
)
from .actions import load_intent, record_sdk_event
from .activity_records import initiator, reserved_until
from .artifacts import ArtifactPolicy
from .authorization import record, scoped
from .contracts import canonical_value
from .deployments import load_deployment
from .errors import ExecutionError
from .human_tasks import eligible
from .orchestrator import TERMINAL, mark_runnable
from .program import ProgramError
from .progression import record_event

logger = logging.getLogger(__name__)
POLL_CANDIDATES = 10
DISPATCH_SUFFIX = "#dispatch"
VALUE_PREFIX = "action-value:"


def _scope(row):
    from lotuspond_runtime.protocol import Scope

    return Scope(
        workspace_id=row.workspace_id, project_id=row.project_id, environment_id=row.environment_id
    )


def _token(command_row_id, session_id) -> str:
    """Binds an action lease to its command and the claiming session; never a secret."""
    content = {"command": command_row_id, "session_id": session_id}
    return sha256(b"lotuspond.action-lease.v1\x00" + canonical_value(content)).hexdigest()


def _attempt_input(artifacts, db, attempt):
    content = artifacts._get_internal(
        db,
        _scope(attempt),
        ArtifactHandle(
            id=attempt.input_artifact_id,
            version=attempt.input_artifact_version,
            sha256=attempt.input_sha256,
        ),
    )
    value = json.loads(content)
    if type(value) is not dict or set(value) != {"intent_id", "decision_id"}:
        raise ExecutionError("conflict")
    return value


# ---------------------------------------------------------------- E03 hooks


# Execution refusals that mean the command's authority is gone, not unreachable.
REVOKED = frozenset({"forbidden", "deployment_unavailable", "budget_exceeded", "invalid_request"})


# Set on a transaction that only reports a claimed write's receipt or stop (Task 12).
REPORTING = "lotuspond.action.reporting"


class ExecutionPinnedRecordSource:
    """E03's `PinnedRecordSource`: a command's records, rebuilt under current authority."""

    def __init__(self, *, budgets, artifacts, recovery, installation_id):
        if any(x is None for x in (budgets, artifacts, recovery)):
            raise ValueError("Budgets, artifacts and recovery are required")
        self.budgets, self.artifacts, self.recovery = budgets, artifacts, recovery
        self.installation_id = installation_id

    def load(self, db, command) -> PinnedCommandRecords:
        try:
            return self._load(db, command)
        except FleetError:
            raise
        except ExecutionError as error:
            # E03 wraps anything else as `authority_unavailable`: keep the refusal's code.
            # A revocation or refusal stays one; a lookup that may recover stays transient.
            if error.code in ("conflict", "not_found"):
                raise FleetError(error.code) from None
            if error.code in REVOKED:
                raise FleetError("forbidden") from None
            raise FleetError("authority_unavailable") from None
        except Exception:
            raise FleetError("authority_unavailable") from None

    def _load(self, db, command):
        scope = _scope(command)
        intent_row = db.scalar(
            scoped(ExecutionActionIntent, scope).where(
                ExecutionActionIntent.action_id == command.action_id,
                ExecutionActionIntent.execution_id == command.execution_id,
            )
        )
        if intent_row is None or intent_row.intent_sha256 != command.intent_sha256:
            raise FleetError("command_rejected")
        execution = record(db, Execution, scope, intent_row.execution_id)
        verified = load_intent(
            db,
            scope,
            intent_row.id,
            artifacts=self.artifacts,
            installation_id=self.installation_id,
        )
        now = database_now_ms(db)
        # The initiator's gate must already be held (taken before any row lock by the
        # action service): one gated late would invert the lock order, so refuse.
        if not authority_gate.held(db, authority_gate.USER, execution.initiating_user_id):
            raise FleetError("conflict")
        # A claimed write finishes and records its status under a pause or a
        # cancellation (decision 12); nothing new is admitted or claimed then.
        claimed = db.scalar(
            scoped(ExecutionActionCommand, scope).where(
                ExecutionActionCommand.command_id == command.command_id,
                ExecutionActionCommand.status.in_(("claimed", "stopped", "uncertain")),
            )
        )
        # Task 12 (REVA-5): a report of a write already claimed records what happened,
        # whatever the initiator's, deployment's and entitlement's authority since. Its
        # integrity, epoch, recipient, expiry and connection still decide: E03 refuses
        # any report of a command past its expiry, which an intent's expiry bounds, and
        # the write then lapses to a read-back. Records, claims and renewals keep every
        # check.
        reporting = claimed is not None and db.info.get(REPORTING) is True
        finishing = claimed is not None and (execution.requested_state != "running" or reporting)
        if not reporting:
            # The initiator's current `execute` and a deployment revision that may run.
            actor = initiator(db, execution)
            load_deployment(
                db, actor, execution.deployment_revision_id, dependencies=self.budgets.deployments
            )
            revision = record(
                db, ExecutionDeploymentRevision, scope, execution.deployment_revision_id
            )
            grant, tenant, recovery = self.budgets._grants(db, scope)
            self.budgets._final(
                db, actor, revision, grant, tenant, recovery, None if finishing else execution
            )
        else:
            recovery = self.budgets._grants(db, scope)[2]
        if (
            (execution.requested_state != "running" and not finishing)
            # A pause observes `paused` at once; its claimed writes still finish.
            or execution.state not in (("running", "paused") if finishing else ("running",))
            or recovery.epoch != intent_row.recovery_epoch
            or recovery.epoch != command.recovery_epoch
            or execution.recovery_epoch != recovery.epoch
            or intent_row.expires_at_ms <= now
        ):
            raise FleetError("forbidden")
        decision = None
        if intent_row.approval_mode == "human":
            row = db.scalar(
                scoped(ExecutionActionDecision, scope).where(
                    ExecutionActionDecision.intent_id == intent_row.id,
                    ExecutionActionDecision.decision_sha256 == command.decision_sha256,
                )
            )
            if row is None or row.decision != "allow" or row.expires_at_ms <= now:
                raise FleetError("forbidden")
            decision = validate_human_decision(json.loads(row.decision_bytes))
            if decision_digest(decision) != row.decision_sha256:
                raise FleetError("command_rejected")
            # Decision 7: a reviewer who lost their eligible role revokes the approval.
            task = record(db, ExecutionHumanTask, scope, row.task_id)
            member = db.scalar(
                select(Membership).where(
                    Membership.workspace_id == scope.workspace_id,
                    Membership.user_id == row.actor_user_id,
                )
            )
            if not reporting and (member is None or not eligible(task, member.role)):
                raise FleetError("forbidden")
            context = decision_context(verified.intent, now).model_copy(
                update={"recovery_epoch": recovery.epoch}
            )
            if check_decision(verified.intent, decision, context).code != "binding_valid":
                raise FleetError("forbidden")
        elif command.decision_sha256 is not None:
            raise FleetError("command_rejected")
        version = db.scalar(
            scoped(ConnectionVersion, scope).where(
                ConnectionVersion.connection_id == intent_row.connection_id,
                ConnectionVersion.version == intent_row.connection_version,
            )
        )
        if version is None:
            raise FleetError("forbidden")
        config = stored_config(db, scope, version)
        links = list(
            db.scalars(
                scoped(ConnectionVersionSecret, scope)
                .where(
                    ConnectionVersionSecret.connection_id == intent_row.connection_id,
                    ConnectionVersionSecret.connection_version == intent_row.connection_version,
                )
                .order_by(ConnectionVersionSecret.secret_reference_id)
            )
        )
        bindings = []
        for link in links:
            reference = record(db, SecretReference, scope, link.secret_reference_id)
            bindings.append(
                ResolvedSecretBinding(
                    reference_id=reference.id,
                    version=link.secret_version,
                    binding_id=reference.binding_id,
                )
            )
        return PinnedCommandRecords(
            intent=verified.intent,
            decision=decision,
            context=command_context(
                verified.intent, Recipient(command.worker_id, command.fencing_generation), now
            ),
            connection_id=intent_row.connection_id,
            connection_version=intent_row.connection_version,
            configuration=config,
            secret_bindings=tuple(bindings),
            actual_input=canonical_value(verified.input),
            actual_target=canonical_value(verified.target),
        )


class ExecutionDispatchAuthority:
    """E03's `BusinessDispatchAuthority`: the SDK binding and a bounded price lookup.

    `validate_delivery` already ran the SDK's `check_command_binding` on the exact
    command; this rechecks the decision under the records' context and that the
    charge provider bounds the operation, never a cached answer.
    """

    def __init__(self, *, charges):
        if charges is None:
            raise ValueError("A charge provider is required")
        self.charges = charges

    def authorize(self, current_identity, command_digest, pins) -> None:
        intent, decision = pins.intent, pins.decision
        if intent.binding.approval_mode == "human":
            report = check_decision(intent, decision, pins.context)
            if report.code != "binding_valid":
                raise FleetError("forbidden")
        elif decision is not None:
            raise FleetError("forbidden")
        bound = getattr(self.charges, "bound_action", None)
        if bound is not None and bound(intent.binding) is not True:
            raise FleetError("forbidden")
        return None


# ---------------------------------------------------------------- dispatch


class ActionDispatchService:
    """Runtime-facing action polls, records, leases, receipts and stops."""

    def __init__(
        self,
        *,
        commands,
        fleet,
        budgets,
        artifacts,
        artifact_policy,
        signer,
        trust,
        installation_id,
    ):
        if any(x is None for x in (commands, fleet, budgets, artifacts, signer, trust)):
            raise ValueError("Commands, fleet, budgets, artifacts, signer and trust are required")
        self.commands, self.fleet, self.budgets, self.artifacts = (
            commands,
            fleet,
            budgets,
            artifacts,
        )
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        self.signer, self.trust = signer, trust
        self.installation_id = installation_id

    # ------------------------------------------------------------ locking

    def _gates(self, db, principal, attempt_ids=()):
        """The fair gates first, before any row lock (`authority_gate`)."""
        identity = principal.identity
        users = []
        if attempt_ids:
            users = db.scalars(
                select(Execution.initiating_user_id)
                .join(ExecutionAttempt, ExecutionAttempt.execution_id == Execution.id)
                .where(
                    *(getattr(Execution, k) == v for k, v in identity.scope.model_dump().items()),
                    ExecutionAttempt.id.in_(list(attempt_ids)),
                )
            ).all()
        authority_gate.pool(db, identity.scope.workspace_id, identity.pool_id, users_=users)

    def _fleet_rows(self, db, principal, intent):
        """E03 fleet rows in E03's order, before any execution row."""
        locked = self.commands._current(db, principal)
        scope = _scope(intent)
        fleet_record(db, Connection, scope, intent.connection_id, lock=True, share=True)
        db.scalar(
            scoped(ConnectionVersion, scope)
            .where(
                ConnectionVersion.connection_id == intent.connection_id,
                ConnectionVersion.version == intent.connection_version,
            )
            .with_for_update()
        )
        for link in db.scalars(
            scoped(ConnectionVersionSecret, scope)
            .where(
                ConnectionVersionSecret.connection_id == intent.connection_id,
                ConnectionVersionSecret.connection_version == intent.connection_version,
            )
            .order_by(ConnectionVersionSecret.secret_reference_id)
        ):
            fleet_record(db, SecretReference, scope, link.secret_reference_id, lock=True)
        return locked

    def _rows(self, db, principal, attempt_id):
        identity = principal.identity
        scope = identity.scope
        hint = record(db, ExecutionAttempt, scope, attempt_id)
        execution_hint = record(db, Execution, scope, hint.execution_id)
        revision = record(
            db, ExecutionDeploymentRevision, scope, execution_hint.deployment_revision_id
        )
        if revision.pool_id != identity.pool_id:
            raise FleetError("unauthenticated")
        value = _attempt_input(self.artifacts, db, hint)
        intent_hint = record(db, ExecutionActionIntent, scope, value["intent_id"])
        locked = self._fleet_rows(db, principal, intent_hint)
        # The E03 admission is a fleet row too: locked with them, before any execution row.
        command_hint = db.scalar(
            scoped(ExecutionActionCommand, scope).where(
                ExecutionActionCommand.attempt_id == hint.id
            )
        )
        if command_hint is not None:
            db.scalar(
                select(RuntimeCommandAdmission)
                .where(RuntimeCommandAdmission.id == command_hint.admission_id)
                .with_for_update()
            )
        execution = record(db, Execution, scope, hint.execution_id, locked=True)
        step = record(db, ExecutionStepInstance, scope, hint.step_instance_id, locked=True)
        attempt = record(db, ExecutionAttempt, scope, attempt_id, locked=True)
        reservation = db.scalar(
            scoped(ExecutionUsageReservation, scope)
            .where(ExecutionUsageReservation.attempt_id == attempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        intent = record(db, ExecutionActionIntent, scope, intent_hint.id, locked=True)
        command = db.scalar(
            scoped(ExecutionActionCommand, scope)
            .where(ExecutionActionCommand.attempt_id == attempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            not step.occurrence_path.endswith(DISPATCH_SUFFIX)
            or intent.execution_id != execution.id
            or attempt.execution_id != execution.id
        ):
            raise ExecutionError("conflict")
        return dict(
            locked=locked,
            execution=execution,
            step=step,
            attempt=attempt,
            reservation=reservation,
            intent=intent,
            command=command,
            decision_id=value["decision_id"],
        )

    def _finish(self, db, locked):
        return self.commands._finish(db, locked)

    def _event(self, db, execution, epoch, code, attempt_id=None, detail=None):
        record_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            code,
            attempt_id=attempt_id,
            detail=detail,
            origin="activity",
        )

    @staticmethod
    def deadline(execution, reservation) -> int:
        """An action attempt ends by its reserved active time or the execution deadline."""
        return min(execution.deadline_ms, reserved_until(reservation))

    # ------------------------------------------------------------ poll

    def _candidates(self, db, principal, now):
        identity = principal.identity
        return list(
            db.scalars(
                select(ExecutionAttempt.id)
                .join(Execution, Execution.id == ExecutionAttempt.execution_id)
                .join(
                    ExecutionDeploymentRevision,
                    ExecutionDeploymentRevision.id == Execution.deployment_revision_id,
                )
                .join(
                    ExecutionStepInstance,
                    ExecutionStepInstance.id == ExecutionAttempt.step_instance_id,
                )
                .join(
                    ExecutionUsageReservation,
                    ExecutionUsageReservation.attempt_id == ExecutionAttempt.id,
                )
                .where(
                    *(
                        getattr(ExecutionAttempt, k) == v
                        for k, v in identity.scope.model_dump().items()
                    ),
                    ExecutionDeploymentRevision.pool_id == identity.pool_id,
                    ExecutionStepInstance.occurrence_path.like("%" + DISPATCH_SUFFIX),
                    Execution.requested_state == "running",
                    Execution.state == "running",
                    Execution.recovery_epoch == identity.recovery_epoch,
                    ExecutionAttempt.state == "reserved",
                    ExecutionUsageReservation.state == "reserved",
                    ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
                    ~select(ExecutionActionCommand.id)
                    .where(ExecutionActionCommand.attempt_id == ExecutionAttempt.id)
                    .exists(),
                )
                .order_by(Execution.admission_sequence, ExecutionAttempt.id)
                .limit(POLL_CANDIDATES)
            )
        )

    def poll(self, db, principal: RuntimePrincipal) -> CommandDelivery | None:
        """A pending action command for this session, else the next reserved action
        attempt of this pool dispatched. At most ten candidates, each in its own
        savepoint, so one refused head never blocks the pool."""
        if type(principal) is not RuntimePrincipal or principal.session_id is None:
            raise FleetError("unauthenticated")
        self.fleet.limit("action-poll:" + principal.identity.instance_id, 60)
        candidates = self._candidates(db, principal, database_now_ms(db))
        self._gates(db, principal, candidates)
        pending = self._pending(db, principal)
        if pending is not None:
            # The session before the hand-out: a runtime whose session ended
            # (disconnected or revoked) is refused, its pending command included. Read
            # without row locks, `_pending` keeps E03's identity-first order. With
            # nothing pending each candidate is authenticated under its own savepoint.
            self._finish(db, self.commands._current(db, principal))
            return pending
        # Only the candidates whose gates were taken: one reserved since then waits
        # for the next poll.
        for attempt_id in candidates:
            try:
                with db.begin_nested():
                    return self._dispatch(db, principal, attempt_id)
            except (ExecutionError, FleetError, ProgramError) as error:
                # An unverifiable intent is fenced by the orchestrator on its next load.
                logger.info("Action candidate skipped: %s", error.code)
        return None

    def _pending(self, db, principal):
        identity = principal.identity
        row = db.scalar(
            select(RuntimeCommandAdmission)
            .join(
                ExecutionActionCommand,
                ExecutionActionCommand.admission_id == RuntimeCommandAdmission.id,
            )
            .join(ExecutionAttempt, ExecutionAttempt.id == ExecutionActionCommand.attempt_id)
            .join(Execution, Execution.id == ExecutionAttempt.execution_id)
            .where(
                RuntimeCommandAdmission.instance_id == identity.instance_id,
                RuntimeCommandAdmission.session_id == principal.session_id,
                RuntimeCommandAdmission.generation == identity.generation,
                RuntimeCommandAdmission.status == "admitted",
                RuntimeCommandAdmission.command_expires_at_ms > database_now_ms(db),
                # Only a command its execution can still claim (Task 10): one a pause
                # released, a cancellation or a restore left behind is never offered.
                ExecutionActionCommand.status == "admitted",
                ExecutionAttempt.state == "dispatched",
                Execution.requested_state == "running",
                Execution.state == "running",
                Execution.recovery_epoch == identity.recovery_epoch,
            )
            .order_by(RuntimeCommandAdmission.admitted_at_ms, RuntimeCommandAdmission.id)
            .limit(1)
        )
        if row is None:
            return None
        return CommandDelivery(
            bytes(row.command_bytes), bytes(row.signature_bytes), row.trust_revision
        )

    def _dispatch(self, db, principal, attempt_id):
        rows = self._rows(db, principal, attempt_id)
        execution, attempt, reservation = rows["execution"], rows["attempt"], rows["reservation"]
        intent, identity = rows["intent"], principal.identity
        if rows["command"] is not None:
            raise ExecutionError("conflict")
        if execution.requested_state != "running" or execution.state != "running":
            raise ExecutionError("forbidden")
        if (
            attempt.state != "reserved"
            or reservation is None
            or reservation.state != "reserved"
            or reservation.compute_stopped_at_ms is not None
        ):
            raise ExecutionError("conflict")
        verified = load_intent(
            db,
            _scope(execution),
            intent.id,
            artifacts=self.artifacts,
            installation_id=self.installation_id,
        )
        decision_sha256 = None
        if intent.approval_mode == "human":
            decision = record(db, ExecutionActionDecision, _scope(execution), rows["decision_id"])
            if decision.intent_id != intent.id or decision.decision != "allow":
                raise ExecutionError("forbidden")
            decision_sha256 = decision.decision_sha256
        now = self._finish(db, rows["locked"])
        deadline = self.deadline(execution, reservation)
        command = derive_command(
            verified.intent,
            decision_sha256,
            Recipient(identity.instance_id, identity.generation),
            new_id(),
            new_id(),
            now,
            deadline,
        )
        snapshot = self.trust.current(identity.installation_id)
        envelope = self.signer.sign(command, snapshot)
        command_bytes = canonical_wire_json(command)
        signature_bytes = command_signature_envelope_json(envelope)
        # E03 rebuilds the records through `ExecutionPinnedRecordSource` and rechecks
        # trust and authority before the admission exists.
        self.commands.admit(db, identity, command_bytes, signature_bytes)
        admission = db.scalar(
            select(RuntimeCommandAdmission).where(
                RuntimeCommandAdmission.installation_id == identity.installation_id,
                RuntimeCommandAdmission.recovery_epoch == identity.recovery_epoch,
                RuntimeCommandAdmission.workspace_id == identity.scope.workspace_id,
                RuntimeCommandAdmission.command_id == command.command_id,
            )
        )
        if admission is None:
            raise ExecutionError("conflict")
        db.add(
            ExecutionActionCommand(
                **_scope(execution).model_dump(),
                execution_id=execution.id,
                attempt_id=attempt.id,
                intent_id=intent.id,
                decision_row_id=rows["decision_id"],
                installation_id=admission.installation_id,
                recovery_epoch=admission.recovery_epoch,
                admission_id=admission.id,
                command_id=admission.command_id,
                command_digest=admission.command_digest,
                status="admitted",
            )
        )
        attempt.state = "dispatched"
        intent.effect_state = "dispatching"
        db.flush()
        epoch = identity.recovery_epoch
        self._event(db, execution, epoch, "attempt_dispatched", attempt.id)
        record_sdk_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "action_dispatched",
            {"command": json.loads(command.model_dump_json())},
            attempt_id=attempt.id,
        )
        return CommandDelivery(command_bytes, signature_bytes, admission.trust_revision)

    def records(self, db, principal, command_id, command_sha256) -> PinnedCommandRecords:
        """The command's records, only to the session holding its live lease."""
        row, hint = self._command(db, principal, command_id, command_sha256)
        self._gates(db, principal, [hint.attempt_id])
        rows = self._rows(db, principal, hint.attempt_id)
        self._held(rows, principal, self._finish(db, rows["locked"]))
        if rows["execution"].requested_state != "running":
            raise FleetError("forbidden")  # a write not yet started never starts now
        command = json.loads(bytes(row.command_bytes))
        from lotuspond_sdk.executable.wire import validate_runtime_command

        return self.commands.records.load(db, validate_runtime_command(command))

    def _command(self, db, principal, command_id, command_sha256):
        identity = principal.identity
        row = db.scalar(
            select(RuntimeCommandAdmission).where(
                RuntimeCommandAdmission.instance_id == identity.instance_id,
                RuntimeCommandAdmission.command_id == command_id,
            )
        )
        if (
            row is None
            or row.session_id != principal.session_id
            or row.generation != identity.generation
            or row.command_digest != command_sha256
        ):
            raise FleetError("not_found")
        action = db.scalar(
            scoped(ExecutionActionCommand, identity.scope).where(
                ExecutionActionCommand.admission_id == row.id
            )
        )
        if action is None:
            raise FleetError("not_found")
        return row, action

    # ------------------------------------------------------------ leases

    @staticmethod
    def _lease(command, attempt, deadline):
        return ActionLease(
            attempt_id=attempt.id,
            command_id=command.command_id,
            ordinal=attempt.ordinal,
            lease_revision=command.lease_revision,
            expires_at_ms=command.lease_expires_at_ms,
            deadline_ms=deadline,
        )

    def claim(self, db, principal, command_id, command_sha256) -> ActionLease:
        """Claim once; an equal claim by the same session returns the same live lease."""
        _, hint = self._command(db, principal, command_id, command_sha256)
        self._gates(db, principal, [hint.attempt_id])
        rows = self._rows(db, principal, hint.attempt_id)
        command, attempt, execution = rows["command"], rows["attempt"], rows["execution"]
        deadline = self.deadline(execution, rows["reservation"])
        now = self._finish(db, rows["locked"])
        token = _token(command.id, principal.session_id)
        if command.status == "claimed":
            if command.lease_token_digest != token or now >= command.lease_expires_at_ms:
                raise ExecutionError("conflict")
            return self._lease(command, attempt, deadline)
        if command.status != "admitted" or attempt.state != "dispatched":
            raise ExecutionError("conflict")

        def hook(admission, at_ms):
            if admission.id != command.admission_id:
                raise FleetError("conflict")
            expires = min(at_ms + ACTION_LEASE_MS, deadline)
            if expires <= at_ms:
                raise FleetError("conflict")
            command.lease_token_digest = token
            command.lease_revision = 1
            command.claimed_at_ms = command.renewed_at_ms = at_ms
            command.lease_expires_at_ms = expires
            command.status = "claimed"
            attempt.state = "claimed"
            db.flush()

        self.commands.claim(db, principal, command_id, command_sha256, claim_hook=hook)
        self._event(db, execution, principal.identity.recovery_epoch, "attempt_claimed", attempt.id)
        return self._lease(command, attempt, deadline)

    def _held(self, rows, principal, now, lease_revision=None):
        command = rows["command"]
        if (
            command is None
            or command.status != "claimed"
            or command.lease_token_digest != _token(command.id, principal.session_id)
            or now >= command.lease_expires_at_ms
            or (lease_revision is not None and lease_revision > command.lease_revision)
        ):
            raise ExecutionError("conflict")
        return command

    def renew(self, db, principal, lease: ActionLease) -> ActionLease:
        lease = ActionLease.model_validate(lease)
        self._gates(db, principal, [lease.attempt_id])
        rows = self._rows(db, principal, lease.attempt_id)
        execution = rows["execution"]
        deadline = self.deadline(execution, rows["reservation"])
        # Current authority (identity, session, epoch, records) through E03, after locks.
        now = self._finish(db, rows["locked"])
        command = self._held(rows, principal, now, lease.lease_revision)
        if lease.command_id != command.command_id:
            raise ExecutionError("conflict")
        if execution.requested_state != "running":
            # A pause or cancellation is reported by refusing renewal: the runtime
            # finishes or stops the write and reports what it observed.
            raise ExecutionError("forbidden")
        self.commands._status_row(db, principal, command.command_id)
        if now - command.renewed_at_ms >= ACTION_RENEW_MS:
            expires = min(now + ACTION_LEASE_MS, deadline)
            if expires <= now:
                raise ExecutionError("conflict")
            command.renewed_at_ms, command.lease_expires_at_ms = now, expires
            command.lease_revision += 1
            db.flush()
        return self._lease(command, rows["attempt"], deadline)

    # ------------------------------------------------------------ receipts

    def store_value(self, db, principal, attempt_id, content) -> ArtifactHandle:
        self._gates(db, principal, [attempt_id])
        rows = self._rows(db, principal, attempt_id)
        now = self._finish(db, rows["locked"])
        self._held(rows, principal, now)
        scope = _scope(rows["execution"])
        handle = self.artifacts._put_internal(db, scope, content, self.policy)
        db.add(
            ExecutionRequest(
                **scope.model_dump(),
                execution_id=rows["execution"].id,
                request_key=VALUE_PREFIX + handle.id,
                digest=sha256(
                    canonical_value({"attempt": attempt_id, "id": handle.id})
                ).hexdigest(),
            )
        )
        db.flush()
        return handle

    def _stop_compute(self, db, rows, key, usage):
        scope = _scope(rows["execution"])
        attempt = rows["attempt"]
        self.budgets.settle_attempt(db, attempt.id, key, usage)
        observation = ExecutionReconciliationObservation(
            **scope.model_dump(),
            execution_id=rows["execution"].id,
            attempt_id=attempt.id,
            source_id="runtime",
            observation_id=key,
            digest=sha256(key.encode()).hexdigest(),
            code="compute_stopped",
            observed_at_ms=database_now_ms(db),
        )
        db.add(observation)
        db.flush()
        self.budgets.confirm_stopped(db, attempt.id, observation.id)

    def record_receipt(self, db, principal, report: ActionReport) -> None:
        report = ActionReport.model_validate(report)
        db.info[REPORTING] = True
        try:
            self._record_receipt(db, principal, report)
        finally:
            db.info.pop(REPORTING, None)

    def _record_receipt(self, db, principal, report) -> None:
        self._gates(db, principal, [report.attempt_id])
        rows = self._rows(db, principal, report.attempt_id)
        execution, attempt, intent = rows["execution"], rows["attempt"], rows["intent"]
        scope = _scope(execution)
        now = self._finish(db, rows["locked"])
        receipt = validate_action_receipt(report.receipt)
        existing = db.scalar(
            scoped(ExecutionActionReceipt, scope).where(
                ExecutionActionReceipt.intent_id == intent.id,
                ExecutionActionReceipt.receipt_id == receipt.receipt_id,
            )
        )
        if existing is not None:
            if existing.receipt_sha256 != wire_digest(receipt):
                raise ExecutionError("conflict")
            return  # an equal repeated receipt changes nothing
        live = self._live(rows, principal, now, report.lease_revision)
        command = rows["command"] if live else self._late(db, principal, rows, now, report)
        if (
            receipt.intent_sha256 != intent.intent_sha256
            or receipt.action_id != intent.action_id
            or receipt.attempt != attempt.ordinal
        ):
            raise ExecutionError("conflict")
        response = report.response
        if response is not None:
            registered = db.scalar(
                scoped(ExecutionRequest, scope).where(
                    ExecutionRequest.request_key == VALUE_PREFIX + response.id
                )
            )
            if registered is None:
                raise ExecutionError("forbidden")
        if not live:
            return self._historical(db, rows, receipt, report, now)
        row = ExecutionActionReceipt(
            **scope.model_dump(),
            id=new_id(),
            execution_id=execution.id,
            attempt_id=attempt.id,
            intent_id=intent.id,
            receipt_id=receipt.receipt_id,
            receipt_bytes=canonical_wire_json(receipt),
            receipt_sha256=wire_digest(receipt),
            status=receipt.status,
            response_artifact_id=None if response is None else response.id,
            response_artifact_version=None if response is None else response.version,
            response_sha256=None if response is None else response.sha256,
            source="live",
        )
        db.add(row)
        db.flush()
        self._stop_compute(db, rows, "receipt:" + row.id, report.usage)
        command.status = "stopped"
        epoch = principal.identity.recovery_epoch
        record_sdk_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            "action_receipt",
            {"receipt": json.loads(receipt.model_dump_json())},
            attempt_id=attempt.id,
        )
        if receipt.status == "unknown":
            self._uncertain(db, principal, rows, command)
        else:
            self.commands.record_status(db, principal, command.command_id, "terminal")
            intent.effect_state = "accepted" if receipt.status == "accepted" else "observed"
            handle = self.artifacts._put_internal(
                db,
                scope,
                canonical_value({"handle_kind": "action_receipt", "id": row.id}),
                self.policy,
            )
            attempt.result_artifact_id = handle.id
            attempt.result_artifact_version = handle.version
            attempt.result_sha256 = handle.sha256
            attempt.state = "succeeded"
            db.flush()
            self._event(db, execution, epoch, "attempt_succeeded", attempt.id)
        mark_runnable(db, execution, now)

    # ------------------------------------------------------------ late reports

    @staticmethod
    def _live(rows, principal, now, lease_revision) -> bool:
        """Whether this session holds the command's live lease at this revision."""
        command = rows["command"]
        return not (
            command is None
            or command.status != "claimed"
            or command.lease_token_digest != _token(command.id, principal.session_id)
            or now >= command.lease_expires_at_ms
            or lease_revision > command.lease_revision
        )

    def _late(self, db, principal, rows, now, report):
        """A report without a live lease: historical evidence, or refused (Task 8).

        Only the runtime instance and generation that claimed the command may send it
        (a restart's new session included), at a revision it was granted, and only
        once its lease is over. A lease past its expiry that the sweeper has not
        reached lapses here first, exactly as the sweeper would lapse it. It never
        extends a lease or issues work.
        """
        command = rows["command"]
        identity = principal.identity
        admission = (
            None if command is None else db.get(RuntimeCommandAdmission, command.admission_id)
        )
        if (
            command is None
            or admission is None
            or command.lease_revision is None
            or report.lease_revision > command.lease_revision
            or admission.instance_id != identity.instance_id
            or admission.generation != identity.generation
        ):
            raise ExecutionError("conflict")
        if command.status == "claimed":
            if now < command.lease_expires_at_ms:
                raise ExecutionError("conflict")  # another session's live lease
            _lapse(
                db,
                rows,
                admission,
                now,
                lambda execution, code, attempt_id: self._event(
                    db, execution, identity.recovery_epoch, code, attempt_id
                ),
            )
        elif command.status not in ("uncertain", "stopped"):
            raise ExecutionError("conflict")
        return command

    def _historical(self, db, rows, receipt, report, now):
        """Record a late receipt as evidence: it may settle known usage and confirm the
        write's compute stop, and supports a resolution; nothing else changes."""
        from .action_reconciliation import register_evidence

        execution, attempt, intent = rows["execution"], rows["attempt"], rows["intent"]
        scope = _scope(execution)
        response = report.response
        row = ExecutionActionReceipt(
            **scope.model_dump(),
            id=new_id(),
            execution_id=execution.id,
            attempt_id=attempt.id,
            intent_id=intent.id,
            receipt_id=receipt.receipt_id,
            receipt_bytes=canonical_wire_json(receipt),
            receipt_sha256=wire_digest(receipt),
            status=receipt.status,
            response_artifact_id=None if response is None else response.id,
            response_artifact_version=None if response is None else response.version,
            response_sha256=None if response is None else response.sha256,
            source="historical",
        )
        db.add(row)
        db.flush()
        handle = self.artifacts._put_internal(
            db,
            scope,
            canonical_value({"handle_kind": "action_receipt", "id": row.id}),
            self.policy,
        )
        register_evidence(db, scope, execution.id, intent.id, handle)
        if response is not None:
            register_evidence(db, scope, execution.id, intent.id, response)
        self._confirm_late(db, rows, "receipt:" + row.id, report.usage)
        mark_runnable(db, execution, now)

    def _confirm_late(self, db, rows, key, usage):
        reservation = rows["reservation"]
        if reservation is not None and reservation.compute_stopped_at_ms is None:
            self._stop_compute(db, rows, key, usage)

    def _uncertain(self, db, principal, rows, command):
        """The write may have happened: read back before anything else (Task 8)."""
        execution, attempt, intent = rows["execution"], rows["attempt"], rows["intent"]
        self.commands.record_status(db, principal, command.command_id, "uncertain")
        command.status = "uncertain"
        attempt.state = "uncertain"
        intent.effect_state = "unknown"
        execution.reason = "action_uncertain"
        db.flush()
        self._event(
            db, execution, principal.identity.recovery_epoch, "attempt_uncertain", attempt.id
        )

    def record_stop(self, db, principal, stop: ActionStop) -> None:
        stop = ActionStop.model_validate(stop)
        db.info[REPORTING] = True
        try:
            self._record_stop(db, principal, stop)
        finally:
            db.info.pop(REPORTING, None)

    def _record_stop(self, db, principal, stop) -> None:
        self._gates(db, principal, [stop.attempt_id])
        rows = self._rows(db, principal, stop.attempt_id)
        execution = rows["execution"]
        now = self._finish(db, rows["locked"])
        live = self._live(rows, principal, now, stop.lease_revision)
        command = rows["command"] if live else self._late(db, principal, rows, now, stop)
        usage = stop.usage
        if stop.code == "not_started":
            # Nothing ran, so zero is known; a read-back still precedes any resend.
            usage = UsageObservation(
                input_tokens=0, output_tokens=0, cost_microusd=0, completed_at_ms=now
            )
        if not live:
            # A journal's late stop: it confirms the stop, and changes nothing else.
            self._confirm_late(db, rows, "stop:" + command.id, usage)
            mark_runnable(db, execution, now)
            return
        self._stop_compute(db, rows, "stop:" + command.id, usage)
        self._uncertain(db, principal, rows, command)
        mark_runnable(db, execution, now)


# ---------------------------------------------------------------- sweeper hooks


def _swept(db, command_row_id):
    from .sweeper import ExecutionSweeper

    hint = db.scalar(
        select(ExecutionActionCommand).where(ExecutionActionCommand.id == command_row_id)
    )
    if hint is None:
        return None
    scope = _scope(hint)
    execution = ExecutionSweeper._claim(db, scope, hint.execution_id)
    if execution is None or execution.state in TERMINAL:
        return None
    # The sweeper's claim takes the gate and the execution row together, so the E03
    # admission (a fleet row) comes after it here. It is taken SKIP LOCKED: the hook
    # never waits on it, so no order inversion can deadlock, and a skipped item is
    # retried by the next pass.
    admission = db.scalar(
        select(RuntimeCommandAdmission)
        .where(RuntimeCommandAdmission.id == hint.admission_id)
        .with_for_update(skip_locked=True)
    )
    if admission is None:
        return None
    attempt = record(db, ExecutionAttempt, scope, hint.attempt_id, locked=True)
    intent = record(db, ExecutionActionIntent, scope, hint.intent_id, locked=True)
    command = record(db, ExecutionActionCommand, scope, command_row_id, locked=True)
    return dict(scope=scope, execution=execution, attempt=attempt, intent=intent, command=command)


def lapse(db, command_row_id, *, epoch, event):
    """A lapsed action lease: the write may have happened. Uncertain, never fenced:
    the slot and liability stay, the execution keeps running, and read-back decides."""
    rows = _swept(db, command_row_id)
    if rows is None:
        return False
    command = rows["command"]
    now = database_now_ms(db)
    if command.status != "claimed" or command.lease_expires_at_ms > now:
        return False
    _lapse(db, rows, db.get(RuntimeCommandAdmission, command.admission_id), now, event)
    return True


def _lapse(db, rows, admission, now, event):
    """The lapse transition itself, under the caller's locks (sweeper or late report)."""
    command, attempt, intent = rows["command"], rows["attempt"], rows["intent"]
    execution = rows["execution"]
    command.status = "uncertain"
    attempt.state = "uncertain"
    intent.effect_state = "unknown"
    execution.reason = "action_uncertain"
    if admission is not None and admission.status == "claimed":
        admission.status = "uncertain"  # E03's own record of the lapse
    db.flush()
    event(execution, "attempt_uncertain", attempt.id)
    mark_runnable(db, execution, now)


def expire_command(db, command_row_id, *, epoch, leases):
    """An unclaimed command past its expiry: nothing ran, so zero usage and the
    attempt fails `timeout_before_dispatch`, exactly as an expired E04 delivery."""
    rows = _swept(db, command_row_id)
    if rows is None:
        return False
    command, attempt, intent = rows["command"], rows["attempt"], rows["intent"]
    execution, scope = rows["execution"], rows["scope"]
    admission = db.get(RuntimeCommandAdmission, command.admission_id)
    now = database_now_ms(db)
    if command.status != "admitted" or admission is None or admission.command_expires_at_ms > now:
        return False
    reservation = db.scalar(
        scoped(ExecutionUsageReservation, scope)
        .where(ExecutionUsageReservation.attempt_id == attempt.id)
        .with_for_update()
    )
    command.status = "expired"
    intent.effect_state = "not_dispatched"
    # E03's record of it closes too: never claimed, it can never be claimed now.
    admission.status = "terminal"
    admission.terminal_at_ms = now
    admission.retain_until_ms = max(now + 86400000, admission.command_expires_at_ms + 30000)
    db.flush()
    leases._fail_unstarted(
        db, scope, execution, attempt, reservation, "action-expiry:" + command.id, now, epoch
    )
    return True
