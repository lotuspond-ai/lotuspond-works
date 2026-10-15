"""Bounded supplied context registration and current source authorization.

No ingestion, connector catalog, memory, or deletion propagation is implied.
Citations carry immutable source/version provenance; resolving/reading either
handle independently requires the current source access provider again.
"""

from typing import Annotated, Literal

from lotuspond_runtime.protocol import CanonicalUUID
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.contract import Identifier
from pydantic import BeforeValidator, Field

from ..fleet.authority import database_now_ms
from ..models import ExecutionArtifact, ExecutionContextSource, ExecutionContextVersion, new_id
from .artifacts import ArtifactPolicy, ArtifactStore, SourceAccess, _json
from .authorization import reauthorize, record, scoped
from .contracts import Contract, array, canonical_value
from .errors import ExecutionError, execution_service

__all__ = ["AuthorizedContext", "ContextRequest", "ContextSource", "SourceAccess"]


class ContextRequest(Contract):
    source_id: CanonicalUUID
    version: int = Field(ge=1, le=2147483647)
    required_freshness_seconds: int = Field(ge=0, le=31536000)
    allowed_classifications: Annotated[
        tuple[Identifier, ...], BeforeValidator(array), Field(min_length=1, max_length=64)
    ]


class AuthorizedContext(Contract):
    value: ArtifactHandle
    citation: ArtifactHandle


class ContextSource:
    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts

    @execution_service
    def register(
        self,
        db,
        actor,
        *,
        source_id,
        version,
        content: bytes,
        citation: bytes,
        classification,
        observed_at_ms,
        expires_at_ms,
        retain_until_ms,
        access_policy_id,
        key_id,
    ) -> AuthorizedContext:
        """Owner/admin-only registration through the same artifact/key/ACL services.

        Version identity is append-only. The source/version is inserted without
        handles first to satisfy immediate cyclic FKs, then bound in this caller's
        transaction. Failed operations require caller rollback, as all services do.
        """
        actor = reauthorize(db, actor, "manage")
        request = ContextRequest(
            source_id=source_id,
            version=version,
            required_freshness_seconds=0,
            allowed_classifications=(classification,),
        )
        policy = ArtifactPolicy(
            classification=classification,
            source_id=request.source_id,
            source_version=request.version,
            retain_until_ms=retain_until_ms,
            access_policy_id=access_policy_id,
            key_id=key_id,
        )
        # Validate both supplied values and times before inserting registration rows.
        _json(content)
        citation_value = _json(citation)
        for value in (observed_at_ms, expires_at_ms):
            if type(value) is not int or not 0 <= value <= 9007199254740991:
                raise ExecutionError("invalid_request")
        now_ms = database_now_ms(db)
        if not observed_at_ms <= now_ms < expires_at_ms or retain_until_ms <= now_ms:
            raise ExecutionError("forbidden")
        provenance = canonical_value(
            {
                "format": "lotuspond.execution-citation.v1",
                "source_id": request.source_id,
                "version": request.version,
                "classification": classification,
                "observed_at_ms": observed_at_ms,
                "access_policy_id": access_policy_id,
                "access": "current_required",
                "citation": citation_value,
            }
        )
        source = db.scalar(
            scoped(ExecutionContextSource, actor.scope).where(
                ExecutionContextSource.id == request.source_id
            )
        )
        if source is None:
            source = ExecutionContextSource(
                **actor.scope.model_dump(),
                id=request.source_id,
                status="active",
                access_policy_id=access_policy_id,
            )
            db.add(source)
            db.flush()
        else:
            source = record(db, ExecutionContextSource, actor.scope, request.source_id, locked=True)
            if source.status != "active" or source.access_policy_id != access_policy_id:
                raise ExecutionError("forbidden")
        pin = db.scalar(
            scoped(ExecutionContextVersion, actor.scope).where(
                ExecutionContextVersion.source_id == request.source_id,
                ExecutionContextVersion.version == request.version,
            )
        )
        if pin is not None:
            raise ExecutionError("conflict")
        pin = ExecutionContextVersion(
            **actor.scope.model_dump(),
            id=new_id(),
            source_id=request.source_id,
            version=request.version,
            classification=classification,
            observed_at_ms=observed_at_ms,
            expires_at_ms=expires_at_ms,
            access_policy_id=access_policy_id,
        )
        db.add(pin)
        db.flush()
        value = self.artifacts.put(db, actor, content, policy)
        citation_handle = self.artifacts.put(db, actor, provenance, policy)
        for prefix, handle in (("content", value), ("citation", citation_handle)):
            setattr(pin, f"{prefix}_artifact_id", handle.id)
            setattr(pin, f"{prefix}_artifact_version", handle.version)
            setattr(pin, f"{prefix}_sha256", handle.sha256)
        db.flush()
        return AuthorizedContext(value=value, citation=citation_handle)

    @execution_service
    def resolve(self, db, actor, request: ContextRequest) -> AuthorizedContext:
        actor = reauthorize(db, actor, "read")
        request = ContextRequest.model_validate(request)
        source, pin = self.artifacts._source(db, actor, request.source_id, request.version)
        self._fresh(db, pin, request)
        handles = []
        for prefix in ("content", "citation"):
            handles.append(
                ArtifactHandle(
                    id=getattr(pin, f"{prefix}_artifact_id"),
                    version=getattr(pin, f"{prefix}_artifact_version"),
                    sha256=getattr(pin, f"{prefix}_sha256"),
                )
            )
        # Same-kind locks use sorted IDs; both pins must agree independently of
        # the normalized FK before any private bytes can be returned.
        for handle in sorted(handles, key=lambda handle: handle.id):
            row = record(db, ExecutionArtifact, actor.scope, handle.id, locked=True)
            if (
                row.source_id != request.source_id
                or row.source_version != request.version
                or row.classification != pin.classification
                or row.access_policy_id != pin.access_policy_id
            ):
                raise ExecutionError("forbidden")
        # Actually authenticate/decrypt both rows. Metadata/FKs are insufficient.
        self.artifacts.get(db, actor, handles[0])
        citation = _json(self.artifacts.get(db, actor, handles[1]))
        expected = {
            "format": "lotuspond.execution-citation.v1",
            "source_id": pin.source_id,
            "version": pin.version,
            "classification": pin.classification,
            "observed_at_ms": pin.observed_at_ms,
            "access_policy_id": pin.access_policy_id,
            "access": "current_required",
        }
        if (
            type(citation) is not dict
            or set(citation) != {*expected, "citation"}
            or any(citation[key] != value for key, value in expected.items())
        ):
            raise ExecutionError("forbidden")
        self._fresh(db, pin, request)
        return AuthorizedContext(value=handles[0], citation=handles[1])

    @staticmethod
    def _fresh(db, pin, request):
        now_ms = database_now_ms(db)
        if (
            pin.classification not in request.allowed_classifications
            or pin.observed_at_ms > now_ms
            or pin.expires_at_ms <= now_ms
            or now_ms - pin.observed_at_ms > request.required_freshness_seconds * 1000
        ):
            raise ExecutionError("forbidden")

    @execution_service
    def set_status(self, db, actor, source_id, status: Literal["revoked", "deleted"]):
        actor = reauthorize(db, actor, "manage")
        if status not in {"revoked", "deleted"}:
            raise ExecutionError("invalid_request")
        source = record(db, ExecutionContextSource, actor.scope, source_id, locked=True)
        if source.status == "deleted" and status != "deleted":
            raise ExecutionError("forbidden")
        source.status = status
        db.flush()
