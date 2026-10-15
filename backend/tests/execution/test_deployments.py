import pytest
from sqlalchemy.orm import Session

from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.contracts import DeploymentConfig, DeploymentRequest, ExecutionLimits
from lotuspond.execution.deployments import create_deployment, set_deployment_status
from lotuspond.execution.errors import ExecutionError
from lotuspond.models import ExecutionDeployment, ExecutionDeploymentRevision

from .test_publication import example
from .test_publication import harness as harness


def deployment_request(h, publication_id, deployment_id=None):
    return DeploymentRequest(
        deployment_id=deployment_id,
        publication_id=publication_id,
        pool_id=h.pool_id,
        release_id=None,
        configuration=DeploymentConfig(
            bindings=(),
            limits=ExecutionLimits(
                **example("minimal")["limits"], active_capacity=1, pending_capacity=10
            ),
        ),
    )


def test_old_revision_stays_pinned(harness):
    h = harness
    with Session(h.engine) as db, db.begin():
        publication = h.publish(db)
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        first = create_deployment(
            db, actor, deployment_request(h, publication.id), "first", dependencies=h.dependencies
        )
        set_deployment_status(
            db,
            actor,
            first.id,
            first.revision_id,
            "enabled",
            "enable1",
            dependencies=h.dependencies,
        )
        old = db.get(ExecutionDeploymentRevision, first.revision_id)
        pinned = old.canonical_configuration, old.configuration_sha256, old.publication_id
        request = deployment_request(h, publication.id, first.id)
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(
                    update={
                        "limits": request.configuration.limits.model_copy(
                            update={"pending_capacity": 9}
                        )
                    }
                )
            }
        )
        second = create_deployment(db, actor, request, "second", dependencies=h.dependencies)
        set_deployment_status(
            db,
            actor,
            first.id,
            second.revision_id,
            "enabled",
            "enable2",
            dependencies=h.dependencies,
        )
        assert db.get(ExecutionDeployment, first.id).active_revision_id == second.revision_id
        assert (old.canonical_configuration, old.configuration_sha256, old.publication_id) == pinned
        assert old.status == "active"
        assert second.revision_id != first.revision_id


def test_deployment_replay_rechecks_current_trust(harness):
    h = harness
    with Session(h.engine) as db, db.begin():
        publication = h.publish(db)
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        request = deployment_request(h, publication.id)
        first = create_deployment(db, actor, request, "first", dependencies=h.dependencies)
        assert create_deployment(db, actor, request, "first", dependencies=h.dependencies) == first
        h.authority.snapshot["trust"]["keys"][0]["state"] = "revoked"
        with pytest.raises(ExecutionError, match="trust_unavailable"):
            create_deployment(db, actor, request, "first", dependencies=h.dependencies)


def form_package():
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    package = example("minimal")
    policy = example("catalog")["documents"][4]
    policy["definition"]["assignment"] = "workspace_admins"
    package["policies"] = [
        {
            "id": "admins",
            "ref": {
                "kind": "policy",
                "name": policy["name"],
                "version": policy["version"],
                "sha256": catalog_document_digest(policy),
            },
        }
    ]
    package["body"]["steps"] = [
        {
            "id": "form",
            "kind": "human_task",
            "mode": "form",
            "assignment_policy_ref": "admins",
            "timeout_seconds": 30,
            "form_schema": {"kind": "null"},
        }
    ]
    return package, policy


def test_form_supported_and_unsupported_assignment_not_dropped(harness):
    from lotuspond.execution.contracts import DependencyBinding

    h = harness
    raw, policy = form_package()
    with Session(h.engine) as db, db.begin():
        p = h.publish(db, policy, request_key="policy")
        package = h.publish(db, raw, (p.id,))
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        request = deployment_request(h, package.id)
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(
                    update={"bindings": (DependencyBinding(dependency=raw["policies"][0]["ref"]),)}
                )
            }
        )
        deployed = create_deployment(db, actor, request, "form", dependencies=h.dependencies)
        assert deployed.status == "disabled"
        policy["definition"]["assignment"] = "arbitrary-group"
        with pytest.raises(ExecutionError, match="unsupported_policy"):
            h.publish(db, policy, request_key="arbitrary")


def test_revocation_overrides_old_pins_and_outage_does_not_block_withdrawal(harness):
    from lotuspond.execution.deployments import load_deployment

    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        view = create_deployment(
            db, actor, deployment_request(h, p.id), "create", dependencies=h.dependencies
        )
        set_deployment_status(
            db, actor, view.id, view.revision_id, "enabled", "enable", dependencies=h.dependencies
        )
        assert (
            load_deployment(db, actor, view.revision_id, dependencies=h.dependencies)[1].id
            == view.revision_id
        )
        # Default dependencies are unavailable; withdrawal still works.
        assert (
            set_deployment_status(db, actor, view.id, None, "disabled", "disable").status
            == "disabled"
        )
        with pytest.raises(ExecutionError, match="deployment_unavailable"):
            load_deployment(db, actor, view.revision_id, dependencies=h.dependencies)
        set_deployment_status(db, actor, view.id, None, "revoked", "revoke")
        with pytest.raises(ExecutionError, match="deployment_unavailable"):
            set_deployment_status(
                db,
                actor,
                view.id,
                view.revision_id,
                "enabled",
                "new-enable",
                dependencies=h.dependencies,
            )


def test_configuration_normalized_links_and_digest_are_rechecked(harness):
    from lotuspond.execution.contracts import DependencyBinding
    from lotuspond.execution.deployments import load_deployment
    from lotuspond.models import DeploymentBinding

    h = harness
    raw, policy = form_package()
    with Session(h.engine) as db, db.begin():
        p = h.publish(db, policy, request_key="policy")
        package = h.publish(db, raw, (p.id,))
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        request = deployment_request(h, package.id)
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(
                    update={"bindings": (DependencyBinding(dependency=raw["policies"][0]["ref"]),)}
                )
            }
        )
        view = create_deployment(db, actor, request, "form", dependencies=h.dependencies)
        db.flush()
        row = db.query(DeploymentBinding).filter_by(deployment_revision_id=view.revision_id).one()
        db.delete(row)
        db.flush()
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            load_deployment(
                db,
                actor,
                view.revision_id,
                operation="manage",
                require_enabled=False,
                dependencies=h.dependencies,
            )


def test_configuration_limits_cannot_raise_package_or_global_ceilings(harness):
    from pydantic import ValidationError

    h = harness
    with pytest.raises(ValidationError):
        ExecutionLimits(**example("minimal")["limits"], active_capacity=6, pending_capacity=10)
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        request = deployment_request(h, p.id)
        changed = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(
                    update={
                        "limits": request.configuration.limits.model_copy(
                            update={"active_seconds": 101}
                        )
                    }
                )
            }
        )
        with pytest.raises(ExecutionError, match="invalid_request"):
            create_deployment(db, actor, changed, "too-large", dependencies=h.dependencies)


def test_database_enforces_signature_provenance_constraints(harness):
    from sqlalchemy.exc import IntegrityError

    from lotuspond.models import ExecutablePublication

    h = harness
    with Session(h.engine) as db, db.begin():
        p = h.publish(db)
        for invalid in (None, b"", b"x" * 65537):
            with pytest.raises(IntegrityError), db.begin_nested():
                db.get(ExecutablePublication, p.id).canonical_signature = invalid
                db.flush()


@pytest.mark.parametrize(
    "corruption", ["connection_revoked", "secret_revoked", "secret_link", "wrong_artifact"]
)
def test_connector_and_secret_pins_are_exact_and_current(harness, corruption):
    from hashlib import sha256

    from lotuspond_runtime.protocol import (
        ConnectionConfig,
        ConnectorPin,
        DataHandling,
        PoolPolicy,
        SecretPin,
        canonical_connection_json,
        canonical_pool_json,
        connection_digest,
        pool_digest,
    )
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    from lotuspond.execution.contracts import DependencyBinding
    from lotuspond.execution.deployments import load_deployment
    from lotuspond.models import (
        Connection,
        ConnectionVersion,
        ConnectionVersionSecret,
        RuntimePool,
        RuntimePoolPolicy,
        RuntimePoolPolicyConnection,
        SecretReference,
    )

    h = harness
    document = example("catalog")["documents"][0]
    raw = example("minimal")
    reference = {
        "kind": "connector",
        "name": document["name"],
        "version": document["version"],
        "sha256": catalog_document_digest(document),
    }
    raw["connectors"] = [{"id": "read", "ref": reference, "credential_ref": "fixture-secret"}]
    raw["secret_refs"] = ["fixture-secret"]
    with Session(h.engine) as db, db.begin():
        dep = h.publish(db, document, request_key="connector")
        publication = h.publish(db, raw, (dep.id,))
        connection = Connection(**h.scope.model_dump(), name="read", status="active")
        secret = SecretReference(
            **h.scope.model_dump(), binding_id="fixture-secret", version=1, status="active"
        )
        db.add_all([connection, secret])
        db.flush()
        pin = ConnectorPin(
            name=document["name"],
            version=document["version"],
            artifact_sha256=document["definition"]["artifact_sha256"],
        )
        config = ConnectionConfig(
            connector=pin,
            operations=("read",),
            destinations=(),
            secrets=(SecretPin(reference_id=secret.id, version=1),),
            data_handling=DataHandling(),
        )
        version = ConnectionVersion(
            **h.scope.model_dump(),
            connection_id=connection.id,
            version=1,
            canonical_config=canonical_connection_json(config),
            config_digest=connection_digest(config),
        )
        db.add(version)
        db.flush()
        secret_link = ConnectionVersionSecret(
            **h.scope.model_dump(),
            connection_id=connection.id,
            connection_version=1,
            secret_reference_id=secret.id,
            secret_version=1,
        )
        db.add(secret_link)
        pool_policy = PoolPolicy(
            connectors=(pin,),
            platforms=("linux",),
            connection_ids=(connection.id,),
            max_instances=1,
            max_concurrent_invocations=1,
        )
        db.add(
            RuntimePoolPolicy(
                **h.scope.model_dump(),
                pool_id=h.pool_id,
                version=2,
                canonical_policy=canonical_pool_json(pool_policy),
                policy_digest=pool_digest(pool_policy),
            )
        )
        db.flush()
        db.add(
            RuntimePoolPolicyConnection(
                **h.scope.model_dump(),
                pool_id=h.pool_id,
                policy_version=2,
                connection_id=connection.id,
            )
        )
        db.get(RuntimePool, h.pool_id).current_policy_version = 2
        db.flush()
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        request = deployment_request(h, publication.id)
        binding = DependencyBinding(
            dependency=reference, connection_id=connection.id, connection_version=1
        )
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(update={"bindings": (binding,)})
            }
        )
        view = create_deployment(db, actor, request, "create", dependencies=h.dependencies)
        set_deployment_status(
            db, actor, view.id, view.revision_id, "enabled", "enable", dependencies=h.dependencies
        )
        if corruption == "connection_revoked":
            connection.status = "revoked"
        elif corruption == "secret_revoked":
            secret.status = "revoked"
        elif corruption == "secret_link":
            db.delete(secret_link)
        else:
            changed = config.model_copy(
                update={"connector": pin.model_copy(update={"artifact_sha256": "0" * 64})}
            )
            version.canonical_config = canonical_connection_json(changed)
            version.config_digest = sha256(version.canonical_config).hexdigest()
        db.flush()
        with pytest.raises(ExecutionError, match="deployment_unavailable|dependency_unavailable"):
            load_deployment(db, actor, view.revision_id, dependencies=h.dependencies)


@pytest.mark.parametrize("corruption", ["revoked", "stale", "classification", "digest"])
def test_context_pins_require_exact_current_source_metadata(harness, corruption):
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    from lotuspond.execution.contracts import DependencyBinding
    from lotuspond.execution.deployments import load_deployment
    from lotuspond.fleet.authority import database_now_ms
    from lotuspond.models import ExecutionArtifact, ExecutionContextSource, ExecutionContextVersion

    h = harness
    document = example("catalog")["documents"][5]
    reference = {
        "kind": "knowledge",
        "name": document["name"],
        "version": document["version"],
        "sha256": catalog_document_digest(document),
    }
    raw = example("minimal")
    raw["knowledge"] = [
        {
            "id": "context",
            "ref": reference,
            "required_freshness_seconds": 30,
            "allowed_classifications": ["synthetic"],
            "citation_schema": {"kind": "null"},
        }
    ]
    with Session(h.engine) as db, db.begin():
        dep = h.publish(db, document, request_key="knowledge")
        p = h.publish(db, raw, (dep.id,))
        now_ms = database_now_ms(db)
        source = ExecutionContextSource(**h.scope.model_dump(), access_policy_id="fixture")
        db.add(source)
        db.flush()
        version = ExecutionContextVersion(
            **h.scope.model_dump(),
            source_id=source.id,
            version=1,
            classification="synthetic",
            observed_at_ms=now_ms,
            expires_at_ms=now_ms + 60000,
            access_policy_id="fixture",
        )
        db.add(version)
        db.flush()
        # Only metadata links are under test; retrieval/decryption belongs to Task4.
        artifact = ExecutionArtifact(
            **h.scope.model_dump(),
            version=1,
            sha256="b" * 64,
            ciphertext=b"x" * 17,
            nonce=b"n" * 12,
            content_bytes=1,
            classification="synthetic",
            source_id=source.id,
            source_version=1,
            access_policy_id="fixture",
            key_id="fixture",
            retain_until_ms=now_ms + 60000,
        )
        db.add(artifact)
        db.flush()
        version.content_artifact_id = version.citation_artifact_id = artifact.id
        version.content_artifact_version = version.citation_artifact_version = 1
        version.content_sha256 = version.citation_sha256 = artifact.sha256
        db.flush()
        actor = authorize_execution(db, h.principal, h.scope, "manage")
        request = deployment_request(h, p.id)
        binding = DependencyBinding(dependency=reference, source_id=source.id, source_version=1)
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(update={"bindings": (binding,)})
            }
        )
        view = create_deployment(db, actor, request, "context", dependencies=h.dependencies)
        set_deployment_status(
            db, actor, view.id, view.revision_id, "enabled", "enable", dependencies=h.dependencies
        )
        if corruption == "revoked":
            source.status = "revoked"
        elif corruption == "stale":
            version.observed_at_ms = now_ms - 31000
        elif corruption == "classification":
            version.classification = "confidential"
        else:
            # Preserve FK agreement while altering the pinned content identity.
            version.content_artifact_id = version.content_artifact_version = (
                version.content_sha256
            ) = None
        db.flush()
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            load_deployment(db, actor, view.revision_id, dependencies=h.dependencies)
