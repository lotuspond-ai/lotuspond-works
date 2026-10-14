import importlib
import os
import subprocess
import sys

import pytest

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import CredentialState

UUID = "01234567-89ab-cdef-0123-456789abcdef"
C = "lpr_" + "A" * 43
P = "lpr_" + "B" * 42 + "A"


def state_module():
    assert importlib.util.find_spec("lotuspond_runtime.state"), "missing private durable state"
    return importlib.import_module("lotuspond_runtime.state")


def credential(revision=0, pending=False):
    return CredentialState(
        revision=revision,
        instance_id=UUID,
        active_token=C,
        pending_token=P if pending else None,
        pending_version=2 if pending else None,
    )


def private_dir(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    return directory


def test_private_state_roundtrip_cas_lock(tmp_path):
    module = state_module()
    directory = private_dir(tmp_path)
    with module.CredentialStore(directory) as store:
        store.save(None, credential())
        assert store.load() == credential()
        with pytest.raises(FleetError):
            module.CredentialStore(directory)
        store.save(0, credential(1, True))
        assert store.load() == credential(1, True)
    assert (directory / "state.json").stat().st_mode & 0o777 == 0o600
    assert C.encode() in (directory / "state.json").read_bytes()


@pytest.mark.parametrize(
    "boundary", ["temp_created", "written", "file_fsynced", "renamed", "directory_fsynced"]
)
def test_state_crash_points(tmp_path, boundary):
    module = state_module()
    directory = private_dir(tmp_path)
    with module.CredentialStore(directory) as store:
        store.save(None, credential())
    script = """
import os, sys
from pathlib import Path
from lotuspond_runtime.state import CredentialStore
from lotuspond_runtime.protocol import CredentialState
store = CredentialStore(Path(sys.argv[1]))
old = store.load()
def crash(point):
    if point == sys.argv[2]: os._exit(71)
store._checkpoint = crash
store.save(0, CredentialState(revision=1, instance_id=old.instance_id,
 active_token=old.active_token, pending_token='lpr_' + 'B'*42 + 'A', pending_version=2))
raise AssertionError('did not interrupt')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(directory), boundary], capture_output=True
    )
    assert result.returncode == 71, result.stderr.decode()
    with module.CredentialStore(directory) as restarted:
        assert restarted.load() in (credential(), credential(1, True))
    assert any(p.name.startswith(".state-") for p in directory.iterdir()) == (
        boundary in ("temp_created", "written", "file_fsynced")
    )


@pytest.mark.parametrize(
    "unsafe", ["directory_mode", "state_mode", "symlink", "hardlink", "oversize", "ancestor"]
)
def test_unsafe_storage_refused(tmp_path, unsafe):
    module = state_module()
    directory = private_dir(tmp_path)
    with module.CredentialStore(directory) as store:
        store.save(None, credential())
    target = directory / "state.json"
    if unsafe == "directory_mode":
        directory.chmod(0o750)
    elif unsafe == "state_mode":
        target.chmod(0o640)
    elif unsafe == "symlink":
        target.rename(directory / "old")
        target.symlink_to(directory / "old")
    elif unsafe == "hardlink":
        os.link(target, directory / "alias")
    elif unsafe == "oversize":
        target.write_bytes(b"x" * 8193)
    else:
        tmp_path.chmod(0o777)
    with pytest.raises(FleetError) as error:
        with module.CredentialStore(directory) as store:
            store.load()
    assert error.value.code == "unsafe_storage"


@pytest.mark.parametrize(
    "expected,next_state", [(None, credential()), (2, credential(3)), (0, credential(0))]
)
def test_revision_conflicts_fence_store(tmp_path, expected, next_state):
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        with pytest.raises(FleetError):
            store.save(expected, next_state)
        with pytest.raises(FleetError):
            store.load()


@pytest.mark.parametrize("operation", ["write", "file_fsync", "rename", "directory_fsync"])
def test_real_syscall_failure_never_reports_durable_success(tmp_path, monkeypatch, operation):
    module = state_module()
    directory = private_dir(tmp_path)
    with module.CredentialStore(directory) as store:
        store.save(None, credential())
        original_write, original_fsync, original_rename = os.write, os.fsync, os.rename

        def fail_write(fd, data):
            # A partial write followed by an actual failed write leaves old state intact.
            if operation == "write":
                original_write(fd, data[:3])
                raise OSError("synthetic")
            return original_write(fd, data)

        def fail_fsync(fd):
            is_directory = fd == store._directory
            if operation == ("directory_fsync" if is_directory else "file_fsync"):
                raise OSError("synthetic")
            return original_fsync(fd)

        def fail_rename(*args, **kwargs):
            if operation == "rename":
                raise OSError("synthetic")
            return original_rename(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(module.os, "write", fail_write)
            patch.setattr(module.os, "fsync", fail_fsync)
            patch.setattr(module.os, "rename", fail_rename)
            with pytest.raises(FleetError) as error:
                store.save(0, credential(1, True))
            assert str(error.value) == "unsafe_storage"
            with pytest.raises(FleetError):
                store.load()
    with module.CredentialStore(directory) as restarted:
        assert restarted.load() in (credential(), credential(1, True))


def test_store_directory_is_pinned_and_lock_excludes_other_process(tmp_path):
    directory = private_dir(tmp_path)
    with state_module().CredentialStore(directory) as store:
        store.save(None, credential())
        script = """
import sys
from pathlib import Path
from lotuspond_runtime.state import CredentialStore
from lotuspond_runtime.errors import FleetError
try: CredentialStore(Path(sys.argv[1]))
except FleetError: raise SystemExit(4)
raise SystemExit(99)
"""
        result = subprocess.run([sys.executable, "-c", script, str(directory)], capture_output=True)
        assert result.returncode == 4
        pinned = directory.with_name("pinned")
        directory.rename(pinned)
        directory.mkdir(mode=0o700)
        store.save(0, credential(1, True))
        assert not (directory / "state.json").exists()
        assert P.encode() in (pinned / "state.json").read_bytes()
