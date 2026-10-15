from dataclasses import replace

import pytest
from command_fixtures import command_case, delivery, records, snapshot

from lotuspond_runtime.errors import FleetError


def validate(case, **changes):
    from lotuspond_runtime.commands import validate_delivery

    args = dict(
        identity=case.identity, records=records(case), trust=snapshot(case), now_ms=case.now
    )
    args.update(changes)
    return validate_delivery(delivery(case), **args)


def test_valid_delivery_grants_no_effect_permission():
    case = command_case()
    assert validate(case) == case.command


@pytest.mark.parametrize(
    "field,value",
    [
        ("actual_input", b"changed"),
        ("actual_target", b"/other"),
        ("connection_version", True),
        ("actual_input", b"x" * 65537),
        ("secret_bindings", ("untrusted",)),
    ],
)
def test_actual_binding_matrix(field, value):
    case = command_case()
    with pytest.raises(FleetError):
        validate(case, records=replace(records(case), **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("installation_id", "other"),
        ("recovery_epoch", 8),
        ("generation", 2),
        ("instance_id", "00000000-0000-0000-0000-000000000001"),
    ],
)
def test_recipient_binding_matrix(field, value):
    case = command_case()
    with pytest.raises(FleetError):
        validate(case, identity=case.identity.model_copy(update={field: value}))


@pytest.mark.parametrize("field", ["workspace_id", "project_id", "environment_id"])
def test_every_scope_binding(field):
    case = command_case()
    scope = case.identity.scope.model_copy(update={field: "00000000-0000-0000-0000-000000000001"})
    with pytest.raises(FleetError):
        validate(case, identity=case.identity.model_copy(update={"scope": scope}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "other"),
        ("connector_name", "other"),
        ("connector_version", "2.0.0"),
        ("connector_artifact_sha256", "f" * 64),
        ("configuration_sha256", "f" * 64),
        ("policy_sha256", "f" * 64),
    ],
)
def test_intent_actual_fields(field, value):
    from lotuspond_sdk.executable.actions import build_action_intent

    case = command_case()
    intent = build_action_intent(case.intent.binding.model_copy(update={field: value}))
    # Re-sign a command matching the changed intent, keeping authoritative context/config.
    case.command = case.command.model_copy(update={"intent_sha256": intent.sha256})
    with pytest.raises(FleetError):
        validate(case, records=replace(records(case), intent=intent))


@pytest.mark.parametrize("now", [1499, 62000])
def test_future_expired_half_open(now):
    case = command_case()
    # Keep trust current so this specifically exercises command half-open time.
    current = replace(snapshot(case), valid_until_ms=now + 60000)
    with pytest.raises(FleetError, match="command_rejected"):
        validate(case, now_ms=now, trust=current)


def test_duplicate_keys_and_combined_envelope_bounds():
    from lotuspond_runtime.commands import validate_delivery
    from lotuspond_runtime.protocol import CommandDelivery

    case = command_case()
    for command in (b'{"command_id":"a","command_id":"b"}', b"x" * 65537):
        with pytest.raises(FleetError):
            validate_delivery(
                CommandDelivery(command, delivery(case).signature_bytes, 1),
                identity=case.identity,
                records=records(case),
                trust=snapshot(case),
                now_ms=case.now,
            )


def test_client_poll_claim_fixed_routes():
    import json

    from test_client import SESSION, TOKEN, RecordingTransport

    from lotuspond_runtime.client import RuntimeClient
    from lotuspond_runtime.commands import delivery_wire

    case = command_case()
    transport = RecordingTransport(json.loads(delivery_wire(delivery(case))))
    client = RuntimeClient(transport)
    assert client.poll(TOKEN, SESSION) == delivery(case)
    assert transport.calls[-1][1] == "/commands/poll"
    transport.response = {
        "command_id": case.command.command_id,
        "command_sha256": "a" * 64,
        "status": "claimed",
    }
    assert client.claim(TOKEN, SESSION, "command-1", "a" * 64).status == "claimed"
    assert transport.calls[-1][1] == "/commands/command-1/claim"
    for invalid in ("../sign", "a/b", "a?x", "a%2fb"):
        with pytest.raises(FleetError):
            client.claim(TOKEN, SESSION, invalid, "a" * 64)


def test_supervisor_verifies_before_claim_and_never_invokes():
    import threading
    from types import SimpleNamespace

    from test_client import SESSION, TOKEN
    from test_supervisor import Clock

    from lotuspond_runtime.supervisor import RuntimeSupervisor

    case = command_case()
    clock = Clock()
    clock.now = case.now
    calls = []

    class Client:
        def poll(self, *args):
            calls.append("poll")
            return delivery(case)

        def claim(self, *args):
            calls.append("claim")
            from lotuspond_runtime.protocol import AdmissionView

            return AdmissionView(
                command_id=case.command.command_id, command_sha256=args[-1], status="claimed"
            )

    class Records:
        def load(self, command):
            calls.append("records")
            return records(case)

    class Trust:
        def current(self, installation_id):
            calls.append("trust")
            return snapshot(case)

    supervisor = RuntimeSupervisor(
        Client(),
        None,
        __import__(
            "lotuspond_runtime.protocol", fromlist=["RuntimeObservation"]
        ).RuntimeObservation(platform="linux", connectors=()),
        installation_id=case.identity.installation_id,
        command_records=Records(),
        command_trust=Trust(),
        wall_ms=clock.wall,
        monotonic=clock.monotonic,
    )
    supervisor._session, supervisor._identity = SESSION, case.identity
    supervisor._server_anchor, supervisor._mono_anchor, supervisor._wall_anchor = (
        case.now,
        clock.monotonic(),
        case.now,
    )
    state = SimpleNamespace(active_token=TOKEN)
    stop = threading.Event()
    supervisor._commands(state, stop, case.now + 60000)
    assert calls == ["poll"]
    supervisor._commands(state, stop, case.now + 60000)
    assert calls == ["poll", "records", "trust", "claim"]
    assert supervisor.command_status == "claimed"
    assert not hasattr(supervisor, "invoke")


@pytest.mark.parametrize(
    "mode",
    [
        "malformed",
        "cross-context",
        "modified",
        "duplicate",
        "wrong-digest",
        "bad-signature",
        "untrusted",
        "revoked",
    ],
)
def test_signature_full_validator_matrix(mode):
    import json

    from lotuspond_sdk.trust import TrustSet

    from lotuspond_runtime.commands import validate_delivery

    case = command_case()
    original = delivery(case)
    sig = json.loads(original.signature_bytes)
    current = snapshot(case)
    if mode == "malformed":
        sig = {"unknown": True}
    elif mode == "cross-context":
        sig["context"] = "lotuspond-workflow-package-signature-v2"
    elif mode == "modified":
        original = replace(
            original, command_bytes=original.command_bytes.replace(b"command-1", b"command-2")
        )
    elif mode == "wrong-digest":
        sig["sha256"] = "f" * 64
    elif mode == "bad-signature":
        sig["signature"] = "0" * 128
    elif mode == "untrusted":
        current = replace(current, trust=TrustSet(format_version=1, keys=[]))
    elif mode == "revoked":
        current = replace(
            current,
            trust=TrustSet(
                format_version=1, keys=[case.trust.keys[0].model_copy(update={"state": "revoked"})]
            ),
        )
    encoded = json.dumps(sig).encode()
    if mode == "duplicate":
        encoded = encoded.replace(b"{", b'{"format_version":1,', 1)
    with pytest.raises(FleetError):
        validate_delivery(
            replace(original, signature_bytes=encoded),
            identity=case.identity,
            records=records(case),
            trust=current,
            now_ms=case.now,
        )


@pytest.mark.parametrize("lifetime,accepted", [(300000, True), (300001, False)])
def test_exact_maximum_command_lifetime(lifetime, accepted):

    from lotuspond_runtime.commands import validate_delivery

    case = command_case()
    command = case.command.model_copy(
        update={"issued_at_ms": case.now, "expires_at_ms": case.now + lifetime}
    )
    if accepted:
        result = validate_delivery(
            delivery(case, command),
            identity=case.identity,
            records=records(case),
            trust=snapshot(case),
            now_ms=case.now,
        )
        assert result == command
    else:
        # Deliberately bypass signing's own full validator to reach the boundary.
        import json

        with pytest.raises(FleetError):
            validate_delivery(
                replace(delivery(case), command_bytes=json.dumps(command.model_dump()).encode()),
                identity=case.identity,
                records=records(case),
                trust=snapshot(case),
                now_ms=case.now,
            )


@pytest.mark.parametrize(
    "change", ["missing", "denied", "service", "self", "changed-digest", "expired"]
)
def test_actual_decision_binding(change):
    from lotuspond_sdk.executable.actions import HumanDecision, build_action_intent, decision_digest

    case = command_case()
    intent = build_action_intent(
        case.intent.binding.model_copy(
            update={"approval_mode": "human", "independent_approval": True}
        )
    )
    decision = HumanDecision(
        decision_id="decision-1",
        intent_sha256=intent.sha256,
        decision="allow",
        actor_id="reviewer-1",
        actor_kind="human",
        policy_sha256=intent.binding.policy_sha256,
        issued_at_ms=case.now - 500,
        expires_at_ms=case.now + 60000,
    )
    case.command = case.command.model_copy(
        update={"intent_sha256": intent.sha256, "decision_sha256": decision_digest(decision)}
    )
    if change == "missing":
        decision = None
    elif change == "denied":
        decision = decision.model_copy(update={"decision": "deny"})
    elif change == "service":
        decision = decision.model_copy(update={"actor_kind": "service"})
    elif change == "self":
        decision = decision.model_copy(update={"actor_id": intent.binding.requester_id})
    elif change == "changed-digest":
        decision = decision.model_copy(update={"intent_sha256": "f" * 64})
    elif change == "expired":
        decision = decision.model_copy(update={"expires_at_ms": case.now})
    with pytest.raises(FleetError):
        validate(case, records=replace(records(case), intent=intent, decision=decision))


def test_new_trust_revision_keeps_currently_allowed_older_delivery():
    case = command_case()
    assert validate(case, trust=replace(snapshot(case), revision=2)) == case.command


def test_mutated_nested_records_are_revalidated():
    case = command_case()
    pinned = records(case)
    object.__setattr__(pinned.configuration.connector, "name", False)
    with pytest.raises(FleetError):
        validate(case, records=pinned)


@pytest.mark.parametrize("failure", ["input", "trust", "expired", "recipient"])
def test_supervisor_denies_before_claim(failure):
    import threading
    from types import SimpleNamespace

    from test_client import SESSION, TOKEN
    from test_supervisor import Clock

    from lotuspond_runtime.protocol import RuntimeObservation
    from lotuspond_runtime.supervisor import RuntimeSupervisor

    case = command_case()
    clock = Clock()
    clock.now = case.now
    calls = []

    class Client:
        def poll(self, *args):
            return delivery(case)

        def claim(self, *args):
            calls.append("claim")
            raise AssertionError("must deny before claim")

    class Records:
        def load(self, command):
            if failure == "expired":
                clock.now = case.command.expires_at_ms
            result = records(case)
            return replace(result, actual_input=b"changed") if failure == "input" else result

    class Trust:
        def current(self, installation_id):
            if failure == "trust":
                raise FleetError("trust_unavailable")
            return snapshot(case)

    runner = RuntimeSupervisor(
        Client(),
        None,
        RuntimeObservation(platform="linux", connectors=()),
        installation_id=case.identity.installation_id,
        command_records=Records(),
        command_trust=Trust(),
        wall_ms=clock.wall,
        monotonic=clock.monotonic,
    )
    runner._session, runner._identity = SESSION, case.identity
    if failure == "recipient":
        runner._identity = case.identity.model_copy(update={"generation": 2})
    runner._server_anchor, runner._mono_anchor, runner._wall_anchor = (
        case.now,
        clock.monotonic(),
        case.now,
    )
    for _ in range(2):
        runner._commands(SimpleNamespace(active_token=TOKEN), threading.Event(), case.now + 90000)
    assert calls == []
    assert runner.command_status == "denied"


def test_installed_command_verification(installed_runtime, tmp_path):
    from conftest import artifact_run

    case = command_case()
    payload = dict(
        identity=case.identity.model_dump(mode="json"),
        intent=case.intent.model_dump(mode="json"),
        context=case.context.model_dump(mode="json"),
        configuration=case.configuration.model_dump(mode="json"),
        connection_id=case.connection_id,
        command=delivery(case).command_bytes.decode(),
        signature=delivery(case).signature_bytes.decode(),
        trust=case.trust.model_dump(mode="json"),
    )
    script = (
        "payload="
        + repr(payload)
        + """\n
from dataclasses import replace
from pathlib import Path
import sys
from lotuspond_runtime import commands
from lotuspond_runtime.commands import validate_delivery
from lotuspond_runtime.protocol import (RuntimeIdentity, PinnedCommandRecords, CommandDelivery,
    CommandTrustSnapshot, ConnectionConfig)
from lotuspond_runtime.errors import FleetError
from lotuspond_sdk.executable.actions import ActionIntent,BindingContext
from lotuspond_sdk.trust import TrustSet
assert Path(commands.__file__).is_relative_to(Path(sys.prefix))
identity=RuntimeIdentity.model_validate(payload['identity'])
records=PinnedCommandRecords(ActionIntent.model_validate(payload['intent']),None,
    BindingContext.model_validate(payload['context']),payload['connection_id'],1,
    ConnectionConfig.model_validate(payload['configuration']),(),b'{}',b'/inspect')
delivery=CommandDelivery(payload['command'].encode(),payload['signature'].encode(),1)
trust=CommandTrustSnapshot(1,TrustSet.model_validate(payload['trust']),62000)
assert validate_delivery(delivery,identity=identity,records=records,trust=trust,now_ms=2000).command_id=='command-1'
for changed in (replace(records,actual_input=b'changed'),replace(records,actual_target=b'/other')):
    try:
        validate_delivery(delivery,identity=identity,records=changed,trust=trust,now_ms=2000)
    except FleetError:
        pass
    else:
        raise AssertionError('installed command admitted altered actual bytes')
"""
    )
    venv, env = installed_runtime
    artifact_run([str(venv / "bin/python"), "-I", "-c", script], tmp_path, env)
