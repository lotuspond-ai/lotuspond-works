import os
import subprocess
import sys

import pytest

from lotuspond_sdk import WorkflowPackage
from lotuspond_sdk.errors import SdkError
from lotuspond_sdk.files import load_package, read_regular
from lotuspond_sdk.input import PackageReadError


def test_load_regular_package(tmp_path, golden_bytes):
    path = tmp_path / "package.json"
    path.write_bytes(golden_bytes)
    assert isinstance(load_package(path), WorkflowPackage)
    assert read_regular(path, max_bytes=len(golden_bytes)) == golden_bytes
    with pytest.raises(SdkError) as raised:
        read_regular(path, max_bytes=1)
    assert raised.value.code == "input_too_large"


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_nonregular_does_not_hang_or_echo_path(tmp_path, kind):
    path = tmp_path / "sensitive-path"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("Named pipes unavailable")
        os.mkfifo(path)
    result = subprocess.run(
        [sys.executable, "-m", "lotuspond_sdk", "validate", str(path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "file_unreadable" in result.stdout
    assert str(path) not in result.stdout + result.stderr
    assert result.stderr == ""
    with pytest.raises(PackageReadError):
        load_package(path)


def test_private_reads_refuse_symlinks_and_permissions(tmp_path):
    path = tmp_path / "key"
    path.write_bytes(b"private")
    path.chmod(0o600)
    assert read_regular(path, max_bytes=20, private=True) == b"private"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(SdkError):
        read_regular(link, max_bytes=20, private=True)
    path.chmod(0o644)
    with pytest.raises(SdkError):
        read_regular(path, max_bytes=20, private=True)
