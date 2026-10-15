"""Hand-authored complete structured plans; deliberately imports no SDK code.

Every graph entry, field, order, mapping selection and flow below is explicit.
Only repetitive record envelopes and ASCII canonical hashes use helpers. Source
fixtures are made fully explicit (including optional defaults) before hashing.
Run this file to write the authored expectations, NEVER to compile expectations.
"""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
ROOT = FIXTURES.parents[4]
EXAMPLES = ROOT / "contracts/examples/executable-v2"
NULL = {"kind": "null"}
INTEGER = {"kind": "integer", "min": None, "max": None}
NULL_MAPPING = {"kind": "literal", "schema": NULL, "value": None}
NULL_FLOW = {"kind": "data", "schema": NULL, "may_be_missing": False}
INTEGER_FLOW = {"kind": "data", "schema": INTEGER, "may_be_missing": False}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(value):
    return sha256(canonical(value)).hexdigest()


def fixture(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text())


def ref(name):
    return {"kind": "ref", "source": {"kind": "step", "id": name}, "pointer": ""}


def literal_integer(value):
    return {"kind": "literal", "schema": INTEGER, "value": value}


def step(package, authored, path, region_id, output, mappings, attempts, pins=(), operation=None):
    return {
        "package_sha256": package,
        "step_id": authored["id"],
        "region_id": region_id,
        "region_pointer": path.rsplit("/steps/", 1)[0],
        "source_pointer": path,
        "kind": authored["kind"],
        "source_sha256": digest(authored),
        "input_mapping_sha256": digest(mappings),
        "output_type_sha256": digest(output),
        "output_type": output,
        "pins": list(pins),
        "operation": operation,
        "maximum_step_attempts": attempts,
    }


def region(package, path, region_id, ids, mapping, output, attempts):
    return {
        "package_sha256": package,
        "region_id": region_id,
        "source_pointer": path,
        "kind": "sequence",
        "step_ids": ids,
        "result_mapping_sha256": digest(mapping),
        "output_type": output,
        "maximum_step_attempts": attempts,
    }


def edge(package, source, target, kind, target_package=None):
    return {
        "package_sha256": package,
        "source_pointer": source,
        "target_pointer": target,
        "kind": kind,
        "target_package_sha256": package if target_package is None else target_package,
    }


def requirement(root):
    return {"package_sha256": digest(root), "environment": root["environment"], "secret_refs": []}


def base(root, attempts):
    return {
        "schema_version": "lotuspond.compiled-workflow.v2",
        "compiler_revision": "lotuspond.workflow-compiler.v2.1",
        "package_sha256": digest(root),
        "dependency_digests": [],
        "catalog_digests": [],
        "steps": [],
        "regions": [],
        "edges": [],
        "requirements": [requirement(root)],
        "limits": root["limits"],
        "maximum_step_attempts": attempts,
        "outcome_obligations": [],
        "artifact_availability": "not_checked",
        "authority_granted": False,
    }


def decision():
    root = fixture("decision")
    choose = root["body"]["steps"][0]
    choose["then_body"]["result"] = literal_integer(2)
    choose["else_body"]["result"] = literal_integer(1)
    choose["then_body"]["result_schema"] = {"kind": "data", "schema": INTEGER}
    choose["else_body"]["result_schema"] = {"kind": "data", "schema": INTEGER}
    package = digest(root)
    plan = base(root, 3)
    plan["steps"] = [
        step(
            package,
            choose,
            "/body/steps/0",
            "body",
            INTEGER_FLOW,
            {"condition": choose["condition"]},
            2,
        )
    ]
    plan["regions"] = [
        region(package, "/body", "body", ["choose"], NULL_MAPPING, NULL_FLOW, 3),
        region(
            package,
            "/body/steps/0/then_body",
            "decision:choose:then",
            [],
            literal_integer(2),
            INTEGER_FLOW,
            1,
        ),
        region(
            package,
            "/body/steps/0/else_body",
            "decision:choose:else",
            [],
            literal_integer(1),
            INTEGER_FLOW,
            1,
        ),
    ]
    plan["edges"] = [
        edge(package, "/body", "/body/steps/0", "sequence"),
        edge(package, "/body/steps/0", "/body/steps/0/then_body", "then"),
        edge(package, "/body/steps/0", "/body/steps/0/else_body", "else"),
    ]
    return root, [], plan


def parallel():
    root = fixture("parallel")
    both = root["body"]["steps"][0]
    both["branches"][0]["body"]["result"] = literal_integer(2)
    both["branches"][1]["body"]["result"] = literal_integer(1)
    for branch in both["branches"]:
        branch["body"]["result_schema"] = {"kind": "data", "schema": INTEGER}
    output = {
        "kind": "object",
        "fields": [
            {"name": "left", "value": INTEGER_FLOW},
            {"name": "right", "value": INTEGER_FLOW},
        ],
        "may_be_missing": False,
    }
    package = digest(root)
    plan = base(root, 4)
    plan["steps"] = [step(package, both, "/body/steps/0", "body", output, {}, 3)]
    plan["regions"] = [
        region(package, "/body", "body", ["both"], NULL_MAPPING, NULL_FLOW, 4),
        region(
            package,
            "/body/steps/0/branches/0/body",
            "parallel:both:left",
            [],
            literal_integer(2),
            INTEGER_FLOW,
            1,
        ),
        region(
            package,
            "/body/steps/0/branches/1/body",
            "parallel:both:right",
            [],
            literal_integer(1),
            INTEGER_FLOW,
            1,
        ),
    ]
    plan["edges"] = [
        edge(package, "/body", "/body/steps/0", "sequence"),
        edge(package, "/body/steps/0", "/body/steps/0/branches/0/body", "branch"),
        edge(package, "/body/steps/0", "/body/steps/0/branches/1/body", "branch"),
    ]
    return root, [], plan


def iteration():
    root = fixture("iteration")
    each = root["body"]["steps"][0]
    each["input"] = {
        "kind": "array",
        "items": [literal_integer(2), literal_integer(1)],
        "item_schema": {"kind": "data", "schema": INTEGER},
    }
    each["max_items"] = 2
    each["body"]["result"] = {"kind": "ref", "source": {"kind": "iteration_item"}, "pointer": ""}
    each["body"]["result_schema"] = {"kind": "data", "schema": INTEGER}
    package = digest(root)
    owner = "l" + digest([package, "/body/steps/0"])[:63]
    output = {
        "kind": "array",
        "items": INTEGER_FLOW,
        "max_items": 2,
        "elements": None,
        "item_schema": {"kind": "data", "schema": INTEGER},
        "iteration_id": owner,
        "alternatives": None,
        "selection_path": [],
        "selection_bindings": [],
        "may_be_missing": False,
    }
    plan = base(root, 4)
    plan["steps"] = [
        step(package, each, "/body/steps/0", "body", output, {"input": each["input"]}, 3)
    ]
    plan["regions"] = [
        region(package, "/body", "body", ["each"], NULL_MAPPING, NULL_FLOW, 4),
        region(
            package,
            "/body/steps/0/body",
            "iteration:each:body",
            [],
            each["body"]["result"],
            INTEGER_FLOW,
            1,
        ),
    ]
    plan["edges"] = [
        edge(package, "/body", "/body/steps/0", "sequence"),
        edge(package, "/body/steps/0", "/body/steps/0/body", "iteration"),
    ]
    return root, [], plan


def subworkflow():
    root, child = fixture("subworkflow"), fixture("minimal")
    package, child_package = digest(root), digest(child)
    assert child_package == "724daea0bf5e9e1b724d74dc278b019a7edf1f1e3f98f76e223ca50e4fe71d57"
    invoke = root["body"]["steps"][0]
    pin = {
        "kind": "workflow",
        "name": "synthetic-minimal",
        "version": "1.0.0",
        "sha256": child_package,
    }
    plan = base(root, 3)
    plan["dependency_digests"] = [pin]
    plan["steps"] = [
        step(package, invoke, "/body/steps/0", "body", NULL_FLOW, {"input": NULL_MAPPING}, 2, [pin])
    ]
    plan["regions"] = [
        region(child_package, "/body", "body", [], NULL_MAPPING, NULL_FLOW, 1),
        region(package, "/body", "body", ["invoke"], NULL_MAPPING, NULL_FLOW, 3),
    ]
    plan["edges"] = [
        edge(package, "/body", "/body/steps/0", "sequence"),
        edge(package, "/body/steps/0", "/body", "call", child_package),
    ]
    plan["requirements"] = [requirement(child), requirement(root)]
    return root, [child], plan


def symbol(package, operation, step_id):
    return {
        "kind": "source",
        "operation": operation,
        "step_id": step_id,
        "occurrence_path": [package],
        "occurrence_bindings": [],
    }


def handle(kind, source, intent=None):
    return {
        "kind": "handle",
        "handle_kind": kind,
        "source": source,
        "intent_source": intent,
        "may_be_missing": False,
    }


def receipt_decision():
    root = fixture("reviewed_action")
    root["package_id"] = "synthetic-receipt-decision"
    root["connectors"][0]["credential_ref"] = None
    intent, review, dispatch, verify = root["body"]["steps"]
    # Explicit normalization defaults are authored here, without producer SDK APIs.
    review.update(escalation_policy_ref=None, form_schema=None)
    dispatch["compensation"] = None
    left = deepcopy([intent, review, dispatch])
    right = deepcopy([intent, review, dispatch])
    for prefix, steps in (("a", left), ("b", right)):
        steps[0]["id"], steps[1]["id"], steps[2]["id"] = (
            prefix + "intent",
            prefix + "review",
            prefix + "dispatch",
        )
        steps[1]["intent"] = ref(prefix + "intent")
        steps[2]["intent"], steps[2]["decision"] = ref(prefix + "intent"), ref(prefix + "review")
    choose = {
        "id": "choose",
        "kind": "decision",
        "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
        "then_body": {
            "steps": left,
            "result": ref("adispatch"),
            "result_schema": {"kind": "handle", "handle_kind": "action_receipt"},
        },
        "else_body": {
            "steps": right,
            "result": ref("bdispatch"),
            "result_schema": {"kind": "handle", "handle_kind": "action_receipt"},
        },
    }
    verify["receipt"] = ref("choose")
    root["body"]["steps"] = [choose, verify]
    package = digest(root)
    connector = {
        "kind": "connector",
        "name": "connector-fixture",
        "version": "1.0.0",
        "sha256": "478fde8e7f65a29046477b92f4110126d77aff896e45e5686213e47f720ce76d",
    }
    policy = {
        "kind": "policy",
        "name": "policy-fixture",
        "version": "1.0.0",
        "sha256": "d2fd55f2a66e85f5c47cef915e44e0c0cb1b5853a61f7e7abc8cceeb34198b4d",
    }
    a_intent, b_intent = (
        symbol(package, "action_intent", "aintent"),
        symbol(package, "action_intent", "bintent"),
    )
    a_dispatch, b_dispatch = (
        symbol(package, "action_dispatch", "adispatch"),
        symbol(package, "action_dispatch", "bdispatch"),
    )
    a_receipt, b_receipt = (
        handle("action_receipt", a_dispatch, a_intent),
        handle("action_receipt", b_dispatch, b_intent),
    )
    intent_choice = {
        "kind": "choice",
        "options": [a_intent, b_intent],
        "selection_path": [package, "/body/steps/0"],
        "selection_bindings": [],
    }
    receipt_choice = {
        "kind": "choice",
        "options": [a_dispatch, b_dispatch],
        "selection_path": [package, "/body/steps/0"],
        "selection_bindings": [],
    }
    verification = symbol(package, "verification", "verify")
    plan = base(root, 7)
    plan["catalog_digests"] = [connector, policy]
    plan["steps"] = [
        step(
            package,
            choose,
            "/body/steps/0",
            "body",
            handle("action_receipt", receipt_choice, intent_choice),
            {"condition": choose["condition"]},
            5,
        ),
        step(
            package,
            left[0],
            "/body/steps/0/then_body/steps/0",
            "decision:choose:then",
            handle("action_intent", a_intent),
            {"input": NULL_MAPPING, "target": NULL_MAPPING},
            1,
            [connector, policy],
            "write",
        ),
        step(
            package,
            left[1],
            "/body/steps/0/then_body/steps/1",
            "decision:choose:then",
            handle("human_decision", symbol(package, "human_review", "areview"), a_intent),
            {"intent": ref("aintent")},
            1,
            [policy],
        ),
        step(
            package,
            left[2],
            "/body/steps/0/then_body/steps/2",
            "decision:choose:then",
            a_receipt,
            {"intent": ref("aintent"), "decision": ref("areview")},
            1,
            [connector, policy],
            "write",
        ),
        step(
            package,
            right[0],
            "/body/steps/0/else_body/steps/0",
            "decision:choose:else",
            handle("action_intent", b_intent),
            {"input": NULL_MAPPING, "target": NULL_MAPPING},
            1,
            [connector, policy],
            "write",
        ),
        step(
            package,
            right[1],
            "/body/steps/0/else_body/steps/1",
            "decision:choose:else",
            handle("human_decision", symbol(package, "human_review", "breview"), b_intent),
            {"intent": ref("bintent")},
            1,
            [policy],
        ),
        step(
            package,
            right[2],
            "/body/steps/0/else_body/steps/2",
            "decision:choose:else",
            b_receipt,
            {"intent": ref("bintent"), "decision": ref("breview")},
            1,
            [connector, policy],
            "write",
        ),
        step(
            package,
            verify,
            "/body/steps/1",
            "body",
            handle("verification_evidence", verification, intent_choice),
            {"receipt": ref("choose"), "target": NULL_MAPPING, "input": NULL_MAPPING},
            1,
            [connector],
        ),
    ]
    plan["regions"] = [
        region(package, "/body", "body", ["choose", "verify"], NULL_MAPPING, NULL_FLOW, 7),
        region(
            package,
            "/body/steps/0/then_body",
            "decision:choose:then",
            ["aintent", "areview", "adispatch"],
            ref("adispatch"),
            a_receipt,
            4,
        ),
        region(
            package,
            "/body/steps/0/else_body",
            "decision:choose:else",
            ["bintent", "breview", "bdispatch"],
            ref("bdispatch"),
            b_receipt,
            4,
        ),
    ]
    plan["edges"] = [
        edge(package, "/body", "/body/steps/0", "sequence"),
        edge(package, "/body/steps/0", "/body/steps/0/then_body", "then"),
        edge(package, "/body/steps/0/then_body", "/body/steps/0/then_body/steps/0", "sequence"),
        edge(
            package,
            "/body/steps/0/then_body/steps/0",
            "/body/steps/0/then_body/steps/1",
            "sequence",
        ),
        edge(
            package,
            "/body/steps/0/then_body/steps/1",
            "/body/steps/0/then_body/steps/2",
            "sequence",
        ),
        edge(package, "/body/steps/0", "/body/steps/0/else_body", "else"),
        edge(package, "/body/steps/0/else_body", "/body/steps/0/else_body/steps/0", "sequence"),
        edge(
            package,
            "/body/steps/0/else_body/steps/0",
            "/body/steps/0/else_body/steps/1",
            "sequence",
        ),
        edge(
            package,
            "/body/steps/0/else_body/steps/1",
            "/body/steps/0/else_body/steps/2",
            "sequence",
        ),
        edge(package, "/body/steps/0", "/body/steps/1", "sequence"),
    ]
    plan["outcome_obligations"] = [
        {"package_sha256": package, "dispatch": a_dispatch, "verified_by": [verification]},
        {"package_sha256": package, "dispatch": b_dispatch, "verified_by": [verification]},
    ]
    return root, [], plan


def oracle(name):
    return {
        "decision": decision,
        "parallel": parallel,
        "iteration": iteration,
        "subworkflow": subworkflow,
        "receipt_decision": receipt_decision,
    }[name]()


if __name__ == "__main__":
    for name in ("decision", "parallel", "iteration", "subworkflow", "receipt_decision"):
        root, _, expected = oracle(name)
        content = canonical(expected)
        (FIXTURES / f"plan_{name}.jcs").write_bytes(content)
        print(f"{sha256(content).hexdigest()}  plan_{name}.jcs")
