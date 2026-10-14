"""Bounded local regular-file loading; no references are resolved."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .contract import MAX_PACKAGE_BYTES, WorkflowPackage, validate_package
from .errors import SdkError
from .input import PackageReadError, decode_package_bytes


def read_regular(path: Path, *, max_bytes: int, private: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if private:
        # Fail closed on systems that cannot safely refuse symlinks at open.
        _require_key_platform()
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise SdkError("file_unreadable")
            if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise SdkError("unsafe_private_key")
            if info.st_size > max_bytes:
                raise SdkError("input_too_large")
            data = source.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise SdkError("input_too_large")
            return data
    except (OSError, ValueError) as error:
        if isinstance(error, SdkError):
            raise
        raise SdkError("file_unreadable") from None


def load_package(path: Path) -> WorkflowPackage:
    try:
        data = read_regular(path, max_bytes=MAX_PACKAGE_BYTES)
    except SdkError as error:
        raise PackageReadError(error.code) from None
    return validate_package(decode_package_bytes(data))


def _require_key_platform() -> None:
    if os.name != "posix" or any(
        not hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "getuid", "fchmod")
    ):
        raise SdkError("unsupported_key_platform")


def load_signing_key(path: Path) -> Ed25519PrivateKey:
    """Read at most 4096 bytes from a safe, owner-only Ed25519 PKCS8 PEM file."""
    data = read_regular(path, max_bytes=4096, private=True)
    try:
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey) or not data.startswith(
            b"-----BEGIN PRIVATE KEY-----"
        ):
            raise ValueError
        return key
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise SdkError("invalid_signing_key") from None


def write_new(path: Path, data: bytes, *, private: bool = False) -> None:
    """Exclusively create and fsync an output, retaining any failed publication.

    Once creation succeeds, failures never unlink the destination: a pathname
    might now name another writer's file. Inspect output_incomplete before retry.
    """
    if type(private) is not bool:
        raise TypeError("private must be a boolean.")
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes.")
    if private:
        _require_key_platform()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags, 0o600 if private else 0o644)
    except FileExistsError:
        raise SdkError("output_exists") from None
    except (OSError, ValueError):
        raise SdkError("file_unwritable") from None
    failed = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError
        if private:
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(data)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise OSError
            remaining = remaining[count:]
        os.fsync(descriptor)
    except OSError:
        failed = True
    finally:
        try:
            os.close(descriptor)
        except OSError:
            # Never retry a failed POSIX close: the descriptor may already have
            # been released and reassigned. Leave publication for inspection.
            failed = True
    if failed:
        raise SdkError("output_incomplete") from None


def generate_keypair(private_path: Path, public_path: Path) -> str:
    """Create a new PKCS8/SPKI pair; two-file publication is not crash-atomic."""
    _require_key_platform()
    if os.path.abspath(private_path) == os.path.abspath(public_path):
        raise SdkError("key_paths_conflict")
    for path in (private_path, public_path):
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            raise SdkError("file_unwritable") from None
        raise SdkError("output_exists")
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    try:
        write_new(private_path, private, private=True)
    except SdkError as error:
        if error.code == "output_incomplete":
            raise SdkError("keypair_incomplete") from None
        raise
    try:
        write_new(public_path, public)
    except SdkError:
        # The first destination has already been published. Preserve it and any
        # second destination, including one replaced by a concurrent writer.
        raise SdkError("keypair_incomplete") from None
    return hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
