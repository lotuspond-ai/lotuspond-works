"""Generate E05's synthetic action fixtures (Task 3): `python generate.py` from this folder.

A synthetic target connector with real schemas, its policies and a reviewed-action
package built from `contracts/examples/executable-v2/reviewed_action.json`. The values
match Task 1's literal vector: connector `synthetic-target` 1.0.0, operation `submit`,
target `{"record_id": "R-100"}`, input `{"amount_minor": 75000, "currency": "EUR"}`.
Test-only: nothing here names a real system.
"""

import json
from copy import deepcopy
from pathlib import Path

from lotuspond_sdk.executable import compile_package
from lotuspond_sdk.executable.catalog import catalog_document_digest

HERE = Path(__file__).parent
EXAMPLE = Path(__file__).parents[5] / "contracts" / "examples" / "executable-v2"

STRING = {"kind": "string", "max_length": 128}
TARGET_SCHEMA = {
    "kind": "object",
    "fields": [{"name": "record_id", "schema": STRING, "required": True}],
}
INPUT_SCHEMA = {
    "kind": "object",
    "fields": [
        {
            "name": "amount_minor",
            "schema": {"kind": "integer", "min": 0, "max": 10**12},
            "required": True,
        },
        {"name": "currency", "schema": STRING, "required": True},
    ],
}
READBACK_SCHEMA = {
    "kind": "object",
    "fields": [{"name": "present", "schema": {"kind": "boolean"}, "required": True}],
}
TARGET = {"record_id": "R-100"}
INPUT = {"amount_minor": 75000, "currency": "EUR"}


def literal(schema, value):
    return {"kind": "literal", "schema": schema, "value": value}


def connector():
    present = [{"op": "present", "readback_pointer": "/present"}]
    return {
        "format_version": 1,
        "kind": "connector",
        "name": "synthetic-target",
        "version": "1.0.0",
        "schemas": [],
        "definition": {
            "artifact_sha256": "e" * 64,
            "operations": [
                {
                    "id": "lookup",
                    "target_schema": TARGET_SCHEMA,
                    "input_schema": INPUT_SCHEMA,
                    "output_schema": READBACK_SCHEMA,
                    "effect": "read",
                    "idempotency": "none",
                    "reconciliation": {"mode": "none"},
                },
                {
                    "id": "submit",
                    "target_schema": TARGET_SCHEMA,
                    "input_schema": INPUT_SCHEMA,
                    "output_schema": READBACK_SCHEMA,
                    "effect": "write",
                    "idempotency": "target_key",
                    "reconciliation": {
                        "mode": "read_back",
                        "read_operation": "lookup",
                        "absence_rules": present,
                        "effect_rules": present,
                    },
                },
            ],
        },
    }


def policy(name, *, assignment, escalation=None, approval="human", independent=True):
    return {
        "format_version": 1,
        "kind": "policy",
        "name": name,
        "version": "1.0.0",
        "schemas": [],
        "definition": {
            "approval": approval,
            "independent_review": independent,
            "assignment": assignment,
            "escalation": escalation,
            "allowed_operation_classes": [],
            "allowed_capability_classes": [],
        },
    }


def ref(document):
    return {
        "kind": document["kind"],
        "name": document["name"],
        "version": document["version"],
        "sha256": catalog_document_digest(document),
    }


def documents():
    return {
        "connector": connector(),
        "reviewers": policy("reviewers", assignment="workspace_admins"),
        "escalating": policy(
            "escalating-reviewers", assignment="workspace_admins", escalation="after_half_timeout"
        ),
        "owners": policy("owners", assignment="workspace_owners"),
        "unreviewed": policy(
            "unreviewed", assignment="workspace_admins", approval="none", independent=False
        ),
    }


def reviewed_action(docs):
    package = json.loads((EXAMPLE / "reviewed_action.json").read_text())
    package["package_id"] = "e05-reviewed-action"
    package["connectors"] = [{"id": "store", "ref": ref(docs["connector"])}]
    package["policies"] = [{"id": "reviewers", "ref": ref(docs["reviewers"])}]
    steps = {step["id"]: step for step in package["body"]["steps"]}
    for step in (steps["intent"], steps["verify"]):
        step["target"] = literal(TARGET_SCHEMA, TARGET)
        step["input"] = literal(INPUT_SCHEMA, INPUT)
    steps["intent"]["operation"] = "submit"
    steps["verify"]["read_operation"] = "lookup"
    steps["verify"]["rules"] = [{"op": "present", "readback_pointer": "/present"}]
    steps["intent"]["expiry_seconds"] = 3600
    steps["review"]["timeout_seconds"] = 600
    return package


def main():
    docs = documents()
    for name, document in docs.items():
        (HERE / f"{name}.json").write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
    package = reviewed_action(docs)
    catalog = {"documents": [docs["connector"], docs["reviewers"]]}
    compile_package(deepcopy(package), dependencies=[], catalog=catalog)  # must compile
    (HERE / "reviewed_action.json").write_text(json.dumps(package, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
