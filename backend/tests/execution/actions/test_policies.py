"""E05 Task 3: classification, interpreter selection and the closed policy rules (pure)."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from lotuspond_sdk.executable import validate_executable_package
from lotuspond_sdk.executable.catalog import catalog_document_digest, validate_catalog

from lotuspond.execution.action_contracts import INTERPRETER_V2
from lotuspond.execution.action_policies import (
    Closure,
    admit_closure,
    check_catalog_policy,
    classify_plan,
    unreviewed_operations,
)
from lotuspond.execution.engine_contracts import INTERPRETER_V1
from lotuspond.execution.errors import ExecutionError

FIXTURES = Path(__file__).parents[1] / "fixtures"
ACTIONS = FIXTURES / "actions"
CONTROL_FLOW = FIXTURES / "control-flow"
EXAMPLES = Path(__file__).parents[4] / "contracts" / "examples" / "executable-v2"


def doc(name):
    return json.loads((ACTIONS / f"{name}.json").read_text())


def ref(document):
    return {
        "kind": document["kind"],
        "name": document["name"],
        "version": document["version"],
        "sha256": catalog_document_digest(document),
    }


def closure(package, *documents):
    catalog = validate_catalog({"documents": list(documents)}).documents
    return Closure(
        packages=(validate_executable_package(package),),
        catalog={catalog_document_digest(raw): d for raw, d in zip(documents, catalog)},
    )


def reviewed(policy="reviewers", escalation=None):
    package = doc("reviewed_action")
    documents = [doc("connector"), doc(policy)]
    package["policies"] = [{"id": "reviewers", "ref": ref(documents[1])}]
    if escalation is not None:
        documents.append(doc(escalation))
        package["policies"].append({"id": "escalation", "ref": ref(documents[2])})
        review = next(s for s in package["body"]["steps"] if s["id"] == "review")
        review["escalation_policy_ref"] = "escalation"
    return package, documents


def refused(code, package, *documents):
    with pytest.raises(ExecutionError) as error:
        admit_closure(closure(package, *documents))
    assert error.value.code == code


def test_effect_and_review_plans_select_v2_and_others_v1():
    package, documents = reviewed()
    features = classify_plan(closure(package, *documents))
    assert features.effects and features.review and not features.e05_forms
    assert admit_closure(closure(package, *documents)) == INTERPRETER_V2
    # The same package without effect or review steps is a v1 plan.
    plain = deepcopy(package)
    plain["body"]["steps"] = []
    assert admit_closure(closure(plain, *documents)) == INTERPRETER_V1


def test_v1_control_flow_fixtures_classify_as_v1():
    """Every E04 golden-fixture package stays v1, so its progression is unchanged."""
    raws = [json.loads(p.read_text()) for p in sorted(CONTROL_FLOW.glob("*.json"))]
    raws += json.loads((EXAMPLES / "catalog.json").read_text())["documents"]
    documents = {
        catalog_document_digest(r): r for r in raws if "schema_version" not in r and "kind" in r
    }
    packages = [r for r in raws if r.get("schema_version") == "lotuspond.workflow-package.v2"]
    assert len(packages) >= 20
    by_digest = {}
    from hashlib import sha256

    from lotuspond_sdk.executable import canonical_executable_json

    for raw in packages:
        by_digest[sha256(canonical_executable_json(raw)).hexdigest()] = raw
    for raw in packages:
        sub = [
            validate_executable_package(by_digest[s["ref"]["sha256"]])
            for s in raw["subworkflows"]
            if s["ref"]["sha256"] in by_digest
        ]
        needed = [
            d["ref"]["sha256"]
            for f in ("connectors", "policies", "models", "agents")
            for d in raw[f]
        ]
        catalog = {
            sha: validate_catalog({"documents": [documents[sha]]}).documents[0]
            for sha in needed
            if sha in documents
        }
        assert (
            admit_closure(Closure((validate_executable_package(raw), *sub), catalog))
            == INTERPRETER_V1
        ), raw["package_id"]


def test_policy_vocabulary_is_closed():
    reviewers = validate_catalog({"documents": [doc("reviewers")]}).documents[0]
    check_catalog_policy(reviewers)  # positive control
    for change in (
        {"assignment": "reviewers"},
        {"assignment": "workspace_viewers"},
        {"escalation": "after_timeout"},
    ):
        bad = doc("reviewers")
        bad["definition"].update(change)
        with pytest.raises(ExecutionError, match="unsupported_policy"):
            check_catalog_policy(validate_catalog({"documents": [bad]}).documents[0])
        package, documents = reviewed()
        documents[1] = bad
        package["policies"][0]["ref"] = ref(bad)
        refused("unsupported_policy", package, *documents)
    # A review needs a human policy; an intent's approval must match its policy's.
    package, documents = reviewed()
    documents[1]["definition"]["approval"] = "none"
    package["policies"][0]["ref"] = ref(documents[1])
    refused("unsupported_policy", package, *documents)
    package, documents = reviewed()
    intent = next(s for s in package["body"]["steps"] if s["id"] == "intent")
    intent["independent_review"] = False
    refused("unsupported_policy", package, *documents)


def test_escalation_needs_both_halves():
    # Both halves: admitted under v2.
    package, documents = reviewed("escalating", escalation="owners")
    assert admit_closure(closure(package, *documents)) == INTERPRETER_V2
    # A reference without the review policy's after_half_timeout.
    package, documents = reviewed("reviewers", escalation="owners")
    refused("unsupported_policy", package, *documents)
    # The policy's after_half_timeout without a reference.
    package, documents = reviewed("escalating")
    refused("unsupported_policy", package, *documents)
    # An escalation group that would itself escalate.
    package, documents = reviewed("escalating", escalation="escalating")
    refused("unsupported_policy", package, *documents)


def test_compensation_is_refused():
    package, documents = reviewed()
    dispatch = next(s for s in package["body"]["steps"] if s["id"] == "dispatch")
    verify = next(s for s in package["body"]["steps"] if s["id"] == "verify")
    dispatch["compensation"] = {
        "connector_ref": "store",
        "operation": "submit",
        "target": verify["target"],
        "input": verify["input"],
        "policy_ref": "reviewers",
        "approval": "human",
        "independent_review": True,
        "expiry_seconds": 60,
        "retry": dispatch["retry"],
        "verification": {
            k: verify[k]
            for k in (
                "connector_ref",
                "read_operation",
                "target",
                "input",
                "rule_id",
                "version",
                "rules",
            )
        },
    }
    refused("unsupported_step", package, *documents)


def test_v1_forms_and_external_waits_keep_the_whole_e04_rule():
    """A plan that stays v1 keeps E04's form policy: admins, no escalation, human, independent."""
    raw = json.loads((CONTROL_FLOW / "waits_fast_timer.json").read_text())
    policy = json.loads((CONTROL_FLOW / "waits.policy.json").read_text())

    def admitted(change):
        bad = deepcopy(policy)
        bad["definition"].update(change)
        package = deepcopy(raw)
        package["policies"][0]["ref"]["sha256"] = catalog_document_digest(bad)
        return admit_closure(closure(package, bad))

    assert admitted({}) == INTERPRETER_V1  # positive control
    for change in ({"approval": "none"}, {"independent_review": False}):
        with pytest.raises(ExecutionError, match="unsupported_policy"):
            admitted(change)


def test_unreviewed_operations_are_listed():
    package, documents = reviewed("unreviewed")
    intent = next(s for s in package["body"]["steps"] if s["id"] == "intent")
    intent.update(approval="none", independent_review=False)
    package["body"]["steps"] = [s for s in package["body"]["steps"] if s["id"] != "review"]
    dispatch = next(s for s in package["body"]["steps"] if s["id"] == "dispatch")
    dispatch.pop("decision")
    built = closure(package, *documents)
    assert admit_closure(built) == INTERPRETER_V2  # decision 3: admitted as declared
    assert unreviewed_operations(built) == ("synthetic-target/submit",)
    reviewed_package, reviewed_documents = reviewed()
    assert unreviewed_operations(closure(reviewed_package, *reviewed_documents)) == ()


def test_a_write_must_be_reconcilable():
    package, documents = reviewed()
    intent = next(s for s in package["body"]["steps"] if s["id"] == "intent")
    intent["operation"] = "lookup"  # a read is not a write
    refused("unsupported_operation", package, *documents)
