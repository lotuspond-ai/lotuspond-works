"""Production authorities read from an operator's private directory, outside the database.

E04 keeps recovery, trust, entitlement and key material out of the database so that a
database restore can never rewind them. This is that authority for a single
installation: one private directory, created and edited only by its operator with
`python -m lotuspond.authority`, and reread on every lookup (each lookup is already
bounded and serialized by its caller's slot). Nothing here is cached, and every answer
carries the operator's stated expiry (until the file changes, by default), so an
operator's change (revoking a publisher, stopping execution, a new recovery epoch)
takes effect on the next lookup.

Files (all private: owner-only, no links):

- `installation.json` - `{"installation_id"}`, which must equal the configured one;
- `recovery.json` - `{"epoch", "active"}`;
- `entitlement.json` - `{"revision", "execute", "limits", "demand"}`: whether this
  installation may execute at all, its limits and each attempt's reserved demand.
  There are no commercial terms (E08 is out of scope);
- `publishers.json` - the package publisher `TrustSet`, the admitted catalog
  digests, and `release_required` (true unless the operator turns it off);
- `activity-trust.json` and `activity-signing.key` - the control plane's activity
  signing key and the trust set the runtimes pin;
- `artifact-keys/<key_id>` - 32-byte artifact encryption keys, hex;
- `access.json` - `{"knowledge_sources": "none" | "workspace_members"}`;
- `command-signing.key` and `command-trust.json` - optional (E05): the key that signs
  runtime write commands and `{"revision", "trust"}`, its verification trust. Present
  when the API or worker starts, they switch on interpreter v2 and the action and task
  services; absent, only interpreter v1 runs and those routes answer 503;
- `catalog.json` - optional: the installed connectors and secret bindings
  (`InstalledCatalog.installed`), read once at start. Absent, nothing is installed.

A missing, unreadable or malformed file denies (each caller maps it to its fixed
`*_unavailable` code). These providers are not test-only.
"""

from __future__ import annotations

import json
import os
import stat
import time
from hashlib import sha256
from pathlib import Path

# A command trust snapshot's life: inside E03's 60 s by the margin the runtime's own trust
# uses, because the caller validates it against a `now` read just before asking.
COMMAND_TRUST_MS = 50_000

# Grants and snapshots carry the operator's stated expiry, never "now plus a window":
# the engine compares two lookups of the same grant for equality, so an unchanged file
# must give an identical answer. Without one, a grant stands until the operator edits
# the file (every lookup rereads it, so revocation is immediate).
UNTIL_CHANGED_MS = 253402300799000
MAX_FILE = 262_144


class OperatorDirectory:
    """Strict private reads from one operator directory."""

    def __init__(self, root, installation_id: str):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise ValueError("The authority directory must be an absolute path")
        self.installation_id = installation_id

    def _read(self, name: str) -> bytes:
        root = os.stat(self.root, follow_symlinks=False)
        if not stat.S_ISDIR(root.st_mode) or root.st_mode & 0o077 or root.st_uid != os.geteuid():
            raise PermissionError("the authority directory is not private")
        path = self.root / name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid != os.geteuid()
                or info.st_size > MAX_FILE
            ):
                raise PermissionError("authority file is not private")
            data = os.read(fd, MAX_FILE + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_FILE:
            raise ValueError("authority file too large")
        return data

    def exists(self, name: str) -> bool:
        try:
            os.stat(self.root / name, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def json(self, name: str):
        value = json.loads(self._read(name))
        if type(value) is not dict:
            raise ValueError("authority file must hold an object")
        return value

    def check_installation(self, installation_id: str) -> None:
        if (
            installation_id != self.installation_id
            or self.json("installation.json").get("installation_id") != installation_id
        ):
            raise PermissionError("another installation's authority")


class OperatorRecovery:
    def __init__(self, directory: OperatorDirectory):
        self.directory = directory

    def current(self, installation_id):
        from lotuspond.fleet.authority import RecoverySnapshot

        self.directory.check_installation(installation_id)
        state = self.directory.json("recovery.json")
        return RecoverySnapshot(installation_id, state["epoch"], state["active"])


class OperatorEntitlement:
    """Entitlement, tenant policy, charges and demand for this installation."""

    def __init__(self, directory: OperatorDirectory):
        self.directory = directory

    def _state(self):
        return self.directory.json("entitlement.json")

    def _limits(self, state):
        from lotuspond.execution.contracts import ExecutionLimits

        return ExecutionLimits(**state["limits"])

    def current(self, scope, now_ms):
        from lotuspond.execution.entitlement import EntitlementGrant

        self.directory.check_installation(self.directory.installation_id)
        state = self._state()
        recovery = self.directory.json("recovery.json")
        return EntitlementGrant(
            scope=scope,
            installation_id=self.directory.installation_id,
            recovery_epoch=recovery["epoch"],
            revision=state["revision"],
            expires_at_ms=state.get("expires_at_ms", UNTIL_CHANGED_MS),
            execute=state["execute"] is True,
            limits=self._limits(state),
        )

    def policy(self, scope, now_ms):
        from lotuspond.execution.entitlement import PolicyGrant

        state = self._state()
        return PolicyGrant(
            scope=scope,
            expires_at_ms=state.get("expires_at_ms", UNTIL_CHANGED_MS),
            limits=self._limits(state),
        )

    def bound(self, dependency, demand):
        # No metering or prices here (E08 is out of scope): the demand itself is the bound.
        return demand

    def demand(self, attempt_id):
        from lotuspond.execution.budgets import BudgetDemand

        return BudgetDemand(**self._state()["demand"])


class OperatorTenant:
    def __init__(self, entitlement: OperatorEntitlement):
        self.entitlement = entitlement

    def current(self, scope, now_ms):
        return self.entitlement.policy(scope, now_ms)


class OperatorPublishers:
    """Package publisher trust and the catalog digests the operator has admitted."""

    def __init__(self, directory: OperatorDirectory):
        self.directory = directory

    def current(self, scope):
        state = self.directory.json("publishers.json")
        return {
            "scope": scope.model_dump(),
            "expires_at_ms": state.get("expires_at_ms", UNTIL_CHANGED_MS),
            "trust": state["trust"],
            "catalog": state.get("catalog", []),
            "release_required": state.get("release_required", True) is not False,
            "capabilities": state.get("capabilities", []),
            "destination_classes": state.get("destination_classes", []),
            "operation_classes": state.get("operation_classes", []),
        }


class OperatorKeys:
    def __init__(self, directory: OperatorDirectory):
        self.directory = directory

    def key(self, scope, key_id):
        if not key_id.replace("-", "").isalnum() or len(key_id) > 64:
            raise ValueError("invalid key id")
        key = bytes.fromhex(self.directory._read(f"artifact-keys/{key_id}").decode().strip())
        if len(key) != 32:
            raise ValueError("artifact keys are 32 bytes")
        return key


class OperatorAccess:
    """Knowledge-source access: none unless the operator allows workspace members."""

    def __init__(self, directory: OperatorDirectory):
        self.directory = directory

    def current(self, actor, source_id, version, now_ms):
        return self.directory.json("access.json").get("knowledge_sources") == "workspace_members"


class OperatorActivityTrust:
    def __init__(self, directory: OperatorDirectory):
        self.directory = directory

    def current(self, scope, now_ms):
        from lotuspond_sdk.trust import TrustSet

        return TrustSet.model_validate_json(self.directory._read("activity-trust.json"))


class OperatorSigner:
    """Signs activity deliveries with the installation's key, read for each signature."""

    def __init__(self, directory: OperatorDirectory):
        self.directory = directory

    def sign(self, envelope):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from lotuspond_sdk.activity import sign_activity

        raw = bytes.fromhex(self.directory._read("activity-signing.key").decode().strip())
        return sign_activity(envelope, Ed25519PrivateKey.from_private_bytes(raw))


class OperatorCommandKey:
    """E05's command key provider: signs runtime write commands, answers their trust.

    Both files are reread on every call, so replacing or removing them takes effect on
    the next command. Any failure is `trust_unavailable`, never a partial answer.
    """

    test_only = False

    def __init__(self, directory: OperatorDirectory):
        self.directory = directory

    def current(self, installation_id):
        from lotuspond_runtime.errors import FleetError
        from lotuspond_runtime.protocol import CommandTrustSnapshot
        from lotuspond_sdk.trust import TrustSet

        try:
            self.directory.check_installation(installation_id)
            state = self.directory.json("command-trust.json")
            if set(state) != {"revision", "trust"} or type(state["revision"]) is not int:
                raise ValueError("invalid command trust")
            return CommandTrustSnapshot(
                state["revision"],
                TrustSet.model_validate(state["trust"]),
                time.time_ns() // 1_000_000 + COMMAND_TRUST_MS,
            )
        except Exception:
            raise FleetError("trust_unavailable") from None

    def sign(self, command, snapshot):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from lotuspond_runtime.errors import FleetError
        from lotuspond_runtime.trust import validate_snapshot
        from lotuspond_sdk.executable.signing import sign_runtime_command, verify_runtime_command

        try:
            raw = bytes.fromhex(self.directory._read("command-signing.key").decode().strip())
            key = Ed25519PrivateKey.from_private_bytes(raw)
            snapshot = validate_snapshot(snapshot, now_ms=time.time_ns() // 1_000_000)
            key_id = sha256(key.public_key().public_bytes_raw()).hexdigest()
            if not any(
                item.key_id == key_id and item.state == "allowed" for item in snapshot.trust.keys
            ):
                raise ValueError("the signing key is not allowed")
            envelope = sign_runtime_command(command, key)
            if not verify_runtime_command(command, envelope, snapshot.trust).valid:
                raise ValueError("the signature does not verify")
            return envelope
        except Exception:
            raise FleetError("trust_unavailable") from None


def operator_catalog(directory: OperatorDirectory):
    """The installed catalog, read once: `FleetDependencies` holds an immutable one."""
    from lotuspond.fleet.catalog import InstalledCatalog

    if not directory.exists("catalog.json"):
        return InstalledCatalog()
    return InstalledCatalog.installed(directory.json("catalog.json"))


class OperatorAuthorities:
    """Every authority the execution services need, from one operator directory."""

    def __init__(self, root, installation_id: str):
        directory = OperatorDirectory(root, installation_id)
        self.directory = directory
        self.recovery = OperatorRecovery(directory)
        self.entitlement = OperatorEntitlement(directory)
        self.tenant = OperatorTenant(self.entitlement)
        self.charges = self.entitlement
        self.demand = self.entitlement
        self.publishers = OperatorPublishers(directory)
        self.keys = OperatorKeys(directory)
        self.access = OperatorAccess(directory)
        self.activity_trust = OperatorActivityTrust(directory)
        self.signer = OperatorSigner(directory)
        # Read at start: a malformed catalog refuses to start rather than run without it.
        self.catalog = operator_catalog(directory)
        # Actions are switched on by provisioning a command key, never by a flag.
        self.commands = (
            OperatorCommandKey(directory) if directory.exists("command-signing.key") else None
        )
        self.artifact_key_id = "primary"
        self.retain_until_ms = UNTIL_CHANGED_MS  # until the operator's own retention runs
        self.test_mode = False
