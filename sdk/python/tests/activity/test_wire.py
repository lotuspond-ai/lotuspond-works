"""Exact activity boundaries, including raw transport and mutated models."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lotuspond_sdk.activity import (
    ActivityDelivery,
    ActivityEnvelope,
    ArtifactHandle,
    UsageObservation,
    activity_digest,
    canonical_activity_json,
    decode_activity_delivery,
    validate_activity,
)
from lotuspond_sdk.executable import ExecutableError

HERE = Path(__file__).with_name("fixtures")
SAFE = 9007199254740991


def read_fixture():
    return json.loads((HERE / "read.json").read_bytes())


def delivery(envelope=None, signature=None):
    return json.dumps({
        "envelope": envelope if envelope is not None else (HERE / "read.json").read_text(),
        "signature": signature if signature is not None else (HERE / "read.signature.json").read_text(),
    }).encode()


def test_canonical_literal_and_complete_required_shape():
    raw = read_fixture()
    canonical = canonical_activity_json(raw)
    assert len(canonical) == 1375
    assert canonical == (HERE / "read.canonical.json").read_bytes()
    assert activity_digest(raw) == "06abd378a53efb058bf9b880d97f1952d23db32a50b8ffd15d8a658fc06e2203"
    assert validate_activity(raw).model_dump() == raw
    for name in raw:
        missing = dict(raw)
        del missing[name]
        with pytest.raises(ExecutableError):
            validate_activity(missing)
    with pytest.raises(ExecutableError):
        validate_activity({**raw, "authority_granted": False})


def test_combined_limit_and_lifetime():
    raw = (HERE / "read.canonical.json").read_text()
    signature = (HERE / "read.signature.json").read_text()
    padded = raw + " " * (65536 - len(raw.encode()) - len(signature.encode()))
    envelope, detached = decode_activity_delivery(delivery(padded, signature))
    assert envelope.expires_at_ms - envelope.issued_at_ms == 300000
    assert detached.context == "lotuspond-runtime-activity-signature-v1"
    with pytest.raises(ExecutableError):
        decode_activity_delivery(delivery(padded + " ", signature))
    outer = delivery()
    decode_activity_delivery(outer + b" " * (131072 - len(outer)))
    with pytest.raises(ExecutableError):
        decode_activity_delivery(outer + b" " * (131073 - len(outer)))
    for delta in (0, -1, 300001):
        bad = read_fixture()
        bad["expires_at_ms"] = bad["issued_at_ms"] + delta
        with pytest.raises(ExecutableError):
            validate_activity(bad)


def test_mutated_model_and_scope_fields():
    raw = read_fixture()
    model = validate_activity(raw)
    with pytest.raises(ValidationError):
        model.generation = 2
    for name in ("generation", "issued_at_ms", "recovery_epoch"):
        mutated = validate_activity(raw)
        object.__setattr__(mutated, name, True)
        for helper in (validate_activity, canonical_activity_json, activity_digest):
            with pytest.raises(ExecutableError):
                helper(mutated)
    mutated = validate_activity(raw)
    object.__setattr__(mutated.input, "version", True)
    with pytest.raises(ExecutableError):
        validate_activity(mutated)
    constructed = ActivityEnvelope.model_construct(**raw)
    assert canonical_activity_json(constructed) == canonical_activity_json(raw)
    object.__setattr__(constructed, "extra", "private")
    with pytest.raises(ExecutableError):
        validate_activity(constructed)
    for name in ("workspace_id", "project_id", "environment_id", "execution_id",
                 "step_instance_id", "attempt_id", "deployment_revision_id", "pool_id",
                 "instance_id", "delivery_id", "replay_id"):
        for invalid in ("opaque-id", raw[name].upper().replace("0000", "ABCD", 1), "", None):
            with pytest.raises(ExecutableError):
                validate_activity({**raw, name: invalid})


@pytest.mark.parametrize("field", ["issued_at_ms", "expires_at_ms", "generation", "recovery_epoch"])
@pytest.mark.parametrize("value", [True, 1.0, "1", -1, SAFE + 1])
def test_exact_nonnegative_safe_integers(field, value):
    with pytest.raises(ExecutableError):
        validate_activity({**read_fixture(), field: value})


def test_maximum_identifiers_and_model_operation_matrix():
    raw = read_fixture()
    raw.update(installation_id="A" * 128, generation=SAFE, recovery_epoch=SAFE,
               issued_at_ms=SAFE - 1, expires_at_ms=SAFE, operation="a" * 64)
    raw["input"]["version"] = 2**31 - 1
    raw["dependency"].update(name="a" * 64, version="999999.999999.999999")
    validate_activity(raw)
    for update in ({"installation_id": "a" * 129}, {"installation_id": "é"},
                   {"generation": 0}, {"operation": "a" * 65}):
        with pytest.raises(ExecutableError):
            validate_activity({**raw, **update})
    for version in (0, 2**31, True, 1.0):
        bad = copy.deepcopy(raw)
        bad["input"]["version"] = version
        with pytest.raises(ExecutableError):
            validate_activity(bad)
    for kind in ("connector_read", "model"):
        for dependency in ("connector", "model", "agent"):
            for operation in ("inspect", None):
                candidate = copy.deepcopy(raw)
                candidate.update(kind=kind, operation=operation)
                candidate["dependency"]["kind"] = dependency
                valid = (kind, dependency, operation) in (
                    ("connector_read", "connector", "inspect"), ("model", "model", None)
                )
                if valid:
                    validate_activity(candidate)
                else:
                    with pytest.raises(ExecutableError):
                        validate_activity(candidate)


@pytest.mark.parametrize("content", [
    b'{"envelope":"{}","envelope":"{}","signature":"{}"}',
    b'{"envelope":"\\ud800","signature":"{}"}',
    b'{"envelope":NaN,"signature":"{}"}',
    b'{"envelope":Infinity,"signature":"{}"}',
    b'\xff',
    b'{}' + b'[]',
    b'[' * 33 + b'0' + b']' * 33,
    b'[' + b'0,' * 10000 + b'0]',
])
def test_untrusted_transport_rejects_invalid_json_domains(content):
    with pytest.raises(ValueError):
        decode_activity_delivery(content)


def test_inner_json_duplicate_unicode_nonfinite_and_shape():
    for inner in ('{"x":1,"x":2}', '{"x":"\\ud800"}', '{"x":NaN}',
                  '[' * 33 + '0' + ']' * 33, '{', '[]'):
        for field in ("envelope", "signature"):
            with pytest.raises(ValueError):
                decode_activity_delivery(delivery(**{field: inner}))
    for raw in ({"envelope": {}, "signature": "{}"},
                {"envelope": "{}", "signature": "{}", "key": "forged"}):
        with pytest.raises(ValueError):
            decode_activity_delivery(json.dumps(raw).encode())
    with pytest.raises(ValueError):
        decode_activity_delivery("{}")
    for value in (float("nan"), float("inf"), "\udfff"):
        with pytest.raises(ExecutableError):
            validate_activity({**read_fixture(), "operation": value})


def test_unknown_usage_is_distinct_from_zero_and_frozen():
    unknown = UsageObservation(input_tokens=None, output_tokens=None,
                               cost_microusd=None, completed_at_ms=SAFE)
    zero = UsageObservation(input_tokens=0, output_tokens=0, cost_microusd=0, completed_at_ms=0)
    assert unknown.input_tokens is None and zero.input_tokens == 0
    with pytest.raises(ValidationError):
        unknown.input_tokens = 0
    for name in ("input_tokens", "output_tokens", "cost_microusd", "completed_at_ms"):
        for invalid in (True, 1.0, -1, SAFE + 1):
            with pytest.raises(ValidationError):
                UsageObservation(**{**zero.model_dump(), name: invalid})
    with pytest.raises(ValidationError):
        UsageObservation(completed_at_ms=1)
    with pytest.raises(ValidationError):
        ArtifactHandle(id=read_fixture()["input"]["id"], version=True, sha256="a" * 64)
    with pytest.raises(ValidationError):
        ActivityDelivery(envelope={}, signature="{}")
