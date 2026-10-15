import copy

import pytest

from lotuspond_sdk.executable import ExecutableError, validate_catalog


def test_all_catalog_families(catalog_case):
    catalog = validate_catalog(catalog_case())
    assert {d.kind for d in catalog.documents} == {
        "connector",
        "model",
        "agent",
        "evaluation",
        "policy",
        "knowledge",
        "artifact",
    }
    assert catalog.documents[0].format_version == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "version_bool",
        "version_float",
        "unknown",
        "wrong_definition",
        "workflow",
        "conflict",
        "bad_rule",
        "bad_pin",
        "duplicate_operation",
        "missing_read",
        "bad_number",
    ],
)
def test_catalog_refusals(catalog_case, mutation):
    source = catalog_case()
    doc = source["documents"][0]
    if mutation == "version_bool":
        doc["format_version"] = True
    if mutation == "version_float":
        doc["format_version"] = 1.0
    if mutation == "unknown":
        doc["url"] = "https://example.test"
    if mutation == "wrong_definition":
        doc["definition"] = source["documents"][-1]["definition"]
    if mutation == "workflow":
        doc["kind"] = "workflow"
    if mutation == "conflict":
        source["documents"].append(copy.deepcopy(doc))
        source["documents"][-1]["definition"]["artifact_sha256"] = "c" * 64
    if mutation == "bad_rule":
        source["documents"][3]["definition"]["rules"][0]["rule"] = {"op": "model_assertion"}
    if mutation == "bad_pin":
        source["documents"][2]["definition"]["model_ref"]["sha256"] = "*"
    if mutation == "duplicate_operation":
        doc["definition"]["operations"].append(copy.deepcopy(doc["definition"]["operations"][0]))
    if mutation == "missing_read":
        doc["definition"]["operations"][1]["reconciliation"]["read_operation"] = "missing"
    if mutation == "bad_number":
        source["documents"][-1]["definition"]["size_bytes"] = 1.0
    with pytest.raises(ExecutableError):
        validate_catalog(source)


def test_catalog_literal_definition_scope(catalog_case):
    source = catalog_case()
    doc = source["documents"][3]
    doc["schemas"] = [{"name": "numeric", "schema": {"kind": "number"}}]
    doc["definition"]["rules"][0]["rule"] = {
        "op": "eq",
        "readback_pointer": "",
        "expected": {
            "kind": "literal",
            "schema": {"kind": "ref", "name": "numeric"},
            "value": 10**100,
        },
    }
    catalog = validate_catalog(source)
    assert type(catalog.documents[3].definition.rules[0].rule.expected.value) is float


def test_document_digest_binds_complete_document(catalog_case):
    import hashlib

    from lotuspond_sdk.executable import (
        canonical_catalog_document,
        catalog_document_digest,
        decode_executable_bytes,
    )

    for doc in catalog_case()["documents"]:
        encoded = canonical_catalog_document(doc)
        assert hashlib.sha256(encoded).hexdigest() == catalog_document_digest(doc)
        assert canonical_catalog_document(decode_executable_bytes(encoded)) == encoded
        doc["version"] = "2.0.0"
        assert catalog_document_digest(doc) != hashlib.sha256(encoded).hexdigest()


def test_all_unused_catalog_schemas_checked(catalog_case):
    source = catalog_case()
    source["documents"][0]["schemas"] = [
        {"name": "bad", "schema": {"kind": "ref", "name": "missing"}}
    ]
    with pytest.raises(ExecutableError):
        validate_catalog(source)


def test_catalog_mutation_revalidated(catalog_case):
    catalog = validate_catalog(catalog_case())
    catalog.documents[0].format_version = True
    with pytest.raises(ExecutableError):
        validate_catalog(catalog)


def test_rule_node_depth_and_required_readback(catalog_case):
    source = catalog_case()
    rule = {"op": "present", "readback_pointer": ""}
    for _ in range(8):
        rule = {"op": "and", "rules": [rule, {"op": "present", "readback_pointer": ""}]}
    source["documents"][3]["definition"]["rules"][0]["rule"] = rule
    with pytest.raises(ExecutableError):
        validate_catalog(source)
    source["documents"][3]["definition"]["rules"][0]["rule"] = {
        "op": "eq",
        "expected": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
    }
    with pytest.raises(ExecutableError):
        validate_catalog(source)


def test_rule_typed_literal_domain_and_pointer(catalog_case):
    source = catalog_case()
    rule = {
        "op": "eq",
        "readback_pointer": "/bad~2escape",
        "expected": {
            "kind": "literal",
            "schema": {"kind": "decimal", "precision": 3, "scale": 2},
            "value": "1.00",
        },
    }
    source["documents"][3]["definition"]["rules"][0]["rule"] = rule
    with pytest.raises(ExecutableError):
        validate_catalog(source)
    rule["readback_pointer"] = ""
    rule["expected"]["value"] = "1.0"
    with pytest.raises(ExecutableError):
        validate_catalog(source)


def test_family_count_64(catalog_case):
    doc = catalog_case()["documents"][-1]
    source = {"documents": [dict(copy.deepcopy(doc), name=f"artifact{i}") for i in range(64)]}
    validate_catalog(source)
    source["documents"].append(dict(copy.deepcopy(doc), name="extra"))
    with pytest.raises(ExecutableError):
        validate_catalog(source)


def test_catalog_conflicts_have_domain_code(catalog_case):
    source = catalog_case()
    source["documents"].append(copy.deepcopy(source["documents"][0]))
    source["documents"][-1]["definition"]["artifact_sha256"] = "c" * 64
    with pytest.raises(ExecutableError) as error:
        validate_catalog(source)
    assert error.value.code == "dependency_conflict"


def test_catalog_number_roundtrip_and_complete_bundle_cap(catalog_case):
    from lotuspond_sdk.executable import canonical_catalog_document, decode_executable_bytes
    from lotuspond_sdk.executable._canonical import jcs_bytes
    from lotuspond_sdk.executable.catalog import CATALOG_MODELS
    from lotuspond_sdk.executable.input import project_json

    source = catalog_case()
    doc = source["documents"][3]
    doc["definition"]["rules"][0]["rule"] = {
        "op": "eq",
        "readback_pointer": "",
        "expected": {"kind": "literal", "schema": {"kind": "number"}, "value": 10**100},
    }
    encoded = canonical_catalog_document(doc)
    assert canonical_catalog_document(decode_executable_bytes(encoded)) == encoded
    bundle = validate_catalog(source)
    assert (
        validate_catalog(
            decode_executable_bytes(jcs_bytes(project_json(bundle, model_types=CATALOG_MODELS)))
        )
        == bundle
    )


def test_complete_catalog_item_budget_after_defaults(catalog_case):
    from lotuspond_sdk.executable.input import project_json

    doc = catalog_case()["documents"][1]
    doc["definition"]["output_schema"] = {
        "kind": "object",
        "fields": [
            {"name": f"f{i}", "schema": {"kind": "integer"}, "required": True} for i in range(64)
        ],
    }
    source = {"documents": [dict(copy.deepcopy(doc), name=f"m{i}") for i in range(12)]}
    project_json(source)
    with pytest.raises(ExecutableError) as error:
        validate_catalog(source)
    assert error.value.code == "input_too_complex"


def test_bundle_bytes_are_not_only_per_document(catalog_case):
    from lotuspond_sdk.executable import canonical_catalog_document

    doc = catalog_case()["documents"][3]
    schema = {
        "kind": "object",
        "fields": [
            {"name": f"f{i}", "schema": {"kind": "string", "max_length": 65536}, "required": True}
            for i in range(10)
        ],
    }
    doc["definition"]["rules"][0]["rule"] = {
        "op": "eq",
        "readback_pointer": "",
        "expected": {
            "kind": "literal",
            "schema": schema,
            "value": {f"f{i}": "x" * 60000 for i in range(10)},
        },
    }
    assert len(canonical_catalog_document(doc)) < 1048576
    with pytest.raises(ExecutableError) as error:
        validate_catalog({"documents": [doc, dict(copy.deepcopy(doc), name="other")]})
    assert error.value.code == "input_too_large"


def test_catalog_schema_count_is_preflighted(catalog_case):
    source = catalog_case()
    source["documents"][0]["schemas"] = [
        {"name": f"s{i}", "schema": {"kind": "null"}} for i in range(65)
    ]
    with pytest.raises(ExecutableError) as error:
        validate_catalog(source)
    assert error.value.code == "invalid_schema"


def test_identical_normalized_documents_are_retained(catalog_case):
    from lotuspond_sdk.executable import canonical_catalog_document, decode_executable_bytes

    source = catalog_case()["documents"][0]
    explicit = decode_executable_bytes(canonical_catalog_document(source))
    assert source != explicit  # Defaults differ in the authored representations.
    bundle = validate_catalog({"documents": [source, explicit]})
    assert len(bundle.documents) == 2
    assert bundle.documents[0] == bundle.documents[1]
    assert bundle.documents[0] is not bundle.documents[1]


def test_identical_documents_count_toward_family_limit(catalog_case):
    document = catalog_case()["documents"][-1]
    source = {"documents": [copy.deepcopy(document) for _ in range(64)]}
    assert len(validate_catalog(source).documents) == 64
    source["documents"].append(copy.deepcopy(document))
    with pytest.raises(ExecutableError) as error:
        validate_catalog(source)
    assert error.value.code == "invalid_catalog"


def test_identical_documents_count_toward_normalized_item_limit(catalog_case):
    from lotuspond_sdk.executable.input import project_json

    document = catalog_case()["documents"][1]
    document["definition"]["output_schema"] = {
        "kind": "object",
        "fields": [
            {"name": f"f{i}", "schema": {"kind": "integer"}, "required": True} for i in range(64)
        ],
    }
    source = {"documents": [copy.deepcopy(document) for _ in range(12)]}
    project_json(source)  # Raw item count fits; defaults push the complete bundle over.
    with pytest.raises(ExecutableError) as error:
        validate_catalog(source)
    assert error.value.code == "input_too_complex"


def test_identical_documents_count_toward_complete_byte_limit(catalog_case):
    from lotuspond_sdk.executable import canonical_catalog_document

    document = catalog_case()["documents"][3]
    schema = {
        "kind": "object",
        "fields": [
            {
                "name": f"f{i}",
                "schema": {"kind": "string", "max_length": 65536},
                "required": True,
            }
            for i in range(10)
        ],
    }
    document["definition"]["rules"][0]["rule"] = {
        "op": "eq",
        "readback_pointer": "",
        "expected": {
            "kind": "literal",
            "schema": schema,
            "value": {f"f{i}": "x" * 60000 for i in range(10)},
        },
    }
    assert len(canonical_catalog_document(document)) < 1048576
    with pytest.raises(ExecutableError) as error:
        validate_catalog({"documents": [document, copy.deepcopy(document)]})
    assert error.value.code == "input_too_large"


def test_identical_bundle_normalized_byte_boundary(catalog_case):
    import json

    from lotuspond_sdk.executable import canonical_catalog_document, decode_executable_bytes
    from lotuspond_sdk.executable._canonical import jcs_bytes
    from lotuspond_sdk.executable.catalog import CATALOG_MODELS
    from lotuspond_sdk.executable.input import project_json

    document = catalog_case()["documents"][3]
    schema = {
        "kind": "object",
        "fields": [
            {
                "name": f"f{i}",
                "schema": {"kind": "string", "max_length": 65536},
                "required": True,
            }
            for i in range(8)
        ]
        + [{"name": "n", "schema": {"kind": "number"}, "required": True}],
    }
    values = {f"f{i}": "" for i in range(8)}
    values["n"] = 1
    document["definition"]["rules"][0]["rule"] = {
        "op": "eq",
        "readback_pointer": "",
        "expected": {"kind": "literal", "schema": schema, "value": values},
    }
    overhead = len(b'{"documents":[') + len(b"]}") + 1
    remaining = (1048576 - overhead) // 2 - len(canonical_catalog_document(document))
    for i in range(8):
        amount = min(remaining, 65536)
        values[f"f{i}"] = "x" * amount
        remaining -= amount
    assert remaining == 0
    source = {"documents": [document, copy.deepcopy(document)]}
    bundle = validate_catalog(source)
    encoded = jcs_bytes(project_json(bundle, model_types=CATALOG_MODELS))
    assert len(encoded) == 1048575
    assert len(validate_catalog(decode_executable_bytes(encoded)).documents) == 2
    values["f7"] += "x"
    source["documents"][1] = copy.deepcopy(document)
    raw = json.dumps(source, separators=(",", ":")).encode()
    assert len(raw) <= 1048576  # Omitted schema defaults leave raw input below its cap.
    with pytest.raises(ExecutableError) as error:
        validate_catalog(decode_executable_bytes(raw))
    assert error.value.code == "input_too_large"
