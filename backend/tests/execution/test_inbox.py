"""Durable signal acceptance is distinct from completing a persisted wait."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.contracts import DependencyBinding, SignalRequest
from lotuspond.execution.deployments import create_deployment, set_deployment_status
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.inbox import InboxService
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    Execution,
    ExecutionInbox,
    ExecutionOutbox,
    ExecutionStepInstance,
    ExecutionWait,
    new_id,
)

from .test_admission import admission as admission
from .test_admission import admit, numeric_schema, numeric_value
from .test_budgets import budget_harness as budget_harness
from .test_deployments import deployment_request, form_package
from .test_publication import harness as harness


@pytest.fixture
def signals(admission, request):
    h = admission
    raw, policy = form_package()
    raw["package_id"] = "signal-fixture"
    options = getattr(request, "param", "external")
    options = options if isinstance(options, dict) else {"kind": options}
    human = options["kind"] == "human"
    original_steps = raw["body"]["steps"]
    raw["body"]["steps"] = [
        dict(
            id="form",
            kind="external_wait",
            assignment_policy_ref="admins",
            expiry_seconds=30,
            signal_schema={"kind": "null"},
            correlation=dict(
                kind="literal", schema={"kind": "string", "max_length": 30}, value="fixture"
            ),
        )
    ]
    private = getattr(request, "param", "external") == "private"
    mutable = getattr(request, "param", "external") == "mutable"
    if mutable:
        raw["body"]["steps"][0]["signal_schema"] = {
            "kind": "array",
            "max_items": 2,
            "items": {"kind": "null"},
        }
    if private:
        raw["body"]["steps"][0]["signal_schema"] = {"kind": "string", "max_length": 100}
    if human:
        raw["body"]["steps"] = original_steps
    if "schema" in options:
        field = "form_schema" if human else "signal_schema"
        raw["body"]["steps"][0][field] = options["schema"]
        h.nested = options["nested"]
    with Session(h.engine) as db, db.begin():
        dep = h.publish(db, policy, request_key="signal-policy")
        pub = h.publish(db, raw, (dep.id,), request_key="signal-package")
        request = deployment_request(h, pub.id)
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(
                    update={"bindings": (DependencyBinding(dependency=raw["policies"][0]["ref"]),)}
                )
            }
        )
        view = create_deployment(db, h.actor, request, "signals", dependencies=h.dependencies)
        set_deployment_status(
            db,
            h.actor,
            view.id,
            view.revision_id,
            "enabled",
            "signal-enable",
            dependencies=h.dependencies,
        )
        h.request = h.request.model_copy(update={"deployment_revision_id": view.revision_id})
    h.execution_id = admit(h).id
    with Session(h.engine) as db, db.begin():
        db.get(Execution, h.execution_id).state = "waiting"
        step = ExecutionStepInstance(
            **h.scope.model_dump(),
            execution_id=h.execution_id,
            node_id="form",
            occurrence_path="form",
        )
        db.add(step)
        db.flush()
        wait = ExecutionWait(
            **h.scope.model_dump(),
            execution_id=h.execution_id,
            step_instance_id=step.id,
            wait_key="fixture",
            deadline_ms=database_now_ms(db) + 30000,
        )
        db.add(wait)
        db.flush()
        h.wait_id = wait.id
    h.inbox = InboxService(budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy)
    h.signal = SignalRequest(
        signal_id="signal",
        wait_id=h.wait_id,
        payload="PRIVATE_SIGNAL_SENTINEL" if private else ([] if mutable else None),
    )
    return h


def accept(h, request=None):
    with Session(h.engine) as db, db.begin():
        return h.inbox.accept_signal(db, h.actor, h.execution_id, request or h.signal)


def test_signal_commit_before_ack_and_wait_binding(signals):
    h = signals
    with Session(h.engine) as db:
        with db.begin():
            first = h.inbox.accept_signal(db, h.actor, h.execution_id, h.signal)
            with Session(h.engine) as observer:
                assert observer.scalar(select(func.count()).select_from(ExecutionInbox)) == 0
    assert first == accept(h)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExecutionOutbox)
                .where(ExecutionOutbox.kind == "engine_signal")
            )
            == 1
        )
        assert db.get(ExecutionWait, h.wait_id).state == "open"
    with pytest.raises(ExecutionError, match="conflict"):
        accept(h, h.signal.model_copy(update={"payload": "PRIVATE_SIGNAL"}))


@pytest.mark.parametrize("invalid", ["early", "wrong_wait", "expired", "cancelled"])
def test_unready_signal_cannot_satisfy_wait(signals, invalid):
    h = signals
    with Session(h.engine) as db, db.begin():
        if invalid == "early":
            db.get(Execution, h.execution_id).state = "pending"
        elif invalid == "expired":
            db.get(ExecutionWait, h.wait_id).deadline_ms = database_now_ms(db) - 1
        elif invalid == "cancelled":
            db.get(Execution, h.execution_id).requested_state = "cancelled"
    request = (
        h.signal.model_copy(update={"wait_id": new_id()}) if invalid == "wrong_wait" else h.signal
    )
    with pytest.raises(ExecutionError):
        accept(h, request)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 0
        assert db.get(ExecutionWait, h.wait_id).state == "open"


def test_signal_only_key_persists_encrypted_payload_without_execute(signals):
    from lotuspond.execution.authorization import reauthorize
    from lotuspond.models import ExecutionArtifact

    from .test_admission import key_actor

    h = signals
    h.actor = key_actor(h, ["execution_signal"])
    receipt = accept(h)
    with Session(h.engine) as db, db.begin():
        with pytest.raises(ExecutionError, match="forbidden"):
            reauthorize(db, h.actor, "execute")
        row = db.get(ExecutionInbox, receipt.id)
        artifact = db.get(ExecutionArtifact, row.payload_artifact_id)
        assert artifact.ciphertext != b"null" and len(artifact.nonce) == 12
        assert artifact.access_policy_id == "workspace-current-membership-v1"
    with Session(h.engine) as db, db.begin():
        from lotuspond.models import ApiKey, now

        db.get(ApiKey, h.actor.api_key_id).revoked_at = now()
    with pytest.raises(ExecutionError, match="forbidden"):
        accept(h)


@pytest.mark.parametrize("signals", ["human"], indirect=True)
def test_human_form_requires_current_human_session(signals):
    from .test_admission import key_actor

    h = signals
    human = h.actor
    h.actor = key_actor(h, ["execution_signal", "execute"])
    with pytest.raises(ExecutionError, match="forbidden"):
        accept(h)
    h.actor = human
    assert accept(h).status == "pending"


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_assignment_policy_rechecked_for_current_role(signals, role):
    from lotuspond.models import Membership

    h = signals
    with Session(h.engine) as db, db.begin():
        db.scalar(select(Membership)).role = role
    with pytest.raises(ExecutionError, match="forbidden"):
        accept(h)


@pytest.mark.parametrize("failure", ["artifact", "inbox", "outbox", "before_commit"])
def test_signal_rollback_preserves_no_partial_message(signals, monkeypatch, failure):
    from sqlalchemy import event

    from .test_admission import counts

    h = signals
    with Session(h.engine) as db:
        before = counts(db)
    if failure == "artifact":
        original = h.store._put_signal

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ExecutionError("authority_unavailable")

        monkeypatch.setattr(h.store, "_put_signal", fail)

    def fail_flush(db, context):
        if any(
            isinstance(row, ExecutionInbox if failure == "inbox" else ExecutionOutbox)
            for row in db.new
        ):
            raise ExecutionError("authority_unavailable")

    with Session(h.engine) as db:
        if failure in {"inbox", "outbox"}:
            event.listen(db, "after_flush", fail_flush)
        with pytest.raises(ExecutionError), db.begin():
            h.inbox.accept_signal(db, h.actor, h.execution_id, h.signal)
            if failure == "before_commit":
                raise ExecutionError("authority_unavailable")
    with Session(h.engine) as db:
        assert counts(db) == before
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 0


def test_concurrent_duplicate_signal_creates_one_durable_inbox(signals):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    h = signals
    barrier = Barrier(2)

    def invoke(_):
        barrier.wait(timeout=5)
        return accept(h)

    with ThreadPoolExecutor(2) as executor:
        first, repeated = list(executor.map(invoke, range(2)))
    assert first == repeated
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 1


@pytest.mark.parametrize("expiry", ["wait", "key"])
def test_signal_provider_wait_rechecks_absolute_deadline_and_key(signals, expiry):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime
    from threading import Event

    from lotuspond.models import ApiKey

    from .test_admission import key_actor, wait_for_database_time

    h = signals
    if expiry == "key":
        h.actor = key_actor(h, ["execution_signal"])
    with Session(h.engine) as db, db.begin():
        target = database_now_ms(db) + 1500
        if expiry == "wait":
            db.get(ExecutionWait, h.wait_id).deadline_ms = target
        else:
            db.get(ApiKey, h.actor.api_key_id).expires_at = datetime.fromtimestamp(
                target / 1000, UTC
            )
    entered, release = Event(), Event()
    original = h.keys.key

    def blocking(scope, key_id):
        entered.set()
        assert release.wait(5)
        return original(scope, key_id)

    h.keys.key = blocking
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(accept, h)
        try:
            assert entered.wait(5)
            wait_for_database_time(h, target)
        finally:
            release.set()
        with pytest.raises(ExecutionError, match="forbidden"):
            future.result(timeout=10)
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 0


def test_open_wait_in_parallel_running_execution_accepts_signal(signals):
    h = signals
    with Session(h.engine) as db, db.begin():
        db.get(Execution, h.execution_id).state = "running"
    assert accept(h).status == "pending"


def test_payload_type_and_late_equal_replay_do_not_complete_wait(signals):
    h = signals
    with pytest.raises(ExecutionError, match="invalid_request"):
        accept(h, h.signal.model_copy(update={"payload": False}))
    accept(h)
    with Session(h.engine) as db, db.begin():
        db.get(ExecutionWait, h.wait_id).deadline_ms = database_now_ms(db) - 1
    with pytest.raises(ExecutionError, match="forbidden"):
        accept(h)
    with Session(h.engine) as db:
        assert db.get(ExecutionWait, h.wait_id).state == "open"
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 1


def test_an_equal_replay_after_the_signal_took_effect_returns_its_receipt(signals):
    """Task 13: the answer was lost after commit and progression consumed the signal;
    the client's equal retry learns it was accepted, instead of a refusal."""
    h = signals
    receipt = accept(h)
    with Session(h.engine) as db, db.begin():  # progression consumed it
        db.get(ExecutionInbox, receipt.id).status = "delivered"
        db.get(ExecutionWait, h.wait_id).state = "satisfied"
    again = accept(h)
    assert (again.id, again.status) == (receipt.id, "delivered")
    with pytest.raises(ExecutionError, match="conflict"):  # a changed payload still conflicts
        accept(h, h.signal.model_copy(update={"payload": {"different": True}}))
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 1


@pytest.mark.parametrize("signals", ["private"], indirect=True)
def test_outbox_contains_only_opaque_identity_and_digest_for_private_signal(signals):
    from lotuspond_sdk.activity import ArtifactHandle

    from lotuspond.execution.outbox import lease_outbox
    from lotuspond.models import ExecutionArtifact

    h = signals
    receipt = accept(h)
    with Session(h.engine) as db, db.begin():
        items = lease_outbox(db, "dispatcher")
        (signal,) = [item for item in items if item.kind == "signal"]
        row = db.get(ExecutionInbox, receipt.id)
        assert signal.opaque_token == row.id and signal.digest == row.digest
        assert "PRIVATE_SIGNAL_SENTINEL" not in str(items)
        artifact = db.get(ExecutionArtifact, row.payload_artifact_id)
        assert b"PRIVATE_SIGNAL_SENTINEL" not in artifact.ciphertext
        assert (
            h.store.get(
                db,
                h.actor,
                ArtifactHandle(id=artifact.id, version=artifact.version, sha256=artifact.sha256),
            )
            == b'"PRIVATE_SIGNAL_SENTINEL"'
        )


def test_wait_from_another_execution_is_not_accepted(signals):
    h = signals
    other = admit(h, "other-execution")
    with Session(h.engine) as db, pytest.raises(ExecutionError, match="not_found"), db.begin():
        h.inbox.accept_signal(db, h.actor, other.id, h.signal)


@pytest.mark.parametrize("signals", ["mutable"], indirect=True)
def test_signal_snapshots_exact_payload_before_provider_waits(signals):
    from lotuspond_sdk.activity import ArtifactHandle

    from lotuspond.models import ExecutionArtifact

    h = signals
    original = h.authority.current
    calls = 0

    def mutating(scope):
        nonlocal calls
        calls += 1
        if calls == 3:  # after original digest, during later authority recheck
            h.signal.payload.append(None)
        return original(scope)

    h.authority.current = mutating
    receipt = accept(h)
    with Session(h.engine) as db, db.begin():
        row = db.get(ExecutionInbox, receipt.id)
        artifact = db.get(ExecutionArtifact, row.payload_artifact_id)
        assert (
            h.store.get(
                db,
                h.actor,
                ArtifactHandle(id=artifact.id, version=artifact.version, sha256=artifact.sha256),
            )
            == b"[]"
        )


@pytest.mark.parametrize(
    "signals",
    [
        {"kind": kind, "schema": numeric_schema("integer", nested), "nested": nested}
        for kind in ("external", "human")
        for nested in (False, True)
    ],
    ids=["external-root", "external-nested", "human-root", "human-nested"],
    indirect=True,
)
@pytest.mark.parametrize("invalid", [1.0, -0.0], ids=["integral-float", "negative-zero"])
@pytest.mark.parametrize("replay", [False, True], ids=["new", "equal-replay"])
def test_signal_preserves_strict_integer_domain(signals, invalid, replay):
    from lotuspond.execution.contracts import canonical_value

    from .test_admission import counts

    h = signals
    valid = h.signal.model_copy(update={"payload": numeric_value(int(invalid), h.nested)})
    first = accept(h, valid)
    assert accept(h, valid) == first
    bad = h.signal.model_copy(
        update={
            "signal_id": "signal" if replay else "new-invalid",
            "payload": numeric_value(invalid, h.nested),
        }
    )
    assert canonical_value(valid.payload) == canonical_value(bad.payload)
    with Session(h.engine) as db:
        before = counts(db)
    with pytest.raises(ExecutionError, match="invalid_request"):
        accept(h, bad)
    with Session(h.engine) as db:
        assert counts(db) == before
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 1
        assert db.get(ExecutionWait, h.wait_id).state == "open"


@pytest.mark.parametrize(
    "signals",
    [
        {"kind": kind, "schema": numeric_schema("number", nested), "nested": nested}
        for kind in ("external", "human")
        for nested in (False, True)
    ],
    ids=["external-root", "external-nested", "human-root", "human-nested"],
    indirect=True,
)
def test_signal_accepts_declared_number_domain(signals):
    h = signals
    for index, value in enumerate((1.0, -0.0, 1.25)):
        request = h.signal.model_copy(
            update={"signal_id": f"number-{index}", "payload": numeric_value(value, h.nested)}
        )
        first = accept(h, request)
        assert accept(h, request) == first
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 3
        assert db.get(ExecutionWait, h.wait_id).state == "open"


def test_node_resolution_refuses_unresolved_record_and_mismatched_paths(signals):
    from types import SimpleNamespace

    h = signals
    with Session(h.engine) as db, db.begin():
        execution = db.get(Execution, h.execution_id)
        package, sources = h.inbox._deployment(db, h.actor, execution.deployment_revision_id)
    node = SimpleNamespace(occurrence_path="form", node_id="form")
    assert h.inbox._node(package, sources, node)[1].id == "form"  # positive control
    for path, node_id in (
        ("nope", "nope"),
        ("form#select", "form"),
        ("form", "other"),
        ("form:then", "form"),
    ):
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            h.inbox._node(package, sources, SimpleNamespace(occurrence_path=path, node_id=node_id))


def test_a_signal_takes_no_installation_cursor(signals):
    """Finding 11: signal acceptance changes no installation counter."""
    from .cursor_checks import without_cursor

    h = signals
    receipt = without_cursor(h.engine, lambda: accept(h))
    assert receipt == accept(h)  # durable: an equal replay returns the same receipt
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionInbox)) == 1
