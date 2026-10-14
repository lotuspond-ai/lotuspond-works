import copy

import pytest
from pydantic import ValidationError

from lotuspond_sdk import package_digest, validate_package
from lotuspond_sdk.executable import (
    ExecutableError,
    canonical_executable_json,
    decode_executable_bytes,
    executable_digest,
    validate_executable_package,
)

CASES = ["minimal", "decision", "parallel", "iteration", "reviewed_action", "subworkflow"]


@pytest.mark.parametrize("name", CASES)
def test_synthetic_roundtrip(case, name):
    source = case(name)
    package = validate_executable_package(source)
    canonical = canonical_executable_json(package)
    assert canonical_executable_json(decode_executable_bytes(canonical)) == canonical
    assert executable_digest(package) == executable_digest(source)
    assert package.schema_version == "lotuspond.workflow-package.v2"


def test_v2_is_explicit_and_v1_stays_fixed(case, v1_example):
    with pytest.raises(ExecutableError):
        validate_executable_package(v1_example)
    with pytest.raises(ValidationError):
        validate_package(case("minimal"))
    assert (
        package_digest(v1_example)
        == "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"
    )


@pytest.mark.parametrize("value", [1.0, -0.0, 1000000000000000100, 10**100])
def test_number_literal_normalizes_before_complete_jcs(case, value):
    source = case("minimal")
    source["schemas"] = [{"name": "numeric", "schema": {"kind": "number"}}]
    source["body"]["result"] = {
        "kind": "literal",
        "schema": {"kind": "ref", "name": "numeric"},
        "value": value,
    }
    source["body"]["result_schema"] = {"kind": "data", "schema": {"kind": "number"}}
    source["output_schema"] = {"kind": "number"}
    package = validate_executable_package(source)
    assert type(package.body.result.value) is float
    encoded = canonical_executable_json(package)
    assert canonical_executable_json(decode_executable_bytes(encoded)) == encoded


@pytest.mark.parametrize(
    "field",
    [
        "models",
        "connectors",
        "agents",
        "subworkflows",
        "evaluations",
        "policies",
        "knowledge",
        "artifacts",
        "secret_refs",
    ],
)
def test_declarations_required(case, field):
    source = case("minimal")
    del source[field]
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "duplicate",
        "missing_ref",
        "wrong_pin",
        "url",
        "backedge",
        "join",
        "concurrency",
        "bad_literal",
        "handle_output",
    ],
)
def test_structural_refusals(case, mutation):
    source = case("reviewed_action")
    if mutation == "unknown":
        source["private_secret"] = "do not expose"
    if mutation == "duplicate":
        source["body"]["steps"][1]["id"] = "intent"
    if mutation == "missing_ref":
        source["body"]["steps"][0]["connector_ref"] = "missing"
    if mutation == "wrong_pin":
        source["connectors"][0]["ref"]["kind"] = "model"
    if mutation == "url":
        source["connectors"][0]["credential_ref"] = "https://user:secret@example.test"
    if mutation == "backedge":
        source["body"]["edges"] = [{"from": "verify", "to": "intent"}]
    if mutation == "join":
        source = case("parallel")
        source["body"]["steps"][0]["join"] = "any"
    if mutation == "concurrency":
        source = case("iteration")
        source["body"]["steps"][0]["concurrency"] = 2
    if mutation == "bad_literal":
        source["body"]["result"]["value"] = "not null"
    if mutation == "handle_output":
        source["output_schema"] = {"kind": "handle", "handle_kind": "action_intent"}
    with pytest.raises(ExecutableError) as error:
        validate_executable_package(source)
    assert "do not expose" not in str(error.value)


def test_unique_ids_across_nested_bodies(case):
    source = case("decision")
    timer = {"kind": "timer", "id": "same", "delay_seconds": 1}
    source["body"]["steps"][0]["then_body"]["steps"] = [timer]
    source["body"]["steps"][0]["else_body"]["steps"] = [copy.deepcopy(timer)]
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


@pytest.mark.parametrize("value", [True, 1.0, 0, 10001])
def test_limits_exact_and_bounded(case, value):
    source = case("minimal")
    source["limits"]["max_steps"] = value
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


def test_mutated_package_revalidated(case):
    package = validate_executable_package(case("minimal"))
    package.limits.max_steps = True
    with pytest.raises(ExecutableError):
        canonical_executable_json(package)


def test_authored_step_bound(case):
    source = case("minimal")
    source["body"]["steps"] = [
        {"kind": "timer", "id": f"t{i}", "delay_seconds": 1} for i in range(129)
    ]
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


def test_every_step_and_declaration_family(case, catalog_case):
    source = case("reviewed_action")
    catalog = catalog_case()
    pin = {
        d["kind"]: {
            "kind": d["kind"],
            "name": d["name"],
            "version": d["version"],
            "sha256": "a" * 64,
        }
        for d in catalog["documents"]
    }
    for family, kind in [
        ("models", "model"),
        ("agents", "agent"),
        ("evaluations", "evaluation"),
        ("artifacts", "artifact"),
    ]:
        source[family] = [{"id": kind, "ref": pin[kind]}]
    source["knowledge"] = [
        {
            "id": "facts",
            "ref": pin["knowledge"],
            "required_freshness_seconds": 1,
            "allowed_classifications": ["synthetic"],
            "citation_schema": {"kind": "null"},
        }
    ]
    source["secret_refs"] = ["credential"]
    source["connectors"][0]["credential_ref"] = "credential"
    retry = {"max_attempts": 1, "timeout_seconds": 1, "backoff_seconds": 0, "retry_codes": []}
    value = {"kind": "literal", "schema": {"kind": "null"}, "value": None}
    steps = [
        {"kind": "transform", "id": "map", "mapping": value, "output_schema": {"kind": "null"}},
        {
            "kind": "connector_read",
            "id": "read",
            "connector_ref": "store",
            "operation": "read",
            "target": value,
            "input": value,
            "retry": retry,
        },
        {
            "kind": "agent",
            "id": "think",
            "model_ref": "model",
            "instructions": "Synthetic",
            "output_schema": {"kind": "null"},
            "read_tools": [{"connector_ref": "store", "operation": "read", "retry": retry}],
            "knowledge_refs": ["facts"],
            "limits": catalog["documents"][1]["definition"]["limits"],
            "retry": retry,
        },
        {
            "kind": "human_task",
            "id": "form",
            "mode": "form",
            "form_schema": {"kind": "null"},
            "assignment_policy_ref": "reviewers",
            "timeout_seconds": 1,
        },
        {"kind": "timer", "id": "sleep", "delay_seconds": 1},
        {
            "kind": "external_wait",
            "id": "wait",
            "signal_schema": {"kind": "null"},
            "correlation": value,
            "expiry_seconds": 1,
            "assignment_policy_ref": "reviewers",
        },
    ]
    source["body"]["steps"].extend(steps)
    verification = copy.deepcopy(source["body"]["steps"][3])
    del verification["id"]
    del verification["kind"]
    del verification["receipt"]
    intent = copy.deepcopy(source["body"]["steps"][0])
    del intent["id"]
    del intent["kind"]
    source["body"]["steps"][2]["compensation"] = {
        **intent,
        "retry": retry,
        "verification": verification,
    }
    source["assurance"]["evaluation_refs"] = ["evaluation"]
    source["assurance"]["outcome_rule_refs"] = ["observed"]
    package = validate_executable_package(source)
    assert package.body.steps[6].knowledge_refs == ["facts"]
    assert canonical_executable_json(
        decode_executable_bytes(canonical_executable_json(package))
    ) == canonical_executable_json(package)
    source["body"]["steps"][6]["agent_ref"] = "agent"
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


@pytest.mark.parametrize(
    "family,kind",
    [
        ("models", "model"),
        ("connectors", "connector"),
        ("agents", "agent"),
        ("subworkflows", "workflow"),
        ("evaluations", "evaluation"),
        ("policies", "policy"),
        ("artifacts", "artifact"),
    ],
)
def test_declaration_count_and_duplicates(case, family, kind):
    source = case("minimal")
    source[family] = [
        {
            "id": f"d{i}",
            "ref": {"kind": kind, "name": f"d{i}", "version": "1.0.0", "sha256": "a" * 64},
        }
        for i in range(64)
    ]
    validate_executable_package(source)
    source[family].append(copy.deepcopy(source[family][0]))
    with pytest.raises(ExecutableError):
        validate_executable_package(source)
    source[family].pop()
    source[family][-1]["id"] = source[family][0]["id"]
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_attempts", 11),
        ("max_attempts", True),
        ("timeout_seconds", 86401),
        ("timeout_seconds", 1.0),
        ("backoff_seconds", 3601),
        ("retry_codes", ["unknown"]),
        ("retry_codes", ["unavailable", "unavailable"]),
    ],
)
def test_retry_bounds(case, field, value):
    source = case("reviewed_action")
    source["body"]["steps"][2]["retry"][field] = value
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


@pytest.mark.parametrize(
    "field,maximum",
    [
        ("max_steps", 10000),
        ("active_seconds", 86400),
        ("elapsed_seconds", 31536000),
        ("retained_waits", 10000),
        ("max_input_tokens", 10000000),
        ("max_output_tokens", 10000000),
        ("max_cost_microusd", 1000000000000),
    ],
)
def test_package_limit_maxima(case, field, maximum):
    source = case("minimal")
    source["limits"][field] = maximum
    validate_executable_package(source)
    source["limits"][field] = maximum + 1
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


@pytest.mark.parametrize("value", [0, 1, 1.0, "true"])
def test_exact_independence_flag(case, value):
    source = case("reviewed_action")
    source["body"]["steps"][0]["independent_review"] = value
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


def test_nested_limits_before_model_recursion(case):
    source = case("iteration")
    nested = source["body"]["steps"][0]
    for i in range(9):
        child = copy.deepcopy(nested)
        child["id"] = f"each{i}"
        nested["body"]["steps"] = [child]
        nested = child
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


def test_unknown_mapping_step_reference(case):
    source = case("minimal")
    source["body"]["result"] = {
        "kind": "ref",
        "source": {"kind": "step", "id": "absent"},
        "pointer": "",
    }
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


def test_unused_schema_cycles_rejected(case):
    source = case("minimal")
    source["schemas"] = [{"name": "loop", "schema": {"kind": "ref", "name": "loop"}}]
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


def test_normalized_defaults_and_member_array_order(case):
    source = case("reviewed_action")
    normalized = decode_executable_bytes(canonical_executable_json(source))
    assert normalized["connectors"][0]["credential_ref"] is None
    assert normalized["body"]["steps"][1]["form_schema"] is None
    assert normalized["body"]["steps"][1]["escalation_policy_ref"] is None
    assert normalized["body"]["steps"][2]["compensation"] is None
    assert executable_digest(dict(reversed(list(source.items())))) == executable_digest(source)
    source["body"]["steps"].reverse()
    assert executable_digest(source) != executable_digest(normalized)


def test_constructed_models_and_serializers_are_not_trusted(case):
    from lotuspond_sdk.executable.package import ExecutablePackage

    package = ExecutablePackage.model_construct(**case("minimal"))
    assert canonical_executable_json(package) == canonical_executable_json(case("minimal"))
    package.__dict__["extra"] = "secret"
    with pytest.raises(ExecutableError):
        validate_executable_package(package)

    class Hostile(ExecutablePackage):
        def model_dump(self, *args, **kwargs):
            raise AssertionError("must never call arbitrary serializers")

    with pytest.raises(ExecutableError):
        validate_executable_package(Hostile.model_construct(**case("minimal")))


def test_complete_normalized_document_bytes_and_items(case):
    # A schema-valid raw document can grow beyond the cap when defaults are inserted.
    source = case("minimal")
    fields = [
        {"name": f"f{i}", "schema": {"kind": "string", "max_length": 65536}, "required": True}
        for i in range(16)
    ]
    schema = {"kind": "object", "fields": fields}
    source["output_schema"] = schema
    source["body"]["result_schema"] = {"kind": "data", "schema": schema}
    source["body"]["result"] = {
        "kind": "literal",
        "schema": schema,
        "value": {f"f{i}": "x" * 65536 for i in range(16)},
    }
    with pytest.raises(ExecutableError):
        canonical_executable_json(source)


def test_domain_error_codes_survive_shape_phase(case):
    source = case("reviewed_action")
    source["body"]["steps"][1]["id"] = "intent"
    with pytest.raises(ExecutableError) as error:
        validate_executable_package(source)
    assert error.value.code == "duplicate_identifier"
    source = case("reviewed_action")
    source["body"]["steps"][0]["connector_ref"] = "absent"
    with pytest.raises(ExecutableError) as error:
        validate_executable_package(source)
    assert error.value.code == "unknown_reference"
    source = case("minimal")
    source["extra"] = "private"
    with pytest.raises(ExecutableError) as error:
        validate_executable_package(source)
    assert error.value.code == "invalid_package"


def test_default_insertion_is_measured_at_complete_byte_boundary(case):
    import json

    source = case("minimal")
    schema = {
        "kind": "object",
        "fields": [
            {"name": f"f{i}", "schema": {"kind": "string", "max_length": 65536}, "required": True}
            for i in range(16)
        ]
        + [{"name": "n", "schema": {"kind": "number"}, "required": True}],
    }
    source["output_schema"] = schema
    source["body"]["result_schema"] = {"kind": "data", "schema": schema}
    values = {f"f{i}": "" for i in range(16)}
    values["n"] = 10**100
    source["body"]["result"] = {"kind": "literal", "schema": schema, "value": values}
    overhead = len(canonical_executable_json(source))
    remaining = 1048576 - overhead
    for i in range(16):
        amount = min(remaining, 65536)
        values[f"f{i}"] = "x" * amount
        remaining -= amount
    assert remaining == 0
    encoded = canonical_executable_json(source)
    assert len(encoded) == 1048576
    assert canonical_executable_json(decode_executable_bytes(encoded)) == encoded
    values["f15"] += "x"
    # Raw source can still fit when its integral number is spelled canonically.
    raw = (
        json.dumps(source, separators=(",", ":")).encode().replace(str(10**100).encode(), b"1e+100")
    )
    assert len(raw) <= 1048576
    with pytest.raises(ExecutableError) as error:
        canonical_executable_json(decode_executable_bytes(raw))
    assert error.value.code == "input_too_large"


def test_complete_normalized_item_limit(case):
    # Defaults on each integer schema push an otherwise bounded document over 10,000 items.
    source = case("minimal")
    item = {
        "kind": "object",
        "fields": [
            {"name": f"f{i}", "schema": {"kind": "integer"}, "required": True} for i in range(64)
        ],
    }
    source["body"]["steps"] = [
        {
            "id": f"t{i}",
            "kind": "transform",
            "mapping": {"kind": "literal", "schema": {"kind": "null"}, "value": None},
            "output_schema": copy.deepcopy(item),
        }
        for i in range(12)
    ]
    from lotuspond_sdk.executable.input import project_json

    project_json(source)
    with pytest.raises(ExecutableError) as error:
        validate_executable_package(source)
    assert error.value.code == "input_too_complex"


@pytest.mark.parametrize("depth", [8, 9])
def test_control_depth_boundary(case, depth):
    source = case("iteration")
    template = copy.deepcopy(source["body"]["steps"][0])
    current = source["body"]
    for i in range(depth):
        step = copy.deepcopy(template)
        step["id"] = f"each{i}"
        current["steps"] = [step]
        current = step["body"]
    if depth == 8:
        validate_executable_package(source)
    else:
        with pytest.raises(ExecutableError):
            validate_executable_package(source)


@pytest.mark.parametrize(
    "mutation",
    [
        "decision_exports",
        "branch_duplicate",
        "missing_credential",
        "knowledge_missing",
        "human_mixed",
        "human_missing",
        "literal_handle",
        "policy_missing",
        "rule_missing",
        "pin_conflict",
        "schema_count",
        "platform_duplicate",
        "capability_duplicate",
        "environment_count",
        "literal_unknown_schema",
    ],
)
def test_more_closed_structures(case, mutation):
    source = case("reviewed_action")
    if mutation == "decision_exports":
        source = case("decision")
        source["body"]["steps"][0]["else_body"]["result_schema"] = {
            "kind": "data",
            "schema": {"kind": "boolean"},
        }
    if mutation == "branch_duplicate":
        source = case("parallel")
        source["body"]["steps"][0]["branches"][1]["id"] = "left"
    if mutation == "missing_credential":
        source["connectors"][0]["credential_ref"] = "missing"
    if mutation == "knowledge_missing":
        source["knowledge"] = [
            {
                "id": "facts",
                "ref": {
                    "kind": "knowledge",
                    "name": "facts",
                    "version": "1.0.0",
                    "sha256": "a" * 64,
                },
                "required_freshness_seconds": 0,
                "allowed_classifications": [],
                "citation_schema": {"kind": "null"},
            }
        ]
    if mutation == "human_mixed":
        source["body"]["steps"][1]["form_schema"] = {"kind": "null"}
    if mutation == "human_missing":
        del source["body"]["steps"][1]["intent"]
    if mutation == "literal_handle":
        source["body"]["steps"][2]["intent"] = {
            "kind": "literal",
            "schema": {"kind": "null"},
            "value": None,
        }
    if mutation == "policy_missing":
        source["body"]["steps"][1]["assignment_policy_ref"] = "missing"
    if mutation == "rule_missing":
        source["assurance"]["outcome_rule_refs"] = ["missing"]
    if mutation == "pin_conflict":
        source["connectors"].append(copy.deepcopy(source["connectors"][0]))
        source["connectors"][1]["id"] = "another"
        source["connectors"][1]["ref"]["sha256"] = "b" * 64
    if mutation == "schema_count":
        source["schemas"] = [{"name": f"s{i}", "schema": {"kind": "null"}} for i in range(65)]
    if mutation == "platform_duplicate":
        source["environment"]["platforms"] *= 2
    if mutation == "capability_duplicate":
        source["environment"]["required_capabilities"] = ["read", "read"]
    if mutation == "environment_count":
        source["environment"]["destination_classes"] = [f"d{i}" for i in range(65)]
    if mutation == "literal_unknown_schema":
        source["body"]["result"]["schema"] = {"kind": "ref", "name": "missing"}
    with pytest.raises(ExecutableError):
        validate_executable_package(source)


def test_schema_count_is_refused_before_recursive_shape_validation(case):
    source = case("minimal")
    source["schemas"] = [{"name": f"s{i}", "schema": {"kind": "null"}} for i in range(65)]
    with pytest.raises(ExecutableError) as error:
        validate_executable_package(source)
    assert error.value.code == "invalid_schema"
