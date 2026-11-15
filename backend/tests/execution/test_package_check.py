"""Studio v2: the publication path without trust or storage, and what publications are."""

import json
from copy import deepcopy

from lotuspond_sdk.executable.catalog import catalog_document_digest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.publication import check_v2
from lotuspond.models import ExecutablePublication

from .test_publication import example
from .test_publication import harness as harness  # noqa: F401


def check(h, package, dependency_ids=(), role_principal=None):
    with Session(h.engine) as db, db.begin():
        actor = authorize_execution(db, role_principal or h.principal, h.scope, "publish")
        return check_v2(
            db,
            actor,
            {"package": package, "dependency_ids": list(dependency_ids)},
            dependencies=h.dependencies,
        )


def with_model(package, model):
    package = deepcopy(package)
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
    return package


def publications(h):
    with Session(h.engine) as db:
        return db.scalar(select(func.count()).select_from(ExecutablePublication))


def test_a_valid_package_checks_with_its_digests_and_stores_nothing(harness):
    h = harness
    answer = check(h, example("minimal"))
    assert answer["valid"] is True and answer["problems"] == []
    assert len(answer["package_sha256"]) == 64 and len(answer["plan_sha256"]) == 64
    # The canonical bytes are what the author signs; their digest is the package's.
    from hashlib import sha256

    assert sha256(answer["canonical"].encode()).hexdigest() == answer["package_sha256"]
    assert publications(h) == 0  # a check records nothing


def test_problems_are_the_sdks_paths_and_codes_never_values(harness):
    h = harness
    package = example("minimal")
    package["version"] = "not-a-version-SECRET"
    answer = check(h, package)
    assert answer["valid"] is False and answer["package_sha256"] is None
    assert answer["problems"] and all(
        set(problem) == {"path", "code", "message"} for problem in answer["problems"]
    )
    assert "SECRET" not in json.dumps(answer)
    other = check(h, {"schema_version": "lotuspond.workflow-package.v1"})
    assert other["valid"] is False and other["problems"][0]["code"] == "invalid_package"


def test_the_closure_is_checked_against_the_named_publications(harness):
    h = harness
    model = example("catalog")["documents"][1]
    package = with_model(example("minimal"), model)
    # Valid on its own, but its pinned model is not supplied: a closure problem.
    missing = check(h, package)
    assert missing["valid"] is False and missing["package_sha256"] is not None
    assert missing["problems"]
    with Session(h.engine) as db, db.begin():
        published = h.publish(db, model, request_key="model")
    answer = check(h, package, [published.id])
    assert answer["valid"] is True, answer["problems"]
    # A catalog document the operator no longer trusts is reported, not raised.
    h.authority.snapshot["catalog"] = []
    revoked = check(h, package, [published.id])
    assert revoked["valid"] is False and revoked["problems"][0]["code"] == "trust_unavailable"


def test_publication_views_say_what_each_publication_is(harness):
    h = harness
    model = example("catalog")["documents"][1]
    with Session(h.engine) as db, db.begin():
        view = h.publish(db, model, request_key="model")
        workflow = h.publish(db, example("minimal"), request_key="workflow")
    assert (view.kind, view.name, view.version) == ("model", model["name"], model["version"])
    assert (workflow.kind, workflow.name) == ("workflow", example("minimal")["package_id"])
