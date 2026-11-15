"""Generate the executable-v2 versions of the Studio's five domain patterns (ADR-0013 step 3).

Each v1 pattern (`contracts/examples/<name>.v1.json`) was a shape: read systems, let an agent
work, have a person review, then write. Its v2 version keeps that shape as executable steps:
connector reads, agent steps on a declared model, and each write as a reviewed action (intent,
human review, dispatch, read-back verification). Data schemas stay `null`, as in the SDK's own
synthetic examples: these are patterns to start from, not configured integrations.

Every connector, model and policy here is a synthetic catalog document, pinned by the SDK's
`catalog_document_digest`. Nothing is a qualified integration, a credential or authority.

    cd backend && uv run python ../contracts/examples/executable-v2/patterns/generate.py [--check]

writes `<name>.json` (the package) and `<name>.catalog.json` (its catalog documents) beside
this file, after the SDK validates and compiles each package against its catalog.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lotuspond_sdk.executable import compile_package, validate_executable_package
from lotuspond_sdk.executable.catalog import catalog_document_digest

HERE = Path(__file__).resolve().parent
NULL = {"kind": "null"}
NOTHING = {"kind": "literal", "schema": NULL, "value": None}
RETRY = {"max_attempts": 1, "timeout_seconds": 30, "backoff_seconds": 0, "retry_codes": []}
AGENT_LIMITS = {
    "max_steps": 5,
    "timeout_seconds": 60,
    "max_input_tokens": 4000,
    "max_output_tokens": 1000,
    "max_cost_microusd": 100000,
}


def ref(source: str) -> dict:
    return {"kind": "ref", "source": {"kind": "step", "id": source}, "pointer": ""}


def operation(name: str, effect: str, read_back: str | None = None) -> dict:
    reconciliation = (
        {"mode": "none"}
        if effect == "read"
        else {
            "mode": "read_back",
            "read_operation": read_back,
            "absence_rules": [{"op": "present", "readback_pointer": ""}],
            "effect_rules": [{"op": "present", "readback_pointer": ""}],
        }
    )
    return {
        "id": name,
        "target_schema": NULL,
        "input_schema": NULL,
        "output_schema": NULL,
        "effect": effect,
        "idempotency": "none",
        "reconciliation": reconciliation,
    }


def connector(name: str, operations: list[dict]) -> dict:
    return {
        "format_version": 1,
        "kind": "connector",
        "name": f"synthetic-{name}",
        "version": "1.0.0",
        "schemas": [],
        "definition": {"artifact_sha256": "b" * 64, "operations": operations},
    }


MODEL = {
    "format_version": 1,
    "kind": "model",
    "name": "synthetic-reasoner",
    "version": "1.0.0",
    "schemas": [],
    "definition": {
        "profile": "synthetic",
        "configuration_revision": "1",
        "configuration_sha256": "b" * 64,
        "input_schema": NULL,
        "output_schema": NULL,
        "read_tools": [],
        "limits": {
            "max_steps": 10,
            "timeout_seconds": 120,
            "max_input_tokens": 8000,
            "max_output_tokens": 2000,
            "max_cost_microusd": 200000,
        },
    },
}


def policy(name: str, approval: str) -> dict:
    return {
        "format_version": 1,
        "kind": "policy",
        "name": f"synthetic-{name}",
        "version": "1.0.0",
        "schemas": [],
        "definition": {
            "approval": approval,
            "independent_review": approval == "human",
            "assignment": "workspace_editors",
            "escalation": None,
            "allowed_operation_classes": [],
            "allowed_capability_classes": [],
        },
    }


REVIEWERS = policy("reviewers", "human")
AUTOMATIC = policy("automatic", "none")


def pin(document: dict) -> dict:
    return {
        "kind": document["kind"],
        "name": document["name"],
        "version": document["version"],
        "sha256": catalog_document_digest(document),
    }


def read(step_id: str, connector_ref: str, op: str) -> dict:
    return {
        "kind": "connector_read",
        "id": step_id,
        "connector_ref": connector_ref,
        "operation": op,
        "target": NOTHING,
        "input": NOTHING,
        "retry": RETRY,
    }


def agent(step_id: str, instructions: str) -> dict:
    return {
        "kind": "agent",
        "id": step_id,
        "model_ref": "reasoner",
        "input": NOTHING,
        "instructions": instructions,
        "output_schema": NULL,
        "read_tools": [],
        "knowledge_refs": [],
        "limits": AGENT_LIMITS,
        "retry": RETRY,
    }


def reviewed_write(
    step_id: str, connector_ref: str, op: str, read_op: str, *, reviewed=True
) -> list[dict]:
    """A write as E05 requires it: an intent, its review (unless its policy needs none),
    the dispatch, and a read-back verification of the outcome."""
    intent = f"{step_id}-intent"
    steps = [
        {
            "kind": "action_intent",
            "id": intent,
            "connector_ref": connector_ref,
            "operation": op,
            "target": NOTHING,
            "input": NOTHING,
            "policy_ref": "reviewers" if reviewed else "automatic",
            "approval": "human" if reviewed else "none",
            "independent_review": reviewed,
            "expiry_seconds": 3600,
        }
    ]
    dispatch = {
        "kind": "action_dispatch",
        "id": f"{step_id}-send",
        "intent": ref(intent),
        "retry": RETRY,
    }
    if reviewed:
        steps.append(
            {
                "kind": "human_task",
                "id": f"{step_id}-review",
                "assignment_policy_ref": "reviewers",
                "timeout_seconds": 86400,
                "mode": "review",
                "intent": ref(intent),
            }
        )
        dispatch["decision"] = ref(f"{step_id}-review")
    steps.append(dispatch)
    steps.append(
        {
            "kind": "outcome_verification",
            "id": f"{step_id}-verify",
            "receipt": ref(f"{step_id}-send"),
            "connector_ref": connector_ref,
            "read_operation": read_op,
            "target": NOTHING,
            "input": NOTHING,
            "rule_id": f"{step_id}-applied",
            "version": "1",
            "rules": [{"op": "present", "readback_pointer": ""}],
        }
    )
    return steps


def package(package_id: str, name: str, steps, connectors: dict, policies: list[dict]) -> dict:
    return {
        "schema_version": "lotuspond.workflow-package.v2",
        "package_id": package_id,
        "version": "1.0.0",
        "name": name,
        "input_schema": NULL,
        "output_schema": NULL,
        "schemas": [],
        "body": {
            "steps": steps,
            "result": NOTHING,
            "result_schema": {"kind": "data", "schema": NULL},
        },
        "secret_refs": [],
        "environment": {
            "runtime_contract": "lotuspond.runtime-contract.v1",
            "platforms": [{"os": "linux", "architecture": "amd64"}],
            "required_capabilities": [],
            "destination_classes": [],
            "operation_classes": [],
        },
        "limits": {
            "max_steps": 200,
            "active_seconds": 3600,
            "elapsed_seconds": 604800,
            "retained_waits": 20,
            "max_input_tokens": 50000,
            "max_output_tokens": 10000,
            "max_cost_microusd": 2000000,
        },
        "assurance": {
            "evaluation_refs": [],
            "outcome_rule_refs": [],
            "required_classification": "synthetic",
        },
        "models": [{"id": "reasoner", "ref": pin(MODEL)}],
        "connectors": [{"id": key, "ref": pin(doc)} for key, doc in connectors.items()],
        "agents": [],
        "subworkflows": [],
        "evaluations": [],
        "policies": [
            {"id": "reviewers" if p is REVIEWERS else "automatic", "ref": pin(p)} for p in policies
        ],
        "knowledge": [],
        "artifacts": [],
    }


def patterns() -> dict[str, tuple[dict, list[dict]]]:
    case_system = connector(
        "case-system",
        [
            operation("read-case", "read"),
            operation("write-case", "write", "read-case"),
            operation("record-decision", "write", "read-case"),
        ],
    )
    documents = connector("document-source", [operation("read-document", "read")])
    records = connector(
        "records-system",
        [operation("read-record", "read"), operation("create-record", "write", "read-record")],
    )
    messaging = connector(
        "messaging",
        [operation("read-message", "read"), operation("send-message", "write", "read-message")],
    )
    desk = connector(
        "service-desk",
        [operation("read-request", "read"), operation("post-reply", "write", "read-request")],
    )
    knowledge = connector("knowledge-base", [operation("search-articles", "read")])
    ledger = connector("source-ledger", [operation("read-entries", "read")])
    target = connector(
        "target-system",
        [operation("read-records", "read"), operation("post-adjustment", "write", "read-records")],
    )
    built = {
        "reviewed-action": (
            package(
                "reviewed-action",
                "Reviewed action",
                [
                    read("read", "case-system", "read-case"),
                    agent("propose", "Propose a case update for a person to review."),
                    *reviewed_write("write", "case-system", "write-case", "read-case"),
                ],
                {"case-system": case_system},
                [REVIEWERS],
            ),
            [case_system],
        ),
        "document-intake": (
            package(
                "document-intake",
                "Document intake",
                [
                    read("read", "document-source", "read-document"),
                    agent(
                        "extract",
                        "Extract the fields the intake form declares. Cite the page of each.",
                    ),
                    agent(
                        "check",
                        "Compare the extracted fields with the declared validation rules and"
                        " list every field that fails one.",
                    ),
                    *reviewed_write("create", "records-system", "create-record", "read-record"),
                ],
                {"document-source": documents, "records-system": records},
                [REVIEWERS],
            ),
            [documents, records],
        ),
        "case-approval": (
            package(
                "case-approval",
                "Case approval",
                [
                    read("read", "case-system", "read-case"),
                    agent(
                        "recommend",
                        "Summarize the request against the stated approval criteria and"
                        " recommend a decision for a person to make.",
                    ),
                    *reviewed_write("record", "case-system", "record-decision", "read-case"),
                    # The notification follows the reviewed decision and needs no review of
                    # its own; its policy says so, and deployment lists it as unreviewed.
                    *reviewed_write(
                        "notify", "messaging", "send-message", "read-message", reviewed=False
                    ),
                ],
                {"case-system": case_system, "messaging": messaging},
                [REVIEWERS, AUTOMATIC],
            ),
            [case_system, messaging],
        ),
        "knowledge-answer": (
            package(
                "knowledge-answer",
                "Knowledge answer",
                [
                    read("read", "service-desk", "read-request"),
                    read("search", "knowledge-base", "search-articles"),
                    agent(
                        "draft",
                        "Draft a reply using only the retrieved articles, and cite each one"
                        " used. Say so when they do not answer the request.",
                    ),
                    *reviewed_write("reply", "service-desk", "post-reply", "read-request"),
                ],
                {"service-desk": desk, "knowledge-base": knowledge},
                [REVIEWERS],
            ),
            [desk, knowledge],
        ),
        "record-reconciliation": (
            package(
                "record-reconciliation",
                "Record reconciliation",
                [
                    read("read-source", "source-ledger", "read-entries"),
                    read("read-target", "target-system", "read-records"),
                    agent(
                        "reconcile",
                        "Match the entries with the records. List each unmatched item and"
                        " propose the adjustment that would resolve it.",
                    ),
                    *reviewed_write("adjust", "target-system", "post-adjustment", "read-records"),
                ],
                {"source-ledger": ledger, "target-system": target},
                [REVIEWERS],
            ),
            [ledger, target],
        ),
    }
    return built


def documents_of(name: str, connectors: list[dict]) -> list[dict]:
    policies = [REVIEWERS, AUTOMATIC] if name == "case-approval" else [REVIEWERS]
    return [MODEL, *connectors, *policies]


def render(value) -> str:
    return json.dumps(value, indent=2, sort_keys=False) + "\n"


def main(argv: list[str]) -> int:
    check = argv == ["--check"]
    stale = []
    for name, (pkg, connectors) in patterns().items():
        catalog = {"documents": documents_of(name, connectors)}
        validate_executable_package(pkg)
        compile_package(pkg, dependencies=[], catalog=catalog)
        for path, value in ((HERE / f"{name}.json", pkg), (HERE / f"{name}.catalog.json", catalog)):
            text = render(value)
            if check:
                if not path.exists() or path.read_text() != text:
                    stale.append(path.name)
            else:
                path.write_text(text)
    if stale:
        print("stale: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
