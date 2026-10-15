"""Bounded activity projection and transport decoding, independent of runtime I/O."""

from __future__ import annotations

import hashlib

from ..executable._canonical import jcs_bytes
from ..executable._validation import validate_model
from ..executable.errors import ExecutableError
from ..executable.input import WIRE_LIMITS, JsonLimits, decode_executable_bytes, project_json
from .models import ACTIVITY_MODELS, ActivityDelivery, ActivityEnvelope, ActivitySignature

DELIVERY_LIMITS = JsonLimits(131072, 32, 10000)


def _admit(raw, model, *, code="invalid_wire", limits=WIRE_LIMITS):
    return validate_model(raw, model_type=model, model_types=ACTIVITY_MODELS,
                          code=code, limits=limits)


def validate_activity(raw: object) -> ActivityEnvelope:
    """Recheck the entire declaration, including constructed or mutated known models."""
    return _admit(raw, ActivityEnvelope)


def canonical_activity_json(raw: object) -> bytes:
    """Capture complete admitted RFC 8785 content without a trailing newline."""
    admitted = validate_activity(raw)
    return jcs_bytes(project_json(admitted, model_types=ACTIVITY_MODELS, limits=WIRE_LIMITS),
                     max_bytes=65536)


def activity_digest(raw: object) -> str:
    """Hash complete activity content; identity does not establish authority."""
    return hashlib.sha256(canonical_activity_json(raw)).hexdigest()


def decode_activity_delivery(content: bytes) -> tuple[ActivityEnvelope, ActivitySignature]:
    """Decode exact inner documents under a combined UTF-8 budget before admission.

    Duplicate keys, invalid Unicode, nonfinite numbers, excessive depth/items and
    both raw outer and unescaped inner byte bounds are refused. Signatures are
    admitted structurally here; trust and current runtime bindings are separate.
    """
    if type(content) is not bytes:
        raise ExecutableError("invalid_json_value")
    if len(content) > DELIVERY_LIMITS.max_bytes:
        raise ExecutableError("input_too_large")
    delivery = _admit(decode_executable_bytes(content), ActivityDelivery, limits=DELIVERY_LIMITS)
    envelope = validate_activity(decode_executable_bytes(delivery.envelope.encode("utf-8")))
    signature = _admit(decode_executable_bytes(delivery.signature.encode("utf-8")),
                       ActivitySignature, code="invalid_envelope")
    return envelope, signature
