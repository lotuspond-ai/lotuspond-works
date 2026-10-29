"""Task 10: provider selection, read-only adapters, connection rules, sanitized failures."""

import hashlib
import logging
import time

import pytest
from activity_env import build
from fixtures.activity_provider import CONNECTOR, INSTALLED, MODEL, calls

from lotuspond_runtime.activity_worker import ActivityWorker
from lotuspond_runtime.protocol import ConnectorPin, DataHandling, SecretPin
from lotuspond_runtime.providers import ActivityFailure, ProviderDescriptor, ProviderSet

SENTINEL = "PRIVATE-PROVIDER-SENTINEL-7f3a"
PIN = SecretPin(reference_id="00000000-0000-4000-8000-0000000000aa", version=1)
SECOND = SecretPin(reference_id="00000000-0000-4000-8000-0000000000bb", version=2)


def test_read_adapter_cannot_write(tmp_path):
    env = build(tmp_path)
    # The delivery asks for the connector's write operation: no read adapter has it.
    env.server.root("connector_read", operation="write")
    assert ActivityWorker(env.deps).run_once() == "unavailable"
    assert calls(env.counter) == 0  # the provider was never invoked
    (result,) = env.server.results
    assert result.usage.cost_microusd == 0  # nothing ran, so zero is known
    with pytest.raises(ValueError):
        ProviderDescriptor("x", CONNECTOR, "model")  # effect and dependency disagree
    with pytest.raises(ActivityFailure, match="unavailable"):
        env.deps.providers.select(CONNECTOR, "read", "write")


def test_private_provider_exception_is_sanitized(tmp_path, caplog):
    env = build(tmp_path, read_script={"read": {"raise": SENTINEL}})
    env.server.root("connector_read")
    with caplog.at_level(logging.DEBUG):
        assert ActivityWorker(env.deps).run_once() == "provider_failed"
    (result,) = env.server.results
    assert SENTINEL not in result.model_dump_json()
    assert SENTINEL not in caplog.text
    for path in env.journal_dir.iterdir():
        if path.name.startswith("attempt-"):
            assert SENTINEL not in path.read_text()
    assert calls(env.counter) == 1


def test_each_synthetic_call_is_counted_against_its_attempt(tmp_path):
    """The counter names the attempt it served, so calls are observed per attempt."""
    env = build(tmp_path)
    attempt = env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert env.counter.read_text().splitlines() == [attempt.id]


def test_unconfigured_real_provider_reports_unavailable_and_fixtures_need_test_mode(tmp_path):
    env = build(tmp_path)
    worker = ActivityWorker(env.deps)
    object.__setattr__(env.deps, "providers", ProviderSet((), test_mode=False))
    env.server.root("connector_read")
    assert worker.run_once() == "unavailable"
    assert calls(env.counter) == 0
    synthetic = ProviderDescriptor("fixture-model", MODEL, "model", test_only=True)
    with pytest.raises(ValueError):
        ProviderSet((synthetic,), test_mode=False)  # never selectable in production
    with pytest.raises(ValueError):
        ProviderDescriptor("real", MODEL, "model", script={"rounds": []})
    with pytest.raises(AttributeError):
        env.deps.providers.test_mode = True  # fixed at construction
    # A script is immutable data: the caller's dictionary cannot change it later.
    script = {"rounds": [{"output": None}]}
    descriptor = ProviderDescriptor("fixture-model", MODEL, "model", test_only=True, script=script)
    script["rounds"].append({"raise": "late"})
    assert descriptor.script_data() == {"rounds": [{"output": None}]}


def test_a_changed_pin_never_selects_a_provider(tmp_path):
    env = build(tmp_path)
    changed = CONNECTOR.model_copy(update={"sha256": "f" * 64})
    with pytest.raises(ActivityFailure, match="unavailable"):
        env.deps.providers.select(changed, "read", "read")
    renamed = CONNECTOR.model_copy(update={"version": "2.0.0"})
    with pytest.raises(ActivityFailure, match="unavailable"):
        env.deps.providers.select(renamed, "read", "read")
    with pytest.raises(ValueError):  # an installed artifact for another connector
        ProviderDescriptor(
            "fixture-read",
            CONNECTOR,
            "read",
            frozenset({"read"}),
            connector=INSTALLED.model_copy(update={"name": "other-connector"}),
        )


def test_secret_is_resolved_just_before_dispatch_and_never_recorded(tmp_path):
    secrets = {PIN: b"PRIVATE-CONNECTION-SECRET-91c2", SECOND: b"PRIVATE-SECOND-SECRET-44aa"}
    seen = []

    class Secrets:
        def resolve(self, pin):
            # Only after the journal made the start durable, before any provider call.
            seen.append((pin, env.journal.get(attempt.id)["phase"], calls(env.counter)))
            return secrets[pin]

    digests = [hashlib.sha256(value).hexdigest() for value in secrets.values()]
    env = build(
        tmp_path,
        secrets=Secrets(),
        read_script={"read": {"value": None, "require_secrets": digests}},
    )
    env.server.secrets = (PIN, SECOND)
    attempt = env.server.root("connector_read")
    # The provider fails unless it received every pinned secret, in order.
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert seen == [(PIN, "started", 0), (SECOND, "started", 0)] and calls(env.counter) == 1
    stored = b"".join(env.server.values.values())
    reported = "".join(r.model_dump_json() for r in env.server.results)
    for secret in secrets.values():
        assert secret not in stored and secret.decode() not in reported
        for path in env.journal_dir.iterdir():
            if path.name.startswith("attempt-"):
                assert secret not in path.read_bytes()


def test_a_connection_secret_without_a_resolver_denies_before_any_call(tmp_path):
    env = build(tmp_path)
    env.server.secrets = (PIN,)
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "unavailable"
    assert calls(env.counter) == 0


def test_a_connection_for_another_connector_never_receives_its_secret(tmp_path):
    seen = []

    class Secrets:
        def resolve(self, pin):
            seen.append(pin)
            return b"secret"

    other = ConnectorPin(name="other-connector", version="1.0.0", artifact_sha256="f" * 64)
    env = build(tmp_path, secrets=Secrets(), connection_changes={"connector": other})
    env.server.secrets = (PIN,)
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "unavailable"
    assert seen == [] and calls(env.counter) == 0
    # The same name with another installed artifact is not this provider's either.
    rebuilt = INSTALLED.model_copy(update={"artifact_sha256": "e" * 64})
    (tmp_path / "rebuilt").mkdir()
    env = build(tmp_path / "rebuilt", secrets=Secrets(), connection_changes={"connector": rebuilt})
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "unavailable"
    assert seen == [] and calls(env.counter) == 0


def test_the_connections_request_and_response_bounds_apply(tmp_path):
    # `{"input":null,"target":null}` is 27 bytes: over a 10-byte request bound.
    env = build(tmp_path, connection_changes={"data_handling": DataHandling(max_request_bytes=10)})
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    assert calls(env.counter) == 0 and env.server.results[-1].usage.cost_microusd == 0
    # A 100-byte response over a 50-byte bound is refused; what it cost is kept.
    (tmp_path / "response").mkdir()
    env = build(
        tmp_path / "response",
        read_script={"read": {"value_bytes": 100}},
        connection_changes={"data_handling": DataHandling(max_response_bytes=50)},
    )
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "invalid_output"
    assert calls(env.counter) == 1 and env.server.results[-1].usage.cost_microusd == 1


def test_the_connections_timeout_bounds_the_call(tmp_path):
    env = build(
        tmp_path,
        read_script={"read": {"sleep_s": 10, "value": None}},
        connection_changes={"data_handling": DataHandling(timeout_ms=1000)},
    )
    env.server.root("connector_read")
    started = time.monotonic()
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    assert time.monotonic() - started < 4  # 1 s timeout, then TERM and confirmation
    (result,) = env.server.results
    assert result.usage.cost_microusd is None  # it was reached: unknown, never zero


def test_a_model_providers_credentials_are_resolved_just_before_its_call(tmp_path):
    credential = b"PRIVATE-MODEL-KEY-0b7c"
    seen = []

    class Secrets:
        def resolve(self, pin):
            seen.append(pin)
            return credential

    digest = hashlib.sha256(credential).hexdigest()
    env = build(
        tmp_path,
        secrets=Secrets(),
        credentials=(PIN,),
        model_script={"rounds": [{"output": None, "require_secrets": [digest]}]},
    )
    env.server.root("model")
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert seen == [PIN]
    assert credential not in b"".join(env.server.values.values())


def test_mounted_secrets_resolve_pins_through_the_operator_mapping(tmp_path):
    import json

    from lotuspond_runtime.activity_worker import MountedActivitySecrets
    from lotuspond_runtime.errors import FleetError
    from lotuspond_runtime.resolver import MountedSecretResolver

    root = tmp_path / "secrets"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    (root / "value-v1").write_bytes(b"opaque-synthetic")
    (root / "value-v1").chmod(0o600)
    mapping = root / "mapping.json"
    mapping.write_text(
        json.dumps(
            {"bindings": [{"binding_id": "binding-a", "version": 1, "filename": "value-v1"}]}
        )
    )
    mapping.chmod(0o600)
    secrets = MountedActivitySecrets(
        MountedSecretResolver(root, mapping), {(PIN.reference_id, PIN.version): "binding-a"}
    )
    assert secrets.resolve(PIN) == b"opaque-synthetic"
    with pytest.raises(FleetError, match="secret_unavailable"):
        secrets.resolve(SECOND)  # not mapped by the operator
    with pytest.raises(ValueError):
        MountedActivitySecrets(object(), {})  # only E03's mounted resolver
