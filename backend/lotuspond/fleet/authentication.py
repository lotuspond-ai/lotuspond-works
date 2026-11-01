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

from .. import authority_gate
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


def lock(query, db, *, share=False):
    query = query.with_for_update(read=share)
    return db.scalar(query.execution_options(populate_existing=True))


def lock_pool(db, scope, pool_id, dependencies, *, share=False, users=()):
    """The workspace, pool and connection rows, in E03's parent order.

    E04 throughput option C (owner decisions 2026-10-30, "Split by caller" and "C with
    the fair gate"). The fair gates come first, before any row lock
    (`authority_gate`): the workspace's shared, then the pool's. Then the workspace
    and connection rows are taken FOR SHARE, which still fences their revocation: an
    authority change takes its gate exclusive and the rows FOR UPDATE.
    - `share`, for callers that need the rows only to fence a revocation: the pool's
      gate shared and its row FOR SHARE.
    - Otherwise, for a caller that counts or allocates under the pool row (enrollment
      issue and exchange): the pool's gate exclusive and its row FOR UPDATE, from the
      first lock of it.
    `users` are the users whose rows the caller's transaction will lock (an
    activity's initiator): their gates, shared, come between the workspace's and the
    pool's.
    """
    scope = Scope.model_validate(scope)
    authority_gate.pool(db, scope.workspace_id, pool_id, exclusive=not share, users_=users)
    workspace = lock(select(Workspace).where(Workspace.id == scope.workspace_id), db, share=True)
    if workspace is None or workspace.status != "active":
        raise FleetError("unauthenticated")
    pool = record(db, RuntimePool, scope, pool_id, lock=True, share=share)
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
        row = record(db, Connection, scope, identifier, lock=True, share=True)
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


def runtime_hint(db: Session, token: str, session_secret: str | None):
    """The runtime a request names, by plain reads before any lock: grants nothing.

    For the fair gates its call takes first (`authority_gate`); the locked
    authentication after them still decides."""
    try:
        digest = token_digest(token, "lpr_")
        session_digest = (
            token_digest(session_secret, "lps_") if session_secret is not None else None
        )
    except Exception:
        return None
    credential = db.scalar(
        select(RuntimeCredential).where(RuntimeCredential.token_digest == digest)
    )
    if credential is None:
        return None
    instance = db.scalar(
        select(RuntimeInstance).where(RuntimeInstance.id == credential.instance_id)
    )
    if instance is None:
        return None
    session_id = None
    if session_digest is not None:
        session_id = db.scalar(
            select(RuntimeSession.id).where(
                RuntimeSession.instance_id == instance.id,
                RuntimeSession.token_digest == session_digest,
            )
        )
    return RuntimePrincipal(identity_for(instance, credential), credential.id, session_id)


def authenticate_runtime(
    db: Session,
    token: str,
    *,
    session_secret: str | None,
    require_session: bool,
    dependencies: FleetDependencies,
    users=(),
) -> RuntimePrincipal:
    digest = token_digest(token, "lpr_")
    session_digest = token_digest(session_secret, "lps_") if session_secret is not None else None
    hint = db.scalar(select(RuntimeCredential).where(RuntimeCredential.token_digest == digest))
    if hint is None:
        raise FleetError("unauthenticated")
    scope = row_scope(hint)
    _, policy = lock_pool(db, scope, hint.pool_id, dependencies, share=True, users=users)
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
