"""Create and edit an installation's operator authority directory.

    python -m lotuspond.authority init DIR --installation-id ID
    python -m lotuspond.authority trust-publisher DIR PUBLIC_KEY_HEX
    python -m lotuspond.authority admit DIR DIGEST [DIGEST ...]
    python -m lotuspond.authority execution DIR on|off
    python -m lotuspond.authority release-required DIR on|off
    python -m lotuspond.authority new-epoch DIR
    python -m lotuspond.authority command-key DIR
    python -m lotuspond.authority command-trust DIR
    python -m lotuspond.authority install-connector DIR FILE
    python -m lotuspond.authority secret-binding DIR BINDING_ID VERSION
    python -m lotuspond.authority show DIR

`init` creates the directory (it must not exist or must be empty) with a new activity
signing key and its trust set, a new artifact key, recovery epoch 1, no trusted
publisher, no admitted catalog document, release decisions required, and execution
switched off. An operator turns execution on deliberately. `new-epoch` is the step
after a database restore. `command-key` provisions the key that signs runtime write
commands (E05), which switches on reviewed actions when the API and workers next start;
`command-trust` prints its public trust (revision and key set) for each runtime's
`actions.trust_file`.
`install-connector` and `secret-binding` add to the installed catalog (read at start).
`show` prints a summary and never key material. Every file
is written atomically, owner-only. The directory is read by
`lotuspond.execution.operator_authority`; see that module for its format.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from hashlib import sha256
from pathlib import Path

DEFAULT_LIMITS = {
    "max_steps": 10000,
    "active_seconds": 86400,
    "elapsed_seconds": 31536000,
    "retained_waits": 10000,
    "max_input_tokens": 10000000,
    "max_output_tokens": 10000000,
    "max_cost_microusd": 1000000000,
    "active_capacity": 5,
    "pending_capacity": 1000,
}
# Each attempt's reservation, the same for every kind of attempt (a read reserves what
# a model round does). Once an attempt stops it is charged what it reported, so these
# only bound what is in flight; they must leave room under an agent's own limits.
DEFAULT_DEMAND = {
    "input_tokens": 8000,
    "output_tokens": 1000,
    "cost_microusd": 2000,
    "active_ms": 20000,
}


def _write(path: Path, value) -> None:
    content = value if isinstance(value, str) else json.dumps(value, indent=1, sort_keys=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content + ("" if content.endswith("\n") else "\n"))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def _read(root: Path, name: str):
    return json.loads((root / name).read_text())


def _trust_entry(public: bytes) -> dict:
    return {"key_id": sha256(public).hexdigest(), "public_key": public.hex(), "state": "allowed"}


def init(root: Path, installation_id: str) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if root.exists() and any(root.iterdir()):
        raise SystemExit("refused: the directory is not empty")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    (root / "artifact-keys").mkdir(mode=0o700)
    signing = Ed25519PrivateKey.generate()
    _write(root / "installation.json", {"installation_id": installation_id})
    _write(root / "recovery.json", {"epoch": 1, "active": True})
    _write(
        root / "entitlement.json",
        {"revision": "1", "execute": False, "limits": DEFAULT_LIMITS, "demand": DEFAULT_DEMAND},
    )
    _write(
        root / "publishers.json",
        {"trust": {"format_version": 1, "keys": []}, "catalog": [], "release_required": True},
    )
    _write(root / "activity-signing.key", signing.private_bytes_raw().hex())
    _write(
        root / "activity-trust.json",
        {"format_version": 1, "keys": [_trust_entry(signing.public_key().public_bytes_raw())]},
    )
    _write(root / "artifact-keys" / "primary", secrets.token_bytes(32).hex())
    _write(root / "access.json", {"knowledge_sources": "none"})


def trust_publisher(root: Path, public_hex: str) -> None:
    public = bytes.fromhex(public_hex)
    if len(public) != 32:
        raise SystemExit("refused: an Ed25519 public key is 32 bytes")
    state = _read(root, "publishers.json")
    entry = _trust_entry(public)
    if all(key["key_id"] != entry["key_id"] for key in state["trust"]["keys"]):
        state["trust"]["keys"].append(entry)
    _write(root / "publishers.json", state)


def admit(root: Path, digests: list[str]) -> None:
    if not all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests):
        raise SystemExit("refused: a catalog digest is 64 lowercase hex characters")
    state = _read(root, "publishers.json")
    state["catalog"] = sorted(set(state.get("catalog", [])) | set(digests))
    _write(root / "publishers.json", state)


def switch(root: Path, name: str, key: str, on: bool) -> None:
    state = _read(root, name)
    state[key] = on
    if name == "entitlement.json":
        state["revision"] = str(int(state["revision"]) + 1)
    _write(root / name, state)


def new_epoch(root: Path) -> None:
    state = _read(root, "recovery.json")
    state["epoch"] += 1
    _write(root / "recovery.json", state)


def command_key(root: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if (root / "command-signing.key").exists() or (root / "command-trust.json").exists():
        raise SystemExit("refused: a command key is already provisioned")
    signing = Ed25519PrivateKey.generate()
    entry = _trust_entry(signing.public_key().public_bytes_raw())
    # The trust first: a key file without its trust would switch actions on unverifiable.
    _write(
        root / "command-trust.json",
        {"revision": 1, "trust": {"format_version": 1, "keys": [entry]}},
    )
    _write(root / "command-signing.key", signing.private_bytes_raw().hex())


def command_trust(root: Path) -> dict:
    """`{"revision", "trust"}`: the public command trust each runtime pins. A runtime
    refuses a command issued under a newer revision than its own, so it carries both."""
    return _read(root, "command-trust.json")


def _catalog(root: Path) -> dict:
    path = root / "catalog.json"
    return (
        _read(root, "catalog.json")
        if path.exists()
        else {
            "connectors": [],
            "secret_bindings": [],
        }
    )


def install_connector(root: Path, description: dict) -> None:
    from lotuspond.fleet.catalog import InstalledCatalog, connector_descriptor

    try:
        pin = connector_descriptor(description).pin
    except Exception:
        raise SystemExit("refused: not a valid connector description") from None
    state = _catalog(root)
    for item in state["connectors"]:
        if item["pin"] == pin.model_dump():
            if item == description:
                return
            raise SystemExit("refused: this connector version is installed differently")
    state["connectors"].append(description)
    InstalledCatalog.installed(state)
    _write(root / "catalog.json", state)


def secret_binding(root: Path, binding_id: str, version: int) -> None:
    from lotuspond.fleet.catalog import InstalledCatalog

    state = _catalog(root)
    row = {"binding_id": binding_id, "version": version}
    if row not in state["secret_bindings"]:
        state["secret_bindings"].append(row)
    try:
        InstalledCatalog.installed(state)
    except Exception:
        raise SystemExit("refused: not a valid secret binding") from None
    _write(root / "catalog.json", state)


def show(root: Path) -> dict:
    publishers = _read(root, "publishers.json")
    entitlement = _read(root, "entitlement.json")
    return {
        "installation_id": _read(root, "installation.json")["installation_id"],
        "recovery_epoch": _read(root, "recovery.json")["epoch"],
        "execution": "on" if entitlement["execute"] else "off",
        "release_required": publishers.get("release_required", True),
        "trusted_publishers": [key["key_id"] for key in publishers["trust"]["keys"]],
        "admitted_catalog_documents": len(publishers.get("catalog", [])),
        "command_key": (
            [key["key_id"] for key in command_trust(root)["trust"]["keys"]]
            if (root / "command-signing.key").exists()
            else None
        ),
        "installed_connectors": [
            f"{item['pin']['name']}@{item['pin']['version']}"
            for item in _catalog(root)["connectors"]
        ],
        "secret_bindings": len(_catalog(root)["secret_bindings"]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lotuspond.authority", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("init")
    p.add_argument("dir")
    p.add_argument("--installation-id", required=True)
    p = commands.add_parser("trust-publisher")
    p.add_argument("dir")
    p.add_argument("public_key")
    p = commands.add_parser("admit")
    p.add_argument("dir")
    p.add_argument("digests", nargs="+")
    for name in ("execution", "release-required"):
        p = commands.add_parser(name)
        p.add_argument("dir")
        p.add_argument("state", choices=("on", "off"))
    for name in ("new-epoch", "show", "command-key", "command-trust"):
        commands.add_parser(name).add_argument("dir")
    p = commands.add_parser("install-connector")
    p.add_argument("dir")
    p.add_argument("file")
    p = commands.add_parser("secret-binding")
    p.add_argument("dir")
    p.add_argument("binding_id")
    p.add_argument("version", type=int)
    args = parser.parse_args(argv)
    root = Path(args.dir).absolute()
    if args.command == "init":
        init(root, args.installation_id)
    elif args.command == "trust-publisher":
        trust_publisher(root, args.public_key)
    elif args.command == "admit":
        admit(root, args.digests)
    elif args.command == "execution":
        switch(root, "entitlement.json", "execute", args.state == "on")
    elif args.command == "release-required":
        switch(root, "publishers.json", "release_required", args.state == "on")
    elif args.command == "new-epoch":
        new_epoch(root)
    elif args.command == "command-key":
        command_key(root)
    elif args.command == "command-trust":
        print(json.dumps(command_trust(root), indent=1, sort_keys=True))
        return 0
    elif args.command == "install-connector":
        install_connector(root, json.loads(Path(args.file).read_text()))
    elif args.command == "secret-binding":
        secret_binding(root, args.binding_id, args.version)
    print(json.dumps(show(root), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
