"""Typed private control API. No backend imports or ambient authentication."""

from __future__ import annotations

import json

from pydantic import SecretStr

from .control_transport import ControlTransport
from .protocol import (
    AcquiredSession,
    IssuedCredential,
    PreparedCredential,
    RuntimeObservation,
    SelfState,
    validate_token,
)


class RuntimeClient:
    def __init__(self, transport: ControlTransport):
        self.transport = transport

    def _call(self, route, token, model, session=None, body=b"{}"):
        validate_token(token, "lpe_" if route == "/enroll" else "lpr_")
        if session is not None:
            validate_token(session, "lps_")
        result = model.model_validate_json(
            self.transport.request(
                "GET" if route == "/self" else "POST", route, body, token, session
            )
        )
        if isinstance(result, (IssuedCredential, PreparedCredential)):
            validate_token(result.token, "lpr_")
        if isinstance(result, AcquiredSession):
            validate_token(result.secret, "lps_")
        return result

    def enroll(self, token: SecretStr, observation: RuntimeObservation) -> IssuedCredential:
        observation = RuntimeObservation.model_validate(observation)
        body = json.dumps(observation.model_dump(mode="json"), separators=(",", ":")).encode()
        return self._call("/enroll", token, IssuedCredential, body=body)

    def self_state(self, token: SecretStr) -> SelfState:
        return self._call("/self", token, SelfState, body=None)

    def acquire(self, token: SecretStr) -> AcquiredSession:
        return self._call("/session", token, AcquiredSession)

    def heartbeat(self, token: SecretStr, session: SecretStr) -> SelfState:
        validate_token(session, "lps_")
        return self._call("/heartbeat", token, SelfState, session)

    def prepare(self, token: SecretStr, session: SecretStr) -> PreparedCredential:
        validate_token(session, "lps_")
        return self._call("/credentials/prepare", token, PreparedCredential, session)

    def activate(self, token: SecretStr, session: SecretStr | None) -> SelfState:
        return self._call("/credentials/activate", token, SelfState, session)

    def cancel(self, token: SecretStr, session: SecretStr) -> SelfState:
        validate_token(session, "lps_")
        return self._call("/credentials/cancel", token, SelfState, session)

    def close(self) -> None:
        self.transport.close()
