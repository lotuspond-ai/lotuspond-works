"""Domain-separated detached activity signatures against independent trust."""

from __future__ import annotations

import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import ValidationError

from ..errors import SdkError
from ..trust import TrustSet
from .canonical import _admit, canonical_activity_json
from .models import ActivitySignature, ActivityVerificationReport


def _signed_frame(signature: ActivitySignature, canonical: bytes) -> bytes:
    header = {name: getattr(signature, name) for name in (
        "format_version", "algorithm", "context", "schema_version", "canonicalization",
        "key_id", "sha256",
    )}
    encoded = json.dumps(header, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":")).encode("ascii")
    return (signature.context.encode("ascii") + b"\0" + len(encoded).to_bytes(8, "big")
            + encoded + len(canonical).to_bytes(8, "big") + canonical)


def sign_activity(raw: object, key: Ed25519PrivateKey) -> ActivitySignature:
    """Validate and capture the complete activity once; never discover a key."""
    canonical = canonical_activity_json(raw)
    if not isinstance(key, Ed25519PrivateKey):
        raise SdkError("invalid_signing_key")
    header = dict(
        format_version=1, algorithm="ed25519", context="lotuspond-runtime-activity-signature-v1",
        schema_version="lotuspond.runtime-activity.v1",
        canonicalization="lotuspond.runtime-activity.jcs.v1",
        key_id=hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest(),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )
    provisional = _admit({**header, "signature": "0" * 128}, ActivitySignature,
                         code="invalid_envelope")
    return _admit({**header, "signature": key.sign(_signed_frame(provisional, canonical)).hex()},
                  ActivitySignature, code="invalid_envelope")


def verify_activity(raw: object, signature: object, trust: TrustSet) -> ActivityVerificationReport:
    """Verify integrity against supplied current trust; runtime bindings remain unchecked."""
    canonical = canonical_activity_json(raw)
    detached = _admit(signature, ActivitySignature, code="invalid_envelope")
    try:
        trusted = TrustSet.model_validate(trust)
    except ValidationError:
        raise SdkError("invalid_trust_set") from None
    digest = hashlib.sha256(canonical).hexdigest()
    key = next((item for item in trusted.keys if item.key_id == detached.key_id), None)
    if key is None:
        code = "untrusted_key"
    elif key.state == "revoked":
        code = "revoked_key"
    elif digest != detached.sha256:
        code = "digest_mismatch"
    else:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key.public_key))
        try:
            public.verify(bytes.fromhex(detached.signature), _signed_frame(detached, canonical))
            code = "verified"
        except InvalidSignature:
            code = "invalid_signature"
    return _admit(dict(valid=code == "verified", code=code, sha256=digest,
                       key_id=detached.key_id), ActivityVerificationReport, code="invalid_envelope")
