"""Verify a Lotuspond airgap archive without extracting it.

The verification host requires Python 3. The maximum supported compressed
archive and total regular-file payload are each 10 GiB. The accepted archive
format is gzip-compressed POSIX USTAR; extended metadata is rejected.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_BUNDLE_BYTES = 10 * 1024 * 1024 * 1024
MAX_TAR_OVERHEAD_BYTES = 64 * 1024 * 1024
MAX_TAR_STREAM_BYTES = MAX_BUNDLE_BYTES + MAX_TAR_OVERHEAD_BYTES
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MEMBERS = 10_000
MAX_PATH_BYTES = 1024
READ_SIZE = 1024 * 1024
BLOCK_SIZE = 512
MANIFEST_NAME = "MANIFEST.sha256"
ROOT_PATTERNS = {
    "airgap": re.compile(r"lotuspond-airgap-[A-Za-z0-9._-]+\Z"),
    "execution": re.compile(r"lotuspond-execution-[A-Za-z0-9._-]+\Z"),
}
PATH_PATTERN = re.compile(r"[A-Za-z0-9._/-]+\Z")
MANIFEST_LINE = re.compile(r"([0-9a-f]{64})  \./([A-Za-z0-9._/-]+)\Z")
CHECKSUM = re.compile(r"([0-9a-fA-F]{64})\n?\Z")


class VerificationError(Exception):
    """A safe, user-facing bundle verification failure."""


@dataclass(frozen=True)
class VerificationResult:
    root: str
    members: int
    regular_files: int
    payload_bytes: int
    archive_sha256: str


class _CompressedReader:
    """Hash and bound the exact compressed bytes consumed by gzip."""

    def __init__(self, source):
        self.source = source
        self.bytes_read = 0
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        remaining = MAX_BUNDLE_BYTES - self.bytes_read
        requested = remaining + 1 if size < 0 else min(size, remaining + 1)
        data = self.source.read(requested)
        self.bytes_read += len(data)
        if self.bytes_read > MAX_BUNDLE_BYTES:
            raise VerificationError("bundle exceeds the 10 GiB compressed-size limit")
        self.digest.update(data)
        return data


class _DecompressedReader:
    """Bound all decompressed tar bytes, including headers and trailing padding."""

    def __init__(self, source):
        self.source = source
        self.bytes_read = 0

    def read(self, size=-1):
        remaining = MAX_TAR_STREAM_BYTES - self.bytes_read
        requested = remaining + 1 if size < 0 else min(size, remaining + 1)
        data = self.source.read(requested)
        self.bytes_read += len(data)
        if self.bytes_read > MAX_TAR_STREAM_BYTES:
            raise VerificationError("bundle exceeds the decompressed tar-stream limit")
        return data


def _hash_compressed(source) -> str:
    reader = _CompressedReader(source)
    while reader.read(READ_SIZE):
        pass
    source.seek(0)
    return reader.digest.hexdigest()


def _regular_file(path: Path, label: str) -> None:
    try:
        if not path.is_file():
            raise VerificationError(f"{label} must be a regular file")
    except OSError as error:
        raise VerificationError(f"cannot inspect {label}") from error


def _expected_digest(checksum_path: Path) -> str:
    try:
        if checksum_path.stat().st_size > 1024:
            raise VerificationError("checksum file is too large")
        raw = checksum_path.read_text(encoding="ascii")
    except VerificationError:
        raise
    except (OSError, UnicodeError) as error:
        raise VerificationError("cannot read checksum file") from error
    match = CHECKSUM.fullmatch(raw)
    if not match:
        raise VerificationError("checksum file must contain one SHA-256 hex digest")
    return match.group(1).lower()


def _safe_path(raw_name: str, *, directory: bool = False) -> str:
    name = raw_name[:-1] if directory and raw_name.endswith("/") else raw_name
    if not name or len(name.encode("utf-8", errors="ignore")) > MAX_PATH_BYTES:
        raise VerificationError("bundle contains an invalid archive path")
    if "\\" in name or not PATH_PATTERN.fullmatch(name) or name.startswith("/") or "//" in name:
        raise VerificationError("bundle contains an unsafe archive path")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise VerificationError("bundle contains an unsafe archive path")
    return "/".join(parts)


def _read_exact(stream, size: int, message: str) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise VerificationError(message)
        chunks.extend(chunk)
    return bytes(chunks)


def _octal(field: bytes, label: str) -> int:
    value = field.rstrip(b"\0 ").lstrip(b" ")
    if not value or any(byte not in b"01234567" for byte in value):
        raise VerificationError(f"bundle contains an invalid USTAR {label}")
    return int(value, 8)


def _header_path(header: bytes, directory: bool) -> str:
    try:
        name = header[0:100].split(b"\0", 1)[0].decode("ascii")
        prefix = header[345:500].split(b"\0", 1)[0].decode("ascii")
    except UnicodeError as error:
        raise VerificationError("bundle contains a non-ASCII USTAR path") from error
    raw = f"{prefix}/{name}" if prefix else name
    return _safe_path(raw, directory=directory)


def _verify_header(header: bytes) -> tuple[str, int, bool]:
    if header[257:263] != b"ustar\0" or header[263:265] != b"00":
        raise VerificationError("bundle must use POSIX USTAR without extended metadata")
    stored_checksum = _octal(header[148:156], "header checksum")
    calculated_checksum = sum(header[:148]) + (8 * 32) + sum(header[156:])
    if stored_checksum != calculated_checksum:
        raise VerificationError("bundle contains an invalid USTAR header checksum")
    member_type = header[156:157]
    if member_type not in {b"\0", b"0", b"5"}:
        raise VerificationError("bundle contains a link or unsupported member type")
    directory = member_type == b"5"
    size = _octal(header[124:136], "member size")
    if directory and size:
        raise VerificationError("bundle contains an invalid directory member")
    if header[157:257].split(b"\0", 1)[0]:
        raise VerificationError("bundle contains an unexpected link target")
    return _header_path(header, directory), size, directory


def _hash_member(stream, declared_size: int, *, capture: bool) -> tuple[str, bytes | None]:
    digest = hashlib.sha256()
    captured = bytearray() if capture else None
    remaining = declared_size
    while remaining:
        chunk = stream.read(min(READ_SIZE, remaining))
        if not chunk:
            raise VerificationError("bundle contains a truncated file member")
        remaining -= len(chunk)
        digest.update(chunk)
        if captured is not None:
            captured.extend(chunk)
    padding_size = (-declared_size) % BLOCK_SIZE
    if padding_size:
        padding = _read_exact(stream, padding_size, "bundle contains truncated member padding")
        if any(padding):
            raise VerificationError("bundle contains non-zero member padding")
    return digest.hexdigest(), bytes(captured) if captured is not None else None


def _manifest_entries(manifest: bytes, expected_files: set[str]) -> dict[str, str]:
    try:
        text = manifest.decode("utf-8")
    except UnicodeError as error:
        raise VerificationError("manifest must be valid UTF-8") from error
    if not text.endswith("\n"):
        raise VerificationError("manifest must end with a newline")
    entries: dict[str, str] = {}
    order: list[str] = []
    for line in text.splitlines():
        match = MANIFEST_LINE.fullmatch(line)
        if not match:
            raise VerificationError("manifest contains an invalid entry")
        relative = _safe_path(match.group(2))
        if relative == MANIFEST_NAME or relative in entries:
            raise VerificationError("manifest contains a duplicate or forbidden path")
        entries[relative] = match.group(1)
        order.append(relative)
    if order != sorted(order):
        raise VerificationError("manifest entries must be sorted")
    if set(entries) != expected_files:
        raise VerificationError("manifest does not list every regular file exactly once")
    return entries


def verify_bundle(
    archive: Path | str, checksum_path: Path | str, *, kind: str = "airgap"
) -> VerificationResult:
    archive = Path(archive)
    checksum_path = Path(checksum_path)
    root_pattern = ROOT_PATTERNS.get(kind)
    if root_pattern is None:
        raise VerificationError("bundle kind is not supported")
    _regular_file(archive, "bundle archive")
    _regular_file(checksum_path, "checksum file")
    expected_archive_digest = _expected_digest(checksum_path)

    seen: set[str] = set()
    roots: set[str] = set()
    directories: set[str] = set()
    file_digests: dict[str, str] = {}
    manifest: bytes | None = None
    member_count = 0
    payload_bytes = 0
    zero_blocks = 0
    try:
        with archive.open("rb") as raw:
            if _hash_compressed(raw) != expected_archive_digest:
                raise VerificationError("bundle checksum mismatch")
            compressed = _CompressedReader(raw)
            with gzip.GzipFile(fileobj=compressed, mode="rb") as gzip_stream:
                stream = _DecompressedReader(gzip_stream)
                while zero_blocks < 2:
                    header = _read_exact(stream, BLOCK_SIZE, "bundle contains a truncated USTAR header")
                    if not any(header):
                        zero_blocks += 1
                        continue
                    if zero_blocks:
                        raise VerificationError("bundle contains data after a USTAR end marker")
                    path, size, directory = _verify_header(header)
                    member_count += 1
                    if member_count > MAX_MEMBERS:
                        raise VerificationError("bundle exceeds the member-count limit")
                    if path in seen:
                        raise VerificationError("bundle contains a duplicate archive path")
                    seen.add(path)
                    root = path.split("/", 1)[0]
                    roots.add(root)
                    if len(roots) > 1:
                        raise VerificationError("bundle must contain exactly one top-level root")
                    if directory:
                        directories.add(path)
                        continue
                    if size > MAX_BUNDLE_BYTES:
                        raise VerificationError("bundle contains an oversized file member")
                    payload_bytes += size
                    if payload_bytes > MAX_BUNDLE_BYTES:
                        raise VerificationError("bundle exceeds the 10 GiB payload limit")
                    is_manifest = path == f"{root}/{MANIFEST_NAME}"
                    if is_manifest and size > MAX_MANIFEST_BYTES:
                        raise VerificationError("manifest exceeds the 1 MiB limit")
                    digest, captured = _hash_member(stream, size, capture=is_manifest)
                    file_digests[path] = digest
                    if is_manifest:
                        manifest = captured
                while trailing := stream.read(READ_SIZE):
                    if any(trailing):
                        raise VerificationError("bundle contains non-zero data after the USTAR end marker")
            archive_digest = compressed.digest.hexdigest()
            if compressed.bytes_read != raw.seek(0, 2):
                raise VerificationError("bundle changed or was not fully read during verification")
    except VerificationError:
        raise
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise VerificationError("bundle is not a complete gzip USTAR archive") from error

    if archive_digest != expected_archive_digest:
        raise VerificationError("bundle checksum mismatch")
    if len(roots) != 1:
        raise VerificationError("bundle must contain exactly one top-level root")
    root = next(iter(roots))
    if not root_pattern.fullmatch(root) or root not in directories:
        raise VerificationError("bundle top-level root is invalid or missing its directory entry")
    manifest_path = f"{root}/{MANIFEST_NAME}"
    if manifest is None or manifest_path not in file_digests:
        raise VerificationError("bundle is missing its root manifest")
    relative_digests = {
        path.removeprefix(f"{root}/"): digest
        for path, digest in file_digests.items()
        if path != manifest_path
    }
    entries = _manifest_entries(manifest, set(relative_digests))
    for relative, digest in relative_digests.items():
        if entries[relative] != digest:
            raise VerificationError(f"manifest digest mismatch for {relative}")
    return VerificationResult(
        root=root,
        members=member_count,
        regular_files=len(file_digests),
        payload_bytes=payload_bytes,
        archive_sha256=archive_digest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=tuple(ROOT_PATTERNS),
        default="airgap",
        help="airgap service bundle or customer execution bundle",
    )
    parser.add_argument("archive", help="bundle.tar.gz (maximum 10 GiB)")
    parser.add_argument("checksum", help="trusted-channel file containing only the archive SHA-256")
    args = parser.parse_args(argv)
    try:
        result = verify_bundle(args.archive, args.checksum, kind=args.kind)
    except VerificationError as error:
        print(f"bundle verification failed: {error}", file=sys.stderr)
        return 1
    print(
        f"verified {args.archive} without extraction; root={result.root}; "
        f"files={result.regular_files}; inspect {result.root}/INSTALL.md after controlled extraction"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
