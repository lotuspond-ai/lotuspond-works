"""Task 10: bounded agents; model output is data, tools are declared, budgets shared."""

import hashlib
import time

import pytest
from activity_env import build
from fixtures.activity_provider import CONNECTOR, agent_records, calls

from lotuspond_runtime.activity_worker import (
    ActivityWorker,
    LeaseKeeper,
    checked_lease,
    verify_delivery,
)
from lotuspond_runtime.agent import AgentRequest, run_agent
from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import SecretPin

TOOL = "connector-fixture-read"


def proposal(name=TOOL, arguments=None):
    return {"name": name, "arguments": arguments or {"target": None, "input": None}}


def children(env):
    return [a for a in env.server.attempts.values() if a.parent is not None]


def root_result(env):
    return next(r for r in env.server.results if env.server.attempts[r.attempt_id].parent is None)


def agent_request(env):
    attempt = env.server.root("model")
    delivery = env.server.poll()
    envelope, records = verify_delivery(delivery, env.deps)
    lease = checked_lease(env.server.claim(delivery), envelope, env.deps)
    request = AgentRequest.from_records(env.deps, envelope, records, None)
    return attempt, request, LeaseKeeper(env.deps, lease)


@pytest.mark.parametrize(
    "bad",
    [
        proposal("connector-fixture-write"),  # not declared
        proposal(arguments={"target": 1, "input": None}),  # not the declared schema
        proposal(arguments={"target": None, "input": None, "url": "https://x"}),
        proposal(name={"name": TOOL}),
    ],
)
def test_proposed_tool_call_is_data_not_authority(tmp_path, bad):
    env = build(
        tmp_path, model_script={"rounds": [{"tool_calls": [proposal(), bad]}, {"output": None}]}
    )
    _, request, keeper = agent_request(env)
    with pytest.raises(FleetError) as refused:
        run_agent(request, env.deps, keeper)
    undeclared_tool_error = refused.value
    assert undeclared_tool_error.code == "forbidden"
    # Only the model round ran: no read provider was invoked for either proposal,
    # not even the valid one proposed beside it.
    assert calls(env.counter) == 1
    assert [c.kind for c in children(env)] == ["model"]


def test_a_refused_proposal_ends_the_activity_as_invalid_output(tmp_path):
    env = build(tmp_path, model_script={"rounds": [{"tool_calls": [proposal("undeclared")]}]})
    env.server.root("model")
    assert ActivityWorker(env.deps).run_once() == "invalid_output"
    (model_round,) = children(env)
    assert model_round.result.code == "succeeded"  # the round itself completed


def test_declared_tool_runs_as_its_own_child_and_the_agent_completes(tmp_path):
    # Every control call takes 20 ms, one at a time, like the real transport.
    env = build(
        tmp_path,
        model_script={"rounds": [{"tool_calls": [proposal()]}, {"output": None}]},
        read_script={"read": {"value": None}},
        latency_s=0.02,
    )
    env.server.root("model")
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert env.server.overlaps == 0  # no call ever overlapped another
    kids = children(env)
    assert [c.kind for c in kids] == ["model", "connector_read", "model"]
    assert all(c.result.code == "succeeded" for c in kids)
    assert calls(env.counter) == 3  # two model rounds and one read, observed
    # Each child had its own signed delivery and claim; the root's own usage is zero.
    assert len(set(env.server.claims)) == 4
    assert root_result(env).usage.cost_microusd == 0


def test_nested_calls_share_budget(tmp_path):
    # The model keeps asking for tools; the shared root budget admits 3 children.
    env = build(
        tmp_path,
        model_script={"rounds": [{"tool_calls": [proposal()]}]},
        budget_children=3,
    )
    env.server.root("model")
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    assert len(children(env)) == 3  # never more than the shared budget admitted
    assert calls(env.counter) == 3
    # Every admitted child reported its own usage against the same root.
    assert sum(1 for r in env.server.results if env.server.attempts[r.attempt_id].parent) == 3


def test_model_output_must_match_the_pinned_schema(tmp_path):
    env = build(tmp_path, model_script={"rounds": [{"output": {"forged": True}}]})
    env.server.root("model")
    assert ActivityWorker(env.deps).run_once() == "invalid_output"
    result = root_result(env)
    assert result.result is None and result.code == "invalid_output"


@pytest.mark.parametrize(
    ("kind", "changes", "invoked"),
    [
        # A model round signed as a connector read.
        (
            "model",
            {"kind": "connector_read", "dependency": CONNECTOR.model_dump(), "operation": "read"},
            0,
        ),
        # A model round signed with other arguments than the stored ones.
        (
            "model",
            {
                "input": {
                    "id": "00000000-0000-4000-8000-0000000000ab",
                    "version": 1,
                    "sha256": "0" * 64,
                }
            },
            0,
        ),
        # A tool read signed for another operation of the same connector.
        ("connector_read", {"operation": "list"}, 1),
    ],
)
def test_a_child_bound_to_anything_else_is_never_claimed(tmp_path, kind, changes, invoked):
    env = build(tmp_path, model_script={"rounds": [{"tool_calls": [proposal()]}, {"output": None}]})
    env.server.root("model")
    env.server.child_override = (kind, changes)
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    (misbound,) = [c for c in children(env) if c.kind == kind and c.result is None]
    assert misbound.id not in env.server.claims  # refused before any claim
    # It is what the control plane dispatched, so it is declined at once.
    assert misbound.status == "declined" and env.server.declines == [misbound.id]
    assert calls(env.counter) == invoked


def test_a_tool_read_resolves_its_connections_secrets(tmp_path):
    secret = b"PRIVATE-TOOL-SECRET-5d1e"
    pin = SecretPin(reference_id="00000000-0000-4000-8000-0000000000aa", version=1)
    seen = []

    class Secrets:
        def resolve(self, requested):
            seen.append(requested)
            return secret

    script = {"rounds": [{"tool_calls": [proposal()]}, {"output": None}]}
    # The read fixture fails unless it received exactly this secret.
    read = {"read": {"value": None, "require_secrets": [hashlib.sha256(secret).hexdigest()]}}
    env = build(tmp_path, model_script=script, read_script=read, secrets=Secrets())
    env.server.secrets = (pin,)
    env.server.root("model")
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert seen == [pin] and calls(env.counter) == 3
    assert secret not in b"".join(env.server.values.values())
    for path in env.journal_dir.iterdir():
        if path.name.startswith("attempt-"):
            assert secret not in path.read_bytes()
    # Without a resolver the tool is unavailable and its provider never runs.
    (tmp_path / "plain").mkdir()
    plain = build(tmp_path / "plain", model_script=script, read_script=read)
    plain.server.secrets = (pin,)
    plain.server.root("model")
    assert ActivityWorker(plain.deps).run_once() == "unavailable"
    assert calls(plain.counter) == 1  # the model round only


def test_losing_the_parents_lease_cancels_its_running_child(tmp_path):
    env = build(tmp_path, model_script={"rounds": [{"sleep_s": 30, "output": None}]})
    root = env.server.root("model")
    env.server.lose_renewal_ids = {root.id}  # only the root's lease is lost
    started = time.monotonic()
    assert ActivityWorker(env.deps).run_once() == "lost"
    assert time.monotonic() - started < 6
    (model_round,) = children(env)
    assert calls(env.counter) == 1
    assert env.journal.get(root.id)["phase"] == "abandoned"
    assert env.journal.get(model_round.id)["phase"] == "abandoned"
    assert env.server.results == []  # both lapse to reconciliation; nothing invented


def test_a_stopping_root_stops_its_running_child_and_both_report(tmp_path):
    # A pause or a cancellation refuses the root's renewal while a round runs.
    env = build(tmp_path, model_script={"rounds": [{"sleep_s": 30, "output": None}]})
    root = env.server.root("model")
    env.server.refuse_renewal_ids = {root.id}
    started = time.monotonic()
    assert ActivityWorker(env.deps).run_once() == "cancelled"
    assert time.monotonic() - started < 6
    (model_round,) = children(env)
    assert calls(env.counter) == 1
    # The round stopped and reported what it knows (unknown cost); the root follows.
    assert model_round.result.code == "cancelled"
    assert model_round.result.usage.cost_microusd is None
    assert root_result(env).code == "cancelled"
    assert root_result(env).usage.cost_microusd == 0  # the root itself called nothing
    assert env.journal.get(root.id)["phase"] == "recorded"


def test_a_child_refused_by_a_stop_ends_the_agent_as_cancelled(tmp_path):
    env = build(tmp_path, model_script={"rounds": [{"output": None}]})
    root = env.server.root("model")
    # Paused before the first round: the child is refused, and so is the renewal.
    env.server.refuse_children_ids = env.server.refuse_renewal_ids = {root.id}
    assert ActivityWorker(env.deps).run_once() == "cancelled"
    assert children(env) == [] and calls(env.counter) == 0
    assert root_result(env).code == "cancelled"
    assert root_result(env).usage.cost_microusd == 0


def test_a_child_refused_for_another_reason_still_fails_the_agent(tmp_path):
    env = build(tmp_path, model_script={"rounds": [{"output": None}]})
    root = env.server.root("model")
    env.server.refuse_children_ids = {root.id}  # refused, but the root may go on
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    assert root_result(env).code == "provider_failed"


def test_an_unrelated_pending_delivery_is_never_handed_to_an_agent(tmp_path):
    env = build(tmp_path, model_script={"rounds": [{"tool_calls": [proposal()]}, {"output": None}]})
    agent = env.server.root("model")
    unrelated = env.server.root("connector_read")  # pending while the agent runs
    worker = ActivityWorker(env.deps)
    assert worker.run_once() == "succeeded"  # the agent's children came by targeted poll
    assert unrelated.status == "pending"
    assert worker.run_once() == "succeeded"
    reported = {r.attempt_id for r in env.server.results}
    assert {agent.id, unrelated.id} <= reported


def test_a_model_round_beyond_its_limits_ends_the_agent(tmp_path):
    env = build(
        tmp_path,
        model_script={"rounds": [{"tool_calls": [proposal()], "usage": {"input_tokens": 10}}]},
    )
    env.server.root("model", agent=agent_records(max_input_tokens=5))
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    (model_round,) = children(env)
    # The round reports what it really cost; no tool runs beyond the limit.
    assert model_round.result.usage.input_tokens == 10
    assert calls(env.counter) == 1


def test_a_model_round_longer_than_thirty_seconds_keeps_its_outcome(tmp_path):
    env = build(tmp_path, model_script={"rounds": [{"sleep_s": 31, "output": None}]})
    env.server.root("model")
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    (model_round,) = children(env)
    assert model_round.result.code == "succeeded"
    assert model_round.result.usage.cost_microusd == 1


def test_a_childs_lease_out_of_bounds_is_reported_and_the_root_can_report(tmp_path):
    env = build(tmp_path, child_lease_ms=120000)  # a child lease Task 9 forbids
    env.server.root("model")
    assert ActivityWorker(env.deps).run_once() == "unavailable"
    (model_round,) = children(env)
    # Nothing ran under it; it is reported with zero usage, so the root reports too.
    assert model_round.result.code == "unavailable"
    assert model_round.result.usage.cost_microusd == 0
    assert root_result(env).code == "unavailable"
    assert calls(env.counter) == 0


def test_tool_reads_draw_on_the_agents_cost_limit(tmp_path):
    # Each round and each read costs 1; a limit of 2 leaves nothing for a 2nd round.
    env = build(
        tmp_path,
        model_script={"rounds": [{"tool_calls": [proposal()]}, {"output": None}]},
    )
    env.server.root("model", agent=agent_records(max_cost_microusd=2))
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    assert [c.kind for c in children(env)] == ["model", "connector_read"]
    assert calls(env.counter) == 2


def test_context_reads_keep_the_lease_live(tmp_path):
    from lotuspond_runtime.context import ContextRef

    class Slow:
        def read(self, attempt_id, ref):
            time.sleep(0.7)
            return {"citation": ref.version}

    refs = tuple(
        ContextRef(source_id=f"00000000-0000-4000-8000-00000000004{i}", version=i) for i in range(3)
    )
    # 2.1 s of context reads under a 2 s lease: renewed between reads, never lost.
    env = build(tmp_path, context=Slow(), lease_ms=2000)
    env.server.root("model", agent=agent_records(context=refs))
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert env.server.renewals  # the root's lease was renewed during the reads
    many = tuple(
        ContextRef(source_id=f"00000000-0000-4000-8000-{i:012d}", version=1) for i in range(65)
    )
    with pytest.raises(ValueError):
        agent_records(context=many)  # at most 64 context references


def test_a_child_claim_lost_in_transit_is_left_to_reconciliation(tmp_path):
    env = build(tmp_path)
    root = env.server.root("model")
    env.server.faults["child_claim_ack"] = ["transport_failed"] * 3
    assert ActivityWorker(env.deps).run_once() == "lost"
    (model_round,) = children(env)
    # The claim may have committed: nothing ran and nothing was invented.
    assert calls(env.counter) == 0 and env.server.results == []
    assert env.journal.get(model_round.id)["phase"] == "abandoned"
    assert env.journal.get(root.id)["phase"] == "abandoned"


def test_an_agent_whose_output_cannot_be_stored_is_never_retryable(tmp_path):
    env = build(tmp_path)
    env.server.root("model")
    # The round's argument and result store; then the root's own output fails.
    env.server.faults["store"] = [None, None] + ["transport_failed"] * 3
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    assert calls(env.counter) == 1  # a new ordinal would run the model again
