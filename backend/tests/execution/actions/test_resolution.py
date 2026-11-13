"""E05 Task 8: an owner's or admin's resolution of an action that awaits one."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import ResolutionRequest
from lotuspond.execution.action_reconciliation import ResolutionService
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.sweeper import ExecutionSweeper
from lotuspond.models import ExecutionActionIntent, ExecutionCapacity

from ..activity_fixtures import Demand
from ..activity_fixtures import activity as activity  # noqa: F401
from ..api_fixtures import member
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..control_fixtures import until
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import intent_row
from .readback_fixtures import (
    Target,
    commands,
    dispatch_outcome,
    execution,
    observations,
    pending_readback,
    read,
    readback_package,
    readbacks,
    reservation,
    send,
)
from .test_tasks import actor_for
from .test_tasks import tasks as tasks  # noqa: F401


@pytest.fixture
def resolving(actions):
    h = actions
    h.resolutions = ResolutionService(
        artifacts=h.store,
        artifact_policy=h.policy,
        budgets=h.budgets,
        recovery=h.fence,
        installation_id="test-installation",
    )

    def resolve(actor, execution_id, kind, resolution_id="r-1", evidence=None):
        request = ResolutionRequest(resolution_id=resolution_id, kind=kind, evidence=evidence)
        action_id = intent_row(h, execution_id).action_id
        with Session(h.engine) as db, db.begin():
            return h.resolutions.resolve(db, actor, execution_id, action_id, request)

    h.resolve = resolve
    return h


def admin(h, role="admin"):
    return actor_for(h, member(h, role), role)


def unknown_five_times(h, name):
    """A lost response, then five read-backs that cannot decide: an owner is needed."""
    target = Target()
    package, documents = readback_package(max_attempts=3)
    execution_id = h.reviewed(name, package, documents, active_ms=20000)
    send(h, target, "lose_response")
    h.run2()
    for _ in range(5):
        read(h, pending_readback(h, execution_id).id, {"elsewhere": True})
        h.run2()
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("reconciliation_required", "action_uncertain")
    return execution_id, target


def readback_evidence(h, execution_id):
    attempt = readbacks(h, execution_id)[-1]
    from lotuspond_sdk.activity import ArtifactHandle

    return ArtifactHandle(
        id=attempt.result_artifact_id,
        version=attempt.result_artifact_version,
        sha256=attempt.result_sha256,
    )


def test_resolution_requires_owner_and_recorded_evidence(resolving):
    h = resolving
    execution_id, target = unknown_five_times(h, "resolve")
    # Editors, viewers and API keys cannot resolve.
    with pytest.raises(ExecutionError) as refused:
        h.resolve(admin(h, "editor"), execution_id, "close_unknown")
    editor_resolution_error = refused.value
    assert editor_resolution_error.code == "forbidden"
    with pytest.raises(ExecutionError) as viewer:
        h.resolve(admin(h, "viewer"), execution_id, "close_unknown")
    assert viewer.value.code == "forbidden"
    from ..test_admission import key_actor

    with pytest.raises(ExecutionError) as keyed:
        h.resolve(key_actor(h, ["execute", "read"]), execution_id, "close_unknown")
    assert keyed.value.code == "forbidden"
    owner = admin(h, "owner")
    # Effect present must cite evidence already recorded for this action.
    with pytest.raises(ExecutionError) as missing:
        h.resolve(owner, execution_id, "effect_present")
    assert missing.value.code == "conflict"
    with Session(h.engine) as db, db.begin():
        from lotuspond.execution.progression import _scope

        stray = h.store._put_internal(
            db, _scope(execution(h, execution_id)), b'{"present":true}', h.policy
        )
    with pytest.raises(ExecutionError) as unrecorded:
        h.resolve(owner, execution_id, "effect_present", evidence=stray)
    assert unrecorded.value.code == "conflict"
    with pytest.raises(ExecutionError) as stray_evidence:
        h.resolve(owner, execution_id, "close_unknown", evidence=stray)
    assert stray_evidence.value.code == "invalid_request"
    # Positive control and the outcome: the owner closes it unknown, without resending.
    view = h.resolve(owner, execution_id, "close_unknown")
    assert view.effect == "unknown"
    assert h.resolve(owner, execution_id, "close_unknown").effect == "unknown"  # a replay
    with pytest.raises(ExecutionError):
        h.resolve(owner, execution_id, "readback")  # the same ID, another kind
    h.run2()
    state, value = dispatch_outcome(h, execution_id)
    assert state == "failed" and value["failure"] == "outcome_unknown"
    row = execution(h, execution_id)
    assert (row.state, row.reason) == ("failed", "outcome_unknown")
    with Session(h.engine) as db:
        assert db.get(ExecutionActionIntent, intent_row(h, execution_id).id).effect_state == (
            "unknown"
        )
    assert (len(commands(h, execution_id)), target.writes) == (1, 1)
    # Nothing is resolved twice, and a terminal execution is not resolved at all.
    with pytest.raises(ExecutionError) as ended:
        h.resolve(owner, execution_id, "readback", resolution_id="r-2")
    assert ended.value.code == "conflict"


def test_effect_present_resolution_cites_recorded_evidence(resolving):
    """A lapsed write without `target_key` reads back absent (unqualified), so an owner
    is needed; the runtime's late journal report is then recorded as historical
    evidence, and the owner marks the effect present citing it."""
    from lotuspond.execution.action_reconciliation import recorded_evidence
    from lotuspond.execution.progression import _scope

    from .readback_fixtures import report

    h = resolving
    target = Target()
    package, documents = readback_package(idempotency="none", max_attempts=3)
    execution_id = h.reviewed("present", package, documents, active_ms=4000)
    lease = send(h, target, "lapse")
    until(h, lease.expires_at_ms)
    h.sweep()
    h.run2()
    sweeper = ExecutionSweeper(leases=h.leases, recovery=h.fence, readbacks=(h.budgets, Demand()))
    h.sweep(sweeper=sweeper)
    # The target's read model lags: it shows nothing yet.
    read(h, pending_readback(h, execution_id).id, {"present": False}, reserve=False)
    h.run2()
    assert execution(h, execution_id).state == "reconciliation_required"
    report(h, lease, "accepted")  # the journal's report, after the lapse
    with Session(h.engine) as db:
        recorded = recorded_evidence(
            db, _scope(execution(h, execution_id)), intent_row(h, execution_id).id
        )
    readback_result = readback_evidence(h, execution_id)
    [receipt_evidence] = [handle for handle in recorded if handle != readback_result]
    owner = admin(h)
    # The receipt confirmed the stop, yet a re-read that still sees nothing is not a
    # qualified absence: a receipt says the write happened, so the owner decides.
    h.resolve(owner, execution_id, "readback", resolution_id="r-reread")
    read(h, pending_readback(h, execution_id).id, {"present": False})
    h.run2()
    latest = [o for o in observations(h, execution_id) if o.source == "readback"][-1]
    assert (latest.result, latest.qualified) == ("effect_absent", False)
    assert execution(h, execution_id).state == "reconciliation_required"
    view = h.resolve(owner, execution_id, "effect_present", evidence=receipt_evidence)
    assert view.effect == "observed" and receipt_evidence in view.evidence
    assert view.receipt_status == "accepted"
    # A replay citing other evidence is not the same resolution.
    with pytest.raises(ExecutionError) as other:
        h.resolve(owner, execution_id, "effect_present", evidence=readback_result)
    assert other.value.code == "conflict"
    h.run2()
    assert dispatch_outcome(h, execution_id)[0] == "succeeded"
    assert execution(h, execution_id).state == "waiting"
    resolution = [o for o in observations(h, execution_id) if o.source == "resolution"][-1]
    assert (resolution.result, resolution.rule_id) == (
        "effect_present",
        "resolution-effect_present",
    )
    assert (len(commands(h, execution_id)), target.writes) == (1, 1)  # never a resend


def test_readback_resolution_is_one_more_bounded_readback(resolving):
    h = resolving
    execution_id, target = unknown_five_times(h, "reread")
    owner = admin(h)
    h.resolve(owner, execution_id, "readback", resolution_id="r" * 128)  # the longest ID
    h.resolve(owner, execution_id, "readback", resolution_id="r" * 128)  # a replay
    assert execution(h, execution_id).state == "running"
    attempt = pending_readback(h, execution_id)
    assert len(readbacks(h, execution_id)) == 6
    read(h, attempt.id, {"present": True})
    h.run2()
    assert dispatch_outcome(h, execution_id)[0] == "succeeded"
    assert len(commands(h, execution_id)) == 1


def test_compute_stopped_can_qualify_a_later_absence(resolving):
    """No `target_key`, a lapse (no confirmed stop), then absent: unqualified, so an
    owner waits. The owner's confirmed stop makes the next read-back's absence
    qualified, and the step fails `not_applied` without a write."""
    h = resolving
    target = Target()
    package, documents = readback_package(idempotency="none", max_attempts=3)
    execution_id = h.reviewed("stopped", package, documents, active_ms=4000)
    lease = send(h, target, "lapse")
    target.records.clear()
    target.writes = 0  # the write was lost before the target applied it
    until(h, lease.expires_at_ms)
    assert h.sweep().lapsed_action_leases == 1
    h.run2()
    sweeper = ExecutionSweeper(leases=h.leases, recovery=h.fence, readbacks=(h.budgets, Demand()))
    h.sweep(sweeper=sweeper)
    read(h, pending_readback(h, execution_id).id, {"present": False}, reserve=False)
    h.run2()
    assert execution(h, execution_id).state == "reconciliation_required"
    assert reservation(h, lease.attempt_id).compute_stopped_at_ms is None
    with Session(h.engine) as db:
        before = db.scalar(select(ExecutionCapacity)).active
    h.resolve(admin(h), execution_id, "compute_stopped")
    assert reservation(h, lease.attempt_id).compute_stopped_at_ms is not None
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionCapacity)).active == before - 1  # the slot returns
    read(h, pending_readback(h, execution_id).id, {"present": False})
    h.run2()
    latest = [o for o in observations(h, execution_id) if o.source == "readback"][-1]
    assert (latest.result, latest.qualified) == ("effect_absent", True)
    state, value = dispatch_outcome(h, execution_id)
    assert state == "failed" and value["failure"] == "not_applied"
    assert (len(commands(h, execution_id)), target.writes) == (1, 0)


def test_containment_needs_a_confirmed_stop(resolving):
    """A lapsed write read back present is settled, but its compute stop is still
    unconfirmed: only a `compute_stopped` resolution is accepted while it runs."""
    h = resolving
    target = Target()
    package, documents = readback_package()
    execution_id = h.reviewed("contain", package, documents, active_ms=4000)
    lease = send(h, target, "lapse")
    until(h, lease.expires_at_ms)
    h.sweep()
    h.run2()
    sweeper = ExecutionSweeper(leases=h.leases, recovery=h.fence, readbacks=(h.budgets, Demand()))
    h.sweep(sweeper=sweeper)
    read(h, pending_readback(h, execution_id).id, {"present": True}, reserve=False)
    h.run2()
    assert dispatch_outcome(h, execution_id)[0] == "succeeded"
    owner = admin(h, "owner")
    for kind in ("readback", "close_unknown"):
        with pytest.raises(ExecutionError) as refused:
            h.resolve(owner, execution_id, kind, resolution_id="r-" + kind)
        assert refused.value.code == "conflict"
    h.resolve(owner, execution_id, "compute_stopped")
    assert reservation(h, lease.attempt_id).compute_stopped_at_ms is not None
    assert len(readbacks(h, execution_id)) == 1  # it asked for no read-back
    with pytest.raises(ExecutionError):
        h.resolve(owner, execution_id, "compute_stopped", resolution_id="r-again")


def test_a_superseded_write_can_still_be_contained(resolving):
    """A lapsed `target_key` write reads back absent and is resent, but its stop was
    never reported. An owner can still confirm that first write's stop although a
    later attempt is the intent's latest (this harness's one slot needs it back)."""
    from .dispatch_fixtures import reserve

    h = resolving
    target = Target()
    package, documents = readback_package(max_attempts=2)
    execution_id = h.reviewed("superseded", package, documents, active_ms=4000)
    first = send(h, target, "lapse")
    target.records.clear()
    target.writes = 0  # lost before the target applied it
    until(h, first.expires_at_ms)
    h.sweep()
    h.run2()
    sweeper = ExecutionSweeper(leases=h.leases, recovery=h.fence, readbacks=(h.budgets, Demand()))
    h.sweep(sweeper=sweeper)
    read(h, pending_readback(h, execution_id).id, {"present": False}, reserve=False)
    h.run2()
    from sqlalchemy import select as query

    from lotuspond.models import ExecutionAttempt

    with Session(h.engine) as db:
        second = db.scalar(
            query(ExecutionAttempt).where(
                ExecutionAttempt.execution_id == execution_id,
                ExecutionAttempt.ordinal == 2,
                ExecutionAttempt.parent_attempt_id.is_(None),
            )
        )
    assert second is not None and second.state == "reserved"
    assert reservation(h, first.attempt_id).compute_stopped_at_ms is None
    h.resolve(admin(h, "owner"), execution_id, "compute_stopped")
    assert reservation(h, first.attempt_id).compute_stopped_at_ms is not None
    reserve(h, second.id, 4000)
    send(h, target, "applied")
    h.run2()
    assert dispatch_outcome(h, execution_id)[0] == "succeeded"
    assert (len(commands(h, execution_id)), target.writes) == (2, 1)
    assert len(set(target.keys)) == 1
