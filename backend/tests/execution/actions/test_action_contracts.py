"""E05 Task 1: frozen action binding vectors and closed vocabularies."""

import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from lotuspond_runtime.errors import FleetError
from lotuspond_sdk.executable.actions import (
    HumanDecision,
    canonical_binding_json,
    canonical_decision_json,
    check_decision,
    decision_digest,
)
from lotuspond_sdk.executable.wire import canonical_wire_json, wire_digest
from pydantic import ValidationError

from lotuspond.execution import action_contracts as ac
from lotuspond.execution.errors import ExecutionError

REFUSED = (ValidationError, FleetError)
FIXTURES = Path(__file__).parent / "fixtures"
RECORDS = json.loads((FIXTURES / "records.json").read_text())
EXPECTED = RECORDS["expected"]


def compact(value) -> bytes:
    """The planning method: sorted compact ASCII JSON, independent of the SDK."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def fields(**changes) -> ac.BindingFields:
    return dataclasses.replace(ac.BindingFields(**RECORDS["binding_fields"]), **changes)


def decision(intent, **changes) -> HumanDecision:
    return HumanDecision(**{**RECORDS["decision"], "intent_sha256": intent.sha256, **changes})


def command(intent, decided):
    c = RECORDS["command"]
    return ac.derive_command(
        intent,
        decision_digest(decided),
        ac.Recipient(c["worker_id"], c["fencing_generation"]),
        c["command_id"],
        c["replay_id"],
        c["now_ms"],
        c["attempt_deadline_ms"],
    )


def test_literal_binding_decision_and_command_vectors():
    assert hashlib.sha256(compact(RECORDS["target"])).hexdigest() == EXPECTED["target_sha256"]
    assert hashlib.sha256(compact(RECORDS["input"])).hexdigest() == EXPECTED["input_sha256"]
    intent = ac.derive_intent(fields())
    binding_canonical = canonical_binding_json(intent.binding)
    assert binding_canonical == (FIXTURES / "binding.canonical.json").read_bytes()
    assert binding_canonical == compact(intent.binding.model_dump(mode="json"))
    assert len(binding_canonical) == 1245
    assert intent.sha256 == "2c976a61d1ad7c137b0e453862854019b3c47c575f9e6f07303efd6fb0da81bd"
    assert intent.sha256 == hashlib.sha256(binding_canonical).hexdigest()
    assert ac.idempotency_key(intent) == intent.binding.action_id

    decided = decision(intent)
    decision_canonical = canonical_decision_json(decided)
    assert decision_canonical == (FIXTURES / "decision.canonical.json").read_bytes()
    assert decision_canonical == compact(decided.model_dump(mode="json"))
    assert len(decision_canonical) == 419
    decision_sha256 = decision_digest(decided)
    assert decision_sha256 == "c4887c888df1e8b9e797445071416630bd39db8fcb3298433c1ddbffef9b4ad6"

    issued = command(intent, decided)
    command_canonical = canonical_wire_json(issued)
    assert command_canonical == (FIXTURES / "command.canonical.json").read_bytes()
    assert command_canonical == compact(issued.model_dump(mode="json"))
    assert issued.expires_at_ms == 1800000150000 and len(command_canonical) == 783
    command_sha256 = wire_digest(issued)
    assert command_sha256 == "678f627d787de45584199fd8aa5906fabb4a85c205b93c9b79c698d59431a9e9"


BOUND_FIELDS = [
    ("installation_id", "other-installation"),
    ("recovery_epoch", 2),
    ("workspace_id", "00000000-0000-4000-8000-000000000101"),
    ("project_id", "00000000-0000-4000-8000-000000000102"),
    ("environment_id", "00000000-0000-4000-8000-000000000103"),
    ("execution_id", "00000000-0000-4000-8000-000000000104"),
    ("step_id", "other"),
    ("action_id", "00000000-0000-4000-8000-000000000105"),
    ("package_sha256", "1" * 64),
    ("plan_sha256", "2" * 64),
    ("configuration_sha256", "3" * 64),
    ("connector_name", "other-target"),
    ("connector_version", "1.0.1"),
    ("connector_artifact_sha256", "4" * 64),
    ("operation", "cancel"),
    ("target_sha256", "5" * 64),
    ("input_sha256", "6" * 64),
    ("policy_sha256", "7" * 64),
    ("approval_mode", "none"),
    ("independent_approval", False),
    ("requester_id", "00000000-0000-4000-8000-000000000106"),
    ("issued_at_ms", 1800000000001),
    ("expiry_seconds", 3599),  # changes the derived expiry, the 23rd field
]


def test_each_bound_field_changes_the_digest():
    assert len(BOUND_FIELDS) == 23
    assert len(ac.derive_binding(fields()).model_dump()) == 23 + 2  # + format, record type
    original = ac.derive_intent(fields())
    old_decision = decision(original)
    seen = {original.sha256}
    for name, value in BOUND_FIELDS:
        changed = ac.derive_intent(fields(**{name: value}))
        assert changed.sha256 not in seen, name
        seen.add(changed.sha256)
        report = check_decision(
            changed,
            old_decision,
            ac.decision_context(changed, RECORDS["decision"]["issued_at_ms"]),
        )
        expected = {
            "approval_mode": "approval_unexpected",  # an unreviewed intent takes no decision
            "policy_sha256": "context_mismatch",  # the decision quotes the old policy
        }.get(name, "intent_mismatch")
        assert report.code == expected, name
    # The expiry is bounded by the execution deadline.
    assert (
        ac.derive_binding(fields(execution_deadline_ms=1800000001000)).expires_at_ms
        == 1800000001000
    )


def test_decision_context_old_epoch_and_independence():
    intent = ac.derive_intent(fields())
    now = RECORDS["decision"]["issued_at_ms"]
    context = ac.decision_context(intent, now)
    assert (context.worker_id, context.fencing_generation) == ("control-plane", 0)
    assert check_decision(intent, decision(intent), context).code == "binding_valid"
    stale = context.model_copy(update={"recovery_epoch": 2})
    old_epoch_report = check_decision(intent, decision(intent), stale)
    assert old_epoch_report.code == "stale_epoch"
    requester = decision(intent, actor_id=intent.binding.requester_id)
    assert check_decision(intent, requester, context).code == "independence_required"
    for kind in ("service", "worker", "model"):
        assert check_decision(intent, decision(intent, actor_kind=kind), context).code == (
            "invalid_approver"
        )
    other = decision(intent, intent_sha256="0" * 64)
    assert check_decision(intent, other, context).code == "intent_mismatch"


def test_command_expiry_is_bounded_by_the_attempt():
    intent = ac.derive_intent(fields())
    recipient = ac.Recipient("worker-1", 3)
    now = 1800000120000
    long = ac.derive_command(intent, None, recipient, "c-1", "r-1", now, now + 10_000_000)
    assert long.expires_at_ms == now + 300_000
    short = ac.derive_command(intent, None, recipient, "c-1", "r-1", now, now + 30_000)
    assert short.expires_at_ms == now + 30_000
    for deadline in (now, now - 1):
        with pytest.raises(ExecutionError):
            ac.derive_command(intent, None, recipient, "c-1", "r-1", now, deadline)
    context = ac.command_context(intent, recipient, now)
    assert (context.worker_id, context.fencing_generation) == ("worker-1", 3)


def test_closed_vocabularies():
    assert ac.assignment_roles("workspace_admins") == {"owner", "admin"}
    assert ac.assignment_roles("workspace_editors") == {"owner", "admin", "editor"}
    for unknown in ("reviewers", "workspace_viewers", "", None):
        with pytest.raises(ExecutionError) as error:
            ac.assignment_roles(unknown)
        assert error.value.code == "unsupported_policy"
    ac.check_escalation("after_half_timeout")
    ac.check_escalation(None)
    with pytest.raises(ExecutionError):
        ac.check_escalation("after_timeout")
    assert len(ac.FAILURE_CODES) == 9 and set(ac.FAILURE_CODES) <= set(ac.EXECUTION_REASONS)
    view = {
        "id": "t-1",
        "execution_id": "e-1",
        "kind": "review",
        "state": "open",
        "deadline_ms": 1,
        "escalated": False,
        "escalate_at_ms": None,
        "claim": "none",
        "claim_expires_at_ms": None,
        "intent_sha256": "a" * 64,
    }
    ac.HumanTaskView(**view)
    for bad in ({"state": "approved"}, {"kind": "approval"}, {"claim": "mine"}, {"extra": 1}):
        with pytest.raises(REFUSED):
            ac.HumanTaskView(**{**view, **bad})
    with pytest.raises(REFUSED):
        ac.HumanTaskView(**{**view, "deadline_ms": "1"})  # no coercion
    with pytest.raises(REFUSED):
        ac.DecisionRequest(decision_id="d", intent_sha256="a" * 64, decision="approve")
    with pytest.raises(REFUSED):
        ac.ResolutionRequest(resolution_id="r", kind="resend", evidence=None)


def test_constants_and_state_vocabularies_are_pinned():
    from typing import get_args

    assert (ac.TASK_CLAIM_MS, ac.ACTION_LEASE_MS, ac.ACTION_RENEW_MS, ac.MAX_READBACKS) == (
        900_000,
        60_000,
        20_000,
        5,
    )
    assert ac.assignment_roles("workspace_owners") == {"owner"}
    assert set(ac.ASSIGNMENT_ROLES) == {"workspace_owners", "workspace_admins", "workspace_editors"}
    for values, alias in (
        (ac.AUTHORIZATION_STATES, ac.Authorization),
        (ac.EFFECT_STATES, ac.Effect),
        (ac.OUTCOME_STATES, ac.Outcome),
        (ac.TASK_KINDS, ac.TaskKind),
        (ac.TASK_STATES, ac.TaskState),
        (ac.RESOLUTION_KINDS, ac.ResolutionKind),
    ):
        assert values == get_args(alias)
    assert len(ac.AUTHORIZATION_STATES) == 8 and len(ac.EFFECT_STATES) == 6
    assert ac.COMMAND_STATES == ("admitted", "claimed", "stopped", "uncertain", "expired")


USAGE = {"input_tokens": None, "output_tokens": None, "cost_microusd": None, "completed_at_ms": 1}
VALID_DTOS = [
    (
        ac.ProposalView,
        {
            "intent_sha256": "a" * 64,
            "connector_name": "c",
            "connector_version": "1.0.0",
            "operation": "submit",
            "policy_name": "p",
            "policy_version": "1.0.0",
            "approval_mode": "human",
            "independent_approval": True,
            "requester_id": "u-1",
            "issued_at_ms": 1,
            "expires_at_ms": 2,
            "target": {"a": 1},
            "input": [1, "x"],
        },
        {"approval_mode": "auto"},
    ),
    (ac.SubmissionRequest, {"submission_id": "s-1", "payload": {"x": 1}}, {"payload": object()}),
    (
        ac.ActionView,
        {
            "action_id": "a-1",
            "step_id": "hold",
            "occurrence_path": "hold",
            "intent_sha256": "a" * 64,
            "authorization": "approved",
            "effect": "observed",
            "outcome": "verified",
            "task_id": None,
            "receipt_status": None,
            "evidence": (),
        },
        {"authorization": "pending", "outcome": "done"},
    ),
    (
        ac.ActionLease,
        {
            "attempt_id": "a",
            "command_id": "c",
            "ordinal": 1,
            "lease_revision": 1,
            "expires_at_ms": 2,
            "deadline_ms": 3,
        },
        {"lease_revision": "1"},
    ),
    (
        ac.ActionStop,
        {"attempt_id": "a", "lease_revision": 1, "code": "not_started", "usage": USAGE},
        {"code": "stopped"},
    ),
]


@pytest.mark.parametrize("model, valid, bad", VALID_DTOS, ids=lambda v: getattr(v, "__name__", ""))
def test_every_dto_refuses_unknown_values_and_fields(model, valid, bad):
    model(**valid)
    for change in (bad, {"unexpected": 1}):
        with pytest.raises(REFUSED):
            model(**{**valid, **change})


def test_contracts_module_is_inert():
    probe = (
        "import sys, threading; import lotuspond.execution.action_contracts; "
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "{'sqlalchemy','fastapi','starlette','socket','ssl','urllib3','anthropic','httpx','httpx2','psycopg'}"
        " or m in {'lotuspond.db', 'lotuspond.models', 'http.client'}"
        " or m.startswith('lotuspond_runtime.https_json')"
        " or m.startswith('lotuspond_runtime.anthropic_messages')]; "
        "bad += ['thread'] * (threading.active_count() - 1); print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
