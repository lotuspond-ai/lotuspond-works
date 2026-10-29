"""Closed public errors; provider, database and caller text never escapes."""

from enum import StrEnum
from functools import wraps

from sqlalchemy.exc import SQLAlchemyError


class ExecutionCode(StrEnum):
    CAPACITY_UNAVAILABLE = "capacity_unavailable"
    ENTITLEMENT_UNAVAILABLE = "entitlement_unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    PRICE_UNAVAILABLE = "price_unavailable"
    RECOVERY_UNAVAILABLE = "recovery_unavailable"
    INVALID_REQUEST = "invalid_request"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNSUPPORTED_STEP = "unsupported_step"
    UNSUPPORTED_POLICY = "unsupported_policy"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    TRUST_UNAVAILABLE = "trust_unavailable"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    ASSURANCE_UNAVAILABLE = "assurance_unavailable"
    DEPLOYMENT_UNAVAILABLE = "deployment_unavailable"
    SOURCE_UNAVAILABLE = "source_unavailable"
    KEY_UNAVAILABLE = "key_unavailable"
    ARTIFACT_CORRUPT = "artifact_corrupt"
    UNSUPPORTED_INTERPRETER = "unsupported_interpreter"


class ExecutionError(Exception):
    def __init__(self, code: str):
        try:
            self.code = ExecutionCode(code).value
        except (TypeError, ValueError):
            self.code = ExecutionCode.INVALID_REQUEST.value
        super().__init__(self.code)


def execution_service(function):
    """Keep typed/SDK/database failures value-free at the service boundary."""

    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ExecutionError:
            raise
        except SQLAlchemyError:
            raise ExecutionError("authority_unavailable") from None
        except Exception:
            raise ExecutionError("invalid_request") from None

    return invoke
