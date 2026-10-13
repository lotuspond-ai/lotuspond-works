import importlib

import pytest


@pytest.mark.parametrize(
    "data,code",
    [
        (b'{"private":1,"private":2}', "duplicate_key"),
        (b'{"private":NaN}', "nonfinite_number"),
        (b"[" * 40 + b"0" + b"]" * 40, "input_too_deep"),
        (b"x" * (1024 * 1024 + 1), "input_too_large"),
        (b"\xff", "invalid_utf8"),
        (b"{", "invalid_json"),
    ],
    ids=["duplicate", "nonfinite", "depth", "size", "utf8", "json"],
)
def test_shared_byte_decoder_rejects_safely(data, code):
    from lotuspond import package_cli

    decoder = importlib.import_module("lotuspond.package_input")
    with pytest.raises(package_cli.PackageReadError) as error:
        decoder.decode_package_bytes(data)
    assert error.value.code == code
    assert "private" not in str(error.value)
