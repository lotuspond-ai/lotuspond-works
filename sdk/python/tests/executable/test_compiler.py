"""Typed compiler contracts; golden expectations are authored, never compiled."""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

import lotuspond_sdk.executable as api
from lotuspond_sdk.executable import ExecutableError


def compile_case(raw, dependencies=None, catalog=None):
    assert hasattr(api, "compile_package"), "public compiler is missing"
    if catalog is not None:
        pins = {
            d["ref"]["sha256"]
            for family in (
                "connectors",
                "policies",
                "models",
                "agents",
                "knowledge",
                "evaluations",
                "artifacts",
            )
            for d in raw[family]
        }
        catalog = {
            "documents": [d for d in catalog["documents"] if api.catalog_document_digest(d) in pins]
        }
    return api.compile_package(
        raw,
        dependencies=[] if dependencies is None else dependencies,
        catalog={"documents": []} if catalog is None else catalog,
    )


def test_literal_empty_plan(case):
    root = case("minimal")
    plan = compile_case(root)
    # Identity oracle is the reviewed source fixture, not the compiler.
    digest = "724daea0bf5e9e1b724d74dc278b019a7edf1f1e3f98f76e223ca50e4fe71d57"
    expected = {
        "schema_version": "lotuspond.compiled-workflow.v2",
        "compiler_revision": "lotuspond.workflow-compiler.v2.1",
        "package_sha256": digest,
        "dependency_digests": [],
        "catalog_digests": [],
        "steps": [],
        "regions": [
            {
                "package_sha256": digest,
                "source_pointer": "/body",
                "region_id": "body",
                "kind": "sequence",
                "step_ids": [],
                "result_mapping_sha256": sha256(
                    b'{"kind":"literal","schema":{"kind":"null"},"value":null}'
                ).hexdigest(),
                "output_type": {
                    "kind": "data",
                    "schema": {"kind": "null"},
                    "may_be_missing": False,
                },
                "maximum_step_attempts": 1,
            }
        ],
        "edges": [],
        "requirements": [
            {"package_sha256": digest, "environment": root["environment"], "secret_refs": []}
        ],
        "limits": root["limits"],
        "maximum_step_attempts": 1,
        "outcome_obligations": [],
        "artifact_availability": "not_checked",
        "authority_granted": False,
    }
    # Standard-library sorted ASCII JSON is an independent JCS oracle for this fixture.
    expected_bytes = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    assert (
        expected_bytes
        == (Path(__file__).parents[1] / "fixtures/executable-v2/plan_minimal.jcs").read_bytes()
    )
    assert api.canonical_plan_json(plan) == expected_bytes
    assert api.compiled_plan_digest(plan) == sha256(expected_bytes).hexdigest()


@pytest.mark.parametrize(
    "name,attempts",
    [
        ("decision", 3),
        ("parallel", 4),
        ("iteration", 3),
        ("reviewed_action", 5),
        ("subworkflow", 3),
    ],
)
def test_structured_counts(case, catalog_case, name, attempts):
    plan = compile_case(
        case(name), [case("minimal")] if name == "subworkflow" else [], catalog_case()
    )
    assert plan.maximum_step_attempts == attempts
    assert plan.authority_granted is False


def test_sources_unchanged_and_catalog_order(case, catalog_case):
    root, catalog = case("reviewed_action"), catalog_case()
    before = deepcopy((root, catalog))
    first = compile_case(root, catalog=catalog)
    assert (root, catalog) == before
    catalog["documents"].reverse()
    assert api.canonical_plan_json(first) == api.canonical_plan_json(
        compile_case(root, catalog=catalog)
    )


def test_verification_missing(case, catalog_case):
    root = case("reviewed_action")
    root["body"]["steps"].pop()
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog_case())
    assert error.value.code == "verification_missing"


def test_intent_mismatch(case, catalog_case):
    root = case("reviewed_action")
    other = deepcopy(root["body"]["steps"][0])
    other["id"] = "other"
    root["body"]["steps"].insert(0, other)
    root["body"]["steps"][3]["intent"]["source"]["id"] = "other"
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog_case())
    assert error.value.code == "intent_mismatch"


def test_branch_private_output_unavailable(case):
    root = case("unjoined_branch_reference")
    with pytest.raises(ExecutableError) as error:
        compile_case(root)
    assert error.value.code == "unavailable_value"


def test_budget_counts_region_overhead(case):
    root = case("iteration")
    root["limits"]["max_steps"] = 2
    with pytest.raises(ExecutableError) as error:
        compile_case(root)
    assert error.value.code == "expansion_exceeded"


def test_plan_mutated_flag_is_revalidated(case):
    plan = compile_case(case("minimal"))
    plan.authority_granted = 0
    with pytest.raises(ExecutableError):
        api.canonical_plan_json(plan)


def ref(step, pointer=""):
    return {"kind": "ref", "source": {"kind": "step", "id": step}, "pointer": pointer}


def null_body():
    return {
        "steps": [],
        "result": {"kind": "literal", "schema": {"kind": "null"}, "value": None},
        "result_schema": {"kind": "data", "schema": {"kind": "null"}},
    }


def receipt_branch(case, prefix):
    value = case("reviewed_action")
    steps = value["body"]["steps"][:3]
    for step in steps:
        step["id"] = prefix + step["id"]
        for field in ("intent", "decision"):
            if field in step and step[field]:
                step[field]["source"]["id"] = prefix + step[field]["source"]["id"]
    return {
        "steps": steps,
        "result": ref(prefix + "dispatch"),
        "result_schema": {"kind": "handle", "handle_kind": "action_receipt"},
    }


def test_receipts_from_both_decision_branches_verified_after_join(case, catalog_case):
    root = case("reviewed_action")
    verify = root["body"]["steps"][-1]
    verify["receipt"] = ref("choose")
    root["body"]["steps"] = [
        {
            "id": "choose",
            "kind": "decision",
            "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
            "then_body": receipt_branch(case, "a"),
            "else_body": receipt_branch(case, "b"),
        },
        verify,
    ]
    plan = compile_case(root, catalog=catalog_case())
    assert len(plan.outcome_obligations) == 2
    assert plan.maximum_step_attempts == 7


def test_branch_private_dispatch_does_not_disappear(case, catalog_case):
    root = case("reviewed_action")
    branch = receipt_branch(case, "a")
    branch.update({k: v for k, v in null_body().items() if k != "steps"})
    root["body"]["steps"] = [
        {
            "id": "choose",
            "kind": "decision",
            "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
            "then_body": branch,
            "else_body": null_body(),
        }
    ]
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog_case())
    assert error.value.code == "verification_missing"


def test_only_one_branch_verifying_prior_dispatch_refuses(case, catalog_case):
    root = case("reviewed_action")
    verify = root["body"]["steps"].pop()
    branch = null_body()
    branch["steps"] = [verify]
    root["body"]["steps"].append(
        {
            "id": "choose",
            "kind": "decision",
            "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
            "then_body": branch,
            "else_body": null_body(),
        }
    )
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog_case())
    assert error.value.code == "verification_missing"


def test_array_loop_receipt_exports_verified_in_later_loop(case, catalog_case):
    root = case("reviewed_action")
    producer = case("iteration")["body"]["steps"][0]
    producer["id"] = "produce"
    producer["body"] = receipt_branch(case, "a")
    verify = root["body"]["steps"][-1]
    verify["receipt"] = {"kind": "ref", "source": {"kind": "iteration_item"}, "pointer": ""}
    consumer = {
        "id": "consume",
        "kind": "iteration",
        "input": ref("produce"),
        "max_items": 1,
        "concurrency": 1,
        "body": null_body(),
    }
    consumer["body"]["steps"] = [verify]
    root["body"]["steps"] = [producer, consumer]
    plan = compile_case(root, catalog=catalog_case())
    assert len(plan.outcome_obligations) == 1
    assert plan.maximum_step_attempts == 9


def test_optional_value_guard_scope(case):
    root = case("decision")
    root["input_schema"] = {
        "kind": "object",
        "fields": [{"name": "x", "required": False, "schema": {"kind": "integer"}}],
    }
    optional = {"kind": "ref", "source": {"kind": "package_input"}, "pointer": "/x"}
    step = root["body"]["steps"][0]
    step["condition"] = {"kind": "call", "fn": "exists", "args": [optional]}
    step["then_body"]["steps"] = [
        {
            "id": "guarded",
            "kind": "transform",
            "mapping": optional,
            "output_schema": {"kind": "integer"},
        }
    ]
    compile_case(root)
    step["else_body"]["steps"] = step["then_body"]["steps"]
    step["then_body"]["steps"] = []
    with pytest.raises(ExecutableError) as error:
        compile_case(root)
    assert error.value.code == "unavailable_value"


@pytest.mark.parametrize("attempts,valid", [(10000, True), (10001, False)])
def test_expansion_exact_boundary(case, catalog_case, attempts, valid):
    root = case(f"attempt_budget_{attempts}")
    if valid:
        assert compile_case(root, catalog=catalog_case()).maximum_step_attempts == 10000
    else:
        with pytest.raises(ExecutableError) as error:
            compile_case(root, catalog=catalog_case())
        assert error.value.code == "expansion_exceeded"


def test_zero_length_data_iteration(case):
    root = case("iteration")
    loop = root["body"]["steps"][0]
    loop["input"]["schema"]["max_items"] = 0
    loop["input"]["value"] = []
    assert compile_case(root).maximum_step_attempts == 3


def test_source_symbols_include_package_identity(case, catalog_case):
    root = case("reviewed_action")
    plan = compile_case(root, catalog=catalog_case())
    assert plan.steps[0].output_type.source.occurrence_path == [plan.package_sha256]


def agent_case(case, catalog_case):
    root = case("minimal")
    model = catalog_case()["documents"][1]
    root["models"] = [
        {
            "id": "model",
            "ref": {
                "kind": "model",
                "name": model["name"],
                "version": model["version"],
                "sha256": api.catalog_document_digest(model),
            },
        }
    ]
    step = {
        "kind": "agent",
        "id": "agent",
        "model_ref": "model",
        "instructions": "PRIVATE INSTRUCTION",
        "input": null_body()["result"],
        "output_schema": {"kind": "null"},
        "limits": deepcopy(model["definition"]["limits"]),
        "retry": {
            "max_attempts": 2,
            "timeout_seconds": 30,
            "backoff_seconds": 0,
            "retry_codes": [],
        },
    }
    root["body"]["steps"] = [step]
    return root, {"documents": [model]}


def test_agent_requires_explicit_typed_input(case, catalog_case):
    root, catalog = agent_case(case, catalog_case)
    plan = api.compile_package(root, dependencies=[], catalog=catalog)
    assert plan.maximum_step_attempts == 21
    assert b"PRIVATE INSTRUCTION" not in api.canonical_plan_json(plan)
    root["body"]["steps"][0].pop("input")
    with pytest.raises(ExecutableError):
        api.compile_package(root, dependencies=[], catalog=catalog)


def test_agent_input_mismatch(case, catalog_case):
    root, catalog = agent_case(case, catalog_case)
    root["body"]["steps"][0]["input"] = {
        "kind": "literal",
        "schema": {"kind": "integer"},
        "value": 1,
    }
    with pytest.raises(ExecutableError) as error:
        api.compile_package(root, dependencies=[], catalog=catalog)
    assert error.value.code == "type_mismatch"


def test_parent_step_precedes_children_in_authored_plan_order(case):
    root = case("decision")
    root["body"]["steps"][0]["then_body"]["steps"] = [
        {"id": "child", "kind": "timer", "delay_seconds": 1}
    ]
    assert [x.step_id for x in compile_case(root).steps] == ["choose", "child"]


def test_reconciliation_rules_checked_against_read_schema(case, catalog_case):
    root, catalog = case("reviewed_action"), catalog_case()
    connector = catalog["documents"][0]
    connector["definition"]["operations"][1]["reconciliation"]["effect_rules"] = [
        {"op": "present", "readback_pointer": "/does-not-exist"}
    ]
    root["connectors"][0]["ref"]["sha256"] = api.catalog_document_digest(connector)
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog)
    assert error.value.code in ("unknown_reference", "type_mismatch")


def test_compensation_policy_cannot_be_weakened(case, catalog_case):
    root = case("reviewed_action")
    intent, _, dispatch, verify = root["body"]["steps"]
    compensation = {k: deepcopy(v) for k, v in intent.items() if k not in ("id", "kind")}
    compensation["approval"] = "none"
    compensation["retry"] = dispatch["retry"]
    compensation["verification"] = {
        k: deepcopy(v) for k, v in verify.items() if k not in ("id", "kind", "receipt")
    }
    dispatch["compensation"] = compensation
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog_case())
    assert error.value.code == "intent_mismatch"


def test_compensation_has_own_bounded_obligation(case, catalog_case):
    root = case("reviewed_action")
    intent, _, dispatch, verify = root["body"]["steps"]
    compensation = {k: deepcopy(v) for k, v in intent.items() if k not in ("id", "kind")}
    compensation["retry"] = {**dispatch["retry"], "max_attempts": 3}
    compensation["verification"] = {
        k: deepcopy(v) for k, v in verify.items() if k not in ("id", "kind", "receipt")
    }
    dispatch["compensation"] = compensation
    plan = compile_case(root, catalog=catalog_case())
    assert plan.maximum_step_attempts == 11
    assert len(plan.outcome_obligations) == 2


def test_plan_output_digest_revalidated(case):
    root = case("decision")
    plan = compile_case(root)
    plan.steps[0].output_type_sha256 = "0" * 64
    with pytest.raises(ExecutableError):
        api.canonical_plan_json(plan)


def test_empty_iteration_cannot_verify_prior_receipt(case, catalog_case):
    root = case("reviewed_action")
    verify = root["body"]["steps"].pop()
    loop = case("iteration")["body"]["steps"][0]
    loop["input"]["value"] = []
    loop["body"]["steps"] = [verify]
    root["body"]["steps"].append(loop)
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog_case())
    assert error.value.code == "verification_missing"


def target_case(case, catalog_case):
    root, catalog = case("reviewed_action"), catalog_case()
    schema = {"kind": "string", "max_length": 20}
    connector = catalog["documents"][0]
    for operation in connector["definition"]["operations"]:
        operation["target_schema"] = schema
    root["connectors"][0]["ref"]["sha256"] = api.catalog_document_digest(connector)
    for step in (root["body"]["steps"][0], root["body"]["steps"][-1]):
        step["target"] = {"kind": "literal", "schema": schema, "value": "original"}
    return root, catalog


def test_verification_changed_target_refuses(case, catalog_case):
    root, catalog = target_case(case, catalog_case)
    root["body"]["steps"][-1]["target"]["value"] = "changed"
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog)
    assert error.value.code == "intent_mismatch"


def test_verification_target_alias_through_transform(case, catalog_case):
    root, catalog = target_case(case, catalog_case)
    original = root["body"]["steps"][0]["target"]
    root["body"]["steps"].insert(
        0,
        {
            "id": "alias",
            "kind": "transform",
            "mapping": original,
            "output_schema": original["schema"],
        },
    )
    root["body"]["steps"][-1]["target"] = ref("alias")
    assert compile_case(root, catalog=catalog).maximum_step_attempts == 6


def test_verification_different_connector_pin_refuses(case, catalog_case):
    root, catalog = target_case(case, catalog_case)
    other = deepcopy(catalog["documents"][0])
    other["name"] = "other-connector"
    catalog["documents"].append(other)
    root["connectors"].append(
        {
            "id": "other",
            "ref": {
                "kind": "connector",
                "name": other["name"],
                "version": other["version"],
                "sha256": api.catalog_document_digest(other),
            },
        }
    )
    root["body"]["steps"][-1]["connector_ref"] = "other"
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog)
    assert error.value.code == "intent_mismatch"


def test_decision_receipt_and_target_exports_keep_correlation(case, catalog_case):
    root, catalog = target_case(case, catalog_case)
    branches = []
    for prefix, target in [("a", "first"), ("b", "second")]:
        branch = receipt_branch(case, prefix)
        target_map = {
            "kind": "literal",
            "schema": {"kind": "string", "max_length": 20},
            "value": target,
        }
        branch["steps"][0]["target"] = target_map
        branch["result"] = {
            "kind": "object",
            "fields": [
                {"name": "receipt", "value": ref(prefix + "dispatch")},
                {"name": "target", "value": target_map},
            ],
        }
        branch["result_schema"] = {
            "kind": "object",
            "fields": [
                {
                    "name": "receipt",
                    "required": True,
                    "schema": {"kind": "handle", "handle_kind": "action_receipt"},
                },
                {
                    "name": "target",
                    "required": True,
                    "schema": {"kind": "data", "schema": target_map["schema"]},
                },
            ],
        }
        branches.append(branch)
    verify = root["body"]["steps"][-1]
    verify["receipt"] = ref("choose", "/receipt")
    verify["target"] = ref("choose", "/target")
    root["body"]["steps"] = [
        {
            "id": "choose",
            "kind": "decision",
            "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
            "then_body": branches[0],
            "else_body": branches[1],
        },
        verify,
    ]
    assert len(compile_case(root, catalog=catalog).outcome_obligations) == 2
    branches[0]["result"]["fields"][1]["value"] = branches[1]["result"]["fields"][1]["value"]
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog)
    assert error.value.code == "intent_mismatch"


def test_iteration_target_and_receipt_aliases_keep_owner_bindings(case, catalog_case):
    root, catalog = target_case(case, catalog_case)
    producer = case("iteration")["body"]["steps"][0]
    producer["input"] = {
        "kind": "literal",
        "schema": {"kind": "array", "items": {"kind": "string", "max_length": 20}, "max_items": 1},
        "value": ["target"],
    }
    producer["body"] = receipt_branch(case, "a")
    target = {"kind": "ref", "source": {"kind": "iteration_item"}, "pointer": ""}
    producer["body"]["steps"][0]["target"] = target
    producer["body"]["result"] = {
        "kind": "object",
        "fields": [
            {"name": "receipt", "value": ref("adispatch")},
            {"name": "target", "value": target},
        ],
    }
    producer["body"]["result_schema"] = {
        "kind": "object",
        "fields": [
            {
                "name": "receipt",
                "required": True,
                "schema": {"kind": "handle", "handle_kind": "action_receipt"},
            },
            {
                "name": "target",
                "required": True,
                "schema": {"kind": "data", "schema": {"kind": "string", "max_length": 20}},
            },
        ],
    }
    verify = root["body"]["steps"][-1]
    verify["receipt"] = {"kind": "ref", "source": {"kind": "iteration_item"}, "pointer": "/receipt"}
    verify["target"] = {"kind": "ref", "source": {"kind": "iteration_item"}, "pointer": "/target"}
    consumer = {
        "id": "consume",
        "kind": "iteration",
        "input": ref("each"),
        "max_items": 1,
        "concurrency": 1,
        "body": null_body(),
    }
    consumer["body"]["steps"] = [verify]
    root["body"]["steps"] = [producer, consumer]
    plan = compile_case(root, catalog=catalog)
    evidence = next(x for x in plan.steps if x.step_id == "verify").output_type
    assert evidence.intent_source.occurrence_bindings[0].owner.startswith("l")
    assert evidence.intent_source.occurrence_bindings[0].path[0].startswith("$item:l")


def test_full_flow_projection_retains_ordered_alternatives_and_bindings():
    from lotuspond_sdk.executable.plan import flow_projection
    from lotuspond_sdk.executable.types import FlowArrayType, HandleType, SourceSymbol, parse_export

    origin = SourceSymbol("action_intent", "intent", ("0",), (("owner", ("0",)),))
    handle = HandleType("action_intent", origin)
    expected_handle = {
        "kind": "handle",
        "handle_kind": "action_intent",
        "source": {
            "kind": "source",
            "operation": "action_intent",
            "step_id": "intent",
            "occurrence_path": ["0"],
            "occurrence_bindings": [{"owner": "owner", "path": ["0"]}],
        },
        "intent_source": None,
        "may_be_missing": False,
    }
    assert flow_projection(handle) == expected_handle
    schema = parse_export({"kind": "handle", "handle_kind": "action_intent"})
    empty = FlowArrayType(None, 0, elements=(), item_schema=schema)
    full = FlowArrayType(handle, 1, elements=(handle,), item_schema=schema)
    # The summary is reconstructed by validation; alternatives retain empty positions.
    array = FlowArrayType(
        handle,
        1,
        item_schema=schema,
        alternatives=(empty, full),
        selection_path=("0",),
        selection_bindings=(("selector", ("0",)),),
    )
    projected = flow_projection(array)
    assert projected["selection_bindings"] == [{"owner": "selector", "path": ["0"]}]
    assert projected["alternatives"][0]["elements"] == []
    assert projected["alternatives"][0]["max_items"] == 0
    assert projected["alternatives"][1]["elements"] == [expected_handle]


def test_catalog_rules_use_write_input_and_readback_schema(case, catalog_case):
    root, catalog = case("reviewed_action"), catalog_case()
    connector = catalog["documents"][0]
    read, write = connector["definition"]["operations"]
    schema = {
        "kind": "object",
        "fields": [{"name": "x", "required": True, "schema": {"kind": "integer"}}],
    }
    write["input_schema"] = schema
    read["output_schema"] = schema
    expected = {"kind": "ref", "source": {"kind": "package_input"}, "pointer": "/x"}
    write["reconciliation"]["effect_rules"] = [
        {"op": "eq", "readback_pointer": "/x", "expected": expected}
    ]
    root["body"]["steps"][0]["input"] = {"kind": "literal", "schema": schema, "value": {"x": 1}}
    root["connectors"][0]["ref"]["sha256"] = api.catalog_document_digest(connector)
    compile_case(root, catalog=catalog)
    write["reconciliation"]["effect_rules"][0]["expected"]["pointer"] = "/absent"
    root["connectors"][0]["ref"]["sha256"] = api.catalog_document_digest(connector)
    with pytest.raises(ExecutableError):
        compile_case(root, catalog=catalog)


def test_literal_timer_plan(case):
    root = case("minimal")
    root["body"]["steps"] = [{"id": "wait", "kind": "timer", "delay_seconds": 7}]

    def canonical(x):
        return json.dumps(x, sort_keys=True, separators=(",", ":")).encode()

    digest = sha256(canonical(root)).hexdigest()
    output = {"kind": "data", "schema": {"kind": "null"}, "may_be_missing": False}
    expected = {
        "schema_version": "lotuspond.compiled-workflow.v2",
        "compiler_revision": "lotuspond.workflow-compiler.v2.1",
        "package_sha256": digest,
        "dependency_digests": [],
        "catalog_digests": [],
        "steps": [
            {
                "package_sha256": digest,
                "step_id": "wait",
                "region_pointer": "/body",
                "region_id": "body",
                "source_pointer": "/body/steps/0",
                "kind": "timer",
                "source_sha256": sha256(
                    b'{"delay_seconds":7,"id":"wait","kind":"timer"}'
                ).hexdigest(),
                "input_mapping_sha256": sha256(b"{}").hexdigest(),
                "output_type_sha256": sha256(canonical(output)).hexdigest(),
                "output_type": output,
                "pins": [],
                "operation": None,
                "maximum_step_attempts": 1,
            }
        ],
        "regions": [
            {
                "package_sha256": digest,
                "source_pointer": "/body",
                "region_id": "body",
                "kind": "sequence",
                "step_ids": ["wait"],
                "result_mapping_sha256": sha256(
                    b'{"kind":"literal","schema":{"kind":"null"},"value":null}'
                ).hexdigest(),
                "output_type": output,
                "maximum_step_attempts": 2,
            }
        ],
        "edges": [
            {
                "package_sha256": digest,
                "source_pointer": "/body",
                "target_pointer": "/body/steps/0",
                "kind": "sequence",
                "target_package_sha256": digest,
            }
        ],
        "requirements": [
            {"package_sha256": digest, "environment": root["environment"], "secret_refs": []}
        ],
        "limits": root["limits"],
        "maximum_step_attempts": 2,
        "outcome_obligations": [],
        "artifact_availability": "not_checked",
        "authority_granted": False,
    }
    plan = compile_case(root)
    assert (
        canonical(expected)
        == (Path(__file__).parents[1] / "fixtures/executable-v2/plan_timer.jcs").read_bytes()
    )
    assert api.canonical_plan_json(plan) == canonical(expected)
    assert api.compiled_plan_digest(plan) == sha256(canonical(expected)).hexdigest()


def test_shared_symbolic_expression_dag_is_bounded(case, catalog_case):
    root, catalog = target_case(case, catalog_case)
    schema = {"kind": "integer"}
    connector = catalog["documents"][0]
    for operation in connector["definition"]["operations"]:
        operation["target_schema"] = schema
    root["connectors"][0]["ref"]["sha256"] = api.catalog_document_digest(connector)
    mappings = [
        {
            "id": "value-0",
            "kind": "transform",
            "output_schema": schema,
            "mapping": {"kind": "literal", "schema": schema, "value": 1},
        }
    ]
    for index in range(1, 30):
        mappings.append(
            {
                "id": f"value-{index}",
                "kind": "transform",
                "output_schema": schema,
                "mapping": {
                    "kind": "call",
                    "fn": "add",
                    "args": [ref(f"value-{index - 1}"), ref(f"value-{index - 1}")],
                },
            }
        )
    root["body"]["steps"][0]["target"] = ref("value-29")
    root["body"]["steps"][-1]["target"] = ref("value-29")
    root["body"]["steps"] = mappings + root["body"]["steps"]
    assert compile_case(root, catalog=catalog).maximum_step_attempts == 35


def test_readback_presence_refines_only_conjunction(case, catalog_case):
    root, catalog = case("reviewed_action"), catalog_case()
    connector = catalog["documents"][0]
    connector["definition"]["operations"][0]["output_schema"] = {
        "kind": "object",
        "fields": [{"name": "x", "required": False, "schema": {"kind": "integer"}}],
    }
    root["connectors"][0]["ref"]["sha256"] = api.catalog_document_digest(connector)
    present = {"op": "present", "readback_pointer": "/x"}
    compare = {
        "op": "eq",
        "readback_pointer": "/x",
        "expected": {"kind": "literal", "schema": {"kind": "integer"}, "value": 1},
    }
    root["body"]["steps"][-1]["rules"] = [{"op": "and", "rules": [present, compare]}]
    compile_case(root, catalog=catalog)
    root["body"]["steps"][-1]["rules"][0]["op"] = "or"
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog)
    assert error.value.code == "unavailable_value"


def test_agent_limits_respect_package_envelope(case, catalog_case):
    root, catalog = agent_case(case, catalog_case)
    root["limits"]["max_input_tokens"] = 10
    with pytest.raises(ExecutableError) as error:
        api.compile_package(root, dependencies=[], catalog=catalog)
    assert error.value.code == "expansion_exceeded"


def test_plan_oversize_rejected_before_serialization(case):
    plan = compile_case(case("minimal"))
    raw = json.loads(api.canonical_plan_json(plan))
    raw["regions"][0]["source_pointer"] = "x" * 8388609
    with pytest.raises(ExecutableError):
        api.canonical_plan_json(raw)


def test_child_environment_and_limits_cannot_expand(case):
    def pin(child):
        return {
            "id": "child",
            "ref": {
                "kind": "workflow",
                "name": child["package_id"],
                "version": child["version"],
                "sha256": api.executable_digest(child),
            },
        }

    child, root = case("minimal"), case("subworkflow")
    child["environment"]["required_capabilities"] = ["new-capability"]
    root["subworkflows"] = [pin(child)]
    with pytest.raises(ExecutableError) as error:
        compile_case(root, [child])
    assert error.value.code == "invalid_requirements"
    child["environment"]["required_capabilities"] = []
    child["limits"]["max_cost_microusd"] += 1
    root["subworkflows"] = [pin(child)]
    with pytest.raises(ExecutableError) as error:
        compile_case(root, [child])
    assert error.value.code == "expansion_exceeded"


def test_repeated_subworkflow_calls_have_distinct_call_sites(case):
    root = case("subworkflow")
    root["body"]["steps"].append({**root["body"]["steps"][0], "id": "second"})
    plan = compile_case(root, [case("minimal")])
    calls = [edge for edge in plan.edges if edge.kind == "call"]
    assert [x.source_pointer for x in calls] == ["/body/steps/0", "/body/steps/1"]
    assert plan.maximum_step_attempts == 5
    assert len(plan.regions) == 2


def test_human_review_policy_alias_preserves_exact_pin(case, catalog_case):
    root = case("reviewed_action")
    root["policies"].append({"id": "same-policy", "ref": deepcopy(root["policies"][0]["ref"])})
    root["body"]["steps"][1]["assignment_policy_ref"] = "same-policy"
    assert compile_case(root, catalog=catalog_case()).maximum_step_attempts == 5


def test_agent_read_tool_must_be_allowed_by_pinned_model(case, catalog_case):
    root, catalog = agent_case(case, catalog_case)
    connector = catalog_case()["documents"][0]
    root["connectors"] = [
        {
            "id": "store",
            "ref": {
                "kind": "connector",
                "name": connector["name"],
                "version": connector["version"],
                "sha256": api.catalog_document_digest(connector),
            },
        }
    ]
    catalog["documents"].append(connector)
    root["body"]["steps"][0]["read_tools"] = [
        {"connector_ref": "store", "operation": "read", "retry": root["body"]["steps"][0]["retry"]}
    ]
    with pytest.raises(ExecutableError) as error:
        api.compile_package(root, dependencies=[], catalog=catalog)
    assert error.value.code == "unsupported_operation"


def test_connector_input_is_typed(case, catalog_case):
    root = case("reviewed_action")
    root["body"]["steps"][0]["input"] = {
        "kind": "literal",
        "schema": {"kind": "integer"},
        "value": 1,
    }
    with pytest.raises(ExecutableError) as error:
        compile_case(root, catalog=catalog_case())
    assert error.value.code == "type_mismatch"


def test_plan_choice_metadata_revalidated_after_mutation(case, catalog_case):
    root = case("reviewed_action")
    plan = compile_case(root, catalog=catalog_case())
    plan.steps[0].output_type.source.occurrence_bindings = [
        {"owner": "owner", "path": ["0"]},
        {"owner": "owner", "path": ["0"]},
    ]
    with pytest.raises(ExecutableError):
        api.canonical_plan_json(plan)


@pytest.mark.parametrize("drop_receipts", [False, True])
def test_arrays_from_branch_specific_iterations_are_fully_verified(
    case, catalog_case, drop_receipts
):
    root = case("reviewed_action")
    root["input_schema"] = {"kind": "array", "items": {"kind": "integer"}, "max_items": 1}
    branches = []
    for prefix in ("a", "b"):
        loop = case("iteration")["body"]["steps"][0]
        loop["id"] = prefix + "loop"
        loop["input"] = {"kind": "ref", "source": {"kind": "package_input"}, "pointer": ""}
        loop["body"] = receipt_branch(case, prefix)
        branches.append(
            {
                "steps": [loop],
                "result": ref(loop["id"]),
                "result_schema": {
                    "kind": "array",
                    "items": {"kind": "handle", "handle_kind": "action_receipt"},
                    "max_items": 1,
                },
            }
        )
    decision = {
        "id": "choose",
        "kind": "decision",
        "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
        "then_body": branches[0],
        "else_body": branches[1],
    }
    verify = root["body"]["steps"][-1]
    verify["receipt"] = {"kind": "ref", "source": {"kind": "iteration_item"}, "pointer": ""}
    consumer = {
        "id": "consume",
        "kind": "iteration",
        "input": ref("choose"),
        "max_items": 1,
        "concurrency": 1,
        "body": null_body(),
    }
    consumer["body"]["steps"] = [verify]
    root["body"]["steps"] = [decision, consumer]
    if drop_receipts:
        branches[0]["result"] = {
            "kind": "array",
            "items": [],
            "item_schema": {"kind": "handle", "handle_kind": "action_receipt"},
        }
        with pytest.raises(ExecutableError) as error:
            compile_case(root, catalog=catalog_case())
        assert error.value.code == "verification_missing"
    else:
        assert len(compile_case(root, catalog=catalog_case()).outcome_obligations) == 2


def test_region_identity_survives_moving_owning_step(case):
    root = case("decision")
    first = compile_case(root)
    root["body"]["steps"].insert(0, {"id": "before", "kind": "timer", "delay_seconds": 1})
    second = compile_case(root)
    assert [(x.region_id, x.source_pointer) for x in first.regions] == [
        ("body", "/body"),
        ("decision:choose:then", "/body/steps/0/then_body"),
        ("decision:choose:else", "/body/steps/0/else_body"),
    ]
    assert [x.region_id for x in second.regions] == [x.region_id for x in first.regions]
    assert second.regions[1].source_pointer == "/body/steps/1/then_body"


def test_zero_length_constructor_iteration_preserves_declared_data_type(case):
    root = case("iteration")
    root["body"]["steps"][0]["input"] = {
        "kind": "array",
        "items": [],
        "item_schema": {"kind": "data", "schema": {"kind": "integer"}},
    }
    assert compile_case(root).maximum_step_attempts == 3


@pytest.mark.parametrize("nesting", ["none", "loop", "branch", "loop_branch"])
@pytest.mark.parametrize("same_item", [True, False])
def test_decision_selector_tracks_producing_occurrence(case, catalog_case, nesting, same_item):
    root = case("selector_occurrence")
    _, catalog = target_case(case, catalog_case)
    if same_item:
        root["body"]["steps"][1]["then_body"]["steps"][0]["body"]["steps"][0]["target"] = {
            "kind": "ref",
            "source": {"kind": "iteration_item"},
            "pointer": "/target",
        }
    if "branch" in nesting:
        root["body"] = {
            **null_body(),
            "steps": [
                {
                    "id": "outer-choice",
                    "kind": "decision",
                    "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
                    "then_body": root["body"],
                    "else_body": null_body(),
                }
            ],
        }
    if "loop" in nesting:
        root["body"] = {
            **null_body(),
            "steps": [
                {
                    "id": "outer-loop",
                    "kind": "iteration",
                    "input": {
                        "kind": "literal",
                        "schema": {"kind": "array", "items": {"kind": "null"}, "max_items": 1},
                        "value": [None],
                    },
                    "max_items": 1,
                    "concurrency": 1,
                    "body": root["body"],
                }
            ],
        }
    if same_item:
        plan = compile_case(root, catalog=catalog)
        assert len(plan.outcome_obligations) == 2
        consumer = next(step for step in plan.steps if step.step_id == "verify")
        choice = consumer.output_type.intent_source

        def owner(path):
            canonical = json.dumps([plan.package_sha256, path], separators=(",", ":")).encode()
            return "l" + sha256(canonical).hexdigest()[:63]

        body_path = "/body"
        outer_slots = []
        if "loop" in nesting:
            outer_slots.append("$item:" + owner("/body/steps/0"))
            body_path += "/steps/0/body"
        if "branch" in nesting:
            body_path += "/steps/0/then_body"
        producing_owner = owner(body_path + "/steps/0")
        consuming_slot = "$item:" + owner(body_path + "/steps/1/then_body/steps/0")
        assert choice.selection_path == [
            plan.package_sha256,
            body_path + "/steps/0/body/steps/0",
            *outer_slots,
            consuming_slot,
        ]
        assert [(binding.owner, binding.path) for binding in choice.selection_bindings] == [
            (producing_owner, [consuming_slot])
        ]
        assert [option.step_id for option in choice.options] == ["aintent", "bintent"]
        for option in choice.options:
            assert option.occurrence_path == [plan.package_sha256, *outer_slots, consuming_slot]
            assert option.occurrence_bindings == choice.selection_bindings
    else:
        with pytest.raises(ExecutableError) as error:
            compile_case(root, catalog=catalog)
        assert error.value.code == "intent_mismatch"


@pytest.mark.parametrize("operation", ["select", "item", "public_select"])
def test_shared_choice_distribution_has_bounded_work(operation):
    import subprocess
    import sys

    probe = Path(__file__).parents[1] / "fixtures/executable-v2/shared_choice_probe.py"
    result = subprocess.run(
        [sys.executable, str(probe), operation, str(Path(__file__).parents[4])],
        capture_output=True,
        text=True,
        # The probe enforces a deterministic work bound; this only contains hangs
        # and includes interpreter startup, imports and shared-runner scheduling.
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "PASS"


@pytest.mark.parametrize(
    "name", ["decision", "parallel", "iteration", "subworkflow", "receipt_decision"]
)
def test_complete_structured_literal_plan(case, catalog_case, name):
    import runpy

    fixtures = Path(__file__).parents[1] / "fixtures/executable-v2"
    golden = fixtures / f"plan_{name}.jcs"
    assert golden.is_file(), f"mandatory complete {name} literal plan is missing"
    oracle = runpy.run_path(str(fixtures / "structured_plan_oracles.py"))
    root, dependencies, expected = oracle["oracle"](name)
    expected_bytes = oracle["canonical"](expected)
    assert expected_bytes == golden.read_bytes()
    hashes = {
        name: value
        for value, name in (
            line.split() for line in (fixtures / "plan-hashes.txt").read_text().splitlines()
        )
    }
    assert sha256(expected_bytes).hexdigest() == hashes[golden.name]
    plan = compile_case(root, dependencies, catalog_case())
    assert json.loads(api.canonical_plan_json(plan)) == expected
    assert api.canonical_plan_json(plan) == expected_bytes
    assert api.compiled_plan_digest(plan) == hashes[golden.name]


def test_choice_distribution_enforces_whole_operation_budget():
    from lotuspond_sdk.executable.compiler import _distribute, _SymbolWork

    values = [("object", (("x", ("literal", str(i))),)) for i in range(2)]
    for index in range(2, 32):
        values.append(("choice", ("selector", (str(index),), ()), (values[-1], values[-2])))
    with pytest.raises(ExecutableError) as error:
        _distribute(values[-1], "x", item=False, work=_SymbolWork(remaining=100))
    assert error.value.code == "expansion_exceeded"
    work = _SymbolWork(remaining=10000)
    result = _distribute(values[-1], "x", item=False, work=work)
    assert result[2][1] is result[2][0][2][0]  # shared v29 survives both incoming edges
    assert 0 < work.remaining < 10000


def test_projection_context_includes_token_slot_and_ordered_bindings():
    from lotuspond_sdk.executable.compiler import _item, _select, _symbol_fingerprint

    decision = (
        "choice",
        ("selector", ("package", "/body/steps/0", "$item:producer"), ()),
        (("literal", "first"), ("literal", "second")),
    )
    values = ("repeat", "producer", decision)
    first, second = _select(values, "0"), _select(values, "1")
    assert first[1] == ("selector", ("package", "/body/steps/0", "0"), (("producer", ("0",)),))
    assert second[1] == ("selector", ("package", "/body/steps/0", "1"), (("producer", ("1",)),))
    assert _symbol_fingerprint(first) != _symbol_fingerprint(second)
    current = _item(values, "$item:consumer")
    other = _item(values, "$item:other")
    assert _symbol_fingerprint(current) != _symbol_fingerprint(other)
    # Selecting the same owner-bound alias twice does not append a new binding.
    assert _item(values, "$item:consumer") == current
    assert current[1][2] == (("producer", ("$item:consumer",)),)
