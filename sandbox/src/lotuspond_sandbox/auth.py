from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .manifest import ManifestError

GRANT_SCHEMA = "lotuspond.sandbox.grant.v1"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_MAC = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RunGrant:
    schema_version: str
    run_id: str
    manifest_id: str
    environment_id: str
    task_id: str
    steps: int
    seed: int
    expires_at: int
    nonce: str
    mac: str


def load_auth_key() -> bytes:
    raw = os.environ.get("LOTUSPOND_SANDBOX_AUTH_KEY", "")
    if not re.fullmatch(r"[0-9a-f]{64,128}", raw):
        raise ManifestError(
            "LOTUSPOND_SANDBOX_AUTH_KEY must be 32 through 64 bytes of lowercase hex"
        )
    return bytes.fromhex(raw)


def _payload(grant: RunGrant | dict[str, Any]) -> bytes:
    values = asdict(grant) if isinstance(grant, RunGrant) else dict(grant)
    values.pop("mac", None)
    return json.dumps(
        values, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def issue_grant(
    *,
    key: bytes,
    run_id: str,
    manifest_id: str,
    environment_id: str,
    task_id: str,
    steps: int,
    seed: int,
    expires_at: int,
    nonce: str,
) -> RunGrant:
    _validate_key(key)
    values: dict[str, Any] = {
        "schema_version": GRANT_SCHEMA,
        "run_id": run_id,
        "manifest_id": manifest_id,
        "environment_id": environment_id,
        "task_id": task_id,
        "steps": steps,
        "seed": seed,
        "expires_at": expires_at,
        "nonce": nonce,
    }
    _validate_values(values, now=int(time.time()), allow_future=True)
    return RunGrant(
        **values, mac=hmac.new(key, _payload(values), hashlib.sha256).hexdigest()
    )


def parse_grant(value: Any) -> RunGrant:
    expected = {
        "schema_version",
        "run_id",
        "manifest_id",
        "environment_id",
        "task_id",
        "steps",
        "seed",
        "expires_at",
        "nonce",
        "mac",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ManifestError("grant fields are invalid")
    _validate_values(value, now=int(time.time()), allow_future=False)
    if not isinstance(value["mac"], str) or not _MAC.fullmatch(value["mac"]):
        raise ManifestError("grant MAC is invalid")
    return RunGrant(**value)


def verify_grant(grant: RunGrant, key: bytes) -> None:
    _validate_key(key)
    expected = hmac.new(key, _payload(grant), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, grant.mac):
        raise ManifestError("grant authentication failed")
    _validate_values(asdict(grant), now=int(time.time()), allow_future=False)


def _validate_values(values: dict[str, Any], *, now: int, allow_future: bool) -> None:
    if values.get("schema_version") != GRANT_SCHEMA:
        raise ManifestError("grant schema is unsupported")
    for field in ("run_id", "manifest_id", "environment_id", "task_id", "nonce"):
        value = values.get(field)
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ManifestError(f"grant {field} is invalid")
    steps = values.get("steps")
    seed = values.get("seed")
    expires_at = values.get("expires_at")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 100:
        raise ManifestError("grant steps must be from 1 through 100")
    if (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or not 0 <= seed <= 2_147_483_647
    ):
        raise ManifestError("grant seed is invalid")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        raise ManifestError("grant expiry is invalid")
    if allow_future:
        if not now < expires_at <= now + 300:
            raise ManifestError("new grants must expire within five minutes")
    elif expires_at < now:
        raise ManifestError("grant has expired")


def _validate_key(key: bytes) -> None:
    if not 32 <= len(key) <= 64:
        raise ManifestError(
            "sandbox authentication key must contain 32 through 64 bytes"
        )


def load_grant(path: Path) -> RunGrant:
    if path.is_symlink() or path.stat().st_size > 64 * 1024:
        raise ManifestError("grant file is unsafe")
    if path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ManifestError("grant must not be group- or world-writable")
    with path.open("r", encoding="utf-8") as handle:
        return parse_grant(json.load(handle))


def write_grant(path: Path, grant: RunGrant) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(asdict(grant), handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
