"""An operator's activity-worker configuration for real providers (outside test mode).

Strict, closed data, read from one private file. It names:

- `trust_file`: the control plane's activity-signing key set (a `TrustSet`), which the
  operator installs out of band and the worker rereads on every delivery;
- `journal_directory`: the private invocation journal;
- `secrets`: the mounted secret root, its mapping file, and which pinned secret
  (reference, version) each binding serves;
- `providers`: each real provider by fixed identifier (`https-json` or
  `anthropic-messages`), pinned to its catalog dependency, with its settings.

Each provider's settings are checked here against its pins before the worker starts,
so a mismatched routes document or model configuration never reaches a delivery.
Nothing in it is code, and no synthetic provider is accepted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lotuspond_sdk.executable.package import ArtifactRef

from .protocol import ConnectorPin, SecretPin
from .providers import ProviderDescriptor

REAL = {"https-json": "read", "anthropic-messages": "model"}


def _operator_path(value) -> Path:
    if type(value) is not str or not value.startswith("/") or ".." in value.split("/"):
        raise ValueError("Invalid operator path")
    return Path(value)


class OperatorTrust:
    """The operator-installed activity-signing `TrustSet`, reread on every call."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def current(self, scope, now_ms):
        from lotuspond_sdk.trust import TrustSet

        from .state import read_private_file

        return TrustSet.model_validate_json(read_private_file(self.path, 65536))


@dataclass(frozen=True)
class ActivitySetup:
    trust: object
    journal_directory: Path
    descriptors: tuple
    secrets: object
    test_mode: bool


def _descriptor(item) -> ProviderDescriptor:
    if type(item) is not dict or set(item) - {
        "provider_id",
        "dependency",
        "operations",
        "connector",
        "credentials",
        "settings",
    }:
        raise ValueError("Invalid provider")
    provider_id = item.get("provider_id")
    effect = REAL.get(provider_id)
    if effect is None:
        raise ValueError("Unknown provider")
    descriptor = ProviderDescriptor(
        provider_id,
        ArtifactRef.model_validate(item.get("dependency")),
        effect,
        frozenset(item.get("operations", ())),
        connector=(ConnectorPin.model_validate(item["connector"]) if effect == "read" else None),
        credentials=tuple(SecretPin.model_validate(pin) for pin in item.get("credentials", ())),
        settings=item.get("settings"),
    )
    # Fail at start, not at the first delivery.
    if provider_id == "https-json":
        from .https_json import HttpsJsonReadProvider

        HttpsJsonReadProvider(descriptor, ())
    else:
        from .anthropic_messages import validate_settings

        validate_settings(descriptor.settings_data(), descriptor.dependency)
        if len(descriptor.credentials) != 1:
            raise ValueError("The model provider needs exactly one key")
    return descriptor


def worker_config(raw: bytes, *, test_mode: bool = False) -> ActivitySetup:
    """The operator configuration. `test_mode` (a test-mode runtime only) lets an
    `https-json` route reach a loopback stand-in; nothing else changes."""
    from .activity_worker import MountedActivitySecrets
    from .resolver import MountedSecretResolver

    data = json.loads(raw)
    if type(data) is not dict or set(data) != {
        "trust_file",
        "journal_directory",
        "secrets",
        "providers",
    }:
        raise ValueError("Invalid activity worker configuration")
    trust = _operator_path(data["trust_file"])
    journal = _operator_path(data["journal_directory"])
    secrets = data["secrets"]
    if type(secrets) is not dict or set(secrets) != {"root", "mapping_file", "bindings"}:
        raise ValueError("Invalid secrets")
    bindings = {}
    for row in secrets["bindings"]:
        if type(row) is not dict or set(row) != {"reference_id", "version", "binding"}:
            raise ValueError("Invalid secret binding")
        pin = SecretPin(reference_id=row["reference_id"], version=row["version"])
        bindings[(pin.reference_id, pin.version)] = row["binding"]
    resolver = MountedSecretResolver(
        _operator_path(secrets["root"]), _operator_path(secrets["mapping_file"])
    )
    providers = data["providers"]
    if type(providers) is not list or not 1 <= len(providers) <= 8:
        raise ValueError("Invalid providers")
    return ActivitySetup(
        trust=OperatorTrust(trust),
        journal_directory=journal,
        descriptors=tuple(_descriptor(item) for item in providers),
        secrets=MountedActivitySecrets(resolver, bindings),
        test_mode=test_mode is True,
    )
