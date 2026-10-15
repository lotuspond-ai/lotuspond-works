"""Literal RFC answers and a separately executed ECMAScript numeric oracle."""

import json
import struct
import subprocess
from pathlib import Path

import pytest

from lotuspond_sdk.executable import CANONICALIZATION, SCHEMA_VERSION, ExecutableError
from lotuspond_sdk.executable._canonical import jcs_bytes, sha256_hex
from lotuspond_sdk.executable.input import decode_executable_bytes

ROOT = Path(__file__).resolve().parents[4]
ORACLE = ROOT / "scripts/checks/jcs-oracle.mjs"
VECTORS = Path(__file__).parents[1] / "fixtures/executable-v2/jcs-numbers.json"
NUMBERS = json.loads(VECTORS.read_text())


@pytest.mark.parametrize("hex_bits,expected", NUMBERS)
def test_rfc8785_appendix_b(hex_bits, expected):
    number = struct.unpack(">d", bytes.fromhex(hex_bits))[0]
    if expected is None:
        with pytest.raises(ExecutableError) as raised:
            jcs_bytes(number)
        assert raised.value.code == "nonfinite_number"
    else:
        assert jcs_bytes(number) == expected.encode("ascii")
        # Integral output outside safe integers is still readable before field typing.
        decoded = decode_executable_bytes(expected.encode("ascii"))
        assert jcs_bytes(float(decoded)) == expected.encode("ascii")


def test_jcs_numeric_and_utf16_order():
    assert jcs_bytes({"z": -0.0, "a": 1.0}) == b'{"a":1,"z":0}'
    assert jcs_bytes({"\U00010000": 1, "\ue000": 2}).decode() == '{"𐀀":1,"":2}'
    assert jcs_bytes({"2": 2, "10": 10, "a": 0}) == b'{"10":10,"2":2,"a":0}'
    assert jcs_bytes([9007199254740991, -9007199254740991]) == (
        b"[9007199254740991,-9007199254740991]"
    )
    for number in (9007199254740992, -9007199254740992):
        with pytest.raises(ExecutableError) as raised:
            jcs_bytes(number)
        assert raised.value.code == "safe_integer"


def test_rfc_section_322_complete_literal():
    raw = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27],
        "string": '\u20ac$\x0f\nA\'B"\\"/',
        "literals": [None, True, False],
    }
    assert jcs_bytes(raw) == (
        b'{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        b'"string":"\xe2\x82\xac$\\u000f\\nA\'B\\"\\\\\\"/"}'
    )


def test_no_unicode_normalization_and_array_order_is_significant():
    assert jcs_bytes(["é", "e\u0301"]) == b'["\xc3\xa9","e\xcc\x81"]'
    assert jcs_bytes(["é", "e\u0301"]) != jcs_bytes(["e\u0301", "é"])
    assert jcs_bytes({"\r": 0, "1": 1, "\u0080": 2, "ö": 3, "€": 4, "😀": 5, "דּ": 6}).decode() == (
        '{"\\r":0,"1":1,"\u0080":2,"ö":3,"€":4,"😀":5,"דּ":6}'
    )


@pytest.mark.parametrize("cap", [True, 0, -1, 1.0, "2", 8388609])
def test_byte_cap_requires_exact_supported_integer(cap):
    with pytest.raises(ExecutableError) as raised:
        jcs_bytes(0, max_bytes=cap)
    assert raised.value.code == "invalid_json_value"


def test_byte_cap_applies_to_jcs_output_not_python_spelling():
    assert jcs_bytes(1.0, max_bytes=1) == b"1"
    assert jcs_bytes({"a": 0}, max_bytes=7) == b'{"a":0}'
    with pytest.raises(ExecutableError) as raised:
        jcs_bytes({"a": 0}, max_bytes=6)
    assert raised.value.code == "input_too_large"
    assert len(jcs_bytes("x" * 1048574)) == 1048576
    with pytest.raises(ExecutableError):
        jcs_bytes("x" * 1048575)


def test_package_item_cap_is_not_raised_to_report_limit():
    with pytest.raises(ExecutableError) as raised:
        jcs_bytes([0] * 10000)
    assert raised.value.code == "input_too_complex"
    assert len(jcs_bytes([0] * 10000, max_bytes=1048577)) == 20001
    assert len(jcs_bytes("x" * 8388606, max_bytes=8388608)) == 8388608
    with pytest.raises(ExecutableError) as raised:
        jcs_bytes("x" * 8388607, max_bytes=8388608)
    assert raised.value.code == "report_too_large"


def test_independent_node_oracle():
    result = subprocess.run(
        ["node", ORACLE, "--verify", VECTORS],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert "26 RFC 8785 numeric vectors" in result.stdout
    cases = [{"10": 10, "2": 2, "😀": -0.0, "דּ": 1e20}, ["é", "e\u0301", 1e-6, 1e-7]]
    cases += [
        struct.unpack(">d", bytes.fromhex(bits))[0]
        for bits, expected in NUMBERS
        if expected is not None
    ]
    for case in cases:
        oracle = subprocess.run(
            ["node", ORACLE],
            input=json.dumps(case).encode(),
            capture_output=True,
            check=True,
            timeout=15,
        )
        assert oracle.stdout == jcs_bytes(case)


def test_identifiers_and_digest_do_not_replace_v1():
    from lotuspond_sdk import canonical_package_json, package_digest, validate_package
    from lotuspond_sdk.metadata import CANONICALIZATION as V1_CANONICALIZATION

    assert SCHEMA_VERSION == "lotuspond.workflow-package.v2"
    assert CANONICALIZATION == "lotuspond.workflow-package.jcs.v2"
    assert V1_CANONICALIZATION == "lotuspond.workflow-package.canonical-json.v1"
    raw = json.loads((VECTORS.parent.parent / "reviewed-action.canonical.json").read_bytes())
    canonical = canonical_package_json(validate_package(raw))
    assert len(canonical) == 1166
    assert package_digest(raw) == "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_full_cap_numeric_jcs_remains_readable_after_declared_number_normalization():
    cap = 1048576
    prefix = b'[100000000000000000000,1e-7,"'
    suffix = b'"]'
    padding = "x" * (cap - len(prefix) - len(suffix))
    expected = prefix + padding.encode() + suffix
    canonical = jcs_bytes([1e20, 1e-7, padding])
    assert canonical == expected
    assert len(canonical) == cap
    reread = decode_executable_bytes(canonical)
    assert type(reread[0]) is int and reread[0] == 10**20
    # Declared number fields normalize bounded JSON int/float spellings to float.
    normalized = [float(reread[0]), float(reread[1]), reread[2]]
    assert jcs_bytes(normalized) == expected


@pytest.mark.parametrize("number,spelling", [(1.0, b"1"), (-0.0, b"0"), (1e-7, b"1e-7")])
def test_full_cap_jcs_counts_actual_numeric_spelling_and_one_byte_overflow(number, spelling):
    cap = 1048576
    prefix = b"[" + spelling + b',"'
    suffix = b'"]'
    padding = "x" * (cap - len(prefix) - len(suffix))
    expected = prefix + padding.encode() + suffix
    assert jcs_bytes([number, padding]) == expected
    assert jcs_bytes([number, padding[:-1]]) == prefix + padding[:-1].encode() + suffix
    with pytest.raises(ExecutableError) as raised:
        jcs_bytes([number, padding + "x"])
    assert raised.value.code == "input_too_large"
