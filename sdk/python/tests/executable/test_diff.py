"""Literal authored-source diff expectations, independent of diff traversal."""

import copy
import json

import pytest

from lotuspond_sdk.executable import (
    ExecutableChange,
    ExecutableDiff,
    ExecutableError,
    diff_executable_packages,
    executable_diff_json,
    validate_executable_diff,
    validate_executable_package,
)


def timer(identifier, delay=1):
    return {"id": identifier, "kind": "timer", "delay_seconds": delay}


@pytest.fixture
def before_move(case):
    source = case("parallel")
    source["body"]["steps"][0]["branches"][0]["body"]["steps"] = [timer("wait")]
    return source


@pytest.fixture
def after_move_and_edit(before_move):
    source = copy.deepcopy(before_move)
    branches = source["body"]["steps"][0]["branches"]
    branches[0]["body"]["steps"] = []
    branches[1]["body"]["steps"] = [timer("wait", 2)]
    return source


@pytest.fixture
def expected_move_and_edit():
    return [
        (
            "control_flow",
            "moved",
            "step:wait",
            "/body/steps/0/branches/0/body/steps/0",
            "/body/steps/0/branches/1/body/steps/0",
        ),
        (
            "limits",
            "changed",
            "step:wait",
            "/body/steps/0/branches/0/body/steps/0/delay_seconds",
            "/body/steps/0/branches/1/body/steps/0/delay_seconds",
        ),
    ]


def changes(report):
    return [
        (c.category, c.operation, c.identity, c.before_path, c.after_path) for c in report.changes
    ]


def test_move_and_edit_remain_private(before_move, after_move_and_edit, expected_move_and_edit):
    report = diff_executable_packages(before_move, after_move_and_edit)
    assert changes(report) == expected_move_and_edit
    assert report.authority_granted is False
    assert report.behavioral_compatibility == "not_assessed"


def test_noop_normalized_and_input_not_mutated(case):
    raw = case("typed_agent")
    snapshot = copy.deepcopy(raw)
    normalized = validate_executable_package(raw)
    assert diff_executable_packages(raw, normalized).changes == []
    assert raw == snapshot


def test_add_remove_without_reorder_noise(case):
    before = case("minimal")
    before["body"]["steps"] = [timer("a"), timer("b")]
    after = copy.deepcopy(before)
    after["body"]["steps"] = [timer("c"), timer("b")]
    assert changes(diff_executable_packages(before, after)) == [
        ("control_flow", "removed", "step:a", "/body/steps/0", None),
        ("control_flow", "added", "step:c", None, "/body/steps/0"),
    ]


def test_reorder_plus_edit(case):
    before = case("minimal")
    before["body"]["steps"] = [timer("a"), timer("b")]
    after = copy.deepcopy(before)
    after["body"]["steps"] = [timer("b"), timer("a", 2)]
    assert changes(diff_executable_packages(before, after)) == [
        ("control_flow", "reordered", "body", "/body/steps", "/body/steps"),
        (
            "limits",
            "changed",
            "step:a",
            "/body/steps/0/delay_seconds",
            "/body/steps/1/delay_seconds",
        ),
    ]


def test_mapping_approval_outcome_budget_changes(case):
    before = case("reviewed_action")
    after = copy.deepcopy(before)
    after["body"]["steps"][0]["input"] = {
        "kind": "literal",
        "schema": {"kind": "string", "max_length": 100},
        "value": "PRIVATE_SENTINEL",
    }
    after["body"]["steps"][0]["independent_review"] = False
    after["body"]["steps"][2]["retry"]["max_attempts"] = 2
    after["body"]["steps"][3]["rules"][0]["readback_pointer"] = "/秘密~0~1"
    after["limits"]["max_cost_microusd"] = 1
    report = diff_executable_packages(before, after)
    assert changes(report) == [
        (
            "approval_policy",
            "changed",
            "step:intent",
            "/body/steps/0/independent_review",
            "/body/steps/0/independent_review",
        ),
        ("data_mapping", "changed", "step:intent", "/body/steps/0/input", "/body/steps/0/input"),
        ("limits", "changed", "package", "/limits/max_cost_microusd", "/limits/max_cost_microusd"),
        (
            "limits",
            "changed",
            "step:dispatch",
            "/body/steps/2/retry/max_attempts",
            "/body/steps/2/retry/max_attempts",
        ),
        ("outcome", "changed", "step:verify", "/body/steps/3/rules", "/body/steps/3/rules"),
    ]
    assert "PRIVATE_SENTINEL" not in report.model_dump_json()
    assert "秘密" not in report.model_dump_json()
    assert (
        "PRIVATE_SENTINEL"
        in diff_executable_packages(before, after, include_values=True).model_dump_json()
    )


def test_private_agent_input_and_instructions(case):
    before = case("typed_agent")
    after = copy.deepcopy(before)
    after["body"]["steps"][0]["instructions"] = "PRIVATE_SENTINEL"
    after["body"]["steps"][0]["input"] = {
        "kind": "literal",
        "schema": {"kind": "number"},
        "value": 1.0,
    }
    report = diff_executable_packages(before, after)
    assert changes(report) == [
        ("data_mapping", "changed", "step:think", "/body/steps/0/input", "/body/steps/0/input"),
        (
            "model_tools",
            "changed",
            "step:think",
            "/body/steps/0/instructions",
            "/body/steps/0/instructions",
        ),
    ]
    assert "PRIVATE_SENTINEL" not in executable_diff_json(report).decode()


def test_kind_replacement_and_children_independent(case):
    before = case("parallel")
    before["body"]["steps"][0]["branches"][0]["body"]["steps"] = [timer("child")]
    after = copy.deepcopy(before)
    after["body"]["steps"] = [timer("both")]
    assert changes(diff_executable_packages(before, after)) == [
        ("control_flow", "removed", "parallel:both:left", "/body/steps/0/branches/0/body", None),
        ("control_flow", "removed", "parallel:both:right", "/body/steps/0/branches/1/body", None),
        ("control_flow", "changed", "step:both", "/body/steps/0", "/body/steps/0"),
        ("control_flow", "removed", "step:child", "/body/steps/0/branches/0/body/steps/0", None),
    ]


def test_region_exports_guards_and_branch_order(case):
    before = case("parallel")
    after = copy.deepcopy(before)
    after["body"]["steps"][0]["branches"].reverse()
    after["body"]["steps"][0]["branches"][1]["body"]["result"] = {
        "kind": "literal",
        "schema": {"kind": "number"},
        "value": 1,
    }
    assert changes(diff_executable_packages(before, after)) == [
        (
            "control_flow",
            "reordered",
            "step:both",
            "/body/steps/0/branches",
            "/body/steps/0/branches",
        ),
        (
            "data_mapping",
            "changed",
            "parallel:both:left",
            "/body/steps/0/branches/0/body/result",
            "/body/steps/0/branches/1/body/result",
        ),
    ]


def test_declaration_aliases_pins_and_order(case):
    before = case("typed_agent")
    before["models"].append({**copy.deepcopy(before["models"][0]), "id": "alias"})
    after = copy.deepcopy(before)
    for decl in after["models"]:
        decl["ref"]["version"] = "2.0.0"
    after["models"].reverse()
    assert changes(diff_executable_packages(before, after)) == [
        ("model_tools", "reordered", "models", "/models", "/models"),
        (
            "model_tools",
            "changed",
            "models:alias",
            "/models/1/ref/version",
            "/models/0/ref/version",
        ),
        (
            "model_tools",
            "changed",
            "models:model",
            "/models/0/ref/version",
            "/models/1/ref/version",
        ),
    ]


def test_object_member_order_deterministic_arrays_significant(case):
    before = case("minimal")
    after = copy.deepcopy(before)
    after["environment"]["required_capabilities"] = ["a", "b"]
    reordered = json.loads(json.dumps(after, sort_keys=True))
    assert executable_diff_json(diff_executable_packages(before, after)) == executable_diff_json(
        diff_executable_packages(before, reordered)
    )
    swapped = copy.deepcopy(after)
    swapped["environment"]["required_capabilities"].reverse()
    assert changes(diff_executable_packages(after, swapped)) == [
        (
            "metadata",
            "changed",
            "package",
            "/environment/required_capabilities",
            "/environment/required_capabilities",
        ),
    ]


@pytest.mark.parametrize("flag", [0, 1, "true", None])
def test_include_values_requires_actual_bool(case, flag):
    with pytest.raises(TypeError):
        diff_executable_packages(case("minimal"), case("minimal"), include_values=flag)


@pytest.mark.parametrize(
    "field,value",
    [
        ("authority_granted", 0),
        ("authority_granted", True),
        ("include_values", 0),
        ("behavioral_compatibility", "compatible"),
    ],
)
def test_mutated_and_constructed_reports_revalidated(case, field, value):
    report = diff_executable_packages(case("minimal"), case("minimal"))
    setattr(report, field, value)
    with pytest.raises(ExecutableError):
        executable_diff_json(report)
    with pytest.raises(ExecutableError):
        report.model_dump_json()
    malformed = ExecutableDiff.model_construct(**report.__dict__)
    with pytest.raises(ExecutableError):
        validate_executable_diff(malformed)


def test_private_report_cannot_smuggle_values_or_invalid_paths(case):
    before = case("minimal")
    after = copy.deepcopy(before)
    after["name"] = "PRIVATE_SENTINEL"
    report = diff_executable_packages(before, after, include_values=True)
    report.include_values = False
    with pytest.raises(ExecutableError):
        executable_diff_json(report)
    report.include_values = True
    report.changes[0].operation = "added"
    with pytest.raises(ExecutableError):
        executable_diff_json(report)
    report.changes[0].operation = "changed"
    report.changes[0].before_path = "/PRIVATE_SENTINEL"
    with pytest.raises(ExecutableError):
        executable_diff_json(report)


def test_input_models_revalidated_without_arbitrary_serializers(case):
    source = validate_executable_package(case("minimal"))
    source.limits.max_steps = True
    with pytest.raises(ExecutableError):
        diff_executable_packages(source, case("minimal"))

    class Arbitrary:
        def model_dump(self, **kwargs):
            pytest.fail("serializer invoked")

    with pytest.raises(ExecutableError):
        diff_executable_packages(Arbitrary(), case("minimal"))


def test_report_ascii_budget_and_normalized_defaults(case):
    report = diff_executable_packages(case("minimal"), case("minimal"), include_values=True)
    huge = "𐀀" * 350000
    report.changes = [
        ExecutableChange.model_construct(
            category="metadata",
            operation="changed",
            identity="package",
            before_path="/name",
            after_path="/name",
            values={"before": huge, "after": huge},
        )
    ]
    # UTF-8/JCS is below 8 MiB; actual ASCII escapes exceed it.
    with pytest.raises(ExecutableError) as error:
        executable_diff_json(report)
    assert error.value.code == "report_too_large"
    with pytest.raises(ExecutableError):
        validate_executable_diff(report)


def test_moving_parent_preserves_child_region_identity(case):
    before = case("parallel")
    inner = copy.deepcopy(before["body"]["steps"][0])
    inner["id"] = "inner"
    inner["branches"][0]["body"]["steps"] = [timer("child")]
    branches = before["body"]["steps"][0]["branches"]
    branches[0]["body"]["steps"] = [inner]
    after = copy.deepcopy(before)
    branches = after["body"]["steps"][0]["branches"]
    branches[1]["body"]["steps"] = branches[0]["body"]["steps"]
    branches[0]["body"]["steps"] = []
    assert changes(diff_executable_packages(before, after)) == [
        (
            "control_flow",
            "moved",
            "step:inner",
            "/body/steps/0/branches/0/body/steps/0",
            "/body/steps/0/branches/1/body/steps/0",
        ),
    ]


def test_all_declaration_families_and_public_data_covered(case):
    before = case("minimal")
    for family, kind in [
        ("models", "model"),
        ("agents", "agent"),
        ("connectors", "connector"),
        ("knowledge", "knowledge"),
        ("subworkflows", "workflow"),
        ("evaluations", "evaluation"),
        ("policies", "policy"),
        ("artifacts", "artifact"),
    ]:
        decl = {
            "id": "pin",
            "ref": {"kind": kind, "name": "fixture", "version": "1.0.0", "sha256": "1" * 64},
        }
        if family == "knowledge":
            decl.update(
                required_freshness_seconds=1,
                allowed_classifications=[],
                citation_schema={"kind": "null"},
            )
        before[family] = [decl]
    before["schemas"] = [{"name": "data", "schema": {"kind": "string", "max_length": 2}}]
    after = copy.deepcopy(before)
    for family in [
        "models",
        "agents",
        "connectors",
        "knowledge",
        "subworkflows",
        "evaluations",
        "policies",
        "artifacts",
    ]:
        after[family][0]["ref"]["sha256"] = "2" * 64
    after["input_schema"] = {"kind": "string", "max_length": 3}
    after["output_schema"] = {"kind": "string", "max_length": 4}
    after["schemas"][0]["schema"]["max_length"] = 3
    after["body"]["result_schema"] = {"kind": "data", "schema": {"kind": "string", "max_length": 4}}
    after["secret_refs"] = ["credential"]
    assert changes(diff_executable_packages(before, after)) == [
        (
            "approval_policy",
            "changed",
            "policies:pin",
            "/policies/0/ref/sha256",
            "/policies/0/ref/sha256",
        ),
        (
            "connector_action",
            "changed",
            "connectors:pin",
            "/connectors/0/ref/sha256",
            "/connectors/0/ref/sha256",
        ),
        ("connector_action", "added", "secret_refs:credential", None, "/secret_refs/0"),
        (
            "control_flow",
            "changed",
            "subworkflows:pin",
            "/subworkflows/0/ref/sha256",
            "/subworkflows/0/ref/sha256",
        ),
        ("data_mapping", "changed", "body", "/body/result_schema", "/body/result_schema"),
        ("data_mapping", "changed", "package", "/input_schema", "/input_schema"),
        ("data_mapping", "changed", "package", "/output_schema", "/output_schema"),
        ("data_mapping", "changed", "schemas:data", "/schemas/0/schema", "/schemas/0/schema"),
        (
            "metadata",
            "changed",
            "artifacts:pin",
            "/artifacts/0/ref/sha256",
            "/artifacts/0/ref/sha256",
        ),
        ("model_tools", "changed", "agents:pin", "/agents/0/ref/sha256", "/agents/0/ref/sha256"),
        (
            "model_tools",
            "changed",
            "knowledge:pin",
            "/knowledge/0/ref/sha256",
            "/knowledge/0/ref/sha256",
        ),
        ("model_tools", "changed", "models:pin", "/models/0/ref/sha256", "/models/0/ref/sha256"),
        (
            "outcome",
            "changed",
            "evaluations:pin",
            "/evaluations/0/ref/sha256",
            "/evaluations/0/ref/sha256",
        ),
    ]


def test_compensation_verification_and_assurance_categories(case):
    before = case("reviewed_action")
    steps = before["body"]["steps"]
    verification = {
        key: copy.deepcopy(value)
        for key, value in steps[3].items()
        if key not in ("kind", "id", "receipt")
    }
    steps[2]["compensation"] = {
        **{
            key: copy.deepcopy(steps[0][key])
            for key in (
                "connector_ref",
                "operation",
                "target",
                "input",
                "policy_ref",
                "approval",
                "independent_review",
                "expiry_seconds",
            )
        },
        "retry": copy.deepcopy(steps[2]["retry"]),
        "verification": verification,
    }
    after = copy.deepcopy(before)
    after["body"]["steps"][2]["compensation"]["verification"]["rules"][0]["readback_pointer"] = (
        "/changed"
    )
    after["assurance"]["required_classification"] = "internal"
    assert changes(diff_executable_packages(before, after)) == [
        (
            "outcome",
            "changed",
            "package",
            "/assurance/required_classification",
            "/assurance/required_classification",
        ),
        (
            "outcome",
            "changed",
            "step:dispatch",
            "/body/steps/2/compensation/verification/rules",
            "/body/steps/2/compensation/verification/rules",
        ),
    ]


def test_decision_guard_and_iteration_controls(case):
    before = case("decision")
    after = copy.deepcopy(before)
    after["body"]["steps"][0]["condition"]["value"] = False
    assert changes(diff_executable_packages(before, after)) == [
        (
            "control_flow",
            "changed",
            "step:choose",
            "/body/steps/0/condition",
            "/body/steps/0/condition",
        ),
    ]
    before = case("iteration")
    after = copy.deepcopy(before)
    after["body"]["steps"][0]["max_items"] = 4
    assert changes(diff_executable_packages(before, after)) == [
        ("limits", "changed", "step:each", "/body/steps/0/max_items", "/body/steps/0/max_items"),
    ]


def test_report_projection_never_calls_model_serializers(case):
    report = diff_executable_packages(case("minimal"), case("minimal"))

    class HostileDiff(ExecutableDiff):
        def model_dump(self, **kwargs):
            pytest.fail("untrusted serializer called")

    hostile = HostileDiff.model_construct(**report.__dict__)
    with pytest.raises(ExecutableError):
        executable_diff_json(hostile)


def test_report_normalized_defaults_count_and_complete_ascii_output(case):
    report = diff_executable_packages(case("minimal"), case("minimal"))
    raw = {
        "before_sha256": report.before_sha256,
        "after_sha256": report.after_sha256,
        "changes": [],
    }
    encoded = executable_diff_json(raw)
    assert encoded.endswith(b"\n")
    assert json.loads(encoded) == {
        **raw,
        "include_values": False,
        "authority_granted": False,
        "behavioral_compatibility": "not_assessed",
    }
    assert validate_executable_diff(raw).model_dump() == json.loads(encoded)


def test_private_data_member_names_are_never_source_paths(case):
    before = case("minimal")
    schema = {
        "kind": "object",
        "fields": [
            {
                "name": "秘密/~PRIVATE_SENTINEL",
                "schema": {"kind": "integer", "min": None, "max": None},
                "required": True,
            }
        ],
    }
    before["body"]["result"] = {
        "kind": "literal",
        "schema": schema,
        "value": {"秘密/~PRIVATE_SENTINEL": 1},
    }
    after = copy.deepcopy(before)
    after["body"]["result"]["value"]["秘密/~PRIVATE_SENTINEL"] = 2
    report = diff_executable_packages(before, after)
    assert changes(report) == [
        ("data_mapping", "changed", "body", "/body/result", "/body/result"),
    ]
    assert "PRIVATE_SENTINEL" not in executable_diff_json(report).decode()


@pytest.mark.parametrize("include_values", [False, True])
@pytest.mark.parametrize("replacement", ["branch", "owner"])
def test_region_replacement_moves_child_at_same_pointer(case, replacement, include_values):
    before = case("parallel")
    branch = before["body"]["steps"][0]["branches"][0]
    branch["body"]["steps"] = [timer("child")]
    branch["body"]["result"] = {
        "kind": "literal",
        "schema": {"kind": "string", "max_length": 64},
        "value": "PRIVATE_SENTINEL",
    }
    branch["body"]["result_schema"] = {
        "kind": "data",
        "schema": {"kind": "string", "max_length": 64},
    }
    after = copy.deepcopy(before)
    if replacement == "branch":
        after["body"]["steps"][0]["branches"][0]["id"] = "renamed"
        expected = [
            (
                "control_flow",
                "removed",
                "parallel:both:left",
                "/body/steps/0/branches/0/body",
                None,
            ),
            (
                "control_flow",
                "added",
                "parallel:both:renamed",
                None,
                "/body/steps/0/branches/0/body",
            ),
            (
                "control_flow",
                "moved",
                "step:child",
                "/body/steps/0/branches/0/body/steps/0",
                "/body/steps/0/branches/0/body/steps/0",
            ),
        ]
        target_region = "parallel:both:renamed"
    else:
        after["body"]["steps"][0]["id"] = "other"
        expected = [
            (
                "control_flow",
                "removed",
                "parallel:both:left",
                "/body/steps/0/branches/0/body",
                None,
            ),
            (
                "control_flow",
                "removed",
                "parallel:both:right",
                "/body/steps/0/branches/1/body",
                None,
            ),
            ("control_flow", "added", "parallel:other:left", None, "/body/steps/0/branches/0/body"),
            (
                "control_flow",
                "added",
                "parallel:other:right",
                None,
                "/body/steps/0/branches/1/body",
            ),
            ("control_flow", "removed", "step:both", "/body/steps/0", None),
            (
                "control_flow",
                "moved",
                "step:child",
                "/body/steps/0/branches/0/body/steps/0",
                "/body/steps/0/branches/0/body/steps/0",
            ),
            ("control_flow", "added", "step:other", None, "/body/steps/0"),
        ]
        target_region = "parallel:other:left"
    validate_executable_package(before)
    validate_executable_package(after)
    report = diff_executable_packages(before, after, include_values=include_values)
    assert changes(report) == expected
    assert changes(validate_executable_diff(report)) == expected
    encoded = executable_diff_json(report)
    assert changes(validate_executable_diff(json.loads(encoded))) == expected
    assert report.model_dump_json() == encoded.decode("ascii").removesuffix("\n")
    moved = next(change for change in report.changes if change.operation == "moved")
    if include_values:
        assert moved.values.before == "parallel:both:left"
        assert moved.values.after == target_region
        assert "PRIVATE_SENTINEL" in encoded.decode("ascii")
    else:
        assert moved.values is None
        assert "PRIVATE_SENTINEL" not in encoded.decode("ascii")


@pytest.mark.parametrize(
    "field,value",
    [
        ("category", "limits"),
        ("identity", "package"),
        ("before_path", None),
        ("after_path", None),
        ("before_path", "/PRIVATE_SENTINEL"),
    ],
)
def test_moved_report_retains_identity_category_and_path_guards(
    before_move,
    after_move_and_edit,
    field,
    value,
):
    report = diff_executable_packages(before_move, after_move_and_edit)
    moved = next(change for change in report.changes if change.operation == "moved")
    setattr(moved, field, value)
    with pytest.raises(ExecutableError):
        executable_diff_json(report)
