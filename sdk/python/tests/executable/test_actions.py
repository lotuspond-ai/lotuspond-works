"""Synthetic binding contracts, including independently frozen intent bytes."""

import copy
import hashlib
from pathlib import Path

import pytest

from lotuspond_sdk.executable import ExecutableError
from lotuspond_sdk.executable.actions import (
    ActionBinding,
    ActionIntent,
    BindingReport,
    build_action_intent,
    canonical_binding_json,
    check_command_binding,
    check_decision,
    decision_digest,
    intent_digest,
    validate_binding_context,
    validate_binding_report,
)

ORACLE = Path(__file__).parent.parent / "fixtures/executable-v2/action-binding.jcs"
EXPECTED_HASH = "e3860ea56b81199fb42870a5ee4e17bc0d4c90e54026fe2d36e21debd5cc7c7c"


def test_literal_intent_oracle(action_binding):
    expected = ORACLE.read_bytes()
    assert hashlib.sha256(expected).hexdigest() == EXPECTED_HASH
    assert canonical_binding_json(action_binding) == expected
    intent = build_action_intent(action_binding)
    assert intent.sha256 == EXPECTED_HASH == intent_digest(intent)
    assert intent_digest(intent.model_dump()) == EXPECTED_HASH


@pytest.mark.parametrize(
    "field",
    [
        "installation_id",
        "recovery_epoch",
        "workspace_id",
        "project_id",
        "environment_id",
        "execution_id",
        "step_id",
        "action_id",
        "package_sha256",
        "configuration_sha256",
        "plan_sha256",
        "connector_name",
        "connector_version",
        "connector_artifact_sha256",
        "operation",
        "target_sha256",
        "input_sha256",
        "policy_sha256",
        "approval_mode",
        "independent_approval",
        "requester_id",
        "issued_at_ms",
        "expires_at_ms",
    ],
)
def test_each_binding_field_changes_identity(action_binding, field):
    original = build_action_intent(action_binding)
    value = action_binding[field]
    action_binding[field] = (
        False
        if field == "independent_approval"
        else "none"
        if field == "approval_mode"
        else "2.0.0"
        if field == "connector_version"
        else "f" * 64
        if field.endswith("sha256")
        else value + 1
        if type(value) is int
        else value + "-other"
    )
    assert build_action_intent(action_binding).sha256 != original.sha256


def test_valid_checks_grant_no_authority(
    action_binding, human_decision, binding_context, runtime_command
):
    intent = build_action_intent(action_binding)
    for result in (
        check_decision(intent, human_decision, binding_context),
        check_command_binding(runtime_command, intent, human_decision, binding_context),
    ):
        assert result.valid is True
        assert result.code == "binding_valid"
        assert result.signature_status == "not_checked"
        assert result.assurance == "synthetic-contract-evaluation"
        assert result.authority_granted is False


@pytest.mark.parametrize("field", ["target_sha256", "input_sha256", "connector_artifact_sha256"])
def test_changed_intent_cannot_reuse_decision(
    action_binding, human_decision, binding_context, field
):
    action_binding[field] = "f" * 64
    result = check_decision(build_action_intent(action_binding), human_decision, binding_context)
    assert (result.valid, result.code, result.authority_granted) == (
        False,
        "intent_mismatch",
        False,
    )


@pytest.mark.parametrize(
    "field,code",
    [
        *[
            (f, "context_mismatch")
            for f in (
                "installation_id",
                "workspace_id",
                "project_id",
                "environment_id",
                "execution_id",
                "step_id",
                "action_id",
                "package_sha256",
                "configuration_sha256",
                "plan_sha256",
                "policy_sha256",
                "requester_id",
            )
        ],
        ("recovery_epoch", "stale_epoch"),
        ("worker_id", "wrong_recipient"),
        ("fencing_generation", "stale_generation"),
    ],
)
def test_every_expected_context_field_is_checked(
    action_binding, human_decision, runtime_command, binding_context, field, code
):
    value = binding_context[field]
    binding_context[field] = (
        value + 1
        if type(value) is int
        else ("f" * 64 if field.endswith("sha256") else value + "-other")
    )
    report = check_command_binding(
        runtime_command, build_action_intent(action_binding), human_decision, binding_context
    )
    assert report.code == code and report.valid is False


@pytest.mark.parametrize(
    "field,code",
    [
        *[
            (f, "context_mismatch")
            for f in (
                "installation_id",
                "workspace_id",
                "project_id",
                "environment_id",
                "execution_id",
                "action_id",
            )
        ],
        ("recovery_epoch", "stale_epoch"),
        ("fencing_generation", "stale_generation"),
        ("worker_id", "wrong_recipient"),
        ("intent_sha256", "intent_mismatch"),
        ("decision_sha256", "intent_mismatch"),
    ],
)
def test_command_one_field_refusals(
    action_binding, human_decision, runtime_command, binding_context, field, code
):
    value = runtime_command[field]
    runtime_command[field] = (
        value + 1
        if type(value) is int
        else ("f" * 64 if field.endswith("sha256") else value + "-other")
    )
    assert (
        check_command_binding(
            runtime_command, build_action_intent(action_binding), human_decision, binding_context
        ).code
        == code
    )


@pytest.mark.parametrize(
    "now,code",
    [
        (999, "not_yet_valid"),
        (1000, "not_yet_valid"),
        (1799, "not_yet_valid"),
        (1800, "binding_valid"),
        (7999, "binding_valid"),
        (8000, "expired"),
        (9000, "expired"),
        (10000, "expired"),
    ],
)
def test_explicit_half_open_time(
    action_binding, human_decision, runtime_command, binding_context, now, code
):
    binding_context["now_ms"] = now
    assert (
        check_command_binding(
            runtime_command, build_action_intent(action_binding), human_decision, binding_context
        ).code
        == code
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("actor_kind", "service", "invalid_approver"),
        ("actor_kind", "worker", "invalid_approver"),
        ("actor_kind", "model", "invalid_approver"),
        ("actor_id", "requester-1", "independence_required"),
        ("decision", "deny", "approval_denied"),
        ("policy_sha256", "f" * 64, "context_mismatch"),
        ("intent_sha256", "f" * 64, "intent_mismatch"),
        ("expires_at_ms", 10001, "intent_mismatch"),
    ],
)
def test_decision_refusals(action_binding, human_decision, binding_context, field, value, code):
    human_decision[field] = value
    assert (
        check_decision(build_action_intent(action_binding), human_decision, binding_context).code
        == code
    )


def test_approval_modes(action_binding, human_decision, runtime_command, binding_context):
    intent = build_action_intent(action_binding)
    assert (
        check_command_binding(runtime_command, intent, None, binding_context).code
        == "approval_required"
    )
    action_binding["approval_mode"] = "none"
    intent = build_action_intent(action_binding)
    runtime_command.update(intent_sha256=intent.sha256, decision_sha256=None)
    assert check_command_binding(runtime_command, intent, None, binding_context).valid
    assert (
        check_command_binding(runtime_command, intent, human_decision, binding_context).code
        == "approval_unexpected"
    )
    runtime_command["decision_sha256"] = "f" * 64
    assert (
        check_command_binding(runtime_command, intent, None, binding_context).code
        == "approval_unexpected"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", True),
        ("format_version", 1.0),
        ("issued_at_ms", True),
        ("issued_at_ms", 1.0),
        ("recovery_epoch", False),
        ("expires_at_ms", 1000),
        ("expires_at_ms", 2592001001),
        ("expires_at_ms", 2**53),
        ("independent_approval", 1),
        ("target_sha256", "F" * 64),
        ("secret", "private sentinel"),
    ],
)
def test_malformed_binding_is_private(action_binding, field, value):
    action_binding[field] = value
    with pytest.raises(ExecutableError) as error:
        build_action_intent(action_binding)
    assert error.value.code == "invalid_wire"
    assert "private sentinel" not in str(error.value)


def test_mutated_and_constructed_inputs_revalidated(action_binding, binding_context):
    binding = ActionBinding.model_validate(action_binding)
    binding.recovery_epoch = True
    with pytest.raises(ExecutableError):
        build_action_intent(binding)
    intent = ActionIntent.model_construct(binding=action_binding, sha256="f" * 64)
    with pytest.raises(ExecutableError) as error:
        intent_digest(intent)
    assert error.value.code == "invalid_wire"
    for field, value in (("now_ms", True), ("now_ms", 1.0), ("recovery_epoch", -1)):
        context = copy.deepcopy(binding_context)
        context[field] = value
        with pytest.raises(ExecutableError) as error:
            validate_binding_context(context)
        assert error.value.code == "invalid_context"


def test_generic_form_and_extra_fields_are_not_decisions(action_binding, binding_context):
    with pytest.raises(ExecutableError) as error:
        check_decision(build_action_intent(action_binding), {"approved": True}, binding_context)
    assert error.value.code == "invalid_wire"


def test_report_fixed_flags_revalidated():
    report = BindingReport(valid=True, code="binding_valid")
    report.authority_granted = 0
    with pytest.raises(ExecutableError):
        validate_binding_report(report)
    with pytest.raises(ExecutableError):
        validate_binding_report(BindingReport.model_construct(valid=True, code="expired"))


def test_decision_digest_includes_actor_and_all_fields(human_decision):
    original = decision_digest(human_decision)
    human_decision["actor_id"] = "reviewer-2"
    assert decision_digest(human_decision) != original


@pytest.mark.parametrize(
    "field",
    [
        "decision_id",
        "intent_sha256",
        "decision",
        "actor_id",
        "actor_kind",
        "policy_sha256",
        "issued_at_ms",
        "expires_at_ms",
    ],
)
def test_every_decision_field_changes_full_digest(human_decision, field):
    digest = decision_digest(human_decision)
    value = human_decision[field]
    human_decision[field] = (
        "deny"
        if field == "decision"
        else "worker"
        if field == "actor_kind"
        else "f" * 64
        if field.endswith("sha256")
        else value + 1
        if type(value) is int
        else value + "-other"
    )
    assert decision_digest(human_decision) != digest


def test_binding_refusal_precedence(
    action_binding, human_decision, runtime_command, binding_context
):
    intent = build_action_intent(action_binding)
    runtime_command.update(
        workspace_id="other",
        recovery_epoch=1,
        fencing_generation=2,
        worker_id="other",
        intent_sha256="f" * 64,
    )
    binding_context["now_ms"] = 10000
    for field, code in (
        ("workspace_id", "context_mismatch"),
        ("recovery_epoch", "stale_epoch"),
        ("fencing_generation", "stale_generation"),
        ("worker_id", "wrong_recipient"),
    ):
        assert (
            check_command_binding(runtime_command, intent, human_decision, binding_context).code
            == code
        )
        runtime_command[field] = binding_context[field]
    assert (
        check_command_binding(runtime_command, intent, human_decision, binding_context).code
        == "expired"
    )
    binding_context["now_ms"] = 2000
    human_decision["decision"] = "deny"
    assert (
        check_command_binding(runtime_command, intent, human_decision, binding_context).code
        == "intent_mismatch"
    )


def test_no_ambient_clock_and_nonindependent_review(
    action_binding, human_decision, binding_context, monkeypatch
):
    import time

    monkeypatch.setattr(time, "time", lambda: pytest.fail("must not read wall clock"))
    action_binding["independent_approval"] = False
    intent = build_action_intent(action_binding)
    human_decision.update(actor_id=action_binding["requester_id"], intent_sha256=intent.sha256)
    assert check_decision(intent, human_decision, binding_context).valid


def test_exact_intent_lifetime_boundary(action_binding):
    action_binding["expires_at_ms"] = action_binding["issued_at_ms"] + 2592000000
    assert build_action_intent(action_binding)
    action_binding["expires_at_ms"] += 1
    with pytest.raises(ExecutableError) as error:
        build_action_intent(action_binding)
    assert error.value.code == "invalid_wire"


def test_arbitrary_objects_are_malformed_not_serialized(action_binding, binding_context):
    class Malicious:
        def model_dump(self):
            pytest.fail("arbitrary serializer invoked")

    for validate, value, code in (
        (build_action_intent, Malicious(), "invalid_wire"),
        (validate_binding_context, Malicious(), "invalid_context"),
    ):
        with pytest.raises(ExecutableError) as error:
            validate(value)
        assert error.value.code == code
    subclass = type("UnreviewedBinding", (ActionBinding,), {})
    with pytest.raises(ExecutableError) as error:
        build_action_intent(subclass.model_validate(action_binding))
    assert error.value.code == "invalid_wire"


def test_context_requires_every_expected_field(binding_context):
    for field in binding_context:
        with pytest.raises(ExecutableError) as error:
            validate_binding_context({k: v for k, v in binding_context.items() if k != field})
        assert error.value.code == "invalid_context"


def test_command_decision_digest_precedes_actor_consistency(
    action_binding, human_decision, runtime_command, binding_context
):
    intent = build_action_intent(action_binding)
    human_decision["decision"] = "deny"
    assert (
        check_command_binding(runtime_command, intent, human_decision, binding_context).code
        == "intent_mismatch"
    )
    runtime_command["decision_sha256"] = decision_digest(human_decision)
    assert (
        check_command_binding(runtime_command, intent, human_decision, binding_context).code
        == "approval_denied"
    )


def test_intent_and_decision_expiry_equalities(action_binding, human_decision, binding_context):
    intent = build_action_intent(action_binding)
    for now, code in (
        (1499, "not_yet_valid"),
        (1500, "binding_valid"),
        (8999, "binding_valid"),
        (9000, "expired"),
    ):
        binding_context["now_ms"] = now
        assert check_decision(intent, human_decision, binding_context).code == code
    human_decision["expires_at_ms"] = 10000
    binding_context["now_ms"] = 9999
    assert check_decision(intent, human_decision, binding_context).valid
    binding_context["now_ms"] = 10000
    assert check_decision(intent, human_decision, binding_context).code == "expired"


def test_report_ascii_and_mutated_fixed_values():
    from lotuspond_sdk.executable.actions import binding_report_json

    encoded = binding_report_json(BindingReport(valid=False, code="expired"))
    assert encoded.isascii() and encoded.endswith(b"\n")
    for field, value in (
        ("authority_granted", 0),
        ("authority_granted", True),
        ("signature_status", "verified"),
        ("valid", 1),
    ):
        report = (
            BindingReport.model_construct(valid=False, code="expired", **{field: value})
            if (field != "valid")
            else BindingReport.model_construct(valid=value, code="expired")
        )
        with pytest.raises(ExecutableError):
            binding_report_json(report)
