import os
import stat
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from lotuspond_sdk import SdkError, files, generate_keypair, load_signing_key, write_new


def test_key_file_boundaries(tmp_path):
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    key_id = generate_keypair(private, public)
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert stat.S_IMODE(public.stat().st_mode) & 0o022 == 0
    key = load_signing_key(private)
    pub = serialization.load_pem_public_key(public.read_bytes())
    assert key.public_key().public_bytes_raw() == pub.public_bytes_raw()
    import hashlib

    assert key_id == hashlib.sha256(pub.public_bytes_raw()).hexdigest()
    original = private.read_bytes()
    with pytest.raises(SdkError) as error:
        generate_keypair(private, public)
    assert error.value.code == "output_exists"
    assert private.read_bytes() == original
    private.chmod(0o644)
    with pytest.raises(SdkError) as error:
        load_signing_key(private)
    assert error.value.code == "unsafe_private_key"


def test_existing_second_file_and_same_paths_are_not_clobbered(tmp_path):
    private, public = tmp_path / "private", tmp_path / "public"
    public.write_bytes(b"user-owned")
    with pytest.raises(SdkError):
        generate_keypair(private, public)
    assert not private.exists()
    assert public.read_bytes() == b"user-owned"
    with pytest.raises(SdkError) as error:
        generate_keypair(private, private)
    assert error.value.code == "key_paths_conflict"
    assert not private.exists()


@pytest.mark.parametrize("kind", ["fifo", "symlink", "directory"])
def test_nonregular_and_symlink_keys_do_not_hang_or_resolve(tmp_path, kind):
    target = tmp_path / "key"
    if kind == "fifo":
        os.mkfifo(target, 0o600)
    elif kind == "directory":
        target.mkdir()
    else:
        private, public = tmp_path / "private", tmp_path / "public"
        generate_keypair(private, public)
        target.symlink_to(private)
    # A bounded child makes FIFO hangs fail without hanging the suite.
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from lotuspond_sdk import load_signing_key, "
            "SdkError; import sys\ntry: load_signing_key(Path(sys.argv[1]))\n"
            "except SdkError: sys.exit(0)\nsys.exit(1)",
            str(target),
        ],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0
    with pytest.raises(SdkError):
        write_new(target, b"new private material", private=True)


def test_wrong_owner_is_refused(tmp_path, monkeypatch):
    private, public = tmp_path / "private", tmp_path / "public"
    generate_keypair(private, public)
    fstat = files.os.fstat

    def wrong_owner(fd):
        value = fstat(fd)
        return SimpleNamespace(st_uid=os.getuid() + 1, st_mode=value.st_mode, st_size=value.st_size)

    monkeypatch.setattr(files.os, "fstat", wrong_owner)
    with pytest.raises(SdkError) as error:
        load_signing_key(private)
    assert error.value.code == "unsafe_private_key"


@pytest.mark.parametrize(
    "data", [b"PRIVATE_SYNTHETIC_SECRET", b"x" * 4097], ids=["malformed", "over-limit"]
)
def test_malformed_and_oversized_keys_are_safe(tmp_path, data, caplog):
    path = tmp_path / "key"
    path.write_bytes(data)
    path.chmod(0o600)
    with pytest.raises(SdkError) as error:
        load_signing_key(path)
    assert error.value.code in ("invalid_signing_key", "input_too_large")
    assert "PRIVATE_SYNTHETIC_SECRET" not in str(error.value)
    assert str(path) not in str(error.value)
    assert not caplog.text
    assert error.value.__context__ is None or error.value.__suppress_context__


def test_wrong_algorithm_and_encrypted_key_are_refused(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "key"
    for encryption in (
        serialization.NoEncryption(),
        serialization.BestAvailableEncryption(b"test"),
    ):
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
            )
        )
        path.chmod(0o600)
        with pytest.raises(SdkError) as error:
            load_signing_key(path)
        assert error.value.code == "invalid_signing_key"


def test_platform_permission_enforcement_is_mandatory(tmp_path, monkeypatch):
    monkeypatch.delattr(files.os, "O_NOFOLLOW")
    for action in (
        lambda: load_signing_key(tmp_path / "key"),
        lambda: write_new(tmp_path / "key", b"private", private=True),
        lambda: generate_keypair(tmp_path / "key", tmp_path / "public"),
    ):
        with pytest.raises(SdkError) as error:
            action()
        assert error.value.code == "unsupported_key_platform"
    assert not (tmp_path / "key").exists()


def test_complete_short_writes_and_fsync(tmp_path, monkeypatch):
    original = files.os.write
    synced = []
    sync = files.os.fsync
    monkeypatch.setattr(files.os, "write", lambda fd, data: original(fd, data[:3]))

    def fsync(fd):
        synced.append(fd)
        sync(fd)

    monkeypatch.setattr(files.os, "fsync", fsync)
    path = tmp_path / "output"
    write_new(path, b"twelve bytes", private=True)
    assert path.read_bytes() == b"twelve bytes"
    assert synced


@pytest.mark.parametrize("failure", ["partial", "zero", "fsync", "second"])
def test_keypair_write_failures_preserve_outputs_for_inspection(tmp_path, monkeypatch, failure):
    private, public = tmp_path / "private", tmp_path / "public"
    original = files.os.write
    count = 0

    def failing(fd, data):
        nonlocal count
        count += 1
        if failure == "zero":
            return 0
        if failure == "partial" and count == 1:
            return original(fd, data[:3])
        if failure == "partial" or (failure == "second" and count == 2):
            raise OSError("SYNTHETIC_PRIVATE_FAILURE")
        return original(fd, data)

    monkeypatch.setattr(files.os, "write", failing)
    if failure == "fsync":

        def bad_sync(fd):
            raise OSError("SYNTHETIC_PRIVATE_FAILURE")

        monkeypatch.setattr(files.os, "fsync", bad_sync)
    with pytest.raises(SdkError) as error:
        generate_keypair(private, public)
    assert error.value.code == "keypair_incomplete"
    assert private.exists()
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert public.exists() == (failure == "second")
    assert "SYNTHETIC_PRIVATE_FAILURE" not in str(error.value)


def test_second_creation_collision_preserves_user_file(tmp_path, monkeypatch):
    private, public = tmp_path / "private", tmp_path / "public"
    original = files.os.open

    def collide(path, flags, *args, **kwargs):
        if path == public:
            public.write_bytes(b"user file")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(files.os, "open", collide)
    with pytest.raises(SdkError) as error:
        generate_keypair(private, public)
    assert error.value.code == "keypair_incomplete"
    assert private.exists()
    assert public.read_bytes() == b"user file"


@pytest.mark.parametrize("replace", [False, True])
def test_keypair_failure_never_unlinks_and_preserves_replacement(tmp_path, monkeypatch, replace):
    private, public = tmp_path / "private", tmp_path / "public"
    original_write = files.os.write
    count = 0

    def fail_second(fd, data):
        nonlocal count
        count += 1
        if count == 2:
            if replace:
                private.unlink()
                private.write_bytes(b"user replacement")
            raise OSError("synthetic second failure")
        return original_write(fd, data)

    monkeypatch.setattr(files.os, "write", fail_second)
    original_unlink = files.os.unlink

    def refuse_cleanup(path, *args, **kwargs):
        # Only the injected concurrent actor may replace the private pathname.
        if replace and count == 2 and path == private:
            return original_unlink(path, *args, **kwargs)
        raise AssertionError("Publication failure must never unlink an output.")

    monkeypatch.setattr(files.os, "unlink", refuse_cleanup)
    with pytest.raises(SdkError) as error:
        generate_keypair(private, public)
    assert error.value.code == "keypair_incomplete"
    assert private.exists()
    if replace:
        assert private.read_bytes() == b"user replacement"


def test_key_read_exact_byte_limit_and_umask(tmp_path):
    private, public = tmp_path / "private", tmp_path / "public"
    original_umask = os.umask(0o777)
    try:
        generate_keypair(private, public)
    finally:
        os.umask(original_umask)
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert stat.S_IMODE(public.stat().st_mode) == 0
    key = load_signing_key(private)
    data = private.read_bytes()
    private.write_bytes(data + b"\n" * (4096 - len(data)))
    assert load_signing_key(private).private_bytes_raw() == key.private_bytes_raw()
    private.write_bytes(private.read_bytes() + b"\n")
    with pytest.raises(SdkError) as error:
        load_signing_key(private)
    assert error.value.code == "input_too_large"


def test_standalone_partial_write_preserves_output_for_inspection(tmp_path, monkeypatch):
    path = tmp_path / "private"
    original_write = files.os.write
    count = 0

    def fail_after_partial(fd, data):
        nonlocal count
        count += 1
        if count == 1:
            return original_write(fd, data[:5])
        raise OSError("synthetic write failure")

    def bad_unlink(path):
        raise AssertionError("Publication failure must never unlink an output.")

    monkeypatch.setattr(files.os, "write", fail_after_partial)
    monkeypatch.setattr(files.os, "unlink", bad_unlink)
    with pytest.raises(SdkError) as error:
        write_new(path, b"synthetic-only", private=True)
    assert error.value.code == "output_incomplete"
    assert path.read_bytes() == b"synth"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_private_read_does_not_follow_a_swapped_symlink(tmp_path, monkeypatch):
    private, public = tmp_path / "private", tmp_path / "public"
    generate_keypair(private, public)
    target = tmp_path / "target"
    target.write_bytes(private.read_bytes())
    target.chmod(0o600)
    original_open = files.os.open

    def swapped(path, flags, *args, **kwargs):
        if path == private:
            private.unlink()
            private.symlink_to(target)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(files.os, "open", swapped)
    with pytest.raises(SdkError):
        load_signing_key(private)


def test_descriptor_close_failure_is_fixed_and_requires_inspection(tmp_path, monkeypatch):
    path = tmp_path / "private"
    original = files.os.close

    def fail_close(fd):
        original(fd)
        raise OSError("SYNTHETIC_PRIVATE_CLOSE_FAILURE")

    monkeypatch.setattr(files.os, "close", fail_close)
    with pytest.raises(SdkError) as error:
        write_new(path, b"synthetic-only", private=True)
    assert error.value.code == "output_incomplete"
    assert path.read_bytes() == b"synthetic-only"
    assert "SYNTHETIC_PRIVATE_CLOSE_FAILURE" not in str(error.value)


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_write_private_flag_requires_boolean(tmp_path, value):
    path = tmp_path / "output"
    with pytest.raises(TypeError):
        write_new(path, b"synthetic-only", private=value)
    assert not path.exists()


def test_encrypted_ed25519_private_key_is_refused(tmp_path):
    from test_signing import synthetic_key

    path = tmp_path / "private"
    path.write_bytes(
        synthetic_key().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"synthetic-test-password"),
        )
    )
    path.chmod(0o600)
    with pytest.raises(SdkError) as error:
        load_signing_key(path)
    assert error.value.code == "invalid_signing_key"
    assert "synthetic-test-password" not in str(error.value)
