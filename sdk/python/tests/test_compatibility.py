"""Synthetic declaration checks: exact support, bounded safe reports, no authority."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from lotuspond_sdk import (
    CANONICALIZATION,
    SCHEMA_VERSION,
    CapabilityProfile,
    CompatibilityReport,
    CompatibilityRequirements,
    SdkError,
    canonical_package_json,
    check_declared_compatibility,
    package_digest,
    validate_package,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "contracts" / "examples"


@pytest.fixture
def full_profile():
    return {
        "format_version": "lotuspond.workflow-capabilities.v1",
        "assurance": "synthetic-declaration",
        "schema_versions": [SCHEMA_VERSION],
        "canonicalizations": [CANONICALIZATION],
        "node_kinds": ["start", "end", "agent", "connector", "human_review"],
        "connectors": [
            {
                "connector": "synthetic-case-system",
                "version": "1.0.0",
                "operations": ["read-case", "write-case"],
            }
        ],
        "models": [{"profile": "primary-model", "revision": "2026-10-13"}],
        "platforms": [
            {
                "os": "linux",
                "architecture": "x86_64",
                "capabilities": ["human-review", "secret-references"],
            }
        ],
    }


@pytest.fixture
def requirements():
    return {
        "format_version": 1,
        "os": "linux",
        "architecture": "x86_64",
        "required_capabilities": ["human-review", "secret-references"],
    }


def codes(report):
    return {reason.code for reason in report.reasons}


def test_partial_profile_fails_closed(example, full_profile, requirements, golden_bytes):
    report = check_declared_compatibility(example, full_profile, requirements)
    assert report.compatible is True
    assert report.assurance == "synthetic-declaration"
    assert report.authority_granted is False
    assert report.contract_valid is True
    assert report.signature_status == "not_checked"
    assert report.reasons == []
    assert report.reasons_truncated is False
    assert report.package_sha256 == hashlib.sha256(golden_bytes).hexdigest()
    for field, expected in [
        ("connectors", "unsupported_connector"),
        ("models", "unsupported_model"),
        ("platforms", "unsupported_platform"),
    ]:
        partial = deepcopy(full_profile)
        partial[field] = []
        report = check_declared_compatibility(example, partial, requirements)
        assert report.compatible is False
        assert expected in codes(report)
        assert report.authority_granted is False


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("schema_versions", "unsupported_schema"),
        ("canonicalizations", "unsupported_canonicalization"),
        ("node_kinds", "unsupported_node"),
    ],
)
def test_empty_support_lists_are_well_formed_refusals(
    example, full_profile, requirements, field, code
):
    full_profile[field] = []
    report = check_declared_compatibility(example, full_profile, requirements)
    assert report.compatible is False
    assert code in codes(report)


@pytest.mark.parametrize(
    ("collection", "field", "value", "code"),
    [
        ("connectors", "connector", "other-connector", "unsupported_connector"),
        ("connectors", "version", "1.0.1", "unsupported_connector"),
        ("connectors", "operations", ["read-case"], "unsupported_operation"),
        ("models", "profile", "other-model", "unsupported_model"),
        ("models", "revision", "2026-10-14", "unsupported_model"),
        ("platforms", "os", "windows", "unsupported_platform"),
        ("platforms", "architecture", "arm64", "unsupported_platform"),
        ("platforms", "capabilities", ["human-review"], "missing_capability"),
    ],
)
def test_support_is_exact(example, full_profile, requirements, collection, field, value, code):
    full_profile[collection][0][field] = value
    report = check_declared_compatibility(example, full_profile, requirements)
    assert report.compatible is False
    assert code in codes(report)


def test_unused_declarations_and_operations_are_checked(example, full_profile, requirements):
    example["models"].append({"id": "unused-model", "profile": "unused", "revision": "v1"})
    example["connectors"].append(
        {
            "id": "unused-connector",
            "connector": "unused",
            "version": "1.0.0",
            "operations": ["unused-operation"],
        }
    )
    example["connectors"][0]["operations"].append("unused-operation")
    report = check_declared_compatibility(example, full_profile, requirements)
    assert report.compatible is False
    assert {"unsupported_model", "unsupported_connector", "unsupported_operation"} <= codes(report)
    assert {reason.path for reason in report.reasons} == {
        "/models/1",
        "/connectors/1",
        "/connectors/0/operations/2",
    }


@pytest.mark.parametrize("value", [None, {}, [], "private-profile", 1])
def test_invalid_profiles_have_fixed_errors(example, requirements, value):
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, value, requirements)
    assert failure.value.code == "invalid_capability_profile"
    assert "private-profile" not in str(failure.value)


@pytest.mark.parametrize(
    "field",
    [
        "format_version",
        "assurance",
        "schema_versions",
        "canonicalizations",
        "node_kinds",
        "connectors",
        "models",
        "platforms",
    ],
)
def test_profile_fields_are_required(example, full_profile, requirements, field):
    del full_profile[field]
    with pytest.raises(SdkError, match="capability profile") as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_capability_profile"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", "lotuspond.workflow-capabilities.v2"),
        ("assurance", "production-supported"),
        ("schema_versions", ["lotuspond.workflow-package.v2"]),
        ("canonicalizations", ["other-canonicalizer"]),
        ("node_kinds", ["unknown"]),
        ("private-extra-field", "secret-value"),
        ("node_kinds", ("start",)),
    ],
)
def test_profile_rejects_unknown_metadata_and_nonlists(
    example, full_profile, requirements, field, value
):
    full_profile[field] = value
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_capability_profile"
    assert "secret-value" not in str(failure.value)
    assert "private-extra-field" not in str(failure.value)


@pytest.mark.parametrize(
    "collection",
    ["schema_versions", "canonicalizations", "node_kinds", "connectors", "models", "platforms"],
)
def test_profile_rejects_duplicate_natural_keys(example, full_profile, requirements, collection):
    full_profile[collection].append(deepcopy(full_profile[collection][0]))
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_capability_profile"


@pytest.mark.parametrize("collection", ["connectors", "platforms"])
def test_profile_rejects_duplicate_nested_capabilities(
    example, full_profile, requirements, collection
):
    key = "operations" if collection == "connectors" else "capabilities"
    full_profile[collection][0][key] *= 2
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_capability_profile"


def test_conflicting_platforms_are_invalid(example, full_profile, requirements):
    full_profile["platforms"].append({"os": "linux", "architecture": "x86_64", "capabilities": []})
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_capability_profile"


@pytest.mark.parametrize(
    ("collection", "field", "value"),
    [
        ("connectors", "version", "*"),
        ("models", "revision", "*"),
        ("platforms", "architecture", "*"),
        ("connectors", "extra", "secret"),
        ("models", "extra", "secret"),
        ("platforms", "extra", "secret"),
    ],
)
def test_nested_records_are_strict(example, full_profile, requirements, collection, field, value):
    full_profile[collection][0][field] = value
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_capability_profile"


@pytest.mark.parametrize("collection", ["connectors", "models", "platforms"])
def test_nested_fields_are_required(example, full_profile, requirements, collection):
    for field in tuple(full_profile[collection][0]):
        invalid = deepcopy(full_profile)
        del invalid[collection][0][field]
        with pytest.raises(SdkError) as failure:
            check_declared_compatibility(example, invalid, requirements)
        assert failure.value.code == "invalid_capability_profile"


@pytest.mark.parametrize("value", [None, {}, [], "private-requirements", True])
def test_requirements_are_explicit(example, full_profile, value):
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, value)
    assert failure.value.code == "invalid_compatibility_requirements"
    assert "private-requirements" not in str(failure.value)


@pytest.mark.parametrize("value", [True, False, 1.0, "1", 2])
def test_requirements_version_is_an_exact_integer(example, full_profile, requirements, value):
    requirements["format_version"] = value
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_compatibility_requirements"


@pytest.mark.parametrize("field", ["format_version", "os", "architecture", "required_capabilities"])
def test_requirements_fields_are_required(example, full_profile, requirements, field):
    del requirements[field]
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_compatibility_requirements"


def test_requirements_forbid_duplicates_extras_and_wildcards(example, full_profile, requirements):
    for change in [
        {"extra": "secret"},
        {"os": "*"},
        {"architecture": "*"},
        {"required_capabilities": ["human-review"] * 2},
        {"required_capabilities": ("human-review",)},
    ]:
        with pytest.raises(SdkError) as failure:
            check_declared_compatibility(example, full_profile, requirements | change)
        assert failure.value.code == "invalid_compatibility_requirements"


def test_empty_profiles_deny_support_but_empty_requirements_are_permitted(
    example, full_profile, requirements
):
    for field in (
        "schema_versions",
        "canonicalizations",
        "node_kinds",
        "connectors",
        "models",
        "platforms",
    ):
        full_profile[field] = []
    assert check_declared_compatibility(example, full_profile, requirements).compatible is False
    requirements["required_capabilities"] = []
    full_profile["platforms"] = [{"os": "linux", "architecture": "x86_64", "capabilities": []}]
    report = check_declared_compatibility(example, full_profile, requirements)
    assert "missing_capability" not in codes(report)
    assert "unsupported_platform" not in codes(report)


def test_all_profile_lists_are_bounded(example, full_profile, requirements):
    changes = [
        (
            "connectors",
            [{"connector": f"c{i}", "version": "1.0.0", "operations": []} for i in range(65)],
        ),
        ("models", [{"profile": f"m{i}", "revision": "r1"} for i in range(65)]),
        (
            "platforms",
            [{"os": f"os{i}", "architecture": "arm64", "capabilities": []} for i in range(65)],
        ),
    ]
    for collection, value in changes:
        with pytest.raises(SdkError) as failure:
            check_declared_compatibility(example, full_profile | {collection: value}, requirements)
        assert failure.value.code == "invalid_capability_profile"
    for collection, key, size in [
        ("connectors", "operations", 33),
        ("platforms", "capabilities", 65),
    ]:
        invalid = deepcopy(full_profile)
        invalid[collection][0][key] = [f"c{i}" for i in range(size)]
        with pytest.raises(SdkError) as failure:
            check_declared_compatibility(example, invalid, requirements)
        assert failure.value.code == "invalid_capability_profile"
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(
            example,
            full_profile,
            requirements | {"required_capabilities": [f"c{i}" for i in range(65)]},
        )
    assert failure.value.code == "invalid_compatibility_requirements"


def test_mutable_typed_inputs_revalidated(example, full_profile, requirements):
    for mutation in [
        lambda p: p.connectors[0].operations.append("*"),
        lambda p: p.__dict__.update(private_extra="secret"),
        lambda p: p.connectors[0].__dict__.update(private_extra="secret"),
        lambda p: p.models[0].__dict__.pop("revision"),
        lambda p: p.platforms.append(p.platforms[0]),
        lambda p: p.__dict__.update(__pydantic_extra__={"private_extra": "secret"}),
    ]:
        profile = CapabilityProfile.model_validate(full_profile)
        mutation(profile)
        with pytest.raises(SdkError) as failure:
            check_declared_compatibility(example, profile, requirements)
        assert failure.value.code == "invalid_capability_profile"
    typed = CompatibilityRequirements.model_validate(requirements)
    typed.format_version = True
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, typed)
    assert failure.value.code == "invalid_compatibility_requirements"
    package = validate_package(example)
    package.nodes[2].limits.max_steps = "5"
    with pytest.raises(ValidationError):
        check_declared_compatibility(package, full_profile, requirements)


def test_digests_bind_assessment_without_changing_package(
    example, full_profile, requirements, golden_bytes
):
    profile = CapabilityProfile.model_validate(full_profile)
    assessment = CompatibilityRequirements.model_validate(requirements)
    report = check_declared_compatibility(validate_package(example), profile, assessment)
    for value, digest in [
        (full_profile, report.profile_sha256),
        (requirements, report.requirements_sha256),
    ]:
        assert (
            digest
            == hashlib.sha256(
                json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
        )
    full_profile["models"][0]["revision"] = "another-revision"
    requirements["required_capabilities"].append("another-capability")
    changed = check_declared_compatibility(example, full_profile, requirements)
    assert changed.profile_sha256 != report.profile_sha256
    assert changed.requirements_sha256 != report.requirements_sha256
    assert (
        changed.package_sha256 == report.package_sha256 == package_digest(validate_package(example))
    )
    assert canonical_package_json(validate_package(example)) == golden_bytes
    assert (
        check_declared_compatibility(
            example, dict(reversed(list(full_profile.items()))), requirements
        ).profile_sha256
        == changed.profile_sha256
    )


def test_more_than_twenty_reasons_remains_incompatible_and_private(
    example, full_profile, requirements
):
    example["models"] = [
        {"id": f"m{i}", "profile": f"private-model{i}", "revision": "secret-revision"}
        for i in range(32)
    ]
    example["nodes"][2]["model_ref"] = "m0"
    example["name"] = "secret-package-title"
    example["nodes"][2]["instructions"] = "secret-instructions"
    report = check_declared_compatibility(example, full_profile, requirements)
    assert report.compatible is False
    assert report.reasons_truncated is True
    assert len(report.reasons) == 20
    pairs = [(reason.code, reason.path) for reason in report.reasons]
    assert pairs == sorted(pairs)
    assert {reason.code for reason in report.reasons} == {"unsupported_model"}
    encoded = report.model_dump_json()
    assert encoded.isascii()
    assert all(
        secret not in encoded
        for secret in [
            "private-model",
            "secret-revision",
            "secret-package-title",
            "secret-instructions",
            "case-credential",
        ]
    )
    assert (
        check_declared_compatibility(example, full_profile, requirements).model_dump_json()
        == encoded
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authority_granted", 0),
        ("authority_granted", True),
        ("contract_valid", 1),
        ("contract_valid", False),
        ("compatible", 1),
        ("reasons_truncated", 0),
        ("signature_status", "verified"),
        ("assurance", "production-supported"),
    ],
)
def test_report_fields_cannot_overstate_assurance(
    example, full_profile, requirements, field, value
):
    raw = check_declared_compatibility(example, full_profile, requirements).model_dump()
    raw[field] = value
    with pytest.raises(ValidationError):
        CompatibilityReport.model_validate(raw)


def test_report_rejects_mutations_and_unsafe_paths(example, full_profile, requirements):
    report = check_declared_compatibility(example, full_profile, requirements)
    report.authority_granted = 0
    with pytest.raises(ValidationError):
        report.model_dump_json()
    raw = check_declared_compatibility(example, full_profile, requirements).model_dump()
    raw.update(compatible=False, reasons=[{"code": "unsupported_model", "path": "/secret-value"}])
    with pytest.raises(ValidationError):
        CompatibilityReport.model_validate(raw)
    raw["reasons"] = [{"code": "unknown-code", "path": "/models/0"}]
    with pytest.raises(ValidationError):
        CompatibilityReport.model_validate(raw)


def test_serialization_bounds_actual_formatting_and_full_record(
    example, full_profile, requirements, monkeypatch
):
    import lotuspond_sdk.trust as shared_reports

    report = check_declared_compatibility(example, full_profile, requirements)
    compact = report.model_dump_json()
    indented = report.model_dump_json(indent=2)
    monkeypatch.setattr(shared_reports, "MAX_REPORT_BYTES", len(indented))
    assert report.model_dump_json(indent=2) == indented
    monkeypatch.setattr(shared_reports, "MAX_REPORT_BYTES", len(indented) - 1)
    with pytest.raises(SdkError) as failure:
        report.model_dump_json(indent=2)
    assert failure.value.code == "report_too_large"
    monkeypatch.setattr(shared_reports, "MAX_REPORT_BYTES", len(compact) - 1)
    with pytest.raises(SdkError):
        report.model_dump_json(include={"compatible"})
    with pytest.raises(SdkError):
        report.model_dump(include={"compatible"})


def test_synthetic_fixtures_match_example(example):
    profile = json.loads((EXAMPLES / "synthetic-capabilities.v1.json").read_bytes())
    requirements = json.loads((EXAMPLES / "synthetic-requirements.v1.json").read_bytes())
    report = check_declared_compatibility(example, profile, requirements)
    assert report.compatible is True
    assert report.assurance == "synthetic-declaration"
    assert report.authority_granted is False


def test_multiple_explicit_versions_and_revisions_match_exactly(
    example, full_profile, requirements
):
    full_profile["connectors"].append(
        {"connector": "synthetic-case-system", "version": "2.0.0", "operations": ["new-operation"]}
    )
    full_profile["models"].append({"profile": "primary-model", "revision": "new-revision"})
    assert check_declared_compatibility(example, full_profile, requirements).compatible is True
    example["connectors"].append(
        {
            "id": "new-declaration",
            "connector": "synthetic-case-system",
            "version": "2.0.0",
            "operations": ["new-operation"],
        }
    )
    example["models"].append(
        {"id": "new-model", "profile": "primary-model", "revision": "new-revision"}
    )
    assert check_declared_compatibility(example, full_profile, requirements).compatible is True
    # Support at another version cannot lend operations to the declared version.
    example["connectors"][0]["operations"].append("new-operation")
    assert "unsupported_operation" in codes(
        check_declared_compatibility(example, full_profile, requirements)
    )
    full_profile["connectors"][1]["version"] = "1.0.0"
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, requirements)
    assert failure.value.code == "invalid_capability_profile"


def test_constructed_and_nested_typed_mutations_do_not_drop_unknown_fields(
    example, full_profile, requirements
):
    typed = CapabilityProfile.model_validate(full_profile)
    object.__setattr__(typed.connectors[0], "__pydantic_extra__", {"private_extra": "secret"})
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, typed, requirements)
    assert failure.value.code == "invalid_capability_profile"
    typed = CompatibilityRequirements.model_validate(requirements)
    typed.__dict__["private_extra"] = "secret"
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, full_profile, typed)
    assert failure.value.code == "invalid_compatibility_requirements"
    typed = CapabilityProfile.model_construct(**(full_profile | {"node_kinds": ["unknown"]}))
    with pytest.raises(SdkError) as failure:
        check_declared_compatibility(example, typed, requirements)
    assert failure.value.code == "invalid_capability_profile"
    report = check_declared_compatibility(example, full_profile, requirements)
    report.__dict__["private_extra"] = "secret"
    with pytest.raises(ValidationError):
        report.model_dump()


def test_non_json_cyclic_deep_and_oversize_inputs_fail_safely(example, full_profile, requirements):
    cycle = []
    cycle.append(cycle)
    deep = []
    for _ in range(33):
        deep = [deep]
    for invalid in [cycle, deep, {"secret-key": "s" * (1024 * 1024 + 1)}, object()]:
        with pytest.raises(SdkError) as failure:
            check_declared_compatibility(
                example, full_profile | {"node_kinds": invalid}, requirements
            )
        assert failure.value.code == "invalid_capability_profile"


def test_report_refusals_are_bounded_sorted_and_cannot_claim_success(
    example, full_profile, requirements
):
    report = check_declared_compatibility(example, full_profile | {"models": []}, requirements)
    report.reasons[0].__dict__["private_extra"] = "secret"
    with pytest.raises(ValidationError):
        report.model_dump_json()
    raw = check_declared_compatibility(example, full_profile, requirements).model_dump()
    refusal = {"code": "unsupported_model", "path": "/models/0"}
    for changes in [
        {"reasons_truncated": True},
        {"reasons": [refusal]},
        {"compatible": False, "reasons": [refusal] * 21},
        {
            "compatible": False,
            "reasons": [{"code": "unsupported_model", "path": "/models/1"}, refusal],
        },
    ]:
        with pytest.raises(ValidationError):
            CompatibilityReport.model_validate(raw | changes)
