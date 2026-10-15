"""Explicit execution grants; telemetry subscriptions confer no execution rights."""

from typing import Protocol

from lotuspond_runtime.protocol import OpaqueId, SafeInt, Scope

from .contracts import Contract, ExecutionLimits
from .errors import ExecutionError


class EntitlementGrant(Contract):
    scope: Scope
    installation_id: OpaqueId
    recovery_epoch: SafeInt
    revision: OpaqueId
    expires_at_ms: SafeInt
    execute: bool
    limits: ExecutionLimits


class PolicyGrant(Contract):
    scope: Scope
    expires_at_ms: SafeInt
    limits: ExecutionLimits


class ExecutionEntitlement(Protocol):
    def current(self, scope: Scope, now_ms: int) -> EntitlementGrant: ...


class UnavailableExecutionEntitlement:
    def current(self, scope: Scope, now_ms: int) -> EntitlementGrant:
        raise ExecutionError("entitlement_unavailable")
