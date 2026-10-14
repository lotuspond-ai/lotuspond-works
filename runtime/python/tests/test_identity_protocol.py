"""Identity additions retain strict, pure and secret-safe wire semantics."""

import pytest
from pydantic import SecretStr

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    EnrollmentIssued,
    EnrollmentList,
    EnrollmentView,
    InstanceList,
    InstanceView,
    encode_enrollment_issued,
)

UUID = "01234567-89ab-cdef-0123-456789abcdef"
SCOPE = {"workspace_id": UUID, "project_id": UUID, "environment_id": UUID}


def test_enrollment_one_time_encoder_revalidates_and_hides_secret():
    result = EnrollmentIssued(
        enrollment_id=UUID, token=SecretStr("synthetic-private"), expires_at_ms=600000
    )
    assert "synthetic-private" not in repr(result)
    assert "synthetic-private" not in result.model_dump_json()
    assert encode_enrollment_issued(result) == {
        "enrollment_id": UUID,
        "token": "synthetic-private",
        "expires_at_ms": 600000,
    }
    object.__setattr__(result, "expires_at_ms", True)
    with pytest.raises(FleetError, match="invalid_request"):
        encode_enrollment_issued(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("expires_at_ms", True),
        ("expires_at_ms", 1.0),
        ("enrollment_id", UUID.upper()),
        ("token", b"private"),
        ("unknown", "value"),
    ],
)
def test_enrollment_issuance_strict_fields(field, value):
    raw = {"enrollment_id": UUID, "token": "private", "expires_at_ms": 1}
    raw[field] = value
    with pytest.raises(FleetError, match="invalid_request"):
        EnrollmentIssued.model_validate(raw)


def test_inventory_has_explicit_safe_fields_and_bounded_collections():
    enrollment = EnrollmentView(
        id=UUID,
        scope=SCOPE,
        pool_id=UUID,
        status="issued",
        policy_version=1,
        expires_at_ms=600000,
        consumed_at_ms=None,
        created_at_ms=0,
    )
    instance = InstanceView(
        id=UUID,
        scope=SCOPE,
        pool_id=UUID,
        status="active",
        installation_id="test",
        recovery_epoch=7,
        generation=0,
        current_policy_version=1,
        last_seen_at_ms=0,
        created_at_ms=0,
    )
    for model, item in [(EnrollmentList, enrollment), (InstanceList, instance)]:
        assert len(model(items=[item] * 100, next_after_id=None).items) == 100
        with pytest.raises(FleetError, match="invalid_request"):
            model(items=[item] * 101, next_after_id=None)
        raw = item.model_dump()
        raw["token_digest"] = "a" * 64
        with pytest.raises(FleetError, match="invalid_request"):
            type(item).model_validate(raw)
