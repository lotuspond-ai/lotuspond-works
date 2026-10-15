"""Synthetic independently supplied command records; never distributed fixture authority."""

import hashlib
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lotuspond_sdk.executable.actions import BindingContext, build_action_intent
from lotuspond_sdk.executable.signing import command_signature_envelope_json, sign_runtime_command
from lotuspond_sdk.executable.wire import RuntimeCommand, canonical_wire_json
from lotuspond_sdk.trust import TrustSet

from lotuspond_runtime.protocol import ConnectionConfig, ConnectorPin, RuntimeIdentity, Scope


def command_case(*, identity=None, now=2000, connection_id=None, configuration=None):
    identity = identity or RuntimeIdentity(
        scope=Scope(**{k: str(uuid4()) for k in ("workspace_id", "project_id", "environment_id")}),
        installation_id="test-installation",
        recovery_epoch=7,
        pool_id=str(uuid4()),
        instance_id=str(uuid4()),
        credential_version=1,
        generation=1,
    )
    configuration = configuration or ConnectionConfig(
        connector=ConnectorPin(name="lotuspond-fixture", version="1.0.0", artifact_sha256="a" * 64),
        operations=("inspect",),
        destinations=(),
        secrets=(),
        data_handling={},
    )
    common = dict(
        installation_id=identity.installation_id,
        recovery_epoch=identity.recovery_epoch,
        **identity.scope.model_dump(),
        execution_id="execution-1",
        action_id="action-1",
    )
    binding = dict(
        **common,
        step_id="dispatch",
        package_sha256="1" * 64,
        configuration_sha256="2" * 64,
        plan_sha256="3" * 64,
        policy_sha256="4" * 64,
        requester_id="requester-1",
        connector_name=configuration.connector.name,
        connector_version=configuration.connector.version,
        connector_artifact_sha256=configuration.connector.artifact_sha256,
        operation="inspect",
        target_sha256=hashlib.sha256(b"/inspect").hexdigest(),
        input_sha256=hashlib.sha256(b"{}").hexdigest(),
        approval_mode="none",
        independent_approval=False,
        issued_at_ms=now - 1000,
        expires_at_ms=now + 600000,
    )
    intent = build_action_intent(binding)
    ctx = BindingContext(
        **{k: binding[k] for k in BindingContext.model_fields if k in binding},
        worker_id=identity.instance_id,
        fencing_generation=identity.generation,
        now_ms=now,
    )
    command = RuntimeCommand(
        **common,
        command_id="command-1",
        replay_id="replay-1",
        intent_sha256=intent.sha256,
        worker_id=identity.instance_id,
        fencing_generation=identity.generation,
        issued_at_ms=now - 500,
        expires_at_ms=now + 60000,
    )
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    trust = TrustSet(
        format_version=1,
        keys=[
            dict(
                key_id=hashlib.sha256(public).hexdigest(), public_key=public.hex(), state="allowed"
            )
        ],
    )
    return SimpleNamespace(
        identity=identity,
        configuration=configuration,
        intent=intent,
        context=ctx,
        command=command,
        key=key,
        trust=trust,
        now=now,
        connection_id=connection_id or str(uuid4()),
        connection_version=1,
        actual_input=b"{}",
        actual_target=b"/inspect",
        secret_bindings=(),
    )


def records(case):
    from lotuspond_runtime.protocol import PinnedCommandRecords

    return PinnedCommandRecords(
        case.intent,
        None,
        case.context,
        case.connection_id,
        case.connection_version,
        case.configuration,
        case.secret_bindings,
        case.actual_input,
        case.actual_target,
    )


def delivery(case, command=None):
    from lotuspond_runtime.protocol import CommandDelivery

    cmd = command or case.command
    return CommandDelivery(
        canonical_wire_json(cmd),
        command_signature_envelope_json(sign_runtime_command(cmd, case.key)),
        1,
    )


def snapshot(case):
    from lotuspond_runtime.protocol import CommandTrustSnapshot

    return CommandTrustSnapshot(1, case.trust, case.now + 60000)
