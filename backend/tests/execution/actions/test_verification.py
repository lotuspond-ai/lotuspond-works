"""E05 Task 9: outcome verification, and success gated on every dispatched outcome.

Real PostgreSQL and real database time. Verification reads go through E04's activity
service; writes are counted at the fake target, never from report counters.
"""

import json
from copy import deepcopy

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import DecisionRequest
from lotuspond.execution.action_verification import (
    VerificationService,
    completion_allowed,
    outcome_state,
)
from lotuspond.execution.expressions import ValueEnvironment
from lotuspond.execution.sweeper import ExecutionSweeper
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    ExecutionActionCommand,
    ExecutionActionIntent,
    ExecutionActionOutcome,
    ExecutionActionReceipt,
    ExecutionAttempt,
    ExecutionEvent,
    ExecutionHumanTask,
    ExecutionStepInstance,
)

from ..activity_fixtures import Demand
from ..activity_fixtures import activity as activity  # noqa: F401
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..control_fixtures import until
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import intent_row, reserve
from .readback_fixtures import (
    Target,
    execution,
    flag,
    pending_readback,
    read,
    readback_package,
    send,
)
from .test_admission import doc
from .test_resolution import admin
from .test_resolution import resolving as resolving  # noqa: F401
from .test_tasks import review_package
from .test_tasks import tasks as tasks  # noqa: F401

EQ_TRUE = [{"op": "eq", "readback_pointer": "/present", "expected": flag(True)}]


def verification_package(rules=EQ_TRUE, **options):
    """Intent, review, dispatch, then verification (Task 8's distinguishing rules)."""
    package, documents = readback_package(**options)
    steps = [s for s in package["body"]["steps"] if s["id"] != "pause"]
    steps[3]["rules"] = deepcopy(rules)
    package["body"]["steps"] = steps
    return package, documents


def verification_reads(h, execution_id, path="verify"):
    with Session(h.engine) as db:
        return list(
            db.scalars(
                select(ExecutionAttempt)
                .join(
                    ExecutionStepInstance,
                    ExecutionStepInstance.id == ExecutionAttempt.step_instance_id,
                )
                .where(
                    ExecutionAttempt.execution_id == execution_id,
                    ExecutionStepInstance.occurrence_path == path,
                )
                .order_by(ExecutionAttempt.ordinal)
            )
        )


def pending_read(h, execution_id, path="verify"):
    found = [a for a in verification_reads(h, execution_id, path) if a.state == "reserved"]
    assert len(found) == 1, [a.state for a in verification_reads(h, execution_id, path)]
    return found[0]


def outcomes(h, execution_id):
    with Session(h.engine) as db:
        return list(
            db.scalars(
                select(ExecutionActionOutcome).where(
                    ExecutionActionOutcome.execution_id == execution_id
                )
            )
        )


def record_of(row):
    return json.loads(row.outcome_bytes)


def events(h, execution_id, code):
    with Session(h.engine) as db:
        return list(
            db.scalars(
                select(ExecutionEvent).where(
                    ExecutionEvent.execution_id == execution_id, ExecutionEvent.code == code
                )
            )
        )


def step_value(h, execution_id, path):
    """The closed value or failure of a record (e.g. `verify#outcome`)."""
    from lotuspond_sdk.activity import ArtifactHandle

    from lotuspond.execution.progression import _scope

    [attempt] = verification_reads(h, execution_id, path)
    with Session(h.engine) as db:
        content = h.store._get_internal(
            db,
            _scope(attempt),
            ArtifactHandle(
                id=attempt.result_artifact_id,
                version=attempt.result_artifact_version,
                sha256=attempt.result_sha256,
            ),
        )
    return attempt.state, json.loads(content)


def run_to_read(h, key, mode="applied", **options):
    target = Target()
    package, documents = verification_package(**options)
    execution_id = h.reviewed(key, package, documents, active_ms=20000)
    send(h, target, mode)
    h.run2()
    return execution_id, target


def test_verified_requires_evidence_and_rules(actions):
    h = actions
    execution_id, target = run_to_read(h, "verified")
    assert target.writes == 1
    assert execution(h, execution_id).state != "succeeded"  # never from the receipt
    assert outcomes(h, execution_id) == []
    attempt = pending_read(h, execution_id)
    envelope = read(h, attempt.id, {"present": True})
    assert envelope["operation"] == "lookup"
    h.run2()
    [row] = outcomes(h, execution_id)
    verified_outcome = record_of(row)
    assert verified_outcome["state"] == row.state == "verified"
    assert verified_outcome["evidence_refs"]
    [evidence] = verified_outcome["evidence_refs"]
    assert evidence["source_kind"] == "target_readback"
    with Session(h.engine) as db:
        read_row = db.get(ExecutionAttempt, attempt.id)
    assert evidence == {
        "artifact_id": read_row.result_artifact_id,
        "sha256": read_row.result_sha256,
        "media_type": "application/json",
        "classification": "internal",
        "source_kind": "target_readback",
    }
    assert (row.rule_id, row.rule_version) == ("observed", "1")
    intent = intent_row(h, execution_id)
    assert (intent.outcome_state, intent.effect_state) == ("verified", "observed")
    assert len(events(h, execution_id, "outcome_recorded")) == 1
    assert step_value(h, execution_id, "verify#outcome") == (
        "succeeded",
        {"handle_kind": "verification_evidence", "id": row.id},
    )
    done = execution(h, execution_id)
    assert (done.state, done.reason) == ("succeeded", "completed")
    # The rules decide, never the read's mere success: pure positive and negative.
    expected = ValueEnvironment(input=None)
    assert outcome_state(EQ_TRUE, {"present": True}, expected) == "verified"
    assert outcome_state(EQ_TRUE, {"present": False}, expected) == "not_verified"
    assert outcome_state(EQ_TRUE, {}, expected) == "unknown"
    assert outcome_state(EQ_TRUE, {"present": "true"}, expected) == "unknown"


def test_not_verified_fails_with_the_effect_visible(actions):
    """The write lost its answer and read-back observed it; the verification read then
    shows the record without the expected value: not verified, the effect kept."""
    h = actions
    execution_id, target = run_to_read(h, "not-verified", mode="lose_response")
    read(h, pending_readback(h, execution_id).id, {"present": True})  # the read-back
    h.run2()
    assert intent_row(h, execution_id).effect_state == "observed"
    attempt = pending_read(h, execution_id)
    read(h, attempt.id, {"present": False})
    h.run2()
    not_verified_execution = execution(h, execution_id)
    assert not_verified_execution.state == "failed"
    assert not_verified_execution.reason == "outcome_not_verified"
    [row] = outcomes(h, execution_id)
    assert row.state == "not_verified" and record_of(row)["evidence_refs"]
    intent = intent_row(h, execution_id)
    assert (intent.outcome_state, intent.effect_state) == ("not_verified", "observed")
    assert target.writes == 1  # nothing resent, nothing undone
    assert step_value(h, execution_id, "verify#outcome")[1]["failure"] == "outcome_not_verified"


def test_unknown_after_read_retries(actions):
    h = actions
    execution_id, _ = run_to_read(h, "unknown")
    from lotuspond.execution.action_verification import VERIFICATION_READ

    for n in range(VERIFICATION_READ.max_attempts):
        attempt = pending_read(h, execution_id)
        assert attempt.ordinal == n + 1
        read(h, attempt.id, None, code="unavailable")
        h.run2()
        if n + 1 < VERIFICATION_READ.max_attempts:
            assert outcomes(h, execution_id) == []  # still retrying, nothing settled
            with Session(h.engine) as db:
                until(h, database_now_ms(db) + VERIFICATION_READ.backoff_seconds * 1000)
            h.run2()
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "outcome_unknown")
    [outcome] = outcomes(h, execution_id)
    assert outcome.state == "unknown" and record_of(outcome)["evidence_refs"] == []
    assert intent_row(h, execution_id).outcome_state == "unknown"
    assert len(verification_reads(h, execution_id)) == VERIFICATION_READ.max_attempts


# ---------------------------------------------------------------- parallel


def branch_steps(suffix, record_id):
    full = deepcopy(doc("reviewed_action")["body"]["steps"])
    renamed = {s["id"]: f"{s['id']}_{suffix}" for s in full}
    for step in full:
        step["id"] = renamed[step["id"]]
        for field in ("intent", "decision", "receipt"):
            if field in step:
                step[field]["source"]["id"] = renamed[step[field]["source"]["id"]]
        if "target" in step:
            step["target"]["value"] = {"record_id": record_id}
    full[3]["rules"] = deepcopy(EQ_TRUE)
    return full


def parallel_package():
    package, documents = readback_package()
    null = {"kind": "literal", "schema": {"kind": "null"}, "value": None}
    body = {"result": null, "result_schema": {"kind": "data", "schema": {"kind": "null"}}}
    package["body"]["steps"] = [
        {
            "kind": "parallel",
            "id": "both",
            "join": "all",
            "branches": [
                {"id": "a", "body": {**body, "steps": branch_steps("a", "R-100")}},
                {"id": "b", "body": {**body, "steps": branch_steps("b", "R-200")}},
            ],
        }
    ]
    return package, documents


def approve_all(h, execution_id, key):
    with Session(h.engine) as db:
        rows = list(
            db.scalars(
                select(ExecutionHumanTask)
                .where(ExecutionHumanTask.execution_id == execution_id)
                .order_by(ExecutionHumanTask.id)
            )
        )
        digests = {
            row.id: db.get(ExecutionActionIntent, row.intent_id).intent_sha256 for row in rows
        }
    assert len(rows) == 2
    reviewer = h.reviewer()
    for n, row in enumerate(rows):
        h.call("claim", reviewer, row.id)
        h.call(
            "decide",
            reviewer,
            row.id,
            DecisionRequest(
                decision_id=f"d-{key}-{n}", intent_sha256=digests[row.id], decision="allow"
            ),
        )
    h.run2()


def report_accepted(h, lease):
    """The `accepted` receipt for this lease's own intent (two intents here)."""
    from lotuspond.execution.action_contracts import ActionReport

    with Session(h.engine) as db:
        command = db.scalar(
            select(ExecutionActionCommand).where(
                ExecutionActionCommand.attempt_id == lease.attempt_id
            )
        )
        intent = db.get(ExecutionActionIntent, command.intent_id)
    receipt = ActionReport(
        attempt_id=lease.attempt_id,
        lease_revision=lease.lease_revision,
        receipt={
            "receipt_id": lease.attempt_id,
            "action_id": intent.action_id,
            "intent_sha256": intent.intent_sha256,
            "attempt": lease.ordinal,
            "target_operation_ref": "submit",
            "status": "accepted",
            "evidence_refs": [],
        },
        response=None,
        usage={"input_tokens": 0, "output_tokens": 0, "cost_microusd": 0, "completed_at_ms": 0},
    )
    with Session(h.engine) as db, db.begin():
        h.dispatcher.record_receipt(db, h.runtime, receipt)
    return intent


def dispatch_both(h, execution_id):
    """Each branch's write, one slot at a time; their intents by branch."""
    by_branch = {}
    for _ in range(2):
        with Session(h.engine) as db:
            attempt = db.scalar(
                select(ExecutionAttempt)
                .join(
                    ExecutionStepInstance,
                    ExecutionStepInstance.id == ExecutionAttempt.step_instance_id,
                )
                .where(
                    ExecutionAttempt.execution_id == execution_id,
                    ExecutionStepInstance.occurrence_path.like("%#dispatch"),
                    ExecutionAttempt.state == "reserved",
                )
                .order_by(ExecutionAttempt.id)
                .limit(1)
            )
            path = db.get(ExecutionStepInstance, attempt.step_instance_id).occurrence_path
        reserve(h, attempt.id, 20000)
        lease = h.claim(h.poll())
        by_branch[path.split("/")[0]] = report_accepted(h, lease)
        h.run2()
    return by_branch


def test_success_requires_every_dispatched_outcome(actions):
    h = actions
    package, documents = parallel_package()
    execution_id = h.start2(package, "mixed", documents)
    approve_all(h, execution_id, "mixed")
    dispatch_both(h, execution_id)
    read(h, pending_read(h, execution_id, "both:a/verify_a").id, {"present": True})
    h.run2()
    assert execution(h, execution_id).state not in ("succeeded", "failed")  # b is open
    read(h, pending_read(h, execution_id, "both:b/verify_b").id, {"present": False})
    h.run2()
    mixed_parallel_execution = execution(h, execution_id)
    assert mixed_parallel_execution.state != "succeeded"
    assert (mixed_parallel_execution.state, mixed_parallel_execution.reason) == (
        "failed",
        "outcome_not_verified",
    )
    assert sorted(row.state for row in outcomes(h, execution_id)) == ["not_verified", "verified"]
    # The gate itself: one unverified dispatched outcome refuses success.
    assert completion_allowed([("observed", "verified"), ("accepted", "not_verified")]) == (
        "outcome_not_verified"
    )
    assert completion_allowed([("observed", "verified"), ("accepted", "unknown")]) == (
        "outcome_unknown"
    )
    assert completion_allowed([("absent", "unknown")]) == "outcome_unknown"
    assert completion_allowed([("observed", "verified"), ("not_dispatched", "unknown")]) is None
    assert completion_allowed([]) is None


def test_computational_success_is_unchanged(actions):
    h = actions
    package, documents = review_package()
    null = {"kind": "literal", "schema": {"kind": "null"}, "value": None}
    package["body"]["steps"] = [
        {"kind": "transform", "id": "compute", "mapping": null, "output_schema": {"kind": "null"}}
    ]
    package["connectors"] = []  # no effect, no connector to bind
    execution_id = h.start2(package, "computational", documents[1:])
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("succeeded", "completed")
    assert outcomes(h, execution_id) == [] and intent_row(h, execution_id) is None


def test_outcome_binds_the_exact_intent_and_receipt(actions):
    h = actions
    package, documents = parallel_package()
    execution_id = h.start2(package, "bind", documents)
    approve_all(h, execution_id, "bind")
    intents = dispatch_both(h, execution_id)
    with Session(h.engine) as db:
        receipts = {
            branch: db.scalar(
                select(ExecutionActionReceipt).where(ExecutionActionReceipt.intent_id == intent.id)
            )
            for branch, intent in intents.items()
        }
    read_a = pending_read(h, execution_id, "both:a/verify_a")
    read(h, read_a.id, {"present": True})
    service = VerificationService(
        artifacts=h.store, artifact_policy=h.policy, installation_id="test-installation"
    )

    def attempt_record(receipt_id):
        """Call the service directly; roll back whatever it did."""
        db = Session(h.engine)
        try:
            db.begin()
            row = db.get(type(execution(h, execution_id)), execution_id)
            result = service.record(
                db,
                row,
                1,
                path="both:a/verify_a#outcome",
                node_id="verify_a",
                attempt_id=read_a.id,
                receipt_id=receipt_id,
                state="verified",
            )
            # Inside the same transaction, before any rollback: nothing was written.
            assert not db.new and not db.dirty
            assert db.scalar(select(func.count()).select_from(ExecutionActionOutcome)) == 0
            return result
        finally:
            db.rollback()
            db.close()

    # Another intent's receipt, or no receipt at all, is refused; nothing is written.
    assert attempt_record(receipts["both:b"].id) == (None, "binding_refused")
    assert attempt_record(read_a.id) == (None, "binding_refused")
    assert outcomes(h, execution_id) == []
    h.run2()
    [row] = outcomes(h, execution_id)
    outcome = record_of(row)
    assert row.intent_id == intents["both:a"].id
    assert outcome["intent_sha256"] == intents["both:a"].intent_sha256
    assert outcome["action_id"] == intents["both:a"].action_id
    assert row.receipt_row_id == receipts["both:a"].id and row.observation_row_id is None
    assert outcome["receipt_id"] == receipts["both:a"].receipt_id
    # The record is the SDK's, and the SDK's own transition accepts it for this intent.
    from lotuspond_sdk.executable.wire import validate_outcome_record

    assert validate_outcome_record(outcome).model_dump(mode="json") == outcome


def test_a_write_settled_by_readback_verifies_its_observation(resolving):
    """A lapsed write has no receipt row: its effect-present read-back observation is
    the receipt evidence the outcome names."""
    h = resolving
    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed("lapsed", package, documents, active_ms=4000)
    lease = send(h, target, "lapse")
    until(h, lease.expires_at_ms)
    h.sweep()
    h.run2()
    sweeper = ExecutionSweeper(leases=h.leases, recovery=h.fence, readbacks=(h.budgets, Demand()))
    h.sweep(sweeper=sweeper)
    read(h, pending_readback(h, execution_id).id, {"present": True}, reserve=False)
    h.run2()
    # The write still holds the only slot until an owner confirms its stop.
    h.resolve(admin(h, "owner"), execution_id, "compute_stopped")
    h.run2()
    read(h, pending_read(h, execution_id).id, {"present": True})
    h.run2()
    [row] = outcomes(h, execution_id)
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionActionReceipt)) is None
        from lotuspond.models import ExecutionActionObservation

        observation = db.get(ExecutionActionObservation, row.observation_row_id)
    assert row.receipt_row_id is None and observation.result == "effect_present"
    assert record_of(row)["receipt_id"] == observation.observation_id
    assert row.state == "verified"
    assert execution(h, execution_id).state == "succeeded"


def test_success_is_gated_at_the_terminal_transition(actions, monkeypatch):
    """The gate itself, not only the failing step: an otherwise complete execution
    whose dispatched outcome is not verified never succeeds, and stores no result."""
    h = actions
    from lotuspond.execution import action_verification

    real = action_verification.dispatched_outcomes
    seen = []

    def unverified(db, row):
        found = real(db, row)
        seen.append(found)
        return [(effect, "unknown") for effect, _ in found]

    execution_id, _ = run_to_read(h, "gated")
    monkeypatch.setattr(action_verification, "dispatched_outcomes", unverified)
    read(h, pending_read(h, execution_id).id, {"present": True})
    h.run2()
    assert seen == [[("observed", "verified")]]  # the step verified; only the gate refused
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "outcome_unknown")
    assert row.result_artifact_id is None
