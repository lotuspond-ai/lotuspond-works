"""E05 Task 8 harness: uncertain writes settled by read-back against a fake target.

The runtime is simulated through the real API services: the action dispatch
service for commands and receipts, and E04's activity service for read-backs. The
target is an in-memory fixture outside every database snapshot, so writes and
idempotency keys are counted where the effect happens, never from report counters.
"""

import json
from copy import deepcopy
from hashlib import sha256

from lotuspond_runtime.activity_protocol import ActivityResult
from lotuspond_sdk.executable.catalog import catalog_document_digest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import ActionReport, ActionStop
from lotuspond.execution.contracts import canonical_value
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionActionCommand,
    ExecutionActionObservation,
    ExecutionAttempt,
    ExecutionStepInstance,
    ExecutionUsageReservation,
)

from ..activity_fixtures import Demand
from .dispatch_fixtures import intent_row
from .test_admission import doc

BOOLEAN = {"kind": "boolean"}


def flag(value):
    return {"kind": "literal", "schema": BOOLEAN, "value": value}


def readback_package(*, idempotency="target_key", max_attempts=1, backoff=0):
    """Intent, review and dispatch, then a long timer before the (Task 9) verification,
    so the dispatch step's outcome stays visible while the execution runs.

    The connector's rules decide: effect present when `/present` is true, absent
    when it is false (the shared fixture's `present` rules can never tell them apart).
    """
    from .test_tasks import review_package

    package, documents = review_package()
    steps = deepcopy(doc("reviewed_action")["body"]["steps"])
    steps[2]["retry"].update(max_attempts=max_attempts, backoff_seconds=backoff)
    pause = {"kind": "timer", "id": "pause", "delay_seconds": 900}
    package["body"]["steps"] = [*steps[:3], pause, steps[3]]
    connector = deepcopy(documents[0])
    submit = next(o for o in connector["definition"]["operations"] if o["id"] == "submit")
    submit["idempotency"] = idempotency
    submit["reconciliation"]["effect_rules"] = [
        {"op": "eq", "readback_pointer": "/present", "expected": flag(True)}
    ]
    submit["reconciliation"]["absence_rules"] = [
        {"op": "eq", "readback_pointer": "/present", "expected": flag(False)}
    ]
    documents[0] = connector
    package["connectors"][0]["ref"]["sha256"] = catalog_document_digest(connector)
    return package, documents


class Target:
    """The synthetic target: per-key records, applied at most once per key."""

    def __init__(self):
        self.records, self.writes, self.keys = {}, 0, []

    def write(self, key, target, value):
        self.keys.append(key)
        if key in self.records:
            return "duplicate"
        self.records[key] = (target, value)
        self.writes += 1
        return "applied"

    def present(self, target):
        return any(stored == target for stored, _ in self.records.values())


def send(h, target, mode):
    """One command, as a runtime runs it: claim, fetch records, maybe write, report.

    `applied` writes and reports `accepted`; `lose_response` writes and reports
    `unknown` (the answer never arrived); `lose_request` writes nothing and reports
    `unknown`; `lapse` writes and reports nothing, so the lease lapses.
    """
    delivery = h.poll()
    assert delivery is not None
    lease = h.claim(delivery)
    command = json.loads(delivery.command_bytes)
    with Session(h.engine) as db, db.begin():
        records = h.dispatcher.records(
            db, h.runtime, command["command_id"], sha256(delivery.command_bytes).hexdigest()
        )
    key = records.intent.binding.action_id  # the runtime's idempotency key
    if mode in ("applied", "lose_response", "lapse"):
        target.write(key, json.loads(records.actual_target), json.loads(records.actual_input))
    else:
        target.keys.append(key)  # sent, but lost before the target applied it
    if mode != "lapse":
        report(h, lease, "accepted" if mode == "applied" else "unknown")
    return lease


def report(h, lease, status, principal=None):
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, lease.attempt_id)
        intent = intent_row(h, attempt.execution_id)
    receipt = ActionReport(
        attempt_id=lease.attempt_id,
        lease_revision=lease.lease_revision,
        receipt={
            "receipt_id": lease.attempt_id,
            "action_id": intent.action_id,
            "intent_sha256": intent.intent_sha256,
            "attempt": lease.ordinal,
            "target_operation_ref": "submit",
            "status": status,
            "evidence_refs": [],
        },
        response=None,
        usage={"input_tokens": 0, "output_tokens": 0, "cost_microusd": 0, "completed_at_ms": 0},
    )
    with Session(h.engine) as db, db.begin():
        h.dispatcher.record_receipt(db, principal or h.runtime, receipt)


def stop(h, lease, code="not_started", principal=None):
    with Session(h.engine) as db, db.begin():
        h.dispatcher.record_stop(
            db,
            principal or h.runtime,
            ActionStop(
                attempt_id=lease.attempt_id,
                lease_revision=lease.lease_revision,
                code=code,
                usage={
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_microusd": 0,
                    "completed_at_ms": 0,
                },
            ),
        )


def readbacks(h, execution_id):
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
                    ExecutionStepInstance.occurrence_path.like("%#reconcile"),
                )
                .order_by(ExecutionAttempt.ordinal)
            )
        )


def pending_readback(h, execution_id):
    found = [a for a in readbacks(h, execution_id) if a.state == "reserved"]
    assert len(found) == 1, [a.state for a in readbacks(h, execution_id)]
    return found[0]


def reserve_readback(h, attempt_id, active_ms=10000):
    """As the scheduler would, for a write whose compute is already stopped."""
    with Session(h.engine) as db, db.begin():
        h.budgets.reserve_attempt(db, h.actor, attempt_id, Demand(active_ms).demand(attempt_id))


def activities(h):
    """E04's activity service over the synthetic catalog the action runtime observes."""
    if getattr(h, "readback_activities", None) is None:
        from lotuspond.execution.dispatch import ActivityService

        from ..activity_fixtures import Signer

        h.readback_activities = ActivityService(
            fleet=h.command_fleet,
            budgets=h.budgets,
            artifacts=h.store,
            artifact_policy=h.policy,
            trust=h.trust,
            signer=Signer(h.activity_key),
            demand=Demand(),
            installation_id="test-installation",
            test_mode=True,
        )
        h.closing.append(h.readback_activities)
    return h.readback_activities


def read(h, attempt_id, value, *, reserve=True, code="succeeded"):
    """Deliver one read-back through E04's activity service and answer it."""
    if reserve:
        reserve_readback(h, attempt_id)
    principal, service = h.runtime, activities(h)
    with Session(h.engine) as db, db.begin():
        delivery = service.dispatch_activity(db, attempt_id, principal.identity)
    envelope = json.loads(delivery.envelope)
    assert envelope["kind"] == "connector_read" and envelope["operation"] == "lookup"
    with Session(h.engine) as db, db.begin():
        lease = service.claim_activity(
            db,
            principal,
            envelope["delivery_id"],
            sha256(delivery.envelope.encode()).hexdigest(),
        )
    handle = None
    if code == "succeeded":
        with Session(h.engine) as db, db.begin():
            handle = service.store_activity_value(db, principal, attempt_id, canonical_value(value))
    with Session(h.engine) as db, db.begin():
        service.record_activity_result(
            db,
            principal,
            ActivityResult(
                attempt_id=attempt_id,
                lease_revision=lease.lease_revision,
                result=handle,
                code=code,
                usage=dict(
                    input_tokens=0,
                    output_tokens=0,
                    cost_microusd=0,
                    completed_at_ms=database_now_ms(db) - 1,
                ),
            ),
        )
    return envelope


def read_input(h, attempt_id):
    """The read-back's input: exactly the intent's target and input."""
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, attempt_id)
        from lotuspond_sdk.activity import ArtifactHandle

        from lotuspond.execution.progression import _scope

        content = h.store._get_internal(
            db,
            _scope(attempt),
            ArtifactHandle(
                id=attempt.input_artifact_id,
                version=attempt.input_artifact_version,
                sha256=attempt.input_sha256,
            ),
        )
    return json.loads(content)


def observe(h, execution_id, target):
    """Read back the target for this execution's pending read-back, then progress."""
    attempt = pending_readback(h, execution_id)
    value = read_input(h, attempt.id)
    read(h, attempt.id, {"present": target.present(value["target"])})
    h.run2()
    return attempt


def commands(h, execution_id):
    with Session(h.engine) as db:
        return list(
            db.scalars(
                select(ExecutionActionCommand).where(
                    ExecutionActionCommand.execution_id == execution_id
                )
            )
        )


def observations(h, execution_id):
    """In the order they were made: by the attempt each names (read-back ordinals
    grow), a final resolution (naming its write) last."""
    with Session(h.engine) as db:
        rows = list(
            db.execute(
                select(
                    ExecutionActionObservation,
                    ExecutionAttempt.ordinal,
                    ExecutionAttempt.step_instance_id,
                )
                .join(
                    ExecutionAttempt, ExecutionAttempt.id == ExecutionActionObservation.attempt_id
                )
                .where(ExecutionActionObservation.execution_id == execution_id)
            )
        )
    return [
        row[0]
        for row in sorted(rows, key=lambda r: (r[1] < 100, r[1], r[0].source == "resolution"))
    ]


def execution(h, execution_id):
    with Session(h.engine) as db:
        return db.get(Execution, execution_id)


def dispatch_outcome(h, execution_id):
    """The dispatch step's own record: its last attempt's state and value or failure."""
    from lotuspond.execution.progression import _scope

    with Session(h.engine) as db:
        attempt = db.scalar(
            select(ExecutionAttempt)
            .join(
                ExecutionStepInstance,
                ExecutionStepInstance.id == ExecutionAttempt.step_instance_id,
            )
            .where(
                ExecutionAttempt.execution_id == execution_id,
                ExecutionStepInstance.occurrence_path == "dispatch",
            )
        )
        if attempt is None:
            return None
        from lotuspond_sdk.activity import ArtifactHandle

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


def reservation(h, attempt_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(ExecutionUsageReservation).where(
                ExecutionUsageReservation.attempt_id == attempt_id
            )
        )
