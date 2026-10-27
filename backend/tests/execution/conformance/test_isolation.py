"""Task 13 conformance: tenant isolation (TEN-1 to TEN-7)."""

import json
from uuid import uuid4

import pytest
from driver import Refused, decoded_events, form_package
from lotuspond_runtime.errors import FleetError
from processes import wait_for
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lotuspond import models
from lotuspond.models import Execution, ExecutionActivityDelivery, ExecutionAttempt


def refusal(call):
    try:
        call()
    except Refused as refused:
        return refused.status, refused.code
    raise AssertionError("a foreign request was served")


def test_another_workspace_sees_not_found_indistinguishable_from_missing(system):
    """TEN-1: admit, read, signal, control and reconcile across workspaces."""
    w = system
    operator, owner = w.operator, w.customer
    other = operator.tenant(w.engine, label="Other")
    revision = operator.deploy(owner, "form", package=form_package(), dependencies=["waits.policy"])
    execution_id = operator.start(owner, revision)["id"]
    wait = operator.open_wait(owner, execution_id, "review")
    missing = str(uuid4())
    for real, fake in (
        (lambda: operator.start(other, revision), lambda: operator.start(other, missing)),
        (
            lambda: operator.execution(other, execution_id),
            lambda: operator.execution(other, missing),
        ),
        (
            lambda: operator.signal(other, execution_id, wait["id"], "f", {"approved": True}),
            lambda: operator.signal(other, missing, str(uuid4()), "f", {"approved": True}),
        ),
        (
            lambda: operator.control(other, execution_id, "cancel"),
            lambda: operator.control(other, missing, "cancel"),
        ),
        (
            lambda: other.call("POST", f"/executions/{execution_id}/reconciliation", {}),
            lambda: other.call("POST", f"/executions/{missing}/reconciliation", {}),
        ),
    ):
        assert refusal(real) == refusal(fake)
    assert refusal(lambda: operator.execution(other, execution_id))[0] == 404
    assert operator.execution(owner, execution_id)["state"] == "waiting"  # untouched


def test_rows_naming_another_scope_are_rejected_by_the_database(system):
    """TEN-2: composite foreign keys refuse a row that mixes scopes."""
    w = system
    operator, owner = w.operator, w.customer
    other = operator.tenant(w.engine, label="Other")
    revision = operator.deploy(owner, "form", package=form_package(), dependencies=["waits.policy"])
    execution_id = operator.start(owner, revision)["id"]
    operator.open_wait(owner, execution_id, "review")
    with Session(w.engine) as db:
        step = db.scalar(
            select(models.ExecutionStepInstance).where(
                models.ExecutionStepInstance.execution_id == execution_id
            )
        )
        execution = db.get(Execution, execution_id)

    def attempt(scope):
        # Every reference is real and in the owner's scope: only `scope` can be wrong.
        return ExecutionAttempt(
            **scope,
            execution_id=execution_id,
            step_instance_id=step.id,
            ordinal=99,
            input_artifact_id=execution.input_artifact_id,
            input_artifact_version=execution.input_artifact_version,
            input_sha256=execution.input_sha256,
            state="reserved",
        )

    # Positive control: the same row in the execution's own scope is valid.
    with Session(w.engine) as db:
        db.add(attempt(owner.scope))
        db.flush()
        db.rollback()
    with pytest.raises(IntegrityError) as refused, Session(w.engine) as db, db.begin():
        db.add(attempt(other.scope))
    constraint = refused.value.orig.diag.constraint_name
    with Session(w.engine) as db:
        definition = db.scalar(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
            {"name": constraint},
        )
    # A composite key over the scope refused it, not some unrelated reference.
    assert definition.startswith("FOREIGN KEY") and "workspace_id" in definition, definition


def failure(lease):
    """A failure report for this lease, with explicitly unknown usage."""
    import time

    from lotuspond_runtime.activity_protocol import ActivityResult

    return ActivityResult(
        attempt_id=lease.attempt_id,
        lease_revision=lease.lease_revision,
        result=None,
        code="provider_failed",
        usage={
            "input_tokens": None,
            "output_tokens": None,
            "cost_microusd": None,
            "completed_at_ms": time.time_ns() // 1_000_000,
        },
    )


def test_another_pools_runtime_cannot_use_known_identifiers(system, linux):
    """TEN-3: claim, renewal and report with a known ID are refused like unknown ones."""
    w = system
    operator, owner = w.operator, w.customer
    other = operator.tenant(w.engine, label="Other")
    revision = operator.deploy(owner, "activity_slow", dependencies=[operator.documents[0]])
    mine, theirs = operator.hand(owner, "hand-mine"), operator.hand(other, "hand-theirs")
    try:
        operator.start(owner, revision)
        delivery = wait_for(lambda: mine.activity.poll(), 60)
        forged = delivery.model_copy(
            update={
                "envelope": delivery.envelope.replace(
                    json.loads(delivery.envelope)["delivery_id"], str(uuid4())
                )
            }
        )
        codes = []
        for candidate in (delivery, forged):
            with pytest.raises(FleetError) as refused:
                theirs.activity.claim(candidate)
            codes.append(refused.value.code)
        assert codes[0] == codes[1], codes  # existence is not revealed
        lease = mine.activity.claim(delivery)  # positive control: its own runtime can
        unknown = lease.model_copy(update={"attempt_id": str(uuid4())})

        def refusal(call):
            with pytest.raises(FleetError) as refused:
                call()
            return refused.value.code

        # Renewal and report of a known lease are refused exactly as an unknown one's,
        # and never with a code that admits the lease exists.
        renewals = [
            refusal(lambda item=item: theirs.activity.renew(item)) for item in (lease, unknown)
        ]
        reports = [
            refusal(lambda item=item: theirs.activity.result(failure(item)))
            for item in (lease, unknown)
        ]
        assert renewals[0] == renewals[1] and reports[0] == reports[1], (renewals, reports)
        assert "conflict" not in {*renewals, *reports}, (renewals, reports)
        assert mine.activity.renew(lease).attempt_id == lease.attempt_id  # still its own
    finally:
        mine.close()
        theirs.close()


def test_the_same_idempotency_key_in_two_scopes_makes_two_executions(system):
    """TEN-4."""
    w = system
    operator, owner = w.operator, w.customer
    other = operator.tenant(w.engine, label="Other")
    first = operator.deploy(owner, "form", package=form_package(), dependencies=["waits.policy"])
    second = operator.deploy(other, "form", package=form_package(), dependencies=["waits.policy"])
    a = operator.start(owner, first, key="shared-key")
    b = operator.start(other, second, key="shared-key")
    assert a["id"] != b["id"]


def test_a_reviewer_in_two_workspaces_sees_each_scope_alone(system):
    """TEN-5: one person, two workspaces; each wait list shows only its own scope."""
    w = system
    operator, owner = w.operator, w.customer
    other = operator.tenant(w.engine, label="Other")
    mine = operator.deploy(owner, "form", package=form_package(), dependencies=["waits.policy"])
    theirs = operator.deploy(other, "form", package=form_package(), dependencies=["waits.policy"])
    own_execution = operator.start(owner, mine)["id"]
    foreign_execution = operator.start(other, theirs)["id"]
    operator.open_wait(owner, own_execution, "review")
    operator.open_wait(other, foreign_execution, "review")
    with Session(w.engine) as db, db.begin():  # E01's surface: the owner joins Other as admin
        user_id = db.scalar(
            select(models.Membership.user_id).where(
                models.Membership.workspace_id == owner.workspace, models.Membership.role == "owner"
            )
        )
        db.add(models.Membership(workspace_id=other.workspace, user_id=user_id, role="admin"))
    as_other = type(owner)(
        owner.client.base_url,
        w.processes.ca,
        w.config["origin"],
        owner.headers["cookie"],
        other.workspace,
        other.prefix,
    )
    try:
        own = {item["id"] for item in operator.waits(owner, own_execution)}
        theirs_seen = {item["id"] for item in operator.waits(as_other, foreign_execution)}
        assert own and theirs_seen and not own & theirs_seen
        assert refusal(lambda: operator.waits(as_other, own_execution))[0] == 404
        assert refusal(lambda: operator.waits(owner, foreign_execution))[0] == 404
    finally:
        as_other.close()


def test_the_same_package_name_and_version_pins_each_scopes_own_digest(system):
    """TEN-6."""
    w = system
    operator, owner = w.operator, w.customer
    other = operator.tenant(w.engine, label="Other")
    plain, changed = form_package(), form_package()
    changed["body"]["steps"][0]["timeout_seconds"] = 1800  # same name and version
    a = operator.deploy(owner, "form", package=plain, dependencies=["waits.policy"])
    b = operator.deploy(other, "form", package=changed, dependencies=["waits.policy"])
    first, second = operator.start(owner, a)["id"], operator.start(other, b)["id"]
    with Session(w.engine) as db:
        digests = [
            db.get(
                models.ExecutablePublication,
                db.get(models.ExecutionDeploymentRevision, revision).publication_id,
            ).package_sha256
            for revision in (a, b)
        ]
        # Each execution is pinned, through its revision, to its own scope's bytes.
        assert db.get(Execution, first).deployment_revision_id == a
        assert db.get(Execution, second).deployment_revision_id == b
    assert digests[0] != digests[1]


def test_no_record_of_one_tenant_names_another(system, linux):
    """TEN-7: projections, decoded events and delivery payloads hold no foreign IDs."""
    w = system
    operator, owner = w.operator, w.customer
    other = operator.tenant(w.engine, label="Other")
    revision = operator.deploy(owner, "activity_read", dependencies=[operator.documents[0]])
    foreign = operator.deploy(other, "activity_read", dependencies=[operator.documents[0]])
    operator.runtime(owner, "runtime-mine")
    operator.runtime(other, "runtime-theirs")
    mine = operator.start(owner, revision)["id"]
    theirs = operator.start(other, foreign)["id"]
    for customer, execution_id in ((owner, mine), (other, theirs)):
        operator.settled(customer, execution_id, {"succeeded", "failed"}, 90)
    foreign_ids = {*other.scope.values(), theirs, foreign}
    texts = [json.dumps(operator.execution(owner, mine)), json.dumps(operator.events(owner, mine))]
    texts += [json.dumps(content) for _, content in decoded_events(w, mine)]
    with Session(w.engine) as db:
        texts += [
            bytes(row.canonical_envelope).decode()
            for row in db.scalars(
                select(ExecutionActivityDelivery).where(
                    ExecutionActivityDelivery.execution_id == mine
                )
            )
        ]
    assert len(texts) > 3
    assert not [
        identifier for identifier in foreign_ids for content in texts if identifier in content
    ]
