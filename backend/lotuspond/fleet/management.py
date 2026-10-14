"""Scoped administration inside the caller's transaction; never resolves secrets."""

import hashlib
from datetime import UTC, datetime

import rfc8785
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    ConnectionConfig,
    ConnectionVersionDetail,
    ConnectionVersionView,
    ConnectionView,
    ManagementNameRequest,
    ManagementStatusRequest,
    PoolPolicy,
    PoolPolicyView,
    PoolView,
    ResourceStatus,
    Scope,
    SecretReferenceView,
    SecretRegistrationRequest,
    canonical_connection_json,
    canonical_pool_json,
    connection_digest,
    decode_fleet_json,
    pool_digest,
)
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Connection,
    ConnectionVersion,
    ConnectionVersionSecret,
    Environment,
    FleetMutationRequest,
    Project,
    RuntimePool,
    RuntimePoolPolicy,
    RuntimePoolPolicyConnection,
    SecretReference,
    Workspace,
)
from ..security import Principal
from .audit import fleet_audit
from .catalog import InstalledCatalog


def scoped(model, scope: Scope):
    """Every fleet record lookup carries all three customer scope predicates."""
    return select(model).where(*(getattr(model, k) == v for k, v in scope.model_dump().items()))


def checked_scope(db: Session, principal: Principal, project_id: str, environment_id: str) -> Scope:
    scope = Scope(
        workspace_id=principal.workspace_id, project_id=project_id, environment_id=environment_id
    )
    project = db.scalar(
        select(Project).where(
            Project.workspace_id == scope.workspace_id, Project.id == scope.project_id
        )
    )
    environment = db.scalar(
        select(Environment).where(
            Environment.workspace_id == scope.workspace_id,
            Environment.project_id == scope.project_id,
            Environment.id == scope.environment_id,
        )
    )
    if project is None or environment is None:
        raise FleetError("not_found")
    return scope


def record(db: Session, model, scope: Scope, identifier: str, *, lock=False):
    from lotuspond_runtime.protocol import CanonicalUUID

    identifier = TypeAdapter(CanonicalUUID).validate_python(identifier, strict=True)
    query = scoped(model, scope).where(model.id == identifier)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise FleetError("not_found")
    return row


def milliseconds(row) -> int:
    created = row.created_at
    if created.tzinfo is None:  # SQLite test storage omits timezone metadata.
        created = created.replace(tzinfo=UTC)
    delta = created.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def latest(db, model, scope, field, identifier):
    return db.scalar(
        select(model.version)
        .where(
            *(getattr(model, k) == v for k, v in scope.model_dump().items()),
            getattr(model, field) == identifier,
        )
        .order_by(model.version.desc())
        .limit(1)
    )


def connection_view(db, scope, row):
    return ConnectionView(
        id=row.id,
        scope=scope,
        name=row.name,
        status=row.status,
        current_version=latest(db, ConnectionVersion, scope, "connection_id", row.id),
        created_at_ms=milliseconds(row),
    )


def pool_view(db, scope, row):
    return PoolView(
        id=row.id,
        scope=scope,
        name=row.name,
        status=row.status,
        current_version=row.current_policy_version,
        created_at_ms=milliseconds(row),
    )


def secret_view(scope, row):
    return SecretReferenceView(
        id=row.id,
        scope=scope,
        binding_id=row.binding_id,
        version=row.version,
        status=row.status,
        created_at_ms=milliseconds(row),
    )


def version_view(scope, row):
    return ConnectionVersionView(
        id=row.id,
        scope=scope,
        connection_id=row.connection_id,
        version=row.version,
        sha256=row.config_digest,
        created_at_ms=milliseconds(row),
    )


def policy_view(scope, row):
    return PoolPolicyView(
        id=row.id,
        scope=scope,
        pool_id=row.pool_id,
        version=row.version,
        sha256=row.policy_digest,
        created_at_ms=milliseconds(row),
    )


def stored_config(db: Session, scope: Scope, row: ConnectionVersion) -> ConnectionConfig:
    """Refuse altered canonical bytes, digest or normalized link metadata."""
    try:
        config = ConnectionConfig.model_validate_json(row.canonical_config)
        links = list(
            db.scalars(
                scoped(ConnectionVersionSecret, scope)
                .where(
                    ConnectionVersionSecret.connection_id == row.connection_id,
                    ConnectionVersionSecret.connection_version == row.version,
                )
                .limit(17)
            )
        )
        actual = {(link.secret_reference_id, link.secret_version) for link in links}
        if (
            canonical_connection_json(config) != row.canonical_config
            or connection_digest(config) != row.config_digest
            or len(links) != len(config.secrets)
            or actual != {(pin.reference_id, pin.version) for pin in config.secrets}
        ):
            raise FleetError("authority_unavailable")
        return config
    except FleetError:
        raise FleetError("authority_unavailable") from None


def stored_policy(db: Session, scope: Scope, row: RuntimePoolPolicy) -> PoolPolicy:
    try:
        policy = PoolPolicy.model_validate_json(row.canonical_policy)
        links = list(
            db.scalars(
                scoped(RuntimePoolPolicyConnection, scope)
                .where(
                    RuntimePoolPolicyConnection.pool_id == row.pool_id,
                    RuntimePoolPolicyConnection.policy_version == row.version,
                )
                .limit(65)
            )
        )
        if (
            canonical_pool_json(policy) != row.canonical_policy
            or pool_digest(policy) != row.policy_digest
            or len(links) != len(policy.connection_ids)
            or {link.connection_id for link in links} != set(policy.connection_ids)
        ):
            raise FleetError("authority_unavailable")
        return policy
    except FleetError:
        raise FleetError("authority_unavailable") from None


class ManagementService:
    def __init__(self, db: Session, *, catalog: InstalledCatalog, actor_id: str):
        self.db, self.catalog, self.actor_id = db, catalog, actor_id

    def lock_scope(self, scope):
        scope = Scope.model_validate(scope)
        workspace = self.db.scalar(
            select(Workspace).where(Workspace.id == scope.workspace_id).with_for_update()
        )
        if workspace is None:
            raise FleetError("not_found")
        if workspace.status != "active":
            raise FleetError("forbidden")
        checked_scope(
            self.db,
            Principal(self.actor_id, scope.workspace_id, "admin", self.actor_id),
            scope.project_id,
            scope.environment_id,
        )
        return scope

    def mutation(self, scope, operation, key, content, view, execute):
        from lotuspond_runtime.protocol import OpaqueId

        key = TypeAdapter(OpaqueId).validate_python(key, strict=True)
        digest = hashlib.sha256(
            b"lotuspond-fleet-mutation-v1\0" + rfc8785.dumps(content)
        ).hexdigest()
        previous = self.db.scalar(
            scoped(FleetMutationRequest, scope).where(
                FleetMutationRequest.operation == operation, FleetMutationRequest.request_key == key
            )
        )
        if previous:
            if previous.request_digest != digest:
                raise FleetError("conflict")
            return view.model_validate(previous.result)
        result = execute()
        self.db.flush()
        self.db.add(
            FleetMutationRequest(
                **scope.model_dump(),
                operation=operation,
                request_key=key,
                request_digest=digest,
                result=result.model_dump(mode="json"),
            )
        )
        self.db.flush()
        return result

    def audit(self, scope, code, resource_id, revision=None):
        fleet_audit(self.db, self.actor_id, scope, code, resource_id, revision)

    def validate_config(self, scope, config):
        descriptor = self.catalog.connector(config.connector)
        for operation in config.operations:
            rule = descriptor.operations.get(operation)
            if (
                rule is None
                or (rule.requires_destination and not config.destinations)
                or (rule.requires_secret and not config.secrets)
            ):
                raise FleetError("invalid_request")
        for pin in sorted(config.secrets, key=lambda p: p.reference_id):
            ref = record(self.db, SecretReference, scope, pin.reference_id, lock=True)
            if ref.version != pin.version:
                raise FleetError("not_found")
            if ref.status != "active":
                raise FleetError("conflict")
            if not self.catalog.secret_binding(ref.binding_id, ref.version):
                raise FleetError("invalid_request")

    def register_secret(
        self, scope, binding_id: str, version: int, key: str
    ) -> SecretReferenceView:
        scope = self.lock_scope(scope)
        request = SecretRegistrationRequest(binding_id=binding_id, version=version)
        if not self.catalog.secret_binding(request.binding_id, request.version):
            raise FleetError("invalid_request")

        previous = self.db.scalar(
            scoped(SecretReference, scope)
            .where(
                SecretReference.binding_id == request.binding_id,
                SecretReference.version == request.version,
            )
            .with_for_update()
        )
        if previous is not None and previous.status != "active":
            raise FleetError("conflict")

        def execute():
            if previous is not None:
                raise FleetError("conflict")
            row = SecretReference(**scope.model_dump(), **request.model_dump())
            self.db.add(row)
            self.db.flush()
            self.audit(scope, "fleet.secret.registered", row.id, version)
            return secret_view(scope, row)

        return self.mutation(
            scope, "secret.register", key, request.model_dump(), SecretReferenceView, execute
        )

    def revoke_secret(self, scope, reference_id: str, key: str) -> SecretReferenceView:
        scope = self.lock_scope(scope)
        row = record(self.db, SecretReference, scope, reference_id, lock=True)

        def execute():
            row.status = "revoked"
            self.audit(scope, "fleet.secret.revoked", row.id, row.version)
            return secret_view(scope, row)

        return self.mutation(
            scope, "secret.revoke", key, {"id": row.id}, SecretReferenceView, execute
        )


class ConnectionService(ManagementService):
    def create(self, scope: Scope, name: str, key: str) -> ConnectionView:
        scope = self.lock_scope(scope)
        request = ManagementNameRequest(name=name)

        def execute():
            row = Connection(**scope.model_dump(), name=request.name)
            self.db.add(row)
            self.db.flush()
            self.audit(scope, "fleet.connection.created", row.id)
            return connection_view(self.db, scope, row)

        return self.mutation(
            scope, "connection.create", key, request.model_dump(), ConnectionView, execute
        )

    def publish(
        self, scope: Scope, connection_id: str, config: ConnectionConfig, key: str
    ) -> ConnectionVersionView:
        scope = self.lock_scope(scope)
        row = record(self.db, Connection, scope, connection_id, lock=True)
        if row.status != "active":
            raise FleetError("conflict")
        config = ConnectionConfig.model_validate(config)
        self.validate_config(scope, config)
        content = canonical_connection_json(config)

        def execute():
            version = (latest(self.db, ConnectionVersion, scope, "connection_id", row.id) or 0) + 1
            if version > 2147483647:
                raise FleetError("conflict")
            revision = ConnectionVersion(
                **scope.model_dump(),
                connection_id=row.id,
                version=version,
                canonical_config=content,
                config_digest=connection_digest(config),
            )
            self.db.add(revision)
            self.db.flush()
            for pin in config.secrets:
                self.db.add(
                    ConnectionVersionSecret(
                        **scope.model_dump(),
                        connection_id=row.id,
                        connection_version=version,
                        secret_reference_id=pin.reference_id,
                        secret_version=pin.version,
                    )
                )
            self.audit(scope, "fleet.connection.published", row.id, version)
            return version_view(scope, revision)

        return self.mutation(
            scope,
            "connection.publish",
            key,
            {"id": row.id, "config": decode_fleet_json(content)},
            ConnectionVersionView,
            execute,
        )

    def set_status(
        self, scope: Scope, connection_id: str, status: ResourceStatus, key: str
    ) -> ConnectionView:
        scope = self.lock_scope(scope)
        row = record(self.db, Connection, scope, connection_id, lock=True)
        status = ManagementStatusRequest(status=status).status
        if row.status == "revoked" and status != "revoked":
            raise FleetError("conflict")

        def execute():
            row.status = status
            self.audit(scope, "fleet.connection.status", row.id)
            return connection_view(self.db, scope, row)

        return self.mutation(
            scope,
            "connection.status",
            key,
            {"id": row.id, "status": status},
            ConnectionView,
            execute,
        )

    def detail(self, scope, connection_id, version):
        record(self.db, Connection, scope, connection_id)
        row = self.db.scalar(
            scoped(ConnectionVersion, scope).where(
                ConnectionVersion.connection_id == connection_id,
                ConnectionVersion.version == version,
            )
        )
        if row is None:
            raise FleetError("not_found")
        return ConnectionVersionDetail(
            **version_view(scope, row).model_dump(), config=stored_config(self.db, scope, row)
        )


class PoolService(ManagementService):
    def create(self, scope: Scope, name: str, key: str) -> PoolView:
        scope = self.lock_scope(scope)
        request = ManagementNameRequest(name=name)

        def execute():
            row = RuntimePool(**scope.model_dump(), name=request.name)
            self.db.add(row)
            self.db.flush()
            self.audit(scope, "fleet.pool.created", row.id)
            return pool_view(self.db, scope, row)

        return self.mutation(scope, "pool.create", key, request.model_dump(), PoolView, execute)

    def publish(self, scope: Scope, pool_id: str, policy: PoolPolicy, key: str) -> PoolPolicyView:
        scope = self.lock_scope(scope)
        row = record(self.db, RuntimePool, scope, pool_id, lock=True)
        if row.status != "active":
            raise FleetError("conflict")
        policy = PoolPolicy.model_validate(policy)
        for pin in policy.connectors:
            self.catalog.connector(pin)
        # Workspace lock serializes allocation; lower-level locks still follow
        # pool -> all connections sorted -> all references sorted.
        connections = [
            record(self.db, Connection, scope, identifier, lock=True)
            for identifier in sorted(policy.connection_ids)
        ]
        configs = []
        for connection in connections:
            if connection.status != "active":
                raise FleetError("conflict")
            version = latest(self.db, ConnectionVersion, scope, "connection_id", connection.id)
            revision = self.db.scalar(
                scoped(ConnectionVersion, scope).where(
                    ConnectionVersion.connection_id == connection.id,
                    ConnectionVersion.version == version,
                )
            )
            if revision is None:
                raise FleetError("conflict")
            config = stored_config(self.db, scope, revision)
            if config.connector not in policy.connectors:
                raise FleetError("invalid_request")
            configs.append(config)
        references = sorted({pin.reference_id for config in configs for pin in config.secrets})
        for identifier in references:
            record(self.db, SecretReference, scope, identifier, lock=True)
        for config in configs:
            self.validate_config(scope, config)
        content = canonical_pool_json(policy)

        def execute():
            version = (row.current_policy_version or 0) + 1
            if version > 2147483647:
                raise FleetError("conflict")
            revision = RuntimePoolPolicy(
                **scope.model_dump(),
                pool_id=row.id,
                version=version,
                canonical_policy=content,
                policy_digest=pool_digest(policy),
            )
            self.db.add(revision)
            self.db.flush()
            for identifier in policy.connection_ids:
                self.db.add(
                    RuntimePoolPolicyConnection(
                        **scope.model_dump(),
                        pool_id=row.id,
                        policy_version=version,
                        connection_id=identifier,
                    )
                )
            row.current_policy_version = version
            self.audit(scope, "fleet.pool.published", row.id, version)
            return policy_view(scope, revision)

        return self.mutation(
            scope,
            "pool.publish",
            key,
            {"id": row.id, "policy": decode_fleet_json(content)},
            PoolPolicyView,
            execute,
        )

    def set_status(self, scope: Scope, pool_id: str, status: ResourceStatus, key: str) -> PoolView:
        scope = self.lock_scope(scope)
        row = record(self.db, RuntimePool, scope, pool_id, lock=True)
        status = ManagementStatusRequest(status=status).status
        if row.status == "revoked" and status != "revoked":
            raise FleetError("conflict")

        def execute():
            row.status = status
            self.audit(scope, "fleet.pool.status", row.id)
            return pool_view(self.db, scope, row)

        return self.mutation(
            scope, "pool.status", key, {"id": row.id, "status": status}, PoolView, execute
        )
