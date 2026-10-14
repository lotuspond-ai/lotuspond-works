"""Separate digest-only machine authentication, with shared current-authority locks."""

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import RuntimeIdentity, RuntimeObservation, Scope
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Connection,
    RuntimeCredential,
    RuntimeInstance,
    RuntimePool,
    RuntimePoolPolicy,
    RuntimeSession,
    Workspace,
)
from .authority import FleetDependencies
from .management import record, scoped, stored_policy

TOKEN_CLASSES = {
    "lpe_": b"lotuspond-runtime-enrollment-v1\0",
    "lpr_": b"lotuspond-runtime-credential-v1\0",
    "lps_": b"lotuspond-runtime-session-v1\0",
}


def new_token(prefix: str) -> str:
    if prefix not in TOKEN_CLASSES:
        raise ValueError("Invalid token class")
    return prefix + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def token_digest(token: str, prefix: str) -> str:
    if (
        type(token) is not str
        or prefix not in TOKEN_CLASSES
        or re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]", token) is None
    ):
        raise FleetError("unauthenticated")
    return hashlib.sha256(TOKEN_CLASSES[prefix] + token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class RuntimePrincipal:
    identity: RuntimeIdentity
    credential_id: str
    session_id: str | None


def row_scope(row) -> Scope:
    return Scope(
        workspace_id=row.workspace_id, project_id=row.project_id, environment_id=row.environment_id
    )


def machine_fields(row):
    return {
        **row_scope(row).model_dump(),
        "pool_id": row.pool_id,
        "installation_id": row.installation_id,
        "recovery_epoch": row.recovery_epoch,
    }


def machine_query(model, row):
    query = scoped(model, row_scope(row))
    return query.where(
        model.pool_id == row.pool_id,
        model.installation_id == row.installation_id,
        model.recovery_epoch == row.recovery_epoch,
    )


def lock(query, db):
    return db.scalar(query.with_for_update().execution_options(populate_existing=True))


def lock_pool(db, scope, pool_id, dependencies):
    scope = Scope.model_validate(scope)
    workspace = lock(select(Workspace).where(Workspace.id == scope.workspace_id), db)
    if workspace is None or workspace.status != "active":
        raise FleetError("unauthenticated")
    pool = record(db, RuntimePool, scope, pool_id, lock=True)
    if pool.status != "active" or pool.current_policy_version is None:
        raise FleetError("unauthenticated")
    revision = db.scalar(
        scoped(RuntimePoolPolicy, scope).where(
            RuntimePoolPolicy.pool_id == pool.id,
            RuntimePoolPolicy.version == pool.current_policy_version,
        )
    )
    if revision is None:
        raise FleetError("authority_unavailable")
    policy = stored_policy(db, scope, revision)
    # Serialize with current connection revocation without reversing the
    # management lock order. Historical pins alone never grant current authority.
    for identifier in sorted(policy.connection_ids):
        row = record(db, Connection, scope, identifier, lock=True)
        if row.status != "active":
            raise FleetError("unauthenticated")
    # Identity does not resolve connection secrets. Their current revocation is
    # checked when Task8 admits a specific pinned work requirement.
    return pool, policy


def check_observation(raw, policy, dependencies, ceiling=None):
    observation = RuntimeObservation.model_validate(raw)
    if observation.platform not in policy.platforms or not set(observation.connectors).issubset(
        policy.connectors
    ):
        raise FleetError("unauthenticated")
    if ceiling is not None:
        ceiling = RuntimeObservation.model_validate(ceiling)
        if observation.platform != ceiling.platform or not set(observation.connectors).issubset(
            ceiling.connectors
        ):
            raise FleetError("unauthenticated")
    for pin in observation.connectors:
        try:
            dependencies.catalog.connector(pin)
        except FleetError:
            raise FleetError("unauthenticated") from None
    return observation


def identity_for(instance, credential):
    return RuntimeIdentity(
        scope=row_scope(instance),
        installation_id=instance.installation_id,
        recovery_epoch=instance.recovery_epoch,
        pool_id=instance.pool_id,
        instance_id=instance.id,
        credential_version=credential.version,
        generation=instance.generation,
    )


def authenticate_runtime(
    db: Session,
    token: str,
    *,
    session_secret: str | None,
    require_session: bool,
    dependencies: FleetDependencies,
) -> RuntimePrincipal:
    digest = token_digest(token, "lpr_")
    session_digest = token_digest(session_secret, "lps_") if session_secret is not None else None
    hint = db.scalar(select(RuntimeCredential).where(RuntimeCredential.token_digest == digest))
    if hint is None:
        raise FleetError("unauthenticated")
    scope = row_scope(hint)
    _, policy = lock_pool(db, scope, hint.pool_id, dependencies)
    instance = lock(
        machine_query(RuntimeInstance, hint).where(RuntimeInstance.id == hint.instance_id), db
    )
    if instance is None or instance.status != "active":
        raise FleetError("unauthenticated")
    check_observation(
        RuntimeObservation.model_validate_json(instance.observation_ceiling), policy, dependencies
    )
    # Lock the active session even without process proof, before C. Later
    # self/acquisition services inspect that same row under the shared order.
    selected = RuntimeSession.status == "active"
    if session_digest is not None:
        selected = selected | (RuntimeSession.token_digest == session_digest)
    sessions = list(
        db.scalars(
            machine_query(RuntimeSession, instance)
            .where(RuntimeSession.instance_id == instance.id, selected)
            .order_by(RuntimeSession.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    session = next((row for row in sessions if row.token_digest == session_digest), None)
    if session_digest is not None and session is None:
        raise FleetError("unauthenticated")
    credential = lock(
        machine_query(RuntimeCredential, instance).where(
            RuntimeCredential.id == hint.id,
            RuntimeCredential.instance_id == instance.id,
            RuntimeCredential.token_digest == digest,
        ),
        db,
    )
    dependencies.current(instance.installation_id, instance.recovery_epoch)
    now = dependencies.now(db, scope=scope, actor_id=instance.id, resource_id=instance.id)
    if credential is None or credential.status != "active" or now >= credential.expires_at_ms:
        raise FleetError("unauthenticated")
    if session is not None and (
        session.status != "active"
        or now >= session.expires_at_ms
        or session.generation != instance.generation
    ):
        raise FleetError("unauthenticated")
    if require_session and session is None:
        raise FleetError("unauthenticated")
    return RuntimePrincipal(
        identity_for(instance, credential),
        credential.id,
        session.id if session is not None else None,
    )
