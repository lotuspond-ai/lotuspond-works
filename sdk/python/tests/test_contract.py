"""Behavioral checks for the declarative package admission boundary."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import unittest
from pathlib import Path

import pytest
from pydantic import ValidationError

from lotuspond_sdk import canonical_package_json, package_digest, validate_package
from lotuspond_sdk.contract import MAX_JSON_ITEMS, MAX_PACKAGE_BYTES, check_json_bounds


def reviewed_package() -> dict:
    return {
        "schema_version": "lotuspond.workflow-package.v1",
        "package_id": "reviewed-action",
        "version": "1.0.0",
        "name": "Reviewed action example",
        "entry_node": "start",
        "models": [{"id": "reasoner", "profile": "primary-model", "revision": "2026-10-13"}],
        "connectors": [
            {
                "id": "case-system",
                "connector": "synthetic-case-system",
                "version": "1.0.0",
                "secret_ref": "case-credential",
                "operations": ["read-case", "write-case"],
            }
        ],
        "secret_refs": [{"id": "case-credential"}],
        "nodes": [
            {"id": "start", "kind": "start"},
            {
                "id": "read",
                "kind": "connector",
                "connector_ref": "case-system",
                "operation": "read-case",
            },
            {
                "id": "propose",
                "kind": "agent",
                "model_ref": "reasoner",
                "instructions": "Propose a case update for a person to review.",
                "limits": {
                    "max_steps": 5,
                    "timeout_seconds": 60,
                    "max_input_tokens": 4000,
                    "max_output_tokens": 1000,
                    "max_cost_microusd": 100000,
                },
            },
            {"id": "review", "kind": "human_review"},
            {
                "id": "write",
                "kind": "connector",
                "connector_ref": "case-system",
                "operation": "write-case",
            },
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"source": "start", "target": "read"},
            {"source": "read", "target": "propose"},
            {"source": "propose", "target": "review"},
            {"source": "review", "target": "write"},
            {"source": "write", "target": "end"},
        ],
    }


class PackageContractTests(unittest.TestCase):
    def contract(self):
        self.assertIsNotNone(
            importlib.util.find_spec("lotuspond_sdk.contract"),
            "The package admission module must exist.",
        )
        return importlib.import_module("lotuspond_sdk.contract")

    def test_valid_package_returns_typed_model_without_mutating_input(self):
        raw = reviewed_package()
        original = copy.deepcopy(raw)
        contract = self.contract()
        package = contract.validate_package(raw)
        self.assertIsInstance(package, contract.WorkflowPackage)
        self.assertEqual(package.nodes[2].limits.max_steps, 5)
        self.assertEqual(raw, original)

    def test_credentials_and_unknown_fields_are_rejected_at_every_boundary(self):
        for target in ("package", "connector", "model", "secret", "node", "limits", "edge"):
            with self.subTest(target=target):
                raw = reviewed_package()
                location = {
                    "package": raw,
                    "connector": raw["connectors"][0],
                    "model": raw["models"][0],
                    "secret": raw["secret_refs"][0],
                    "node": raw["nodes"][2],
                    "limits": raw["nodes"][2]["limits"],
                    "edge": raw["edges"][0],
                }[target]
                location["api_key"] = "must-never-be-an-embedded-credential"
                with self.assertRaises(ValidationError):
                    self.contract().validate_package(raw)

    def test_agent_limits_are_required_strict_positive_and_bounded(self):
        for field in reviewed_package()["nodes"][2]["limits"]:
            for value in (None, 0, -1, True, "5", 10**12):
                with self.subTest(field=field, value=value):
                    raw = reviewed_package()
                    if value is None:
                        del raw["nodes"][2]["limits"][field]
                    else:
                        raw["nodes"][2]["limits"][field] = value
                    with self.assertRaises(ValidationError):
                        self.contract().validate_package(raw)

    def test_unsupported_schema_node_kind_and_version_are_rejected(self):
        for change in ("schema", "kind", "version"):
            with self.subTest(change=change):
                raw = reviewed_package()
                if change == "schema":
                    raw["schema_version"] = "lotuspond.workflow-package.v99"
                elif change == "kind":
                    raw["nodes"][2]["kind"] = "python-exec"
                else:
                    raw["version"] = "latest"
                with self.assertRaises(ValidationError):
                    self.contract().validate_package(raw)

    def test_declared_ids_must_be_unique(self):
        for collection in ("models", "connectors", "secret_refs", "nodes"):
            with self.subTest(collection=collection):
                raw = reviewed_package()
                raw[collection].append(copy.deepcopy(raw[collection][0]))
                with self.assertRaises(ValidationError):
                    self.contract().validate_package(raw)

    def test_all_references_and_operations_must_be_declared(self):
        for target in ("model", "connector", "secret", "operation", "edge", "entry"):
            with self.subTest(target=target):
                raw = reviewed_package()
                location, field = {
                    "model": (raw["nodes"][2], "model_ref"),
                    "connector": (raw["nodes"][1], "connector_ref"),
                    "secret": (raw["connectors"][0], "secret_ref"),
                    "operation": (raw["nodes"][1], "operation"),
                    "edge": (raw["edges"][0], "target"),
                    "entry": (raw, "entry_node"),
                }[target]
                location[field] = "undeclared"
                with self.assertRaises(ValidationError):
                    self.contract().validate_package(raw)

    def test_graph_rejects_cycles_unreachable_nodes_and_nonterminal_dead_ends(self):
        for defect in ("cycle", "unreachable", "dead-end", "end-outgoing", "entry-incoming"):
            with self.subTest(defect=defect):
                raw = reviewed_package()
                if defect == "cycle":
                    raw["edges"].append({"source": "write", "target": "propose"})
                elif defect == "unreachable":
                    raw["nodes"].append({"id": "orphan", "kind": "end"})
                elif defect == "dead-end":
                    raw["edges"] = raw["edges"][:-1]
                elif defect == "end-outgoing":
                    raw["edges"].append({"source": "end", "target": "read"})
                else:
                    raw["edges"].append({"source": "write", "target": "start"})
                with self.assertRaises(ValidationError):
                    self.contract().validate_package(raw)

    def test_graph_requires_one_start_matching_entry_and_an_end(self):
        for defect in ("wrong-entry", "second-start", "no-end"):
            with self.subTest(defect=defect):
                raw = reviewed_package()
                if defect == "wrong-entry":
                    raw["entry_node"] = "read"
                elif defect == "second-start":
                    raw["nodes"][1] = {"id": "read", "kind": "start"}
                else:
                    raw["nodes"][-1] = {"id": "end", "kind": "human_review"}
                with self.assertRaises(ValidationError):
                    self.contract().validate_package(raw)

    def test_duplicate_edges_and_connector_operations_are_rejected(self):
        for defect in ("edge", "operation"):
            with self.subTest(defect=defect):
                raw = reviewed_package()
                if defect == "edge":
                    raw["edges"].append(copy.deepcopy(raw["edges"][0]))
                else:
                    raw["connectors"][0]["operations"].append("read-case")
                with self.assertRaises(ValidationError):
                    self.contract().validate_package(raw)

    def test_reference_only_package_without_agents_is_supported(self):
        raw = reviewed_package()
        raw["models"] = []
        raw["nodes"][2] = {"id": "propose", "kind": "human_review"}
        self.assertEqual(len(self.contract().validate_package(raw).models), 0)

    def test_direct_model_validation_applies_graph_invariants(self):
        raw = reviewed_package()
        raw["edges"].append({"source": "write", "target": "read"})
        with self.assertRaises(ValidationError):
            self.contract().WorkflowPackage.model_validate(raw)

    def test_existing_models_cannot_bypass_strict_validation_after_mutation(self):
        contract = self.contract()
        package = contract.validate_package(reviewed_package())
        package.nodes[2].limits.max_steps = 0
        with self.assertRaises(ValidationError) as caught:
            contract.validate_package(package)
        self.assertIn("greater_than_equal", {item["type"] for item in caught.exception.errors()})

    def test_content_digest_ignores_object_key_order_but_detects_content_changes(self):
        contract = self.contract()
        raw = reviewed_package()
        reordered = json.loads(json.dumps(raw, sort_keys=True))
        first = contract.package_digest(contract.validate_package(raw))
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(first, contract.package_digest(contract.validate_package(reordered)))
        raw["nodes"][2]["instructions"] = "Propose a different update."
        self.assertNotEqual(first, contract.package_digest(contract.validate_package(raw)))

    def test_digest_rejects_mutated_or_validation_bypassed_models(self):
        contract = self.contract()
        for defect in ("reference", "cycle", "non-json-type", "constructed"):
            with self.subTest(defect=defect):
                package = contract.validate_package(reviewed_package())
                if defect == "reference":
                    package.nodes[2].model_ref = "undeclared"
                elif defect == "cycle":
                    package.edges.append(contract.Edge(source="write", target="read"))
                elif defect == "non-json-type":
                    package.models[0].revision = Path("not-a-json-string")
                else:
                    raw = reviewed_package()
                    raw["nodes"] = []
                    package = contract.WorkflowPackage.model_construct(**raw)
                with self.assertRaises(ValueError):
                    contract.package_digest(package)

    def test_deep_oversized_and_nonfinite_programmatic_inputs_are_rejected(self):
        contract = self.contract()
        for defect in ("depth", "bytes", "nonfinite", "cycle"):
            with self.subTest(defect=defect):
                raw = reviewed_package()
                if defect == "depth":
                    nested = []
                    for _ in range(40):
                        nested = [nested]
                    raw["extra"] = nested
                elif defect == "bytes":
                    raw["name"] = "x" * (contract.MAX_PACKAGE_BYTES + 1)
                elif defect == "nonfinite":
                    raw["nodes"][2]["limits"]["max_steps"] = float("nan")
                else:
                    raw["extra"] = raw
                with self.assertRaises(ValueError):
                    contract.validate_package(raw)

    def test_exported_schema_matches_model_and_example_is_valid(self):
        root = Path(__file__).resolve().parents[3]
        schema_path = root / "contracts/workflow-package.v1.schema.json"
        self.assertTrue(schema_path.is_file(), "The reviewable JSON schema must be generated.")
        schema = self.contract().WorkflowPackage.model_json_schema()
        self.assertEqual(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema.get("$id"), "urn:lotuspond:workflow-package:v1")
        self.assertEqual(json.loads(schema_path.read_text()), schema)
        example = json.loads((root / "contracts/examples/reviewed-action.v1.json").read_text())
        self.assertEqual(self.contract().validate_package(example).package_id, "reviewed-action")


if __name__ == "__main__":
    unittest.main()


def boundary_package():
    from lotuspond_sdk.contract import MAX_PACKAGE_BYTES

    raw = {
        "schema_version": "lotuspond.workflow-package.v1",
        "package_id": "boundary",
        "version": "1.0.0",
        "name": "Boundary fixture",
        "entry_node": "start",
        "models": [{"id": "reasoner", "profile": "primary-model", "revision": "v1"}],
        "connectors": [
            {
                "id": f"connector-{i}",
                "connector": "synthetic",
                "version": "1.0.0",
                "operations": ["read"],
            }
            for i in range(32)
        ],
        "secret_refs": [],
        "nodes": [{"id": "start", "kind": "start"}]
        + [
            {
                "id": f"agent-{i}",
                "kind": "agent",
                "model_ref": "reasoner",
                "instructions": "x",
                "limits": {
                    "max_steps": 1,
                    "timeout_seconds": 1,
                    "max_input_tokens": 1,
                    "max_output_tokens": 1,
                    "max_cost_microusd": 1,
                },
            }
            for i in range(64)
        ]
        + [{"id": "end", "kind": "end"}],
    }
    raw["edges"] = [
        {"source": left["id"], "target": right["id"]}
        for left, right in zip(raw["nodes"], raw["nodes"][1:])
    ]
    remaining = MAX_PACKAGE_BYTES - len(
        json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
    )
    for node in raw["nodes"][1:-1]:
        added = min(remaining, 16383)
        node["instructions"] += "x" * added
        remaining -= added
    assert remaining == 0
    return raw


def test_valid_instances_revalidate_without_mutating_or_trusting_them():
    from lotuspond_sdk.contract import WorkflowPackage, package_digest, validate_package

    package = validate_package(reviewed_package())
    for validate in (validate_package, WorkflowPackage.model_validate):
        again = validate(package)
        assert again is not package
        assert again.model_dump() == package.model_dump()
        assert package_digest(again) == package_digest(package)


def test_normalized_size_rejected_at_admission_not_later_digest():
    from lotuspond_sdk.contract import MAX_PACKAGE_BYTES, validate_package

    raw = boundary_package()
    assert (
        len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode())
        == MAX_PACKAGE_BYTES
    )
    try:
        validate_package(raw)
    except ValidationError as error:
        assert error.errors(include_input=False)[0]["type"] == "input_too_large"
    else:
        raise AssertionError("Admission must reject normalized content beyond the byte bound")


def test_instance_revalidation_rejects_extras_and_arbitrary_values_without_running_them():
    from lotuspond_sdk.contract import validate_package

    class Unsafe:
        def __str__(self):
            raise AssertionError("Untrusted string conversion executed")

        def __iter__(self):
            raise AssertionError("Untrusted iteration executed")

    for defect in ("unknown-field", "extra-state", "arbitrary-node", "nested-extra"):
        package = validate_package(reviewed_package())
        if defect == "unknown-field":
            package.__dict__["unknown"] = "private"
        elif defect == "extra-state":
            package.__pydantic_extra__ = {"unknown": "private"}
        elif defect == "nested-extra":
            package.nodes[2].__dict__["unknown"] = "private"
        else:
            package.nodes[0] = Unsafe()
        try:
            validate_package(package)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"Mutated model accepted: {defect}")


def test_omitted_and_null_defaults_at_normalized_byte_boundary_digest_identically():
    from lotuspond_sdk.contract import (
        MAX_PACKAGE_BYTES,
        canonical_package_json,
        package_digest,
        validate_package,
    )

    raw = boundary_package()
    raw["nodes"][1]["instructions"] = raw["nodes"][1]["instructions"][:-576]
    explicit = copy.deepcopy(raw)
    for connector in explicit["connectors"]:
        connector["secret_ref"] = None
    assert (
        len(json.dumps(explicit, ensure_ascii=False, separators=(",", ":")).encode())
        == MAX_PACKAGE_BYTES
    )
    omitted_package = validate_package(raw)
    explicit_package = validate_package(explicit)
    assert len(canonical_package_json(omitted_package)) == MAX_PACKAGE_BYTES
    assert package_digest(omitted_package) == package_digest(explicit_package)
    assert package_digest(validate_package(omitted_package)) == package_digest(explicit_package)
    raw["nodes"][1]["instructions"] += "x"
    try:
        validate_package(raw)
    except ValidationError as error:
        assert error.errors(include_input=False)[0]["type"] == "input_too_large"
    else:
        raise AssertionError("Normalized limit plus one accepted")


def test_historical_contract_identity(example, golden_bytes):
    assert canonical_package_json(validate_package(example)) == golden_bytes
    assert (
        package_digest(validate_package(example))
        == "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"
    )


def test_typed_mutation_and_unicode_parity(example, golden_bytes):
    package = validate_package(example)
    package.nodes[2].limits.max_steps = True
    with pytest.raises(ValidationError):
        validate_package(package)
    example["name"] = "हिंदी café"
    assert canonical_package_json(validate_package(example)) == golden_bytes.replace(
        b"Synthetic reviewed action example", "हिंदी café".encode("utf-8")
    )


def test_non_ascii_instructions_and_normalized_default(example):
    example["nodes"][2]["instructions"] = "हिंदी café"
    del example["connectors"][0]["secret_ref"]
    package = validate_package(example)
    content = canonical_package_json(package)
    assert "हिंदी café".encode() in content
    assert b'"secret_ref":null' in content
    assert canonical_package_json(validate_package(package)) == content


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
def test_json_item_budget_accepts_exact_boundary_and_rejects_one_more(nested):
    # Freeze the public limit rather than allowing a changed constant to move the test.
    assert MAX_JSON_ITEMS == 10_000
    # Count includes containers and every JSON value. Each nested list is smaller
    # than the budget, so that case demands a cumulative bound across branches.
    raw = [[None] * 4_998, [None] * 4_999] if nested else [None] * 9_999
    assert len(json.dumps(raw).encode()) < MAX_PACKAGE_BYTES
    check_json_bounds(raw)  # Exactly 10,000 JSON items, at depth at most two.
    (raw[0] if nested else raw).append(None)
    assert len(json.dumps(raw).encode()) < MAX_PACKAGE_BYTES
    with pytest.raises(ValueError) as raised:
        check_json_bounds(raw)
    assert raised.value.type == "input_too_complex"


@pytest.mark.parametrize("typed", [False, True], ids=["raw-package", "mutated-model"])
def test_excessive_package_items_fail_with_item_budget_error(example, typed):
    package = validate_package(example) if typed else example
    operations = ["read-case"] * 10_000
    # Both forms remain shallow and well below the byte bound. List-length or
    # duplicate-operation schema errors must not mask the item budget rejection.
    if typed:
        package.connectors[0].operations = operations
        raw = package.model_dump(mode="json")
    else:
        package["connectors"][0]["operations"] = operations
        raw = package
    assert len(json.dumps(raw).encode()) < MAX_PACKAGE_BYTES
    with pytest.raises(ValidationError) as raised:
        validate_package(package)
    assert {error["type"] for error in raised.value.errors(include_input=False)} == {
        "input_too_complex"
    }
