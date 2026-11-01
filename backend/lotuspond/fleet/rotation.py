"""Digest-only two-phase credential rotation in the caller's transaction.

The pending activation and already-active metadata retry paths deliberately
have different proof requirements. The latter never changes process authority.
"""

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import PreparedCredential, RuntimeObservation, SelfState
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import RuntimeCredential, RuntimeInstance, RuntimePool, RuntimeSession
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
from .identity import locked_identity
from .management import record


def rotation_limit(dependencies, instance_id):
    # One shared per-instance budget, including metadata retries.
    dependencies.limit("rotation:" + instance_id, 10)


def final_time(db, instance, dependencies):
    """Call only after all quota/row-lock waits, before any dirty mutation."""
    dependencies.current(instance.installation_id, instance.recovery_epoch)
    return dependencies.now(
        db, scope=row_scope(instance), actor_id=instance.id, resource_id=instance.id
    )


def require_live(credential, now):
    if credential is None or credential.status != "active" or now >= credential.expires_at_ms:
        raise FleetError("unauthenticated")


def require_process(instance, session, now):
    if (
        session is None
        or session.status != "active"
        or session.generation != instance.generation
        or now >= session.expires_at_ms
    ):
        raise FleetError("unauthenticated")


def pending_successor(db, instance):
    return lock(
        machine_query(RuntimeCredential, instance).where(
            RuntimeCredential.instance_id == instance.id, RuntimeCredential.status == "pending"
        ),
        db,
    )


def state(instance, credential, sessions, now):
    live = next(
        (
            s
            for s in sessions
            if s.status == "active"
            and s.generation == instance.generation
            and now < s.expires_at_ms
        ),
        None,
    )
    return SelfState(
        identity=identity_for(instance, credential),
        credential_expires_at_ms=credential.expires_at_ms,
        session_expires_at_ms=live.expires_at_ms if live else None,
        server_time_ms=now,
    )


def prepare_rotation(
    db: Session, principal: RuntimePrincipal, dependencies: FleetDependencies
) -> PreparedCredential:
    rotation_limit(dependencies, principal.identity.instance_id)
    instance, credential, session, _, _ = locked_identity(
        db, principal, dependencies, require_session=True
    )
    pending = pending_successor(db, instance)
    # MAX is scoped and returns one scalar, including cancelled/taken-over history.
    version = db.scalar(
        machine_query(RuntimeCredential, instance)
        .with_only_columns(func.max(RuntimeCredential.version))
        .where(RuntimeCredential.instance_id == instance.id)
    )
    pool = record(db, RuntimePool, row_scope(instance), instance.pool_id)  # Already locked.
    now = final_time(db, instance, dependencies)
    require_live(credential, now)
    require_process(instance, session, now)
    if version >= 2147483647:
        raise FleetError("conflict")
    if pending is not None:
        pending.status = "revoked"
    credential.successor_id = None
    db.flush()  # Release the one-pending partial index before replacement.
    token = new_token("lpr_")
    candidate = RuntimeCredential(
        **machine_fields(instance),
        instance_id=instance.id,
        version=version + 1,
        token_digest=token_digest(token, "lpr_"),
        status="pending",
        # Pending validity is its deadline; the active lifetime starts at activation.
        expires_at_ms=now + 60000,
        predecessor_id=credential.id,
        session_id=session.id,
        generation=instance.generation,
        prepared_policy_version=pool.current_policy_version,
        activation_deadline_ms=now + 60000,
    )
    db.add(candidate)
    db.flush()
    credential.successor_id = candidate.id
    fleet_audit(
        db,
        instance.id,
        row_scope(instance),
        "fleet.credential.prepared",
        instance.id,
        candidate.version,
    )
    db.flush()
    return PreparedCredential(
        token=token,
        credential_version=candidate.version,
        activation_deadline_ms=candidate.activation_deadline_ms,
        server_time_ms=now,
    )


def activate_rotation(
    db: Session, token: str, session_secret: str | None, dependencies: FleetDependencies
) -> SelfState:
    digest = token_digest(token, "lpr_")
    proof = token_digest(session_secret, "lps_") if session_secret is not None else None
    hint = db.scalar(select(RuntimeCredential).where(RuntimeCredential.token_digest == digest))
    if hint is None:
        raise FleetError("unauthenticated")
    rotation_limit(dependencies, hint.instance_id)
    scope = row_scope(hint)
    pool, policy = lock_pool(db, scope, hint.pool_id, dependencies, share=True)
    instance = lock(
        machine_query(RuntimeInstance, hint).where(RuntimeInstance.id == hint.instance_id), db
    )
    if instance is None or instance.status != "active":
        raise FleetError("unauthenticated")
    check_observation(
        RuntimeObservation.model_validate_json(instance.observation_ceiling), policy, dependencies
    )
    # Match the shared parent -> session -> credential lock order. Include proof
    # rows as well as the active lease, refreshing caller-cached ORM attributes.
    selected = RuntimeSession.status == "active"
    if proof is not None:
        selected = selected | (RuntimeSession.token_digest == proof)
    sessions = list(
        db.scalars(
            machine_query(RuntimeSession, instance)
            .where(RuntimeSession.instance_id == instance.id, selected)
            .order_by(RuntimeSession.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    credentials = list(
        db.scalars(
            machine_query(RuntimeCredential, instance)
            .where(
                RuntimeCredential.instance_id == instance.id,
                (RuntimeCredential.status.in_(("active", "pending")))
                | (RuntimeCredential.id == hint.id),
            )
            .order_by(RuntimeCredential.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    credential = next((r for r in credentials if r.token_digest == digest), None)
    now = final_time(db, instance, dependencies)
    if credential is None or credential.predecessor_id is None:
        raise FleetError("unauthenticated")
    if credential.status == "active":
        require_live(credential, now)
        # Safe response-loss recovery: no C/session proof, no new lease, no
        # lifetime extension, and especially no modification of later pending Q.
        return state(instance, credential, sessions, now)
    if (
        credential.status != "pending"
        or credential.activation_deadline_ms is None
        or now >= credential.activation_deadline_ms
        or now >= credential.expires_at_ms
        or credential.prepared_policy_version != pool.current_policy_version
        or credential.generation != instance.generation
    ):
        raise FleetError("unauthenticated")
    predecessor = next((r for r in credentials if r.id == credential.predecessor_id), None)
    require_live(predecessor, now)
    session = next((s for s in sessions if s.token_digest == proof), None)
    require_process(instance, session, now)
    if session.id != credential.session_id or predecessor.successor_id != credential.id:
        raise FleetError("unauthenticated")
    predecessor.status, predecessor.successor_id = "revoked", None
    db.flush()  # Release active partial uniqueness before promoting P.
    credential.status = "active"
    credential.expires_at_ms = now + 900000
    fleet_audit(
        db, instance.id, scope, "fleet.credential.activated", instance.id, credential.version
    )
    db.flush()
    return state(instance, credential, sessions, now)


def cancel_rotation(
    db: Session, principal: RuntimePrincipal, dependencies: FleetDependencies
) -> SelfState:
    rotation_limit(dependencies, principal.identity.instance_id)
    instance, credential, session, sessions, _ = locked_identity(
        db, principal, dependencies, require_session=True
    )
    pending = pending_successor(db, instance)
    now = final_time(db, instance, dependencies)
    require_live(credential, now)
    require_process(instance, session, now)
    if pending is not None and (
        pending.predecessor_id == credential.id and credential.successor_id == pending.id
    ):
        pending.status = "revoked"
        credential.successor_id = None
        fleet_audit(
            db,
            instance.id,
            row_scope(instance),
            "fleet.credential.cancelled",
            instance.id,
            pending.version,
        )
        db.flush()
    return state(instance, credential, sessions, now)
