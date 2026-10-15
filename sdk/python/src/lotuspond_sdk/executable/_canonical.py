"""Reviewed RFC 8785 adapter; deliberately separate from the v1 byte contract."""

import hashlib
from io import BytesIO

import rfc8785

from .errors import ExecutableError
from .input import PACKAGE_LIMITS, REPORT_LIMITS, project_json


class _BoundedSink(BytesIO):
    def __init__(self, maximum: int):
        super().__init__()
        self.maximum = maximum

    def write(self, content: bytes) -> int:
        if self.tell() + len(content) > self.maximum:
            code = (
                "report_too_large" if self.maximum > PACKAGE_LIMITS.max_bytes else "input_too_large"
            )
            raise ExecutableError(code)
        return super().write(content)


def jcs_bytes(raw: object, *, max_bytes: int = 1048576) -> bytes:
    """Serialize normalized exact JSON; bare unsafe integers always refuse.

    Package caps use package traversal limits. Explicit larger output caps use
    report traversal limits; package admission must never use that larger mode.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= REPORT_LIMITS.max_bytes:
        raise ExecutableError("invalid_json_value")
    limits = PACKAGE_LIMITS if max_bytes <= PACKAGE_LIMITS.max_bytes else REPORT_LIMITS
    projected = project_json(raw, limits=limits)
    sink = _BoundedSink(max_bytes)
    try:
        rfc8785.dump(projected, sink)
    except rfc8785.IntegerDomainError:
        raise ExecutableError("safe_integer") from None
    except rfc8785.FloatDomainError:
        raise ExecutableError("nonfinite_number") from None
    except rfc8785.CanonicalizationError:
        raise ExecutableError("invalid_json_value") from None
    return sink.getvalue()


def sha256_hex(content: bytes) -> str:
    """Content identity only; this grants no trust or execution authority."""
    if type(content) is not bytes:
        raise ExecutableError("invalid_json_value")
    return hashlib.sha256(content).hexdigest()
