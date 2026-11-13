"""E05 Task 7: write provider selection and the supervised write child."""

import time

import pytest
from action_env import PIN, FakeActionPlane, calls, descriptor, identity
from fixtures.synthetic_target import SyntheticTarget

from lotuspond_runtime.action_invocation import (
    WriteDescriptor,
    WriteProviderSet,
    WriteRefused,
    _provider,
    invoke_write,
)
from lotuspond_runtime.activity_invocation import call_provider
from lotuspond_runtime.protocol import ConnectorPin


class Never:
    def is_set(self):
        return False


@pytest.fixture
def target(tmp_path):
    with SyntheticTarget(tmp_path / "target", tmp_path / "tls") as running:
        yield running


def _invoke(target, tmp_path, action, desc, *, test_mode=True, key=None):
    return invoke_write(
        desc,
        action.records,
        key,
        [],
        test_mode=test_mode,
        cancel=Never(),
        deadline_ns=time.monotonic_ns() + 20 * 10**9,
        deadline_ms=0,
        counter_id=action.command.command_id,
    )


def test_fixture_unselectable_in_production(tmp_path, target):
    desc = descriptor(target, tmp_path / "provider-calls")
    with pytest.raises(ValueError):
        WriteProviderSet((desc,), test_mode=False)
    # Even handed to the child, production settings make it unavailable.
    assert _provider(desc, (), test_mode=False) is None
    action = FakeActionPlane(identity(), port=target.port).action()
    outcome = _invoke(target, tmp_path, action, desc, test_mode=False)
    assert (outcome.reply, outcome.code, outcome.reached) == (None, "unavailable", False)
    assert target.requests() == [] and calls(tmp_path / "provider-calls") == 0
    # Positive control: the same descriptor in test mode reaches the target.
    assert _invoke(target, tmp_path, action, desc).reply["status"] == "accepted"
    assert len(target.requests()) == 1


def test_selection_is_by_exact_pin_and_write_operation(tmp_path, target):
    desc = descriptor(target, tmp_path / "provider-calls")
    providers = WriteProviderSet((desc,), test_mode=True)
    assert providers.select(PIN, "submit") is desc
    changed = ConnectorPin(name=PIN.name, version=PIN.version, artifact_sha256="f" * 64)
    for connector, operation in ((changed, "submit"), (PIN, "lookup"), (PIN, "delete")):
        with pytest.raises(WriteRefused):
            providers.select(connector, operation)
    with pytest.raises(ValueError):
        WriteProviderSet((desc, desc), test_mode=True)


def test_descriptor_is_data_not_code():
    with pytest.raises(ValueError):
        WriteDescriptor("synthetic-target-write", PIN, {"submit": "always"}, test_only=True)
    with pytest.raises(ValueError):
        WriteDescriptor("real", PIN, {"submit": "none"}, script={"x": 1})
    with pytest.raises(ValueError):
        WriteDescriptor("real", PIN, {})
    desc = WriteDescriptor("synthetic-target-write", PIN, {"submit": "target_key"}, test_only=True)
    assert WriteDescriptor.from_json(desc.to_json()) == desc
    assert desc.idempotency("submit") == "target_key" and desc.idempotency("lookup") is None


def test_only_fixed_child_modules_start():
    with pytest.raises(ValueError):
        call_provider({}, deadline_ns=0, cancel=Never(), module="os")


def test_a_write_past_its_deadline_is_stopped_and_cleaned_up(tmp_path, target):
    target.fault(delay_ms=3000)
    action = FakeActionPlane(identity(), port=target.port).action()
    desc = descriptor(target, tmp_path / "provider-calls")
    started = time.monotonic()
    outcome = invoke_write(
        desc,
        action.records,
        None,
        [],
        test_mode=True,
        cancel=Never(),
        deadline_ns=time.monotonic_ns() + 1 * 10**9,
        deadline_ms=0,
    )
    assert outcome.reply is None and outcome.started and outcome.cleanup.confirmed
    assert time.monotonic() - started < 5


def test_action_configuration_is_closed_data(tmp_path):
    from lotuspond_runtime.action_fixture import worker_config

    good = {
        "trust_file": str(tmp_path / "command-trust.json"),
        "providers": [
            {
                "provider_id": "synthetic-target-write",
                "connector": PIN.model_dump(),
                "operations": {"submit": "target_key"},
                "script": {"ca_file": "/ca.pem", "loopback": True},
            }
        ],
    }
    trust_file, descriptors = worker_config(good)
    assert trust_file.name == "command-trust.json"
    assert [d.provider_id for d in descriptors] == ["synthetic-target-write"]
    assert all(d.test_only for d in descriptors)
    provider = good["providers"][0]
    for bad in (
        {**good, "extra": 1},
        {**good, "trust_file": "relative.json"},
        {**good, "providers": []},
        {**good, "providers": [{**provider, "provider_id": "https-json"}]},
        {**good, "providers": [{**provider, "operations": {"submit": "maybe"}}]},
        {**good, "providers": [{**provider, "code": "import os"}]},
    ):
        with pytest.raises((ValueError, TypeError, KeyError)):
            worker_config(bad)


def test_the_cli_accepts_actions_only_in_test_mode(tmp_path, capsys):
    import json

    from test_cli import activity_config, cli_module, config_file

    module = cli_module()
    actions = {
        "trust_file": str(tmp_path / "command-trust.json"),
        "providers": [{"provider_id": "unknown-writer", "connector": PIN.model_dump()}],
    }
    invalid = activity_config(tmp_path, actions=actions)
    config = str(config_file(tmp_path, mode="test"))
    assert (
        module.main(["activity-worker", "--config", config, "--activity-config", str(invalid)]) == 2
    )
    assert capsys.readouterr().err.strip() == "invalid_input"
    production = str(config_file(tmp_path, mode="production"))
    valid = {
        **actions,
        "providers": [
            {
                **actions["providers"][0],
                "provider_id": "synthetic-target-write",
                "operations": {"submit": "none"},
            }
        ],
    }
    path = activity_config(tmp_path, actions=valid)
    assert (
        module.main(["activity-worker", "--config", production, "--activity-config", str(path)])
        == 2
    )
    assert capsys.readouterr().err.strip() == "invalid_input"
    assert json.loads(path.read_text())["actions"] == valid
