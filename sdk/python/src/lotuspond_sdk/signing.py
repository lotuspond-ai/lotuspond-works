"""Detached package signatures over a distinct, versioned, length-prefixed frame."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import Field, ValidationError, field_validator, model_validator

from .contract import SCHEMA_VERSION, canonical_package_json, validate_package
from .errors import SdkError
from .metadata import CANONICALIZATION
from .trust import TrustSet, _StrictRecord

SIGNATURE_CONTEXT = "lotuspond-workflow-package-signature-v1"


class SignatureEnvelope(_StrictRecord):
    format_version: Literal[1]
    algorithm: Literal["ed25519"]
    context: Literal["lotuspond-workflow-package-signature-v1"]
    schema_version: Literal["lotuspond.workflow-package.v1"]
    canonicalization: Literal["lotuspond.workflow-package.canonical-json.v1"]
    key_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")

    @field_validator("format_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Only integer signature format version 1 is supported.")
        return value


class VerificationReport(_StrictRecord):
    valid: bool
    code: Literal[
        "verified", "untrusted_key", "revoked_key", "digest_mismatch", "invalid_signature"
    ]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    key_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_valid: Literal[True] = True
    compatibility_status: Literal["not_checked"] = "not_checked"
    authority_granted: Literal[False] = False

    @field_validator("contract_valid", "authority_granted", mode="before")
    @classmethod
    def exact_boolean(cls, value, info):
        required = info.field_name == "contract_valid"
        if value is not required:
            raise ValueError("Verification reports require exact fixed booleans.")
        return value

    @model_validator(mode="after")
    def consistent_result(self) -> VerificationReport:
        if self.valid != (self.code == "verified"):
            raise ValueError("The verification outcome must match its code.")
        return self


def _signed_frame(header: dict, canonical: bytes) -> bytes:
    h = json.dumps(header, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return (
        SIGNATURE_CONTEXT.encode("ascii")
        + b"\x00"
        + len(h).to_bytes(8, "big")
        + h
        + len(canonical).to_bytes(8, "big")
        + canonical
    )


def sign_package(raw: object, private_key: Ed25519PrivateKey) -> SignatureEnvelope:
    """Validate and sign canonical content; trust is provisioned separately."""
    canonical = canonical_package_json(validate_package(raw))
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SdkError("invalid_signing_key")
    header = {
        "format_version": 1,
        "algorithm": "ed25519",
        "context": SIGNATURE_CONTEXT,
        "schema_version": SCHEMA_VERSION,
        "canonicalization": CANONICALIZATION,
        "key_id": hashlib.sha256(private_key.public_key().public_bytes_raw()).hexdigest(),
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }
    return SignatureEnvelope(
        **header, signature=private_key.sign(_signed_frame(header, canonical)).hex()
    )


def verify_package(raw: object, envelope: object, trust_set: object) -> VerificationReport:
    """Verify locally against allowed external trust, without granting authority."""
    canonical = canonical_package_json(validate_package(raw))
    try:
        signature = SignatureEnvelope.model_validate(envelope)
    except ValidationError:
        raise SdkError("invalid_signature_envelope") from None
    try:
        trusted = TrustSet.model_validate(trust_set)
    except ValidationError:
        raise SdkError("invalid_trust_set") from None
    digest = hashlib.sha256(canonical).hexdigest()
    key = next((key for key in trusted.keys if key.key_id == signature.key_id), None)
    if key is None:
        code = "untrusted_key"
    elif key.state == "revoked":
        code = "revoked_key"
    elif digest != signature.sha256:
        code = "digest_mismatch"
    else:
        header = signature.model_dump(exclude={"signature"})
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key.public_key))
        try:
            public.verify(bytes.fromhex(signature.signature), _signed_frame(header, canonical))
            code = "verified"
        except InvalidSignature:
            code = "invalid_signature"
    return VerificationReport(
        valid=code == "verified", code=code, sha256=digest, key_id=signature.key_id
    )
