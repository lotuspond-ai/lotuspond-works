"""Admission uses real PostgreSQL transactions and independently configured providers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.admission import AdmissionService
from lotuspond.execution.artifacts import WORKSPACE_ACCESS_POLICY, ArtifactPolicy, ArtifactStore
from lotuspond.execution.contracts import AdmissionRequest
from lotuspond.execution.errors import ExecutionError
from lotuspond.models import (
    Execution,
    ExecutionArtifact,
    ExecutionCapacity,
    ExecutionOutbox,
    ExecutionRequest,
    Membership,
)

from .test_artifacts import FixtureKeys
from .test_budgets import budget_harness as budget_harness
from .test_publication import harness as harness


@pytest.fixture
def admission(budget_harness):
    h = budget_harness
    from sqlalchemy import text

    with Session(h.engine) as db:
        schema = db.scalar(text("SELECT current_schema()"))
        assert schema.startswith("test_execution_")
        print(f"Owned schema creation observed: {schema}")
    h.keys = FixtureKeys()
    h.store = ArtifactStore(h.keys, None, test_mode=True)
    h.policy = ArtifactPolicy(
        classification="customer-private",
        source_id=None,
        source_version=None,
        retain_until_ms=9007199254740991,
        access_policy_id=WORKSPACE_ACCESS_POLICY,
        key_id="fixture",
    )
    h.service = AdmissionService(
        budgets=h.budgets,
        artifacts=h.store,
        artifact_policy=h.policy,
        interpreter_id="lotuspond.execution-interpreter.v1",
    )
    h.request = AdmissionRequest(deployment_revision_id=h.revision_id, input=None)
    yield h
    h.store.close()


def counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (Execution, ExecutionArtifact, ExecutionRequest, ExecutionOutbox)
    ) + (
        db.scalar(select(ExecutionCapacity.pending)),
        db.scalar(select(ExecutionCapacity.next_admission_sequence)),
    )


def admit(h, key="same", request=None):
    with Session(h.engine) as db, db.begin():
        return h.service.admit_execution(db, h.actor, request or h.request, key)


def test_admission_retry_after_commit_returns_one_execution(admission):
    h = admission
    barrier = Barrier(2)

    def invoke(_):
        barrier.wait(timeout=5)
        return admit(h)

    with ThreadPoolExecutor(2) as pool:
        first, repeated = list(pool.map(invoke, range(2)))
    assert first.id == repeated.id == admit(h).id
    with Session(h.engine) as db:
        execution_count = db.scalar(select(func.count()).select_from(Execution))
        start_outbox_count = db.scalar(select(func.count()).select_from(ExecutionOutbox))
        assert execution_count == start_outbox_count == 1
        row = db.get(Execution, first.id)
        assert row.admission_sequence == 1
        assert db.scalar(select(ExecutionCapacity.pending)) == 1
        assert row.input_sha256 == db.get(ExecutionArtifact, row.input_artifact_id).sha256


def test_conflicting_key_refused_after_reauthorization(admission):
    h = admission
    admit(h)
    changed = h.request.model_copy(update={"input": "PRIVATE_CONFLICT"})
    with pytest.raises(ExecutionError, match="conflict"):
        admit(h, request=changed)
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Membership)).role = "viewer"
    with pytest.raises(ExecutionError, match="forbidden"):
        admit(h, request=changed)


@pytest.mark.parametrize(
    "failure", ["artifact", "reservation", "execution", "outbox", "before_commit"]
)
def test_admission_failure_rolls_back_every_record(admission, monkeypatch, failure):
    from sqlalchemy import event

    h = admission
    with Session(h.engine) as db:
        rows_before_request = counts(db)
    if failure in {"artifact", "reservation"}:
        owner, name = (h.store, "put") if failure == "artifact" else (h.budgets, "_count_admission")
        original = getattr(owner, name)

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ExecutionError("authority_unavailable")

        monkeypatch.setattr(owner, name, fail)

    def after_flush(db, context):
        if any(
            isinstance(row, Execution if failure == "execution" else ExecutionOutbox)
            for row in db.new
        ):
            raise ExecutionError("authority_unavailable")

    with Session(h.engine) as db:
        if failure in {"execution", "outbox"}:
            event.listen(db, "after_flush", after_flush)
        with pytest.raises(ExecutionError), db.begin():
            h.service.admit_execution(db, h.actor, h.request, "rollback")
            if failure == "before_commit":
                raise ExecutionError("authority_unavailable")
    with Session(h.engine) as db:
        rows_after_rollback = counts(db)
        assert rows_after_rollback == rows_before_request


def test_namespace_preserves_full_keys_and_cannot_collide_with_wait_capacity(admission):
    h = admission
    for key in ("capacity.wait:00000000-0000-4000-8000-000000000001", "x" * 128, "x"):
        assert admit(h, key).id == admit(h, key).id
    with Session(h.engine) as db:
        keys = list(db.scalars(select(ExecutionRequest.request_key)))
        assert len(set(keys)) == 3
        assert all(k.startswith("admission:") and len(k) == 74 for k in keys)
        assert sorted(db.scalars(select(Execution.admission_sequence))) == [1, 2, 3]


def test_input_is_typed_and_missing_key_provider_denies(admission):
    h = admission
    with pytest.raises(ExecutionError, match="invalid_request"):
        admit(h, request=h.request.model_copy(update={"input": False}))
    h.store.keys = None
    with pytest.raises(ExecutionError, match="key_unavailable"):
        admit(h)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(Execution)) == 0


def key_actor(h, scopes):
    from datetime import timedelta

    from lotuspond.execution.authorization import authorize_execution
    from lotuspond.models import ApiKey, now
    from lotuspond.security import Principal

    with Session(h.engine) as db, db.begin():
        key = ApiKey(
            workspace_id=h.scope.workspace_id,
            name="test",
            prefix="test",
            token_hash="2" * 64,
            scopes=scopes,
            expires_at=now() + timedelta(days=1),
            created_by_user_id=h.actor.user_id,
        )
        db.add(key)
        db.flush()
        return authorize_execution(
            db,
            Principal(key.id, h.scope.workspace_id, "service"),
            h.scope,
            "signal" if "execution_signal" in scopes else "execute",
        )


def wait_for_database_time(h, target):
    from threading import Event
    from time import monotonic

    from lotuspond.fleet.authority import database_now_ms

    end = monotonic() + 5
    with Session(h.engine) as observer:
        while database_now_ms(observer) <= target and monotonic() < end:
            Event().wait(0.01)
        assert database_now_ms(observer) > target


def test_equal_replay_does_not_reserve_fresh_capacity_or_license(admission):
    h = admission
    first = admit(h)
    with Session(h.engine) as db, db.begin():
        db.scalar(select(ExecutionCapacity)).pending_limit = 1
    h.provider.allowed = False
    assert admit(h).id == first.id
    h.provider.allowed = True
    with pytest.raises(ExecutionError, match="capacity_unavailable"):
        admit(h, "new")


def test_distinct_concurrent_admissions_receive_atomic_fifo_sequence(admission):
    h = admission
    barrier = Barrier(3)

    def invoke(i):
        barrier.wait(timeout=5)
        return admit(h, str(i))

    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(invoke, range(3)))
    assert len({r.id for r in results}) == 3
    with Session(h.engine) as db:
        assert sorted(db.scalars(select(Execution.admission_sequence))) == [1, 2, 3]
        assert db.scalar(select(ExecutionCapacity.next_admission_sequence)) == 4


def test_activation_serializes_new_admission_but_keeps_old_replay(admission):
    from threading import Event
    from time import monotonic

    from sqlalchemy import text

    from lotuspond.execution.contracts import DeploymentConfig, DeploymentRequest
    from lotuspond.execution.deployments import create_deployment, set_deployment_status
    from lotuspond.models import ExecutionDeploymentRevision

    h = admission
    first = admit(h)
    with Session(h.engine) as db, db.begin():
        old = db.get(ExecutionDeploymentRevision, h.revision_id)
        config = DeploymentConfig.model_validate_json(old.canonical_configuration)
        request = DeploymentRequest(
            deployment_id=old.deployment_id,
            publication_id=old.publication_id,
            pool_id=old.pool_id,
            release_id=None,
            configuration=config.model_copy(
                update={"limits": config.limits.model_copy(update={"pending_capacity": 9})}
            ),
        )
        second = create_deployment(db, h.actor, request, "revision-2", dependencies=h.dependencies)
    started, pids = Event(), []

    def waiting_admission():
        with Session(h.engine) as db, db.begin():
            pids.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            return h.service.admit_execution(db, h.actor, h.request, "old-new")

    with ThreadPoolExecutor(1) as executor:
        with Session(h.engine) as blocker, blocker.begin():
            set_deployment_status(
                blocker,
                h.actor,
                second.id,
                second.revision_id,
                "enabled",
                "activate-2",
                dependencies=h.dependencies,
            )
            blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
            future = executor.submit(waiting_admission)
            assert started.wait(5)
            with h.engine.connect() as observer:
                end = monotonic() + 5
                while monotonic() < end:
                    if observer.scalar(
                        text("SELECT :blocker = ANY(pg_blocking_pids(:pid))"),
                        {"blocker": blocker_pid, "pid": pids[0]},
                    ):
                        break
                else:
                    pytest.fail("Admission did not wait on activation")
        with pytest.raises(ExecutionError, match="deployment_unavailable"):
            future.result(timeout=10)
    assert admit(h).id == first.id
    assert (
        admit(
            h,
            "new-revision",
            h.request.model_copy(update={"deployment_revision_id": second.revision_id}),
        ).id
        != first.id
    )


def test_admission_takes_no_installation_cursor(admission):
    """Finding 11: admission changes only its environment's counters, never the cursor."""
    from .cursor_checks import without_cursor

    h = admission
    view = without_cursor(h.engine, lambda: admit(h, "no-cursor"))
    assert view.state == "pending"


def test_admission_takes_the_workspace_row_before_customer_and_capacity_rows(admission):
    from threading import Event
    from time import monotonic

    from sqlalchemy import text

    from lotuspond.models import User, Workspace

    h = admission
    started, pids = Event(), []

    def blocked():
        with Session(h.engine) as db, db.begin():
            pids.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            return h.service.admit_execution(db, h.actor, h.request, "lock-order")

    with ThreadPoolExecutor(1) as executor:
        with Session(h.engine) as blocker, blocker.begin():
            blocker.scalar(
                select(Workspace).where(Workspace.id == h.scope.workspace_id).with_for_update()
            )
            future = executor.submit(blocked)
            assert started.wait(5)
            with h.engine.connect() as observer:
                end = monotonic() + 5
                while monotonic() < end:
                    if observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pids[0]}
                    ):
                        break
                else:
                    pytest.fail("Admission did not wait on its workspace row")
            # NOWAIT proves the admission has acquired neither account nor capacity rows.
            blocker.scalar(
                select(User).where(User.id == h.actor.user_id).with_for_update(nowait=True)
            )
            blocker.scalar(select(ExecutionCapacity).with_for_update(nowait=True))
        assert future.result(timeout=10).state == "pending"


@pytest.mark.parametrize("expiry", ["deadline", "key", "retention"])
def test_admission_rechecks_expiry_after_real_provider_wait(admission, expiry):
    from datetime import UTC, datetime
    from threading import Event

    from lotuspond.fleet.authority import database_now_ms
    from lotuspond.models import ApiKey

    h = admission
    entered, release = Event(), Event()
    with Session(h.engine) as db:
        target = database_now_ms(db) + 1500
    if expiry == "deadline":
        h.provider.limits = h.provider.limits.model_copy(update={"elapsed_seconds": 1})
    elif expiry == "key":
        h.actor = key_actor(h, ["execute"])
        with Session(h.engine) as db, db.begin():
            db.get(ApiKey, h.actor.api_key_id).expires_at = datetime.fromtimestamp(
                target / 1000, UTC
            )
    else:
        h.service.policy = h.policy.model_copy(update={"retain_until_ms": target})
    original_key = h.keys.key

    def blocking_key(scope, key_id):
        entered.set()
        assert release.wait(5)
        return original_key(scope, key_id)

    if expiry == "deadline":
        # The key is looked up before admission time is taken (finding 11, part 6);
        # the final guard's tenant lookup (the second) still waits after it.
        original_tenant, calls = h.budgets.tenant_policy.current, []

        def blocking_tenant(scope, now_ms):
            calls.append(None)
            if len(calls) == 2:
                entered.set()
                assert release.wait(5)
            return original_tenant(scope, now_ms)

        h.budgets.tenant_policy.current = blocking_tenant
    else:
        h.keys.key = blocking_key
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(admit, h)
        try:
            assert entered.wait(5)
            wait_for_database_time(h, target + 100)
        finally:
            release.set()
        with pytest.raises(ExecutionError, match="forbidden"):
            future.result(timeout=10)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(Execution)) == 0
        assert db.scalar(select(ExecutionCapacity.pending)) == 0


@pytest.mark.parametrize("missing", ["installation", "environment"])
def test_explicitly_provisioned_capacity_required(admission, missing):
    from lotuspond.models import ExecutionSchedulerCursor

    h = admission
    with Session(h.engine) as db, db.begin():
        model = ExecutionSchedulerCursor if missing == "installation" else ExecutionCapacity
        db.delete(db.scalar(select(model)))
    with pytest.raises(ExecutionError, match="capacity_unavailable"):
        admit(h)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(Execution)) == 0
        assert db.scalar(select(func.count()).select_from(ExecutionOutbox)) == 0


def test_artifact_retention_rechecked_after_later_budget_provider_wait(admission):
    from threading import Event

    from lotuspond.fleet.authority import database_now_ms

    h = admission
    with Session(h.engine) as db:
        target = database_now_ms(db) + 1500
    h.service.policy = h.policy.model_copy(update={"retain_until_ms": target})
    entered, release = Event(), Event()
    original = h.budgets.tenant_policy.current
    calls = 0

    def blocking(scope, now_ms):
        nonlocal calls
        calls += 1
        if calls == 2:  # initial limits captured; input artifact has already flushed
            entered.set()
            assert release.wait(5)
        return original(scope, now_ms)

    h.budgets.tenant_policy.current = blocking
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(admit, h)
        try:
            assert entered.wait(5)
            wait_for_database_time(h, target)
        finally:
            release.set()
        with pytest.raises(ExecutionError, match="forbidden"):
            future.result(timeout=10)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(Execution)) == 0
        assert db.scalar(select(ExecutionCapacity.pending)) == 0


def test_external_key_cannot_reuse_internal_wait_reservation_request(admission):
    from lotuspond.models import new_id

    h = admission
    first = admit(h, "first")
    adversarial = "capacity.wait:" + new_id()
    with Session(h.engine) as db, db.begin():
        db.add(
            ExecutionRequest(
                **h.scope.model_dump(),
                execution_id=first.id,
                request_key=adversarial,
                digest="d" * 64,
            )
        )
    second = admit(h, adversarial)
    assert second.id != first.id
    assert admit(h, adversarial).id == second.id
    with Session(h.engine) as db:
        internal = db.scalar(
            select(ExecutionRequest).where(ExecutionRequest.request_key == adversarial)
        )
        assert internal.execution_id == first.id and internal.digest == "d" * 64
        assert db.scalar(select(ExecutionCapacity.pending)) == 2


def test_default_composition_is_unavailable():
    from lotuspond.execution.admission import admit_execution
    from lotuspond.execution.inbox import accept_signal

    for call in (
        lambda: admit_execution(None, None, None, "key"),
        lambda: accept_signal(None, None, None, None),
    ):
        with pytest.raises(ExecutionError, match="authority_unavailable"):
            call()


def test_admission_snapshots_exact_input_before_provider_waits(admission):
    from lotuspond_sdk.activity import ArtifactHandle

    from lotuspond.execution.deployments import create_deployment, set_deployment_status

    from .test_deployments import deployment_request
    from .test_publication import example

    h = admission
    raw = example("minimal")
    raw["package_id"] = "mutable-admission"
    raw["input_schema"] = {"kind": "array", "max_items": 2, "items": {"kind": "null"}}
    with Session(h.engine) as db, db.begin():
        pub = h.publish(db, raw, request_key="mutable-publication")
        view = create_deployment(
            db,
            h.actor,
            deployment_request(h, pub.id),
            "mutable-deployment",
            dependencies=h.dependencies,
        )
        set_deployment_status(
            db,
            h.actor,
            view.id,
            view.revision_id,
            "enabled",
            "mutable-enable",
            dependencies=h.dependencies,
        )
    h.request = AdmissionRequest(deployment_revision_id=view.revision_id, input=[])
    original, calls = h.provider.current, 0

    def mutating(scope, now_ms):
        nonlocal calls
        calls += 1
        if calls == 1:
            h.request.input.append(None)
        return original(scope, now_ms)

    h.provider.current = mutating
    result = admit(h)
    with Session(h.engine) as db, db.begin():
        row = db.get(Execution, result.id)
        assert (
            h.store.get(
                db,
                h.actor,
                ArtifactHandle(
                    id=row.input_artifact_id,
                    version=row.input_artifact_version,
                    sha256=row.input_sha256,
                ),
            )
            == b"[]"
        )


def numeric_schema(kind, nested):
    scalar = {"kind": kind}
    return (
        {"kind": "object", "fields": [{"name": "value", "schema": scalar, "required": True}]}
        if nested
        else scalar
    )


def numeric_value(value, nested):
    return {"value": value} if nested else value


@pytest.fixture
def typed_admission(admission, request):
    from lotuspond.execution.deployments import create_deployment, set_deployment_status

    from .test_deployments import deployment_request
    from .test_publication import example

    h = admission
    kind, h.nested = request.param
    raw = example("minimal")
    raw["package_id"] = "typed-admission"
    raw["input_schema"] = numeric_schema(kind, h.nested)
    with Session(h.engine) as db, db.begin():
        pub = h.publish(db, raw, request_key="typed-publication")
        view = create_deployment(
            db,
            h.actor,
            deployment_request(h, pub.id),
            "typed-deployment",
            dependencies=h.dependencies,
        )
        set_deployment_status(
            db,
            h.actor,
            view.id,
            view.revision_id,
            "enabled",
            "typed-enable",
            dependencies=h.dependencies,
        )
    h.request = AdmissionRequest(
        deployment_revision_id=view.revision_id, input=numeric_value(0, h.nested)
    )
    return h


@pytest.mark.parametrize("typed_admission", [("integer", False), ("integer", True)], indirect=True)
@pytest.mark.parametrize("invalid", [1.0, -0.0], ids=["integral-float", "negative-zero"])
@pytest.mark.parametrize("replay", [False, True], ids=["new", "equal-replay"])
def test_admission_preserves_strict_integer_domain(typed_admission, invalid, replay):
    from lotuspond.execution.contracts import canonical_value

    h = typed_admission
    valid = h.request.model_copy(update={"input": numeric_value(int(invalid), h.nested)})
    first = admit(h, "valid", valid)
    assert admit(h, "valid", valid).id == first.id
    bad = h.request.model_copy(update={"input": numeric_value(invalid, h.nested)})
    assert canonical_value(valid.input) == canonical_value(bad.input)
    with Session(h.engine) as db:
        before = counts(db)
    with pytest.raises(ExecutionError, match="invalid_request"):
        admit(h, "valid" if replay else "new-invalid", bad)
    with Session(h.engine) as db:
        assert counts(db) == before
        assert db.scalar(select(ExecutionCapacity.pending)) == 1


@pytest.mark.parametrize("typed_admission", [("number", False), ("number", True)], indirect=True)
def test_admission_accepts_declared_number_domain(typed_admission):
    h = typed_admission
    for index, value in enumerate((1.0, -0.0, 1.25)):
        request = h.request.model_copy(update={"input": numeric_value(value, h.nested)})
        first = admit(h, f"number-{index}", request)
        assert admit(h, f"number-{index}", request).id == first.id
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionCapacity.pending)) == 3


def test_admission_looks_up_before_its_first_lock_and_loads_the_deployment_twice(
    admission, monkeypatch
):
    """Finding 11, part 6: a tenant's workspace row is not held while lookups wait.

    The grants, the publisher snapshot and the artifact key are looked up before the
    first row lock; under the locks the deployment is loaded once, then once more by
    the final guard, which repeats and compares every lookup (it was four of each).
    """
    from sqlalchemy import text

    from lotuspond.execution import budgets as budgets_module

    h = admission
    calls, db = [], Session(h.engine)

    def row_locks():
        return db.scalar(
            text(
                "SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid()"
                " AND locktype = 'relation' AND mode IN ('RowShareLock', 'RowExclusiveLock')"
            )
        )

    def watched(name, function):
        def wrapper(*args, **kwargs):
            calls.append((name, row_locks()))
            return function(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(h.budgets, "_grants", watched("grants", h.budgets._grants))
    dependencies = type(h.budgets.deployments)  # a frozen dataclass: patch its class
    monkeypatch.setattr(dependencies, "current", watched("snapshot", dependencies.current))
    monkeypatch.setattr(h.store, "_key", watched("key", h.store._key))
    monkeypatch.setattr(
        budgets_module, "load_deployment", watched("load", budgets_module.load_deployment)
    )
    with db, db.begin():
        h.service.admit_execution(db, h.actor, h.request, "looked-up")
    assert calls[:3] == [("snapshot", 0), ("grants", 0), ("key", 0)]
    names = [name for name, _ in calls]
    assert names.count("load") == 2
    assert names.count("grants") == 2
    assert names.count("key") == 1


def test_a_refused_early_lookup_refuses_in_the_usual_order(admission, monkeypatch):
    """A lookup refused before the first lock discloses nothing before authorization."""
    h = admission
    original = h.budgets._grants
    refusing = {"on": True}

    def grants(db, scope):
        if refusing["on"]:
            raise ExecutionError("entitlement_unavailable")
        return original(db, scope)

    monkeypatch.setattr(h.budgets, "_grants", grants)
    with Session(h.engine) as db, db.begin():
        membership = db.scalar(select(Membership))
        role, membership.role = membership.role, "viewer"
    with pytest.raises(ExecutionError, match="forbidden"):
        admit(h, key="unauthorized")
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Membership)).role = role
    with pytest.raises(ExecutionError, match="entitlement_unavailable"):
        admit(h, key="unentitled")
    refusing["on"] = False
    assert admit(h, key="entitled").state == "pending"
