"""Context-specific detached-signature framing, separate from the v1 byte contract."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .signing import CommandSignatureEnvelope, ExecutableSignatureEnvelope

PACKAGE_SIGNATURE_CONTEXT = 'lotuspond-workflow-package-signature-v2'
COMMAND_SIGNATURE_CONTEXT = 'lotuspond-runtime-command-v1'


def _signed_frame(
    envelope: ExecutableSignatureEnvelope | CommandSignatureEnvelope, canonical: bytes,
) -> bytes:
    """Frame captured content; caller must supply a context-specifically admitted envelope."""
    header = {
        name: getattr(envelope, name)
        for name in ('format_version', 'algorithm', 'context', 'schema_version',
                     'canonicalization', 'key_id', 'sha256')
    }
    h = json.dumps(header, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return (envelope.context.encode('ascii') + b'\0' + len(h).to_bytes(8, 'big') + h
            + len(canonical).to_bytes(8, 'big') + canonical)
