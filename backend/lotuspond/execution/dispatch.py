"""Signed activity dispatch, durable claims, child requests, values and results (E04).

Every operation runs in the caller's transaction and reloads persisted records,
current E03 identity and the initiating principal's authority under locks; nothing
is reused from a request body or an earlier call. A claim is durable and never
reclaimable on timeout: an expired delivery fails its attempt, and a lapsed lease
leaves the attempt uncertain for reconciliation.

Lock order: see `activity_records`. Runtime routes authenticate before calling in,
so the workspace and E03 fleet rows are already held. The initiating principal's
account rows follow them; this is safe because every transaction that locks account
rows of a workspace first holds that workspace row `FOR UPDATE`. Operations that
change capacity counters need the installation cursor, which Task 5's order puts
first: they take it with `NOWAIT`, so a late acquisition never waits and cannot
close a cycle (a busy cursor refuses with a retryable code).
"""

import json
import logging
from functools import wraps
from hashlib import sha256

from lotuspond_runtime.activity_protocol import (
    MAX_DELIVERY_MS,
    MAX_LEASE_MS,
    MAX_VALUE_BYTES,
    ActivityLease,
    ActivityResult,
    ChildActivityRequest,
)
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import RuntimeIdentity
from lotuspond_sdk.activity import (
    ActivityDelivery,
    ActivitySignature,
    ArtifactHandle,
    canonical_activity_json,
    verify_activity,
)
from lotuspond_sdk.executable.input import JsonLimits, decode_executable_bytes, project_json
from lotuspond_sdk.trust import TrustSet
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from ..fleet.authentication import RuntimePrincipal, machine_fields, machine_query
from ..fleet.identity import locked_identity
from ..fleet.rotation import final_time, require_live, require_process
from ..models import (
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionDeploymentRevision,
    ExecutionLease,
    ExecutionReconciliationObservation,
    ExecutionRequest,
    ExecutionSchedulerCursor,
    ExecutionStepInstance,
    ExecutionUsageReservation,
    RuntimeCredential,
    RuntimeInstance,
    RuntimeSession,
    new_id,
)
from .activity_records import (
    CHILD_ORDINAL_BASE,
    RESULT_PREFIX,
    VALUE_PREFIX,
    ActivityRecordSource,
    child_digest,
    initiator,
    lease_revision,
    lease_token,
    lease_view,
    registered_values,
    reserved_until,
    scope_of,
    signature_text,
    value_digest,
)
from .artifacts import ArtifactPolicy
from .authorization import lock, record, scoped
from .budgets import BudgetDemand
from .contracts import canonical_value
from .deployments import load_deployment
from .errors import ExecutionError
from .expressions import ExpressionError, typed_schema
from .orchestrator import mark_runnable
from .progression import record_event
from .publication import _Lookup

VALUE_LIMITS = JsonLimits(MAX_VALUE_BYTES, 32, 10000)
POLL_CANDIDATES = 10
# Within the window, at most this many pending deliveries and children, so that
# refused ones awaiting the sweeper never hide every root.
POLL_CLASS = 4
# A delivery is signed only with at least this long left to claim it.
MIN_DELIVERY_MS = 1000
# Runtime-stored values per execution: results and child arguments, bounded.
MAX_VALUES = 1024
logger = logging.getLogger("lotuspond.execution")
RETRYABLE = frozenset({"unavailable", "rate_limited", "timeout_before_dispatch"})


def activity_service(function):
    """Fixed codes only. Identity refusals stay `FleetError`; everything else is closed."""

    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ExecutionError, FleetError):
            raise
        except SQLAlchemyError:
            raise ExecutionError("authority_unavailable") from None
        except Exception:
            raise ExecutionError("invalid_request") from None

    return invoke


def cursor_nowait(db, installation_id):
    """The installation cursor taken after other rows: never waits (see module doc)."""
    try:
        with db.begin_nested():
            row = db.scalar(
                select(ExecutionSchedulerCursor)
                .where(ExecutionSchedulerCursor.installation_id == installation_id)
                .with_for_update(nowait=True)
                .execution_options(populate_existing=True)
            )
    except OperationalError:
        raise ExecutionError("capacity_unavailable") from None
    if row is None:
        raise ExecutionError("capacity_unavailable")
    return row


def failure_content(code, at_ms) -> bytes:
    return canonical_value({"failure": code, "failed_at_ms": at_ms})


def close_children(db, budgets, scope, parent, now):
    """End the never-started children of a root attempt whose own work is over.

    The caller holds the execution row (every activity transaction locks it before
    any attempt row) and the installation cursor. A reserved or dispatched child
    never started: it is cancelled, its delivery can no longer be claimed, and its
    compute stop is confirmed so containment can complete. A claimed child, or an
    uncertain one not yet confirmed stopped, may still run inside the parent's
    compute slot: the parent's stop is refused (`conflict`) until that child reports
    or is reconciled. Returns the cancelled child IDs.
    """
    children = list(
        db.scalars(
            scoped(ExecutionAttempt, scope)
            .where(
                ExecutionAttempt.parent_attempt_id == parent.id,
                ExecutionAttempt.state.in_(("reserved", "dispatched", "claimed", "uncertain")),
            )
            .order_by(ExecutionAttempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    unstopped = set(
        db.scalars(
            select(ExecutionUsageReservation.attempt_id).where(
                ExecutionUsageReservation.workspace_id == scope.workspace_id,
                ExecutionUsageReservation.attempt_id.in_([c.id for c in children]),
                ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
            )
        )
    )
    # A claimed child, or an uncertain one whose compute was never confirmed stopped,
    # may still run inside the parent's slot: the parent's compute cannot stop yet.
    if any(
        child.state == "claimed" or (child.state == "uncertain" and child.id in unstopped)
        for child in children
    ):
        raise ExecutionError("conflict")
    closed = [child for child in children if child.state in ("reserved", "dispatched")]
    for child in closed:
        delivery = lock(
            db,
            scoped(ExecutionActivityDelivery, scope).where(
                ExecutionActivityDelivery.attempt_id == child.id
            ),
        )
        if delivery is not None and delivery.status == "pending":
            delivery.status = "conflict"
        child.state = "cancelled"
    db.flush()
    for child in closed:
        reservation = db.scalar(
            scoped(ExecutionUsageReservation, scope).where(
                ExecutionUsageReservation.attempt_id == child.id
            )
        )
        if reservation is None or reservation.compute_stopped_at_ms is not None:
            continue
        observation = ExecutionReconciliationObservation(
            **scope.model_dump(),
            execution_id=child.execution_id,
            attempt_id=child.id,
            source_id="activity",
            observation_id="parent-ended:" + child.id,
            digest=sha256(b"parent-ended:" + child.id.encode()).hexdigest(),
            code="compute_stopped",
            observed_at_ms=now,
        )
        db.add(observation)
        db.flush()
        budgets.confirm_stopped(db, child.id, observation.id)
    return [child.id for child in closed]


class ActivityService:
    """Runtime-facing activity operations; providers are constructor-injected."""

    def __init__(
        self,
        *,
        fleet,
        budgets,
        artifacts,
        artifact_policy,
        trust,
        signer,
        demand,
        installation_id,
        records=None,
        test_mode=False,
    ):
        if any(item is None for item in (fleet, budgets, artifacts, trust, signer, demand)):
            raise ValueError("Fleet, budget, artifact, trust, signer and demand are required")
        if not test_mode and any(
            getattr(item, "test_only", False)
            for item in (fleet.catalog, fleet.recovery, budgets, artifacts, trust, signer, demand)
        ):
            raise ValueError("Synthetic providers require test mode")
        self.fleet, self.budgets, self.artifacts = fleet, budgets, artifacts
        self.policy = ArtifactPolicy.model_validate(artifact_policy)
        if self.policy.source_id is not None:
            raise ValueError("Activity values use the workspace policy")
        self.trust, self.signer, self.demand = trust, signer, demand
        self.installation_id = installation_id
        if installation_id != budgets.installation_id:
            raise ValueError("Installation mismatch")
        self.records = records or ActivityRecordSource()
        self.timeout = budgets.timeout_seconds
        self._slots = {name: _Lookup() for name in ("trust", "signer", "demand")}

    def close(self):
        for slot in self._slots.values():
            slot.closed = True

    def lock_cursor(self, db):
        """Routes for child requests and results call this before runtime authentication.

        Task 5's order puts the installation cursor first. The services still take
        it with NOWAIT, so a caller that skipped this cannot deadlock; it is refused
        with a retryable code while another transaction holds the cursor.
        """
        return self.budgets._cursor(db)

    # ------------------------------------------------------------ identity

    def _principal(self, db, recipient):
        """A recipient hint selects nothing; `locked_identity` proves it current."""
        recipient = RuntimeIdentity.model_validate(recipient)
        instance = db.scalar(
            select(RuntimeInstance).where(
                RuntimeInstance.id == recipient.instance_id,
                RuntimeInstance.workspace_id == recipient.scope.workspace_id,
                RuntimeInstance.project_id == recipient.scope.project_id,
                RuntimeInstance.environment_id == recipient.scope.environment_id,
                RuntimeInstance.pool_id == recipient.pool_id,
                RuntimeInstance.installation_id == recipient.installation_id,
                RuntimeInstance.recovery_epoch == recipient.recovery_epoch,
            )
        )
        if instance is None:
            raise FleetError("unauthenticated")
        credential = db.scalar(
            machine_query(RuntimeCredential, instance).where(
                RuntimeCredential.instance_id == instance.id,
                RuntimeCredential.version == recipient.credential_version,
                RuntimeCredential.status == "active",
            )
        )
        session = db.scalar(
            machine_query(RuntimeSession, instance).where(
                RuntimeSession.instance_id == instance.id,
                RuntimeSession.status == "active",
                RuntimeSession.generation == recipient.generation,
            )
        )
        if credential is None or session is None:
            raise FleetError("unauthenticated")
        return RuntimePrincipal(recipient, credential.id, session.id)

    def _identity(self, db, principal):
        if type(principal) is not RuntimePrincipal or principal.session_id is None:
            raise FleetError("unauthenticated")
        if principal.identity.installation_id != self.installation_id:
            raise FleetError("unauthenticated")
        return locked_identity(db, principal, self.fleet, require_session=True)

    def _finish(self, db, locked):
        """Current recovery and database time after every lock and provider wait."""
        instance, credential, session, _, _ = locked
        now = final_time(db, instance, self.fleet)
        require_live(credential, now)
        require_process(instance, session, now)
        return now

    # ------------------------------------------------------------ providers

    def _call(self, name, method, args, code):
        return self._slots[name].call(getattr(self, name), method, args, self.timeout, code)

    def _trust(self, scope, now):
        try:
            return TrustSet.model_validate(
                self._call("trust", "current", (scope, now), "trust_unavailable")
            )
        except ExecutionError:
            raise
        except Exception:
            raise ExecutionError("trust_unavailable") from None

    def _verify(self, delivery, trust):
        """The stored signature must still verify under current independent trust."""
        try:
            signature = ActivitySignature.model_validate_json(bytes(delivery.canonical_signature))
            report = verify_activity(
                decode_executable_bytes(bytes(delivery.canonical_envelope)), signature, trust
            )
        except Exception:
            raise ExecutionError("conflict") from None
        if not report.valid or report.sha256 != delivery.digest:
            raise ExecutionError("trust_unavailable")

    # ------------------------------------------------------------ rows

    def _locked(self, db, principal, attempt_id, *, counters=False, authority=True):
        """Lock one attempt's rows in the documented order; returns them by name.

        `authority` reauthorizes the initiator and reloads the deployment. Recording
        an outcome (a result or a stored value) grants nothing and skips it, so a
        revoked initiator cannot discard a known outcome; renewal and new work keep it.
        """
        if counters:
            cursor_nowait(db, self.installation_id)
        locked = self._identity(db, principal)
        identity = principal.identity
        scope = identity.scope
        hint = record(db, ExecutionAttempt, scope, attempt_id)
        execution_hint = record(db, Execution, scope, hint.execution_id)
        revision = record(
            db, ExecutionDeploymentRevision, scope, execution_hint.deployment_revision_id
        )
        # Another pool's runtime is refused before any account row or provider call.
        if revision.pool_id != identity.pool_id:
            raise FleetError("unauthenticated")
        actor = initiator(db, execution_hint) if authority else None
        if counters:
            lock(db, scoped(ExecutionCapacity, scope))
        if authority:
            _, revision = load_deployment(
                db, actor, revision.id, dependencies=self.budgets.deployments
            )
        execution = record(db, Execution, scope, hint.execution_id, locked=True)
        step = record(db, ExecutionStepInstance, scope, hint.step_instance_id, locked=True)
        ids = sorted({hint.id, *([hint.parent_attempt_id] if hint.parent_attempt_id else [])})
        attempts = {i: record(db, ExecutionAttempt, scope, i, locked=True) for i in ids}
        reservations = {
            row.attempt_id: row
            for row in db.scalars(
                scoped(ExecutionUsageReservation, scope)
                .where(ExecutionUsageReservation.attempt_id.in_(ids))
                .order_by(ExecutionUsageReservation.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        deliveries = {
            row.attempt_id: row
            for row in db.scalars(
                scoped(ExecutionActivityDelivery, scope)
                .where(ExecutionActivityDelivery.attempt_id.in_(ids))
                .order_by(ExecutionActivityDelivery.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        leases = {
            row.attempt_id: row
            for row in db.scalars(
                scoped(ExecutionLease, scope)
                .where(ExecutionLease.attempt_id.in_(ids))
                .order_by(ExecutionLease.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        attempt = attempts[hint.id]
        parent = attempts.get(hint.parent_attempt_id) if hint.parent_attempt_id else None
        return dict(
            locked=locked,
            actor=actor,
            revision=revision,
            execution=execution,
            step=step,
            attempt=attempt,
            parent=parent,
            reservations=reservations,
            deliveries=deliveries,
            leases=leases,
        )

    def _held(self, rows, attempt, principal, now):
        """The attempt's live lease, held by this principal's session; else refused."""
        lease = rows["leases"].get(attempt.id)
        delivery = rows["deliveries"].get(attempt.id)
        if (
            lease is None
            or delivery is None
            or lease.status != "active"
            or lease.token_digest != lease_token(delivery, principal.session_id)
            or delivery.session_id != principal.session_id
            or delivery.instance_id != principal.identity.instance_id
            or delivery.generation != principal.identity.generation
            or now >= lease.expires_at_ms
        ):
            raise ExecutionError("conflict")
        return lease, delivery

    def _event(self, db, execution, epoch, code, *, attempt_id, detail=None):
        """`epoch` is the recovery epoch `locked_identity` proved current under locks."""
        record_event(
            db,
            self.artifacts,
            self.policy,
            self.installation_id,
            execution,
            epoch,
            code,
            path=None,
            attempt_id=attempt_id,
            detail=detail,
            origin="activity",
        )

    # ------------------------------------------------------------ dispatch

    @activity_service
    def dispatch_activity(
        self, db, attempt_id: str, recipient: RuntimeIdentity
    ) -> ActivityDelivery:
        """Sign and persist the one delivery of a reserved attempt for this recipient."""
        return self._dispatch(db, self._principal(db, recipient), attempt_id)

    def _dispatch(self, db, principal, attempt_id):
        rows = self._locked(db, principal, attempt_id)
        execution, attempt, parent = rows["execution"], rows["attempt"], rows["parent"]
        identity = principal.identity
        existing = rows["deliveries"].get(attempt.id)
        if existing is not None:
            # One delivery per attempt: an equal recipient gets the same bytes back.
            if (
                existing.status != "pending"
                or existing.session_id != principal.session_id
                or existing.instance_id != identity.instance_id
                or existing.generation != identity.generation
            ):
                raise ExecutionError("conflict")
            if execution.requested_state != "running" or execution.state != "running":
                raise ExecutionError("forbidden")
            now = self._finish(db, rows["locked"])
            if now >= existing.expires_at_ms:
                raise ExecutionError("conflict")
            self.records.load(db, attempt.id)
            self._verify(existing, self._trust(identity.scope, now))
            return ActivityDelivery(
                envelope=bytes(existing.canonical_envelope).decode("utf-8"),
                signature=bytes(existing.canonical_signature).decode("utf-8"),
            )
        reservation = rows["reservations"].get(attempt.id)
        if execution.requested_state != "running" or execution.state != "running":
            raise ExecutionError("forbidden")
        if (
            attempt.state != "reserved"
            or reservation is None
            or reservation.state != "reserved"
            or reservation.compute_stopped_at_ms is not None
        ):
            raise ExecutionError("conflict")
        pins = self.records.pins(db, execution, rows["step"], attempt)
        grant, tenant, recovery = self.budgets._grants(db, identity.scope)
        self.budgets._final(db, rows["actor"], rows["revision"], grant, tenant, recovery, execution)
        if recovery.epoch != identity.recovery_epoch:
            raise ExecutionError("recovery_unavailable")
        now = self._finish(db, rows["locked"])
        parent_deadline = None
        if parent is not None:
            # A child goes only to the runtime session holding its parent's live lease.
            self._held(rows, parent, principal, now)
            parent_deadline = self.records.load(db, parent.id).deadline_ms
        deadline = min(execution.deadline_ms, now + pins.timeout_ms)
        if parent_deadline is not None:
            deadline = min(deadline, parent_deadline)
        if reserved_until(reservation) <= now:
            raise ExecutionError("budget_exceeded")  # its reserved active time is spent
        # The first claim must fit in the reserved active time as well, with time left.
        expires = min(now + MAX_DELIVERY_MS, deadline, reserved_until(reservation))
        if expires - now < MIN_DELIVERY_MS:
            raise ExecutionError(
                "budget_exceeded" if expires == reserved_until(reservation) else "forbidden"
            )
        trust = self._trust(identity.scope, now)
        instance = rows["locked"][0]
        envelope = self.records.envelope(
            pins,
            installation_id=self.installation_id,
            recovery_epoch=identity.recovery_epoch,
            recipient=dict(
                pool_id=identity.pool_id,
                instance_id=identity.instance_id,
                generation=identity.generation,
            ),
            delivery=dict(id=new_id(), replay_id=new_id(), issued_at_ms=now, expires_at_ms=expires),
        )
        try:
            signature = ActivitySignature.model_validate(
                self._call("signer", "sign", (envelope,), "trust_unavailable")
            )
        except ExecutionError:
            raise
        except Exception:
            raise ExecutionError("trust_unavailable") from None
        if not verify_activity(envelope, signature, trust).valid:
            raise ExecutionError("trust_unavailable")
        # Provider lookups may have waited through an expiry or revocation.
        later = self._finish(db, rows["locked"])
        if later >= expires or later >= deadline:
            raise ExecutionError("forbidden")
        canonical = canonical_activity_json(envelope)
        text = signature_text(signature)
        db.add(
            ExecutionActivityDelivery(
                **machine_fields(instance),
                id=envelope.delivery_id,
                execution_id=execution.id,
                attempt_id=attempt.id,
                instance_id=instance.id,
                session_id=principal.session_id,
                generation=identity.generation,
                replay_id=envelope.replay_id,
                canonical_envelope=canonical,
                canonical_signature=text.encode("ascii"),
                digest=sha256(canonical).hexdigest(),
                issued_at_ms=envelope.issued_at_ms,
                expires_at_ms=envelope.expires_at_ms,
                retain_until_ms=execution.deadline_ms + 86400000,
                status="pending",
            )
        )
        attempt.state = "dispatched"
        db.flush()
        self._event(
            db, execution, identity.recovery_epoch, "attempt_dispatched", attempt_id=attempt.id
        )
        return ActivityDelivery(envelope=canonical.decode("utf-8"), signature=text)

    @activity_service
    def poll_activity(self, db, principal) -> ActivityDelivery | None:
        """The next delivery for this runtime session: a pending one, or a new one.

        Pending deliveries of running executions come first, then children of
        attempts this session holds, then roots in admission order. At most
        `POLL_CANDIDATES` are tried (at most `POLL_CLASS` pending deliveries and
        children, so roots always get a turn), each in its own savepoint: a refused candidate
        (revoked initiator, disabled deployment, passed deadline) is skipped, so it
        never blocks the rest of the pool. Scheduling and reservation (Task 5)
        happen before, in their own transaction.
        """
        self.fleet.limit("activity-poll:" + principal.identity.instance_id, 60)
        locked = self._identity(db, principal)
        identity = principal.identity
        now = locked[-1]
        pending = list(
            db.scalars(
                select(ExecutionActivityDelivery.attempt_id)
                .join(Execution, Execution.id == ExecutionActivityDelivery.execution_id)
                .where(
                    ExecutionActivityDelivery.workspace_id == identity.scope.workspace_id,
                    ExecutionActivityDelivery.project_id == identity.scope.project_id,
                    ExecutionActivityDelivery.environment_id == identity.scope.environment_id,
                    ExecutionActivityDelivery.instance_id == identity.instance_id,
                    ExecutionActivityDelivery.session_id == principal.session_id,
                    ExecutionActivityDelivery.generation == identity.generation,
                    ExecutionActivityDelivery.status == "pending",
                    ExecutionActivityDelivery.expires_at_ms > now,
                    Execution.requested_state == "running",
                    Execution.state == "running",
                )
                .order_by(ExecutionActivityDelivery.issued_at_ms, ExecutionActivityDelivery.id)
                .limit(POLL_CLASS)
            )
        )
        held = (
            select(ExecutionActivityDelivery.attempt_id)
            .join(ExecutionLease, ExecutionLease.delivery_id == ExecutionActivityDelivery.id)
            .where(
                ExecutionActivityDelivery.session_id == principal.session_id,
                ExecutionLease.status == "active",
            )
        )
        reserved = (
            select(ExecutionAttempt.id)
            .join(Execution, Execution.id == ExecutionAttempt.execution_id)
            .join(
                ExecutionDeploymentRevision,
                ExecutionDeploymentRevision.id == Execution.deployment_revision_id,
            )
            .join(
                ExecutionUsageReservation,
                ExecutionUsageReservation.attempt_id == ExecutionAttempt.id,
            )
            .where(
                ExecutionAttempt.workspace_id == identity.scope.workspace_id,
                ExecutionAttempt.project_id == identity.scope.project_id,
                ExecutionAttempt.environment_id == identity.scope.environment_id,
                ExecutionDeploymentRevision.pool_id == identity.pool_id,
                Execution.requested_state == "running",
                Execution.state == "running",
                ExecutionAttempt.state == "reserved",
                ExecutionUsageReservation.state == "reserved",
                ExecutionUsageReservation.compute_stopped_at_ms.is_(None),
                ~select(ExecutionActivityDelivery.id)
                .where(ExecutionActivityDelivery.attempt_id == ExecutionAttempt.id)
                .exists(),
            )
            .order_by(Execution.admission_sequence, ExecutionAttempt.ordinal, ExecutionAttempt.id)
        )
        children = list(
            db.scalars(
                reserved.where(ExecutionAttempt.parent_attempt_id.in_(held)).limit(POLL_CLASS)
            )
        )
        roots = list(
            db.scalars(
                reserved.where(ExecutionAttempt.parent_attempt_id.is_(None)).limit(
                    POLL_CANDIDATES - len(pending) - len(children)
                )
            )
        )
        for attempt_id in [*pending, *children, *roots]:
            try:
                with db.begin_nested():
                    return self._dispatch(db, principal, attempt_id)
            except (ExecutionError, FleetError) as error:
                # Locks taken for a refused candidate were released with its savepoint.
                logger.info("Activity candidate skipped: %s", error.code)
        self._finish(db, locked)
        return None

    # ------------------------------------------------------------ claim

    @activity_service
    def claim_activity(self, db, principal, delivery_id: str, digest: str) -> ActivityLease:
        """Claim a delivery once; an equal claim by the same session returns the same lease."""
        identity = principal.identity
        delivery_hint = record(db, ExecutionActivityDelivery, identity.scope, delivery_id)
        rows = self._locked(db, principal, delivery_hint.attempt_id)
        execution, attempt = rows["execution"], rows["attempt"]
        delivery = rows["deliveries"].get(attempt.id)
        if (
            delivery is None
            or delivery.id != delivery_hint.id
            or delivery.instance_id != identity.instance_id
            or delivery.generation != identity.generation
            or delivery.pool_id != identity.pool_id
        ):
            raise FleetError("unauthenticated")
        if delivery.session_id != principal.session_id or delivery.digest != digest:
            raise ExecutionError("conflict")
        records = self.records.load(db, attempt.id)
        grant, tenant, recovery = self.budgets._grants(db, identity.scope)
        self.budgets._final(db, rows["actor"], rows["revision"], grant, tenant, recovery)
        if recovery.epoch != delivery.recovery_epoch:
            raise ExecutionError("recovery_unavailable")
        self._verify(delivery, self._trust(identity.scope, rows["locked"][-1]))
        now = self._finish(db, rows["locked"])
        lease = rows["leases"].get(attempt.id)
        if lease is not None:
            # Lost acknowledgement: the same live lease, never a second invocation,
            # even after a pause or cancel (renewal then reports the cancellation).
            if (
                lease.status != "active"
                or lease.token_digest != lease_token(delivery, principal.session_id)
                or now >= lease.expires_at_ms
            ):
                raise ExecutionError("conflict")
            return lease_view(lease, delivery, records.deadline_ms)
        if execution.requested_state != "running" or execution.state != "running":
            raise ExecutionError("forbidden")
        if (
            delivery.status != "pending"
            or attempt.state != "dispatched"
            or now >= delivery.expires_at_ms
            or now >= records.deadline_ms
        ):
            raise ExecutionError("conflict")
        if rows["parent"] is not None:
            self._held(rows, rows["parent"], principal, now)
        reservation = rows["reservations"].get(attempt.id)
        if reservation is None or reservation.compute_stopped_at_ms is not None:
            raise ExecutionError("conflict")
        expires = min(now + MAX_LEASE_MS, records.deadline_ms, reserved_until(reservation))
        if expires <= now:
            raise ExecutionError("budget_exceeded")
        row = ExecutionLease(
            **identity.scope.model_dump(),
            execution_id=execution.id,
            attempt_id=attempt.id,
            delivery_id=delivery.id,
            token_digest=lease_token(delivery, principal.session_id),
            claimed_at_ms=now,
            renewed_at_ms=now,
            expires_at_ms=expires,
            status="active",
        )
        db.add(row)
        delivery.status = "leased"
        attempt.state = "claimed"
        db.flush()
        self._event(
            db, execution, identity.recovery_epoch, "attempt_claimed", attempt_id=attempt.id
        )
        return lease_view(row, delivery, records.deadline_ms)

    # ------------------------------------------------------------ values

    @activity_service
    def store_activity_value(
        self, db, principal, attempt_id: str, content: bytes
    ) -> ArtifactHandle:
        """Encrypt one canonical JSON value for an attempt this session holds.

        Only values stored this way for an attempt can be its result or the
        arguments of its child requests.
        """
        if type(content) is not bytes or not 0 < len(content) <= MAX_VALUE_BYTES:
            raise ExecutionError("invalid_request")
        try:
            value = project_json(decode_executable_bytes(content), limits=VALUE_LIMITS)
        except Exception:
            raise ExecutionError("invalid_request") from None
        if canonical_value(value) != content:
            raise ExecutionError("invalid_request")
        rows = self._locked(db, principal, attempt_id, authority=False)
        now = self._finish(db, rows["locked"])
        self._held(rows, rows["attempt"], principal, now)
        scope = scope_of(rows["execution"])
        stored = db.scalar(
            select(func.count())
            .select_from(ExecutionRequest)
            .where(
                ExecutionRequest.workspace_id == scope.workspace_id,
                ExecutionRequest.execution_id == rows["execution"].id,
                ExecutionRequest.request_key.like(VALUE_PREFIX + "%"),
            )
        )
        if stored >= MAX_VALUES:
            raise ExecutionError("budget_exceeded")
        handle = self.artifacts._put_internal(db, scope, content, self.policy)
        db.add(
            ExecutionRequest(
                **scope.model_dump(),
                execution_id=rows["execution"].id,
                request_key=VALUE_PREFIX + handle.id,
                digest=value_digest(rows["attempt"].id, handle.id),
            )
        )
        db.flush()
        return handle

    def _value(self, db, scope, handle):
        content = self.artifacts._get_internal(db, scope, handle)
        return project_json(decode_executable_bytes(content), limits=VALUE_LIMITS)

    # ------------------------------------------------------------ children

    @activity_service
    def request_child_activity(self, db, principal, request: ChildActivityRequest) -> str:
        """Admit one model round or declared read tool under the parent's root budget."""
        request = ChildActivityRequest.model_validate(request)
        rows = self._locked(db, principal, request.parent_attempt_id, counters=True)
        execution, parent, step = rows["execution"], rows["attempt"], rows["step"]
        scope = scope_of(execution)
        now = self._finish(db, rows["locked"])
        if parent.parent_attempt_id is not None:
            raise ExecutionError("forbidden")  # children do not request children
        self._held(rows, parent, principal, now)
        if parent.state != "claimed" or execution.requested_state != "running":
            raise ExecutionError("forbidden")
        pins = self.records.pins(db, execution, step, parent)
        declaration = next(
            (
                item
                for item in pins.declarations
                if item.kind == request.kind and item.name == request.declaration_name
            ),
            None,
        )
        if declaration is None:
            raise ExecutionError("forbidden")
        digest = child_digest(
            request.request_id, request.kind, request.declaration_name, request.arguments
        )
        existing = db.scalar(
            scoped(ExecutionAttempt, scope).where(
                ExecutionAttempt.parent_attempt_id == parent.id,
                ExecutionAttempt.child_request_id == request.request_id,
            )
        )
        if existing is not None:
            if existing.child_request_digest != digest:
                raise ExecutionError("conflict")
            return existing.id
        # The actual arguments: stored by this session for the parent, and typed.
        if request.arguments.id not in registered_values(
            db, scope, parent.id, [request.arguments.id]
        ):
            raise ExecutionError("forbidden")
        value = self._value(db, scope, request.arguments)
        if request.kind == "connector_read":
            schemas = declaration.input_schema
            try:
                if type(value) is not dict or set(value) != {"target", "input"}:
                    raise ExpressionError("type_mismatch")
                typed_schema(value["target"], schemas["target"], schemas["definitions"])
                typed_schema(value["input"], schemas["input"], schemas["definitions"])
            except ExpressionError:
                raise ExecutionError("forbidden") from None
        ordinal = max(
            CHILD_ORDINAL_BASE,
            db.scalar(
                select(func.max(ExecutionAttempt.ordinal)).where(
                    ExecutionAttempt.workspace_id == scope.workspace_id,
                    ExecutionAttempt.execution_id == execution.id,
                    ExecutionAttempt.step_instance_id == step.id,
                )
            )
            or 0,
        )
        child = ExecutionAttempt(
            **scope.model_dump(),
            execution_id=execution.id,
            step_instance_id=step.id,
            ordinal=ordinal + 1,
            parent_attempt_id=parent.id,
            child_request_id=request.request_id,
            child_request_digest=digest,
            input_artifact_id=request.arguments.id,
            input_artifact_version=request.arguments.version,
            input_sha256=request.arguments.sha256,
            state="reserved",
        )
        db.add(child)
        db.flush()
        try:
            demand = BudgetDemand.model_validate(
                self._call("demand", "demand", (child.id,), "price_unavailable")
            )
        except ExecutionError:
            raise
        except Exception:
            raise ExecutionError("price_unavailable") from None
        # Shared root budget: the reservation counts against the execution's limits
        # but takes no compute slot (the parent's slot covers its child).
        self.budgets.reserve_attempt(db, rows["actor"], child.id, demand, shared=True)
        # Provider lookups may have outlasted the session or the parent's lease.
        self._held(rows, parent, principal, self._finish(db, rows["locked"]))
        self._event(
            db,
            execution,
            principal.identity.recovery_epoch,
            "attempt_reserved",
            attempt_id=child.id,
        )
        return child.id

    # ------------------------------------------------------------ results

    @activity_service
    def record_activity_result(self, db, principal, result: ActivityResult) -> None:
        """Record an outcome, settle usage and stop compute; marks the execution due."""
        result = ActivityResult.model_validate(result)
        rows = self._locked(db, principal, result.attempt_id, counters=True, authority=False)
        execution, attempt = rows["execution"], rows["attempt"]
        scope = scope_of(execution)
        now = self._finish(db, rows["locked"])
        digest = sha256(canonical_value(result.model_dump(mode="json"))).hexdigest()
        delivery = rows["deliveries"].get(attempt.id)
        if delivery is None or delivery.session_id != principal.session_id:
            raise ExecutionError("conflict")  # only the claiming session reports
        prior = db.scalar(
            scoped(ExecutionRequest, scope).where(
                ExecutionRequest.request_key == RESULT_PREFIX + attempt.id
            )
        )
        if prior is not None:
            if prior.digest != digest:
                raise ExecutionError("conflict")
            return  # an equal repeated result changes nothing
        lease, delivery = self._held(rows, attempt, principal, now)
        if result.lease_revision > lease_revision(lease) or attempt.state != "claimed":
            raise ExecutionError("conflict")
        closed = []
        if attempt.parent_attempt_id is None:
            # A root's work is over: its children report first, unstarted ones end.
            closed = close_children(db, self.budgets, scope, attempt, now)
        records = self.records.load(db, attempt.id)
        if result.usage.completed_at_ms > now:
            raise ExecutionError("invalid_request")
        outcome, content = "failed", None
        if result.code == "succeeded":
            if result.result is None or result.result.id not in registered_values(
                db, scope, attempt.id, [result.result.id]
            ):
                raise ExecutionError("forbidden")
            value = self._value(db, scope, result.result)
            schema = records.output_schema
            try:
                if schema["schema"] is not None:
                    typed_schema(value, schema["schema"], schema["definitions"])
                outcome = "succeeded"
            except ExpressionError:
                content = failure_content("invalid_result", now)
        else:
            if result.result is not None:
                raise ExecutionError("invalid_request")
            code = "invalid_result" if result.code == "invalid_output" else result.code
            content = failure_content(code, now)
        db.add(
            ExecutionRequest(
                **scope.model_dump(),
                execution_id=execution.id,
                request_key=RESULT_PREFIX + attempt.id,
                digest=digest,
            )
        )
        db.flush()
        # Usage first: unknown usage leaves the attempt uncertain for reconciliation.
        self.budgets.settle_attempt(db, attempt.id, "result:" + delivery.id, result.usage)
        observation = ExecutionReconciliationObservation(
            **scope.model_dump(),
            execution_id=execution.id,
            attempt_id=attempt.id,
            source_id="runtime",
            observation_id="result:" + delivery.id,
            digest=digest,
            code="compute_stopped",
            observed_at_ms=now,
        )
        db.add(observation)
        db.flush()
        self.budgets.confirm_stopped(db, attempt.id, observation.id)
        lease.status = "stopped"
        delivery.status = "delivered"
        if attempt.state == "uncertain":
            execution.state = "reconciliation_required"
            execution.reason = "uncertain"
            db.flush()
            epoch = principal.identity.recovery_epoch
            self._event(db, execution, epoch, "attempt_uncertain", attempt_id=attempt.id)
            self._event(db, execution, epoch, "reconciliation_required", attempt_id=attempt.id)
            return
        if outcome == "succeeded":
            handle = result.result
        else:
            handle = self.artifacts._put_internal(db, scope, content, self.policy)
        attempt.result_artifact_id = handle.id
        attempt.result_artifact_version = handle.version
        attempt.result_sha256 = handle.sha256
        attempt.state = outcome
        db.flush()
        self._event(
            db,
            execution,
            principal.identity.recovery_epoch,
            "attempt_succeeded" if outcome == "succeeded" else "attempt_failed",
            attempt_id=attempt.id,
            detail=None if outcome == "succeeded" else json.loads(content)["failure"],
        )
        for child_id in closed:
            self._event(
                db,
                execution,
                principal.identity.recovery_epoch,
                "cancelled",
                attempt_id=child_id,
                detail="parent_ended",
            )
        # The result and the runnable marker commit together or not at all.
        mark_runnable(db, execution, now)
