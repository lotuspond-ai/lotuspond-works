"""Operator-custody internal command signer. No route or fixture key selector."""

import hashlib
from pathlib import Path
from time import time_ns

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import CommandTrustSnapshot
from lotuspond_runtime.trust import validate_snapshot
from lotuspond_sdk import load_signing_key
from lotuspond_sdk.executable.signing import (
    CommandSignatureEnvelope,
    sign_runtime_command,
    verify_runtime_command,
)
from lotuspond_sdk.executable.wire import RuntimeCommand


class CommandSigner:
    def __init__(self, key_path: Path):
        self._key_path = key_path

    def sign(
        self, command: RuntimeCommand, snapshot: CommandTrustSnapshot
    ) -> CommandSignatureEnvelope:
        try:
            key = load_signing_key(self._key_path)
            snapshot = validate_snapshot(snapshot, now_ms=time_ns() // 1000000)
            key_id = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
            if not any(
                item.key_id == key_id and item.state == "allowed" for item in snapshot.trust.keys
            ):
                raise FleetError("trust_unavailable")
            envelope = sign_runtime_command(command, key)
            if not verify_runtime_command(command, envelope, snapshot.trust).valid:
                raise FleetError("trust_unavailable")
            return envelope
        except Exception:
            raise FleetError("trust_unavailable") from None
