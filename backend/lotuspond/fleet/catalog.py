"""Immutable operator-owned metadata; no executable paths or caller effects."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import ConnectorPin, Identifier, SecretRegistrationRequest
from pydantic import TypeAdapter


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

    @classmethod
    def installed(cls, data) -> "InstalledCatalog":
        """An operator's installed catalog (`catalog.json` in the authority directory).

        Strict, closed data: `{"connectors": [{"pin", "operations": {name: {"effect",
        "requires_destination", "requires_secret"}}}], "secret_bindings": [{"binding_id",
        "version"}]}`. Not test-only: it is how an installation admits real connectors.
        """
        if type(data) is not dict or set(data) != {"connectors", "secret_bindings"}:
            raise ValueError("Invalid catalog")
        connectors, bindings = data["connectors"], data["secret_bindings"]
        if type(connectors) is not list or len(connectors) > 64:
            raise ValueError("Invalid catalog connectors")
        if type(bindings) is not list or len(bindings) > 256:
            raise ValueError("Invalid catalog secret bindings")
        installed = {}
        for item in connectors:
            descriptor = connector_descriptor(item)
            if descriptor.pin in installed:
                raise ValueError("A connector is installed twice")
            installed[descriptor.pin] = descriptor
        pairs = set()
        for row in bindings:
            if type(row) is not dict or set(row) != {"binding_id", "version"}:
                raise ValueError("Invalid secret binding")
            item = SecretRegistrationRequest(binding_id=row["binding_id"], version=row["version"])
            pairs.add((item.binding_id, item.version))
        result = cls()
        object.__setattr__(result, "_connectors", MappingProxyType(installed))
        object.__setattr__(result, "_bindings", frozenset(pairs))
        return result

    def connector(self, pin: ConnectorPin) -> ConnectorDescriptor:
        descriptor = self._connectors.get(ConnectorPin.model_validate(pin))
        if descriptor is None:
            raise FleetError("invalid_request")
        return descriptor

    def secret_binding(self, binding_id: str, version: int) -> bool:
        item = SecretRegistrationRequest(binding_id=binding_id, version=version)
        return (item.binding_id, item.version) in self._bindings


def connector_descriptor(item) -> ConnectorDescriptor:
    """One installed connector description, strictly checked."""
    if type(item) is not dict or set(item) != {"pin", "operations"}:
        raise ValueError("Invalid connector")
    pin = ConnectorPin.model_validate(item["pin"])
    operations = item["operations"]
    if type(operations) is not dict or not 1 <= len(operations) <= 64:
        raise ValueError("Invalid connector operations")
    described = {}
    for name, operation in operations.items():
        TypeAdapter(Identifier).validate_python(name, strict=True)
        if (
            type(operation) is not dict
            or set(operation) != {"effect", "requires_destination", "requires_secret"}
            or operation["effect"] not in ("none", "read", "write")
            or type(operation["requires_destination"]) is not bool
            or type(operation["requires_secret"]) is not bool
        ):
            raise ValueError("Invalid connector operation")
        described[name] = OperationDescriptor(
            operation["effect"], operation["requires_destination"], operation["requires_secret"]
        )
    return ConnectorDescriptor(pin, described)
