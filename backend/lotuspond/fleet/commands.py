"""Internal signed command admission and durable replay; never business execution.

Every API participates in the caller's transaction. The common workspace lock
serializes replay uniqueness across all pools/environments in that workspace.
"""

from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Literal, Protocol

from lotuspond_runtime.canonical import canonical_connection_json, decode_fleet_json
from lotuspond_runtime.commands import delivery_wire, validate_delivery, validate_records
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    AdmissionView,
    CommandDelivery,
    Digest,
    OpaqueId,
    PinnedCommandRecords,
    RuntimeIdentity,
    RuntimeObservation,
)
from lotuspond_runtime.trust import (
    CommandTrustAuthority,
    validate_snapshot,
)
from lotuspond_runtime.trust import (
    UnavailableCommandTrustAuthority as UnavailableCommandTrustAuthority,
)
from lotuspond_sdk.executable.signing import (
    command_signature_envelope_json,
    validate_command_signature_envelope,
)
from lotuspond_sdk.executable.wire import (
    RuntimeCommand,
    canonical_wire_json,
    validate_runtime_command,
    wire_digest,
)
from pydantic import TypeAdapter
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import (
    Connection,
    ConnectionVersion,
    RuntimeCommandAdmission,
    RuntimeCredential,
    RuntimeInstance,
    RuntimePool,
    RuntimePoolPolicy,
    RuntimeSession,
    SecretReference,
)
from .authentication import (
    RuntimePrincipal,
    lock,
    machine_fields,
    machine_query,
    row_scope,
)
from .authority import FleetDependencies, RecoveryAuthority
from .catalog import InstalledCatalog
from .identity import locked_identity
from .management import record, scoped, stored_config, stored_policy
from .rotation import final_time, require_live, require_process


class PinnedRecordSource(Protocol):
    def load(self, db: Session, command: RuntimeCommand) -> PinnedCommandRecords: ...


class BusinessDispatchAuthority(Protocol):
    def authorize(
        self,
        current_identity: RuntimeIdentity,
        command_digest: str,
        pinned_records: PinnedCommandRecords,
    ) -> None: ...


class DenyPinnedRecordSource:
    def load(self, db: Session, command: RuntimeCommand) -> PinnedCommandRecords:
        raise FleetError("authority_unavailable")


class DenyBusinessDispatchAuthority:
    def authorize(self, current_identity, command_digest, pinned_records) -> None:
        raise FleetError("forbidden")


@dataclass(frozen=True)
class CommandDependencies:
    """Internal hooks, constructor-only in test settings; no production loader."""

    dispatch: BusinessDispatchAuthority
    records: PinnedRecordSource
    trust: CommandTrustAuthority


class _HookSlot:
    """One bounded external call; lost/late replies never become cached authority."""

    def __init__(self):
        self.lock = Lock()

    def call(self, fn, *args):
        if not self.lock.acquire(blocking=False):
            raise FleetError("authority_unavailable")
        done, result = Event(), []

        def run():
            try:
                result.append((True, fn(*args)))
            except FleetError as error:
                result.append((False, error))
            except Exception:
                result.append((False, FleetError("authority_unavailable")))
            finally:
                done.set()
                self.lock.release()

        try:
            Thread(target=run, name="fleet-command-authority", daemon=True).start()
        except Exception:
            self.lock.release()
            raise FleetError("authority_unavailable") from None
        if not done.wait(5) or not result:
            raise FleetError("authority_unavailable")
        valid, value = result[0]
        if not valid:
            raise value
        return value


def view(row):
    return AdmissionView(
        command_id=row.command_id, command_sha256=row.command_digest, status=row.status
    )


class CommandService:
    def __init__(
        self,
        recovery: RecoveryAuthority,
        dispatch: BusinessDispatchAuthority,
        records: PinnedRecordSource,
        trust: CommandTrustAuthority,
        *,
        dependencies: FleetDependencies | None = None,
    ):
        if dependencies is not None and dependencies.recovery is not recovery:
            raise FleetError("authority_unavailable")
        self.dependencies = dependencies or FleetDependencies(recovery, InstalledCatalog())
        self.dispatch, self.records, self.trust = dispatch, records, trust
        self._trust_slot, self._dispatch_slot = _HookSlot(), _HookSlot()

    def _recipient(self, db, recipient):
        recipient = RuntimeIdentity.model_validate(recipient)
        # Hints select no result and confer no authority. locked_identity performs
        # all parent/current/session/credential checks with refreshed common locks.
        instance = db.scalar(
            scoped(RuntimeInstance, recipient.scope).where(
                RuntimeInstance.id == recipient.instance_id,
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

    def _current(self, db, principal):
        return locked_identity(db, principal, self.dependencies, require_session=True)

    def _finish(self, db, locked):
        instance, credential, session, _, _ = locked
        now = final_time(db, instance, self.dependencies)
        require_live(credential, now)
        require_process(instance, session, now)
        return now

    def _records(self, db, command, locked):
        try:
            pins = validate_records(self.records.load(db, command))
        except FleetError:
            raise
        except Exception:
            raise FleetError("authority_unavailable") from None
        instance = locked[0]
        scope = row_scope(instance)
        pool = record(db, RuntimePool, scope, instance.pool_id)  # common parent already locked
        revision = db.scalar(
            scoped(RuntimePoolPolicy, scope).where(
                RuntimePoolPolicy.pool_id == pool.id,
                RuntimePoolPolicy.version == pool.current_policy_version,
            )
        )
        if revision is None:
            raise FleetError("authority_unavailable")
        policy = stored_policy(db, scope, revision)
        if pins.connection_id not in policy.connection_ids:
            raise FleetError("forbidden")
        # Shared, as `locked_identity` shared it: a status fence only (option C).
        connection = record(db, Connection, scope, pins.connection_id, lock=True, share=True)
        if connection.status != "active":
            raise FleetError("forbidden")
        version = lock(
            scoped(ConnectionVersion, scope).where(
                ConnectionVersion.connection_id == connection.id,
                ConnectionVersion.version == pins.connection_version,
            ),
            db,
        )
        if version is None or canonical_connection_json(stored_config(db, scope, version)) != (
            canonical_connection_json(pins.configuration)
        ):
            raise FleetError("command_rejected")
        config = pins.configuration
        observation = RuntimeObservation.model_validate_json(instance.observation_ceiling)
        if (
            config.connector not in policy.connectors
            or config.connector not in observation.connectors
        ):
            raise FleetError("forbidden")
        descriptor = self.dependencies.catalog.connector(config.connector)
        operation = descriptor.operations.get(pins.intent.binding.operation)
        if (
            operation is None
            or operation.requires_destination
            and not config.destinations
            or operation.requires_secret
            and not config.secrets
        ):
            raise FleetError("forbidden")
        for binding in sorted(pins.secret_bindings, key=lambda b: b.reference_id):
            reference = record(db, SecretReference, scope, binding.reference_id, lock=True)
            if (
                reference.status != "active"
                or reference.version != binding.version
                or reference.binding_id != binding.binding_id
                or not self.dependencies.catalog.secret_binding(binding.binding_id, binding.version)
            ):
                raise FleetError("forbidden")
        return pins

    def _authorize(self, db, principal, delivery, locked):
        command = validate_runtime_command(decode_fleet_json(delivery.command_bytes))
        pins = self._records(db, command, locked)
        snapshot = self._trust_slot.call(self.trust.current, principal.identity.installation_id)
        # Validate detached public-data records before passing them across the
        # external authority boundary, and recheck actual time after all lookups.
        snapshot = validate_snapshot(snapshot, now_ms=self._finish(db, locked))
        validate_delivery(
            delivery,
            identity=principal.identity,
            records=pins,
            trust=snapshot,
            now_ms=self._finish(db, locked),
        )
        result = self._dispatch_slot.call(
            self.dispatch.authorize, principal.identity, wire_digest(command), pins
        )
        if result is not None:
            raise FleetError("authority_unavailable")
        # Dispatch lookup may have waited through a trust withdrawal. Require
        # a fresh independently available snapshot after that wait; do not turn
        # an earlier still-unexpired snapshot into cached current authority.
        snapshot = self._trust_slot.call(self.trust.current, principal.identity.installation_id)
        now = self._finish(db, locked)
        snapshot = validate_snapshot(snapshot, now_ms=now)
        validate_delivery(
            delivery, identity=principal.identity, records=pins, trust=snapshot, now_ms=now
        )
        return command, snapshot, pins, now

    def _row_delivery(self, row):
        delivery = CommandDelivery(
            bytes(row.command_bytes), bytes(row.signature_bytes), row.trust_revision
        )
        try:
            delivery_wire(delivery)
            command = validate_runtime_command(decode_fleet_json(delivery.command_bytes))
            if (
                command.command_id != row.command_id
                or command.replay_id != row.replay_id
                or wire_digest(command) != row.command_digest
                or command.expires_at_ms != row.command_expires_at_ms
                or command.worker_id != row.recipient
                or command.fencing_generation != row.generation
                or command.installation_id != row.installation_id
                or command.recovery_epoch != row.recovery_epoch
                or any(
                    getattr(command, name) != getattr(row, name)
                    for name in ("workspace_id", "project_id", "environment_id")
                )
            ):
                raise FleetError("command_rejected")
        except FleetError:
            raise
        except Exception:
            raise FleetError("command_rejected") from None
        return delivery

    def _same_recipient(self, row, principal):
        identity = principal.identity
        if (
            row_scope(row) != identity.scope
            or row.pool_id != identity.pool_id
            or row.instance_id != identity.instance_id
            or row.recipient != identity.instance_id
            or row.generation != identity.generation
            or row.session_id != principal.session_id
        ):
            raise FleetError("conflict")

    def admit(
        self, db: Session, recipient: RuntimeIdentity, command_bytes: bytes, signature_bytes: bytes
    ) -> AdmissionView:
        # Initial delivery uses revision1 solely for envelope bounds. Current
        # provisioned trust supplies the persisted revision below.
        raw = CommandDelivery(command_bytes, signature_bytes, 1)
        delivery_wire(raw)
        try:
            command = validate_runtime_command(decode_fleet_json(command_bytes))
            canonical = canonical_wire_json(command)
            signature = command_signature_envelope_json(
                validate_command_signature_envelope(decode_fleet_json(signature_bytes))
            )
        except FleetError:
            raise
        except Exception:
            raise FleetError("command_rejected") from None
        principal = self._recipient(db, recipient)
        locked = self._current(db, principal)
        delivery = CommandDelivery(canonical, signature, 1)
        command, snapshot, pins, _ = self._authorize(db, principal, delivery, locked)
        identity = principal.identity
        rows = list(
            db.scalars(
                select(RuntimeCommandAdmission)
                .where(
                    RuntimeCommandAdmission.installation_id == identity.installation_id,
                    RuntimeCommandAdmission.recovery_epoch == identity.recovery_epoch,
                    RuntimeCommandAdmission.workspace_id == identity.scope.workspace_id,
                    or_(
                        RuntimeCommandAdmission.command_id == command.command_id,
                        RuntimeCommandAdmission.replay_id == command.replay_id,
                    ),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        # Replay-row locks can wait after the first authorization. Reconstruct
        # current records and all independent authority again before exposing
        # either a duplicate result or a new durable admission.
        command, snapshot, pins, now = self._authorize(db, principal, delivery, locked)
        if rows:
            if len(rows) != 1:
                raise FleetError("conflict")
            row = rows[0]
            self._same_recipient(row, principal)
            if (
                row.command_id != command.command_id
                or row.replay_id != command.replay_id
                or bytes(row.command_bytes) != canonical
                or row.command_digest != wire_digest(command)
            ):
                raise FleetError("conflict")
            # Old issuance key must remain currently trusted, even on replay with
            # a freshly signed equivalent envelope. Never replace stored proof.
            validate_delivery(
                self._row_delivery(row), identity=identity, records=pins, trust=snapshot, now_ms=now
            )
            return view(row)
        instance, _, session, _, _ = locked
        row = RuntimeCommandAdmission(
            **machine_fields(instance),
            instance_id=instance.id,
            session_id=session.id,
            generation=instance.generation,
            command_id=command.command_id,
            replay_id=command.replay_id,
            recipient=identity.instance_id,
            command_bytes=canonical,
            signature_bytes=signature,
            command_digest=wire_digest(command),
            trust_revision=snapshot.revision,
            command_expires_at_ms=command.expires_at_ms,
            status="admitted",
            admitted_at_ms=now,
        )
        db.add(row)
        db.flush()
        return view(row)

    def poll(self, db: Session, principal: RuntimePrincipal) -> CommandDelivery | None:
        self.dependencies.limit("poll:" + principal.identity.instance_id, 60)
        locked = self._current(db, principal)
        instance = locked[0]
        # Filtering expired/closed deliveries does not disclose replay results.
        row = lock(
            machine_query(RuntimeCommandAdmission, instance)
            .where(
                RuntimeCommandAdmission.instance_id == instance.id,
                RuntimeCommandAdmission.session_id == principal.session_id,
                RuntimeCommandAdmission.generation == instance.generation,
                RuntimeCommandAdmission.status == "admitted",
                RuntimeCommandAdmission.command_expires_at_ms > locked[-1],
            )
            .order_by(RuntimeCommandAdmission.admitted_at_ms, RuntimeCommandAdmission.id)
            .limit(1),
            db,
        )
        if row is None:
            self._finish(db, locked)
            return None
        self._same_recipient(row, principal)
        delivery = self._row_delivery(row)
        self._authorize(db, principal, delivery, locked)
        return delivery

    def _status_row(self, db, principal, command_id):
        TypeAdapter(OpaqueId).validate_python(command_id, strict=True)
        locked = self._current(db, principal)
        row = lock(
            machine_query(RuntimeCommandAdmission, locked[0]).where(
                RuntimeCommandAdmission.instance_id == principal.identity.instance_id,
                RuntimeCommandAdmission.command_id == command_id,
            ),
            db,
        )
        if row is None:
            self._finish(db, locked)
            raise FleetError("not_found")
        self._same_recipient(row, principal)
        _, _, _, now = self._authorize(db, principal, self._row_delivery(row), locked)
        return row, now

    def claim(
        self,
        db: Session,
        principal: RuntimePrincipal,
        command_id: str,
        command_sha256: str,
        *,
        claim_hook=None,
    ) -> AdmissionView:
        """Claim once. `claim_hook(row, now)` (E05's action lease) runs in this transaction
        after every check; a command an execution action references is claimed only
        through it."""
        TypeAdapter(Digest).validate_python(command_sha256, strict=True)
        if claim_hook is None:
            # E05: an action command is never claimed through the generic route.
            # Authentication first (no replay query before it), then a plain read,
            # before the admission lock and any authority lookup.
            TypeAdapter(OpaqueId).validate_python(command_id, strict=True)
            self._current(db, principal)
            hint = db.scalar(
                select(RuntimeCommandAdmission).where(
                    RuntimeCommandAdmission.instance_id == principal.identity.instance_id,
                    RuntimeCommandAdmission.command_id == command_id,
                )
            )
            if hint is not None and _action_command(db, hint):
                raise FleetError("conflict")
        row, now = self._status_row(db, principal, command_id)
        if row.command_digest != command_sha256 or row.status != "admitted":
            raise FleetError("conflict")
        row.status, row.claimed_at_ms = "claimed", now
        db.flush()
        if claim_hook is not None:
            claim_hook(row, now)
        return view(row)

    def record_status(
        self,
        db: Session,
        principal: RuntimePrincipal,
        command_id: str,
        status: Literal["terminal", "uncertain"],
    ) -> AdmissionView:
        if type(status) is not str or status not in ("terminal", "uncertain"):
            raise FleetError("invalid_request")
        row, now = self._status_row(db, principal, command_id)
        if row.status not in ("claimed", status):
            raise FleetError("conflict")
        if row.status != status:
            row.status = status
            if status == "terminal":
                row.terminal_at_ms = now
                row.retain_until_ms = max(now + 86400000, row.command_expires_at_ms + 30000)
            db.flush()
        return view(row)


def _action_command(db, row) -> bool:
    """Whether an E05 action command references this admission."""
    from ..models import ExecutionActionCommand

    return (
        db.scalar(
            select(ExecutionActionCommand.id).where(
                ExecutionActionCommand.workspace_id == row.workspace_id,
                ExecutionActionCommand.project_id == row.project_id,
                ExecutionActionCommand.environment_id == row.environment_id,
                ExecutionActionCommand.admission_id == row.id,
            )
        )
        is not None
    )
