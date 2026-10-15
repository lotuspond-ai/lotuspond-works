"""Knowledge is pinned evidence, with current use-time authority."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from lotuspond.execution.artifacts import ArtifactPolicy, ArtifactStore
from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.context import ContextRequest, ContextSource
from lotuspond.execution.errors import ExecutionError
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import (
    ExecutionArtifact,
    ExecutionContextSource,
    ExecutionContextVersion,
    new_id,
)

from .test_artifacts import artifacts as artifacts
from .test_publication import harness as harness


def register(h, db, **changes):
    values = dict(
        source_id=new_id(),
        version=1,
        content=b'{ "input": "PRIVATE_CONTEXT_SENTINEL" }',
        citation=b'{"document":"PRIVATE_CITATION_SENTINEL"}',
        classification="customer-private",
        observed_at_ms=database_now_ms(db),
        expires_at_ms=database_now_ms(db) + 60000,
        retain_until_ms=database_now_ms(db) + 120000,
        access_policy_id="private-policy",
        key_id="fixture-key",
    )
    values.update(changes)
    source = ContextSource(h.store)
    handles = source.register(db, h.actor, **values)
    request = ContextRequest(
        source_id=values["source_id"],
        version=values["version"],
        required_freshness_seconds=60,
        allowed_classifications=("customer-private",),
    )
    return source, request, handles


def test_revoked_source_cannot_be_reread(artifacts):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        source, request, handles = register(h, db)
        assert source.resolve(db, h.actor, request) == handles
        assert b"PRIVATE_CONTEXT_SENTINEL" in h.store.get(db, h.actor, handles.value)
        citation = json.loads(h.store.get(db, h.actor, handles.citation))
        assert citation["source_id"] == request.source_id
        assert citation["version"] == request.version
        assert citation["access_policy_id"] == "private-policy"
        # A retained result is linked to the same immutable source/version as its input.
        policy = ArtifactPolicy(
            **{
                **h.policy.model_dump(),
                "source_id": request.source_id,
                "source_version": request.version,
                "access_policy_id": "private-policy",
            }
        )
        result = h.store.put(db, h.actor, b'"PRIVATE_RESULT_SENTINEL"', policy)
    with Session(h.engine) as db, db.begin():
        source.set_status(db, h.actor, request.source_id, "revoked")
    for handle in [handles.value, handles.citation, result]:
        with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
            h.store.get(db, h.actor, handle)
        revoked_source_error = error.value
        assert revoked_source_error.code == "forbidden"
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        source.resolve(db, h.actor, request)


def test_current_external_access_missing_and_deleted_source(artifacts):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        source, request, handles = register(h, db)
    h.access.allowed = False
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        source.resolve(db, h.actor, request)
    missing = ContextSource(ArtifactStore(h.keys, None, test_mode=True))
    with (
        Session(h.engine) as db,
        db.begin(),
        pytest.raises(ExecutionError, match="source_unavailable"),
    ):
        missing.resolve(db, h.actor, request)
    h.access.allowed = True
    with Session(h.engine) as db, db.begin():
        source.set_status(db, h.actor, request.source_id, "deleted")
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        h.store.get(db, h.actor, handles.value)


@pytest.mark.parametrize(
    "change",
    [
        "expired",
        "stale",
        "future",
        "classification",
        "policy",
        "unbound",
        "citation",
        "citation_source",
    ],
)
def test_current_freshness_classification_and_citation(artifacts, change):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        source, request, handles = register(h, db)
    with Session(h.engine) as db, db.begin():
        version = db.scalar(
            select(ExecutionContextVersion).where(
                ExecutionContextVersion.source_id == request.source_id
            )
        )
        now = database_now_ms(db)
        if change == "expired":
            version.observed_at_ms, version.expires_at_ms = now - 100, now
        elif change == "stale":
            version.observed_at_ms = now - 60001
        elif change == "future":
            version.observed_at_ms = now + 10000
        elif change == "classification":
            version.classification = "changed"
        elif change == "policy":
            version.access_policy_id = "changed"
        elif change == "unbound":
            version.content_artifact_id = version.content_artifact_version = (
                version.content_sha256
            ) = None
        elif change == "citation_source":
            # Authenticate a real source-bound artifact whose citation names a
            # different source; a valid FK/tag must not bypass provenance checks.
            document = json.loads(h.store.get(db, h.actor, handles.citation))
            document["source_id"] = new_id()
            policy = ArtifactPolicy(
                **{
                    **h.policy.model_dump(),
                    "source_id": request.source_id,
                    "source_version": request.version,
                    "access_policy_id": "private-policy",
                }
            )
            changed = h.store.put(db, h.actor, json.dumps(document).encode(), policy)
            version.citation_artifact_id = changed.id
            version.citation_artifact_version = changed.version
            version.citation_sha256 = changed.sha256
        else:
            # Valid full-scope FK, wrong citation document/identity at the service boundary.
            version.citation_artifact_id = handles.value.id
            version.citation_artifact_version = handles.value.version
            version.citation_sha256 = handles.value.sha256
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError):
        source.resolve(db, h.actor, request)


def test_revoke_and_read_serialize_on_real_postgresql_connections(artifacts):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        source, request, handles = register(h, db)
    started, pid = Event(), []

    def blocked():
        with Session(h.engine) as db, db.begin():
            pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                return h.store.get(db, h.actor, handles.value)
            except ExecutionError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=1) as executor:
        with Session(h.engine) as db, db.begin():
            source.set_status(db, h.actor, request.source_id, "revoked")
            revoker_pid = db.scalar(text("SELECT pg_backend_pid()"))
            future = executor.submit(blocked)
            assert started.wait(5)
            deadline = monotonic() + 5
            with h.engine.connect() as observer:
                while monotonic() < deadline:
                    if observer.scalar(
                        text("SELECT :blocker = ANY(pg_blocking_pids(:pid))"),
                        {"pid": pid[0], "blocker": revoker_pid},
                    ):
                        break
                else:
                    pytest.fail("Read never reached the real PostgreSQL revocation barrier")
        assert future.result(timeout=5) == "forbidden"
    assert pid[0] != revoker_pid


def test_source_access_timeout_is_fixed_and_bounded(artifacts, caplog):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        source, request, handles = register(h, db)
    entered, release = Event(), Event()

    class StuckAccess:
        def current(self, actor, source_id, version, now_ms):
            entered.set()
            assert release.wait(5)
            raise RuntimeError("PRIVATE_PROVIDER_SENTINEL")

    store = ArtifactStore(h.keys, StuckAccess(), test_mode=True, timeout_seconds=0.02)
    try:
        start = monotonic()
        with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
            store.get(db, h.actor, handles.value)
        assert error.value.code == str(error.value) == "source_unavailable"
        assert entered.is_set() and monotonic() - start < 1
    finally:
        release.set()
        store.close()
    assert "PRIVATE_PROVIDER_SENTINEL" not in caplog.text


def test_source_version_and_policy_are_not_arbitrary_grants(artifacts):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        source, request, handles = register(h, db)
        wrong = ArtifactPolicy(
            **{
                **h.policy.model_dump(),
                "source_id": request.source_id,
                "source_version": 1,
                "access_policy_id": "invented",
            }
        )
        with pytest.raises(ExecutionError, match="forbidden"):
            h.store.put(db, h.actor, b"null", wrong)
        with pytest.raises(ExecutionError, match="conflict"):
            register(h, db, source_id=request.source_id)
        assert h.access.calls[-1][1:3] == (request.source_id, 1)
        assert db.get(ExecutionArtifact, handles.value.id)
        assert db.get(ExecutionContextSource, request.source_id)
        assert authorize_execution(db, h.principal, h.scope, "read")


def test_exact_freshness_expiry_and_retention_boundaries(artifacts, monkeypatch):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        anchor = database_now_ms(db)
    clock = [anchor]
    monkeypatch.setattr("lotuspond.execution.artifacts.database_now_ms", lambda db: clock[0])
    monkeypatch.setattr("lotuspond.execution.context.database_now_ms", lambda db: clock[0])
    with Session(h.engine) as db, db.begin():
        source, request, handles = register(
            h, db, observed_at_ms=anchor, expires_at_ms=anchor + 2000, retain_until_ms=anchor + 3000
        )
        request = ContextRequest(**{**request.model_dump(), "required_freshness_seconds": 1})
        clock[0] = anchor + 1000
        assert source.resolve(db, h.actor, request) == handles
        clock[0] += 1
        with pytest.raises(ExecutionError, match="forbidden"):
            source.resolve(db, h.actor, request)
        request = ContextRequest(**{**request.model_dump(), "required_freshness_seconds": 2})
        clock[0] = anchor + 1999
        assert source.resolve(db, h.actor, request) == handles
        clock[0] += 1
        with pytest.raises(ExecutionError, match="forbidden"):
            h.store.get(db, h.actor, handles.value)
        # Retention is exclusive even for an artifact without a source.
        policy = ArtifactPolicy(**{**h.policy.model_dump(), "retain_until_ms": anchor + 3000})
        handle = h.store.put(db, h.actor, b"null", policy)
        clock[0] = anchor + 2999
        assert h.store.get(db, h.actor, handle) == b"null"
        clock[0] += 1
        with pytest.raises(ExecutionError, match="forbidden"):
            h.store.get(db, h.actor, handle)


def test_current_clock_after_real_source_lock_wait(artifacts):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        source, request, handles = register(h, db)
    started, pid = Event(), []

    def blocked():
        with Session(h.engine) as db, db.begin():
            # Authorize before the source row barrier; the read must refresh it.
            authorize_execution(db, h.principal, h.scope, "read")
            pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                return h.store.get(db, h.actor, handles.value)
            except ExecutionError as error:
                return error.code

    # Independently configured source authority can serialize on the source row
    # without needing the workspace/account locks used by customer mutations.
    with ThreadPoolExecutor(max_workers=1) as executor:
        with Session(h.engine) as blocker, blocker.begin():
            row = blocker.scalar(
                select(ExecutionContextSource)
                .where(ExecutionContextSource.id == request.source_id)
                .with_for_update()
            )
            blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
            future = executor.submit(blocked)
            assert started.wait(5)
            deadline = monotonic() + 5
            with h.engine.connect() as observer:
                while monotonic() < deadline:
                    if observer.scalar(
                        text("SELECT :blocker = ANY(pg_blocking_pids(:pid))"),
                        {"pid": pid[0], "blocker": blocker_pid},
                    ):
                        break
                else:
                    pytest.fail("Read did not wait on the actual source row")
            pin = blocker.scalar(
                select(ExecutionContextVersion).where(ExecutionContextVersion.source_id == row.id)
            )
            pin.expires_at_ms = database_now_ms(blocker)
        assert future.result(timeout=5) == "forbidden"


def test_registration_bounds_and_caller_rollback(artifacts):
    h = artifacts
    with Session(h.engine) as db:
        source, request, handles = register(h, db)
        db.rollback()
    with Session(h.engine) as db, db.begin():
        assert db.get(ExecutionContextSource, request.source_id) is None
        assert db.get(ExecutionArtifact, handles.value.id) is None
        for value in [b'"' + b"x" * 1048575 + b'"', b'{"x":1,"x":2}']:
            with pytest.raises(ExecutionError, match="invalid_request"):
                register(h, db, content=value)
            with pytest.raises(ExecutionError, match="invalid_request"):
                register(h, db, citation=value)


def test_expiry_after_bounded_key_provider_wait_uses_current_database_time(artifacts):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        cutoff = database_now_ms(db) + 1000
        source, request, handles = register(h, db, expires_at_ms=cutoff)
    entered, release = Event(), Event()

    class WaitingKeys:
        def key(self, scope, key_id):
            entered.set()
            assert release.wait(5)
            return b"k" * 32

    store = ArtifactStore(WaitingKeys(), h.access, timeout_seconds=5, test_mode=True)

    def read():
        with Session(h.engine) as db, db.begin():
            try:
                store.get(db, h.actor, handles.value)
            except ExecutionError as error:
                return error.code
            pytest.fail("An expired source was read after the provider wait")

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read)
            assert entered.wait(5)
            deadline = monotonic() + 5
            with Session(h.engine) as observer, observer.begin():
                while database_now_ms(observer) < cutoff:
                    assert monotonic() < deadline
            release.set()
            assert future.result(timeout=5) == "forbidden"
    finally:
        release.set()
        store.close()
