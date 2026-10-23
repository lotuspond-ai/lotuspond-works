"""Task 10: verification, journal, lease loss and restart of the activity worker.

The journal uses `state.private_directory`, which is Linux-only by design; like the
existing runtime storage tests, these run on Linux (CI or a Linux container).
"""

import hashlib
import json
import time

import pytest
from activity_env import anchored, build, reopen
from fixtures.activity_provider import calls
from lotuspond_sdk.activity import (
    ActivityDelivery,
    canonical_activity_json,
    sign_activity,
    validate_activity,
)

import lotuspond_runtime.activity_invocation as invocation
import lotuspond_runtime.activity_worker as worker
from lotuspond_runtime.activity_worker import (
    ActivityWorker,
    ServerClock,
    observed_usage,
    run_activity,
)
from lotuspond_runtime.errors import FleetError


def observe(monkeypatch):
    """Every provider outcome the worker received, in order."""
    outcomes = []
    original = worker.call_provider

    def observed(*args, **kwargs):
        outcome = original(*args, **kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(worker, "call_provider", observed)
    return outcomes


def test_read_activity_runs_once_and_reports_typed_output(tmp_path):
    env = build(tmp_path)
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    (result,) = env.server.results
    assert env.server.value(result.result) is None
    assert result.usage.cost_microusd == 1
    assert calls(env.counter) == 1
    entry = env.journal.get(result.attempt_id)
    assert entry["phase"] == "recorded" and entry["outcome"]["code"] == "succeeded"
    assert type(entry["group"]) is int  # the provider's group, recorded before it ran


def test_lost_lease_stops_child_and_retains_unknown_usage(tmp_path, monkeypatch):
    # The provider would run 30 s; the control plane refuses the first renewal.
    env = build(tmp_path, read_script={"read": {"sleep_s": 30, "value": None}})
    env.server.refuse_renewal = True
    attempt = env.server.root("connector_read")
    outcomes = observe(monkeypatch)
    started = time.monotonic()
    assert ActivityWorker(env.deps).run_once() == "lost"
    assert time.monotonic() - started < 5  # cancelled at the first refused renewal
    (outcome,) = outcomes
    cleanup = outcome.cleanup
    assert cleanup.confirmed is True  # TERM/KILL and confirmed exit of the child group
    assert outcome.code == "cancelled" and outcome.started
    # The provider was reached and its response lost: what it cost is unknown.
    lost_response_usage = observed_usage(outcome, env.deps)
    assert lost_response_usage.cost_microusd is None
    assert calls(env.counter) == 1
    assert env.server.results == []  # no outcome is invented: the server lapses it
    assert env.journal.get(attempt.id)["phase"] == "abandoned"
    assert not invocation.fenced()


def test_a_provider_that_ran_without_reporting_usage_keeps_it_unknown(tmp_path):
    # The provider was reached, then failed without any usage report.
    env = build(tmp_path, read_script={"read": {"failure": "unavailable"}})
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "unavailable"
    assert calls(env.counter) == 1
    (result,) = env.server.results
    # The provider may have done work: unknown, never measured zero.
    assert result.usage.cost_microusd is None
    assert result.usage.input_tokens is None


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        # A failure with a usage report keeps it: the declared retry stays possible.
        ({"failure": "rate_limited", "usage": {"cost_microusd": 7}}, 7),
        # A failure that positively sent nothing costs zero.
        ({"failure": "unavailable", "not_reached": True}, 0),
    ],
)
def test_a_failing_provider_reports_what_it_knows(tmp_path, step, expected):
    env = build(tmp_path, read_script={"read": step})
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == step["failure"]
    (result,) = env.server.results
    assert result.usage.cost_microusd == expected


def test_restart_after_started_journal_never_reinvokes(tmp_path, monkeypatch):
    env = build(tmp_path)
    attempt = env.server.root("connector_read")
    original = worker.call_provider

    def crash_after_start(*args, **kwargs):
        original(*args, **kwargs)  # the provider was reached
        raise KeyboardInterrupt  # the process dies before reporting

    monkeypatch.setattr(worker, "call_provider", crash_after_start)
    with pytest.raises(KeyboardInterrupt):
        ActivityWorker(env.deps).run_once()
    monkeypatch.setattr(worker, "call_provider", original)
    provider_calls_before_restart = calls(env.counter)
    assert provider_calls_before_restart == 1
    # A restarted worker reopens the journal from disk: the attempt is ambiguous,
    # abandoned, and its delivery is refused if it is ever offered again.
    restarted = reopen(env)
    assert ActivityWorker(restarted.deps).recover() == [attempt.id]
    env.server.offers.append(attempt.delivery)
    assert ActivityWorker(restarted.deps).run_once() == "refused"
    with pytest.raises(FleetError, match="conflict"):
        run_activity(attempt.delivery, attempt.lease, restarted.deps)
    provider_calls_after_restart = calls(env.counter)
    assert provider_calls_after_restart == provider_calls_before_restart
    assert restarted.journal.get(attempt.id)["phase"] == "abandoned"
    assert env.server.results == []


def signed(env, envelope):
    canonical = canonical_activity_json(validate_activity(envelope))
    signature = sign_activity(validate_activity(envelope), env.server.key)
    return ActivityDelivery(
        envelope=canonical.decode(),
        signature=json.dumps(signature.model_dump(), sort_keys=True, separators=(",", ":")),
    )


def test_tampered_misaddressed_or_untrusted_deliveries_are_refused(tmp_path):
    env = build(tmp_path)
    attempt = env.server.root("connector_read")
    delivery = attempt.delivery
    envelope = json.loads(delivery.envelope)
    env.server.withdraw(attempt)  # only the altered versions are offered
    worker_ = ActivityWorker(env.deps)
    for changed in (
        # Altered after signing.
        ActivityDelivery(
            envelope=json.dumps(
                dict(envelope, operation="write"), separators=(",", ":"), sort_keys=True
            ),
            signature=delivery.signature,
        ),
        # Not the exact canonical bytes that were signed.
        ActivityDelivery(envelope=json.dumps(envelope, indent=1), signature=delivery.signature),
        # Validly signed, but addressed to another runtime instance or generation.
        signed(env, dict(envelope, instance_id="00000000-0000-4000-8000-0000000000ee")),
        signed(env, dict(envelope, generation=2)),
    ):
        env.server.offers.append(changed)
        with pytest.raises(FleetError, match="command_rejected"):
            worker_.run_once()
    # Signed by a key the runtime's current activity trust does not hold.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from lotuspond_sdk.trust import TrustSet

    other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    env.server.current = lambda scope, now: TrustSet.model_validate(
        {
            "format_version": 1,
            "keys": [
                {
                    "key_id": hashlib.sha256(other).hexdigest(),
                    "public_key": other.hex(),
                    "state": "allowed",
                }
            ],
        }
    )
    env.server.offers.append(delivery)
    # An unknown signer is a gap in this runtime's own trust, not a bad delivery.
    with pytest.raises(FleetError, match="trust_unavailable"):
        worker_.run_once()
    assert calls(env.counter) == 0 and env.server.claims == []


def test_a_lease_beyond_sixty_seconds_or_past_its_deadline_is_refused(tmp_path):
    # A control plane (or anything in between) offering a 120 s lease is not trusted
    # to run anything; the attempt is reported as not served, with nothing spent.
    env = build(tmp_path, lease_ms=120000)
    attempt = env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "unavailable"
    assert calls(env.counter) == 0  # refused before any provider was reached
    (result,) = env.server.results
    assert result.usage.cost_microusd == 0
    assert env.journal.get(attempt.id)["phase"] == "recorded"


def test_a_lost_claim_acknowledgement_reclaims_the_same_lease(tmp_path):
    env = build(tmp_path)
    attempt = env.server.root("connector_read")
    # The claim commits, but its answer is lost three times: the worker gives up.
    env.server.faults["claim_ack"] = ["transport_failed"] * 3
    with pytest.raises(FleetError, match="transport_failed"):
        ActivityWorker(env.deps).run_once()
    assert env.journal.get(attempt.id)["phase"] == "claimed" and calls(env.counter) == 0
    # Offered again, the same delivery is claimed again: the equal claim returns
    # the same live lease, and the provider runs exactly once.
    env.server.offers.append(attempt.delivery)
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert calls(env.counter) == 1
    assert env.server.claims == [attempt.id] * 4


def test_a_transient_claim_or_renewal_failure_is_retried_not_lost(tmp_path):
    env = build(tmp_path, read_script={"read": {"sleep_s": 2.5, "value": None}})
    env.server.root("connector_read")
    env.server.faults["claim_ack"] = ["transport_failed"]
    env.server.faults["renew"] = ["transport_failed"]
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    assert calls(env.counter) == 1
    assert env.server.calls.count("renew") >= 2  # the failed renewal was retried


def test_a_transient_failure_too_close_to_expiry_ends_the_lease(tmp_path):
    # 12 s of lease is less than one more blocked request (10 s) plus the 2 s stop:
    # a transient renewal failure is not retried, the provider is stopped in time.
    env = build(tmp_path, lease_ms=12000, read_script={"read": {"sleep_s": 10, "value": None}})
    env.server.root("connector_read")
    env.server.faults["renew"] = ["transport_failed"]
    started = time.monotonic()
    assert ActivityWorker(env.deps).run_once() == "lost"
    assert time.monotonic() - started < 5
    assert env.server.results == []


@pytest.mark.parametrize("skew_ms", [-300000, 300000])
def test_the_control_planes_clock_decides_not_the_runtimes(tmp_path, skew_ms):
    # The control plane's clock is five minutes away from the runtime's wall clock.
    env = build(tmp_path, skew_ms=skew_ms)
    env.server.root("connector_read")
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    (result,) = env.server.results
    assert result.usage.completed_at_ms <= env.server.now_ms()  # never in its future


def test_a_runtime_on_its_own_wall_clock_would_refuse_a_valid_delivery(tmp_path):
    # Positive control for the anchored clock: the control plane is 5 s ahead.
    env = build(tmp_path, skew_ms=5000)
    env.server.root("connector_read")

    class Local:
        def now_ms(self):
            return time.time_ns() // 1_000_000

        settled_ms = now_ms

    import dataclasses

    local = dataclasses.replace(env.deps, clock=Local())
    with pytest.raises(FleetError, match="command_rejected"):
        ActivityWorker(local).run_once()  # issued in the runtime's future
    # A local clock fault is never a reason to decline: the anchored clock serves it.
    assert env.server.declines == []
    assert ActivityWorker(env.deps).run_once() == "succeeded"


def test_an_unanchored_or_jumped_clock_makes_time_unavailable():
    clock = ServerClock()
    with pytest.raises(FleetError, match="authority_unavailable"):
        clock.now_ms()
    wall = [1_000_000]
    mono = [10.0]
    clock = ServerClock(monotonic=lambda: mono[0], wall_ms=lambda: wall[0])
    clock.anchor(5_000_000, sent=9.9, received=10.0)
    # The roundtrip and a 50 ms drift allowance widen the bounds on either side.
    assert clock.now_ms() == 5_000_150 and clock.settled_ms() == 4_999_950
    mono[0], wall[0] = 11.0, 1_001_000
    # One second later the allowance has grown by 1 ms.
    assert clock.now_ms() == 5_001_151 and clock.settled_ms() == 5_000_949
    wall[0] += 60_000  # the wall clock jumped (or the host slept): nothing is trusted
    with pytest.raises(FleetError, match="authority_unavailable"):
        clock.now_ms()
    # An anchor older than 10 minutes is not stretched further: re-anchor or stop.
    clock.anchor(5_000_000, sent=10.9, received=11.0)
    mono[0], wall[0] = 610.0, wall[0] + 599_000
    assert clock.settled_ms() == 5_599_000 - 649  # 599 s old: still usable
    mono[0], wall[0] = 612.0, wall[0] + 2_000  # consistent wall time, only older
    with pytest.raises(FleetError, match="authority_unavailable"):
        clock.settled_ms()


def test_a_store_failure_after_success_keeps_the_known_usage(tmp_path):
    env = build(tmp_path)
    env.server.root("connector_read")
    env.server.faults["store"] = ["transport_failed"]  # transient: retried
    assert ActivityWorker(env.deps).run_once() == "succeeded"
    env.server.root("connector_read")
    env.server.faults["store"] = ["conflict"]  # refused: reported, usage kept
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    assert env.server.results[-1].usage.cost_microusd == 1
    # Transient failures until the retries are spent: the provider ran, so the code
    # is final, never a retryable one that would run it again.
    env.server.root("connector_read")
    env.server.faults["store"] = ["transport_failed"] * 3
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    assert env.server.results[-1].usage.cost_microusd == 1


def test_a_mismatched_input_after_start_is_reported_with_zero_usage(tmp_path):
    env = build(tmp_path)
    attempt = env.server.root("connector_read")
    env.server.values[attempt.input.id] = b'{"input":1,"target":null}'  # not the signed bytes
    assert ActivityWorker(env.deps).run_once() == "provider_failed"
    (result,) = env.server.results
    assert calls(env.counter) == 0 and result.usage.cost_microusd == 0


def test_an_unreported_outcome_stays_in_the_journal_as_evidence(tmp_path):
    env = build(tmp_path)
    attempt = env.server.root("connector_read")
    env.server.faults["result"] = ["conflict"]
    assert ActivityWorker(env.deps).run_once() == "unreported"
    entry = env.journal.get(attempt.id)
    assert entry["phase"] == "finished"
    assert entry["outcome"]["code"] == "succeeded" and entry["outcome"]["usage"]["cost_microusd"]


def test_a_fenced_worker_stops_before_polling_even_after_a_restart(tmp_path, monkeypatch):
    env = build(tmp_path)
    env.server.root("connector_read")
    monkeypatch.setattr(invocation, "_fenced", True)  # an earlier cleanup was unconfirmed
    assert ActivityWorker(env.deps).run_once() == "fenced"
    assert "poll" not in env.server.calls and env.server.claims == []
    # The fence is persisted: a restarted process (fresh module state) still stops.
    monkeypatch.setattr(invocation, "_fenced", False)
    restarted = reopen(env)
    assert ActivityWorker(restarted.deps).run_once() == "fenced"
    assert "poll" not in env.server.calls and calls(env.counter) == 0


def test_the_server_anchored_clock_is_required(tmp_path):
    import dataclasses

    env = build(tmp_path)
    with pytest.raises(ValueError):
        dataclasses.replace(env.deps, clock=lambda: time.time_ns() // 1_000_000)


def test_the_harness_clock_is_anchored_on_the_control_plane(tmp_path):
    env = build(tmp_path, skew_ms=120000)
    assert abs(anchored(env.server).now_ms() - env.server.now_ms()) < 1000


class Breaking:
    """A server clock that becomes unavailable when told (a host suspend, a jump)."""

    def __init__(self, clock):
        self.clock, self.broken = clock, False

    def now_ms(self):
        if self.broken:
            raise FleetError("authority_unavailable")
        return self.clock.now_ms()

    def settled_ms(self):
        if self.broken:
            raise FleetError("authority_unavailable")
        return self.clock.settled_ms()


@pytest.mark.parametrize("kind", ["connector_read", "model"])
def test_time_lost_after_a_provider_ran_never_reports_zero(tmp_path, monkeypatch, kind):
    import dataclasses

    script = {"rounds": [{"output": None, "usage": {"cost_microusd": 9}}]}
    env = build(
        tmp_path, model_script=script, read_script={"read": {"usage": {"cost_microusd": 9}}}
    )
    clock = Breaking(env.deps.clock)
    deps = dataclasses.replace(env.deps, clock=clock)
    original = worker.call_provider

    def then_break(*args, **kwargs):
        outcome = original(*args, **kwargs)  # the provider ran and charged 9
        clock.broken = True
        return outcome

    monkeypatch.setattr(worker, "call_provider", then_break)
    attempt = env.server.root(kind)
    assert ActivityWorker(deps).run_once() == "lost"
    assert calls(env.counter) == 1
    # Nothing is reported, least of all zero: the lapse leaves it to reconciliation.
    assert env.server.results == []
    assert all(
        env.journal.get(a.id)["phase"] == "abandoned"
        for a in env.server.attempts.values()
        if a.status == "leased"
    )
    assert env.journal.get(attempt.id)["phase"] == "abandoned"


def test_a_surviving_provider_group_fences_the_restarted_worker(tmp_path):
    import os
    import subprocess

    from lotuspond_runtime.activity_worker import boot_id

    env = build(tmp_path)
    survivor = subprocess.Popen(
        ["sleep", "30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    gone = "00000000-0000-4000-8000-000000000002"
    alive = "00000000-0000-4000-8000-000000000003"
    try:
        for attempt_id in (gone, alive):
            env.journal.advance(attempt_id, "claimed", delivery_sha256="a" * 64, generation=1)
        # A group number that no longer exists, and one whose provider outlived us.
        env.journal.advance(gone, "started", group=2**22 + 7, boot=boot_id())
        assert not env.journal.fenced()
        restarted = reopen(env)
        restarted.journal.advance(alive, "started", group=survivor.pid, boot=boot_id())
        # A worker recovers once, by itself, before its first poll.
        assert ActivityWorker(restarted.deps).run_once() == "fenced"
        assert restarted.journal.fenced()  # no new work until an operator confirms
        assert [restarted.journal.get(a)["phase"] for a in (gone, alive)] == ["abandoned"] * 2
        assert "poll" not in env.server.calls
    finally:
        os.killpg(survivor.pid, 9)
        survivor.wait(timeout=5)


def test_an_unverifiable_delivery_is_declined_and_no_longer_heads_the_polls(tmp_path):
    from fixtures.activity_provider import MODEL

    env = build(tmp_path)
    stuck = env.server.root("connector_read")
    served = env.server.root("connector_read")
    # The pinned records no longer match the signed envelope: it cannot be verified.
    stuck.dependency = MODEL.model_copy(update={"kind": "connector"})
    worker_ = ActivityWorker(env.deps)
    assert worker_.run_once() == "declined"
    assert env.server.declines == [stuck.id] and env.server.claims == []
    # The next poll is no longer headed by it: the other delivery runs.
    assert worker_.run_once() == "succeeded"
    assert [r.attempt_id for r in env.server.results] == [served.id]
    assert calls(env.counter) == 1 and env.journal.get(stuck.id) is None


def test_a_refused_decline_leaves_the_original_refusal(tmp_path):
    env = build(tmp_path)
    attempt = env.server.root("connector_read")
    env.server.withdraw(attempt)  # no longer pending: the control plane refuses
    envelope = json.loads(attempt.delivery.envelope)
    env.server.offers.append(signed(env, dict(envelope, generation=2)))
    with pytest.raises(FleetError, match="command_rejected"):
        ActivityWorker(env.deps).run_once()
    assert env.server.declines == []


def test_a_local_trust_outage_declines_nothing(tmp_path):
    env = build(tmp_path)
    env.server.root("connector_read")

    def unavailable(scope, now):
        raise RuntimeError("trust provider down")

    env.server.current = unavailable  # every delivery would fail alike
    with pytest.raises(FleetError, match="trust_unavailable"):
        ActivityWorker(env.deps).run_once()
    assert env.server.declines == []


def stale_trust(env):
    """The runtime's trust holds only another key: stale after a rotation."""
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from lotuspond_sdk.trust import TrustSet

    other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    env.server.current = lambda scope, now: TrustSet.model_validate(
        {
            "format_version": 1,
            "keys": [
                {
                    "key_id": hashlib.sha256(other).hexdigest(),
                    "public_key": other.hex(),
                    "state": "allowed",
                }
            ],
        }
    )


def test_a_stale_trust_set_declines_nothing(tmp_path):
    env = build(tmp_path)
    for _ in range(3):
        env.server.root("connector_read")  # genuine, pending deliveries
    stale_trust(env)
    worker_ = ActivityWorker(env.deps)
    for _ in range(3):
        with pytest.raises(FleetError, match="trust_unavailable"):
            worker_.run_once()
    # Declining would let one stale runtime fail its pool's attempts at poll rate.
    assert env.server.declines == [] and env.server.claims == []


@pytest.mark.parametrize("code", ["forbidden", "not_found", "conflict"])
def test_a_records_refusal_is_declined_but_an_outage_is_not(tmp_path, code):
    env = build(tmp_path)
    refused = env.server.root("connector_read")
    original = env.server.load

    def load(envelope):
        if envelope.attempt_id == refused.id:
            raise FleetError(code)  # the control plane will not serve this delivery
        return original(envelope)

    env.server.load = load
    assert ActivityWorker(env.deps).run_once() == "declined"
    assert env.server.declines == [refused.id]
    outage = env.server.root("connector_read")

    def down(envelope):
        raise FleetError("authority_unavailable")

    env.server.load = down
    with pytest.raises(FleetError, match="authority_unavailable"):
        ActivityWorker(env.deps).run_once()
    assert outage.id not in env.server.declines


def revoked_trust(env):
    """The runtime's trust marks the control plane's signing key revoked."""
    import hashlib

    from lotuspond_sdk.trust import TrustSet

    public = env.server.key.public_key().public_bytes_raw()
    env.server.current = lambda scope, now: TrustSet.model_validate(
        {
            "format_version": 1,
            "keys": [
                {
                    "key_id": hashlib.sha256(public).hexdigest(),
                    "public_key": public.hex(),
                    "state": "revoked",
                }
            ],
        }
    )


def test_a_broken_runtime_declines_at_most_three_in_a_row(tmp_path):
    env = build(tmp_path)
    attempts = [env.server.root("connector_read") for _ in range(5)]
    good = env.server.current
    revoked_trust(env)  # every delivery now fails alike, in a way no rule singles out
    worker_ = ActivityWorker(env.deps)
    assert [worker_.run_once() for _ in range(3)] == ["declined"] * 3
    with pytest.raises(FleetError, match="command_rejected"):
        worker_.run_once()  # the breaker holds: the refusal stands, nothing declined
    assert env.server.declines == [a.id for a in attempts[:3]]
    # One verified delivery resets it.
    env.server.current = good
    assert worker_.run_once() == "succeeded"
    revoked_trust(env)
    env.server.root("connector_read")
    assert worker_.run_once() == "declined"


def test_a_stale_runtime_identity_declines_nothing(tmp_path):
    import dataclasses

    env = build(tmp_path)
    for _ in range(3):
        env.server.root("connector_read")
    stale = dataclasses.replace(
        env.deps, identity=env.deps.identity.model_copy(update={"generation": 2})
    )
    worker_ = ActivityWorker(stale)
    for _ in range(3):
        with pytest.raises(FleetError, match="command_rejected"):
            worker_.run_once()  # misaddressed by this runtime's own stale identity
    assert env.server.declines == []


def test_the_decline_breaker_survives_new_workers_and_restarts(tmp_path):
    env = build(tmp_path)
    for _ in range(5):
        env.server.root("connector_read")
    revoked_trust(env)
    # A fresh worker for every poll, then a restarted process: still 3 in a row.
    assert [ActivityWorker(env.deps).run_once() for _ in range(2)] == ["declined"] * 2
    restarted = reopen(env)
    assert ActivityWorker(restarted.deps).run_once() == "declined"
    for _ in range(2):
        with pytest.raises(FleetError, match="command_rejected"):
            ActivityWorker(restarted.deps).run_once()
    assert len(env.server.declines) == 3
