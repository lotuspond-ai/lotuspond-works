"""Bounded encrypted JSON evidence; handles and historical metadata grant no access.

The caller owns the transaction. Workspace/account locks precede source/version
locks and artifact locks. Providers are separately configured and never get a DB
session. No keys, plaintext, provider exceptions or cached allows are persisted.
"""

import json
import os
from hashlib import sha256
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from lotuspond_runtime.protocol import CanonicalUUID, OpaqueId, SafeInt, Scope
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.contract import Identifier
from lotuspond_sdk.executable.input import decode_executable_bytes
from pydantic import Field, TypeAdapter, model_validator

from ..fleet.authority import database_now_ms
from ..models import ExecutionArtifact, ExecutionContextSource, ExecutionContextVersion, new_id
from .authorization import ExecutionActor, lock, reauthorize, record, scoped
from .contracts import Contract
from .errors import ExecutionError, execution_service
from .publication import _Lookup

# The only built-in policy for values without an independently authorized source.
# A different/more restrictive label must never silently acquire these semantics.
WORKSPACE_ACCESS_POLICY = "workspace-current-membership-v1"


class ArtifactPolicy(Contract):
    classification: Identifier
    source_id: CanonicalUUID | None
    source_version: int | None = Field(ge=1, le=2147483647)
    retain_until_ms: SafeInt
    access_policy_id: OpaqueId
    key_id: OpaqueId

    @model_validator(mode="after")
    def pair(self):
        if (self.source_id is None) != (self.source_version is None):
            raise ExecutionError("invalid_request")
        if self.source_id is None and self.access_policy_id != WORKSPACE_ACCESS_POLICY:
            raise ExecutionError("unsupported_policy")
        return self


class ArtifactKeyProvider(Protocol):
    def key(self, scope: Scope, key_id: str) -> bytes: ...


class SourceAccess(Protocol):
    def current(self, actor: ExecutionActor, source_id: str, version: int, now_ms: int) -> bool: ...


def _json(content):
    if type(content) is not bytes:
        raise ExecutionError("invalid_request")
    return decode_executable_bytes(content)


def _aad(scope, row):
    """All immutable interpretation/access metadata is authenticated, including retention."""
    return json.dumps(
        {
            "format": "lotuspond.execution-artifact.v1",
            **scope.model_dump(),
            **{
                key: getattr(row, key)
                for key in (
                    "id",
                    "version",
                    "sha256",
                    "content_bytes",
                    "classification",
                    "source_id",
                    "source_version",
                    "access_policy_id",
                    "key_id",
                    "retain_until_ms",
                )
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


class ArtifactStore:
    def __init__(
        self,
        keys: ArtifactKeyProvider | None,
        source_access: SourceAccess | None,
        *,
        timeout_seconds: float = 5.0,
        test_mode: bool = False,
    ):
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 5:
            raise ValueError("Invalid artifact timeout")
        if not test_mode and any(getattr(p, "test_only", False) for p in (keys, source_access)):
            raise ValueError("Synthetic providers require test mode")
        self.keys, self.source_access = keys, source_access
        self.timeout_seconds = timeout_seconds
        # Independent slots, bounded at one in-flight call each, including stuck calls.
        self._keys, self._access = _Lookup(), _Lookup()

    def close(self):
        self._keys.closed = self._access.closed = True

    def _key(self, scope, key_id):
        key = self._keys.call(
            self.keys, "key", (scope, key_id), self.timeout_seconds, "key_unavailable"
        )
        if type(key) is not bytes or len(key) != 32:
            raise ExecutionError("key_unavailable")
        return key

    def _source(self, db, actor, source_id, version):
        if source_id is None:
            return None
        source = record(db, ExecutionContextSource, actor.scope, source_id, locked=True)
        pin = lock(
            db,
            scoped(ExecutionContextVersion, actor.scope).where(
                ExecutionContextVersion.source_id == source_id,
                ExecutionContextVersion.version == version,
            ),
        )
        if pin is None:
            raise ExecutionError("not_found")
        self._source_valid(db, source, pin)
        return source, pin

    @staticmethod
    def _source_valid(db, source, pin):
        now_ms = database_now_ms(db)
        if (
            source.status != "active"
            or source.access_policy_id != pin.access_policy_id
            or pin.observed_at_ms > now_ms
            or pin.expires_at_ms <= now_ms
        ):
            raise ExecutionError("forbidden")

    def _current(self, db, actor, policy, source):
        # Evaluate current DB time after each bounded provider/lock wait.
        if source is not None:
            source_row, pin = source
            self._source_valid(db, source_row, pin)
            if (
                policy.classification != pin.classification
                or policy.access_policy_id != pin.access_policy_id
            ):
                raise ExecutionError("forbidden")
            allowed = self._access.call(
                self.source_access,
                "current",
                (actor, policy.source_id, policy.source_version, database_now_ms(db)),
                self.timeout_seconds,
                "source_unavailable",
            )
            if type(allowed) is not bool:
                raise ExecutionError("source_unavailable")
            if not allowed:
                raise ExecutionError("forbidden")
            self._source_valid(db, source_row, pin)
        if policy.retain_until_ms <= database_now_ms(db):
            raise ExecutionError("forbidden")

    @execution_service
    def put(
        self, db, actor: ExecutionActor, content: bytes, policy: ArtifactPolicy
    ) -> ArtifactHandle:
        return self._put(db, actor, content, policy, operation="execute")

    @execution_service
    def _put_signal(self, db, actor, content, policy, *, check_current):
        """Internal inbox-only route; the callback is trusted service code, never input.

        It must recheck exact persisted wait/source/assignment/type and deadline.
        Only source-null workspace-policy response artifacts are supported here:
        no new source parent may be acquired after the inbox locks its wait.
        """
        policy = ArtifactPolicy.model_validate(policy)
        if policy.source_id is not None or not callable(check_current):
            raise ExecutionError("forbidden")
        return self._put(
            db, actor, content, policy, operation="signal", check_current=check_current
        )

    def _put(self, db, actor, content, policy, *, operation, check_current=None):
        actor = reauthorize(db, actor, operation)
        if check_current is not None:
            check_current()
        policy = ArtifactPolicy.model_validate(policy)
        _json(content)
        source = self._source(db, actor, policy.source_id, policy.source_version)
        row = ExecutionArtifact(
            **actor.scope.model_dump(),
            **policy.model_dump(),
            id=new_id(),
            version=1,
            sha256=sha256(content).hexdigest(),
            content_bytes=len(content),
            nonce=os.urandom(12),
        )
        key = self._key(actor.scope, policy.key_id)
        self._current(db, actor, policy, source)
        # An external key/source lookup is not authority to outlive current account expiry.
        reauthorize(db, actor, operation)
        if check_current is not None:
            check_current()
        row.ciphertext = AESGCM(key).encrypt(row.nonce, content, _aad(actor.scope, row))
        db.add(row)
        db.flush()
        self._current_time(db, policy, source)
        if check_current is not None:
            check_current()
        return ArtifactHandle(id=row.id, version=row.version, sha256=row.sha256)

    @execution_service
    def get(self, db, actor: ExecutionActor, handle: ArtifactHandle) -> bytes:
        actor = reauthorize(db, actor, "read")
        handle = ArtifactHandle.model_validate(handle)
        hint = record(db, ExecutionArtifact, actor.scope, handle.id)
        source = self._source(db, actor, hint.source_id, hint.source_version)
        row = record(db, ExecutionArtifact, actor.scope, handle.id, locked=True)
        if row.version != handle.version or row.sha256 != handle.sha256:
            raise ExecutionError("not_found")
        policy = ArtifactPolicy.model_validate(
            {key: getattr(row, key) for key in ArtifactPolicy.model_fields}
        )
        key = self._key(actor.scope, row.key_id)
        self._current(db, actor, policy, source)
        reauthorize(db, actor, "read")
        try:
            content = AESGCM(key).decrypt(row.nonce, row.ciphertext, _aad(actor.scope, row))
            if len(content) != row.content_bytes or sha256(content).hexdigest() != row.sha256:
                raise ValueError
            _json(content)
        except Exception:
            raise ExecutionError("artifact_corrupt") from None
        # Crypto/JSON work cannot extend a freshness or retention window either.
        self._current_time(db, policy, source)
        return content

    def _cached_key(self, scope, key_id, keys):
        """A caller-owned per-transaction cache; never persisted or shared."""
        if keys is None:
            return self._key(scope, key_id)
        slot = (scope.workspace_id, scope.project_id, scope.environment_id, key_id)
        if slot not in keys:
            keys[slot] = self._key(scope, key_id)
        return keys[slot]

    @execution_service
    def _put_internal(self, db, scope, content, policy, *, keys=None) -> ArtifactHandle:
        """Orchestrator-only write of an execution's own workspace-policy value.

        No customer actor: the caller holds the execution row lock and records the
        value on that execution's rows. Source-bound policies are refused; current
        source ACLs need an authenticated actor (runtime and API paths).
        """
        scope = Scope.model_validate(scope)
        policy = ArtifactPolicy.model_validate(policy)
        if policy.source_id is not None:
            raise ExecutionError("forbidden")
        _json(content)
        row = ExecutionArtifact(
            **scope.model_dump(),
            **policy.model_dump(),
            id=new_id(),
            version=1,
            sha256=sha256(content).hexdigest(),
            content_bytes=len(content),
            nonce=os.urandom(12),
        )
        key = self._cached_key(scope, policy.key_id, keys)
        self._current_time(db, policy, None)
        row.ciphertext = AESGCM(key).encrypt(row.nonce, content, _aad(scope, row))
        db.add(row)
        db.flush()
        return ArtifactHandle(id=row.id, version=row.version, sha256=row.sha256)

    @execution_service
    def _get_internal(self, db, scope, handle, *, keys=None) -> bytes:
        """Orchestrator-only read of a workspace-policy value pinned by the caller's rows."""
        scope = Scope.model_validate(scope)
        handle = ArtifactHandle.model_validate(handle)
        row = record(db, ExecutionArtifact, scope, handle.id)
        if row.version != handle.version or row.sha256 != handle.sha256:
            raise ExecutionError("not_found")
        if row.source_id is not None:
            raise ExecutionError("forbidden")
        policy = ArtifactPolicy.model_validate(
            {key: getattr(row, key) for key in ArtifactPolicy.model_fields}
        )
        key = self._cached_key(scope, row.key_id, keys)
        self._current_time(db, policy, None)
        try:
            content = AESGCM(key).decrypt(row.nonce, row.ciphertext, _aad(scope, row))
            if len(content) != row.content_bytes or sha256(content).hexdigest() != row.sha256:
                raise ValueError
            _json(content)
        except Exception:
            raise ExecutionError("artifact_corrupt") from None
        return content

    def _current_time(self, db, policy, source):
        if source is not None:
            self._source_valid(db, *source)
        if policy.retain_until_ms <= database_now_ms(db):
            raise ExecutionError("forbidden")

    @execution_service
    def retention_inventory(self, db, actor, *, limit=100, after_id=None):
        """Dry-run only: bounded expired handle inventory, never delete any evidence.

        Pending/uncertain dependencies are deliberately not classified as safe to
        delete. A future reviewed reconciliation/deletion policy must do that.
        """
        actor = reauthorize(db, actor, "manage")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ExecutionError("invalid_request")
        query = scoped(ExecutionArtifact, actor.scope).where(
            ExecutionArtifact.retain_until_ms <= database_now_ms(db)
        )
        if after_id is not None:
            after_id = TypeAdapter(CanonicalUUID).validate_python(after_id, strict=True)
            query = query.where(ExecutionArtifact.id > after_id)
        # Inventory never fetches ciphertext (up to 1 MiB per row).
        query = query.with_only_columns(
            ExecutionArtifact.id, ExecutionArtifact.version, ExecutionArtifact.sha256
        )
        rows = db.execute(query.order_by(ExecutionArtifact.id).limit(limit)).all()
        return tuple(ArtifactHandle(id=r.id, version=r.version, sha256=r.sha256) for r in rows)
