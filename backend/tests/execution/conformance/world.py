"""Task 13: one conformance world and its independent, test-only authorities.

A world is an owned PostgreSQL schema plus a private directory. Every authority the
engine consults outside its own rows (package trust and catalog, entitlement and
price, recovery epoch, artifact keys, source access, activity signing and trust)
is a small JSON file in that directory, reread on every call. So the API process,
the worker processes and the test process share one authority; a test changes it
by rewriting a file; and it lives outside the database, so a restore never rolls
it back. Never imported by the production API, and self-contained: it imports only
installed packages, so the installed acceptance can copy it outside the checkout.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

INSTALLATION = "test-installation"
MAX_MS = 9007199254740991


def write_private(path: Path, value) -> None:
    """Atomic 0600 replacement; readers see the old or the new file, never a part."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}")
    content = value if isinstance(value, (bytes, str)) else json.dumps(value, sort_keys=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content.encode() if isinstance(content, str) else content)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def private_directory(path: Path) -> Path:
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


class World:
    """The private directory of one world; `state(name)` reads, `update` rewrites."""

    def __init__(self, root):
        self.root = Path(root)

    @property
    def config(self) -> dict:
        return self.state("world")

    def path(self, name) -> Path:
        return self.root / name

    def state(self, name) -> dict:
        return json.loads((self.root / f"{name}.json").read_text())

    def write(self, name, value) -> None:
        write_private(self.root / f"{name}.json", value)

    def update(self, name, **changes) -> dict:
        value = {**self.state(name), **changes}
        self.write(name, value)
        return value


# ------------------------------------------------------------------ authorities


class FileRecovery:
    """The independent recovery authority: epoch and availability, outside the DB."""

    test_only = True

    def __init__(self, world: World):
        self.world = world

    def current(self, installation_id):
        from lotuspond.fleet.authority import RecoverySnapshot

        state = self.world.state("recovery")
        if not state["available"]:
            raise RuntimeError("PRIVATE_RECOVERY_OUTAGE")
        return RecoverySnapshot(installation_id, state["epoch"], True)


class FileEntitlement:
    """Entitlement, tenant policy and charges: one expiry and one execute switch."""

    test_only = True

    def __init__(self, world: World):
        self.world = world

    def _limits(self):
        from lotuspond.execution.contracts import ExecutionLimits

        return ExecutionLimits(**self.world.state("entitlement")["limits"])

    def current(self, scope, now_ms):
        from lotuspond.execution.entitlement import EntitlementGrant

        state = self.world.state("entitlement")
        return EntitlementGrant(
            scope=scope,
            installation_id=INSTALLATION,
            recovery_epoch=self.world.state("recovery")["epoch"],
            revision="conformance-revision",
            expires_at_ms=state["expires_at_ms"],
            execute=state["execute"],
            limits=self._limits(),
        )

    def policy(self, scope, now_ms):
        from lotuspond.execution.entitlement import PolicyGrant

        state = self.world.state("entitlement")
        return PolicyGrant(scope=scope, expires_at_ms=state["expires_at_ms"], limits=self._limits())

    def bound(self, dependency, demand):
        if self.world.state("entitlement")["price_expires_at_ms"] <= _wall_ms():
            raise RuntimeError("PRIVATE_PRICE_EXPIRED")
        return demand

    def bound_action(self, binding):
        """E05: an action's dispatch is bounded by the same price authority."""
        if self.world.state("entitlement")["price_expires_at_ms"] <= _wall_ms():
            raise RuntimeError("PRIVATE_PRICE_EXPIRED")
        return True


class FileTenant:
    test_only = True

    def __init__(self, entitlement: FileEntitlement):
        self.entitlement = entitlement

    def current(self, scope, now_ms):
        return self.entitlement.policy(scope, now_ms)


class FileAuthority:
    """Package trust and the catalog digests an operator has admitted."""

    test_only = True

    def __init__(self, world: World):
        self.world = world

    def current(self, scope):
        state = self.world.state("authority")
        return {**state["snapshot"], "scope": scope.model_dump(), "catalog": state["catalog"]}


class FileKeys:
    test_only = True

    def __init__(self, world: World):
        self.world = world

    def key(self, scope, key_id):
        return bytes.fromhex(self.world.state("keys")["artifact"])


class FileAccess:
    test_only = True

    def __init__(self, world: World):
        self.world = world

    def current(self, actor, source_id, version, now_ms):
        return self.world.state("access")["allowed"] is True


class FileActivityTrust:
    """The control plane's view of activity signing trust (the runtime has its own file)."""

    test_only = True

    def __init__(self, world: World):
        self.world = world

    def current(self, scope, now_ms):
        from lotuspond_sdk.trust import TrustSet

        return TrustSet.model_validate(self.world.state("activity-trust"))


class FileSigner:
    test_only = True

    def __init__(self, world: World):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        raw = bytes.fromhex(world.state("keys")["activity"])
        self.key = Ed25519PrivateKey.from_private_bytes(raw)

    def sign(self, envelope):
        from lotuspond_sdk.activity import sign_activity

        return sign_activity(envelope, self.key)


class Demand:
    """Bounded per-attempt demand; leases never outlive the reserved active time."""

    test_only = True

    def __init__(self, world: World):
        self.world = world

    def demand(self, attempt_id):
        from lotuspond.execution.budgets import BudgetDemand

        return BudgetDemand(**self.world.state("entitlement")["demand"])


class FileCommandKey:
    """E05: the operator's command signing key and its verification trust (E03).

    Both are files outside the database. `available: false` makes the trust lookup
    fail, as an unprovisioned or unreachable key provider does."""

    test_only = True

    def __init__(self, world: World):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self.world = world
        raw = bytes.fromhex(world.state("keys")["command"])
        self.key = Ed25519PrivateKey.from_private_bytes(raw)

    def current(self, installation_id):
        from lotuspond_runtime.errors import FleetError
        from lotuspond_runtime.protocol import CommandTrustSnapshot
        from lotuspond_sdk.trust import TrustSet

        state = self.world.state("actions")
        if not state["command_key_available"]:
            raise FleetError("trust_unavailable")
        trust = TrustSet.model_validate(trust_set(self.world.state("keys")["command"]))
        # Inside E03's 60 s by a margin: it is checked against a `now` read earlier.
        return CommandTrustSnapshot(1, trust, _wall_ms() + 50000)

    def sign(self, command, snapshot):
        from lotuspond_sdk.executable.signing import sign_runtime_command

        return sign_runtime_command(command, self.key)


def file_catalog(world: World):
    """The installed descriptors of the world's synthetic connectors (test only)."""
    from types import MappingProxyType

    from lotuspond_runtime.protocol import ConnectorPin

    from lotuspond.fleet.catalog import ConnectorDescriptor, InstalledCatalog, OperationDescriptor

    connectors = {}
    for item in world.state("actions")["connectors"]:
        pin = ConnectorPin.model_validate(item["pin"])
        operations = {
            name: OperationDescriptor(effect, False, False)
            for name, effect in item["operations"].items()
        }
        connectors[pin] = ConnectorDescriptor(pin, operations)
    catalog = InstalledCatalog()
    object.__setattr__(catalog, "_connectors", MappingProxyType(connectors))
    object.__setattr__(catalog, "test_only", True)
    return catalog


def actions_mode(world: World, connector: dict | list) -> dict:
    """Give a world E05's action authorities before any process starts.

    A command signing key and its trust, and the connector (or each of several)
    installed in the catalog. Composition then wires interpreter v2 and the action
    services; a world without this keeps E04's exact v1 graph. Returns the (first)
    connector pin.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    keys = world.state("keys")
    if "command" not in keys:
        world.update("keys", command=Ed25519PrivateKey.generate().private_bytes_raw().hex())
    installed = []
    for document in connector if isinstance(connector, list) else [connector]:
        pin = {
            "name": document["name"],
            "version": document["version"],
            "artifact_sha256": document["definition"]["artifact_sha256"],
        }
        operations = {op["id"]: op["effect"] for op in document["definition"]["operations"]}
        installed.append({"pin": pin, "operations": operations})
    world.write("actions", {"command_key_available": True, "connectors": installed})
    return installed[0]["pin"]


def _wall_ms():
    import time

    return time.time_ns() // 1_000_000


# ------------------------------------------------------------------ creation


def operator_mode(world: World) -> Path:
    """Switch a world to an operator authority directory before any process starts.

    The directory is created by `python -m lotuspond.authority` exactly as an operator
    would: the world's package key becomes a trusted publisher, execution is switched
    on, and release decisions are not required (milestone 5 adds the release binding).
    Runtimes pin the directory's activity trust.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from lotuspond.authority import init, switch, trust_publisher

    directory = world.root / "operator-authority"
    init(directory, INSTALLATION)
    package = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(world.state("keys")["package"]))
    trust_publisher(directory, package.public_key().public_bytes_raw().hex())
    switch(directory, "entitlement.json", "execute", True)
    switch(directory, "publishers.json", "release_required", False)
    world.write("activity-trust", json.loads((directory / "activity-trust.json").read_text()))
    world.write("operator", {"dir": str(directory)})
    return directory


def trust_set(private_hex: str) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    public = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex)).public_key()
    raw = public.public_bytes_raw()
    return {
        "format_version": 1,
        "keys": [{"key_id": sha256(raw).hexdigest(), "public_key": raw.hex(), "state": "allowed"}],
    }


def create(root, database_url: str, schema: str, *, limits=None, demand=None) -> World:
    """A new world directory; its schema must already exist and be empty."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    world = World(private_directory(root))

    def private_hex():
        return Ed25519PrivateKey.generate().private_bytes_raw().hex()

    package, activity = private_hex(), private_hex()
    world.write(
        "world",
        {
            "database_url": database_url,
            "schema": schema,
            "installation_id": INSTALLATION,
            "origin": "https://conformance.example.com",
        },
    )
    world.write(
        "keys", {"artifact": os.urandom(32).hex(), "package": package, "activity": activity}
    )
    world.write("recovery", {"epoch": 1, "available": True})
    world.write(
        "entitlement",
        {
            "expires_at_ms": MAX_MS,
            "price_expires_at_ms": MAX_MS,
            "execute": True,
            "limits": limits
            or {
                "max_steps": 10000,
                "active_seconds": 86400,
                "elapsed_seconds": 31536000,
                "retained_waits": 10000,
                "max_input_tokens": 10000000,
                "max_output_tokens": 10000000,
                "max_cost_microusd": 1000000000,
                "active_capacity": 5,
                "pending_capacity": 1000,
            },
            "demand": demand
            or {"input_tokens": 10, "output_tokens": 10, "cost_microusd": 10, "active_ms": 10000},
        },
    )
    world.write(
        "authority",
        {
            "snapshot": {
                "expires_at_ms": MAX_MS,
                "trust": trust_set(package),
                "release_required": False,
                "capabilities": [],
                "destination_classes": [],
                "operation_classes": [],
            },
            "catalog": [],
        },
    )
    world.write("access", {"allowed": True})
    world.write("activity-trust", trust_set(activity))
    return world


def admit_catalog(world: World, digest: str) -> None:
    """An operator admits one catalog document digest (before it is published)."""
    if (world.root / "operator.json").exists():
        from lotuspond.authority import admit

        admit(Path(world.state("operator")["dir"]), [digest])
        return
    state = world.state("authority")
    if digest not in state["catalog"]:
        world.update("authority", catalog=[*state["catalog"], digest])


@contextmanager
def owned(url, root):
    """An owned schema on `url`, initialized at the current version, and its directory."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from lotuspond import initialize, models

    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost", "::1"}
    assert parsed.database == "lotuspond_platform_test"
    schema = f"conformance_{uuid4().hex}"
    admin = create_engine(url, hide_parameters=True, pool_pre_ping=True)
    observer, graphs = None, []
    try:
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        # Everything after the schema exists is inside the try: a failed setup still
        # drops it and confirms that.
        created = create(root, url, schema)
        from harness import scoped_url, settings_for

        initialize.initialize(settings_for(created))
        observer = create_engine(scoped_url(created), hide_parameters=True, pool_pre_ping=True)
        with Session(observer) as db, db.begin():
            # Operator provisioning (Task 14's procedure): the installation's cursor.
            db.add(models.ExecutionSchedulerCursor(installation_id=INSTALLATION))
        created.engine = observer

        def graph():
            """The test process's own service graph over this world (built once)."""
            from harness import build

            if not graphs:
                graphs.append(build(created))
            return graphs[0]

        created.graph = graph
        yield created
    finally:
        for built in graphs:
            built.engine.dispose()
        if observer is not None:
            observer.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            remaining = db.scalar(
                text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema"),
                {"schema": schema},
            )
        admin.dispose()
        assert remaining == 0, "owned schema cleanup unconfirmed"
