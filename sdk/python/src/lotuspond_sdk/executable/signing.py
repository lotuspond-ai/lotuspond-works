"""Pure detached signatures: independent trust, unchecked binding and false authority."""

from __future__ import annotations

import hashlib
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import Field, ValidationError, model_validator

from ..errors import SdkError
from ..trust import TrustSet
from ._validation import StrictModel, report_bytes, validate_model
from .errors import ExecutableError
from .framing import COMMAND_SIGNATURE_CONTEXT, PACKAGE_SIGNATURE_CONTEXT, _signed_frame
from .validation import canonical_executable_json
from .wire import canonical_wire_json, validate_runtime_command


class ExecutableSignatureEnvelope(StrictModel):
    format_version: Literal[2]
    algorithm: Literal['ed25519']
    context: Literal['lotuspond-workflow-package-signature-v2']
    schema_version: Literal['lotuspond.workflow-package.v2']
    canonicalization: Literal['lotuspond.workflow-package.jcs.v2']
    key_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    signature: str = Field(pattern=r'^[0-9a-f]{128}$')


class CommandSignatureEnvelope(StrictModel):
    format_version: Literal[1]
    algorithm: Literal['ed25519']
    context: Literal['lotuspond-runtime-command-v1']
    schema_version: Literal['lotuspond.runtime-command.v1']
    canonicalization: Literal['lotuspond.runtime-wire.jcs.v1']
    key_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    signature: str = Field(pattern=r'^[0-9a-f]{128}$')


class ExecutableVerificationReport(StrictModel):
    valid: bool
    code: Literal['verified', 'untrusted_key', 'revoked_key', 'digest_mismatch', 'invalid_signature']
    content_kind: Literal['package_v2', 'runtime_command_v1']
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    key_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    contract_valid: Literal[True] = True
    compatibility_status: Literal['not_checked'] = 'not_checked'
    binding_status: Literal['not_checked'] = 'not_checked'
    authority_granted: Literal[False] = False

    @model_validator(mode='after')
    def consistent_result(self) -> ExecutableVerificationReport:
        if self.valid != (self.code == 'verified'):
            raise ValueError('Verification outcome must match its code.')
        return self


SignatureEnvelope = ExecutableSignatureEnvelope | CommandSignatureEnvelope
SIGNING_MODELS = (ExecutableSignatureEnvelope, CommandSignatureEnvelope, ExecutableVerificationReport)


def _admit(raw: object, model):
    try:
        return validate_model(raw, model_type=model, model_types=(model,), code='invalid_envelope')
    except ExecutableError as error:
        if error.code == 'invalid_json_value':
            raise ExecutableError('invalid_envelope') from None
        raise


def validate_executable_signature_envelope(raw: object) -> ExecutableSignatureEnvelope:
    """Admit only package-v2 headers; reproject constructed/mutated known models."""
    return _admit(raw, ExecutableSignatureEnvelope)


def validate_command_signature_envelope(raw: object) -> CommandSignatureEnvelope:
    """Admit only runtime-command-v1 headers, never another signing context."""
    return _admit(raw, CommandSignatureEnvelope)


def validate_executable_verification_report(raw: object) -> ExecutableVerificationReport:
    result = _admit(raw, ExecutableVerificationReport)
    report_bytes(result, model_type=ExecutableVerificationReport,
                 model_types=(ExecutableVerificationReport,), code='invalid_envelope')
    return result


def executable_signature_envelope_json(raw: object) -> bytes:
    """Emit complete admitted package envelope as bounded ASCII JSON plus newline."""
    return report_bytes(validate_executable_signature_envelope(raw),
                        model_type=ExecutableSignatureEnvelope,
                        model_types=(ExecutableSignatureEnvelope,), code='invalid_envelope')


def command_signature_envelope_json(raw: object) -> bytes:
    """Emit complete admitted command envelope as bounded ASCII JSON plus newline."""
    return report_bytes(validate_command_signature_envelope(raw),
                        model_type=CommandSignatureEnvelope,
                        model_types=(CommandSignatureEnvelope,), code='invalid_envelope')


def executable_verification_report_json(raw: object) -> bytes:
    """Emit complete admitted result as bounded ASCII JSON plus newline."""
    return report_bytes(validate_executable_verification_report(raw),
                        model_type=ExecutableVerificationReport,
                        model_types=(ExecutableVerificationReport,), code='invalid_envelope')


def _sign(canonical: bytes, private_key: Ed25519PrivateKey, *, command: bool) -> SignatureEnvelope:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SdkError('invalid_signing_key')
    model = CommandSignatureEnvelope if command else ExecutableSignatureEnvelope
    header = {
        'format_version': 1 if command else 2,
        'algorithm': 'ed25519',
        'context': COMMAND_SIGNATURE_CONTEXT if command else PACKAGE_SIGNATURE_CONTEXT,
        'schema_version': 'lotuspond.runtime-command.v1' if command else 'lotuspond.workflow-package.v2',
        'canonicalization': ('lotuspond.runtime-wire.jcs.v1' if command
                             else 'lotuspond.workflow-package.jcs.v2'),
        'key_id': hashlib.sha256(private_key.public_key().public_bytes_raw()).hexdigest(),
        'sha256': hashlib.sha256(canonical).hexdigest(),
    }
    envelope = _admit({**header, 'signature': '0' * 128}, model)
    return _admit({**header, 'signature': private_key.sign(_signed_frame(envelope, canonical)).hex()},
                  model)


def sign_executable_package(raw: object, private_key: Ed25519PrivateKey) -> ExecutableSignatureEnvelope:
    """Fully validate and capture package JCS once; no compilation or key discovery."""
    return _sign(canonical_executable_json(raw), private_key, command=False)


def sign_runtime_command(raw: object, private_key: Ed25519PrivateKey) -> CommandSignatureEnvelope:
    """Sign the complete admitted command, without checking context binding or authority."""
    canonical = canonical_wire_json(validate_runtime_command(raw))
    return _sign(canonical, private_key, command=True)


def _verify(
    canonical: bytes, envelope: object, trust_set: object, *, command: bool,
) -> ExecutableVerificationReport:
    signature = (validate_command_signature_envelope(envelope) if command
                 else validate_executable_signature_envelope(envelope))
    # Retain reviewed v1 trust revalidation, duplicate/encoding guards and fixed error.
    # Never project trust through the executable known-model registry or call serializers.
    try:
        trusted = TrustSet.model_validate(trust_set)
    except ValidationError:
        raise SdkError('invalid_trust_set') from None
    digest = hashlib.sha256(canonical).hexdigest()
    key = next((key for key in trusted.keys if key.key_id == signature.key_id), None)
    if key is None:
        code = 'untrusted_key'
    elif key.state == 'revoked':
        code = 'revoked_key'
    elif digest != signature.sha256:
        code = 'digest_mismatch'
    else:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key.public_key))
        try:
            public.verify(bytes.fromhex(signature.signature), _signed_frame(signature, canonical))
            code = 'verified'
        except InvalidSignature:
            code = 'invalid_signature'
    return validate_executable_verification_report({
        'valid': code == 'verified', 'code': code,
        'content_kind': 'runtime_command_v1' if command else 'package_v2',
        'sha256': digest, 'key_id': signature.key_id,
    })


def verify_executable_package(
    raw: object, envelope: object, trust_set: object,
) -> ExecutableVerificationReport:
    """Verify captured package JCS against explicit trust; compatibility remains unchecked."""
    return _verify(canonical_executable_json(raw), envelope, trust_set, command=False)


def verify_runtime_command(
    raw: object, envelope: object, trust_set: object,
) -> ExecutableVerificationReport:
    """Verify complete command integrity; compose check_command_binding separately."""
    canonical = canonical_wire_json(validate_runtime_command(raw))
    return _verify(canonical, envelope, trust_set, command=True)
