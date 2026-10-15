"""Hostile inputs, exact domain boundaries and safe validation/report foundations."""

import json
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError, model_serializer

from lotuspond_sdk.executable import ExecutableError, ExecutableIssue, decode_executable_bytes
from lotuspond_sdk.executable._canonical import jcs_bytes
from lotuspond_sdk.executable._validation import StrictModel, report_bytes, validate_model
from lotuspond_sdk.executable.errors import ERROR_MESSAGES, safe_validation_issues
from lotuspond_sdk.executable.input import (
    PACKAGE_LIMITS,
    REPORT_LIMITS,
    WIRE_LIMITS,
    JsonLimits,
    project_json,
)
from lotuspond_sdk.input import PackageReadError


@pytest.mark.parametrize(
    "content,code",
    [
        (b'{"private":1,"private":2}', "duplicate_key"),
        (b"\xff", "invalid_utf8"),
        (b"{", "invalid_json"),
        (b"NaN", "nonfinite_number"),
        (b"1e309", "nonfinite_number"),
        (b"9" * 310, "number_too_large"),
        (b"1." + b"0" * 1023, "number_too_large"),
        (b" " * 1048577, "input_too_large"),
        (b"[" * 33 + b"0" + b"]" * 33, "input_too_deep"),
    ],
)
def test_decode_uses_existing_fixed_low_level_errors(content, code):
    with pytest.raises(PackageReadError) as raised:
        decode_executable_bytes(content)
    assert raised.value.code == code
    assert "private" not in str(raised.value)


def test_raw_numeric_domains_remain_available_for_field_selection():
    assert decode_executable_bytes(b"9007199254740992") == 9007199254740992
    assert decode_executable_bytes(b"1" + b"0" * 308).bit_length() <= 1024
    assert decode_executable_bytes(b"-1" + b"0" * 308).bit_length() <= 1024
    assert decode_executable_bytes(b"1." + b"0" * 1022) == 1.0
    assert decode_executable_bytes(b"-0.0") == 0.0
    with pytest.raises(ExecutableError) as raised:
        decode_executable_bytes(b"9" * 309)
    assert raised.value.code == "invalid_json_value"
    assert project_json(2**1024 - 1) == 2**1024 - 1
    with pytest.raises(ExecutableError) as raised:
        project_json(2**1024)
    assert raised.value.code == "invalid_json_value"


@pytest.mark.parametrize(
    "raw,code",
    [
        ("\ud800", "invalid_unicode"),
        ({"\udfff": 0}, "invalid_unicode"),
        (float("nan"), "nonfinite_number"),
        (float("inf"), "nonfinite_number"),
        ({1: 0}, "invalid_json_value"),
        ((1, 2), "invalid_json_value"),
        ({1, 2}, "invalid_json_value"),
        (b"private", "invalid_json_value"),
        (object(), "invalid_json_value"),
    ],
)
def test_programmatic_and_escaped_invalid_values_are_refused(raw, code):
    for call in (project_json, jcs_bytes):
        with pytest.raises(ExecutableError) as raised:
            call(raw)
        assert raised.value.code == code
    if code == "invalid_unicode":
        with pytest.raises(ExecutableError) as raised:
            decode_executable_bytes(json.dumps(raw).encode())
        assert raised.value.code == code


def test_cycles_and_shared_acyclic_values():
    cyclic = []
    cyclic.append(cyclic)
    for call in (project_json, jcs_bytes):
        with pytest.raises(ExecutableError) as raised:
            call(cyclic)
        assert raised.value.code == "cyclic_input"
    cyclic_model = Record.model_construct(value=1)
    cyclic_model.__dict__["value"] = cyclic_model
    with pytest.raises(ExecutableError) as raised:
        project_json(cyclic_model, model_types=(Record,))
    assert raised.value.code == "cyclic_input"
    child = {"a": 0}
    assert project_json([child, child]) == [{"a": 0}, {"a": 0}]


def test_exact_types_refuse_subclasses_without_invoking_hooks():
    class Stranger(dict):
        def items(self):
            pytest.fail("arbitrary hook called")

    class IntSubclass(int):
        pass

    for value in (Stranger(a=1), IntSubclass(1)):
        with pytest.raises(ExecutableError) as raised:
            project_json(value)
        assert raised.value.code == "invalid_json_value"


@pytest.mark.parametrize("limits", [PACKAGE_LIMITS, WIRE_LIMITS, REPORT_LIMITS])
def test_every_traversal_byte_depth_item_boundary(limits):
    assert (PACKAGE_LIMITS.max_bytes, PACKAGE_LIMITS.max_depth, PACKAGE_LIMITS.max_items) == (
        1048576,
        32,
        10000,
    )
    assert (WIRE_LIMITS.max_bytes, WIRE_LIMITS.max_depth, WIRE_LIMITS.max_items) == (
        65536,
        32,
        10000,
    )
    assert (REPORT_LIMITS.max_bytes, REPORT_LIMITS.max_depth, REPORT_LIMITS.max_items) == (
        8388608,
        32,
        262144,
    )
    assert project_json("x" * (limits.max_bytes - 2), limits=limits)
    with pytest.raises(ExecutableError) as raised:
        project_json("x" * (limits.max_bytes - 1), limits=limits)
    assert raised.value.code in ("input_too_large", "report_too_large")
    assert len(project_json([0] * (limits.max_items - 1), limits=limits)) == limits.max_items - 1
    with pytest.raises(ExecutableError) as raised:
        project_json([0] * limits.max_items, limits=limits)
    assert raised.value.code == "input_too_complex"
    nested = 0
    for _ in range(limits.max_depth):
        nested = [nested]
    assert project_json(nested, limits=limits) == nested
    with pytest.raises(ExecutableError) as raised:
        project_json([nested], limits=limits)
    assert raised.value.code == "input_too_deep"
    with pytest.raises(ExecutableError):
        project_json([[[[]]]], limits=JsonLimits(100, 3, 100))
    # Dictionary keys count toward the item cap too.
    assert project_json({"a": 0}, limits=JsonLimits(100, 32, 3)) == {"a": 0}
    with pytest.raises(ExecutableError):
        project_json({"a": 0}, limits=JsonLimits(100, 32, 2))


def test_decoder_boundaries_and_escaped_unicode():
    assert decode_executable_bytes(b" " * 1048575 + b"0") == 0
    assert decode_executable_bytes(b"[" * 32 + b"0" + b"]" * 32)
    assert len(decode_executable_bytes(b"[" + b"0," * 9998 + b"0]")) == 9999
    with pytest.raises(ExecutableError) as raised:
        decode_executable_bytes(b"[" + b"0," * 9999 + b"0]")
    assert raised.value.code == "input_too_complex"
    assert decode_executable_bytes(b'"\\ud83d\\ude00"') == "😀"
    assert decode_executable_bytes(b'"[[[\\"[]"') == '[[["[]'
    with pytest.raises(ExecutableError) as raised:
        decode_executable_bytes(b'"\\ud800"')
    assert raised.value.code == "invalid_unicode"
    with pytest.raises(PackageReadError) as raised:
        decode_executable_bytes(b'"' + b"\\u0000" * 174763 + b'"')
    assert raised.value.code == "input_too_large"


class Record(StrictModel):
    value: int
    format_version: Literal[2] = 2
    authority_granted: Literal[False] = False
    synthetic: Literal[True] = True


class SerializerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int

    @model_serializer
    def surprise(self):
        pytest.fail("arbitrary serializer called")


def test_projection_uses_known_exact_classes_and_preserves_defaults():
    model = SerializerRecord(value=1)
    assert project_json(model, model_types=(SerializerRecord,)) == {"value": 1}
    with pytest.raises(ExecutableError):
        project_json(model)

    class Subclass(SerializerRecord):
        pass

    with pytest.raises(ExecutableError):
        project_json(Subclass(value=1), model_types=(SerializerRecord,))
    assert project_json(Record(value=1), model_types=(Record,)) == {
        "value": 1,
        "format_version": 2,
        "authority_granted": False,
        "synthetic": True,
    }
    for target in ("__dict__", "__pydantic_extra__"):
        model = SerializerRecord(value=1)
        if target == "__dict__":
            model.__dict__["private-sentinel"] = "private-value"
        else:
            object.__setattr__(model, target, {"private-sentinel": "private-value"})
        with pytest.raises(ExecutableError) as raised:
            project_json(model, model_types=(SerializerRecord,))
        assert "private" not in str(raised.value)


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("value", True),
        ("value", 1.0),
        ("value", "1"),
        ("format_version", True),
        ("format_version", 2.0),
        ("authority_granted", 0),
        ("authority_granted", 0.0),
        ("synthetic", 1),
        ("synthetic", 1.0),
    ],
)
@pytest.mark.parametrize("origin", ["parsed", "mutated", "constructed"])
def test_fixed_integers_and_booleans_revalidate_every_origin(field, invalid, origin):
    raw = {"value": 1}
    if origin == "parsed":
        raw[field] = invalid
    elif origin == "mutated":
        raw = Record(value=1)
        setattr(raw, field, invalid)
    else:
        raw[field] = invalid
        raw = Record.model_construct(**raw)
    with pytest.raises(ExecutableError) as raised:
        validate_model(raw, model_type=Record, model_types=(Record,), code="invalid_package")
    assert raised.value.code == "invalid_package"
    with pytest.raises(ExecutableError):
        report_bytes(raw, model_type=Record, model_types=(Record,), code="invalid_package")


def test_constructed_models_unknown_or_missing_fields_cannot_bypass_validation():
    for raw in (Record.model_construct(), Record.model_construct(value=1)):
        if "value" in raw.__dict__:
            raw.__dict__["private-sentinel"] = "private-value"
        with pytest.raises(ExecutableError):
            validate_model(raw, model_type=Record, model_types=(Record,), code="invalid_package")
    record = Record(value=1)
    record.value = object()
    with pytest.raises(ExecutableError):
        report_bytes(record, model_type=Record, model_types=(Record,), code="invalid_package")


def test_safe_issues_and_errors_are_closed_sanitized_and_truncated():
    assert len(ERROR_MESSAGES) == 30
    issue = ExecutableIssue("invalid_package", "/body/0/private-sentinel/value")
    assert issue.pointer == "/body/0/*/value"
    error = ExecutableError("invalid_package", issues=[issue] * 21)
    assert len(error.issues) == 20
    assert error.truncated is True
    assert "private" not in str(error.issues)
    assert str(error) == ERROR_MESSAGES["invalid_package"]
    with pytest.raises((TypeError, ValueError)):
        ExecutableError("private-message")
    with pytest.raises((TypeError, ValueError)):
        ExecutableError("invalid_package", truncated=1)

    class Invalid(BaseModel):
        model_config = ConfigDict(extra="forbid")
        format_version: int

    with pytest.raises(ValidationError) as caught:
        Invalid.model_validate(
            {
                "format_version": "private-value",
                **{"private-sentinel-" + str(i): "private-value" for i in range(25)},
            }
        )
    issues, truncated = safe_validation_issues(caught.value, code="invalid_package")
    assert len(issues) == 20 and truncated is True
    assert "private" not in str(issues)


def test_ascii_report_size_includes_newline_and_revalidates_without_serializers():
    class TextReport(StrictModel):
        text: str
        authority_granted: Literal[False] = False

    raw = TextReport(text="😀")
    content = report_bytes(
        raw, model_type=TextReport, model_types=(TextReport,), code="invalid_package"
    )
    assert content == b'{"text":"\\ud83d\\ude00","authority_granted":false}\n'
    assert content.isascii()
    assert (
        report_bytes(
            raw,
            model_type=TextReport,
            model_types=(TextReport,),
            code="invalid_package",
            max_bytes=len(content),
        )
        == content
    )
    with pytest.raises(ExecutableError) as raised:
        report_bytes(
            raw,
            model_type=TextReport,
            model_types=(TextReport,),
            code="invalid_package",
            max_bytes=len(content) - 1,
        )
    assert raised.value.code == "report_too_large"
    raw.authority_granted = 0
    with pytest.raises(ExecutableError):
        report_bytes(raw, model_type=TextReport, model_types=(TextReport,), code="invalid_package")
    # Known serializer models are revalidated and projected without executing serializers.
    assert (
        report_bytes(
            SerializerRecord(value=1),
            model_type=SerializerRecord,
            model_types=(SerializerRecord,),
            code="invalid_package",
        )
        == b'{"value":1}\n'
    )


@pytest.mark.parametrize("origin", ["parsed", "mutated", "constructed"])
def test_integer_fields_select_safe_domain_from_bounded_raw_integers(origin):
    raw = {"value": 9007199254740992}
    if origin == "mutated":
        raw = Record(value=1)
        raw.value = 9007199254740992
    elif origin == "constructed":
        raw = Record.model_construct(**raw)
    with pytest.raises(ExecutableError) as raised:
        validate_model(raw, model_type=Record, model_types=(Record,), code="invalid_package")
    assert raised.value.code == "invalid_package"


def test_bypassed_limits_are_revalidated_before_traversal():
    limits = JsonLimits(100, 32, 100)
    object.__setattr__(limits, "max_items", 10000000)
    with pytest.raises(ExecutableError) as raised:
        project_json([], limits=limits)
    assert raised.value.code == "invalid_json_value"


def test_default_insertion_is_counted_under_original_bounds():
    class DefaultRecord(StrictModel):
        value: int = 0
        synthetic: Literal[True] = True

    with pytest.raises(ExecutableError) as raised:
        validate_model(
            {},
            model_type=DefaultRecord,
            model_types=(DefaultRecord,),
            code="invalid_package",
            limits=JsonLimits(100, 32, 4),
        )
    assert raised.value.code == "input_too_complex"


def test_report_wrapper_and_unicode_expansion_share_final_size_cap():
    class Wrapper(StrictModel):
        ok: bool
        report: Record

    wrapper = Wrapper(ok=True, report=Record(value=1))
    content = report_bytes(
        wrapper, model_type=Wrapper, model_types=(Wrapper, Record), code="invalid_package"
    )
    assert content == (
        b'{"ok":true,"report":{"value":1,"format_version":2,'
        b'"authority_granted":false,"synthetic":true}}\n'
    )
    with pytest.raises(ExecutableError) as raised:
        report_bytes(
            wrapper,
            model_type=Wrapper,
            model_types=(Wrapper, Record),
            code="invalid_package",
            max_bytes=len(content) - 1,
        )
    assert raised.value.code == "report_too_large"

    class UnicodeReport(StrictModel):
        text: str

    with pytest.raises(ExecutableError) as raised:
        report_bytes(
            UnicodeReport(text="😀" * 700000),
            model_type=UnicodeReport,
            model_types=(UnicodeReport,),
            code="invalid_package",
        )
    assert raised.value.code == "report_too_large"


def test_issue_pointer_and_truncation_bounds_are_value_free():
    issue = ExecutableIssue("invalid_mapping", "/" + "private-sentinel/" * 1000)
    assert len(issue.pointer) <= 512
    assert "private" not in str(issue)
    assert len(ExecutableError("invalid_mapping", issues=[issue] * 20).issues) == 20
    assert ExecutableError("invalid_mapping", issues=[issue] * 20).truncated is False
    assert ExecutableError("invalid_mapping", truncated=True).truncated is True


@pytest.mark.parametrize("invalid", [True, 1.0, 9007199254740992])
def test_optional_integer_fields_keep_exact_safe_domain(invalid):
    class OptionalRecord(StrictModel):
        value: int | None = None

    assert (
        validate_model(
            {}, model_type=OptionalRecord, model_types=(OptionalRecord,), code="invalid_package"
        ).value
        is None
    )
    with pytest.raises(ExecutableError) as raised:
        validate_model(
            {"value": invalid},
            model_type=OptionalRecord,
            model_types=(OptionalRecord,),
            code="invalid_package",
        )
    assert raised.value.code == "invalid_package"


def test_decoder_counts_actual_raw_bytes_without_python_float_reserialization():
    cap = 1048576
    prefix = b'[100000000000000000000,1e-7,"'
    suffix = b'"]'
    padding = b"x" * (cap - len(prefix) - len(suffix))
    canonical = prefix + padding + suffix
    expected = [10**20, 1e-7, padding.decode()]
    assert decode_executable_bytes(canonical) == expected
    assert project_json(expected) == expected
    with pytest.raises(PackageReadError) as raised:
        decode_executable_bytes(prefix + padding + b"x" + suffix)
    assert raised.value.code == "input_too_large"
    # Raw input has its own cap even when number normalization would shrink it.
    with pytest.raises(PackageReadError) as raised:
        decode_executable_bytes(canonical.replace(b"1e-7", b"1e-07"))
    assert raised.value.code == "input_too_large"


def test_projection_bounds_structure_without_selecting_numeric_domains():
    limits = JsonLimits(7, 32, 100)
    assert project_json([1.0, "x"], limits=limits) == [1.0, "x"]
    with pytest.raises(ExecutableError) as raised:
        project_json([1.0, "xx"], limits=limits)
    assert raised.value.code == "input_too_large"
    # Unsafe integers remain raw integers before a declared field selects number.
    assert project_json(10**20, limits=JsonLimits(1, 32, 100)) == 10**20
    assert project_json(1e100, limits=JsonLimits(1, 32, 100)) == 1e100
    with pytest.raises(ExecutableError) as raised:
        jcs_bytes(1e100, max_bytes=1)
    assert raised.value.code == "input_too_large"


def test_full_cap_mixed_raw_numeric_domains_normalize_before_jcs_measurement():
    cap = 1048576
    integer_spelling = b"1" + b"0" * 100
    prefix = b"[1e20," + integer_spelling + b',"'
    suffix = b'"]'
    padding = b"x" * (cap - len(prefix) - len(suffix))
    content = prefix + padding + suffix
    assert len(content) == cap
    decoded = decode_executable_bytes(content)
    assert type(decoded[0]) is float and decoded[0] == 1e20
    assert type(decoded[1]) is int and decoded[1] == 10**100
    normalized = [float(decoded[0]), float(decoded[1]), decoded[2]]
    expected = b'[100000000000000000000,1e+100,"' + padding + suffix
    assert len(expected) == cap - 78
    assert jcs_bytes(normalized) == expected
    reread = decode_executable_bytes(expected)
    assert jcs_bytes([float(reread[0]), float(reread[1]), reread[2]]) == expected


def test_validated_numeric_defaults_count_complete_normalized_jcs_bytes():
    class NumericDefaults(StrictModel):
        value: float = 1e20
        padding: str

    cap = 1048576
    prefix = b'{"padding":"'
    suffix = b'","value":100000000000000000000}'
    padding = "x" * (cap - len(prefix) - len(suffix))
    validated = validate_model(
        {"padding": padding},
        model_type=NumericDefaults,
        model_types=(NumericDefaults,),
        code="invalid_package",
    )
    assert jcs_bytes(project_json(validated, model_types=(NumericDefaults,))) == (
        prefix + padding.encode() + suffix
    )
    with pytest.raises(ExecutableError) as raised:
        validate_model(
            {"padding": padding + "x"},
            model_type=NumericDefaults,
            model_types=(NumericDefaults,),
            code="invalid_package",
        )
    assert raised.value.code == "input_too_large"


def test_validated_number_domain_precedes_complete_normalized_byte_measurement():
    class NumberRecord(StrictModel):
        value: float
        padding: str

    cap = 1048576
    prefix = b'{"padding":"'
    suffix = b'","value":1e+100}'
    padding = "x" * (cap - len(prefix) - len(suffix))
    raw = {"value": 10**100, "padding": padding}
    validated = validate_model(
        raw, model_type=NumberRecord, model_types=(NumberRecord,), code="invalid_package"
    )
    assert type(validated.value) is float
    assert jcs_bytes(project_json(validated, model_types=(NumberRecord,))) == (
        prefix + padding.encode() + suffix
    )
    with pytest.raises(ExecutableError) as raised:
        validate_model(
            {**raw, "padding": padding + "x"},
            model_type=NumberRecord,
            model_types=(NumberRecord,),
            code="invalid_package",
        )
    assert raised.value.code == "input_too_large"


def test_report_byte_cap_counts_complete_ascii_without_a_different_numeric_representation():
    class NumericReport(StrictModel):
        value: float
        padding: str

    cap = 8388608
    prefix = b'{"value":1e+20,"padding":"'
    suffix = b'"}\n'
    padding = "x" * (cap - len(prefix) - len(suffix))
    report = NumericReport(value=1e20, padding=padding)
    expected = prefix + padding.encode() + suffix
    assert len(expected) == cap
    assert (
        report_bytes(
            report, model_type=NumericReport, model_types=(NumericReport,), code="invalid_package"
        )
        == expected
    )
    report.padding += "x"
    with pytest.raises(ExecutableError) as raised:
        report_bytes(
            report, model_type=NumericReport, model_types=(NumericReport,), code="invalid_package"
        )
    assert raised.value.code == "report_too_large"
