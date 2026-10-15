"""Compiler boundary regressions from the independent whole-branch review."""

import json
import subprocess
import sys
from copy import deepcopy

import pytest

import lotuspond_sdk.executable as api


def compile_case(root, catalog=None):
    pins = {item["ref"]["sha256"] for family in ("connectors", "policies") for item in root[family]}
    return api.compile_package(
        root,
        dependencies=[],
        catalog={
            "documents": [
                item
                for item in (catalog or {"documents": []})["documents"]
                if api.catalog_document_digest(item) in pins
            ]
        },
    )


def ref(step=None, pointer=""):
    source = {"kind": "package_input"} if step is None else {"kind": "step", "id": step}
    return {"kind": "ref", "source": source, "pointer": pointer}


def object_mapping(value):
    return {"kind": "object", "fields": [{"name": "value", "value": value}]}


@pytest.mark.parametrize("kind", ["object", "handle", "integer", "null", "boolean"])
def test_nonarray_iteration_is_a_fixed_type_error(case, catalog_case, kind):
    root = case("reviewed_action") if kind == "handle" else case("iteration")
    loop = case("iteration")["body"]["steps"][0]
    if kind == "handle":
        loop["input"] = ref("intent")
        root["body"]["steps"].insert(1, loop)
    else:
        loop["input"] = (
            {"kind": "object", "fields": []}
            if kind == "object"
            else {
                "kind": "literal",
                "schema": {"kind": kind},
                "value": {"integer": 1, "null": None, "boolean": True}[kind],
            }
        )
        root["body"]["steps"] = [loop]
    with pytest.raises(api.ExecutableError) as error:
        compile_case(root, catalog_case())
    assert error.value.code == "type_mismatch"


@pytest.mark.parametrize("constructed", [False, True])
@pytest.mark.parametrize("size", [0, 1])
def test_iteration_accepts_bounded_and_empty_arrays(case, constructed, size):
    root = case("iteration")
    step = root["body"]["steps"][0]
    if constructed:
        step["input"] = {
            "kind": "array",
            "items": [{"kind": "literal", "schema": {"kind": "integer"}, "value": 1}] * size,
            "item_schema": {"kind": "data", "schema": {"kind": "integer"}},
        }
    else:
        step["input"]["schema"]["max_items"] = size
        step["input"]["value"] = [1] * size
    assert compile_case(root).maximum_step_attempts == 3


def test_cli_nonarray_iteration_has_private_exit_two(case, tmp_path):
    root = case("iteration")
    root["body"]["steps"][0]["input"] = {"kind": "object", "fields": []}
    package, catalog, output = (tmp_path / name for name in ("package", "catalog", "plan"))
    package.write_text(json.dumps(root))
    catalog.write_text('{"documents":[]}')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lotuspond_sdk",
            "v2",
            "compile",
            str(package),
            "--catalog",
            str(catalog),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["errors"][0]["code"] == "type_mismatch"
    assert not output.exists()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_named_boolean_decision_keeps_schema_context(case, nested, typed):
    root = case("decision")
    root["schemas"] = [{"name": "flag", "schema": {"kind": "boolean"}}]
    condition = {"kind": "literal", "schema": {"kind": "ref", "name": "flag"}, "value": True}
    if nested:
        condition = {"kind": "call", "fn": "not", "args": [condition]}
    root["body"]["steps"][0]["condition"] = condition
    if typed:
        root = api.validate_executable_package(root)
    plan = api.compile_package(root, dependencies=[], catalog={"documents": []})
    assert plan.maximum_step_attempts == 3


def wait_case(case):
    root = case("minimal")
    root["policies"] = case("reviewed_action")["policies"]
    root["input_schema"] = {
        "kind": "object",
        "fields": [
            {"name": "order_id", "schema": {"kind": "string", "max_length": 40}, "required": True}
        ],
    }
    root["body"]["steps"] = [
        {
            "id": "signal",
            "kind": "external_wait",
            "signal_schema": {"kind": "null"},
            "correlation": ref(),
            "expiry_seconds": 10,
            "assignment_policy_ref": "reviewers",
        }
    ]
    return root


@pytest.mark.parametrize(
    "shape", ["reference", "object", "array", "nested", "empty_object", "empty_array"]
)
def test_wait_accepts_complete_ordinary_correlation(case, catalog_case, shape):
    root = wait_case(case)
    field = ref(pointer="/order_id")
    array = {
        "kind": "array",
        "items": [field],
        "item_schema": {
            "kind": "data",
            "schema": {"kind": "string", "max_length": 40},
        },
    }
    correlation = {
        "reference": ref(),
        "object": object_mapping(field),
        "array": array,
        "nested": object_mapping(array),
        "empty_object": {"kind": "object", "fields": []},
        "empty_array": {**array, "items": []},
    }[shape]
    root["body"]["steps"][0]["correlation"] = correlation
    assert compile_case(root, catalog_case()).maximum_step_attempts == 2


@pytest.mark.parametrize("shape", ["direct", "object", "array", "nested"])
def test_wait_refuses_handle_correlation_at_any_depth(case, catalog_case, shape):
    root = case("reviewed_action")
    wait = wait_case(case)["body"]["steps"][0]
    handle = ref("intent")
    array = {
        "kind": "array",
        "items": [handle],
        "item_schema": {
            "kind": "handle",
            "handle_kind": "action_intent",
        },
    }
    wait["correlation"] = {
        "direct": handle,
        "object": object_mapping(handle),
        "array": array,
        "nested": object_mapping(array),
    }[shape]
    root["body"]["steps"].insert(1, wait)
    with pytest.raises(api.ExecutableError) as error:
        compile_case(root, catalog_case())
    assert error.value.code == "type_mismatch"


@pytest.mark.parametrize("nested", [False, True])
def test_wait_refuses_missing_correlation_fields(case, catalog_case, nested):
    root = wait_case(case)
    root["input_schema"]["fields"][0]["required"] = False
    correlation = ref(pointer="/order_id")
    if nested:
        correlation = object_mapping(object_mapping(correlation))
    root["body"]["steps"][0]["correlation"] = correlation
    with pytest.raises(api.ExecutableError) as error:
        compile_case(root, catalog_case())
    assert error.value.code == ("unavailable_value" if nested else "type_mismatch")


def automatic_case(case, catalog_case):
    root, catalog = case("reviewed_action"), catalog_case()
    policy = next(item for item in catalog["documents"] if item["kind"] == "policy")
    policy["definition"].update(approval="none", independent_review=False)
    root["policies"][0]["ref"]["sha256"] = api.catalog_document_digest(policy)
    root["body"]["steps"][0].update(approval="none", independent_review=False)
    return root, catalog


@pytest.mark.parametrize("alias", [False, True])
def test_automatic_intent_cannot_originate_review_decision(case, catalog_case, alias):
    root, catalog = automatic_case(case, catalog_case)
    if alias:
        root["body"]["steps"].insert(
            1,
            {
                "id": "aliases",
                "kind": "parallel",
                "join": "all",
                "branches": [
                    {
                        "id": "one",
                        "body": {
                            "steps": [],
                            "result": ref("intent"),
                            "result_schema": {"kind": "handle", "handle_kind": "action_intent"},
                        },
                    }
                ],
            },
        )
        root["body"]["steps"][2]["intent"] = ref("aliases", "/one")
        root["body"]["steps"][3]["intent"] = ref("aliases", "/one")
    with pytest.raises(api.ExecutableError) as error:
        compile_case(root, catalog)
    assert error.value.code == "intent_mismatch"


@pytest.mark.parametrize(
    "modes", [("human", "human"), ("none", "none"), ("human", "none"), ("none", "human")]
)
def test_review_checks_every_joined_intent_origin(case, catalog_case, modes):
    root, catalog = case("reviewed_action"), catalog_case()
    automatic = deepcopy(next(item for item in catalog["documents"] if item["kind"] == "policy"))
    automatic["name"] = "automatic-policy"
    automatic["definition"].update(approval="none", independent_review=False)
    catalog["documents"].append(automatic)
    root["policies"].append(
        {
            "id": "automatic",
            "ref": {
                "kind": "policy",
                "name": automatic["name"],
                "version": automatic["version"],
                "sha256": api.catalog_document_digest(automatic),
            },
        }
    )
    intent, review = root["body"]["steps"][:2]
    branches = []
    for name, mode in zip(("a", "b"), modes, strict=True):
        origin = deepcopy(intent)
        origin["id"] = name
        if mode == "none":
            origin.update(approval="none", independent_review=False, policy_ref="automatic")
        branches.append(
            {
                "steps": [origin],
                "result": ref(name),
                "result_schema": {"kind": "handle", "handle_kind": "action_intent"},
            }
        )
    review["assignment_policy_ref"] = "automatic" if modes[0] == "none" else "reviewers"
    root["body"]["steps"] = [
        {
            "id": "choose",
            "kind": "decision",
            "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
            "then_body": branches[0],
            "else_body": branches[1],
        },
        review,
    ]
    review["intent"] = ref("choose")
    if modes == ("human", "human"):
        assert compile_case(root, catalog).maximum_step_attempts == 5
        return
    with pytest.raises(api.ExecutableError) as error:
        compile_case(root, catalog)
    assert error.value.code == "intent_mismatch"


def test_compiler_and_wire_agree_on_automatic_approval(
    case,
    catalog_case,
    action_binding,
    human_decision,
    runtime_command,
    binding_context,
):
    root, catalog = automatic_case(case, catalog_case)
    action_binding.update(approval_mode="none", independent_approval=False)
    intent = api.build_action_intent(action_binding)
    human_decision["intent_sha256"] = intent.sha256
    runtime_command["intent_sha256"] = intent.sha256
    runtime_command["decision_sha256"] = api.decision_digest(human_decision)
    assert api.check_decision(intent, human_decision, binding_context).code == "approval_unexpected"
    assert (
        api.check_command_binding(
            runtime_command,
            intent,
            human_decision,
            binding_context,
        ).code
        == "approval_unexpected"
    )
    with pytest.raises(api.ExecutableError) as error:
        compile_case(root, catalog)
    assert error.value.code == "intent_mismatch"
    del root["body"]["steps"][1]
    root["body"]["steps"][1]["decision"] = None
    assert compile_case(root, catalog).maximum_step_attempts == 4
    runtime_command["decision_sha256"] = None
    assert api.check_command_binding(runtime_command, intent, None, binding_context).valid


def test_human_review_control_still_compiles_and_binds(
    case,
    catalog_case,
    action_binding,
    human_decision,
    runtime_command,
    binding_context,
):
    assert compile_case(case("reviewed_action"), catalog_case()).maximum_step_attempts == 5
    intent = api.build_action_intent(action_binding)
    assert api.check_decision(intent, human_decision, binding_context).valid
    assert api.check_command_binding(runtime_command, intent, human_decision, binding_context).valid


@pytest.mark.parametrize("missing", [False, True])
def test_wait_checks_optional_joined_object_fields(case, catalog_case, missing):
    root = wait_case(case)
    schema = {
        "kind": "object",
        "fields": [
            {
                "name": "value",
                "schema": {"kind": "data", "schema": {"kind": "integer"}},
                "required": False,
            }
        ],
    }
    branch = {
        "steps": [],
        "result": object_mapping(
            {
                "kind": "literal",
                "schema": {"kind": "integer"},
                "value": 1,
            }
        ),
        "result_schema": schema,
    }
    other = deepcopy(branch)
    if missing:
        other["result"]["fields"] = []
    root["body"]["steps"].insert(
        0,
        {
            "id": "choose",
            "kind": "decision",
            "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
            "then_body": branch,
            "else_body": other,
        },
    )
    root["body"]["steps"][1]["correlation"] = ref("choose")
    if missing:
        with pytest.raises(api.ExecutableError) as error:
            compile_case(root, catalog_case())
        assert error.value.code == "type_mismatch"
    else:
        assert compile_case(root, catalog_case()).maximum_step_attempts == 4


def test_wait_accepts_joined_array_with_empty_alternative(case, catalog_case):
    root = wait_case(case)
    data = {"kind": "data", "schema": {"kind": "string", "max_length": 40}}
    branch = {
        "steps": [],
        "result": {
            "kind": "array",
            "items": [ref(pointer="/order_id")],
            "item_schema": data,
        },
        "result_schema": {"kind": "array", "items": data, "max_items": 1},
    }
    other = deepcopy(branch)
    other["result"]["items"] = []
    root["body"]["steps"].insert(
        0,
        {
            "id": "choose",
            "kind": "decision",
            "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
            "then_body": branch,
            "else_body": other,
        },
    )
    root["body"]["steps"][1]["correlation"] = ref("choose")
    assert compile_case(root, catalog_case()).maximum_step_attempts == 4
