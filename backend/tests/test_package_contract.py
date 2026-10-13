"""Behavioral checks for the declarative package admission boundary."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import unittest
from pathlib import Path

from pydantic import ValidationError


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
            importlib.util.find_spec("lotuspond.package_contract"),
            "The package admission module must exist.",
        )
        return importlib.import_module("lotuspond.package_contract")

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
        with self.assertRaises(ValidationError):
            contract.validate_package(package)

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
        root = Path(__file__).resolve().parents[2]
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
