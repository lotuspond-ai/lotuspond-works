"""Bounded finite input projection and JCS; no SDK private implementation."""

from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING, get_args

import rfc8785
from pydantic import BaseModel, SecretStr

from .errors import FleetError

if TYPE_CHECKING:
    from .protocol import ConnectionConfig, PoolPolicy

MAX_SAFE_INTEGER = 9007199254740991
MAX_JSON_BYTES = 65536
MAX_CANONICAL_BYTES = 32768
MAX_DEPTH = 16
MAX_VALUES = 4096
MAX_STRING_BYTES = 8192


def _project_fleet_value(raw: object, *, models: bool = False) -> object:
    """Copy only finite supported values; never invoke arbitrary serializers."""
    count = 0
    ancestors: set[int] = set()

    def walk(value: object, depth: int) -> object:
        nonlocal count
        count += 1
        if count > MAX_VALUES or depth > MAX_DEPTH:
            raise FleetError("input_limit")
        kind = type(value)
        if value is None or kind is bool:
            return value
        if kind is str:
            try:
                size = len(value.encode("utf-8"))
            except UnicodeError:
                raise FleetError("invalid_request") from None
            if size > MAX_STRING_BYTES:
                raise FleetError("input_limit")
            return value
        if kind is int:
            if abs(value) > MAX_SAFE_INTEGER:
                raise FleetError("invalid_request")
            return value
        if kind is float:
            if not math.isfinite(value):
                raise FleetError("invalid_request")
            return value
        if models and kind is SecretStr:
            try:
                inner = value.get_secret_value()
            except AttributeError:
                raise FleetError("invalid_request") from None
            if type(inner) is not str:
                raise FleetError("invalid_request")
            walk(inner, depth)
            return value
        if models and isinstance(value, BaseModel):
            # No model_dump hook: constructed or mutated instances must not bypass
            # field/extra checks, and their nested records must be revalidated.
            fields = type(value).model_fields
            data = value.__dict__
            for key, field in fields.items():
                annotation = field.annotation
                if SecretStr in (annotation, *get_args(annotation)) and key in data:
                    if data[key] is not None and type(data[key]) is not SecretStr:
                        raise FleetError("invalid_request")
            if any(key not in fields for key in data) or value.__pydantic_extra__:
                raise FleetError("invalid_request")
            container = {key: data[key] for key in fields if key in data}
        elif kind is dict or kind is list or (models and kind is tuple):
            container = value
        else:
            raise FleetError("invalid_request")
        identity = id(value)
        if identity in ancestors:
            raise FleetError("invalid_request")
        ancestors.add(identity)
        try:
            if type(container) is dict:
                result = {}
                for key, item in container.items():
                    if type(key) is not str:
                        raise FleetError("invalid_request")
                    walk(key, depth + 1)
                    result[key] = walk(item, depth + 1)
                return result
            return [walk(item, depth + 1) for item in container]
        finally:
            ancestors.remove(identity)

    return walk(raw, 0)


def decode_fleet_json(content: bytes) -> object:
    if type(content) is not bytes:
        raise FleetError("invalid_request")
    if len(content) > MAX_JSON_BYTES:
        raise FleetError("input_limit")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in items:
            if key in result:
                raise FleetError("invalid_request")
            result[key] = value
        return result

    def nonfinite(_: str) -> None:
        raise FleetError("invalid_request")

    try:
        raw = json.loads(content.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
        return _project_fleet_value(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise FleetError("invalid_request") from None


def _canonical(raw: object) -> bytes:
    try:
        result = rfc8785.dumps(raw)
    except (ValueError, TypeError, RecursionError):
        raise FleetError("invalid_request") from None
    if len(result) > MAX_CANONICAL_BYTES:
        raise FleetError("input_limit")
    return result


def _connection_projection(model: ConnectionConfig) -> dict[str, object]:
    # Explicit public fields only; never include credential or secret values.
    return {
        "format_version": model.format_version,
        "connector": _connector(model.connector),
        "operations": sorted(model.operations),
        "destinations": [
            {"host": pin.host, "port": pin.port, "path_prefix": pin.path_prefix}
            for pin in sorted(
                model.destinations, key=lambda pin: (pin.host, pin.port, pin.path_prefix)
            )
        ],
        "secrets": [
            {"reference_id": pin.reference_id, "version": pin.version}
            for pin in sorted(model.secrets, key=lambda pin: (pin.reference_id, pin.version))
        ],
        "data_handling": {
            "retain_payloads": model.data_handling.retain_payloads,
            "max_request_bytes": model.data_handling.max_request_bytes,
            "max_response_bytes": model.data_handling.max_response_bytes,
            "timeout_ms": model.data_handling.timeout_ms,
        },
    }


def _connector(pin) -> dict[str, str]:
    return {"name": pin.name, "version": pin.version, "artifact_sha256": pin.artifact_sha256}


def _pool_projection(model: PoolPolicy) -> dict[str, object]:
    return {
        "format_version": model.format_version,
        "connectors": [
            _connector(pin)
            for pin in sorted(
                model.connectors, key=lambda pin: (pin.name, pin.version, pin.artifact_sha256)
            )
        ],
        "platforms": sorted(model.platforms),
        "connection_ids": sorted(model.connection_ids),
        "max_instances": model.max_instances,
        "max_concurrent_invocations": model.max_concurrent_invocations,
    }


def canonical_connection_json(raw: object) -> bytes:
    from .protocol import validate_connection_config

    return _canonical(_connection_projection(validate_connection_config(raw)))


def connection_digest(raw: object) -> str:
    return hashlib.sha256(
        b"lotuspond-connection-config-v1\0" + canonical_connection_json(raw)
    ).hexdigest()


def canonical_pool_json(raw: object) -> bytes:
    from .protocol import validate_pool_policy

    return _canonical(_pool_projection(validate_pool_policy(raw)))


def pool_digest(raw: object) -> str:
    return hashlib.sha256(
        b"lotuspond-runtime-pool-policy-v1\0" + canonical_pool_json(raw)
    ).hexdigest()
