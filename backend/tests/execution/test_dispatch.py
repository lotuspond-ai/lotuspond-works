"""Task 9: signed dispatch, durable claims, child requests and atomic results."""

import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Event
from time import sleep

import pytest
from lotuspond_runtime.activity_protocol import ChildActivityRequest
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import connection_digest
from lotuspond_sdk.activity import canonical_activity_json, sign_activity, validate_activity
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Connection,
    ConnectionVersion,
    Execution,
    ExecutionActivityDelivery,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionEvent,
    ExecutionLease,
    ExecutionRequest,
    ExecutionSchedulerCursor,
    ExecutionUsageReservation,
    Membership,
    RuntimeSession,
)

from .activity_fixtures import FLOW, runtime
from .activity_fixtures import activity as activity
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_progression import flows as flows
from .test_publication import harness as harness


def deliveries(h, attempt_id):
    with Session(h.engine) as db:
        return db.scalar(
            select(func.count())
            .select_from(ExecutionActivityDelivery)
            .where(ExecutionActivityDelivery.attempt_id == attempt_id)
        )


def resign(h, target, **changes):
    """Replace the stored envelope with altered content, validly signed by trusted keys."""
    with Session(h.engine) as db, db.begin():
        row = db.scalar(
            select(ExecutionActivityDelivery).where(ExecutionActivityDelivery.attempt_id == target)
        )
        envelope = json.loads(bytes(row.canonical_envelope))
        envelope.update(changes)
        canonical = canonical_activity_json(validate_activity(envelope))
        signature = sign_activity(envelope, h.activity_key)
        row.canonical_envelope = canonical
        row.canonical_signature = json.dumps(
            signature.model_dump(), sort_keys=True, separators=(",", ":")
        ).encode()
        row.digest = sha256(canonical).hexdigest()
        return row.id, row.digest


def claim_by_id(h, delivery_id, digest, principal=None):
    with Session(h.engine) as db, db.begin():
        return h.activities.claim_activity(
            db, principal or h.principal_runtime, delivery_id, digest
        )


OTHER = "00000000-0000-4000-8000-000000000099"


def altered_fields(envelope, connection):
    model = {"kind": "model", "name": "model-fixture", "version": "1.0.0", "sha256": "e" * 64}
    return {
        "configuration_sha256": {"configuration_sha256": connection},  # the connection's digest
        "package_sha256": {"package_sha256": "f" * 64},
        "plan_sha256": {"plan_sha256": "f" * 64},
        "input": {"input": {**envelope["input"], "sha256": "f" * 64}},
        "dependency": {"dependency": {**envelope["dependency"], "sha256": "f" * 64}},
        "operation": {"operation": "write"},
        "kind": {"kind": "model", "dependency": model, "operation": None},
        "workspace_id": {"workspace_id": OTHER},
        "project_id": {"project_id": OTHER},
        "environment_id": {"environment_id": OTHER},
        "execution_id": {"execution_id": OTHER},
        "step_instance_id": {"step_instance_id": OTHER},
        "attempt_id": {"attempt_id": OTHER},
        "deployment_revision_id": {"deployment_revision_id": OTHER},
        "installation_id": {"installation_id": "other-installation"},
        "recovery_epoch": {"recovery_epoch": envelope["recovery_epoch"] + 1},
        "pool_id": {"pool_id": OTHER},
        "instance_id": {"instance_id": OTHER},
        "generation": {"generation": envelope["generation"] + 1},
        "delivery_id": {"delivery_id": OTHER},
        "replay_id": {"replay_id": OTHER},
        "issued_at_ms": {"issued_at_ms": envelope["issued_at_ms"] - 1},
        "expires_at_ms": {"expires_at_ms": envelope["expires_at_ms"] - 1},
    }


FIELDS = list(
    altered_fields(
        {
            "input": {},
            "dependency": {},
            "recovery_epoch": 0,
            "generation": 0,
            "issued_at_ms": 0,
            "expires_at_ms": 0,
        },
        "",
    )
)


@pytest.mark.parametrize("field", FIELDS)
def test_each_persisted_pin_and_actual_mapping_is_required(activity, field):
    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    delivery = h.dispatch(attempt_id)
    envelope = json.loads(delivery.envelope)
    with Session(h.engine) as db:
        records = h.activities.records.load(db, attempt_id)
        attempt = db.get(ExecutionAttempt, attempt_id)
        execution = db.get(Execution, execution_id)
        version = db.scalar(select(ConnectionVersion))
        # Positive control: every field is the persisted pin, and the connection's
        # own digest is not interchangeable with the deployment configuration digest.
        assert records.envelope.model_dump(mode="json") == envelope
        assert envelope["input"]["sha256"] == attempt.input_sha256 == records.request_sha256
        assert envelope["deployment_revision_id"] == execution.deployment_revision_id
        connection = connection_digest(records.connection)
        assert connection == version.config_digest != envelope["configuration_sha256"]
        assert records.connection.operations == ("read",)
    delivery_id, digest = resign(h, attempt_id, **altered_fields(envelope, connection)[field])
    # The bytes are validly signed by trusted keys, yet they no longer match the
    # persisted pins and recipient: refused, and no lease exists.
    with pytest.raises(ExecutionError, match="conflict"):
        claim_by_id(h, delivery_id, digest)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 0
        assert db.get(ExecutionAttempt, attempt_id).state == "dispatched"


def test_revoked_connection_and_untrusted_key_deny_dispatch(activity):
    h = activity
    _, attempt_id = h.ready("activity_read")
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Connection).with_for_update()).status = "revoked"
    # E03 identity already refuses a pool whose connection is no longer active.
    with pytest.raises(FleetError, match="unauthenticated"):
        h.dispatch(attempt_id)
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Connection).with_for_update()).status = "active"
    h.trust.state = "revoked"
    with pytest.raises(ExecutionError, match="trust_unavailable"):
        h.dispatch(attempt_id)
    h.trust.state = "allowed"
    assert deliveries(h, attempt_id) == 0
    h.dispatch(attempt_id)  # positive control
    assert deliveries(h, attempt_id) == 1


def test_claim_once_with_lost_ack(activity):
    h = activity
    _, attempt_id = h.ready("activity_read")
    delivery = h.dispatch(attempt_id)
    # A repeated poll before the claim returns the same bytes, never a second delivery.
    assert h.dispatch(attempt_id) == delivery
    first_live_lease = h.claim(delivery)
    lease_after_equal_claim = h.claim(delivery)  # the acknowledgement was lost
    assert lease_after_equal_claim == first_live_lease
    with Session(h.engine) as db:
        server_now_ms = database_now_ms(db)
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 1
        assert db.get(ExecutionAttempt, attempt_id).state == "claimed"
        claimed = db.scalar(
            select(func.count())
            .select_from(ExecutionEvent)
            .where(ExecutionEvent.code == "attempt_claimed")
        )
        assert claimed == 1
    lease = first_live_lease
    assert lease.expires_at_ms - server_now_ms <= 60000
    assert lease.expires_at_ms <= lease.deadline_ms
    # Another runtime with valid credentials cannot claim it, now or later.
    other = runtime(h)
    with pytest.raises(FleetError, match="unauthenticated"):
        h.claim(delivery, other)
    with pytest.raises(ExecutionError, match="conflict"):
        claim_by_id(h, json.loads(delivery.envelope)["delivery_id"], "0" * 64)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 1


@pytest.mark.parametrize("change", ["membership", "session_expiry"])
def test_expiry_and_revoke_during_lock_wait(activity, change):
    from .test_leases import until

    h = activity
    if change == "session_expiry":
        h.principal_runtime = runtime(h, session_ms=6000)
    execution_id, attempt_id = h.ready("activity_read", active_ms=30000)
    delivery = h.dispatch(attempt_id)
    waiting, outcome = Event(), []

    def claim():
        waiting.set()
        try:
            outcome.append(h.claim(delivery))
        except (ExecutionError, FleetError) as error:
            outcome.append(error)

    with ThreadPoolExecutor(1) as pool:
        with Session(h.engine) as holder, holder.begin():
            if change == "membership":
                # Revocation holds the account row the claim must lock after identity.
                holder.scalar(select(Membership).with_for_update()).role = "viewer"
            else:
                holder.scalar(
                    select(Execution).where(Execution.id == execution_id).with_for_update()
                )
            future = pool.submit(claim)
            assert waiting.wait(5)
            if change == "session_expiry":
                with Session(h.engine) as db:
                    session = db.get(RuntimeSession, h.principal_runtime.session_id)
                    expires = session.expires_at_ms
                until(h, expires)  # the session expires while the claim waits
            else:
                sleep(1)
            assert not outcome  # still blocked behind this transaction
        future.result(timeout=30)
    # Authority and database time are checked after the locks are acquired.
    expected = (
        (ExecutionError, "forbidden") if change == "membership" else (FleetError, "unauthenticated")
    )
    assert isinstance(outcome[0], expected[0]) and outcome[0].code == expected[1]
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 0


def tools_ready(h):
    model = json.loads((FLOW / "agent_tools.model.json").read_text())
    execution_id, attempt_id = h.ready("agent_tools", model=model, path="think")
    lease = h.claim(h.dispatch(attempt_id))
    return execution_id, attempt_id, lease


def test_child_request_requires_declaration_and_actual_arguments(activity):
    from lotuspond_runtime.activity_protocol import tool_name

    h = activity
    execution_id, parent, lease = tools_ready(h)
    arguments = h.store_value(parent, {"target": None, "input": None})
    name = tool_name("connector-fixture", "read")

    def request(request_id, principal=None, **changes):
        fields = dict(
            request_id=request_id,
            parent_attempt_id=parent,
            kind="connector_read",
            declaration_name=name,
            arguments=arguments.model_dump(),
        )
        fields.update(changes)
        with Session(h.engine) as db, db.begin():
            h.activities.lock_cursor(db)
            return h.activities.request_child_activity(
                db, principal or h.principal_runtime, ChildActivityRequest(**fields)
            )

    with Session(h.engine) as db, db.begin():
        unregistered = h.store._put_internal(db, h.scope, b'{"input":null,"target":null}', h.policy)
    wrong_shape = h.store_value(parent, {"target": 1, "input": None})
    for changes in (
        dict(declaration_name="connector-fixture-write"),  # undeclared
        dict(kind="model"),  # a declared name of another kind
        dict(arguments=unregistered.model_dump()),  # not this lease holder's value
        dict(arguments=wrong_shape.model_dump()),  # not the declared argument schema
    ):
        with pytest.raises(ExecutionError, match="forbidden"):
            request("refused", **changes)
    child = request("tool-1")
    assert request("tool-1") == child  # equal request: the same child, no second one
    changed = h.store_value(parent, {"target": None, "input": None})
    with pytest.raises(ExecutionError, match="conflict"):
        request("tool-1", arguments=changed.model_dump())
    model_round = request("round-1", kind="model", declaration_name="model-tools-fixture")
    with Session(h.engine) as db:
        rows = {
            row.id: row
            for row in db.scalars(
                select(ExecutionAttempt).where(ExecutionAttempt.parent_attempt_id == parent)
            )
        }
        assert set(rows) == {child, model_round}
        assert all(row.ordinal > 10 and row.state == "reserved" for row in rows.values())
        # Shared root budget, no extra compute slot: the parent's slot covers them.
        reservations = db.scalar(
            select(func.count())
            .select_from(ExecutionUsageReservation)
            .where(ExecutionUsageReservation.attempt_id.in_(list(rows)))
        )
        assert reservations == 2
        assert db.scalar(select(ExecutionCapacity.active)) == 1
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 1
    # The parent's lease is not permission for another runtime session.
    with pytest.raises(ExecutionError, match="conflict"):
        request("tool-2", principal=runtime(h))
    # Each child is a newly signed delivery to the same session, claimed separately.
    with Session(h.engine) as db, db.begin():
        delivery = h.activities.poll_activity(db, h.principal_runtime)
    envelope = json.loads(delivery.envelope)
    assert envelope["attempt_id"] in (child, model_round)
    assert envelope["attempt_id"] != parent
    child_lease = h.claim(delivery)
    h.result(envelope["attempt_id"], child_lease, value=None)
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, envelope["attempt_id"]).state == "succeeded"
        # A child's stop returns no slot: the parent still holds its one.
        assert db.scalar(select(ExecutionCapacity.active)) == 1


def test_result_marks_execution_runnable_atomically(activity, monkeypatch):
    import lotuspond.execution.dispatch as dispatch

    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).runnable_at_ms is None
    original = dispatch.mark_runnable

    def crash(*args, **kwargs):
        raise RuntimeError("PRIVATE_CRASH_BEFORE_COMMIT")

    monkeypatch.setattr(dispatch, "mark_runnable", crash)
    with pytest.raises(ExecutionError):
        h.result(attempt_id, lease, value=None)
    with Session(h.engine) as db:
        # Neither the result nor the marker committed.
        assert db.get(ExecutionAttempt, attempt_id).state == "claimed"
        assert db.get(Execution, execution_id).runnable_at_ms is None
        assert not db.scalar(
            select(ExecutionRequest).where(ExecutionRequest.request_key.like("activity.result:%"))
        )
        assert db.scalar(select(ExecutionCapacity.active)) == 1
    monkeypatch.setattr(dispatch, "mark_runnable", original)
    h.result(attempt_id, lease, value=None)
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        reservation = db.scalar(select(ExecutionUsageReservation))
        assert db.get(ExecutionAttempt, attempt_id).state == "succeeded"
        assert execution.runnable_at_ms is not None
        assert (reservation.state, reservation.observed_cost_microusd) == ("settled", 5)
        assert reservation.compute_stopped_at_ms is not None
        assert db.scalar(select(ExecutionCapacity.active)) == 0
        assert db.scalar(select(ExecutionLease.status)) == "stopped"
    # A different result for the same attempt conflicts; nothing changes.
    with pytest.raises(ExecutionError, match="conflict"):
        h.result(attempt_id, lease, code="unavailable")
    h.drive()
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "succeeded"


def test_output_schema_and_unknown_usage_cannot_complete_a_step(activity):
    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    # The operation's output schema is null; a forged value fails the attempt.
    h.result(attempt_id, lease, value={"forged": True})
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "failed"
    second_execution, second = h.ready("activity_read")
    lease = h.claim(h.dispatch(second))
    with Session(h.engine) as db:
        now = database_now_ms(db)
    h.result(
        second,
        lease,
        value=None,
        usage=dict(input_tokens=None, output_tokens=2, cost_microusd=None, completed_at_ms=now),
    )
    with Session(h.engine) as db:
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(ExecutionUsageReservation.attempt_id == second)
        )
        execution = db.get(Execution, second_execution)
        # Unknown usage is never measured zero: reserved liability and reconciliation.
        assert db.get(ExecutionAttempt, second).state == "uncertain"
        assert reservation.state == "uncertain" and reservation.observed_cost_microusd is None
        assert (execution.state, execution.runnable_at_ms) == ("reconciliation_required", None)


def test_plaintext_credentials_never_enter_the_envelope_or_admission(activity):
    from lotuspond.fleet.authentication import new_token, token_digest
    from lotuspond.models import RuntimeCredential

    h = activity
    # Real runtime secrets: only their digests are stored by E03.
    credential_token, session_token = new_token("lpr_"), new_token("lps_")
    with Session(h.engine) as db, db.begin():
        db.get(RuntimeCredential, h.principal_runtime.credential_id).token_digest = token_digest(
            credential_token, "lpr_"
        )
        db.get(RuntimeSession, h.principal_runtime.session_id).token_digest = token_digest(
            session_token, "lps_"
        )
    execution_id, attempt_id = h.ready("activity_read")
    delivery = h.dispatch(attempt_id)
    lease = h.claim(delivery)
    h.result(attempt_id, lease, value=None)
    sentinels = [credential_token, session_token]
    assert not any(secret in delivery.envelope + delivery.signature for secret in sentinels)
    from lotuspond.models import ExecutionArtifact

    with Session(h.engine) as db:
        for model in (
            ExecutionActivityDelivery,
            ExecutionLease,
            ExecutionAttempt,
            Execution,
            ExecutionRequest,
            ExecutionEvent,
            ExecutionUsageReservation,
        ):
            for row in db.scalars(select(model)):
                stored = repr([getattr(row, c.key) for c in model.__table__.columns])
                assert not any(secret in stored for secret in sentinels), model.__tablename__
        handles = [
            (row.event_artifact_id, row.event_artifact_version, row.digest)
            for row in db.scalars(
                select(ExecutionEvent).where(ExecutionEvent.execution_id == execution_id)
            )
        ]
        for row in db.scalars(
            select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution_id)
        ):
            handles.append((row.input_artifact_id, row.input_artifact_version, row.input_sha256))
            if row.result_artifact_id:
                handles.append(
                    (row.result_artifact_id, row.result_artifact_version, row.result_sha256)
                )
        assert len(handles) > 3
        for identifier, version, digest in handles:
            content = h.store._get_internal(
                db, h.scope, {"id": identifier, "version": version, "sha256": digest}
            ).decode()
            assert not any(secret in content for secret in sentinels)
        assert db.scalar(select(func.count()).select_from(ExecutionArtifact)) >= len(handles)


def test_provider_timeout_policy_change_and_reordered_calls_deny(activity):
    from time import monotonic

    from lotuspond_runtime.protocol import PoolPolicy, canonical_pool_json, pool_digest

    from lotuspond.models import RuntimePool, RuntimePoolPolicy

    h = activity
    execution_id, attempt_id = h.ready("activity_read")
    # A result or renewal before any claim is refused (reordered delivery).
    with pytest.raises(ExecutionError, match="conflict"):
        h.result(attempt_id, type("Lease", (), {"lease_revision": 1})(), code="unavailable")
    # A stuck trust provider is bounded: refused within its 5 s slot, nothing stored.
    release = Event()
    original = h.trust.current

    def stuck(scope, now_ms):
        release.wait(30)
        return original(scope, now_ms)

    h.trust.current = stuck
    started = monotonic()
    with pytest.raises(ExecutionError, match="trust_unavailable"):
        h.dispatch(attempt_id)
    assert monotonic() - started < 10
    release.set()
    h.trust.current = original
    assert deliveries(h, attempt_id) == 0
    # A current pool policy without the bound connection denies dispatch.
    with Session(h.engine) as db, db.begin():
        pool = db.get(RuntimePool, h.pool_id)
        current = db.scalar(
            select(RuntimePoolPolicy).where(
                RuntimePoolPolicy.pool_id == pool.id,
                RuntimePoolPolicy.version == pool.current_policy_version,
            )
        )
        policy = PoolPolicy.model_validate_json(current.canonical_policy)
        narrowed = policy.model_copy(update={"connection_ids": ()})
        db.add(
            RuntimePoolPolicy(
                **h.scope.model_dump(),
                pool_id=pool.id,
                version=current.version + 1,
                canonical_policy=canonical_pool_json(narrowed),
                policy_digest=pool_digest(narrowed),
            )
        )
        db.flush()
        pool.current_policy_version = current.version + 1
    with pytest.raises(ExecutionError, match="dependency_unavailable"):
        h.dispatch(attempt_id)
    assert deliveries(h, attempt_id) == 0


def request_child(h, parent, request_id, kind, name, arguments, principal=None):
    with Session(h.engine) as db, db.begin():
        h.activities.lock_cursor(db)
        return h.activities.request_child_activity(
            db,
            principal or h.principal_runtime,
            ChildActivityRequest(
                request_id=request_id,
                parent_attempt_id=parent,
                kind=kind,
                declaration_name=name,
                arguments=arguments.model_dump(),
            ),
        )


def test_root_result_closes_unstarted_children_and_waits_for_running_ones(activity):
    from lotuspond_runtime.activity_protocol import tool_name

    h = activity
    execution_id, parent, lease = tools_ready(h)
    arguments = h.store_value(parent, {"target": None, "input": None})
    tool = request_child(
        h, parent, "tool-1", "connector_read", tool_name("connector-fixture", "read"), arguments
    )
    model_round = request_child(h, parent, "round-1", "model", "model-tools-fixture", arguments)
    with Session(h.engine) as db, db.begin():
        delivery = h.activities.poll_activity(db, h.principal_runtime)
    running = json.loads(delivery.envelope)["attempt_id"]
    unstarted = ({tool, model_round} - {running}).pop()
    child_lease = h.claim(delivery)
    # A running child reports first: the root's result is refused, nothing changes.
    with pytest.raises(ExecutionError, match="conflict"):
        h.result(parent, lease, value=None)
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, parent).state == "claimed"
        assert db.get(ExecutionAttempt, unstarted).state == "reserved"
    h.result(running, child_lease, value=None)
    h.result(parent, lease, value=None)
    with Session(h.engine) as db:
        stopped = db.scalar(
            select(ExecutionUsageReservation.compute_stopped_at_ms).where(
                ExecutionUsageReservation.attempt_id == unstarted
            )
        )
        # The unstarted child can never run now: cancelled, its compute stop confirmed.
        assert db.get(ExecutionAttempt, unstarted).state == "cancelled"
        assert stopped is not None
        assert db.scalar(select(ExecutionCapacity.active)) == 0
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 0
        assert "cancelled" in set(
            db.scalars(
                select(ExecutionEvent.code).where(
                    ExecutionEvent.execution_id == execution_id,
                    ExecutionEvent.attempt_id == unstarted,
                )
            )
        )
    with Session(h.engine) as db, db.begin():
        # Nothing is left to deliver for this session.
        assert h.activities.poll_activity(db, h.principal_runtime) is None
    # Containment completes, so the successful execution finishes.
    h.drive()
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "succeeded"


def test_child_lease_needs_its_parents_live_lease(activity):
    from lotuspond_runtime.activity_protocol import tool_name

    from .test_leases import renew, until

    h = activity
    model = json.loads((FLOW / "agent_tools.model.json").read_text())
    # The root's reserved active time (8 s) ends its lease well before the child's
    # (10 s, reserved later), with room for the steps below on a loaded runner.
    _, parent = h.ready("agent_tools", model=model, path="think", active_ms=8000)
    parent_lease = h.claim(h.dispatch(parent))
    arguments = h.store_value(parent, {"target": None, "input": None})
    request_child(
        h, parent, "tool-1", "connector_read", tool_name("connector-fixture", "read"), arguments
    )
    with Session(h.engine) as db, db.begin():
        delivery = h.activities.poll_activity(db, h.principal_runtime)
    child_lease = h.claim(delivery)
    assert renew(h, child_lease).attempt_id == child_lease.attempt_id  # positive control
    # The child's own lease outlives its parent's, so only the parent can end it.
    assert child_lease.expires_at_ms > parent_lease.expires_at_ms
    until(h, parent_lease.expires_at_ms)
    with pytest.raises(ExecutionError, match="conflict"):
        renew(h, child_lease)


def second_member(h, role="editor"):
    """Another workspace member whose authority a test can later change."""
    from lotuspond.execution.authorization import authorize_execution
    from lotuspond.models import User, new_id
    from lotuspond.security import Principal

    user_id = new_id()
    with Session(h.engine) as db, db.begin():
        db.add(User(id=user_id, email=f"{user_id}@example.com", name="second", password_hash="x"))
        db.flush()
        db.add(Membership(workspace_id=h.scope.workspace_id, user_id=user_id, role=role))
        db.flush()
        actor = authorize_execution(
            db, Principal(user_id, h.scope.workspace_id, role, user_id), h.scope, "execute"
        )
    return user_id, actor


def poll(h, principal=None):
    with Session(h.engine) as db, db.begin():
        return h.activities.poll_activity(db, principal or h.principal_runtime)


def test_a_refused_head_never_blocks_the_pool(activity):
    h = activity
    user_id, member = second_member(h)
    blocked_execution, blocked = h.ready("activity_read", capacity=2, actor=member)
    other_execution, other = h.ready("activity_read", capacity=2)
    with Session(h.engine) as db, db.begin():
        db.scalar(
            select(Membership).where(Membership.user_id == user_id).with_for_update()
        ).role = "viewer"
    # The older execution's initiator lost execute: it is skipped, not served.
    delivery = poll(h)
    assert json.loads(delivery.envelope)["attempt_id"] == other
    assert deliveries(h, blocked) == 0
    with Session(h.engine) as db, db.begin():
        # A paused execution's pending delivery is not served again either.
        db.scalar(
            select(Execution).where(Execution.id == other_execution).with_for_update()
        ).requested_state = "paused"
    assert poll(h) is None


def test_child_admission_is_rechecked_after_provider_waits(activity):
    from time import monotonic

    from lotuspond_runtime.activity_protocol import tool_name

    from .test_leases import until

    h = activity
    h.principal_runtime = runtime(h, session_ms=8000)
    _, parent, _ = tools_ready(h)
    arguments = h.store_value(parent, {"target": None, "input": None})
    with Session(h.engine) as db:
        expires = db.get(RuntimeSession, h.principal_runtime.session_id).expires_at_ms
        remaining = expires - database_now_ms(db)
    # Start the request with the session live and under 4 s left, so the expiry
    # falls inside the provider's 5 s slot whatever the setup took.
    assert remaining > 1000, "setup outlived the session window"
    if remaining > 4000:
        until(h, expires - 4000)
    original, entered = h.activities.demand, []

    class Slow:
        test_only = True

        def demand(self, attempt_id):
            with Session(h.engine) as db:
                entered.append(database_now_ms(db) < expires)  # live when entered
            until(h, expires)  # the session expires during this provider call
            return original.demand(attempt_id)

    h.activities.demand = Slow()
    started = monotonic()
    with pytest.raises(FleetError, match="unauthenticated"):
        request_child(
            h, parent, "late", "connector_read", tool_name("connector-fixture", "read"), arguments
        )
    assert entered == [True]  # the recheck after the provider call refused it
    assert monotonic() - started < 6  # bounded by the 5 s provider slot
    with Session(h.engine) as db:
        children = db.scalar(
            select(func.count())
            .select_from(ExecutionAttempt)
            .where(ExecutionAttempt.parent_attempt_id == parent)
        )
        assert children == 0


def test_a_late_cursor_is_taken_without_waiting(activity):
    from time import monotonic

    h = activity
    _, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    with Session(h.engine) as holder, holder.begin():
        holder.scalar(select(ExecutionSchedulerCursor).with_for_update())
        started = monotonic()
        with (
            Session(h.engine) as db,
            db.begin(),
            pytest.raises(ExecutionError, match="capacity_unavailable"),
        ):
            # No `lock_cursor` first: the service's own NOWAIT refuses at once.
            from lotuspond_runtime.activity_protocol import ActivityResult

            h.activities.record_activity_result(
                db,
                h.principal_runtime,
                ActivityResult(
                    attempt_id=attempt_id,
                    lease_revision=lease.lease_revision,
                    result=None,
                    code="unavailable",
                    usage=dict(
                        input_tokens=0,
                        output_tokens=0,
                        cost_microusd=0,
                        completed_at_ms=database_now_ms(db) - 1,
                    ),
                ),
            )
        assert monotonic() - started < 1
    h.result(attempt_id, lease, code="unavailable")  # positive control once released


def race(*calls):
    """Run calls at once in separate transactions; return each result or error."""
    from threading import Barrier

    barrier = Barrier(len(calls))

    def run(call):
        barrier.wait(timeout=5)
        try:
            return call()
        except (ExecutionError, FleetError) as error:
            return error

    with ThreadPoolExecutor(len(calls)) as pool:
        return [future.result(timeout=60) for future in [pool.submit(run, c) for c in calls]]


def test_a_child_request_racing_its_parents_result_leaves_no_orphan(activity):
    from lotuspond_runtime.activity_protocol import tool_name

    h = activity
    _, parent, lease = tools_ready(h)
    arguments = h.store_value(parent, {"target": None, "input": None})
    race(
        lambda: request_child(
            h, parent, "raced", "connector_read", tool_name("connector-fixture", "read"), arguments
        ),
        lambda: h.result(parent, lease, value=None),
    )
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, parent).state == "succeeded"
        states = set(
            db.scalars(
                select(ExecutionAttempt.state).where(ExecutionAttempt.parent_attempt_id == parent)
            )
        )
        assert states <= {"cancelled"}


def test_a_runtime_in_another_pool_is_refused_before_any_provider_call(activity):
    from lotuspond_runtime.protocol import PoolPolicy, canonical_pool_json, pool_digest

    from lotuspond.models import RuntimePool, RuntimePoolPolicy, new_id

    h = activity
    _, attempt_id = h.ready("activity_read")
    pool_id = new_id()
    with Session(h.engine) as db, db.begin():
        db.add(RuntimePool(**h.scope.model_dump(), id=pool_id, name="other", status="active"))
        db.flush()
        policy = PoolPolicy(
            format_version=1,
            connectors=(),
            platforms=("linux",),
            connection_ids=(),
            max_instances=1,
            max_concurrent_invocations=1,
        )
        db.add(
            RuntimePoolPolicy(
                **h.scope.model_dump(),
                pool_id=pool_id,
                version=1,
                canonical_policy=canonical_pool_json(policy),
                policy_digest=pool_digest(policy),
            )
        )
        db.flush()
        db.get(RuntimePool, pool_id).current_policy_version = 1
    original = h.pool_id
    h.pool_id = pool_id
    other = runtime(h)
    h.pool_id = original
    calls, authority = h.trust.calls, []
    original = h.authority.current

    def counted(scope):
        authority.append(scope)
        return original(scope)

    h.authority.current = counted
    with pytest.raises(FleetError, match="unauthenticated"):
        h.dispatch(attempt_id, other)
    # Refused before the initiator's account rows and any deployment lookup.
    assert authority == [] and h.trust.calls == calls and deliveries(h, attempt_id) == 0
    h.authority.current = original
    with Session(h.engine) as db, db.begin():
        assert h.activities.poll_activity(db, other) is None


def test_lost_result_response_is_repeatable_only_by_its_session(activity):
    h = activity
    _, attempt_id = h.ready("activity_read")
    lease = h.claim(h.dispatch(attempt_id))
    usage = dict(input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=1)
    h.result(attempt_id, lease, code="unavailable", usage=usage)
    h.result(attempt_id, lease, code="unavailable", usage=usage)  # the response was lost
    with pytest.raises(ExecutionError, match="conflict"):
        h.result(attempt_id, lease, code="unavailable", usage=usage, principal=runtime(h))
    with Session(h.engine) as db:
        assert db.get(ExecutionAttempt, attempt_id).state == "failed"
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExecutionRequest)
                .where(ExecutionRequest.request_key.like("activity.result:%"))
            )
            == 1
        )


def test_stored_values_are_bounded_per_execution(activity, monkeypatch):
    import lotuspond.execution.dispatch as dispatch

    h = activity
    _, attempt_id = h.ready("activity_read")
    h.claim(h.dispatch(attempt_id))
    monkeypatch.setattr(dispatch, "MAX_VALUES", 2)
    h.store_value(attempt_id, 1)
    h.store_value(attempt_id, 2)
    with pytest.raises(ExecutionError, match="budget_exceeded"):
        h.store_value(attempt_id, 3)
