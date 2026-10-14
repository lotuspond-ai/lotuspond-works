"""Synthetic support declarations: exact pins, complete sources, and no authority."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

import lotuspond_sdk.executable as api


@pytest.fixture
def requirements():
    return {
        "format_version": 1,
        "runtime_contract": "lotuspond.runtime-contract.v1",
        "os": "linux",
        "architecture": "amd64",
        "required_capabilities": [],
    }


@pytest.fixture
def profile(case):
    return {
        "format_version": "lotuspond.workflow-capabilities.v2",
        "assurance": "synthetic-declaration",
        "schema_versions": ["lotuspond.workflow-package.v2"],
        "compiler_revisions": ["lotuspond.workflow-compiler.v2.1"],
        "canonicalizations": ["lotuspond.workflow-package.jcs.v2"],
        "step_kinds": [
            "transform",
            "decision",
            "parallel",
            "iteration",
            "connector_read",
            "agent",
            "action_intent",
            "human_task",
            "action_dispatch",
            "timer",
            "external_wait",
            "subworkflow",
            "outcome_verification",
        ],
        "expression_functions": [
            "concat",
            "add",
            "subtract",
            "multiply",
            "divide",
            "eq",
            "ne",
            "lt",
            "le",
            "gt",
            "ge",
            "and",
            "or",
            "not",
            "exists",
            "coalesce",
        ],
        "value_kinds": [
            "null",
            "boolean",
            "integer",
            "number",
            "string",
            "decimal",
            "array",
            "object",
            "ref",
            "action_intent",
            "human_decision",
            "action_receipt",
            "verification_evidence",
        ],
        "artifacts": [],
        "operations": [],
        "model_configurations": [],
        "platforms": [{"os": "linux", "architecture": "amd64", "capabilities": []}],
        "supported_limits": case("minimal")["limits"],
    }


def assess(raw, profile, requirements, *, dependencies=None, catalog=None):
    assert hasattr(api, "check_declared_compatibility"), "v2 compatibility API is missing"
    return api.check_declared_compatibility(
        raw,
        profile,
        requirements,
        dependencies=[] if dependencies is None else dependencies,
        catalog={"documents": []} if catalog is None else catalog,
    )


def test_minimal_exact_declarations_and_independent_hashes(case, profile, requirements):
    report = assess(case("minimal"), profile, requirements)
    assert report.compatible is True
    assert report.contract_valid is True
    assert report.closure_complete is True
    assert report.compilation_status == "valid"
    assert report.signature_status == "not_checked"
    assert report.authority_granted is False
    assert report.reasons == [] and report.reasons_truncated is False
    # Inputs use only ASCII and integer domains: sorted stdlib JSON is an independent JCS oracle.
    for raw, actual in [
        (profile, report.profile_sha256),
        (requirements, report.requirements_sha256),
    ]:
        assert (
            actual
            == sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )
    assert (
        report.package_sha256 == "724daea0bf5e9e1b724d74dc278b019a7edf1f1e3f98f76e223ca50e4fe71d57"
    )


@pytest.mark.parametrize(
    "field,code",
    [
        ("schema_versions", "unsupported_schema"),
        ("compiler_revisions", "unsupported_compiler"),
        ("canonicalizations", "unsupported_canonicalization"),
        ("value_kinds", "unsupported_value"),
        ("platforms", "unsupported_platform"),
    ],
)
def test_empty_support_is_refusal(case, profile, requirements, field, code):
    profile[field] = []
    report = assess(case("minimal"), profile, requirements)
    assert report.compatible is False
    assert code in {r.code for r in report.reasons}


@pytest.mark.parametrize(
    "field",
    [
        "max_steps",
        "active_seconds",
        "elapsed_seconds",
        "retained_waits",
        "max_input_tokens",
        "max_output_tokens",
        "max_cost_microusd",
    ],
)
def test_each_declared_budget_is_required(case, profile, requirements, field):
    profile["supported_limits"][field] -= 1
    report = assess(case("minimal"), profile, requirements)
    assert not report.compatible
    assert any(
        r.code == "insufficient_limit" and r.path.endswith("/" + field) for r in report.reasons
    )


def test_missing_closure_is_unresolved(case, profile, requirements):
    report = assess(case("subworkflow"), profile, requirements)
    assert report.contract_valid is True
    assert report.closure_complete is False
    assert report.compilation_status == "unresolved"
    assert report.compatible is False
    assert "dependency_missing" in {r.code for r in report.reasons}


@pytest.mark.parametrize(
    "field",
    [
        "format_version",
        "assurance",
        "schema_versions",
        "compiler_revisions",
        "canonicalizations",
        "step_kinds",
        "expression_functions",
        "value_kinds",
        "artifacts",
        "operations",
        "model_configurations",
        "platforms",
        "supported_limits",
    ],
)
def test_profile_fields_are_required(case, profile, requirements, field):
    del profile[field]
    with pytest.raises(api.ExecutableError) as error:
        assess(case("minimal"), profile, requirements)
    assert error.value.code == "invalid_profile"


@pytest.mark.parametrize(
    "field", ["format_version", "runtime_contract", "os", "architecture", "required_capabilities"]
)
def test_requirement_fields_are_required(case, profile, requirements, field):
    del requirements[field]
    with pytest.raises(api.ExecutableError) as error:
        assess(case("minimal"), profile, requirements)
    assert error.value.code == "invalid_requirements"


def support_catalog(profile, documents):
    """Test fixture declaration, independently assembled from explicit source pins."""
    for document in documents:
        pin = {k: document[k] for k in ("kind", "name", "version")}
        pin["sha256"] = api.catalog_document_digest(document)
        profile["artifacts"].append(pin)
        if document["kind"] == "connector":
            profile["operations"].append(
                {
                    "connector_ref": deepcopy(pin),
                    "operations": [op["id"] for op in document["definition"]["operations"]],
                }
            )
        if document["kind"] == "model":
            profile["model_configurations"].append(
                {
                    "model_ref": deepcopy(pin),
                    "configuration_revision": document["definition"]["configuration_revision"],
                    "configuration_sha256": document["definition"]["configuration_sha256"],
                }
            )


def declared_catalog_package(case, catalog_case, profile):
    root, catalog = case("minimal"), catalog_case()
    families = {
        "connector": "connectors",
        "model": "models",
        "agent": "agents",
        "evaluation": "evaluations",
        "policy": "policies",
        "knowledge": "knowledge",
        "artifact": "artifacts",
    }
    support_catalog(profile, catalog["documents"])
    for pin in profile["artifacts"]:
        entry = {"id": pin["kind"], "ref": deepcopy(pin)}
        if pin["kind"] == "knowledge":
            entry.update(
                required_freshness_seconds=60,
                allowed_classifications=[],
                citation_schema={"kind": "null"},
            )
        root[families[pin["kind"]]].append(entry)
    return root, catalog


def test_all_unused_artifacts_and_operations_and_model_pins(
    case, catalog_case, profile, requirements
):
    root, catalog = declared_catalog_package(case, catalog_case, profile)
    assert assess(root, profile, requirements, catalog=catalog).compatible
    for index in range(7):
        reduced = deepcopy(profile)
        reduced["artifacts"].pop(index)
        report = assess(root, reduced, requirements, catalog=catalog)
        assert not report.compatible and "unsupported_artifact" in {r.code for r in report.reasons}
    for op in ("read", "write"):
        reduced = deepcopy(profile)
        reduced["operations"][0]["operations"].remove(op)
        report = assess(root, reduced, requirements, catalog=catalog)
        assert not report.compatible and "unsupported_operation" in {r.code for r in report.reasons}
    for field in ("configuration_revision", "configuration_sha256"):
        reduced = deepcopy(profile)
        reduced["model_configurations"][0][field] = "2" if field.endswith("revision") else "2" * 64
        report = assess(root, reduced, requirements, catalog=catalog)
        assert not report.compatible and "unsupported_model_configuration" in {
            r.code for r in report.reasons
        }


def test_catalog_internal_pins_are_required(case, catalog_case, profile, requirements):
    documents = [d for d in catalog_case()["documents"] if d["kind"] in ("agent", "model")]
    support_catalog(profile, documents)
    root = case("minimal")
    root["agents"] = [{"id": "reused", "ref": deepcopy(profile["artifacts"][1])}]
    assert assess(root, profile, requirements, catalog={"documents": documents}).compatible
    profile["artifacts"].pop(0)
    report = assess(root, profile, requirements, catalog={"documents": documents})
    assert not report.compatible
    assert any(r.code == "unsupported_artifact" for r in report.reasons)


def test_complete_dependency_features_and_limits(case, profile, requirements):
    child = case("decision")
    child["limits"]["active_seconds"] += 1
    root = case("subworkflow")
    root["limits"]["active_seconds"] = child["limits"]["active_seconds"]
    root["subworkflows"][0]["ref"] = {
        "kind": "workflow",
        "name": child["package_id"],
        "version": child["version"],
        "sha256": api.executable_digest(child),
    }
    profile["artifacts"] = [deepcopy(root["subworkflows"][0]["ref"])]
    profile["step_kinds"].remove("decision")
    report = assess(root, profile, requirements, dependencies=[child])
    assert {"unsupported_step", "insufficient_limit"} <= {r.code for r in report.reasons}


def test_empty_profile_truncates_complete_refusal_set(case, catalog_case, profile, requirements):
    root, catalog = declared_catalog_package(case, catalog_case, profile)
    for field, value in profile.items():
        if isinstance(value, list):
            profile[field] = []
    profile["supported_limits"] = {key: 0 for key in profile["supported_limits"]}
    report = assess(root, profile, requirements, catalog=catalog)
    assert not report.compatible and report.reasons_truncated
    assert len(report.reasons) == 20
    pairs = [(r.code, r.path) for r in report.reasons]
    assert pairs == sorted(set(pairs))
    assert all("fixture" not in r.path and "synthetic" not in r.path for r in report.reasons)


@pytest.mark.parametrize("defect", ["digest", "conflict", "extra", "malformed"])
def test_missing_does_not_hide_invalid_supplied_definitions(
    case, catalog_case, profile, requirements, defect
):
    root, catalog = declared_catalog_package(case, catalog_case, profile)
    catalog["documents"].pop(0)  # First family is missing; remaining definitions still matter.
    if defect == "digest":
        catalog["documents"][0]["definition"]["configuration_revision"] = "changed"
    elif defect == "conflict":
        other = deepcopy(catalog["documents"][0])
        other["definition"]["configuration_revision"] = "changed"
        catalog["documents"].append(other)
    elif defect == "extra":
        other = deepcopy(catalog["documents"][0])
        other["name"] = "unreachable"
        catalog["documents"].append(other)
    else:
        catalog["documents"][0]["definition"]["limits"]["max_steps"] = True
    with pytest.raises(api.ExecutableError) as error:
        assess(root, profile, requirements, catalog=catalog)
    assert error.value.code != "dependency_missing"


@pytest.mark.parametrize(
    "field",
    [
        "schema_versions",
        "compiler_revisions",
        "canonicalizations",
        "step_kinds",
        "expression_functions",
        "value_kinds",
        "platforms",
    ],
)
def test_duplicate_support_entries_are_invalid(case, profile, requirements, field):
    profile[field].append(deepcopy(profile[field][0]))
    with pytest.raises(api.ExecutableError) as error:
        assess(case("minimal"), profile, requirements)
    assert error.value.code == "invalid_profile"


@pytest.mark.parametrize(
    "field",
    [
        "schema_versions",
        "compiler_revisions",
        "canonicalizations",
        "step_kinds",
        "expression_functions",
        "value_kinds",
    ],
)
def test_unknown_literals_are_invalid(case, profile, requirements, field):
    profile[field] = ["unknown-private-value"]
    with pytest.raises(api.ExecutableError) as error:
        assess(case("minimal"), profile, requirements)
    assert error.value.code == "invalid_profile"
    assert "unknown-private-value" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", True),
        ("format_version", 1.0),
        ("runtime_contract", "other"),
        ("required_capabilities", ["same", "same"]),
    ],
)
def test_requirement_refusals(case, profile, requirements, field, value):
    requirements[field] = value
    with pytest.raises(api.ExecutableError) as error:
        assess(case("minimal"), profile, requirements)
    assert error.value.code == "invalid_requirements"


def test_platform_and_package_capabilities_are_explicit(case, profile, requirements):
    root = case("minimal")
    root["environment"]["required_capabilities"] = ["private-package-capability"]
    requirements["required_capabilities"] = ["private-context-capability"]
    report = assess(root, profile, requirements)
    assert len([r for r in report.reasons if r.code == "missing_capability"]) == 2
    profile["platforms"][0]["capabilities"] = [
        "private-package-capability",
        "private-context-capability",
    ]
    assert assess(root, profile, requirements).compatible
    root["environment"]["platforms"] = []
    assert not assess(root, profile, requirements).compatible


def test_report_admission_flags_and_ascii_output(case, profile, requirements):
    result = assess(case("minimal"), profile, requirements)
    encoded = api.executable_compatibility_report_json(result)
    assert encoded.endswith(b"\n") and encoded.isascii()
    assert api.validate_executable_compatibility_report(json.loads(encoded)) == result
    for field, value in [
        ("authority_granted", 0),
        ("contract_valid", 1),
        ("closure_complete", 1),
        ("compatible", 1),
        ("reasons_truncated", 0),
    ]:
        bad = deepcopy(result)
        setattr(bad, field, value)
        with pytest.raises(api.ExecutableError):
            api.executable_compatibility_report_json(bad)


def test_mutated_profile_and_requirements_are_readmitted(case, profile, requirements):
    target = api.validate_executable_capability_profile(profile)
    context = api.validate_executable_requirements(requirements)
    target.supported_limits.max_steps = True
    with pytest.raises(api.ExecutableError):
        assess(case("minimal"), target, context)
    context.format_version = True
    with pytest.raises(api.ExecutableError):
        assess(case("minimal"), profile, context)


@pytest.mark.parametrize(
    "case_name,kind",
    [
        ("decision", "decision"),
        ("parallel", "parallel"),
        ("iteration", "iteration"),
        ("minimal", "transform"),
        ("reviewed_action", "action_intent"),
        ("reviewed_action", "human_task"),
        ("reviewed_action", "action_dispatch"),
        ("reviewed_action", "outcome_verification"),
        ("typed_agent", "agent"),
        ("subworkflow", "subworkflow"),
    ],
)
def test_authored_step_support(case, catalog_case, profile, requirements, case_name, kind):
    root = case(case_name)
    if kind == "transform":
        root["body"]["steps"] = [
            {
                "kind": "transform",
                "id": "map",
                "mapping": deepcopy(root["body"]["result"]),
                "output_schema": {"kind": "null"},
            }
        ]
    declared = {
        d["ref"]["sha256"]
        for family in ("connectors", "policies", "models", "agents")
        for d in root[family]
    }
    catalog = {
        "documents": [
            d for d in catalog_case()["documents"] if api.catalog_document_digest(d) in declared
        ]
    }
    support_catalog(profile, catalog["documents"])
    dependencies = [case("minimal")] if case_name == "subworkflow" else []
    profile["artifacts"].extend(d["ref"] for d in root["subworkflows"])
    assert assess(
        root, profile, requirements, dependencies=dependencies, catalog=catalog
    ).compatible
    profile["step_kinds"].remove(kind)
    report = assess(root, profile, requirements, dependencies=dependencies, catalog=catalog)
    assert not report.compatible
    assert "unsupported_step" in {r.code for r in report.reasons}


def test_optional_expression_and_agent_input_are_requirements(
    case, catalog_case, profile, requirements
):
    root = case("typed_agent")
    model = next(d for d in catalog_case()["documents"] if d["kind"] == "model")
    support_catalog(profile, [model])
    null = {"kind": "literal", "schema": {"kind": "null"}, "value": None}
    root["body"]["steps"][0]["input"] = {
        "kind": "call",
        "fn": "coalesce",
        "args": [deepcopy(null), deepcopy(null)],
    }
    assert assess(root, profile, requirements, catalog={"documents": [model]}).compatible
    profile["expression_functions"].remove("coalesce")
    report = assess(root, profile, requirements, catalog={"documents": [model]})
    assert "unsupported_expression" in {r.code for r in report.reasons}


def test_catalog_unused_schema_types_are_requirements(case, catalog_case, profile, requirements):
    model = next(d for d in catalog_case()["documents"] if d["kind"] == "model")
    model["schemas"] = [
        {"name": "unused", "schema": {"kind": "decimal", "precision": 4, "scale": 2}}
    ]
    support_catalog(profile, [model])
    root = case("minimal")
    root["models"] = [{"id": "unused", "ref": profile["artifacts"][0]}]
    assert assess(root, profile, requirements, catalog={"documents": [model]}).compatible
    profile["value_kinds"].remove("decimal")
    report = assess(root, profile, requirements, catalog={"documents": [model]})
    assert "unsupported_value" in {r.code for r in report.reasons}


def test_literal_payload_is_not_scanned_as_authored_program(case, profile, requirements):
    root = case("minimal")
    schema = {
        "kind": "object",
        "fields": [
            {"name": name, "schema": {"kind": "string", "max_length": 64}, "required": True}
            for name in ("kind", "fn")
        ],
    }
    root["output_schema"] = schema
    root["body"]["result_schema"] = {"kind": "data", "schema": schema}
    root["body"]["result"] = {
        "kind": "literal",
        "schema": schema,
        "value": {"kind": "call", "fn": "coalesce"},
    }
    profile["expression_functions"] = []
    assert assess(root, profile, requirements).compatible


def test_actual_formatted_output_cap(case, profile, requirements):
    from lotuspond_sdk.executable._validation import report_bytes
    from lotuspond_sdk.executable.compatibility import COMPATIBILITY_MODELS

    result = assess(case("minimal"), profile, requirements)
    output = api.executable_compatibility_report_json(result)
    assert (
        report_bytes(
            result,
            model_type=api.ExecutableCompatibilityReport,
            model_types=COMPATIBILITY_MODELS,
            code="invalid_requirements",
            max_bytes=len(output),
        )
        == output
    )
    with pytest.raises(api.ExecutableError) as error:
        report_bytes(
            result,
            model_type=api.ExecutableCompatibilityReport,
            model_types=COMPATIBILITY_MODELS,
            code="invalid_requirements",
            max_bytes=len(output) - 1,
        )
    assert error.value.code == "report_too_large"


@pytest.mark.parametrize(
    "fn",
    [
        "concat",
        "add",
        "subtract",
        "multiply",
        "divide",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and",
        "or",
        "not",
        "exists",
        "coalesce",
    ],
)
def test_every_expression_function(case, profile, requirements, fn):
    root = case("minimal")
    integer = {"kind": "literal", "schema": {"kind": "integer"}, "value": 1}
    boolean = {"kind": "literal", "schema": {"kind": "boolean"}, "value": True}
    string = {"kind": "literal", "schema": {"kind": "string", "max_length": 4}, "value": "x"}
    args, schema = [deepcopy(integer), deepcopy(integer)], {"kind": "integer"}
    if fn == "concat":
        args, schema = [deepcopy(string), deepcopy(string)], {"kind": "string", "max_length": 8}
    elif fn in ("and", "or", "not"):
        args, schema = [deepcopy(boolean)] * (1 if fn == "not" else 2), {"kind": "boolean"}
    elif fn in ("eq", "ne", "lt", "le", "gt", "ge", "exists"):
        schema = {"kind": "boolean"}
        if fn == "exists":
            args = [{"kind": "ref", "source": {"kind": "package_input"}, "pointer": ""}]
    root["body"]["result"] = {"kind": "call", "fn": fn, "args": args}
    root["body"]["result_schema"] = {"kind": "data", "schema": schema}
    root["output_schema"] = schema
    assert assess(root, profile, requirements).compatible
    profile["expression_functions"].remove(fn)
    report = assess(root, profile, requirements)
    assert any(r.code == "unsupported_expression" for r in report.reasons)


@pytest.mark.parametrize(
    "kind,schema",
    [
        ("null", {"kind": "null"}),
        ("boolean", {"kind": "boolean"}),
        ("integer", {"kind": "integer"}),
        ("number", {"kind": "number"}),
        ("string", {"kind": "string", "max_length": 16}),
        ("decimal", {"kind": "decimal", "precision": 4, "scale": 2}),
        ("array", {"kind": "array", "items": {"kind": "null"}, "max_items": 2}),
        ("object", {"kind": "object", "fields": []}),
        ("ref", {"kind": "ref", "name": "base"}),
    ],
)
def test_every_unused_data_kind(case, profile, requirements, kind, schema):
    root = case("minimal")
    root["schemas"] = [
        {"name": "base", "schema": {"kind": "null"}},
        {"name": "unused", "schema": schema},
    ]
    assert assess(root, profile, requirements).compatible
    profile["value_kinds"].remove(kind)
    report = assess(root, profile, requirements)
    assert any(r.code == "unsupported_value" for r in report.reasons)


@pytest.mark.parametrize(
    "kind", ["action_intent", "human_decision", "action_receipt", "verification_evidence"]
)
def test_automatic_originating_handles_require_support(
    case, catalog_case, profile, requirements, kind
):
    root = case("reviewed_action")
    documents = [d for d in catalog_case()["documents"] if d["kind"] in ("connector", "policy")]
    support_catalog(profile, documents)
    profile["value_kinds"].remove(kind)
    report = assess(root, profile, requirements, catalog={"documents": documents})
    assert not report.compatible and any(r.code == "unsupported_value" for r in report.reasons)


@pytest.mark.parametrize("kind", ["timer", "external_wait", "connector_read"])
def test_remaining_steps(case, catalog_case, profile, requirements, kind):
    root = case("reviewed_action")
    null = deepcopy(root["body"]["result"])
    steps = {
        "timer": {"id": "extra", "kind": "timer", "delay_seconds": 1},
        "external_wait": {
            "id": "extra",
            "kind": "external_wait",
            "signal_schema": {"kind": "null"},
            "correlation": null,
            "expiry_seconds": 10,
            "assignment_policy_ref": "reviewers",
        },
        "connector_read": {
            "id": "extra",
            "kind": "connector_read",
            "connector_ref": "store",
            "operation": "read",
            "target": null,
            "input": null,
            "retry": root["body"]["steps"][2]["retry"],
        },
    }
    root["body"]["steps"].append(steps[kind])
    documents = [d for d in catalog_case()["documents"] if d["kind"] in ("connector", "policy")]
    support_catalog(profile, documents)
    assert assess(root, profile, requirements, catalog={"documents": documents}).compatible
    profile["step_kinds"].remove(kind)
    assert any(
        r.code == "unsupported_step"
        for r in assess(root, profile, requirements, catalog={"documents": documents}).reasons
    )


def test_multi_origin_dispatch_checks_every_operation(case, catalog_case, profile, requirements):
    root = case("reviewed_action")
    documents = [d for d in catalog_case()["documents"] if d["kind"] in ("connector", "policy")]
    connector = documents[0]
    alternate = deepcopy(connector["definition"]["operations"][1])
    alternate["id"] = "alternate"
    connector["definition"]["operations"].append(alternate)
    root["connectors"][0]["ref"]["sha256"] = api.catalog_document_digest(connector)
    original, review, dispatch, verify = root["body"]["steps"]
    branches = []
    for name, operation in (("left", "write"), ("right", "alternate")):
        intent = deepcopy(original)
        intent.update(id=name, operation=operation)
        branches.append(
            {
                "steps": [intent],
                "result": {"kind": "ref", "source": {"kind": "step", "id": name}, "pointer": ""},
                "result_schema": {"kind": "handle", "handle_kind": "action_intent"},
            }
        )
    choice = {
        "kind": "decision",
        "id": "choose",
        "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
        "then_body": branches[0],
        "else_body": branches[1],
    }
    review["intent"]["source"]["id"] = "choose"
    dispatch["intent"]["source"]["id"] = "choose"
    root["body"]["steps"] = [choice, review, dispatch, verify]
    support_catalog(profile, documents)
    catalog = {"documents": documents}
    plan = api.compile_package(root, dependencies=[], catalog=catalog)
    assert next(step for step in plan.steps if step.kind == "action_dispatch").operation is None
    assert assess(root, profile, requirements, catalog=catalog).compatible
    profile["operations"][0]["operations"].remove("alternate")
    report = assess(root, profile, requirements, catalog=catalog)
    assert not report.compatible and any(r.code == "unsupported_operation" for r in report.reasons)


@pytest.mark.parametrize("family", ["artifacts", "operations", "model_configurations", "platforms"])
def test_profile_duplicate_natural_keys(case, catalog_case, profile, requirements, family):
    root, catalog = declared_catalog_package(case, catalog_case, profile)
    profile[family].append(deepcopy(profile[family][0]))
    with pytest.raises(api.ExecutableError) as error:
        assess(root, profile, requirements, catalog=catalog)
    assert error.value.code == "invalid_profile"


def test_profile_cross_list_pin_conflict(case, catalog_case, profile, requirements):
    root, catalog = declared_catalog_package(case, catalog_case, profile)
    profile["operations"][0]["connector_ref"]["sha256"] = "0" * 64
    with pytest.raises(api.ExecutableError) as error:
        assess(root, profile, requirements, catalog=catalog)
    assert error.value.code == "invalid_profile"


def test_distinct_explicit_versions_and_exact_digest(case, profile, requirements):
    root, child = case("subworkflow"), case("minimal")
    pin = deepcopy(root["subworkflows"][0]["ref"])
    old = deepcopy(pin)
    old["version"] = "0.1.0"
    profile["artifacts"] = [old, pin]
    assert assess(root, profile, requirements, dependencies=[child]).compatible
    profile["artifacts"][1]["sha256"] = "0" * 64
    report = assess(root, profile, requirements, dependencies=[child])
    assert any(r.code == "unsupported_artifact" for r in report.reasons)


@pytest.mark.parametrize("size,valid", [(64, True), (65, False)])
@pytest.mark.parametrize("field", ["artifacts", "operations", "model_configurations", "platforms"])
def test_support_record_list_bounds(case, profile, field, size, valid):
    refs = [
        {"kind": "connector", "name": f"entry-{i}", "version": "1.0.0", "sha256": "0" * 64}
        for i in range(size)
    ]
    if field == "artifacts":
        entries = refs
    elif field == "operations":
        entries = [{"connector_ref": ref, "operations": []} for ref in refs]
    elif field == "model_configurations":
        entries = [
            {
                "model_ref": {**ref, "kind": "model"},
                "configuration_revision": "1",
                "configuration_sha256": "0" * 64,
            }
            for ref in refs
        ]
    else:
        entries = [
            {"os": f"os-{i}", "architecture": "amd64", "capabilities": []} for i in range(size)
        ]
    profile[field] = entries
    if valid:
        api.validate_executable_capability_profile(profile)
    else:
        with pytest.raises(api.ExecutableError) as error:
            api.validate_executable_capability_profile(profile)
        assert error.value.code == "invalid_profile"


@pytest.mark.parametrize("size,valid", [(32, True), (33, False)])
def test_operation_list_bound(profile, size, valid):
    profile["operations"] = [
        {
            "connector_ref": {
                "kind": "connector",
                "name": "declared",
                "version": "1.0.0",
                "sha256": "0" * 64,
            },
            "operations": [f"operation-{i}" for i in range(size)],
        }
    ]
    if valid:
        api.validate_executable_capability_profile(profile)
    else:
        with pytest.raises(api.ExecutableError):
            api.validate_executable_capability_profile(profile)


def test_incomplete_structural_validity_is_not_a_type_claim(case, profile, requirements):
    root = case("subworkflow")
    root["body"]["result"] = {"kind": "literal", "schema": {"kind": "boolean"}, "value": True}
    report = assess(root, profile, requirements)
    assert report.contract_valid and not report.closure_complete
    assert report.compilation_status == "unresolved"
    with pytest.raises(api.ExecutableError) as error:
        assess(root, profile, requirements, dependencies=[case("minimal")])
    assert error.value.code == "type_mismatch"


def test_missing_does_not_hide_cycle(case, profile, requirements):
    root = case("subworkflow")
    root["subworkflows"].append(
        {
            "id": "self",
            "ref": {
                "kind": "workflow",
                "name": root["package_id"],
                "version": root["version"],
                "sha256": "0" * 64,
            },
        }
    )
    with pytest.raises(api.ExecutableError) as error:
        assess(root, profile, requirements)
    assert error.value.code == "dependency_cycle"


def test_complete_type_invalid_sources_refuse(case, profile, requirements):
    with pytest.raises(api.ExecutableError) as error:
        assess(case("unjoined_branch_reference"), profile, requirements)
    assert error.value.code == "unavailable_value"


def test_implicit_parallel_object_requires_value_support(case, profile, requirements):
    profile["value_kinds"].remove("object")
    report = assess(case("parallel"), profile, requirements)
    assert not report.compatible
    assert any(reason.code == "unsupported_value" for reason in report.reasons)


@pytest.mark.parametrize("size,valid", [(64, True), (65, False)])
def test_requirement_and_platform_capability_bounds(profile, requirements, size, valid):
    capabilities = [f"capability-{index}" for index in range(size)]
    requirements["required_capabilities"] = capabilities
    profile["platforms"][0]["capabilities"] = capabilities
    if valid:
        api.validate_executable_requirements(requirements)
        api.validate_executable_capability_profile(profile)
    else:
        with pytest.raises(api.ExecutableError):
            api.validate_executable_requirements(requirements)
        with pytest.raises(api.ExecutableError):
            api.validate_executable_capability_profile(profile)


@pytest.mark.parametrize("value", [True, 1.0, -1, 10001])
def test_capacity_exact_integer_bounds(profile, value):
    profile["supported_limits"]["max_steps"] = value
    with pytest.raises(api.ExecutableError) as error:
        api.validate_executable_capability_profile(profile)
    assert error.value.code == "invalid_profile"


@pytest.mark.parametrize(
    "change",
    [
        "success_with_reasons",
        "unresolved_success",
        "private_reason",
        "forged_authority",
        "unchecked_status",
        "truncation_without_full_reasons",
    ],
)
def test_constructed_reports_cannot_forge_truth(case, profile, requirements, change):
    report = assess(case("minimal"), profile, requirements)
    data = json.loads(api.executable_compatibility_report_json(report))
    if change == "success_with_reasons":
        data["reasons"] = [{"code": "unsupported_step", "path": "/body/steps/0"}]
    elif change == "unresolved_success":
        data.update(closure_complete=False, compilation_status="unresolved")
    elif change == "private_reason":
        data.update(
            compatible=False,
            reasons=[{"code": "unsupported_step", "path": "/private-customer-name"}],
        )
    elif change == "forged_authority":
        data["authority_granted"] = 0
    elif change == "unchecked_status":
        data["provider_availability"] = "verified"
    else:
        data.update(compatible=False, reasons_truncated=True)
    forged = api.ExecutableCompatibilityReport.model_construct(**data)
    with pytest.raises(api.ExecutableError):
        api.executable_compatibility_report_json(forged)


def test_normalized_identity_is_order_sensitive_but_key_order_independent(
    case, profile, requirements
):
    first = assess(case("minimal"), profile, requirements)
    reordered = dict(reversed(list(profile.items())))
    assert assess(case("minimal"), reordered, requirements).profile_sha256 == first.profile_sha256
    profile["step_kinds"].reverse()
    second = assess(case("minimal"), profile, requirements)
    assert second.compatible and second.profile_sha256 != first.profile_sha256
    assert second.package_sha256 == first.package_sha256
    requirements["required_capabilities"] = ["extra"]
    third = assess(case("minimal"), profile, requirements)
    assert third.requirements_sha256 != second.requirements_sha256
    assert third.package_sha256 == second.package_sha256


def test_no_v1_profile_coercion(case, profile, requirements):
    profile["format_version"] = "lotuspond.workflow-capabilities.v1"
    with pytest.raises(api.ExecutableError) as error:
        assess(case("minimal"), profile, requirements)
    assert error.value.code == "invalid_profile"


def test_catalog_read_tool_operation_is_required(case, catalog_case, profile, requirements):
    documents = [d for d in catalog_case()["documents"] if d["kind"] in ("connector", "model")]
    connector, model = documents
    pin = {key: connector[key] for key in ("kind", "name", "version")}
    pin["sha256"] = api.catalog_document_digest(connector)
    retry = case("typed_agent")["body"]["steps"][0]["retry"]
    model["definition"]["read_tools"] = [
        {"connector_ref": pin, "operation": "read", "retry": retry}
    ]
    support_catalog(profile, documents)
    root = case("minimal")
    root["models"] = [{"id": "unused", "ref": deepcopy(profile["artifacts"][1])}]
    assert assess(root, profile, requirements, catalog={"documents": documents}).compatible
    profile["operations"][0]["operations"].remove("read")
    report = assess(root, profile, requirements, catalog={"documents": documents})
    assert any(r.code == "unsupported_operation" for r in report.reasons)


def test_dependency_order_does_not_change_report(case, profile, requirements):
    root = case("minimal")
    children = [case("minimal"), case("decision")]
    root["package_id"] = "parent"
    for index, child in enumerate(children):
        pin = {
            "kind": "workflow",
            "name": child["package_id"],
            "version": child["version"],
            "sha256": api.executable_digest(child),
        }
        root["subworkflows"].append({"id": f"child-{index}", "ref": pin})
        profile["artifacts"].append(pin)
    profile["step_kinds"] = []
    first = assess(root, profile, requirements, dependencies=children)
    second = assess(root, profile, requirements, dependencies=list(reversed(children)))
    assert first == second
    assert not first.compatible


def test_committed_synthetic_examples(case, catalog_case):
    root = case("reviewed_action")
    catalog = {
        "documents": [
            d for d in catalog_case()["documents"] if d["kind"] in ("connector", "policy")
        ]
    }
    report = assess(root, case("capability-profile"), case("requirements"), catalog=catalog)
    assert report.compatible and report.assurance == "synthetic-declaration"


@pytest.mark.parametrize("kind", ["boolean", "integer"])
@pytest.mark.parametrize("fn", ["eq", "ne", "lt", "le", "gt", "ge", "exists", "and", "or", "not"])
def test_implicit_guard_and_iteration_index_value_requirements(
    case, profile, requirements, kind, fn
):
    root = case("iteration")
    loop = root["body"]["steps"][0]
    loop["input"] = {
        "kind": "literal",
        "schema": {"kind": "array", "items": {"kind": "null"}, "max_items": 1},
        "value": [None],
    }
    index = {"kind": "ref", "source": {"kind": "iteration_index"}, "pointer": ""}
    exists = {"kind": "call", "fn": "exists", "args": [index]}
    if fn == "exists":
        condition = exists
    elif fn in ("and", "or", "not"):
        condition = {
            "kind": "call",
            "fn": fn,
            "args": [deepcopy(exists)] * (1 if fn == "not" else 2),
        }
    else:
        condition = {"kind": "call", "fn": fn, "args": [deepcopy(index), deepcopy(index)]}
    null_body = deepcopy(case("minimal")["body"])
    loop["body"]["steps"] = [
        {
            "kind": "decision",
            "id": "guard",
            "condition": condition,
            "then_body": null_body,
            "else_body": deepcopy(null_body),
        }
    ]
    assert assess(root, profile, requirements).compatible
    profile["value_kinds"].remove(kind)
    report = assess(root, profile, requirements)
    assert not report.compatible
    assert any(r.code == "unsupported_value" for r in report.reasons)


def compensation_case(case, catalog_case, profile, *, approval, nested):
    """Automatic main action: only compensation may require a review handle."""
    child = case("reviewed_action")
    catalog = {
        "documents": [
            d for d in catalog_case()["documents"] if d["kind"] in ("connector", "policy")
        ]
    }
    intent, review, dispatch, verify = child["body"]["steps"]
    dispatch["compensation"] = {
        **{k: deepcopy(v) for k, v in intent.items() if k not in ("id", "kind")},
        "retry": deepcopy(dispatch["retry"]),
        "verification": {
            k: deepcopy(v) for k, v in verify.items() if k not in ("id", "kind", "receipt")
        },
    }
    automatic = deepcopy(catalog["documents"][1])
    automatic["name"] = "automatic-policy"
    automatic["definition"].update(approval="none", independent_review=False)
    catalog["documents"].append(automatic)
    support_catalog(profile, catalog["documents"])
    child["policies"].append({"id": "automatic", "ref": deepcopy(profile["artifacts"][-1])})
    intent.update(policy_ref="automatic", approval="none", independent_review=False)
    dispatch["decision"] = None
    child["body"]["steps"].remove(review)
    if approval == "none":
        dispatch["compensation"].update(
            policy_ref="automatic", approval="none", independent_review=False
        )
    if not nested:
        return child, [], catalog, "/body/steps/1/*"
    root = case("subworkflow")
    pin = {
        "kind": "workflow",
        "name": child["package_id"],
        "version": child["version"],
        "sha256": api.executable_digest(child),
    }
    root["subworkflows"][0]["ref"] = pin
    profile["artifacts"].append(deepcopy(pin))
    return root, [child], catalog, "/dependencies/0/body/steps/1/*"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "approval,review_support,compatible",
    [("human", True, True), ("human", False, False), ("none", True, True), ("none", False, True)],
)
def test_compensation_only_human_review_support(
    case, catalog_case, profile, requirements, nested, approval, review_support, compatible
):
    root, dependencies, catalog, compensation_path = compensation_case(
        case, catalog_case, profile, approval=approval, nested=nested
    )
    plan = api.compile_package(root, dependencies=dependencies, catalog=catalog)
    # No explicit main-path review or exported decision can mask this requirement.
    assert all(step.kind != "human_task" for step in plan.steps)
    assert len(plan.outcome_obligations) == 2
    if not review_support:
        profile["value_kinds"].remove("human_decision")
    report = assess(root, profile, requirements, dependencies=dependencies, catalog=catalog)
    assert report.closure_complete and report.compilation_status == "valid"
    assert report.compatible is compatible
    assert report.authority_granted is False
    if compatible:
        assert report.reasons == []
    else:
        assert [(reason.code, reason.path) for reason in report.reasons] == [
            ("unsupported_value", compensation_path)
        ]


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "handle_kind", ["action_intent", "action_receipt", "verification_evidence"]
)
def test_compensation_originating_handles_have_their_own_requirements(
    case, catalog_case, profile, requirements, nested, handle_kind
):
    root, dependencies, catalog, compensation_path = compensation_case(
        case, catalog_case, profile, approval="none", nested=nested
    )
    profile["value_kinds"].remove(handle_kind)
    report = assess(root, profile, requirements, dependencies=dependencies, catalog=catalog)
    assert not report.compatible
    # Main-path origins also need these handles; independently pin compensation's requirement.
    assert ("unsupported_value", compensation_path) in [
        (reason.code, reason.path) for reason in report.reasons
    ]
