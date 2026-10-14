"""Literal synthetic transition expectations, authored before the reducer.

The tables deliberately state outcomes directly, never derive them from the
implementation. Removing any scope/replay/phase/evidence guard must fail here.
"""

import copy
import hashlib
import json

import pytest

from lotuspond_sdk.executable import ExecutableError
from lotuspond_sdk.executable.actions import build_action_intent
from lotuspond_sdk.executable.wire import event_payload_digest


def api():
    from lotuspond_sdk.executable import transitions

    return transitions


def state(phase="running", action="not_dispatched", outcome="unknown"):
    return {"phase": phase, "action_state": action, "outcome": outcome}


@pytest.fixture
def context(binding_context, action_binding, human_decision):
    return {
        **{
            k: binding_context[k]
            for k in (
                "installation_id",
                "workspace_id",
                "project_id",
                "environment_id",
                "execution_id",
                "recovery_epoch",
                "now_ms",
            )
        },
        "source_id": "worker-1",
        "source_generation": 3,
        "prior_source_sequence": 0,
        "step_id": "dispatch",
        "action_id": "action-1",
        "attempt": 0,
        "active_step": True,
        "package_sha256": "1" * 64,
        "configuration_sha256": "2" * 64,
        "plan_sha256": "3" * 64,
        "binding_context": binding_context,
        "intent": build_action_intent(action_binding).model_dump(),
        "decision": human_decision,
        "target_operation_ref": "operation:synthetic-1",
        "reconciliation_rule_id": "readback",
        "reconciliation_rule_version": "1.0.0",
        "verification_rule_id": "verify",
        "verification_rule_version": "1.0.0",
        "retry_policy": {
            "max_attempts": 3,
            "timeout_seconds": 10,
            "backoff_seconds": 0,
            "retry_codes": ["timeout_before_dispatch", "unavailable"],
        },
    }


def event(context, kind, payload, *, group="E", **changes):
    result = {
        "record_type": "runtime_event",
        "format_version": 1,
        "event_id": "event-1",
        **{
            k: context[k]
            for k in (
                "installation_id",
                "workspace_id",
                "project_id",
                "environment_id",
                "execution_id",
                "recovery_epoch",
                "source_id",
                "source_generation",
            )
        },
        "step_id": "dispatch" if group in ("A", "S") else None,
        "action_id": "action-1" if group == "A" else None,
        "attempt": 0 if group in ("A", "S") else None,
        "source_sequence": 1,
        "observed_at_ms": 2000,
        "event_kind": kind,
        "payload": payload,
        "payload_sha256": event_payload_digest(kind, payload),
    }
    result.update(changes)
    return result


def admission(context, **changes):
    return event(
        context,
        "admission",
        {
            "package_sha256": "1" * 64,
            "configuration_sha256": "2" * 64,
            "plan_sha256": "3" * 64,
            "recovery_epoch": 2,
        },
        **changes,
    )


def check(s, e, c):
    original = copy.deepcopy((s, e, c))
    report = api().check_event_transition(s, e, c)
    assert (s, e, c) == original
    assert report.authority_granted is False
    assert report.assurance == "synthetic-contract-evaluation"
    assert report.accepted_source_sequence == (
        e["source_sequence"] if report.disposition == "applied" else c["prior_source_sequence"]
    )
    if report.disposition == "refused":
        assert report.state.model_dump() == s
        assert report.structurally_retryable is False
    assert api().transition_report_json(report) == api().transition_report_json(
        api().check_event_transition(s, e, c)
    )
    return report


@pytest.mark.parametrize(
    "phase,kind,payload,group,code,next_phase",
    [
        ("new", "admission", None, "E", "applied", "running"),
        ("running", "admission", None, "E", "illegal_transition", "running"),
        ("running", "step_started", {"input_sha256": "6" * 64}, "S", "applied", "running"),
        ("running", "step_result", {"output_sha256": "8" * 64}, "S", "applied", "running"),
        ("new", "step_result", {"output_sha256": "8" * 64}, "S", "illegal_transition", "new"),
        (
            "waiting",
            "step_result",
            {"output_sha256": "8" * 64},
            "S",
            "illegal_transition",
            "waiting",
        ),
        ("running", "execution_failed", {"code": "unknown"}, "E", "applied", "failed"),
        (
            "running",
            "cancellation_requested",
            {"reason_code": "owner_requested"},
            "E",
            "applied",
            "cancelled",
        ),
        (
            "cancelled",
            "step_started",
            {"input_sha256": "6" * 64},
            "S",
            "terminal_state",
            "cancelled",
        ),
        ("completed", "execution_failed", {"code": "unknown"}, "E", "terminal_state", "completed"),
    ],
)
def test_literal_phase_table(context, phase, kind, payload, group, code, next_phase):
    e = admission(context) if payload is None else event(context, kind, payload, group=group)
    report = check(state(phase), e, context)
    assert report.code == code
    assert report.state.phase == next_phase


def test_replay_uses_entire_record_and_precedes_phase_attempt(context):
    e = admission(context)
    literal_bytes = json.dumps(e, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(literal_bytes).hexdigest()
    assert api().event_digest(e) == digest
    context.update(prior_source_sequence=8, known_event={"event_id": "event-1", "sha256": digest})
    assert check(state("cancelled"), e, context).code == "duplicate"
    for field, value in (("observed_at_ms", 2001), ("source_sequence", 2), ("event_id", "other")):
        changed = {**e, field: value}
        expected = "context_mismatch" if field == "event_id" else "replay_conflict"
        assert check(state("cancelled"), changed, context).code == expected
    e["payload"]["plan_sha256"] = "f" * 64
    e["payload_sha256"] = event_payload_digest("admission", e["payload"])
    assert check(state(), e, context).code == "replay_conflict"


@pytest.mark.parametrize(
    "field,value",
    [
        ("installation_id", "other"),
        ("workspace_id", "other"),
        ("project_id", "other"),
        ("environment_id", "other"),
        ("execution_id", "other"),
        ("source_id", "other"),
        ("source_generation", 2),
        ("recovery_epoch", 1),
    ],
)
def test_fences_precede_known_replay(context, field, value):
    e = admission(context)
    context["known_event"] = {"event_id": e["event_id"], "sha256": api().event_digest(e)}
    context[field] = value
    if field in context["binding_context"]:
        context["binding_context"][field] = value
    assert check(state(), e, context).code == "context_mismatch"


@pytest.mark.parametrize(
    "prior,sequence,code,disposition",
    [
        (0, 2, "sequence_gap", "reconciliation_required"),
        (4, 2, "stale_sequence", "refused"),
        (4, 4, "stale_sequence", "refused"),
    ],
)
def test_sequence_table(context, prior, sequence, code, disposition):
    context["prior_source_sequence"] = prior
    report = check(
        state(action="observed", outcome="verified"),
        admission(context, source_sequence=sequence),
        context,
    )
    assert (report.code, report.disposition) == (code, disposition)
    assert (report.state.action_state, report.state.outcome) == ("observed", "verified")


@pytest.fixture
def wait():
    return {
        "wait_id": "wait-1",
        "kind": "external",
        "correlation_sha256": "8" * 64,
        "not_before_ms": 1000,
        "expires_at_ms": 3000,
    }


@pytest.mark.parametrize(
    "kind,now,wait_kind,code",
    [
        ("signal_received", 999, "external", "wait_not_ready"),
        ("signal_received", 1000, "human", "applied"),
        ("signal_received", 2999, "external", "applied"),
        ("signal_received", 3000, "external", "wait_expired"),
        ("signal_received", 2000, "timer", "wait_mismatch"),
        ("timer_elapsed", 999, "timer", "wait_not_ready"),
        ("timer_elapsed", 1000, "timer", "applied"),
        ("timer_elapsed", 3000, "timer", "wait_expired"),
        ("timer_elapsed", 2000, "human", "wait_mismatch"),
    ],
)
def test_wait_table(context, wait, kind, now, wait_kind, code):
    wait["kind"] = wait_kind
    context.update(wait=wait, now_ms=now)
    context["binding_context"]["now_ms"] = now
    payload = {"wait_id": "wait-1"}
    if kind == "signal_received":
        payload.update(correlation_sha256="8" * 64, signal_sha256="9" * 64)
    report = check(state("waiting"), event(context, kind, payload, group="S"), context)
    assert report.code == code
    if code == "applied":
        assert report.state.phase == "running"


def test_unsolicited_steps_and_waits_refuse(context, wait):
    context["active_step"] = False
    assert (
        check(
            state(), event(context, "step_result", {"output_sha256": "8" * 64}, group="S"), context
        ).code
        == "illegal_transition"
    )
    context["active_step"] = True
    e = event(
        context,
        "signal_received",
        {"wait_id": "wait-1", "correlation_sha256": "8" * 64, "signal_sha256": "9" * 64},
        group="S",
    )
    assert check(state("waiting"), e, context).code == "wait_mismatch"
    context["wait"] = {**wait, "wait_id": "other"}
    assert check(state("waiting"), e, context).code == "wait_mismatch"
    context["wait"] = wait
    assert (
        check(state(), event(context, "wait_started", wait, group="S"), context).state.phase
        == "waiting"
    )


def test_dispatch_and_immutable_decision(context, runtime_command):
    e = event(context, "action_dispatched", {"command": runtime_command}, group="A")
    assert check(state(), e, context).state.action_state == "dispatching"
    for s, code in (
        (state("cancelled"), "terminal_state"),
        (state(action="unknown"), "illegal_transition"),
        (state(action="accepted"), "illegal_transition"),
    ):
        assert check(s, e, context).code == code
    changed = {**context["decision"], "decision_id": "decision-2"}
    e = event(context, "decision_recorded", {"decision": changed}, group="A")
    assert check(state(), e, context).code == "binding_refused"
    context["decision"] = None
    assert check(state(), e, context).code == "applied"


@pytest.mark.parametrize(
    "may_dispatched,action,code,retry,next_action",
    [
        (True, "dispatching", "reconciliation_required", False, "unknown"),
        (False, "not_dispatched", "applied", True, "not_dispatched"),
        (False, "dispatching", "reconciliation_required", False, "unknown"),
        (True, "observed", "reconciliation_required", False, "observed"),
    ],
)
def test_timeout_table(context, may_dispatched, action, code, retry, next_action):
    e = event(
        context,
        "dispatch_timeout",
        {"intent_sha256": context["intent"]["sha256"], "may_have_dispatched": may_dispatched},
        group="A",
    )
    report = check(state(action=action), e, context)
    assert (report.code, report.structurally_retryable, report.state.action_state) == (
        code,
        retry,
        next_action,
    )


def test_cancellation_and_compensation_failure_preserve_effects(context):
    for action, outcome in (
        ("unknown", "unknown"),
        ("observed", "verified"),
        ("accepted", "not_verified"),
    ):
        for kind, payload, phase in (
            ("cancellation_requested", {"reason_code": "owner_requested"}, "cancelled"),
            ("execution_failed", {"code": "unknown"}, "failed"),
        ):
            report = check(
                state(action=action, outcome=outcome), event(context, kind, payload), context
            )
            assert report.state.model_dump() == state(phase, action, outcome)


@pytest.fixture
def receipt(context):
    return {
        "record_type": "action_receipt",
        "format_version": 1,
        "receipt_id": "receipt-1",
        "action_id": "action-1",
        "intent_sha256": context["intent"]["sha256"],
        "attempt": 0,
        "target_operation_ref": "operation:synthetic-1",
        "status": "accepted",
        "evidence_refs": [],
    }


@pytest.mark.parametrize(
    "status,evidence,code,action",
    [
        ("accepted", False, "applied", "accepted"),
        ("observed_effect", False, "evidence_missing", "dispatching"),
        ("observed_effect", True, "applied", "observed"),
        ("unknown", False, "reconciliation_required", "unknown"),
    ],
)
def test_receipts_never_verify(context, receipt, evidence_ref, status, evidence, code, action):
    receipt.update(status=status, evidence_refs=[evidence_ref] if evidence else [])
    report = check(
        state(action="dispatching"),
        event(context, "action_receipt", {"receipt": receipt}, group="A"),
        context,
    )
    assert (report.code, report.state.action_state) == (code, action)
    assert report.state.outcome != "verified"


@pytest.mark.parametrize(
    "observation,qualified,evidence,code,retry,action",
    [
        ("unknown", False, False, "reconciliation_required", False, "unknown"),
        ("effect_present", False, False, "evidence_missing", False, "unknown"),
        ("effect_present", False, True, "applied", False, "observed"),
        ("effect_absent", False, True, "reconciliation_required", False, "unknown"),
        ("effect_absent", True, True, "applied", True, "not_dispatched"),
    ],
)
def test_reconciliation_table(
    context, evidence_ref, observation, qualified, evidence, code, retry, action
):
    payload = {
        "intent_sha256": context["intent"]["sha256"],
        "observation": observation,
        "rule_id": "readback",
        "rule_version": "1.0.0",
        "qualified_absence": qualified,
        "evidence_refs": [evidence_ref] if evidence else [],
    }
    report = check(
        state("reconciliation_required", "unknown"),
        event(context, "reconciliation_observed", payload, group="A"),
        context,
    )
    assert (report.code, report.structurally_retryable, report.state.action_state) == (
        code,
        retry,
        action,
    )


def test_outcome_binding_and_no_receipt_promotion(context, receipt, evidence_ref):
    context["receipt"] = receipt
    payload = {
        "outcome": {
            "record_type": "outcome_record",
            "format_version": 1,
            "outcome_id": "outcome-1",
            "action_id": "action-1",
            "intent_sha256": context["intent"]["sha256"],
            "receipt_id": "receipt-1",
            "state": "verified",
            "rule_id": "verify",
            "rule_version": "1.0.0",
            "evidence_refs": [evidence_ref],
            "observed_at_ms": 2000,
        }
    }
    e = event(context, "outcome_recorded", payload, group="A")
    assert check(state(action="accepted"), e, context).state.outcome == "verified"
    context["receipt"] = {**receipt, "receipt_id": "other"}
    assert check(state(action="accepted"), e, context).code == "binding_refused"


@pytest.mark.parametrize(
    "summary,code,outcome",
    [
        ((0, 0, 0, 0, 0), "applied", "unknown"),
        ((2, 2, 0, 0, 0), "applied", "verified"),
        ((2, 1, 1, 0, 0), "verification_missing", "unknown"),
        ((2, 1, 0, 1, 0), "verification_missing", "unknown"),
        ((2, 2, 0, 0, 1), "completion_incomplete", "unknown"),
    ],
)
def test_completion_table(context, summary, code, outcome):
    context["completion"] = dict(
        zip(
            (
                "required_effects",
                "verified_effects",
                "not_verified_effects",
                "unknown_effects",
                "unfinished_steps",
            ),
            summary,
        )
    )
    report = check(
        state(), event(context, "execution_completed", {"output_sha256": "8" * 64}), context
    )
    assert (report.code, report.state.outcome) == (code, outcome)
    if code == "applied":
        assert report.state.phase == "completed"


def test_model_boundaries_reject_mutation_and_bad_summaries(context):
    t = api()
    for model, raw, validate, field, bad in (
        (t.ContractState, state(), t.validate_contract_state, "phase", "private-sentinel"),
        (t.EventContext, context, t.validate_event_context, "now_ms", True),
        (
            t.KnownEvent,
            {"event_id": "event-1", "sha256": "1" * 64},
            t.validate_known_event,
            "sha256",
            "private-sentinel",
        ),
        (
            t.CompletionSummary,
            {
                "required_effects": 1,
                "verified_effects": 1,
                "not_verified_effects": 0,
                "unknown_effects": 0,
                "unfinished_steps": 0,
            },
            t.validate_completion_summary,
            "unknown_effects",
            1,
        ),
    ):
        for constructed in (False, True):
            value = model.model_construct(**raw) if constructed else model.model_validate(raw)
            setattr(value, field, bad)
            with pytest.raises(ExecutableError) as error:
                validate(value)
            assert "private-sentinel" not in str(error.value)
    report = check(state("new"), admission(context), context)
    for field in ("authority_granted", "structurally_retryable"):
        for bad in (0, 1, "false"):
            changed = report.model_copy(update={field: bad})
            with pytest.raises(ExecutableError):
                t.transition_report_json(changed)


def test_admission_hashes_are_independent_context_facts(context):
    for field in ("package_sha256", "configuration_sha256", "plan_sha256"):
        changed = copy.deepcopy(context)
        changed[field] = "f" * 64
        changed["binding_context"][field] = "f" * 64
        assert check(state("new"), admission(context), changed).code == "context_mismatch"
    minimal = {
        k: v for k, v in context.items() if k not in ("binding_context", "intent", "decision")
    }
    assert check(state("new"), admission(context), minimal).code == "applied"


def test_root_public_exports():
    import lotuspond_sdk.executable as public

    for name in (
        "ContractState",
        "EventContext",
        "KnownEvent",
        "CompletionSummary",
        "TransitionReport",
        "event_digest",
        "check_event_transition",
        "validate_contract_state",
        "validate_event_context",
        "validate_known_event",
        "validate_completion_summary",
        "validate_transition_report",
        "transition_report_json",
    ):
        assert getattr(public, name, None) is getattr(api(), name)


def test_reconciliation_pending_cannot_suggest_retry(context):
    e = event(
        context,
        "dispatch_timeout",
        {"intent_sha256": context["intent"]["sha256"], "may_have_dispatched": False},
        group="A",
    )
    report = check(state("reconciliation_required"), e, context)
    assert report.code == "illegal_transition"
    assert report.structurally_retryable is False


def test_empty_completion_cannot_erase_verified_effect(context):
    context["completion"] = {
        "required_effects": 0,
        "verified_effects": 0,
        "unknown_effects": 0,
        "not_verified_effects": 0,
        "unfinished_steps": 0,
    }
    report = check(
        state(outcome="verified"),
        event(context, "execution_completed", {"output_sha256": "8" * 64}),
        context,
    )
    assert report.code == "verification_missing"


@pytest.mark.parametrize(
    "code,action,attempt,now,retry",
    [
        ("unavailable", "not_dispatched", 0, 2000, True),
        ("rate_limited", "not_dispatched", 0, 2000, False),
        ("timeout_before_dispatch", "not_dispatched", 2, 2000, False),
        ("timeout_before_dispatch", "not_dispatched", 0, 10000, False),
        ("unavailable", "unknown", 0, 2000, False),
        ("unavailable", "accepted", 0, 2000, False),
    ],
)
def test_step_failure_retry_never_replaces_possible_write(
    context, code, action, attempt, now, retry
):
    context.update(attempt=attempt, now_ms=now)
    context["binding_context"]["now_ms"] = now
    e = event(context, "step_failed", {"code": code}, group="S", attempt=attempt)
    report = check(state(action=action), e, context)
    assert report.structurally_retryable is retry


def test_non_action_step_retry_needs_no_fabricated_intent(context):
    for field in ("intent", "binding_context", "decision"):
        context.pop(field)
    e = event(context, "step_failed", {"code": "unavailable"}, group="S")
    assert check(state(), e, context).structurally_retryable is True


@pytest.mark.parametrize("field", ["step_id", "attempt", "action_id"])
def test_attempt_facts_after_replay(context, runtime_command, field):
    e = event(context, "action_dispatched", {"command": runtime_command}, group="A")
    changed = copy.deepcopy(context)
    changed[field] = 7 if field == "attempt" else "other"
    if field in changed["binding_context"]:
        changed["binding_context"][field] = changed[field]
    changed["known_event"] = {"event_id": e["event_id"], "sha256": api().event_digest(e)}
    assert check(state("cancelled"), e, changed).code == "duplicate"
    changed["known_event"] = None
    assert check(state(), e, changed).code == "context_mismatch"


@pytest.mark.parametrize(
    "field",
    [
        "now_ms",
        "installation_id",
        "workspace_id",
        "project_id",
        "environment_id",
        "execution_id",
        "recovery_epoch",
        "step_id",
        "action_id",
        "package_sha256",
        "configuration_sha256",
        "plan_sha256",
    ],
)
def test_contradictory_nested_context_is_malformed(context, field):
    nested = context["binding_context"]
    nested[field] = (
        nested[field] + 1
        if type(nested[field]) is int
        else "f" * 64
        if field.endswith("sha256")
        else "other"
    )
    with pytest.raises(ExecutableError) as error:
        api().validate_event_context(context)
    assert error.value.code == "invalid_context"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("decision", None, "approval_required"),
        ("decision", "deny", "approval_denied"),
        ("now_ms", 9000, "expired"),
        ("now_ms", 999, "not_yet_valid"),
    ],
)
def test_dispatch_refusal_bindings(context, runtime_command, field, value, code):
    if field == "decision" and value == "deny":
        from lotuspond_sdk.executable.actions import decision_digest

        context["decision"]["decision"] = "deny"
        runtime_command["decision_sha256"] = decision_digest(context["decision"])
    elif field == "now_ms":
        context["now_ms"] = value
        context["binding_context"]["now_ms"] = value
    else:
        context[field] = value
    report = check(
        state(),
        event(context, "action_dispatched", {"command": runtime_command}, group="A"),
        context,
    )
    assert (report.code, report.binding_code) == ("binding_refused", code)


@pytest.mark.parametrize(
    "attempt,max_attempts,code",
    [(0, 1, "applied"), (1, 1, "retry_exhausted"), (9, 10, "applied"), (10, 10, "retry_exhausted")],
)
def test_zero_based_attempt_ceiling(context, runtime_command, attempt, max_attempts, code):
    context["attempt"] = attempt
    context["retry_policy"]["max_attempts"] = max_attempts
    assert (
        check(
            state(),
            event(
                context,
                "action_dispatched",
                {"command": runtime_command},
                group="A",
                attempt=attempt,
            ),
            context,
        ).code
        == code
    )


@pytest.mark.parametrize(
    "field,value",
    [("intent_sha256", "f" * 64), ("target_operation_ref", "other"), ("receipt_id", "other")],
)
def test_receipt_expected_identity_is_immutable(context, receipt, field, value):
    context["receipt"] = copy.deepcopy(receipt)
    receipt[field] = value
    report = check(
        state(action="dispatching"),
        event(context, "action_receipt", {"receipt": receipt}, group="A"),
        context,
    )
    assert report.code == "binding_refused"


def test_qualified_absence_never_erases_known_effect(context, evidence_ref):
    p = {
        "intent_sha256": context["intent"]["sha256"],
        "observation": "effect_absent",
        "qualified_absence": True,
        "rule_id": "readback",
        "rule_version": "1.0.0",
        "evidence_refs": [evidence_ref],
    }
    e = event(context, "reconciliation_observed", p, group="A")
    report = check(state("reconciliation_required", "observed", "verified"), e, context)
    assert (report.disposition, report.structurally_retryable) == ("reconciliation_required", False)
    assert (report.state.action_state, report.state.outcome) == ("observed", "verified")
    context["reconciliation_rule_id"] = "other"
    assert check(state("reconciliation_required", "unknown"), e, context).code == "binding_refused"


def test_late_evidence_never_resurrects_terminal_state(context, receipt, evidence_ref):
    receipt.update(status="observed_effect", evidence_refs=[evidence_ref])
    e = event(context, "action_receipt", {"receipt": receipt}, group="A")
    for phase in ("cancelled", "failed", "completed"):
        report = check(state(phase, "observed", "verified"), e, context)
        assert report.code == "terminal_state"
        gap = {**e, "source_sequence": 2}
        assert check(state(phase, "observed", "verified"), gap, context).state.phase == phase


def test_all_context_and_state_fields_revalidated(context):
    t = api()
    valid_context = t.validate_event_context(context)
    for model, validate in (
        (valid_context, t.validate_event_context),
        (t.ContractState(**state()), t.validate_contract_state),
    ):
        for field in type(model).model_fields:
            changed = model.model_copy(update={field: {"untrusted": "private sentinel"}})
            with pytest.raises(ExecutableError) as error:
                validate(changed)
            assert "private sentinel" not in str(error.value)
    for field in (
        "prior_source_sequence",
        "source_generation",
        "recovery_epoch",
        "now_ms",
        "attempt",
    ):
        for bad in (True, 1.0, -1):
            with pytest.raises(ExecutableError):
                t.validate_event_context(valid_context.model_copy(update={field: bad}))
    for field in t.CompletionSummary.model_fields:
        for bad in (True, 1.0, -1, 10001):
            raw = dict.fromkeys(t.CompletionSummary.model_fields, 0)
            raw[field] = bad
            with pytest.raises(ExecutableError):
                t.validate_completion_summary(raw)


def test_nested_models_and_public_event_digest_are_fully_admitted(context, wait, receipt):
    t = api()
    raw = copy.deepcopy(context)
    raw.update(wait=wait, receipt=receipt)
    model = t.validate_event_context(raw)
    mutations = [
        ("intent", "sha256", "f" * 64),
        ("decision", "expires_at_ms", 1500),
        ("receipt", "attempt", True),
        ("wait", "expires_at_ms", 1000),
        ("retry_policy", "retry_codes", ["unavailable", "unavailable"]),
    ]
    for nested, field, bad in mutations:
        changed = model.model_copy(deep=True)
        setattr(getattr(changed, nested), field, bad)
        with pytest.raises(ExecutableError):
            t.validate_event_context(changed)
    e = admission(context)
    e["payload_sha256"] = "f" * 64
    with pytest.raises(ExecutableError):
        t.event_digest(e)


def test_limits_and_no_untrusted_serializer(context):
    t = api()

    class Untrusted(t.ContractState):
        def model_dump(self, **kwargs):
            pytest.fail("untrusted serializer invoked")

    with pytest.raises(ExecutableError):
        t.validate_contract_state(Untrusted(**state()))
    oversized = {**context, "extra": "x" * 65537}
    with pytest.raises(ExecutableError) as error:
        t.validate_event_context(oversized)
    assert error.value.code == "input_too_large"
    cyclic = {**context}
    cyclic["extra"] = cyclic
    with pytest.raises(ExecutableError) as error:
        t.validate_event_context(cyclic)
    assert error.value.code == "cyclic_input"
    report = check(state("new"), admission(context), context)
    assert t.transition_report_json(report).isascii()
    assert len(t.transition_report_json(report)) < 65536
    for changes in (
        {"code": "duplicate"},
        {"binding_code": "binding_valid"},
        {"structurally_retryable": True, "state": state("cancelled")},
    ):
        with pytest.raises(ExecutableError):
            t.validate_transition_report(report.model_copy(update=changes))


def test_intent_creation_requires_exact_expected_binding(context, action_binding):
    e = event(context, "intent_created", {"intent": context["intent"]}, group="A")
    assert check(state(), e, context).code == "applied"
    action_binding["input_sha256"] = "f" * 64
    e = event(context, "intent_created", {"intent": build_action_intent(action_binding)}, group="A")
    assert check(state(), e, context).code == "binding_refused"
    context.pop("intent")
    assert check(state(), e, context).code == "binding_refused"


def test_completion_missing_and_current_action_evidence(context):
    e = event(context, "execution_completed", {"output_sha256": "8" * 64})
    assert check(state(), e, context).code == "completion_incomplete"
    context["completion"] = {
        "required_effects": 1,
        "verified_effects": 1,
        "unknown_effects": 0,
        "not_verified_effects": 0,
        "unfinished_steps": 0,
    }
    for action in ("accepted", "observed"):
        assert check(state(action=action), e, context).code == "verification_missing"
    for action in ("unknown", "dispatching"):
        assert check(state(action=action), e, context).code == "illegal_transition"


def test_reconciliation_expiry_and_attempt_limit_retains_absence_without_retry(
    context, evidence_ref
):
    p = {
        "intent_sha256": context["intent"]["sha256"],
        "observation": "effect_absent",
        "qualified_absence": True,
        "rule_id": "readback",
        "rule_version": "1.0.0",
        "evidence_refs": [evidence_ref],
    }
    for now, attempts in ((10000, 3), (2000, 1)):
        context["now_ms"] = context["binding_context"]["now_ms"] = now
        context["retry_policy"]["max_attempts"] = attempts
        report = check(
            state("reconciliation_required", "unknown"),
            event(context, "reconciliation_observed", p, group="A"),
            context,
        )
        assert report.disposition == "applied"
        assert report.state.action_state == "not_dispatched"
        assert report.structurally_retryable is False


def test_every_admitted_state_event_pair_is_total_and_preserves_terminal_effects(
    context, runtime_command, receipt, evidence_ref, wait
):
    """Product invariant: no well-formed pair crashes or manufactures authority."""
    context["wait"] = wait
    context["receipt"] = receipt
    context["completion"] = {
        "required_effects": 0,
        "verified_effects": 0,
        "not_verified_effects": 0,
        "unknown_effects": 0,
        "unfinished_steps": 0,
    }
    events = [
        admission(context),
        event(context, "step_started", {"input_sha256": "6" * 64}, group="S"),
        event(context, "step_result", {"output_sha256": "8" * 64}, group="S"),
        event(context, "step_failed", {"code": "unavailable"}, group="S"),
        event(context, "wait_started", wait, group="S"),
        event(
            context,
            "signal_received",
            {"wait_id": "wait-1", "correlation_sha256": "8" * 64, "signal_sha256": "9" * 64},
            group="S",
        ),
        event(context, "timer_elapsed", {"wait_id": "wait-1"}, group="S"),
        event(context, "intent_created", {"intent": context["intent"]}, group="A"),
        event(context, "decision_recorded", {"decision": context["decision"]}, group="A"),
        event(context, "action_dispatched", {"command": runtime_command}, group="A"),
        event(context, "action_receipt", {"receipt": receipt}, group="A"),
        event(
            context,
            "dispatch_timeout",
            {"intent_sha256": context["intent"]["sha256"], "may_have_dispatched": False},
            group="A",
        ),
        event(
            context,
            "reconciliation_observed",
            {
                "intent_sha256": context["intent"]["sha256"],
                "observation": "unknown",
                "qualified_absence": False,
                "rule_id": "readback",
                "rule_version": "1.0.0",
                "evidence_refs": [],
            },
            group="A",
        ),
        event(
            context,
            "outcome_recorded",
            {
                "outcome": {
                    "record_type": "outcome_record",
                    "format_version": 1,
                    "outcome_id": "outcome-1",
                    "action_id": "action-1",
                    "intent_sha256": context["intent"]["sha256"],
                    "receipt_id": "receipt-1",
                    "state": "unknown",
                    "rule_id": "verify",
                    "rule_version": "1.0.0",
                    "evidence_refs": [],
                    "observed_at_ms": 2000,
                }
            },
            group="A",
        ),
        event(context, "execution_completed", {"output_sha256": "8" * 64}),
        event(context, "cancellation_requested", {"reason_code": "owner_requested"}),
        event(context, "execution_failed", {"code": "unknown"}),
    ]
    for phase in (
        "new",
        "running",
        "waiting",
        "reconciliation_required",
        "cancelled",
        "failed",
        "completed",
    ):
        for action in ("not_dispatched", "dispatching", "accepted", "observed", "unknown"):
            for outcome in ("unknown", "not_verified", "verified"):
                s = state(phase, action, outcome)
                try:
                    api().validate_contract_state(s)
                except ExecutableError:
                    continue
                for e in events:
                    report = check(s, e, context)
                    if phase in ("cancelled", "failed", "completed"):
                        assert report.state.model_dump() == s


def test_unknown_dispatch_never_erases_verified_summary(context):
    e = event(
        context,
        "dispatch_timeout",
        {"intent_sha256": context["intent"]["sha256"], "may_have_dispatched": True},
        group="A",
    )
    report = check(state(outcome="verified"), e, context)
    assert report.disposition == "reconciliation_required"
    assert report.state.outcome == "verified"


def test_dispatch_cannot_reset_known_outcome(context, runtime_command):
    e = event(context, "action_dispatched", {"command": runtime_command}, group="A")
    assert check(state(outcome="verified"), e, context).code == "illegal_transition"


def test_report_and_known_event_fields_revalidate_after_construction(context):
    t = api()
    report = check(state("new"), admission(context), context)
    known = t.KnownEvent(event_id="event-1", sha256="1" * 64)
    for obj, validate in ((report, t.validate_transition_report), (known, t.validate_known_event)):
        for field in type(obj).model_fields:
            raw = obj.model_dump()
            raw[field] = {"private": "sentinel"}
            with pytest.raises(ExecutableError):
                validate(type(obj).model_construct(**raw))


def test_qualified_absence_can_resolve_acknowledgement_only(context, receipt, evidence_ref):
    context["receipt"] = receipt
    p = {
        "intent_sha256": context["intent"]["sha256"],
        "observation": "effect_absent",
        "qualified_absence": True,
        "rule_id": "readback",
        "rule_version": "1.0.0",
        "evidence_refs": [evidence_ref],
    }
    e = event(context, "reconciliation_observed", p, group="A")
    report = check(state("reconciliation_required", "accepted"), e, context)
    assert (report.disposition, report.state.action_state, report.structurally_retryable) == (
        "applied",
        "not_dispatched",
        True,
    )
    for field in ("receipt", "target_operation_ref"):
        changed = {**context, field: None}
        assert (
            check(state("reconciliation_required", "accepted"), e, changed).code
            == "binding_refused"
        )
    context["receipt"] = {**receipt, "target_operation_ref": "other"}
    assert check(state("reconciliation_required", "accepted"), e, context).code == "binding_refused"
    context["receipt"] = {**receipt, "status": "observed_effect", "evidence_refs": [evidence_ref]}
    report = check(state("reconciliation_required", "accepted"), e, context)
    assert (report.disposition, report.structurally_retryable) == ("reconciliation_required", False)


def test_qualified_absence_has_no_invented_failure_code(context, evidence_ref):
    context["retry_policy"]["retry_codes"] = []
    payload = {
        "intent_sha256": context["intent"]["sha256"],
        "observation": "effect_absent",
        "qualified_absence": True,
        "rule_id": "readback",
        "rule_version": "1.0.0",
        "evidence_refs": [evidence_ref],
    }
    report = check(
        state("reconciliation_required", "unknown"),
        event(context, "reconciliation_observed", payload, group="A"),
        context,
    )
    assert report.structurally_retryable is True
    timeout = event(
        context,
        "dispatch_timeout",
        {"intent_sha256": context["intent"]["sha256"], "may_have_dispatched": False},
        group="A",
    )
    assert check(state(), timeout, context).structurally_retryable is False


def test_dispatch_preserves_public_binding_generation_before_expiry(context, runtime_command):
    context["now_ms"] = context["binding_context"]["now_ms"] = 11000
    runtime_command["fencing_generation"] = 2
    e = event(context, "action_dispatched", {"command": runtime_command}, group="A")
    report = check(state(), e, context)
    assert (report.code, report.binding_code) == ("binding_refused", "stale_generation")


def test_decision_preserves_public_binding_scope_before_expiry(context):
    context["now_ms"] = context["binding_context"]["now_ms"] = 11000
    decision = {**context["decision"], "policy_sha256": "f" * 64}
    context["decision"] = None
    e = event(context, "decision_recorded", {"decision": decision}, group="A")
    report = check(state(), e, context)
    assert (report.code, report.binding_code) == ("binding_refused", "context_mismatch")
