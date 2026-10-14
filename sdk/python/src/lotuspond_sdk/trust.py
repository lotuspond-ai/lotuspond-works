"""Independently provisioned Ed25519 trust; package records never confer trust."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import SdkError

MAX_TRUST_KEYS = 64
MAX_REPORT_BYTES = 8 * 1024 * 1024

# Established compressed-point y encodings from libsodium 1.0.18,
# ge25519_has_small_order in src/libsodium/crypto_core/ed25519/ref10/ed25519_ref10.c:
# https://github.com/jedisct1/libsodium/blob/1.0.18/src/libsodium/crypto_core/ed25519/ref10/ed25519_ref10.c#L1022
# The sign bit is ignored for this public-data check, as in the source. This is
# an encoding/small-order guard, not curve arithmetic or a full subgroup check.
_SMALL_ORDER_Y = frozenset(
    bytes.fromhex(value)
    for value in (
        "0000000000000000000000000000000000000000000000000000000000000000",
        "0100000000000000000000000000000000000000000000000000000000000000",
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
        "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
        "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
        "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    )
)
_ED25519_FIELD_PRIME = (1 << 255) - 19


class _StrictRecord(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", hide_input_in_errors=True, revalidate_instances="always"
    )

    def model_dump(self, **kwargs) -> dict:
        validated = type(self).model_validate(self)
        if len(BaseModel.model_dump_json(validated, ensure_ascii=True)) > MAX_REPORT_BYTES:
            raise SdkError("report_too_large")
        return BaseModel.model_dump(validated, **kwargs)

    def model_dump_json(self, **kwargs) -> str:
        validated = type(self).model_validate(self)
        # Check the complete record even if the caller selects only some fields.
        if len(BaseModel.model_dump_json(validated, ensure_ascii=True)) > MAX_REPORT_BYTES:
            raise SdkError("report_too_large")
        kwargs["ensure_ascii"] = True
        return BaseModel.model_dump_json(validated, **kwargs)


class TrustedKey(_StrictRecord):
    key_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["allowed", "revoked"]

    @model_validator(mode="after")
    def matching_fingerprint(self) -> TrustedKey:
        public = bytes.fromhex(self.public_key)
        y_encoding = public[:-1] + bytes([public[-1] & 0x7F])
        if (
            y_encoding in _SMALL_ORDER_Y
            or int.from_bytes(y_encoding, "little") >= _ED25519_FIELD_PRIME
        ):
            raise ValueError("Trust requires canonical, non-small-order public key encodings.")
        if hashlib.sha256(public).hexdigest() != self.key_id:
            raise ValueError("The public key fingerprint must match its identifier.")
        return self


class TrustSet(_StrictRecord):
    format_version: Literal[1]
    keys: list[TrustedKey] = Field(max_length=MAX_TRUST_KEYS)

    @field_validator("format_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Only integer trust format version 1 is supported.")
        return value

    @model_validator(mode="after")
    def unique_keys(self) -> TrustSet:
        if len({key.key_id for key in self.keys}) != len(self.keys) or len(
            {key.public_key for key in self.keys}
        ) != len(self.keys):
            raise ValueError("Trust records must be unambiguous.")
        return self
