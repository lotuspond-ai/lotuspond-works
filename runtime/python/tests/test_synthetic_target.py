"""E05 Task 7: the synthetic target process and its providers, observed from outside."""

import json
import os
import signal

import pytest
import rfc8785
from action_env import LOOPBACK, PIN, FakeActionPlane, calls, descriptor, identity
from fixtures.synthetic_target import SyntheticTarget
from lotuspond_sdk.executable.package import ArtifactRef

from lotuspond_runtime.action_fixture import (
    SyntheticTargetReadProvider,
    SyntheticTargetWriteProvider,
)
from lotuspond_runtime.action_invocation import invoke_write
from lotuspond_runtime.activity_protocol import PinnedActivityRecords
from lotuspond_runtime.providers import ActivityFailure, ProviderDescriptor


@pytest.fixture
def target(tmp_path):
    with SyntheticTarget(tmp_path / "target", tmp_path / "tls") as running:
        yield running


def test_target_counts_are_observed(tmp_path, target):
    """Positive controls: the target counted, the fixture ran and its child is gone."""
    plane = FakeActionPlane(identity(), port=target.port)
    action = plane.action()
    counter = tmp_path / "provider-calls"
    outcome = invoke_write(
        descriptor(target, counter),
        action.records,
        action.command.action_id,
        [],
        test_mode=True,
        cancel=type("Never", (), {"is_set": lambda self: False})(),
        deadline_ns=__import__("time").monotonic_ns() + 20 * 10**9,
        deadline_ms=plane.now_ms() + 20000,
        counter_id=action.command.command_id,
    )
    assert outcome.reply["status"] == "accepted" and outcome.cleanup.confirmed
    assert calls(counter) == 1
    assert [w["idempotency_key"] for w in target.writes()] == [action.command.action_id]
    assert [r["route"] for r in target.requests()] == ["write"]


def _write(target, tmp_path, action, key):
    provider = SyntheticTargetWriteProvider(descriptor(target, tmp_path / "provider-calls"), ())
    return provider.invoke(action.records, key, 0)


def test_a_repeated_key_is_applied_once_and_reported_as_observed(tmp_path, target):
    plane = FakeActionPlane(identity(), port=target.port)
    action = plane.action()
    first = _write(target, tmp_path, action, action.command.action_id)
    second = _write(target, tmp_path, action, action.command.action_id)
    assert (first.status, second.status) == ("accepted", "observed_effect")
    assert first.response == second.response and len(target.writes()) == 1
    assert first.usage.cost_microusd == 0  # known zero, not unknown


def test_faults_lose_requests_responses_and_crash_after_apply(tmp_path, target):
    plane = FakeActionPlane(identity(), port=target.port)
    lost_request, lost_response, crashed = plane.action(), plane.action(), plane.action()
    target.fault(lose_request=1)
    assert _write(target, tmp_path, lost_request, "k-1").status == "unknown"
    assert target.writes() == []  # read and dropped: never applied
    target.fault(lose_response=1)
    assert _write(target, tmp_path, lost_response, "k-2").status == "unknown"
    assert len(target.writes()) == 1  # applied without an answer
    target.fault(crash_after_apply=1)
    assert _write(target, tmp_path, crashed, "k-3").status == "unknown"
    target.process.wait(5)
    assert not target.alive() and len(target.writes()) == 2
    # Records survive a restart on the same port; a repeat is not applied again.
    target.restart()
    assert _write(target, tmp_path, crashed, "k-3").status == "observed_effect"
    assert len(target.writes()) == 2


def test_a_closed_target_is_not_reached(tmp_path, target):
    plane = FakeActionPlane(identity(), port=target.port)
    action = plane.action()
    target.stop()
    with pytest.raises(ActivityFailure) as refused:
        _write(target, tmp_path, action, "k-1")
    assert refused.value.reached is False


def test_the_read_provider_reads_back_through_the_destination_transport(tmp_path, target):
    plane = FakeActionPlane(identity(), port=target.port, operations=("submit", "lookup"))
    action = plane.action()
    _write(target, tmp_path, action, action.command.action_id)
    read = ProviderDescriptor(
        "synthetic-target-read",
        ArtifactRef(kind="connector", name=PIN.name, version=PIN.version, sha256="c" * 64),
        "read",
        frozenset({"lookup"}),
        test_only=True,
        script={"ca_file": str(target.ca_file), "egress": LOOPBACK, "loopback": True},
        connector=PIN,
    )
    envelope = type("Envelope", (), {"operation": "lookup"})()
    records = PinnedActivityRecords(
        envelope, action.records.configuration, read.dependency, 0, {}, {}, "0" * 64
    )
    target_value = json.loads(action.records.actual_target)
    result = SyntheticTargetReadProvider(read, ()).invoke(
        records, {"target": target_value, "input": None}, 0
    )
    assert [r["idempotency_key"] for r in result.value["records"]] == [action.command.action_id]
    assert target.requests()[-1]["route"] == "read"
    # The canonical bytes the binding pins are the bytes the target received.
    assert rfc8785.dumps(target.writes()[0]["target"]) == action.records.actual_target


def test_the_target_is_a_separate_process(target):
    assert target.alive() and target.process.pid != os.getpid()
    os.kill(target.process.pid, signal.SIGKILL)
    target.process.wait(5)
    assert not target.alive()
