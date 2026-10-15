"""Strict synthetic events; declarations never certify effects or provenance."""

import copy
import hashlib
import json

import pytest

from lotuspond_sdk.executable import ExecutableError
from lotuspond_sdk.executable.wire import (
    RuntimeEvent,
    canonical_wire_json,
    validate_wire,
    wire_digest,
)


def payload_hash(payload):
    # These fixtures are ASCII, exact integers, and use no JCS-sensitive numbers.
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def event(kind, payload, *, action=False, step=False):
    return {
        "record_type": "runtime_event",
        "format_version": 1,
        "event_id": "event-1",
        "installation_id": "installation-1",
        "recovery_epoch": 2,
        "workspace_id": "workspace-1",
        "project_id": "project-1",
        "environment_id": "environment-1",
        "execution_id": "execution-1",
        "step_id": "dispatch" if step or action else None,
        "action_id": "action-1" if action else None,
        "attempt": 0 if step or action else None,
        "source_id": "worker-1",
        "source_generation": 3,
        "source_sequence": 0,
        "event_kind": kind,
        "observed_at_ms": 2000,
        "payload": payload,
        "payload_sha256": payload_hash(payload),
    }


@pytest.fixture
def receipt():
    return {
        "record_type": "action_receipt",
        "format_version": 1,
        "receipt_id": "receipt-1",
        "action_id": "action-1",
        "intent_sha256": "9" * 64,
        "attempt": 0,
        "target_operation_ref": "operation:synthetic-1",
        "status": "accepted",
        "evidence_refs": [],
    }


@pytest.fixture
def outcome(evidence_ref):
    return {
        "record_type": "outcome_record",
        "format_version": 1,
        "outcome_id": "outcome-1",
        "action_id": "action-1",
        "intent_sha256": "9" * 64,
        "receipt_id": "receipt-1",
        "state": "verified",
        "rule_id": "readback",
        "rule_version": "1.0.0",
        "evidence_refs": [evidence_ref],
        "observed_at_ms": 2000,
    }


@pytest.fixture
def all_events(action_binding, human_decision, runtime_command, receipt, outcome):
    from lotuspond_sdk.executable.actions import build_action_intent

    return [
        event(
            "admission",
            {
                "package_sha256": "1" * 64,
                "configuration_sha256": "2" * 64,
                "plan_sha256": "3" * 64,
                "recovery_epoch": 2,
            },
        ),
        event("step_started", {"input_sha256": "6" * 64}, step=True),
        event("step_result", {"output_sha256": "8" * 64}, step=True),
        event("step_failed", {"code": "invalid_output"}, step=True),
        event(
            "wait_started",
            {
                "wait_id": "wait-1",
                "kind": "human",
                "correlation_sha256": "8" * 64,
                "not_before_ms": 1000,
                "expires_at_ms": 3000,
            },
            step=True,
        ),
        event(
            "signal_received",
            {"wait_id": "wait-1", "correlation_sha256": "8" * 64, "signal_sha256": "9" * 64},
            step=True,
        ),
        event("timer_elapsed", {"wait_id": "wait-1"}, step=True),
        event(
            "intent_created",
            {"intent": build_action_intent(action_binding).model_dump()},
            action=True,
        ),
        event("decision_recorded", {"decision": human_decision}, action=True),
        event("action_dispatched", {"command": runtime_command}, action=True),
        event("action_receipt", {"receipt": receipt}, action=True),
        event(
            "dispatch_timeout",
            {"intent_sha256": "9" * 64, "may_have_dispatched": True},
            action=True,
        ),
        event(
            "reconciliation_observed",
            {
                "intent_sha256": "9" * 64,
                "observation": "unknown",
                "rule_id": "readback",
                "rule_version": "1.0.0",
                "evidence_refs": [],
                "qualified_absence": False,
            },
            action=True,
        ),
        event("outcome_recorded", {"outcome": outcome}, action=True),
        event("execution_completed", {"output_sha256": "8" * 64}),
        event("cancellation_requested", {"reason_code": "owner_requested"}),
        event("execution_failed", {"code": "unknown"}),
    ]


def test_every_event_variant_roundtrips(all_events):
    for raw in all_events:
        validated = validate_wire(raw)
        assert isinstance(validated, RuntimeEvent)
        canonical = canonical_wire_json(validated)
        assert wire_digest(json.loads(canonical)) == hashlib.sha256(canonical).hexdigest()
        assert validated.payload_sha256 == raw["payload_sha256"]


def test_payloads_are_closed_kind_specific_and_digest_checked(all_events):
    for raw in all_events:
        for mutate in (
            lambda r: r["payload"].update(private="private sentinel"),
            lambda r: r.update(payload_sha256="f" * 64),
            lambda r: r.update(payload={"arbitrary": "private sentinel"}),
        ):
            changed = copy.deepcopy(raw)
            mutate(changed)
            with pytest.raises(ExecutableError) as error:
                validate_wire(changed)
            assert error.value.code == "invalid_wire"
            assert "private sentinel" not in str(error.value)


def test_variant_reference_requirements(all_events):
    for raw in all_events:
        for field in ("step_id", "action_id", "attempt"):
            changed = copy.deepcopy(raw)
            changed[field] = (
                None if raw[field] is not None else (0 if field == "attempt" else "other")
            )
            with pytest.raises(ExecutableError) as error:
                validate_wire(changed)
            assert error.value.code == "invalid_wire"


def test_nested_intent_and_event_scope_revalidated(all_events):
    raw = next(r for r in all_events if r["event_kind"] == "intent_created")
    raw["payload"]["intent"]["binding"]["input_sha256"] = "f" * 64
    raw["payload_sha256"] = payload_hash(raw["payload"])
    with pytest.raises(ExecutableError):
        validate_wire(raw)
    dispatched = next(r for r in all_events if r["event_kind"] == "action_dispatched")
    dispatched["workspace_id"] = "other"
    with pytest.raises(ExecutableError):
        validate_wire(dispatched)


def test_outcome_evidence_and_receipt_acceptance(receipt, outcome):
    assert validate_wire(receipt).status == "accepted"
    assert validate_wire(outcome).state == "verified"
    for field, value in (("evidence_refs", []), ("rule_id", ""), ("rule_version", "")):
        raw = {**outcome, field: value}
        with pytest.raises(ExecutableError):
            validate_wire(raw)
    outcome["evidence_refs"][0]["source_kind"] = "model_claim"
    with pytest.raises(ExecutableError):
        validate_wire(outcome)


def test_wire_lifetime_and_integer_guards(runtime_command, all_events):
    for field, value in (
        ("expires_at_ms", 301801),
        ("expires_at_ms", 1800),
        ("issued_at_ms", True),
        ("fencing_generation", 3.0),
        ("format_version", True),
    ):
        with pytest.raises(ExecutableError) as error:
            validate_wire({**runtime_command, field: value})
        assert error.value.code == "invalid_wire"
    raw = all_events[0]
    for field in (
        "attempt",
        "source_sequence",
        "source_generation",
        "recovery_epoch",
        "observed_at_ms",
    ):
        with pytest.raises(ExecutableError):
            validate_wire({**raw, field: True})


def test_mutated_event_is_not_trusted(all_events):
    raw = next(r for r in all_events if r["event_kind"] == "dispatch_timeout")
    model = validate_wire(raw)
    model.payload.may_have_dispatched = 1
    with pytest.raises(ExecutableError):
        canonical_wire_json(model)
    model = RuntimeEvent.model_construct(**raw)
    model.payload_sha256 = "f" * 64
    with pytest.raises(ExecutableError):
        wire_digest(model)


def test_wire_collection_and_byte_caps(outcome, evidence_ref):
    with pytest.raises(ExecutableError) as error:
        validate_wire({**outcome, "evidence_refs": [evidence_ref] * 65})
    assert error.value.code == "invalid_wire"
    large = {**evidence_ref, "artifact_id": "a" * 128, "media_type": "\x00" * 128}
    with pytest.raises(ExecutableError) as error:
        validate_wire({**outcome, "evidence_refs": [large] * 64})
    assert error.value.code == "input_too_large"
    with pytest.raises(ExecutableError) as error:
        validate_wire({"record_type": "runtime_event", "payload": [0] * 10001})
    assert error.value.code == "input_too_complex"


def test_public_payload_helpers_revalidate_and_normalize(all_events):
    from lotuspond_sdk.executable.wire import canonical_event_payload_json, event_payload_digest

    for raw in all_events:
        content = canonical_event_payload_json(raw["event_kind"], raw["payload"])
        assert hashlib.sha256(content).hexdigest() == raw["payload_sha256"]
        assert event_payload_digest(raw["event_kind"], raw["payload"]) == raw["payload_sha256"]
    raw = next(r for r in all_events if r["event_kind"] == "action_dispatched")
    raw["payload"]["command"]["decision_sha256"] = None
    normalized = event_payload_digest(raw["event_kind"], raw["payload"])
    del raw["payload"]["command"]["decision_sha256"]
    assert event_payload_digest(raw["event_kind"], raw["payload"]) == normalized
    raw = next(r for r in all_events if r["event_kind"] == "intent_created")
    raw["payload"]["intent"]["sha256"] = "f" * 64
    with pytest.raises(ExecutableError) as error:
        event_payload_digest(raw["event_kind"], raw["payload"])
    assert error.value.code == "invalid_wire"
    with pytest.raises(ExecutableError):
        event_payload_digest("private sentinel", {})


def test_nested_records_are_checked_even_if_payload_hash_matches(all_events):
    for raw in all_events:
        changes = {
            "admission": ("recovery_epoch", 99),
            "wait_started": ("expires_at_ms", 1000),
            "reconciliation_observed": ("qualified_absence", True),
        }
        if raw["event_kind"] in changes:
            field, value = changes[raw["event_kind"]]
            raw["payload"][field] = value
            raw["payload_sha256"] = payload_hash(raw["payload"])
            with pytest.raises(ExecutableError) as error:
                validate_wire(raw)
            assert error.value.code == "invalid_wire"
    for kind, wrapper, field, value in (
        ("decision_recorded", "decision", "expires_at_ms", 1500),
        ("action_dispatched", "command", "expires_at_ms", 999999),
        ("action_receipt", "receipt", "action_id", "other"),
        ("action_receipt", "receipt", "attempt", 1),
        ("outcome_recorded", "outcome", "evidence_refs", []),
    ):
        raw = copy.deepcopy(next(r for r in all_events if r["event_kind"] == kind))
        raw["payload"][wrapper][field] = value
        raw["payload_sha256"] = payload_hash(raw["payload"])
        with pytest.raises(ExecutableError) as error:
            validate_wire(raw)
        assert error.value.code == "invalid_wire"


def test_full_wire_digest_covers_delivery_fields(runtime_command):
    original = wire_digest(runtime_command)
    for field in ("command_id", "replay_id"):
        assert wire_digest({**runtime_command, field: "other"}) != original
    runtime_command["expires_at_ms"] = runtime_command["issued_at_ms"] + 300000
    assert validate_wire(runtime_command)


def test_depth_cycle_unicode_and_safe_integer_bounds(all_events):
    nested = []
    for _ in range(33):
        nested = [nested]
    cyclic = []
    cyclic.append(cyclic)
    for value, code in (
        (nested, "input_too_deep"),
        (cyclic, "cyclic_input"),
        ({"private": "\ud800"}, "invalid_unicode"),
    ):
        with pytest.raises(ExecutableError) as error:
            validate_wire(value)
        assert error.value.code == code
    for value in (2**53, -1, 1.0, True):
        with pytest.raises(ExecutableError) as error:
            validate_wire({**all_events[0], "source_sequence": value})
        assert error.value.code == "invalid_wire"


def test_wire_exact_normalized_byte_cap(outcome, evidence_ref):
    # Each escaped NUL adds five bytes versus ASCII; an escaped quote adds one.
    refs = [{**evidence_ref, "artifact_id": "a" * 128, "media_type": "a" * 128} for _ in range(64)]
    raw = {**outcome, "evidence_refs": refs}
    baseline = len(canonical_wire_json(raw))
    nul_count, quote_count = divmod(65536 - baseline, 5)
    chars = ["\x00"] * nul_count + ['"'] * quote_count
    chars += ["a"] * (64 * 128 - len(chars))
    for index, ref in enumerate(refs):
        ref["media_type"] = "".join(chars[index * 128 : (index + 1) * 128])
    assert len(canonical_wire_json(raw)) == 65536
    refs[-1]["media_type"] = refs[-1]["media_type"][:-1] + '"'
    with pytest.raises(ExecutableError) as error:
        canonical_wire_json(raw)
    assert error.value.code == "input_too_large"


def test_receipt_uncertainty_and_qualified_absence_are_declarations(receipt, evidence_ref):
    from lotuspond_sdk.executable.wire import event_payload_digest

    for status in ("accepted", "observed_effect", "unknown"):
        assert validate_wire({**receipt, "status": status}).status == status
    raw = {
        "intent_sha256": "9" * 64,
        "observation": "effect_absent",
        "rule_id": "readback",
        "rule_version": "v1",
        "evidence_refs": [evidence_ref],
        "qualified_absence": True,
    }
    assert event_payload_digest("reconciliation_observed", raw)
    raw["observation"] = "effect_present"
    with pytest.raises(ExecutableError):
        event_payload_digest("reconciliation_observed", raw)
