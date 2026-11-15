"""E05 outside test mode: an operator's write providers and the command trust it pins."""

import hashlib
import json
import sys

import pytest
from test_https_json import write_routes

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.https_json import routes_digest
from lotuspond_runtime.trust import OperatorCommandTrust

# Private operator files are Linux-only (`state.private_directory`).
linux = pytest.mark.skipif(sys.platform != "linux", reason="private storage is Linux-only")

_PUBLIC = bytes(range(32))
ENTRY = {
    "key_id": hashlib.sha256(_PUBLIC).hexdigest(),
    "public_key": _PUBLIC.hex(),
    "state": "allowed",
}


class Clock:
    def __init__(self, now=1_000_000):
        self.now = now

    def now_ms(self):
        return self.now


def private(path, value):
    path.parent.chmod(0o700)  # a private file in a private directory
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    path.chmod(0o600)
    return path


@linux
def test_operator_command_trust_carries_its_revision_and_is_reread(tmp_path):
    path = private(
        tmp_path / "command-trust.json",
        {"revision": 3, "trust": {"format_version": 1, "keys": [ENTRY]}},
    )
    trust = OperatorCommandTrust(path, Clock())
    snapshot = trust.current("installation-1")
    # The runtime refuses a command issued under a newer revision than its own.
    assert snapshot.revision == 3
    assert snapshot.valid_until_ms == 1_000_000 + 50_000  # inside E03's 60 s
    assert [key.key_id for key in snapshot.trust.keys] == [ENTRY["key_id"]]
    private(path, {"revision": 4, "trust": {"format_version": 1, "keys": []}})
    assert trust.current("installation-1").revision == 4  # a rewritten file is seen at once


@linux
@pytest.mark.parametrize(
    "content",
    [
        {"format_version": 1, "keys": [ENTRY]},  # a bare trust set has no revision
        {"revision": "1", "trust": {"format_version": 1, "keys": []}},
        {"revision": 1, "trust": {"format_version": 1, "keys": []}, "extra": True},
        "not json",
    ],
)
def test_operator_command_trust_refuses_anything_else(tmp_path, content):
    path = private(tmp_path / "command-trust.json", content)
    with pytest.raises(FleetError, match="trust_unavailable"):
        OperatorCommandTrust(path, Clock()).current("installation-1")


@linux
def test_operator_command_trust_must_be_private(tmp_path):
    path = private(
        tmp_path / "command-trust.json",
        {"revision": 1, "trust": {"format_version": 1, "keys": []}},
    )
    path.chmod(0o644)
    # Unsafe storage is not a trust answer: the action worker stops on it.
    with pytest.raises(FleetError, match="unsafe_storage"):
        OperatorCommandTrust(path, Clock()).current("installation-1")


def operator_config(tmp_path, *, actions):
    document = write_routes()
    data = {
        "trust_file": str(tmp_path / "activity-trust.json"),
        "journal_directory": str(tmp_path / "journal"),
        "secrets": {
            "root": str(tmp_path / "secrets"),
            "mapping_file": str(tmp_path / "secrets.json"),
            "bindings": [],
        },
        "providers": [
            {
                "provider_id": "https-json",
                "dependency": {
                    "kind": "connector",
                    "name": "ap-standin",
                    "version": "0.1.0",
                    "sha256": "a" * 64,
                },
                "operations": ["get_invoice"],
                "connector": {
                    "name": "ap-standin",
                    "version": "0.1.0",
                    "artifact_sha256": routes_digest(document),
                },
                "settings": {"routes": document, "ca_file": str(tmp_path / "ca.pem")},
            }
        ],
    }
    if actions:
        data["actions"] = {
            "trust_file": str(tmp_path / "command-trust.json"),
            "providers": [
                {
                    "provider_id": "https-json",
                    "connector": {
                        "name": "ap-standin",
                        "version": "0.1.0",
                        "artifact_sha256": routes_digest(document),
                    },
                    "operations": {"set_payment_hold": "target_key"},
                    "settings": {"routes": document, "ca_file": str(tmp_path / "ca.pem")},
                }
            ],
        }
    return json.dumps(data).encode()


def test_an_operator_configuration_may_add_write_providers(tmp_path):
    from lotuspond_runtime.provider_config import worker_config

    setup = worker_config(operator_config(tmp_path, actions=True))
    assert setup.test_mode is False
    trust_file, [descriptor] = setup.actions
    assert trust_file == tmp_path / "command-trust.json"
    assert descriptor.provider_id == "https-json"
    assert worker_config(operator_config(tmp_path, actions=False)).actions is None


def test_operator_write_providers_are_checked_before_the_worker_starts(tmp_path):
    from lotuspond_runtime.provider_config import worker_config

    raw = json.loads(operator_config(tmp_path, actions=True))
    raw["actions"]["providers"][0]["provider_id"] = "synthetic-target-write"
    with pytest.raises(ValueError):
        worker_config(json.dumps(raw).encode())
    raw = json.loads(operator_config(tmp_path, actions=True))
    raw["actions"]["trust_file"] = "relative/command-trust.json"
    with pytest.raises(ValueError):
        worker_config(json.dumps(raw).encode())
