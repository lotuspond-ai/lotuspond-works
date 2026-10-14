"""Synthetic RFC key and independently generated OpenSSL package-signature oracle."""

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from lotuspond_sdk import (
    SdkError,
    SignatureEnvelope,
    VerificationReport,
    sign_package,
    verify_package,
)

SECRET = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
PUBLIC = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
RFC_SIGNATURE = (
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
    "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)
FIXTURE = Path(__file__).parent / "fixtures" / "signing-v1.json"


def synthetic_key():
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(SECRET))


def synthetic_trust(state="allowed"):
    return {
        "format_version": 1,
        "keys": [
            {
                "key_id": hashlib.sha256(bytes.fromhex(PUBLIC)).hexdigest(),
                "public_key": PUBLIC,
                "state": state,
            }
        ],
    }


def independent_frame(envelope, canonical):
    header = {k: v for k, v in envelope.items() if k != "signature"}
    h = json.dumps(header, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return (
        b"lotuspond-workflow-package-signature-v1\x00"
        + len(h).to_bytes(8, "big")
        + h
        + len(canonical).to_bytes(8, "big")
        + canonical
    )


def test_rfc_8032_vector_1():
    key = synthetic_key()
    assert key.public_key().public_bytes_raw().hex() == PUBLIC
    assert key.sign(b"").hex() == RFC_SIGNATURE
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    assert serialization.load_pem_private_key(pem, None).private_bytes_raw().hex() == SECRET


def test_independent_package_fixture(example):
    oracle = json.loads(FIXTURE.read_text())
    assert sign_package(example, synthetic_key()).model_dump() == oracle
    report = verify_package(example, oracle, synthetic_trust())
    assert report.model_dump() == {
        "valid": True,
        "code": "verified",
        "sha256": oracle["sha256"],
        "key_id": oracle["key_id"],
        "contract_valid": True,
        "compatibility_status": "not_checked",
        "authority_granted": False,
    }
    assert "public_key" not in oracle


def test_openssl_cross_verification(example, golden_bytes, tmp_path):
    # CI Linux must fail, not skip, if its independent verifier disappears.
    assert shutil.which("openssl"), "OpenSSL is required for independent cross-verification"
    key = synthetic_key()
    private, public, frame, signature = [
        tmp_path / name for name in ("private", "public", "frame", "sig")
    ]
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    envelope = sign_package(example, key).model_dump()
    frame.write_bytes(independent_frame(envelope, golden_bytes))
    signature.write_bytes(bytes.fromhex(envelope["signature"]))
    checked = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-rawin",
            "-pubin",
            "-inkey",
            str(public),
            "-in",
            str(frame),
            "-sigfile",
            str(signature),
        ],
        capture_output=True,
        timeout=10,
    )
    assert checked.returncode == 0, checked.stderr.decode()
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            str(private),
            "-in",
            str(frame),
            "-out",
            str(signature),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    envelope["signature"] = signature.read_bytes().hex()
    assert verify_package(example, envelope, synthetic_trust()).valid
    assert envelope == json.loads(FIXTURE.read_text())


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", 2),
        ("format_version", True),
        ("format_version", 1.0),
        ("format_version", "1"),
        ("algorithm", "ed448"),
        ("context", "lotuspond-evidence-manifest-v1"),
        ("schema_version", "lotuspond.workflow-package.v2"),
        ("canonicalization", "other"),
        ("key_id", "A" * 64),
        ("sha256", "0" * 63),
        ("signature", "F" * 128),
        ("signature", "00"),
        ("extra", "never accepted"),
    ],
)
def test_malformed_envelopes_fail_closed(example, field, value):
    envelope = json.loads(FIXTURE.read_text())
    envelope[field] = value
    with pytest.raises(SdkError) as error:
        verify_package(example, envelope, synthetic_trust())
    assert error.value.code == "invalid_signature_envelope"


def test_changed_bytes_and_signature_distinctions(example):
    envelope = sign_package(example, synthetic_key())
    changed = copy.deepcopy(example)
    changed["name"] = "Changed synthetic package"
    assert verify_package(changed, envelope, synthetic_trust()).code == "digest_mismatch"
    mutated = envelope.model_dump()
    mutated["signature"] = "00" * 64
    assert verify_package(example, mutated, synthetic_trust()).code == "invalid_signature"
    mutated["sha256"] = "00" * 32
    assert verify_package(example, mutated, synthetic_trust()).code == "digest_mismatch"
    mutated["key_id"] = "00" * 32
    assert verify_package(example, mutated, synthetic_trust()).code == "untrusted_key"


def test_mutated_typed_envelope_is_revalidated(example):
    envelope = sign_package(example, synthetic_key())
    envelope.format_version = True
    with pytest.raises(SdkError) as error:
        verify_package(example, envelope, synthetic_trust())
    assert error.value.code == "invalid_signature_envelope"
    with pytest.raises(ValidationError):
        envelope.model_dump_json()
    envelope = sign_package(example, synthetic_key())
    envelope.__dict__["public_key"] = PUBLIC
    with pytest.raises(SdkError):
        verify_package(example, envelope, synthetic_trust())


def test_package_models_are_revalidated(example):
    from lotuspond_sdk import validate_package

    package = validate_package(example)
    package.nodes[2].limits.max_steps = "unsafe"
    with pytest.raises(ValidationError):
        sign_package(package, synthetic_key())
    with pytest.raises(ValidationError):
        verify_package(package, json.loads(FIXTURE.read_text()), synthetic_trust())


@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_valid", 1),
        ("contract_valid", 1.0),
        ("contract_valid", False),
        ("authority_granted", 0),
        ("authority_granted", 0.0),
        ("authority_granted", True),
        ("valid", 1),
        ("valid", "true"),
    ],
)
def test_report_literal_types(example, field, value):
    record = verify_package(
        example, json.loads(FIXTURE.read_text()), synthetic_trust()
    ).model_dump()
    record[field] = value
    with pytest.raises(ValidationError):
        VerificationReport.model_validate(record)


def test_envelope_and_report_serialization_revalidate(example):
    envelope = sign_package(example, synthetic_key())
    assert SignatureEnvelope.model_validate_json(envelope.model_dump_json()) == envelope
    report = verify_package(example, envelope, synthetic_trust())
    report.authority_granted = 0
    with pytest.raises(ValidationError):
        report.model_dump()


def test_envelope_frame_is_not_evidence_manifest_context(example, golden_bytes):
    envelope = sign_package(example, synthetic_key()).model_dump()
    envelope["signature"] = (
        synthetic_key().sign(b"lotuspond-evidence-manifest-v1\n" + golden_bytes).hex()
    )
    assert verify_package(example, envelope, synthetic_trust()).code == "invalid_signature"


def test_normalization_and_unicode_sign_exact_canonical_content(example):
    from lotuspond_sdk import canonical_package_json, validate_package

    example["name"] = "Synthetic \u2603 package"
    envelope = sign_package(example, synthetic_key())
    canonical = canonical_package_json(validate_package(example))
    synthetic_key().public_key().verify(
        bytes.fromhex(envelope.signature), independent_frame(envelope.model_dump(), canonical)
    )
    assert b"\xe2\x98\x83" in canonical
    assert envelope.sha256 == hashlib.sha256(canonical).hexdigest()
    assert sign_package(validate_package(example), synthetic_key()) == envelope
    assert verify_package(example, envelope, synthetic_trust()).valid


def test_wrong_private_key_type_is_fixed_error(example):
    with pytest.raises(SdkError) as error:
        sign_package(example, object())
    assert error.value.code == "invalid_signing_key"


def test_indented_envelope_cannot_exceed_eight_mib():
    envelope = SignatureEnvelope.model_validate(json.loads(FIXTURE.read_text()))
    with pytest.raises(SdkError) as error:
        envelope.model_dump_json(indent=1024 * 1024)
    assert error.value.code == "report_too_large"


@pytest.mark.parametrize("kind", ["envelope", "report", "trusted_key", "trust_set"])
def test_rendered_record_limit_includes_formatting(example, monkeypatch, kind):
    from pydantic import BaseModel

    from lotuspond_sdk import TrustedKey, TrustSet, trust

    envelope = sign_package(example, synthetic_key())
    record = {
        "envelope": envelope,
        "report": verify_package(example, envelope, synthetic_trust()),
        "trusted_key": TrustedKey.model_validate(synthetic_trust()["keys"][0]),
        "trust_set": TrustSet.model_validate(synthetic_trust()),
    }[kind]
    compact = BaseModel.model_dump_json(record, ensure_ascii=True)
    pretty = BaseModel.model_dump_json(record, indent=4, ensure_ascii=True)
    assert len(compact) < len(pretty)
    # The exact rendered boundary succeeds; one byte over must fail even though
    # the complete compact record fits. No large allocation is needed here.
    monkeypatch.setattr(trust, "MAX_REPORT_BYTES", len(pretty))
    assert record.model_dump_json(indent=4, ensure_ascii=False) == pretty
    monkeypatch.setattr(trust, "MAX_REPORT_BYTES", len(pretty) - 1)
    with pytest.raises(SdkError) as error:
        record.model_dump_json(indent=4)
    assert error.value.code == "report_too_large"
    # Selecting only some fields cannot bypass the full-record precheck.
    monkeypatch.setattr(trust, "MAX_REPORT_BYTES", len(compact) - 1)
    with pytest.raises(SdkError) as error:
        record.model_dump_json(include={"key_id"})
    assert error.value.code == "report_too_large"
