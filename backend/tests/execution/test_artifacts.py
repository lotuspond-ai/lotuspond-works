"""Exact-byte encryption, current authority, and bounded independent providers."""

from dataclasses import replace
from hashlib import sha256
from threading import Event
from time import monotonic

import pytest
from lotuspond_sdk.activity import ArtifactHandle
from sqlalchemy.orm import Session

from lotuspond.execution.artifacts import ArtifactPolicy, ArtifactStore
from lotuspond.execution.authorization import ExecutionActor, authorize_execution
from lotuspond.execution.errors import ExecutionError
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import Environment, ExecutionArtifact, Membership, new_id

from .test_publication import harness as harness


class FixtureKeys:
    test_only = True

    def __init__(self):
        self.value = b"k" * 32

    def key(self, scope, key_id):
        return self.value


class FixtureAccess:
    test_only = True

    def __init__(self):
        self.allowed = True
        self.calls = []

    def current(self, actor, source_id, version, now_ms):
        self.calls.append((actor.scope, source_id, version, now_ms))
        return self.allowed


@pytest.fixture
def artifacts(harness):
    h = harness
    h.keys, h.access = FixtureKeys(), FixtureAccess()
    h.store = ArtifactStore(h.keys, h.access, test_mode=True, timeout_seconds=0.1)
    with Session(h.engine) as db, db.begin():
        h.actor = authorize_execution(db, h.principal, h.scope, "execute")
        h.policy = ArtifactPolicy(
            classification="customer-private",
            source_id=None,
            source_version=None,
            retain_until_ms=database_now_ms(db) + 60000,
            access_policy_id="workspace-current-membership-v1",
            key_id="fixture-key",
        )
    yield h
    h.store.close()


def test_ciphertext_aad_and_digest_bind_scope(artifacts):
    h = artifacts
    private_sentinel = b"PRIVATE_ARTIFACT_SENTINEL"
    content = b'{ "value" : "' + private_sentinel + b'" }\n'
    with Session(h.engine) as db, db.begin():
        handle = h.store.put(db, h.actor, content, h.policy)
        other = h.store.put(db, h.actor, content, h.policy)
        row = db.get(ExecutionArtifact, handle.id)
        persisted_ciphertext = row.ciphertext
        assert private_sentinel not in persisted_ciphertext
        assert handle.sha256 == sha256(content).hexdigest()
        assert h.store.get(db, h.actor, handle) == content
        assert row.nonce != db.get(ExecutionArtifact, other.id).nonce
        assert b"k" * 32 not in handle.model_dump_json().encode()
    # A valid ciphertext from another artifact does not authenticate at this ID.
    with Session(h.engine) as db, db.begin():
        row, alternate = db.get(ExecutionArtifact, handle.id), db.get(ExecutionArtifact, other.id)
        row.ciphertext, row.nonce = alternate.ciphertext, alternate.nonce
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
        h.store.get(db, h.actor, handle)
    assert str(error.value) == "artifact_corrupt"
    # Public fields of a former/separately scoped actor do not confer authority.
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        h.store.get(db, replace(h.actor, _seal=None), handle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("classification", "different"),
        ("access_policy_id", "different"),
        ("key_id", "different"),
        ("retain_until_ms", 9007199254740991),
        ("sha256", "a" * 64),
        ("version", 2),
        ("nonce", b"n" * 12),
    ],
)
def test_authenticated_metadata_cannot_be_changed(artifacts, field, value):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        handle = h.store.put(db, h.actor, b'{"private":"value"}', h.policy)
    with Session(h.engine) as db, db.begin():
        setattr(db.get(ExecutionArtifact, handle.id), field, value)
    if field in {"sha256", "version"}:
        handle = ArtifactHandle.model_validate({**handle.model_dump(), field: value})
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
        h.store.get(db, h.actor, handle)
    assert error.value.code == (
        "unsupported_policy" if field == "access_policy_id" else "artifact_corrupt"
    )


def test_exact_json_limits(artifacts):
    h = artifacts
    oversized = b'"' + b"x" * 1048575 + b'"'
    oversized_value_bytes = len(oversized)
    assert oversized_value_bytes == 1048577
    bad = [
        oversized,
        b"[" * 33 + b"0" + b"]" * 33,
        b"[" + b",".join([b"0"] * 10000) + b"]",
        b'{"x":1,"x":2}',
        b"NaN",
        b"1e999",
        b'"\\ud800"',
        b'"\xff"',
        b"true trailing",
    ]
    with Session(h.engine) as db, db.begin():
        for content in bad:
            with pytest.raises(ExecutionError, match="invalid_request"):
                h.store.put(db, h.actor, content, h.policy)
        for content in [
            b'"' + b"x" * 1048574 + b'"',
            b"[" * 32 + b"0" + b"]" * 32,
            b"[" + b",".join([b"0"] * 9999) + b"]",
        ]:
            handle = h.store.put(db, h.actor, content, h.policy)
            assert h.store.get(db, h.actor, handle) == content


def test_missing_key_denies(artifacts, caplog):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        handle = h.store.put(db, h.actor, b'"PRIVATE_KEY_SENTINEL"', h.policy)
    h.keys.value = None
    for operation in [
        lambda db: h.store.get(db, h.actor, handle),
        lambda db: h.store.put(db, h.actor, b'"PRIVATE_KEY_SENTINEL"', h.policy),
    ]:
        with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
            operation(db)
        assert str(error.value) == error.value.code == "key_unavailable"
        assert error.value.__cause__ is None
    assert "PRIVATE_KEY_SENTINEL" not in caplog.text


def test_no_provider_cached_allow_or_unbounded_timeout(artifacts):
    h = artifacts
    entered, release = Event(), Event()

    class StuckKeys:
        def key(self, scope, key_id):
            entered.set()
            assert release.wait(5)
            return b"k" * 32

    store = ArtifactStore(StuckKeys(), h.access, timeout_seconds=0.02, test_mode=True)
    try:
        start = monotonic()
        with (
            Session(h.engine) as db,
            db.begin(),
            pytest.raises(ExecutionError, match="key_unavailable"),
        ):
            store.put(db, h.actor, b"null", h.policy)
        assert entered.is_set() and monotonic() - start < 1
        with (
            Session(h.engine) as db,
            db.begin(),
            pytest.raises(ExecutionError, match="key_unavailable"),
        ):
            store.put(db, h.actor, b"null", h.policy)
    finally:
        release.set()
        store.close()
    missing = ArtifactStore(None, None)
    with (
        Session(h.engine) as db,
        db.begin(),
        pytest.raises(ExecutionError, match="key_unavailable"),
    ):
        missing.put(db, h.actor, b"null", h.policy)


def test_retention_inventory_is_bounded_dry_run(artifacts):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        cutoff = database_now_ms(db) + 250
        policy = ArtifactPolicy(**{**h.policy.model_dump(), "retain_until_ms": cutoff})
        first = h.store.put(db, h.actor, b"null", policy)
        second = h.store.put(db, h.actor, b"true", policy)
        assert h.store.retention_inventory(db, h.actor, limit=1) == ()
    # The real database wall clock passes the original authenticated retention.
    deadline = monotonic() + 5
    with Session(h.engine) as db, db.begin():
        while database_now_ms(db) < cutoff:
            assert monotonic() < deadline
        page = h.store.retention_inventory(db, h.actor, limit=1)
        assert len(page) == 1
        rest = h.store.retention_inventory(db, h.actor, limit=1, after_id=page[0].id)
        assert {page[0].id, rest[0].id} == {first.id, second.id}
        assert db.get(ExecutionArtifact, first.id) is not None
        assert db.get(ExecutionArtifact, second.id) is not None
        with pytest.raises(ExecutionError, match="forbidden"):
            h.store.get(db, h.actor, first)
        with pytest.raises(ExecutionError, match="invalid_request"):
            h.store.retention_inventory(db, h.actor, limit=1001)


def test_current_membership_and_caller_transaction(artifacts):
    h = artifacts
    with Session(h.engine) as db:
        handle = h.store.put(db, h.actor, b"null", h.policy)
        db.rollback()
    with Session(h.engine) as db, db.begin():
        assert db.get(ExecutionArtifact, handle.id) is None
        db.get(Membership, (h.scope.workspace_id, h.actor.user_id)).role = "viewer"
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        h.store.put(db, h.actor, b"null", h.policy)
    forged = ExecutionActor(h.actor.user_id, None, h.scope, "owner")
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        h.store.retention_inventory(db, forged)


def test_full_scope_is_authenticated_and_queries_deny_other_environment(artifacts):
    h = artifacts
    other_scope = h.scope.model_copy(update={"environment_id": new_id()})
    with Session(h.engine) as db, db.begin():
        db.add(
            Environment(
                id=other_scope.environment_id,
                workspace_id=other_scope.workspace_id,
                project_id=other_scope.project_id,
                name="other",
                kind="local",
            )
        )
        db.flush()
        handle = h.store.put(db, h.actor, b'"private"', h.policy)
        other_actor = authorize_execution(db, h.principal, other_scope, "read")
        with pytest.raises(ExecutionError, match="not_found"):
            h.store.get(db, other_actor, handle)
    # Copying ciphertext into another valid tenant environment grants no integrity.
    with Session(h.engine) as db, db.begin():
        db.get(ExecutionArtifact, handle.id).environment_id = other_scope.environment_id
    with (
        Session(h.engine) as db,
        db.begin(),
        pytest.raises(ExecutionError, match="artifact_corrupt"),
    ):
        h.store.get(db, other_actor, handle)


def test_corrupt_ciphertext_and_provider_exception_have_fixed_codes(artifacts, caplog):
    h = artifacts
    with Session(h.engine) as db, db.begin():
        handle = h.store.put(db, h.actor, b'"PRIVATE_CORRUPTION_SENTINEL"', h.policy)
    with Session(h.engine) as db, db.begin():
        row = db.get(ExecutionArtifact, handle.id)
        row.ciphertext = bytes([row.ciphertext[0] ^ 1]) + row.ciphertext[1:]
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
        h.store.get(db, h.actor, handle)
    assert str(error.value) == "artifact_corrupt"

    class BrokenKeys:
        def key(self, scope, key_id):
            raise RuntimeError("PRIVATE_PROVIDER_EXCEPTION_SENTINEL")

    store = ArtifactStore(BrokenKeys(), None)
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
        store.get(db, h.actor, handle)
    assert str(error.value) == "key_unavailable"
    assert "PRIVATE_PROVIDER_EXCEPTION_SENTINEL" not in caplog.text
    assert "PRIVATE_CORRUPTION_SENTINEL" not in caplog.text
    store.close()


def test_synthetic_providers_require_explicit_test_mode():
    with pytest.raises(ValueError, match="Synthetic providers require test mode"):
        ArtifactStore(FixtureKeys(), FixtureAccess())
    for value in [True, 0, 5.1, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="Invalid artifact timeout"):
            ArtifactStore(None, None, timeout_seconds=value)


def test_restrictive_source_null_policy_is_not_interpreted_as_workspace_access(artifacts):
    h = artifacts
    for label in ["unknown", "owner-only", "private-policy"]:
        with (
            Session(h.engine) as db,
            db.begin(),
            pytest.raises(ExecutionError, match="unsupported_policy"),
        ):
            h.store.put(
                db,
                h.actor,
                b'"private"',
                ArtifactPolicy.model_construct(
                    **{**h.policy.model_dump(), "access_policy_id": label}
                ),
            )
