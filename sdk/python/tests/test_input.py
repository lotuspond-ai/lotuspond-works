import pytest
from pydantic import ValidationError

from lotuspond_sdk import validate_package
from lotuspond_sdk.input import PackageReadError, decode_package_bytes, safe_validation_errors


@pytest.mark.parametrize(
    "data,code",
    [
        (b'{"x":1,"x":2}', "duplicate_key"),
        (b'{"x":{"y":1,"y":2}}', "duplicate_key"),
        (b"\xff", "invalid_utf8"),
        (b"{", "invalid_json"),
        (b" " * 1048577, "input_too_large"),
        (b"[" * 33 + b"0" + b"]" * 33, "input_too_deep"),
        (b"NaN", "nonfinite_number"),
        (b"1e999", "nonfinite_number"),
        (b"9" * 20, "number_too_large"),
    ],
)
def test_bounded_decoder(data, code):
    with pytest.raises(PackageReadError) as raised:
        decode_package_bytes(data)
    assert raised.value.code == code


def test_escaped_invalid_unicode_is_rejected():
    with pytest.raises(ValidationError) as raised:
        validate_package(decode_package_bytes(b'{"name":"\\ud800"}'))
    assert safe_validation_errors(raised.value)[0]["code"] == "invalid_unicode"


def test_error_output_is_bounded_and_value_free(example):
    example.update({"sensitive-marker-" + str(i): "private-value" for i in range(40)})
    with pytest.raises(ValidationError) as raised:
        validate_package(example)
    errors = safe_validation_errors(raised.value)
    assert len(errors) == 20
    assert "sensitive-marker" not in str(errors)
    assert "private-value" not in str(errors)
