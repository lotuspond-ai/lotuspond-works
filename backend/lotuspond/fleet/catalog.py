"""Immutable operator-owned metadata; no executable paths or caller effects."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import ConnectorPin, SecretRegistrationRequest


@dataclass(frozen=True)
class OperationDescriptor:
    effect: Literal["none", "read", "write"]
    requires_destination: bool
    requires_secret: bool


@dataclass(frozen=True)
class ConnectorDescriptor:
    pin: ConnectorPin
    operations: Mapping[str, OperationDescriptor]

    def __post_init__(self):
        object.__setattr__(self, "operations", MappingProxyType(dict(self.operations)))


@dataclass(frozen=True, init=False)
class InstalledCatalog:
    _connectors: Mapping[ConnectorPin, ConnectorDescriptor]
    _bindings: frozenset[tuple[str, int]]
    test_only: bool

    def __init__(self):
        object.__setattr__(self, "_connectors", MappingProxyType({}))
        object.__setattr__(self, "_bindings", frozenset())
        object.__setattr__(self, "test_only", False)

    @classmethod
    def fixture(cls, pin: ConnectorPin, *, test_mode: bool, secret_bindings=frozenset()):
        if test_mode is not True:
            raise ValueError("Fixture catalogs require test mode")
        pin = ConnectorPin.model_validate(pin)
        if (pin.name, pin.version) != ("lotuspond-fixture", "1.0.0"):
            raise ValueError("Unsupported fixture")
        bindings = frozenset(
            (item.binding_id, item.version)
            for binding, version in secret_bindings
            for item in [SecretRegistrationRequest(binding_id=binding, version=version)]
        )
        result = cls()
        descriptor = ConnectorDescriptor(
            pin, {"inspect": OperationDescriptor("none", False, False)}
        )
        object.__setattr__(result, "_connectors", MappingProxyType({pin: descriptor}))
        object.__setattr__(result, "_bindings", bindings)
        object.__setattr__(result, "test_only", True)
        return result

    def connector(self, pin: ConnectorPin) -> ConnectorDescriptor:
        descriptor = self._connectors.get(ConnectorPin.model_validate(pin))
        if descriptor is None:
            raise FleetError("invalid_request")
        return descriptor

    def secret_binding(self, binding_id: str, version: int) -> bool:
        item = SecretRegistrationRequest(binding_id=binding_id, version=version)
        return (item.binding_id, item.version) in self._bindings
