"""Independent signed admission verification. This module grants no business effects."""

import hashlib
import json
from dataclasses import replace

from lotuspond_sdk.executable.actions import (
    check_command_binding,
    validate_action_intent,
    validate_binding_context,
    validate_human_decision,
)
from lotuspond_sdk.executable.signing import (
    command_signature_envelope_json,
    validate_command_signature_envelope,
    verify_runtime_command,
)
from lotuspond_sdk.executable.wire import canonical_wire_json, validate_runtime_command
from pydantic import TypeAdapter

from .canonical import canonical_connection_json, decode_fleet_json
from .errors import FleetError
from .protocol import (
    CanonicalUUID,
    CommandDelivery,
    CommandDeliveryWire,
    ConnectionConfig,
    OpaqueId,
    PinnedCommandRecords,
    ResolvedSecretBinding,
    RuntimeIdentity,
    SafeInt,
    SecretPin,
)
from .trust import validate_snapshot


def validate_records(raw: PinnedCommandRecords) -> PinnedCommandRecords:
    try:
        if type(raw) is not PinnedCommandRecords:
            raise ValueError
        TypeAdapter(CanonicalUUID).validate_python(raw.connection_id, strict=True)
        if type(raw.connection_version) is not int or not 1 <= raw.connection_version <= 2147483647:
            raise ValueError
        if any(type(v) is not bytes for v in (raw.actual_input, raw.actual_target)):
            raise ValueError
        if any(len(v) > 65536 for v in (raw.actual_input, raw.actual_target)):
            raise FleetError("input_limit")
        configuration = ConnectionConfig.model_validate(raw.configuration)
        canonical_connection_json(configuration)
        if type(raw.secret_bindings) is not tuple or len(raw.secret_bindings) > 16:
            raise ValueError
        pins = []
        for item in raw.secret_bindings:
            if type(item) is not ResolvedSecretBinding:
                raise ValueError
            TypeAdapter(OpaqueId).validate_python(item.binding_id, strict=True)
            pins.append(SecretPin(reference_id=item.reference_id, version=item.version))
        if len(set(pins)) != len(pins) or set(pins) != set(configuration.secrets):
            raise ValueError
        return replace(
            raw,
            intent=validate_action_intent(raw.intent),
            decision=None if raw.decision is None else validate_human_decision(raw.decision),
            context=validate_binding_context(raw.context),
            configuration=configuration,
        )
    except FleetError:
        raise
    except Exception:
        raise FleetError("command_rejected") from None


def delivery_wire(delivery: CommandDelivery) -> bytes:
    """Bound combined escaped wire bytes before parsing embedded SDK records."""
    if type(delivery) is not CommandDelivery:
        raise FleetError("command_rejected")
    if any(type(v) is not bytes for v in (delivery.command_bytes, delivery.signature_bytes)):
        raise FleetError("command_rejected")
    if any(not 0 < len(v) <= 65536 for v in (delivery.command_bytes, delivery.signature_bytes)):
        raise FleetError("input_limit")
    try:
        # Bound the raw escaped command before decoding either nested record.
        command = delivery.command_bytes.decode("utf-8")
        escaped = json.dumps(command, ensure_ascii=False).encode("utf-8")
        if len(escaped) + len(delivery.signature_bytes) + 64 > 65536:
            raise FleetError("input_limit")
        envelope = validate_command_signature_envelope(decode_fleet_json(delivery.signature_bytes))
        wire = CommandDeliveryWire(
            command=command,
            signature=json.loads(command_signature_envelope_json(envelope)),
            trust_revision=delivery.trust_revision,
        )
        result = json.dumps(wire.model_dump(), ensure_ascii=False, separators=(",", ":")).encode()
        if len(result) > 65536:
            raise FleetError("input_limit")
        return result
    except FleetError:
        raise
    except Exception:
        raise FleetError("command_rejected") from None


def decode_delivery(content: bytes) -> CommandDelivery | None:
    raw = decode_fleet_json(content)
    if raw is None:
        return None
    value = CommandDeliveryWire.model_validate(raw)
    result = CommandDelivery(
        value.command.encode("utf-8"),
        command_signature_envelope_json(validate_command_signature_envelope(value.signature)),
        value.trust_revision,
    )
    delivery_wire(result)
    return result


def validate_delivery(
    delivery: CommandDelivery,
    *,
    identity: RuntimeIdentity,
    records: PinnedCommandRecords,
    trust,
    now_ms: int,
):
    try:
        delivery_wire(delivery)
        now = TypeAdapter(SafeInt).validate_python(now_ms, strict=True)
        identity = RuntimeIdentity.model_validate(identity)
        records = validate_records(records)
        snapshot = validate_snapshot(trust, now_ms=now)
        # A newer current revision may retain an older issuance key during explicit
        # overlap. An older revision cannot validate a newer issuance snapshot.
        if snapshot.revision < delivery.trust_revision:
            raise FleetError("trust_unavailable")
        command = validate_runtime_command(decode_fleet_json(delivery.command_bytes))
        if canonical_wire_json(command) != delivery.command_bytes:
            raise FleetError("command_rejected")
        signature = validate_command_signature_envelope(decode_fleet_json(delivery.signature_bytes))
        if not verify_runtime_command(command, signature, snapshot.trust).valid:
            raise FleetError("command_rejected")
        expected = records.context
        if (
            expected.installation_id != identity.installation_id
            or expected.recovery_epoch != identity.recovery_epoch
            or expected.worker_id != identity.instance_id
            or expected.fencing_generation != identity.generation
            or any(
                getattr(expected, name) != getattr(identity.scope, name)
                for name in ("workspace_id", "project_id", "environment_id")
            )
        ):
            raise FleetError("command_rejected")
        # Source context supplies deployment pins, but never supplies current time.
        expected = validate_binding_context(expected.model_copy(update={"now_ms": now}))
        if not check_command_binding(command, records.intent, records.decision, expected).valid:
            raise FleetError("command_rejected")
        binding, config = records.intent.binding, records.configuration
        if (
            binding.operation not in config.operations
            or binding.connector_name != config.connector.name
            or binding.connector_version != config.connector.version
            or binding.connector_artifact_sha256 != config.connector.artifact_sha256
            or binding.input_sha256 != hashlib.sha256(records.actual_input).hexdigest()
            or binding.target_sha256 != hashlib.sha256(records.actual_target).hexdigest()
        ):
            raise FleetError("command_rejected")
        return command
    except FleetError:
        raise
    except Exception:
        raise FleetError("command_rejected") from None


class DenyRuntimeRecordSource:
    def load(self, command):
        raise FleetError("authority_unavailable")


class _RecordLookup:
    """One bounded external record+trust lookup; the supervisor retains control.

    No DB session, runtime credential, or session proof enters the helper. A
    late response is discarded, and a stuck lookup occupies its slot forever.
    """

    def __init__(self):
        from threading import Lock

        self._lock = Lock()

    def load(self, source, authority, command, installation_id):
        from threading import Event, Thread

        if not self._lock.acquire(blocking=False):
            raise FleetError("authority_unavailable")
        done, result = Event(), []

        def run():
            try:
                records = validate_records(source.load(command))
                trust = authority.current(installation_id)
                result.append((records, trust))
            except Exception:
                pass
            finally:
                done.set()
                self._lock.release()

        try:
            Thread(target=run, name="runtime-command-records", daemon=True).start()
        except Exception:
            self._lock.release()
            raise FleetError("authority_unavailable") from None
        if not done.wait(5) or not result:
            raise FleetError("authority_unavailable")
        return result[0]
