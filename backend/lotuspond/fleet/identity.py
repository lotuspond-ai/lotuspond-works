"""One-use enrollment and fenced process ownership, in the caller transaction."""

import rfc8785
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    AcquiredSession,
    EnrollmentIssued,
    EnrollmentView,
    InstanceView,
    IssuedCredential,
    RuntimeObservation,
    Scope,
    SelfState,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Membership,
    RuntimeCredential,
    RuntimeEnrollment,
    RuntimeInstance,
    RuntimePool,
    RuntimeSession,
)
from ..security import Principal, require_admin
from .audit import fleet_audit
from .authentication import (
    RuntimePrincipal,
    check_observation,
    identity_for,
    lock,
    lock_pool,
    machine_fields,
    machine_query,
    new_token,
    row_scope,
    token_digest,
)
from .authority import FleetDependencies
from .management import ManagementService, milliseconds, record


def enrollment_view(row):
    return EnrollmentView(
        id=row.id,
        scope=row_scope(row),
        pool_id=row.pool_id,
        status=row.status,
        policy_version=row.policy_version,
        expires_at_ms=row.expires_at_ms,
        consumed_at_ms=row.consumed_at_ms,
        created_at_ms=milliseconds(row),
    )


def instance_view(db, row):
    pool = record(db, RuntimePool, row_scope(row), row.pool_id)
    return InstanceView(
        id=row.id,
        scope=row_scope(row),
        pool_id=row.pool_id,
        status=row.status,
        installation_id=row.installation_id,
        recovery_epoch=row.recovery_epoch,
        generation=row.generation,
        current_policy_version=pool.current_policy_version,
        last_seen_at_ms=row.last_seen_at_ms,
        created_at_ms=milliseconds(row),
    )


def issue_enrollment(
    db: Session,
    principal: Principal,
    scope: Scope,
    pool_id: str,
    ceiling: RuntimeObservation,
    dependencies: FleetDependencies,
) -> EnrollmentIssued:
    require_admin(principal)
    scope = Scope.model_validate(scope)
    if principal.workspace_id != scope.workspace_id:
        raise FleetError("forbidden")
    pool, policy = lock_pool(db, scope, pool_id, dependencies)
    member = db.get(Membership, (scope.workspace_id, principal.user_id), populate_existing=True)
    if member is None or member.role not in {"owner", "admin"}:
        raise FleetError("forbidden")
    ceiling = RuntimeObservation.model_validate(ceiling)
    allowed = []
    for pin in ceiling.connectors:
        if pin in policy.connectors:
            try:
                dependencies.catalog.connector(pin)
            except FleetError:
                continue
            allowed.append(pin)
    ceiling = RuntimeObservation(platform=ceiling.platform, connectors=tuple(allowed))
    dependencies.limit("enrollment:pool:" + pool.id, 60)
    snapshot = dependencies.current(dependencies.installation_id)
    now = dependencies.now(db, scope=scope, actor_id=principal.actor_id, resource_id=pool.id)
    outstanding = db.scalar(
        scoped_count(RuntimeEnrollment, scope).where(
            RuntimeEnrollment.pool_id == pool.id,
            RuntimeEnrollment.status == "issued",
            RuntimeEnrollment.expires_at_ms > now,
        )
    )
    if outstanding >= 100:
        raise FleetError("conflict")
    token = new_token("lpe_")
    row = RuntimeEnrollment(
        **scope.model_dump(),
        pool_id=pool.id,
        installation_id=snapshot.installation_id,
        recovery_epoch=snapshot.epoch,
        policy_version=pool.current_policy_version,
        observation_ceiling=rfc8785.dumps(ceiling.model_dump(mode="json")),
        token_digest=token_digest(token, "lpe_"),
        status="issued",
        expires_at_ms=now + 600000,
    )
    db.add(row)
    db.flush()
    fleet_audit(db, principal.actor_id, scope, "fleet.enrollment.issued", row.id, None)
    return EnrollmentIssued(enrollment_id=row.id, token=token, expires_at_ms=row.expires_at_ms)


def scoped_count(model, scope):
    return (
        select(func.count())
        .select_from(model)
        .where(*(getattr(model, key) == value for key, value in scope.model_dump().items()))
    )


def exchange_enrollment(
    db: Session, token: str, observation: RuntimeObservation, dependencies: FleetDependencies
) -> IssuedCredential:
    digest = token_digest(token, "lpe_")
    hint = db.scalar(select(RuntimeEnrollment).where(RuntimeEnrollment.token_digest == digest))
    if hint is None:
        raise FleetError("unauthenticated")
    scope = row_scope(hint)
    pool, policy = lock_pool(db, scope, hint.pool_id, dependencies)
    row = lock(
        machine_query(RuntimeEnrollment, hint).where(
            RuntimeEnrollment.id == hint.id, RuntimeEnrollment.token_digest == digest
        ),
        db,
    )
    if row is None or row.status != "issued":
        raise FleetError("unauthenticated")
    dependencies.current(row.installation_id, row.recovery_epoch)
    now = dependencies.now(db, scope=scope, actor_id=row.id, resource_id=row.id)
    if now >= row.expires_at_ms:
        raise FleetError("unauthenticated")
    observation = check_observation(
        observation,
        policy,
        dependencies,
        RuntimeObservation.model_validate_json(row.observation_ceiling),
    )
    allocated = db.scalar(
        scoped_count(RuntimeInstance, scope).where(
            RuntimeInstance.pool_id == pool.id, RuntimeInstance.status == "active"
        )
    )
    if allocated >= policy.max_instances:
        raise FleetError("conflict")
    instance = RuntimeInstance(
        **machine_fields(row),
        status="active",
        generation=0,
        observation_ceiling=rfc8785.dumps(observation.model_dump(mode="json")),
        last_seen_at_ms=now,
    )
    db.add(instance)
    db.flush()
    token = new_token("lpr_")
    credential = RuntimeCredential(
        **machine_fields(instance),
        instance_id=instance.id,
        version=1,
        token_digest=token_digest(token, "lpr_"),
        status="active",
        expires_at_ms=now + 900000,
    )
    db.add(credential)
    row.status, row.consumed_at_ms = "consumed", now
    db.flush()
    fleet_audit(db, instance.id, scope, "fleet.enrollment.exchanged", instance.id, None)
    return IssuedCredential(
        token=token,
        state=SelfState(
            identity=identity_for(instance, credential),
            credential_expires_at_ms=credential.expires_at_ms,
            session_expires_at_ms=None,
            server_time_ms=now,
        ),
    )


def locked_identity(db, principal, dependencies, *, require_session=False):
    """Recheck current state for a principal constructed inside the runtime boundary.

    Active credentials' historical preparation-session metadata is deliberately
    irrelevant to safe self-state or recovery acquisition after lease expiry.
    """
    from lotuspond_runtime.protocol import RuntimeIdentity

    identity = RuntimeIdentity.model_validate(principal.identity)
    scope = identity.scope
    _, policy = lock_pool(db, scope, identity.pool_id, dependencies)
    instance = record(db, RuntimeInstance, scope, identity.instance_id, lock=True)
    if (
        instance.pool_id != identity.pool_id
        or instance.installation_id != identity.installation_id
        or instance.recovery_epoch != identity.recovery_epoch
        or instance.status != "active"
    ):
        raise FleetError("unauthenticated")
    check_observation(
        RuntimeObservation.model_validate_json(instance.observation_ceiling), policy, dependencies
    )
    # Lock sessions before credentials, sharing the management parent order.
    sessions = list(
        db.scalars(
            machine_query(RuntimeSession, instance)
            .where(
                RuntimeSession.instance_id == instance.id,
                (RuntimeSession.status == "active") | (RuntimeSession.id == principal.session_id),
            )
            .order_by(RuntimeSession.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    session = next((row for row in sessions if row.id == principal.session_id), None)
    credential = lock(
        machine_query(RuntimeCredential, instance).where(
            RuntimeCredential.id == principal.credential_id,
            RuntimeCredential.instance_id == instance.id,
        ),
        db,
    )
    dependencies.current(instance.installation_id, instance.recovery_epoch)
    now = dependencies.now(db, scope=scope, actor_id=instance.id, resource_id=instance.id)
    if (
        credential is None
        or credential.status != "active"
        or now >= credential.expires_at_ms
        or credential.version != identity.credential_version
    ):
        raise FleetError("unauthenticated")
    if principal.session_id is not None and (
        session is None
        or session.status != "active"
        or now >= session.expires_at_ms
        or session.generation != instance.generation
        or identity.generation != instance.generation
    ):
        raise FleetError("unauthenticated")
    if require_session and session is None:
        raise FleetError("unauthenticated")
    return instance, credential, session, sessions, now


def acquire_session(
    db: Session, principal: RuntimePrincipal, dependencies: FleetDependencies
) -> AcquiredSession:
    dependencies.limit("session:" + principal.identity.instance_id, 10)
    instance, credential, _, sessions, now = locked_identity(db, principal, dependencies)
    if any(row.status == "active" and now < row.expires_at_ms for row in sessions):
        raise FleetError("conflict")
    if instance.generation >= 9007199254740991:
        raise FleetError("conflict")
    for row in sessions:
        row.status = "revoked"
    pending = list(
        db.scalars(
            machine_query(RuntimeCredential, instance)
            .where(
                RuntimeCredential.instance_id == instance.id, RuntimeCredential.status == "pending"
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for row in pending:
        row.status = "revoked"
    db.flush()  # Release partial unique indexes before creating the successor.
    instance.generation += 1
    secret = new_token("lps_")
    session = RuntimeSession(
        **machine_fields(instance),
        instance_id=instance.id,
        generation=instance.generation,
        token_digest=token_digest(secret, "lps_"),
        status="active",
        expires_at_ms=now + 60000,
    )
    db.add(session)
    instance.last_seen_at_ms = now
    db.flush()
    fleet_audit(db, instance.id, row_scope(instance), "fleet.session.acquired", instance.id, None)
    return AcquiredSession(
        secret=secret,
        identity=identity_for(instance, credential),
        expires_at_ms=session.expires_at_ms,
        server_time_ms=now,
    )


def self_state(
    db: Session, principal: RuntimePrincipal, dependencies: FleetDependencies
) -> SelfState:
    instance, credential, _, sessions, now = locked_identity(db, principal, dependencies)
    active = next(
        (row for row in sessions if row.status == "active" and now < row.expires_at_ms), None
    )
    return SelfState(
        identity=identity_for(instance, credential),
        credential_expires_at_ms=credential.expires_at_ms,
        session_expires_at_ms=active.expires_at_ms if active else None,
        server_time_ms=now,
    )


def heartbeat(
    db: Session, principal: RuntimePrincipal, dependencies: FleetDependencies
) -> SelfState:
    dependencies.limit("heartbeat:" + principal.identity.instance_id, 10)
    instance, credential, session, _, now = locked_identity(
        db, principal, dependencies, require_session=True
    )
    session.expires_at_ms = now + 60000
    instance.last_seen_at_ms = now
    db.flush()
    return SelfState(
        identity=identity_for(instance, credential),
        credential_expires_at_ms=credential.expires_at_ms,
        session_expires_at_ms=session.expires_at_ms,
        server_time_ms=now,
    )


class IdentityManagement(ManagementService):
    def revoke_enrollment(self, scope, pool_id, enrollment_id, key):
        scope = self.lock_scope(scope)
        record(self.db, RuntimePool, scope, pool_id, lock=True)
        row = record(self.db, RuntimeEnrollment, scope, enrollment_id, lock=True)
        if row.pool_id != pool_id:
            raise FleetError("not_found")

        def execute():
            if row.status != "issued":
                raise FleetError("conflict")
            row.status = "revoked"
            self.audit(scope, "fleet.enrollment.revoked", row.id)
            return enrollment_view(row)

        return self.mutation(
            scope, "enrollment.revoke", key, {"id": row.id}, EnrollmentView, execute
        )

    def revoke_instance(self, scope, instance_id, key):
        scope = self.lock_scope(scope)
        hint = record(self.db, RuntimeInstance, scope, instance_id)
        record(self.db, RuntimePool, scope, hint.pool_id, lock=True)
        row = record(self.db, RuntimeInstance, scope, instance_id, lock=True)

        def execute():
            row.status = "revoked"
            sessions = list(
                self.db.scalars(
                    machine_query(RuntimeSession, row)
                    .where(RuntimeSession.instance_id == row.id, RuntimeSession.status == "active")
                    .order_by(RuntimeSession.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            for session in sessions:
                session.status = "revoked"
            credentials = list(
                self.db.scalars(
                    machine_query(RuntimeCredential, row)
                    .where(
                        RuntimeCredential.instance_id == row.id,
                        RuntimeCredential.status.in_(("active", "pending")),
                    )
                    .order_by(RuntimeCredential.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            for credential in credentials:
                credential.status = "revoked"
            self.audit(scope, "fleet.instance.revoked", row.id)
            return instance_view(self.db, row)

        return self.mutation(scope, "instance.revoke", key, {"id": row.id}, InstanceView, execute)
