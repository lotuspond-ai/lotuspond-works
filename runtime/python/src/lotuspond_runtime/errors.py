"""Bounded fleet failures: no input, URL, path or provider detail."""

from typing import Literal

ErrorCode = Literal[
    "invalid_request",
    "input_limit",
    "unauthenticated",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "authority_unavailable",
    "unsafe_storage",
    "transport_failed",
    "destination_denied",
    "secret_unavailable",
    "trust_unavailable",
    "command_rejected",
]
_CODES = frozenset(ErrorCode.__args__)


class FleetError(Exception):
    def __init__(self, code: ErrorCode):
        self.code = code if type(code) is str and code in _CODES else "invalid_request"
        super().__init__(self.code)
