"""E05 Task 3: effect packages publish, deploy and admit under v2 on real PostgreSQL."""

import json
from pathlib import Path

import pytest
from lotuspond_runtime.protocol import (
    ConnectionConfig,
    canonical_connection_json,
    connection_digest,
)
from sqlalchemy.orm import Session

from lotuspond.execution.action_contracts import INTERPRETER_V2
from lotuspond.execution.contracts import AdmissionRequest
from lotuspond.execution.deployments import create_deployment, set_deployment_status
from lotuspond.execution.engine_contracts import INTERPRETER_V1
from lotuspond.execution.errors import ExecutionError
from lotuspond.models import ConnectionVersion, Execution
from tests.execution.test_deployments import connection_request

from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401

ACTIONS = Path(__file__).parents[1] / "fixtures" / "actions"


def doc(name):
    return json.loads((ACTIONS / f"{name}.json").read_text())


def publish(h, db, package, documents, key):
    ids = tuple(
        h.publish(db, document, request_key=f"{key}-catalog-{i}").id
        for i, document in enumerate(documents)
    )
    return h.publish(db, package, ids, request_key=key)


def bind(h, db, publication, documents, operations=("lookup", "submit"), artifact=None):
    """A deployment request binding the connector to a new version of one connection."""
    from lotuspond.execution.contracts import DependencyBinding
    from tests.execution.test_deployments import catalog_ref

    if getattr(h, "connection_request", None) is None:
        h.connection_request = connection_request(db, h, publication, documents)
        h.connection_versions = 1
    template = h.connection_request
    binding = template.configuration.bindings[0]
    first = (
        db.query(ConnectionVersion).filter_by(connection_id=binding.connection_id, version=1).one()
    )
    config = ConnectionConfig.model_validate_json(first.canonical_config)
    connector = config.connector
    if artifact is not None:
        connector = connector.model_copy(update={"artifact_sha256": artifact})
    config = config.model_copy(update={"connector": connector, "operations": tuple(operations)})
    h.connection_versions += 1
    db.add(
        ConnectionVersion(
            **h.scope.model_dump(),
            connection_id=binding.connection_id,
            version=h.connection_versions,
            canonical_config=canonical_connection_json(config),
            config_digest=connection_digest(config),
        )
    )
    db.flush()
    bindings = (
        DependencyBinding(
            dependency=catalog_ref(documents[0]),
            connection_id=binding.connection_id,
            connection_version=h.connection_versions,
        ),
        *(DependencyBinding(dependency=catalog_ref(d)) for d in documents[1:]),
    )
    configuration = template.configuration.model_copy(update={"bindings": bindings})
    return template.model_copy(
        update={"publication_id": publication.id, "configuration": configuration}
    )


def deploy(h, db, publication, documents, key, operations=("lookup", "submit"), artifact=None):
    h.last_request = bind(h, db, publication, documents, operations, artifact)
    return create_deployment(db, h.actor, h.last_request, key, dependencies=h.dependencies)


def enable(h, db, view, key):
    set_deployment_status(
        db, h.actor, view.id, view.revision_id, "enabled", key, dependencies=h.dependencies
    )


def test_effect_steps_admitted_under_v2_only(flows):
    h = flows
    documents = [doc("connector"), doc("reviewers")]
    with Session(h.engine) as db, db.begin():
        publication = publish(h, db, doc("reviewed_action"), documents, "reviewed")
        view = deploy(h, db, publication, documents, "reviewed")
        assert view.unreviewed_operations == ()
        enable(h, db, view, "reviewed-on")
    with Session(h.engine) as db, db.begin():
        reviewed = h.service.admit_execution(
            db, h.actor, AdmissionRequest(deployment_revision_id=view.revision_id, input=None), "r"
        )
    plain = h.start("scoped", [1, 2, 3], key="plain")  # positive control: a v1 plan
    with Session(h.engine) as db:
        reviewed_execution = db.get(Execution, reviewed.id)
        plain_execution = db.get(Execution, plain)
        assert reviewed_execution.interpreter_id == "lotuspond.execution-interpreter.v2"
        assert plain_execution.interpreter_id == "lotuspond.execution-interpreter.v1"
    # A worker registered only for v1 runs the v1 execution and never claims the v2 one.
    h.drive()
    with Session(h.engine) as db:
        assert db.get(Execution, plain).state == "succeeded"
        assert db.get(Execution, reviewed.id).state == "pending"
        assert db.get(Execution, reviewed.id).interpreter_id == INTERPRETER_V2 != INTERPRETER_V1


def test_write_connection_binding_is_exact(flows):
    h = flows
    documents = [doc("connector"), doc("reviewers")]
    with Session(h.engine) as db, db.begin():
        publication = publish(h, db, doc("reviewed_action"), documents, "binding")
        for index, operations in enumerate(
            (("lookup",), ("submit",), ("lookup", "submit", "delete"))
        ):
            with db.begin_nested(), pytest.raises(ExecutionError) as error:
                deploy(h, db, publication, documents, f"binding-{index}", operations)
            assert error.value.code == "dependency_unavailable", operations
        # A connection pinning another connector artifact.
        with db.begin_nested(), pytest.raises(ExecutionError, match="dependency_unavailable"):
            deploy(h, db, publication, documents, "binding-pin", artifact="f" * 64)
        # Positive control: the exact binding deploys.
        assert deploy(h, db, publication, documents, "binding-ok").revision_id


def test_reconciliation_input_is_assignable(flows):
    """A read-back narrower than its write is refused, though the package's own values fit."""
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    h = flows
    connector = doc("connector")
    operations = connector["definition"]["operations"]
    lookup = next(op for op in operations if op["id"] == "lookup")
    lookup["target_schema"]["fields"][0]["schema"] = {"kind": "string", "max_length": 8}
    package = doc("reviewed_action")
    package["connectors"][0]["ref"]["sha256"] = catalog_document_digest(connector)
    narrow = {"kind": "object", "fields": [dict(lookup["target_schema"]["fields"][0])]}
    for step in package["body"]["steps"]:
        if step["id"] in {"intent", "verify"}:
            step["target"]["schema"] = narrow  # "R-100" fits both operations
    documents = [connector, doc("reviewers")]
    with Session(h.engine) as db, db.begin():
        publication = publish(h, db, package, documents, "narrow")  # the SDK compiles it
        with pytest.raises(ExecutionError) as error:
            deploy(h, db, publication, documents, "narrow")
        assert error.value.code == "unsupported_operation"


def test_unreviewed_operations_listed_in_deployment(flows):
    h = flows
    documents = [doc("connector"), doc("unreviewed")]
    package = doc("reviewed_action")
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    package["policies"][0]["ref"]["sha256"] = catalog_document_digest(documents[1])
    package["policies"][0]["ref"]["name"] = documents[1]["name"]
    intent = next(s for s in package["body"]["steps"] if s["id"] == "intent")
    intent.update(approval="none", independent_review=False)
    package["body"]["steps"] = [s for s in package["body"]["steps"] if s["id"] != "review"]
    next(s for s in package["body"]["steps"] if s["id"] == "dispatch").pop("decision")
    with Session(h.engine) as db, db.begin():
        publication = publish(h, db, package, documents, "unreviewed")
        view = deploy(h, db, publication, documents, "unreviewed")
        assert view.unreviewed_operations == ("synthetic-target/submit",)
        # The same view on replay.
        replay = create_deployment(
            db, h.actor, h.last_request, "unreviewed", dependencies=h.dependencies
        )
        assert replay.unreviewed_operations == ("synthetic-target/submit",)


def test_policy_vocabulary_is_closed_at_publication(harness):
    h = harness
    bad = doc("reviewers")
    bad["definition"]["assignment"] = "reviewers"
    with Session(h.engine) as db, db.begin():
        h.publish(db, doc("reviewers"), request_key="good")  # positive control
        with pytest.raises(ExecutionError, match="unsupported_policy"):
            h.publish(db, bad, request_key="bad")
