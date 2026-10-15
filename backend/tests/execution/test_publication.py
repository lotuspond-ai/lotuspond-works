"""Exact admission from persisted sources and independent operator trust."""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lotuspond_runtime.protocol import PoolPolicy, Scope, canonical_pool_json, pool_digest
from lotuspond_sdk.executable import canonical_executable_json, compile_package
from lotuspond_sdk.executable.catalog import canonical_catalog_document, catalog_document_digest
from lotuspond_sdk.executable.plan import canonical_plan_json
from lotuspond_sdk.executable.signing import sign_executable_package
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.contracts import PublicationRequest
from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.publication import ExecutionDependencies, publish_v2
from lotuspond.models import (
    Base,
    Environment,
    ExecutableDependency,
    ExecutablePublication,
    Membership,
    Project,
    RuntimePool,
    RuntimePoolPolicy,
    User,
    Workspace,
    new_id,
)
from lotuspond.security import Principal

EXAMPLES = Path(__file__).resolve().parents[3] / "contracts/examples/executable-v2"


def example(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text())


class FixtureAuthority:
    test_only = True

    def __init__(self, scope, key):
        public = key.public_key().public_bytes_raw()
        self.snapshot = {
            "scope": scope.model_dump(),
            "expires_at_ms": 9007199254740991,
            "trust": {
                "format_version": 1,
                "keys": [
                    {
                        "key_id": sha256(public).hexdigest(),
                        "public_key": public.hex(),
                        "state": "allowed",
                    }
                ],
            },
            "catalog": [],
            "release_required": False,
            "capabilities": [],
            "destination_classes": [],
            "operation_classes": [],
        }

    def current(self, scope):
        return deepcopy(self.snapshot)


@pytest.fixture
def harness(execution_database):
    engine = execution_database()
    deepcopy(Base.metadata).create_all(engine)
    scope = Scope(workspace_id=new_id(), project_id=new_id(), environment_id=new_id())
    user_id, pool_id = new_id(), new_id()
    with Session(engine) as db, db.begin():
        db.add(User(id=user_id, email="execution@example.test", name="test", password_hash="x"))
        db.flush()
        db.add(Workspace(id=scope.workspace_id, name="test", slug="execution", owner_id=user_id))
        db.flush()
        db.add_all(
            [
                Membership(workspace_id=scope.workspace_id, user_id=user_id, role="owner"),
                Project(id=scope.project_id, workspace_id=scope.workspace_id, name="test"),
            ]
        )
        db.flush()
        db.add(
            Environment(
                id=scope.environment_id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                name="test",
                kind="local",
            )
        )
        db.flush()
        db.add(RuntimePool(**scope.model_dump(), id=pool_id, name="test", status="active"))
        db.flush()
        policy = PoolPolicy(
            format_version=1,
            connectors=(),
            platforms=("linux",),
            connection_ids=(),
            max_instances=1,
            max_concurrent_invocations=1,
        )
        db.add(
            RuntimePoolPolicy(
                **scope.model_dump(),
                pool_id=pool_id,
                version=1,
                canonical_policy=canonical_pool_json(policy),
                policy_digest=pool_digest(policy),
            )
        )
        db.flush()
        db.get(RuntimePool, pool_id).current_policy_version = 1
    key = Ed25519PrivateKey.generate()
    authority = FixtureAuthority(scope, key)
    dependencies = ExecutionDependencies(authority=authority, test_mode=True)
    principal = Principal(user_id, scope.workspace_id, "owner", user_id)
    value = SimpleNamespace(
        engine=engine,
        scope=scope,
        principal=principal,
        key=key,
        authority=authority,
        dependencies=dependencies,
        pool_id=pool_id,
    )

    def publish(db, raw=None, dependency_ids=(), request_key="publish"):
        raw = example("minimal") if raw is None else raw
        is_workflow = "schema_version" in raw
        canonical = (
            canonical_executable_json(raw) if is_workflow else canonical_catalog_document(raw)
        )
        digest = sha256(canonical).hexdigest()
        if not is_workflow and digest not in authority.snapshot["catalog"]:
            authority.snapshot["catalog"].append(digest)
        request = PublicationRequest(
            package=raw,
            dependency_ids=dependency_ids,
            signature=sign_executable_package(raw, key).model_dump() if is_workflow else None,
        )
        actor = authorize_execution(db, principal, scope, "publish")
        return publish_v2(db, actor, request, request_key, dependencies=dependencies)

    value.publish = publish
    yield value
    dependencies.close()


def test_published_bytes_and_closure_are_exact(harness):
    h = harness
    package = example("minimal")
    document = example("catalog")["documents"][1]
    package["models"] = [
        {
            "id": "model",
            "ref": {
                "kind": "model",
                "name": document["name"],
                "version": document["version"],
                "sha256": catalog_document_digest(document),
            },
        }
    ]
    with Session(h.engine) as db, db.begin():
        dependency = h.publish(db, document, request_key="catalog")
        view = h.publish(db, package, (dependency.id,))
        row = db.get(ExecutablePublication, view.id)
        stored_package_bytes = row.canonical_package
        assert stored_package_bytes == canonical_executable_json(package)
        assert row.package_sha256 == sha256(stored_package_bytes).hexdigest()
        plan = compile_package(package, dependencies=[], catalog={"documents": [document]})
        assert row.canonical_plan == canonical_plan_json(plan)
        link = db.scalar(
            select(ExecutableDependency).where(ExecutableDependency.publication_id == row.id)
        )
        assert json.loads(link.canonical_dependency) == package["models"][0]["ref"]
        assert link.dependency_publication_id == dependency.id
        assert h.publish(db, package, (dependency.id,)).id == view.id


@pytest.mark.parametrize(
    "kind", ["action_intent", "action_dispatch", "outcome_verification", "review"]
)
def test_effect_nodes_refused(harness, kind):
    # Unsupported nodes are refused before compiler graph errors can obscure the boundary.
    raw = example("minimal")
    raw["body"]["steps"] = [
        {"kind": "human_task" if kind == "review" else kind, "mode": "review", "id": "unsupported"}
    ]
    with Session(harness.engine) as db, db.begin(), pytest.raises(ExecutionError) as error:
        actor = authorize_execution(db, harness.principal, harness.scope, "publish")
        publish_v2(
            db,
            actor,
            PublicationRequest(package=raw, dependency_ids=(), signature=None),
            "unsupported",
            dependencies=harness.dependencies,
        )
    effect_node_error = error.value
    assert effect_node_error.code == "unsupported_step"


def test_missing_provider_and_unknown_fields_deny(harness):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PublicationRequest(package={}, dependency_ids=(), signature=None, trusted=True)
    with (
        Session(harness.engine) as db,
        db.begin(),
        pytest.raises(ExecutionError, match="trust_unavailable"),
    ):
        actor = authorize_execution(db, harness.principal, harness.scope, "publish")
        publish_v2(
            db,
            actor,
            PublicationRequest(
                package=example("minimal"),
                dependency_ids=(),
                signature=sign_executable_package(example("minimal"), harness.key).model_dump(),
            ),
            "x",
        )


@pytest.mark.parametrize("change", ["signature", "revoked", "scope", "expiry"])
def test_stored_signatures_and_current_scoped_publisher_trust(harness, change):
    from lotuspond_sdk.executable.signing import executable_signature_envelope_json

    from lotuspond.execution.publication import load_publication

    h = harness
    with Session(h.engine) as db, db.begin():
        publication = h.publish(db)
        row = db.get(ExecutablePublication, publication.id)
        assert row.canonical_signature == executable_signature_envelope_json(
            sign_executable_package(example("minimal"), h.key)
        )
        if change == "signature":
            envelope = json.loads(row.canonical_signature)
            envelope["signature"] = "0" * 128
            row.canonical_signature = executable_signature_envelope_json(envelope)
            db.flush()
        elif change == "revoked":
            h.authority.snapshot["trust"]["keys"][0]["state"] = "revoked"
        elif change == "scope":
            h.authority.snapshot["scope"]["environment_id"] = new_id()
        else:
            h.authority.snapshot["expires_at_ms"] = 0
        with pytest.raises(ExecutionError, match="trust_unavailable"):
            snapshot = h.dependencies.current(db, h.scope)
            load_publication(db, h.scope, publication.id, snapshot)


def test_transitive_catalog_closure_is_complete_and_exact(harness):
    h = harness
    model, agent = example("catalog")["documents"][1:3]
    package = example("minimal")
    package["agents"] = [
        {
            "id": "agent",
            "ref": {
                "kind": "agent",
                "name": agent["name"],
                "version": agent["version"],
                "sha256": catalog_document_digest(agent),
            },
        }
    ]
    with Session(h.engine) as db, db.begin():
        m = h.publish(db, model, request_key="model")
        a = h.publish(db, agent, (m.id,), request_key="agent")
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            h.publish(db, package, (a.id,), request_key="missing-transitive")
        publication = h.publish(db, package, (m.id, a.id))
        assert (
            len(
                list(
                    db.scalars(
                        select(ExecutableDependency).where(
                            ExecutableDependency.publication_id == publication.id
                        )
                    )
                )
            )
            == 2
        )
        link = db.scalar(
            select(ExecutableDependency).where(ExecutableDependency.publication_id == a.id)
        )
        db.delete(link)
        db.flush()
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            h.publish(db, package, (m.id, a.id))


def test_missing_wrong_untrusted_and_unreachable_catalog_pins(harness):
    h = harness
    model = example("catalog")["documents"][1]
    package = example("minimal")
    package["models"] = [
        {
            "id": "model",
            "ref": {
                "kind": "model",
                "name": model["name"],
                "version": model["version"],
                "sha256": catalog_document_digest(model),
            },
        }
    ]
    with Session(h.engine) as db, db.begin():
        m = h.publish(db, model, request_key="model")
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            h.publish(db, package, (), request_key="missing")
        wrong = deepcopy(package)
        wrong["models"][0]["ref"]["sha256"] = "0" * 64
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            h.publish(db, wrong, (m.id,), request_key="wrong")
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            h.publish(db, example("minimal"), (m.id,), request_key="extra")
        h.authority.snapshot["catalog"] = []
        with pytest.raises(ExecutionError, match="trust_unavailable"):
            h.publish(db, package, (m.id,), request_key="revoked")


def test_cross_environment_dependency_is_not_resolved(harness):
    h = harness
    with Session(h.engine) as db, db.begin():
        foreign = Environment(
            workspace_id=h.scope.workspace_id,
            project_id=h.scope.project_id,
            name="other",
            kind="local",
        )
        db.add(foreign)
        db.flush()
        document = example("catalog")["documents"][1]
        canonical = canonical_catalog_document(document)
        row = ExecutablePublication(
            **{**h.scope.model_dump(), "environment_id": foreign.id},
            kind="model",
            name=document["name"],
            version=document["version"],
            canonical_package=canonical,
            package_sha256=sha256(canonical).hexdigest(),
            validator_id="unused",
            publisher_user_id=h.principal.user_id,
        )
        db.add(row)
        db.flush()
        with pytest.raises(ExecutionError, match="not_found"):
            h.publish(db, dependency_ids=(row.id,))


def test_publication_race_cannot_replace_a_version(harness):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import func

    h = harness
    barrier = Barrier(2)

    def publish(name):
        raw = example("minimal")
        raw["name"] = name
        with Session(h.engine) as db, db.begin():
            barrier.wait(timeout=5)
            try:
                return h.publish(db, raw, request_key=name).id
            except ExecutionError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, name) for name in ("one", "two")]
        outcomes = [future.result(timeout=10) for future in futures]
    assert outcomes.count("conflict") == 1
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutablePublication)) == 1


def test_provider_deadline_is_bounded_and_synthetic_not_production_selectable(harness):
    from threading import Event

    h = harness
    with pytest.raises(ValueError, match="Synthetic providers"):
        ExecutionDependencies(authority=h.authority)
    released = Event()

    class StuckAuthority:
        test_only = True

        def current(self, scope):
            released.wait(5)
            return h.authority.current(scope)

    deps = ExecutionDependencies(authority=StuckAuthority(), test_mode=True, timeout_seconds=0.01)
    try:
        with Session(h.engine) as db, db.begin():
            with pytest.raises(ExecutionError, match="trust_unavailable"):
                deps.current(db, h.scope)
            with pytest.raises(ExecutionError, match="trust_unavailable"):
                deps.current(db, h.scope)
    finally:
        released.set()
        deps.close()


def test_publication_request_is_bounded_and_errors_are_value_free():
    secret = "private-provider-value"
    for package in ({"value": "x" * 1048576}, {"value": float("nan")}, {"value": 10**40}):
        with pytest.raises(ExecutionError) as error:
            PublicationRequest(package=package, dependency_ids=(), signature=None)
        assert str(error.value) == "invalid_request"
    assert str(ExecutionError(secret)) == "invalid_request"


def test_catalog_admission_enforces_aggregate_sdk_closure_limits(harness):
    h = harness
    model = example("catalog")["documents"][1]
    template = example("catalog")["documents"][0]
    read = template["definition"]["operations"][0]
    model["definition"]["read_tools"] = []
    identifiers = []
    with Session(h.engine) as db, db.begin():
        for index in range(8):
            document = deepcopy(template)
            document["name"] = f"large-connector-{index}"
            document["definition"]["operations"] = [
                {**deepcopy(read), "id": f"read-{n}"} for n in range(64)
            ]
            dependency = h.publish(db, document, request_key=f"connector-{index}")
            identifiers.append(dependency.id)
            model["definition"]["read_tools"].append(
                {
                    "connector_ref": {
                        "kind": "connector",
                        "name": document["name"],
                        "version": "1.0.0",
                        "sha256": catalog_document_digest(document),
                    },
                    "operation": "read-0",
                    "retry": {
                        "max_attempts": 1,
                        "timeout_seconds": 10,
                        "backoff_seconds": 0,
                        "retry_codes": [],
                    },
                }
            )
        with pytest.raises(ExecutionError, match="dependency_unavailable"):
            h.publish(db, model, tuple(identifiers), request_key="aggregate")


def test_request_json_rejects_duplicate_keys_before_pydantic_projection():
    for raw in (
        b'{"package":{},"package":{},"dependency_ids":[],"signature":null}',
        b'{"package":{"name":"first","name":"second"},"dependency_ids":[],"signature":null}',
        b'{"package":{},"dependency_ids":[],"signature":null}' + b" " * 1048576,
    ):
        with pytest.raises(ExecutionError, match="invalid_request"):
            PublicationRequest.model_validate_json(raw)
    accepted = PublicationRequest.model_validate_json(
        b'{"package":{},"dependency_ids":[],"signature":null}'
    )
    assert accepted.dependency_ids == ()
