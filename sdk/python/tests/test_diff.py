import copy
import json

import pytest
from pydantic import ValidationError

from lotuspond_sdk import PackageChange, PackageDiff, diff_packages
from lotuspond_sdk.contract import canonical_package_json, package_digest, validate_package
from lotuspond_sdk.errors import SdkError


def summary(report):
    return [
        (c.category, c.operation, c.declaration_id, c.before_path, c.after_path)
        for c in report.changes
    ]


def test_reorder_and_edit_are_independent_and_private(example):
    changed = copy.deepcopy(example)
    changed["nodes"][2]["instructions"] = "PRIVATE-INSTRUCTION-SENTINEL"
    changed["nodes"].reverse()
    originals = copy.deepcopy((example, changed))
    report = diff_packages(example, changed)
    assert summary(report) == [
        ("graph", "reordered", None, "/nodes", "/nodes"),
        ("instructions", "modified", "propose", "/nodes/2/instructions", "/nodes/3/instructions"),
    ]
    assert "PRIVATE-INSTRUCTION-SENTINEL" not in report.model_dump_json()
    assert "values" not in report.model_dump_json()
    detailed = diff_packages(example, changed, include_values=True)
    assert detailed.changes[1].values.after == "PRIVATE-INSTRUCTION-SENTINEL"
    assert "PRIVATE-INSTRUCTION-SENTINEL" in detailed.model_dump_json()
    assert report == diff_packages(example, changed)
    assert diff_packages(example, copy.deepcopy(example)).changes == ()
    assert report.authority_granted is False
    assert (example, changed) == originals


@pytest.mark.parametrize("field", ["package_id", "name", "version"])
def test_metadata_has_only_paths_by_default(example, field):
    changed = copy.deepcopy(example)
    changed[field] = {"package_id": "new-id", "name": "PRIVATE-NAME", "version": "2.0.0"}[field]
    report = diff_packages(example, changed)
    assert summary(report) == [("metadata", "modified", None, f"/{field}", f"/{field}")]
    assert changed[field] not in report.model_dump_json()


@pytest.mark.parametrize(
    "collection,index,field,value,category",
    [
        ("models", 0, "profile", "replacement-model", "model"),
        ("models", 0, "revision", "PRIVATE-REVISION", "model"),
        ("connectors", 0, "connector", "replacement-system", "connector"),
        ("connectors", 0, "version", "2.0.0", "connector"),
        ("connectors", 0, "secret_ref", None, "secret_reference"),
        ("nodes", 2, "instructions", "PRIVATE-INSTRUCTIONS", "instructions"),
        ("nodes", 4, "operation", "read-case", "connector"),
    ],
)
def test_field_categories(example, collection, index, field, value, category):
    changed = copy.deepcopy(example)
    changed[collection][index][field] = value
    declaration = example[collection][index]["id"]
    path = f"/{collection}/{index}/{field}"
    report = diff_packages(example, changed)
    assert summary(report) == [(category, "modified", declaration, path, path)]
    if isinstance(value, str):
        assert value not in report.model_dump_json()


def test_limits_and_node_references_have_specific_categories(example):
    changed = copy.deepcopy(example)
    changed["models"].append({"id": "other", "profile": "other", "revision": "v1"})
    changed["nodes"][2]["model_ref"] = "other"
    changed["nodes"][2]["limits"]["max_steps"] = 6
    other_connector = copy.deepcopy(changed["connectors"][0])
    other_connector["id"] = "other"
    changed["connectors"].append(other_connector)
    changed["nodes"][1]["connector_ref"] = "other"
    assert summary(diff_packages(example, changed)) == [
        ("connector", "added", "other", None, "/connectors/1"),
        ("connector", "modified", "read", "/nodes/1/connector_ref", "/nodes/1/connector_ref"),
        ("limits", "modified", "propose", "/nodes/2/limits/max_steps", "/nodes/2/limits/max_steps"),
        ("model", "added", "other", None, "/models/1"),
        ("model", "modified", "propose", "/nodes/2/model_ref", "/nodes/2/model_ref"),
    ]


@pytest.mark.parametrize(
    "collection,category",
    [("models", "model"), ("connectors", "connector"), ("secret_refs", "secret_reference")],
)
def test_declaration_add_remove_and_reorder_coexist(example, collection, category):
    before = copy.deepcopy(example)
    for identifier in ("second", "removed"):
        extra = copy.deepcopy(before[collection][0])
        extra["id"] = identifier
        before[collection].append(extra)
    after = copy.deepcopy(before)
    added = copy.deepcopy(after[collection][0])
    added["id"] = "added"
    after[collection] = [after[collection][1], added, after[collection][0]]
    assert summary(diff_packages(before, after)) == [
        (category, "reordered", None, f"/{collection}", f"/{collection}"),
        (category, "added", "added", None, f"/{collection}/1"),
        (category, "removed", "removed", f"/{collection}/2", None),
    ]
    assert diff_packages(before, after).before_sha256 != diff_packages(before, after).after_sha256


@pytest.mark.parametrize("collection", ["nodes", "edges"])
def test_graph_array_reorder(example, collection):
    changed = copy.deepcopy(example)
    changed[collection].reverse()
    report = diff_packages(example, changed)
    assert summary(report) == [("graph", "reordered", None, f"/{collection}", f"/{collection}")]
    assert report.before_sha256 != report.after_sha256


def test_operations_match_by_value_with_add_remove_and_reorder(example):
    example["connectors"][0]["operations"].append("removed")
    changed = copy.deepcopy(example)
    changed["connectors"][0]["operations"] = ["write-case", "added", "read-case"]
    assert summary(diff_packages(example, changed)) == [
        ("connector", "added", "case-system", None, "/connectors/0/operations/1"),
        (
            "connector",
            "reordered",
            "case-system",
            "/connectors/0/operations",
            "/connectors/0/operations",
        ),
        ("connector", "removed", "case-system", "/connectors/0/operations/2", None),
    ]


def test_insertion_alone_does_not_reorder_common_elements(example):
    changed = copy.deepcopy(example)
    changed["connectors"][0]["operations"].insert(0, "inserted")
    assert summary(diff_packages(example, changed)) == [
        ("connector", "added", "case-system", None, "/connectors/0/operations/0")
    ]


def test_node_kind_change_is_one_graph_replacement(example):
    changed = copy.deepcopy(example)
    changed["nodes"][2] = {"id": "propose", "kind": "human_review"}
    assert summary(diff_packages(example, changed)) == [
        ("graph", "modified", "propose", "/nodes/2", "/nodes/2")
    ]


def test_graph_add_remove_edges_match_by_pair(example):
    changed = copy.deepcopy(example)
    changed["nodes"][3]["id"] = "new-review"
    changed["edges"][2]["target"] = "new-review"
    changed["edges"][3]["source"] = "new-review"
    assert summary(diff_packages(example, changed)) == [
        ("graph", "added", None, None, "/edges/2"),
        ("graph", "added", None, None, "/edges/3"),
        ("graph", "removed", None, "/edges/2", None),
        ("graph", "removed", None, "/edges/3", None),
        ("graph", "added", "new-review", None, "/nodes/3"),
        ("graph", "removed", "review", "/nodes/3", None),
    ]


def test_defaults_key_order_and_typed_models_equal_without_mutation(example):
    before = copy.deepcopy(example)
    before["connectors"][0]["secret_ref"] = None
    after = json.loads(json.dumps(before, sort_keys=True))
    del after["connectors"][0]["secret_ref"]
    original = copy.deepcopy(after)
    report = diff_packages(validate_package(before), after)
    assert report.before_sha256 == report.after_sha256 == package_digest(validate_package(before))
    assert report.changes == ()
    assert after == original


@pytest.mark.parametrize("side", ["before", "after"])
def test_invalid_and_mutated_typed_inputs_rejected(example, side):
    invalid = validate_package(example)
    invalid.nodes[2].limits.max_steps = True
    with pytest.raises(ValidationError):
        diff_packages(
            invalid if side == "before" else example, invalid if side == "after" else example
        )
    invalid_raw = copy.deepcopy(example)
    invalid_raw["name"] = False
    with pytest.raises(ValidationError):
        diff_packages(
            invalid_raw if side == "before" else example,
            invalid_raw if side == "after" else example,
        )


@pytest.mark.parametrize("flag", ["false", "true", 1, 0, None, [], {}])
def test_detail_flag_is_an_explicit_boolean(example, flag):
    with pytest.raises(TypeError):
        diff_packages(example, example, include_values=flag)


def test_report_models_are_strict_forbid_extras_and_revalidate(example):
    report = diff_packages(example, example)
    for update in (
        {"authority_granted": True},
        {"authority_granted": 0},
        {"before_sha256": 5},
        {"unknown": "private"},
        {"changes": []},
    ):
        raw = report.model_dump() | update
        with pytest.raises(ValidationError):
            PackageDiff.model_validate(raw)
    change = PackageChange(
        category="graph",
        operation="modified",
        declaration_id=None,
        before_path="/nodes/0",
        after_path="/nodes/0",
    )
    change.operation = "invalid"
    with pytest.raises(ValidationError):
        PackageChange.model_validate(change)
    report.changes = (change,)
    with pytest.raises(ValidationError):
        report.model_dump_json()


def test_detail_is_ascii_safe_and_paths_resolve_to_normalized_values(example):
    changed = copy.deepcopy(example)
    changed["nodes"][2]["instructions"] = "हिंदी café 😀"
    changed["nodes"].reverse()
    report = diff_packages(example, changed, include_values=True)
    encoded = report.model_dump_json()
    assert encoded.isascii()
    assert json.loads(encoded)["changes"][1]["values"]["after"] == "हिंदी café 😀"
    docs = [validate_package(p).model_dump(mode="json") for p in (example, changed)]
    for change in report.changes:
        if change.operation == "modified":
            for doc, path, value in zip(
                docs,
                (change.before_path, change.after_path),
                (change.values.before, change.values.after),
            ):
                actual = doc
                for token in path.split("/")[1:]:
                    actual = actual[int(token)] if isinstance(actual, list) else actual[token]
                assert actual == value


def test_near_limit_document_remains_private_and_bounded():
    from test_contract import boundary_package

    before = boundary_package()
    before["nodes"][1]["instructions"] = before["nodes"][1]["instructions"][:-576]
    assert len(canonical_package_json(validate_package(before))) == 1_048_576
    after = copy.deepcopy(before)
    after["nodes"][1]["instructions"] = "y" + after["nodes"][1]["instructions"][1:]
    for include_values in (False, True):
        report = diff_packages(before, after, include_values=include_values)
        assert len(report.changes) == 1
        assert len(report.model_dump_json().encode("ascii")) < 8_388_608
    assert "x" * 100 not in diff_packages(before, after).model_dump_json()


@pytest.mark.parametrize("with_values", [False, True], ids=["redacted", "detail"])
def test_serialization_refuses_oversized_reports_instead_of_dropping_changes(with_values):
    change = PackageChange(
        category="instructions",
        operation="modified",
        declaration_id="agent",
        before_path="/nodes/1/instructions" if with_values else "/" + "a" * 8_388_608,
        after_path="/nodes/1/instructions",
        values={"before": "a" * 8_388_608, "after": "b"} if with_values else None,
    )
    report = PackageDiff(before_sha256="a" * 64, after_sha256="b" * 64, changes=(change,))
    for serialize in (report.model_dump, report.model_dump_json):
        with pytest.raises(SdkError) as raised:
            serialize()
        assert raised.value.code == "report_too_large"
    assert len(report.changes) == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_detail_values_must_be_json_finite(value):
    with pytest.raises(ValidationError):
        PackageChange(
            category="limits",
            operation="modified",
            declaration_id="agent",
            before_path="/nodes/1/limits",
            after_path="/nodes/1/limits",
            values={"before": {"nested": [value]}, "after": 1},
        )
