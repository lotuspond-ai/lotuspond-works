"""E05 Task 7: the action worker against the synthetic target.

The journal uses `state.private_directory`, which is Linux-only by design; like
E04's worker tests, these run on Linux (CI or a Linux container). Writes are
counted at the synthetic target and by the provider call counter, never by the
worker's own report.
"""

import dataclasses
import json
import os
import signal

import pytest
from action_env import Secrets, build, calls, reopen
from fixtures.synthetic_target import SyntheticTarget

from lotuspond_runtime.action_protocol import ActionReport, ActionStop
from lotuspond_runtime.action_worker import ActionWorker
from lotuspond_runtime.errors import FleetError

SENTINEL = "SENTINEL-SECRET-7f3a9c"


class Crash(BaseException):
    """A crash point: unwinds past every handler, like the process dying there."""


@pytest.fixture
def target(tmp_path):
    with SyntheticTarget(tmp_path / "target", tmp_path / "tls") as running:
        yield running


def entry(env, action):
    return env.journal.get(action.command.command_id, kind="action")


def test_a_reviewed_write_runs_once_and_reports_its_receipt(tmp_path, target):
    env = build(tmp_path, target)
    action = env.plane.action()
    assert ActionWorker(env.deps).run_once() == "accepted"
    (report,) = env.plane.reports
    assert type(report) is ActionReport and report.receipt.attempt == 1
    assert report.receipt.intent_sha256 == action.command.intent_sha256
    assert report.receipt.target_operation_ref == "record-1"
    assert json.loads(env.plane.values[report.response.id])["id"] == "record-1"
    assert (len(target.writes()), calls(env.counter)) == (1, 1)
    assert entry(env, action)["phase"] == "recorded"
    assert ActionWorker(env.deps).run_once() == "idle"


def test_journal_started_before_connector(tmp_path, target):
    def crash(point):
        if point == "started":
            raise Crash

    env = build(tmp_path, target, checkpoint=crash)
    action = env.plane.action()
    with pytest.raises(Crash):
        ActionWorker(env.deps).run_once()
    journal_phase_before_socket = entry(env, action)["phase"]
    assert journal_phase_before_socket == "started"
    assert target.requests() == [] and calls(env.counter) == 0


def test_restart_never_reinvokes_a_started_write(tmp_path, target):
    env = build(tmp_path, target)
    action = env.plane.action()
    pid = os.fork()
    if pid == 0:  # the worker process, SIGKILLed once the target applied the write
        try:

            def kill(point):
                if point == "invoked":
                    os.kill(os.getpid(), signal.SIGKILL)

            ActionWorker(dataclasses.replace(env.deps, checkpoint=kill)).run_once()
        finally:
            os._exit(3)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    target_writes_before_restart = len(target.writes())
    provider_calls_before = calls(env.counter)
    assert (target_writes_before_restart, provider_calls_before) == (1, 1)
    assert entry(env, action)["phase"] == "started"
    restarted = reopen(env)
    # The same command is offered again (this control plane never saw the claim).
    assert ActionWorker(restarted.deps).run_once() == "refused"
    target_writes_after_restart = len(target.writes())
    assert target_writes_after_restart == target_writes_before_restart
    assert calls(env.counter) == provider_calls_before
    # Reported as historical evidence: it may have been applied, usage unknown.
    (stop,) = env.plane.historical
    assert type(stop) is ActionStop and stop.code == "stopped_unknown"
    assert stop.usage.cost_microusd is None
    assert entry(restarted, action)["phase"] == "recorded"


def test_a_claimed_only_action_reports_not_started_after_restart(tmp_path, target):
    env = build(tmp_path, target)
    action = env.plane.action()

    def crashed(delivery):
        raise Crash  # after the claim, before anything started

    original = env.plane.records
    env.plane.records = crashed
    with pytest.raises(Crash):
        ActionWorker(env.deps).run_once()
    assert entry(env, action)["phase"] == "claimed"
    env.plane.records = original
    restarted = reopen(env)
    assert ActionWorker(restarted.deps).run_once() == "idle"
    (stop,) = env.plane.reports  # the same session's lease still holds here
    assert stop.code == "not_started" and stop.usage.cost_microusd == 0
    assert target.requests() == [] and calls(env.counter) == 0


def test_lost_response_reports_unknown(tmp_path, target):
    target.fault(lose_response=1)
    env = build(tmp_path, target)
    env.plane.action()
    assert ActionWorker(env.deps).run_once() == "unknown"
    (report,) = env.plane.reports
    assert report.receipt.status == "unknown" and report.response is None
    usage = report.usage
    assert (usage.input_tokens, usage.output_tokens, usage.cost_microusd) == (0, 0, 0)
    assert len(target.writes()) == 1  # it was applied; only the answer was lost


def test_idempotency_key_is_the_action_id(tmp_path, target):
    env = build(tmp_path, target)
    action = env.plane.action()
    assert ActionWorker(env.deps).run_once() == "accepted"
    received_idempotency_key = target.requests()[0]["key"]
    intent_action_id = action.records.intent.binding.action_id
    assert received_idempotency_key == intent_action_id == action.command.action_id
    # An operation without `target_key` sends no key (positive control of the rule).
    other = build(tmp_path / "none", target, idempotency="none")
    other.plane.action()
    assert ActionWorker(other.deps).run_once() == "accepted"
    assert target.requests()[1]["key"] is None


def test_write_goes_through_destination_transport(tmp_path, target):
    allowed = build(tmp_path / "allowed", target)
    allowed.plane.action()
    assert ActionWorker(allowed.deps).run_once() == "accepted"  # positive control
    assert len(target.requests()) == 1
    refused = build(tmp_path / "refused", target, egress={"allowed_private_cidrs": []})
    refused.plane.action()
    assert ActionWorker(refused.deps).run_once() == "not_started"
    (stop,) = refused.plane.reports
    assert stop.code == "not_started" and stop.usage.cost_microusd == 0
    assert len(target.requests()) == 1  # the egress policy stopped it before a socket


def test_refused_renewal_lets_the_write_finish(tmp_path, target):
    target.fault(delay_ms=2500)
    env = build(tmp_path, target)
    env.plane.action()
    env.plane.refuse_renewal = True
    assert ActionWorker(env.deps).run_once() == "accepted"
    assert env.plane.renewals == [1]  # asked to stop during the write: renewal stopped
    assert len(target.writes()) == 1 and len(env.plane.reports) == 1


def test_a_lost_lease_lets_the_write_finish_and_keeps_its_outcome(tmp_path, target):
    target.fault(delay_ms=2500)
    env = build(tmp_path, target)
    action = env.plane.action()
    env.plane.lose_renewal = True
    assert ActionWorker(env.deps).run_once() == "lost"
    assert len(target.writes()) == 1 and env.plane.reports == []
    kept = entry(env, action)
    assert kept["phase"] == "finished" and kept["outcome"]["receipt"]["status"] == "accepted"
    # The next round reports it as historical evidence; nothing is written again.
    assert ActionWorker(env.deps).run_once() == "idle"
    # This stand-in's lease still holds (only renewal failed), so it lands as a report.
    (late,) = env.plane.reports + env.plane.historical
    assert late.receipt.status == "accepted" and entry(env, action)["phase"] == "recorded"
    assert len(target.writes()) == 1


def test_secrets_resolved_late_and_never_logged(tmp_path, target, capsys, caplog):
    target.require_secret(SENTINEL)
    env = build(tmp_path, target, secrets=Secrets(SENTINEL.encode()))
    env.plane.action()
    assert ActionWorker(env.deps).run_once() == "accepted"
    assert target.requests()[0]["secret_ok"] is True  # positive control: it arrived
    order = env.plane.calls
    assert order.index("claim") < order.index("records") < order.index("resolve")
    captured = capsys.readouterr()
    captured_logs_and_journal = "\n".join(
        [captured.out, captured.err, caplog.text]
        + [p.read_text(errors="replace") for p in env.journal_dir.iterdir() if p.is_file()]
        + [r.model_dump_json() for r in env.plane.reports]
        + [v.decode() for v in env.plane.values.values()]
        + [(target.store / name).read_text() for name in ("requests.log", "writes.log")]
    )
    secret_sentinel = SENTINEL
    assert secret_sentinel not in captured_logs_and_journal
    # Nor its hex form, which is how it travels to the provider child.
    assert SENTINEL.encode().hex() not in captured_logs_and_journal


def test_a_secret_that_cannot_be_resolved_starts_nothing(tmp_path, target):
    class Missing:
        def resolve(self, binding):
            raise FleetError("secret_unavailable")

    env = build(tmp_path, target, secrets=Missing())
    action = env.plane.action()
    assert ActionWorker(env.deps).run_once() == "not_started"
    assert target.requests() == [] and entry(env, action)["phase"] == "recorded"


def test_an_altered_or_misaddressed_command_is_never_claimed(tmp_path, target):
    env = build(tmp_path, target)
    action = env.plane.action()
    altered = type(action.delivery)(
        action.delivery.command_bytes.replace(b'"submit', b'"submit'),
        action.delivery.signature_bytes.replace(b'"signature":"', b'"signature":"A'),
        1,
    )
    env.plane.offers.append(altered)
    assert ActionWorker(env.deps).run_once() == "refused"
    assert env.plane.claims == [] and target.requests() == []
    # Positive control: the genuine delivery is claimed and written.
    assert ActionWorker(env.deps).run_once() == "accepted"


def test_records_that_do_not_match_the_command_start_nothing(tmp_path, target):
    env = build(tmp_path, target)
    action = env.plane.action()
    other = env.plane.action(value={"amount": 9999})
    action.records = other.records  # pinned records of another intent
    other.status = "reported"
    assert ActionWorker(env.deps).run_once() == "not_started"
    assert target.requests() == [] and calls(env.counter) == 0


def test_a_fence_set_after_the_check_starts_nothing(tmp_path, target, monkeypatch):
    import lotuspond_runtime.action_worker as worker
    from lotuspond_runtime.providers import Fenced

    def fenced_by_another_thread(*args, **kwargs):
        raise Fenced  # `call_provider` refuses before any child exists

    env = build(tmp_path, target)
    action = env.plane.action()
    monkeypatch.setattr(worker, "invoke_write", fenced_by_another_thread)
    assert ActionWorker(env.deps).run_once() == "not_started"
    (stop,) = env.plane.reports
    assert stop.code == "not_started" and stop.usage.cost_microusd == 0
    assert target.requests() == [] and entry(env, action)["phase"] == "recorded"


def test_recovery_keeps_entries_while_time_or_the_control_plane_is_unavailable(tmp_path, target):
    def kill(point):
        if point == "invoked":
            raise Crash

    env = build(tmp_path, target, checkpoint=kill)
    action = env.plane.action()
    with pytest.raises(Crash):
        ActionWorker(env.deps).run_once()

    class NoTime:
        def now_ms(self):
            raise FleetError("authority_unavailable")

        settled_ms = now_ms

    stalled = reopen(env, clock=NoTime(), checkpoint=None)
    assert ActionWorker(stalled.deps).recover() is False
    assert entry(stalled, action)["phase"] == "started"  # kept, not abandoned
    restarted = reopen(stalled, clock=env.deps.clock)
    original = env.plane.stop

    def unavailable(stop):
        raise FleetError("transport_failed")

    env.plane.stop = unavailable
    worker = ActionWorker(restarted.deps)
    assert worker.recover() is False
    assert entry(restarted, action)["phase"] == "finished"
    env.plane.stop = original
    assert worker.recover() is True
    (stop,) = env.plane.reports  # the same session's lease still holds here
    assert stop.code == "stopped_unknown" and entry(restarted, action)["phase"] == "recorded"
    assert len(target.writes()) == 1


def test_an_unreported_outcome_is_reported_on_the_next_round(tmp_path, target):
    env = build(tmp_path, target)
    action = env.plane.action()
    original = env.plane.receipt

    def refused(report):
        raise FleetError("transport_failed")

    env.plane.receipt = refused
    assert ActionWorker(env.deps).run_once() == "unreported"
    worker = ActionWorker(env.deps)
    env.plane.receipt = original
    assert worker.run_once() == "idle"
    (report,) = env.plane.reports
    assert report.receipt.status == "accepted" and entry(env, action)["phase"] == "recorded"
    assert len(target.writes()) == 1


def test_fixture_command_trust_validates_against_an_earlier_now(tmp_path):
    """Task 12: a caller reads `now`, then asks for trust; a snapshot valid for the full
    60 s from a later clock reading was refused as too long-lived (`trust_unavailable`)."""
    import json as _json

    from lotuspond_runtime.action_fixture import FixtureCommandTrust
    from lotuspond_runtime.trust import validate_snapshot

    path = tmp_path / "command-trust.json"
    path.write_text(_json.dumps({"format_version": 1, "keys": []}))
    path.chmod(0o600)

    class Advancing:
        def __init__(self):
            self.ms = 1_800_000_000_000

        def now_ms(self):
            self.ms += 1500  # each reading 1.5 s later, as on a loaded host
            return self.ms

    clock = Advancing()
    trust = FixtureCommandTrust(path, clock)
    now = clock.now_ms()
    snapshot = validate_snapshot(trust.current("test-install"), now_ms=now)
    assert now < snapshot.valid_until_ms <= now + 60000
