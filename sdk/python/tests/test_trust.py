import copy
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from test_signing import synthetic_key, synthetic_trust

from lotuspond_sdk import SdkError, TrustedKey, TrustSet, sign_package, verify_package


def test_duplicate_or_revoked_trust_cannot_authorize(example):
    envelope = sign_package(example, synthetic_key())
    allowed, revoked = synthetic_trust(), synthetic_trust("revoked")
    assert verify_package(example, envelope, allowed).code == "verified"
    report = verify_package(example, envelope, revoked)
    assert report.code == "revoked_key" and report.valid is False
    with pytest.raises(SdkError) as error:
        verify_package(
            example, envelope, {"format_version": 1, "keys": allowed["keys"] + revoked["keys"]}
        )
    assert error.value.code == "invalid_trust_set"
    assert verify_package(example, envelope, allowed).authority_granted is False
    assert (
        verify_package(example, envelope, {"format_version": 1, "keys": []}).code == "untrusted_key"
    )


def test_another_allowed_key_is_not_the_signer(example):
    public = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    trust = {
        "format_version": 1,
        "keys": [
            {
                "key_id": hashlib.sha256(public).hexdigest(),
                "public_key": public.hex(),
                "state": "allowed",
            }
        ],
    }
    assert (
        verify_package(example, sign_package(example, synthetic_key()), trust).code
        == "untrusted_key"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "fingerprint",
        "uppercase",
        "length",
        "state",
        "extra",
        "duplicate",
        "version",
        "boolean",
        "float",
        "limit",
    ],
)
def test_invalid_trust_structure(example, mutation):
    trust = synthetic_trust()
    entry = trust["keys"][0]
    if mutation == "fingerprint":
        entry["key_id"] = "00" * 32
    elif mutation == "uppercase":
        entry["public_key"] = entry["public_key"].upper()
    elif mutation == "length":
        entry["public_key"] = "00"
    elif mutation == "state":
        entry["state"] = "trusted"
    elif mutation == "extra":
        entry["certificate"] = "ignored?"
    elif mutation == "duplicate":
        trust["keys"].append(copy.deepcopy(entry))
    elif mutation == "limit":
        trust["keys"] *= 65
    else:
        trust["format_version"] = {"version": 2, "boolean": True, "float": 1.0}[mutation]
    with pytest.raises(SdkError) as error:
        verify_package(example, sign_package(example, synthetic_key()), trust)
    assert error.value.code == "invalid_trust_set"


def test_trust_limit_and_typed_mutations(example):
    keys = []
    for _ in range(64):
        public = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        keys.append(
            {
                "key_id": hashlib.sha256(public).hexdigest(),
                "public_key": public.hex(),
                "state": "allowed",
            }
        )
    assert len(TrustSet(format_version=1, keys=keys).keys) == 64
    trust = TrustSet.model_validate(synthetic_trust())
    trust.keys[0].state = "invented"
    with pytest.raises(SdkError):
        verify_package(example, sign_package(example, synthetic_key()), trust)
    with pytest.raises(ValidationError):
        trust.model_dump_json()
    trust = TrustSet.model_validate(synthetic_trust())
    trust.keys[0].__dict__["extra"] = "invalid"
    with pytest.raises(SdkError):
        verify_package(example, sign_package(example, synthetic_key()), trust)
    trust = TrustSet.model_validate(synthetic_trust())
    trust.keys.append(TrustedKey.model_validate(synthetic_trust()["keys"][0]))
    with pytest.raises(SdkError):
        verify_package(example, sign_package(example, synthetic_key()), trust)


# Independent literals from libsodium 1.0.18 ge25519_has_small_order;
# see fixture README for source URL/provenance. Both compressed-point sign bits
# must be rejected, independently of the implementation's own constant table.
DISALLOWED_Y_ENCODINGS = (
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0100000000000000000000000000000000000000000000000000000000000000",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
)


def trust_for_public(public):
    return {
        "format_version": 1,
        "keys": [
            {
                "key_id": hashlib.sha256(public).hexdigest(),
                "public_key": public.hex(),
                "state": "allowed",
            }
        ],
    }


@pytest.mark.parametrize("encoding", DISALLOWED_Y_ENCODINGS)
@pytest.mark.parametrize("sign_bit", [0, 128])
def test_disallowed_public_key_encodings_fail_closed(example, encoding, sign_bit):
    public = bytearray.fromhex(encoding)
    public[-1] |= sign_bit
    with pytest.raises(SdkError) as error:
        verify_package(example, sign_package(example, synthetic_key()), trust_for_public(public))
    assert error.value.code == "invalid_trust_set"


@pytest.mark.parametrize("offset", range(19))
@pytest.mark.parametrize("sign_bit", [0, 128])
def test_noncanonical_y_encodings_fail_closed(example, offset, sign_bit):
    # Field encoding permits y in [0,p); all representable y >= p are rejected.
    public = bytearray(((1 << 255) - 19 + offset).to_bytes(32, "little"))
    public[-1] |= sign_bit
    with pytest.raises(SdkError) as error:
        verify_package(example, sign_package(example, synthetic_key()), trust_for_public(public))
    assert error.value.code == "invalid_trust_set"


def test_identity_key_forgery_is_rejected_at_trust_boundary(example):
    # OpenSSL/cryptography 50 accepts R=identity, S=0 under the identity public
    # key for any message. The SDK must never trust that degenerate key.
    envelope = sign_package(example, synthetic_key()).model_dump()
    public = b"\x01" + b"\x00" * 31
    envelope["key_id"] = hashlib.sha256(public).hexdigest()
    envelope["signature"] = (b"\x01" + b"\x00" * 63).hex()
    with pytest.raises(SdkError) as error:
        verify_package(example, envelope, trust_for_public(public))
    assert error.value.code == "invalid_trust_set"


def test_normal_public_key_sign_bits_remain_allowed():
    from test_signing import PUBLIC

    for sign_bit in (0, 128):
        public = bytearray.fromhex(PUBLIC)
        public[-1] = (public[-1] & 127) | sign_bit
        assert TrustSet.model_validate(trust_for_public(public)).keys[0].public_key == public.hex()
