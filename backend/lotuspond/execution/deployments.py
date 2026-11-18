"""Immutable deployment revisions and separate current enablement authority."""

import json
from collections import defaultdict
from hashlib import sha256
from typing import Literal

from lotuspond_runtime.protocol import ConnectorPin
from lotuspond_sdk.executable.package import (
    AgentStep,
    ConnectorReadStep,
    DecisionStep,
    IterationStep,
    ParallelStep,
)
from sqlalchemy import func, select

from .. import authority_gate
from ..fleet.authority import database_now_ms
from ..fleet.management import stored_config, stored_policy
from ..models import (
    Connection,
    ConnectionVersion,
    DeploymentAssuranceBinding,
    DeploymentBinding,
    Execution,
    ExecutionContextSource,
    ExecutionContextVersion,
    ExecutionDeployment,
    ExecutionDeploymentRevision,
    RuntimePool,
    RuntimePoolPolicy,
    SecretReference,
)
from . import memo
from .action_policies import (
    Closure,
    check_write_bindings,
    unreviewed_operations,
    write_operations,
)
from .assurance import release_binding, validated_release_binding
from .authorization import SHARED_OPERATIONS, checked, keep, reauthorize, record, scoped
from .contracts import (
    DeploymentConfig,
    DeploymentRequest,
    DeploymentView,
    canonical_configuration,
    canonical_value,
)
from .errors import ExecutionError, execution_service
from .publication import (
    DEFAULT_DEPENDENCIES,
    closure_of,
    current_snapshot,
    load_publication,
    mutation_replay,
    publication_ref,
    remember,
)


def _view(deployment, revision, unreviewed=()):
    status = revision.status if revision else deployment.status
    return DeploymentView(
        id=deployment.id,
        revision_id=revision.id if revision else deployment.active_revision_id,
        status="enabled" if status == "active" else status,
        unreviewed_operations=tuple(unreviewed),
    )


def stored_unreviewed(db, scope, revision_id) -> tuple[str, ...]:
    """The unreviewed writes of a stored revision, from its pinned bytes; grants nothing."""
    from .program import stored_closure

    if revision_id is None:
        return ()
    revision = record(db, ExecutionDeploymentRevision, scope, revision_id)
    return unreviewed_operations(stored_closure(db, scope, revision.publication_id))


def gates(db, scope, revision_id, operation="execute", users=()):
    """The fair gates for a revision, before any row lock (`authority_gate`).

    The workspace's, then each user's this transaction acts for, shared for an
    operation that shares its rows or exclusive, then the revision's pool's, shared.
    The revision is a plain read: one that is not found takes no pool gate, and is
    refused at its usual place, after authorization.
    """
    exclusive = operation not in SHARED_OPERATIONS
    authority_gate.workspace(db, scope.workspace_id, exclusive=exclusive)
    authority_gate.users(db, users, exclusive=exclusive)
    pool_id = db.scalar(
        scoped(ExecutionDeploymentRevision, scope)
        .where(ExecutionDeploymentRevision.id == revision_id)
        .with_only_columns(ExecutionDeploymentRevision.pool_id)
    )
    if pool_id is not None:
        authority_gate.pool(db, scope.workspace_id, pool_id)


def execution_gates(db, scope, execution_id, operation="execute", users=()):
    """`gates` for an execution, found by a plain read before any row lock: its
    revision's, and its initiator's beside `users`. An execution that is not found
    takes only the workspace and `users` gates, and is refused at its usual place,
    after authorization."""
    row = db.execute(
        select(Execution.deployment_revision_id, Execution.initiating_user_id).where(
            *(getattr(Execution, name) == value for name, value in scope.model_dump().items()),
            Execution.id == execution_id,
        )
    ).first()
    revision_id, initiator = row if row is not None else (None, None)
    gates(db, scope, revision_id, operation, [*users, initiator])


def _pool(db, scope, pool_id):
    """The pool's current policy, its pool and connections shared and active.

    Once per transaction (`checked`): the pool row stays shared to the end, so its
    policy version cannot change, and a version's policy rows never do."""
    entries = checked(db)
    name = ("pool", scope.model_dump_json(), pool_id)
    if entries is not None and name in entries:
        return entries[name]
    policy = _current_pool(db, scope, pool_id)
    keep(db, checked(db), name, policy)
    return policy


def _current_pool(db, scope, pool_id):
    pool = record(db, RuntimePool, scope, pool_id, shared=True)
    if pool.status != "active" or pool.current_policy_version is None:
        raise ExecutionError("deployment_unavailable")
    row = db.scalar(
        scoped(RuntimePoolPolicy, scope).where(
            RuntimePoolPolicy.pool_id == pool.id,
            RuntimePoolPolicy.version == pool.current_policy_version,
        )
    )
    if row is None:
        raise ExecutionError("authority_unavailable")
    try:
        policy = stored_policy(db, scope, row)
        # E03 parent order: workspace, pool, then sorted connection IDs.
        for identifier in sorted(policy.connection_ids):
            if record(db, Connection, scope, identifier, shared=True).status != "active":
                raise ExecutionError("deployment_unavailable")
        return policy
    except ExecutionError:
        raise
    except Exception:
        raise ExecutionError("authority_unavailable") from None


def _body_steps(body):
    """Walk executable positions only; mappings and schemas remain customer data."""
    for step in body.steps:
        yield step
        if isinstance(step, DecisionStep):
            yield from _body_steps(step.then_body)
            yield from _body_steps(step.else_body)
        elif isinstance(step, IterationStep):
            yield from _body_steps(step.body)
        elif isinstance(step, ParallelStep):
            for branch in step.branches:
                yield from _body_steps(branch.body)


def _required_operations(packages, catalog):
    required = defaultdict(set)
    for package in packages:
        declarations = {d.id: d.ref.sha256 for d in package.connectors}
        for step in _body_steps(package.body):
            if isinstance(step, ConnectorReadStep):
                required[declarations[step.connector_ref]].add(step.operation)
            elif isinstance(step, AgentStep):
                for tool in step.read_tools:
                    required[declarations[tool.connector_ref]].add(tool.operation)
    for document in catalog.documents:
        if document.kind in {"model", "agent"}:
            for tool in document.definition.read_tools:
                required[tool.connector_ref.sha256].add(tool.operation)
    return required


def _configuration(db, scope, configuration, package, links, rows, sources, policy, snapshot):
    """Validate every exact pin, current status and normalized secret/source linkage."""
    configuration = DeploymentConfig.model_validate(configuration)
    for name in type(package.limits).model_fields:
        if getattr(configuration.limits, name) > getattr(package.limits, name):
            raise ExecutionError("invalid_request")
    expected = {
        canonical_value(publication_ref(row).model_dump()): (link, row)
        for link, row in zip(links, rows, strict=True)
    }
    bindings = {
        canonical_value(binding.dependency.model_dump()): binding
        for binding in configuration.bindings
    }
    if set(expected) != set(bindings):
        raise ExecutionError("dependency_unavailable")
    workflow_packages = [
        package,
        *[memo.package(sources[row.id]) for row in rows if row.kind == "workflow"],
    ]
    catalog = memo.catalog(
        {"documents": [sources[row.id] for row in rows if row.kind != "workflow"]}
    )
    required_operations = _required_operations(workflow_packages, catalog)
    closure = Closure(
        packages=tuple(workflow_packages),
        catalog={
            row.package_sha256: memo.catalog({"documents": [sources[row.id]]}).documents[0]
            for row in rows
            if row.kind != "workflow"
        },
    )
    write_required = write_operations(closure)
    connections = {}
    packages = [source.model_dump(mode="json") for source in workflow_packages]
    for source in packages:
        environment = source["environment"]
        if (
            not set(environment["required_capabilities"]) <= set(snapshot.capabilities)
            or not set(environment["destination_classes"]) <= set(snapshot.destination_classes)
            or not set(environment["operation_classes"]) <= set(snapshot.operation_classes)
            or any(p["os"] not in policy.platforms for p in environment["platforms"])
        ):
            raise ExecutionError("dependency_unavailable")
    all_secret_names = set(package.secret_refs)
    found_secrets = set()
    now_ms = database_now_ms(db)
    normalized = []
    for identity in sorted(bindings):
        binding = bindings[identity]
        link, row = expected[identity]
        definition = sources[row.id].get("definition", {})
        if row.kind == "connector":
            if (
                binding.connection_id is None
                or binding.source_id is not None
                or binding.connection_id not in policy.connection_ids
            ):
                raise ExecutionError("dependency_unavailable")
            connection = record(db, Connection, scope, binding.connection_id)
            version = db.scalar(
                scoped(ConnectionVersion, scope).where(
                    ConnectionVersion.connection_id == binding.connection_id,
                    ConnectionVersion.version == binding.connection_version,
                )
            )
            if connection.status != "active" or version is None:
                raise ExecutionError("dependency_unavailable")
            try:
                config = stored_config(db, scope, version)
                pin = ConnectorPin(
                    name=row.name,
                    version=row.version,
                    artifact_sha256=definition["artifact_sha256"],
                )
                # Reads always; a write only when an effect step needs it (checked below).
                declared = {op["id"]: op["effect"] for op in definition["operations"]}
                writes = write_required.get(row.package_sha256, set())
                if (
                    config.connector != pin
                    or pin not in policy.connectors
                    or not all(
                        declared.get(op) == "read" or op in writes for op in config.operations
                    )
                ):
                    raise ValueError
                secrets = set()
                for secret_pin in config.secrets:
                    secret = record(db, SecretReference, scope, secret_pin.reference_id)
                    if secret.version != secret_pin.version or secret.status != "active":
                        raise ValueError
                    secrets.add(secret.binding_id)
                if not secrets <= all_secret_names:
                    raise ValueError
                found_secrets.update(secrets)
                for source in packages:
                    for declaration in source["connectors"]:
                        if declaration["ref"]["sha256"] == row.package_sha256:
                            credential = declaration.get("credential_ref")
                            if credential is not None and credential not in secrets:
                                raise ValueError
                # Every actual connector-read/tool operation must be permitted by this connection.
                if not required_operations[row.package_sha256] <= set(config.operations):
                    raise ValueError
                connections[row.package_sha256] = (config.connector, frozenset(config.operations))
            except Exception:
                raise ExecutionError("dependency_unavailable") from None
        elif row.kind in {"knowledge", "artifact"}:
            if binding.source_id is None or binding.connection_id is not None:
                raise ExecutionError("dependency_unavailable")
            source = record(db, ExecutionContextSource, scope, binding.source_id)
            version = db.scalar(
                scoped(ExecutionContextVersion, scope).where(
                    ExecutionContextVersion.source_id == source.id,
                    ExecutionContextVersion.version == binding.source_version,
                )
            )
            if (
                source.status != "active"
                or version is None
                or version.expires_at_ms <= now_ms
                or version.content_sha256 != definition["content_sha256"]
                or version.classification != definition["classification"]
                or version.access_policy_id != source.access_policy_id
            ):
                raise ExecutionError("dependency_unavailable")
            if row.kind == "knowledge" and (
                str(version.version) != definition["source_revision"]
                or version.observed_at_ms > now_ms
                or now_ms - version.observed_at_ms > definition["freshness_seconds"] * 1000
                or version.citation_artifact_id is None
            ):
                raise ExecutionError("dependency_unavailable")
            if row.kind == "knowledge":
                for declared_package in packages:
                    for declaration in declared_package["knowledge"]:
                        if declaration["ref"]["sha256"] == row.package_sha256 and (
                            version.classification not in declaration["allowed_classifications"]
                            or now_ms - version.observed_at_ms
                            > declaration["required_freshness_seconds"] * 1000
                        ):
                            raise ExecutionError("dependency_unavailable")
        elif binding.connection_id is not None or binding.source_id is not None:
            raise ExecutionError("dependency_unavailable")
        normalized.append((link, binding))
    if found_secrets != all_secret_names:
        raise ExecutionError("dependency_unavailable")
    # E05: each write is bound to a connection pinning its connector and allowing the
    # write, its read-back and any verification read (dependency_unavailable), and the
    # read-back accepts the write's target and input (unsupported_operation).
    check_write_bindings(closure, connections)
    return normalized


def _stored_configuration(db, scope, revision, links):
    try:
        config = DeploymentConfig.model_validate_json(revision.canonical_configuration)
        if (
            canonical_configuration(config) != revision.canonical_configuration
            or sha256(revision.canonical_configuration).hexdigest() != revision.configuration_sha256
        ):
            raise ValueError
        stored = list(
            db.scalars(
                scoped(DeploymentBinding, scope)
                .where(DeploymentBinding.deployment_revision_id == revision.id)
                .limit(512)
            )
        )
        by_dependency = {link.id: link for link in links}
        actual = set()
        for binding in stored:
            link = by_dependency[binding.dependency_id]
            if binding.publication_id != revision.publication_id:
                raise ValueError
            actual.add(
                (
                    link.canonical_dependency,
                    binding.connection_id,
                    binding.connection_version,
                    binding.source_id,
                    binding.source_version,
                )
            )
        expected = {
            (
                canonical_value(b.dependency.model_dump()),
                b.connection_id,
                b.connection_version,
                b.source_id,
                b.source_version,
            )
            for b in config.bindings
        }
        if len(stored) != len(config.bindings) or actual != expected:
            raise ValueError
        return config
    except Exception:
        raise ExecutionError("dependency_unavailable") from None


@execution_service
def create_deployment(
    db, actor, request: DeploymentRequest, request_key: str, *, dependencies=DEFAULT_DEPENDENCIES
):
    actor = reauthorize(db, actor, "manage")
    request = DeploymentRequest.model_validate(request)
    policy = _pool(db, actor.scope, request.pool_id)
    snapshot = dependencies.current(db, actor.scope)
    publication, package, links, rows, sources = load_publication(
        db, actor.scope, request.publication_id, snapshot
    )
    normalized = _configuration(
        db, actor.scope, request.configuration, package, links, rows, sources, policy, snapshot
    )
    content = canonical_configuration(request.configuration)
    configuration_sha256 = sha256(content).hexdigest()
    evaluation = request.configuration.evaluation
    release = release_binding(
        db,
        actor,
        publication,
        package,
        request.release_id,
        configuration_sha256,
        snapshot,
        dependencies,
        sources,
        evaluation=evaluation,
    )
    deployment = (
        record(db, ExecutionDeployment, actor.scope, request.deployment_id, locked=True)
        if request.deployment_id
        else None
    )
    if deployment and deployment.status == "revoked":
        raise ExecutionError("deployment_unavailable")
    current_snapshot(db, snapshot)
    unreviewed = unreviewed_operations(closure_of(json.loads(publication.canonical_package), rows, sources))
    if evaluation and unreviewed:
        # A package evaluation cancels each execution at its review; a write without
        # one would reach the evaluation connection's system.
        raise ExecutionError("invalid_request")
    digest, replay = mutation_replay(
        db,
        actor,
        "execution.deployment.create",
        request_key,
        request.model_dump(mode="json"),
        DeploymentView,
    )
    if replay:
        current, revision = load_deployment(
            db,
            actor,
            replay.revision_id,
            operation="manage",
            require_enabled=False,
            dependencies=dependencies,
        )
        return _view(current, revision, unreviewed)
    if deployment is None:
        deployment = ExecutionDeployment(**actor.scope.model_dump(), status="disabled")
        db.add(deployment)
        db.flush()
    number = (
        db.scalar(
            select(func.max(ExecutionDeploymentRevision.revision)).where(
                ExecutionDeploymentRevision.workspace_id == actor.scope.workspace_id,
                ExecutionDeploymentRevision.project_id == actor.scope.project_id,
                ExecutionDeploymentRevision.environment_id == actor.scope.environment_id,
                ExecutionDeploymentRevision.deployment_id == deployment.id,
            )
        )
        or 0
    )
    revision = ExecutionDeploymentRevision(
        **actor.scope.model_dump(),
        deployment_id=deployment.id,
        publication_id=publication.id,
        pool_id=request.pool_id,
        revision=number + 1,
        canonical_configuration=content,
        configuration_sha256=configuration_sha256,
        status="disabled",
    )
    db.add(revision)
    db.flush()
    for link, binding in normalized:
        db.add(
            DeploymentBinding(
                **actor.scope.model_dump(),
                deployment_revision_id=revision.id,
                publication_id=publication.id,
                dependency_id=link.id,
                connection_id=binding.connection_id,
                connection_version=binding.connection_version,
                source_id=binding.source_id,
                source_version=binding.source_version,
            )
        )
    if release:
        db.add(
            DeploymentAssuranceBinding(
                **actor.scope.model_dump(),
                deployment_revision_id=revision.id,
                release_id=release[0],
                candidate_run_id=release[1],
            )
        )
    return remember(
        db,
        actor,
        "execution.deployment.create",
        request_key,
        digest,
        _view(deployment, revision, unreviewed),
    )


def evaluation_only(revision) -> bool:
    """Whether a deployment revision exists only to prove its package (`purpose`).

    Read from the revision's immutable canonical configuration; every authorizing path
    also checks those bytes against their digest (`_stored_configuration`)."""
    try:
        return DeploymentConfig.model_validate_json(revision.canonical_configuration).evaluation
    except Exception:
        raise ExecutionError("deployment_unavailable") from None


@execution_service
def load_deployment(
    db,
    actor,
    revision_id,
    *,
    operation="execute",
    require_enabled=True,
    dependencies=DEFAULT_DEPENDENCIES,
    include_assurance=False,
    snapshot=None,
):
    """Recheck pins/current authority using the shared lock order.

    include_assurance retains fully validated expiring evidence for a final guard
    in this same transaction; it is not reusable authority. Default stays a pair.
    `snapshot` is a publisher snapshot the caller looked up before its first lock
    (admission). It is checked here exactly as one looked up now, its expiry again at
    the end, and the caller's final guard looks one up again.
    """
    gates(db, actor.scope, revision_id, operation, [actor.user_id])
    actor = reauthorize(db, actor, operation)
    hint = record(db, ExecutionDeploymentRevision, actor.scope, revision_id)
    policy = _pool(db, actor.scope, hint.pool_id)
    revision = hint
    if snapshot is None:
        snapshot = dependencies.current(db, actor.scope)
    elif snapshot.scope != actor.scope:
        raise ExecutionError("trust_unavailable")
    publication, package, links, rows, sources = load_publication(
        db, actor.scope, revision.publication_id, snapshot
    )
    config = _stored_configuration(db, actor.scope, revision, links)
    _configuration(db, actor.scope, config, package, links, rows, sources, policy, snapshot)
    # Shared for an operation that shares its account rows; a management caller keeps
    # them exclusive, and may then change them in this transaction.
    shared = operation in SHARED_OPERATIONS
    deployment = record(
        db, ExecutionDeployment, actor.scope, hint.deployment_id, locked=True, shared=shared
    )
    revision = record(
        db, ExecutionDeploymentRevision, actor.scope, revision_id, locked=True, shared=shared
    )
    if (
        deployment.status == "revoked"
        or revision.status == "revoked"
        or require_enabled
        and (deployment.status != "active" or revision.status != "active")
    ):
        raise ExecutionError("deployment_unavailable")
    binding = db.scalar(
        scoped(DeploymentAssuranceBinding, actor.scope).where(
            DeploymentAssuranceBinding.deployment_revision_id == revision.id
        )
    )
    evidence = validated_release_binding(
        db,
        actor,
        publication,
        package,
        binding.release_id if binding else None,
        revision.configuration_sha256,
        snapshot,
        dependencies,
        sources,
        evaluation=config.evaluation,
    )
    result = (evidence.release_id, evidence.candidate_run_id) if evidence else None
    if binding and result != (binding.release_id, binding.candidate_run_id):
        raise ExecutionError("assurance_unavailable")
    current_snapshot(db, snapshot)
    if include_assurance:
        return deployment, revision, evidence
    return deployment, revision


@execution_service
def set_deployment_status(
    db,
    actor,
    deployment_id: str,
    revision_id: str | None,
    status: Literal["enabled", "disabled", "revoked"],
    request_key: str,
    *,
    dependencies=DEFAULT_DEPENDENCIES,
):
    actor = reauthorize(db, actor, "manage")
    if status not in {"enabled", "disabled", "revoked"}:
        raise ExecutionError("invalid_request")
    hint = record(db, ExecutionDeployment, actor.scope, deployment_id)
    target_id = revision_id or hint.active_revision_id
    if status == "enabled":
        if target_id is None:
            raise ExecutionError("deployment_unavailable")
        deployment, checked = load_deployment(
            db,
            actor,
            target_id,
            operation="manage",
            require_enabled=False,
            dependencies=dependencies,
        )
        if deployment.id != deployment_id:
            raise ExecutionError("not_found")
    else:
        # Withdrawing authority must remain possible during provider/connector outages.
        deployment = record(db, ExecutionDeployment, actor.scope, deployment_id, locked=True)
        checked = (
            record(db, ExecutionDeploymentRevision, actor.scope, revision_id, locked=True)
            if revision_id
            else None
        )
        if checked and checked.deployment_id != deployment.id:
            raise ExecutionError("not_found")
    revision = checked if revision_id else None
    digest, replay = mutation_replay(
        db,
        actor,
        "execution.deployment.status",
        request_key,
        {"deployment_id": deployment_id, "revision_id": revision_id, "status": status},
        DeploymentView,
    )
    if replay:
        return _view(deployment, revision)
    target = revision or deployment
    if target.status == "revoked" and status != "revoked":
        raise ExecutionError("deployment_unavailable")
    if status == "enabled" and not revision and checked.status != "active":
        raise ExecutionError("deployment_unavailable")
    target.status = "active" if status == "enabled" else status
    if status == "enabled":
        if revision:
            deployment.active_revision_id = revision.id
            deployment.status = "active"
    return remember(
        db, actor, "execution.deployment.status", request_key, digest, _view(deployment, revision)
    )
