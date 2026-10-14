import importlib
import threading

import pytest
from pydantic import SecretStr
from test_client import IDENTITY, SESSION, STATE, TOKEN
from test_state import P, credential, private_dir, state_module

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    AcquiredSession,
    PreparedCredential,
    RuntimeObservation,
    SelfState,
)


def supervisor_module():
    assert importlib.util.find_spec("lotuspond_runtime.supervisor"), "missing credential supervisor"
    return importlib.import_module("lotuspond_runtime.supervisor")


class Clock:
    def __init__(self):
        self.now = 0

    def wall(self):
        return self.now

    def monotonic(self):
        return self.now / 1000

    def wait(self, stop, seconds):
        self.now += int(seconds * 1000)
        return stop.is_set()


class Authority:
    def __init__(self, clock, stop):
        self.clock, self.stop = clock, stop
        self.active, self.version, self.calls = TOKEN, 1, []
        self.lease = None
        self.expiry = 900000
        self.prepared = False

    def state(self):
        return SelfState.model_validate(
            dict(
                STATE,
                identity=dict(IDENTITY, credential_version=self.version),
                server_time_ms=self.clock.now,
                credential_expires_at_ms=self.expiry,
                session_expires_at_ms=self.lease,
            )
        )

    def self_state(self, token):
        self.calls.append("self")
        if token != self.active:
            raise FleetError("unauthenticated")
        return self.state()

    def acquire(self, token):
        self.calls.append("acquire")
        if self.lease is not None and self.clock.now < self.lease:
            raise FleetError("conflict")
        self.lease = self.clock.now + 60000
        self.prepared = False
        return AcquiredSession(
            secret=SESSION,
            identity=self.state().identity,
            expires_at_ms=self.lease,
            server_time_ms=self.clock.now,
        )

    def heartbeat(self, token, session):
        self.calls.append("heartbeat")
        self.lease = self.clock.now + 60000
        if self.clock.now >= 640000:
            self.stop.set()
        return self.state()

    def prepare(self, token, session):
        self.calls.append("prepare")
        self.prepared = True
        return PreparedCredential(
            token=P,
            credential_version=2,
            activation_deadline_ms=self.clock.now + 60000,
            server_time_ms=self.clock.now,
        )

    def activate(self, token, session):
        self.calls.append("activate")
        if token == self.active:
            return self.state()
        if session != SESSION or not self.prepared:
            raise FleetError("unauthenticated")
        self.active, self.version = token, 2
        self.expiry = self.clock.now + 900000
        return self.state()

    def close(self):
        pass


def supervisor(client, store, clock, **kwargs):
    module = supervisor_module()
    return module.RuntimeSupervisor(
        client,
        store,
        RuntimeObservation(platform="linux", connectors=()),
        installation_id="test-install",
        wall_ms=clock.wall,
        monotonic=clock.monotonic,
        jitter_ms=lambda: 0,
        wait=clock.wait,
        **kwargs,
    )


def test_rotation_scheduling_and_session_memory_only(tmp_path, capsys):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    directory = private_dir(tmp_path)
    with state_module().CredentialStore(directory) as store:
        store.save(None, credential())
        assert supervisor(authority, store, clock).run(stop) == 0
        assert store.load().active_token == SecretStr(P)
    assert authority.calls.count("prepare") == 1
    assert authority.calls.count("heartbeat") >= 30
    output = "".join(capsys.readouterr())
    disk = b"".join(p.read_bytes() for p in directory.iterdir())
    assert SESSION.get_secret_value().encode() not in disk + output.encode()


@pytest.mark.parametrize(
    "point",
    [
        "prepare_received",
        "before_pending_save",
        "after_pending_save",
        "before_activate",
        "after_activate",
        "before_final_save",
        "after_final_save",
    ],
)
def test_rotation_restart_matrix(tmp_path, point):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    directory = private_dir(tmp_path)

    class Crash(BaseException):
        pass

    def checkpoint(name):
        if name == point:
            raise Crash()

    with state_module().CredentialStore(directory) as store:
        store.save(None, credential())
        with pytest.raises(Crash):
            supervisor(authority, store, clock, checkpoint=checkpoint).run(stop)
    # Original session proof was lost; restart may only retry an already-active P.
    stop.clear()
    clock.now += 61000
    with state_module().CredentialStore(directory) as store:
        restarted = supervisor(authority, store, clock)
        assert restarted.run(stop) == 0
        assert store.load().active_token == authority.active


def test_late_prepare_response_is_not_persisted(tmp_path):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    original = authority.prepare

    def late(*args):
        result = original(*args)
        clock.now += 11000
        return result

    authority.prepare = late
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        assert supervisor(authority, store, clock).run(stop) == 3
        assert store.load() == credential()


@pytest.mark.parametrize("code", ["transport_failed", "rate_limited", "authority_unavailable"])
def test_uncertain_pending_recovery_stops_without_discard(tmp_path, code):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)

    def fail(*args):
        raise FleetError(code)

    authority.activate = fail
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential(0, True))
        assert supervisor(authority, store, clock).run(stop) == 3
        assert store.load() == credential(0, True)
        assert "acquire" not in authority.calls


def test_pending_active_restart_waits_for_live_lease(tmp_path):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    authority.active, authority.version = SecretStr(P), 2
    authority.lease = 60000
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential(0, True))
        # Stop after acquisition so the test examines the lost-session recovery boundary.
        original = authority.acquire

        def acquire(token):
            result = original(token)
            stop.set()
            return result

        authority.acquire = acquire
        assert supervisor(authority, store, clock).run(stop) == 0
        assert clock.now >= 60000
        assert store.load().active_token == SecretStr(P)


@pytest.mark.parametrize("phase", ["acquire", "prepare", "activate", "heartbeat"])
def test_lost_mutation_response_preserves_recovery_state(tmp_path, phase):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    original = getattr(authority, phase)

    def lost(*args):
        original(*args)
        raise FleetError("transport_failed")

    setattr(authority, phase, lost)
    directory = private_dir(tmp_path)
    with state_module().CredentialStore(directory) as store:
        store.save(None, credential())
        assert supervisor(authority, store, clock).run(stop) == 3
        assert store.load().pending_token == (SecretStr(P) if phase == "activate" else None)
        if phase == "activate":
            assert authority.active == SecretStr(P)
        if phase == "acquire":
            assert authority.lease == 60000


def test_clock_disagreement_and_identity_mismatch_fence(tmp_path):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    original = authority.self_state
    authority.self_state = lambda token: original(token).model_copy(
        update={"server_time_ms": 30001}
    )
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        assert supervisor(authority, store, clock).run(stop) == 3
        assert "acquire" not in authority.calls


def test_server_clock_schedules_rotation_with_five_minutes_remaining(tmp_path):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    original = authority.prepare

    def prepare(*args):
        assert authority.expiry - clock.now >= 300000
        return original(*args)

    authority.prepare = prepare
    # Local clock is 29 seconds behind; scheduling still follows confirmed server time.
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        module = supervisor_module()
        instance = module.RuntimeSupervisor(
            authority,
            store,
            RuntimeObservation(platform="linux", connectors=()),
            installation_id="test-install",
            wall_ms=lambda: max(0, clock.now - 29000),
            monotonic=clock.monotonic,
            jitter_ms=lambda: 0,
            wait=clock.wait,
        )
        assert instance.run(stop) == 0


@pytest.mark.parametrize(
    "point",
    [
        "prepare_received",
        "before_pending_save",
        "after_pending_save",
        "before_activate",
        "after_activate",
        "before_final_save",
        "after_final_save",
    ],
)
def test_rotation_process_death_at_real_store_boundaries(tmp_path, point):
    import json
    import subprocess
    import sys
    from pathlib import Path

    directory = private_dir(tmp_path)
    with state_module().CredentialStore(directory) as store:
        store.save(None, credential())
    remote = tmp_path / "synthetic-authority.json"
    script = """
import json, os, sys, threading
from pathlib import Path
sys.path.insert(0, sys.argv[4])
from test_supervisor import Clock, Authority, supervisor
from lotuspond_runtime.state import CredentialStore
clock, stop = Clock(), threading.Event()
clock.now = 580000
authority = Authority(clock, stop)
def checkpoint(name):
    if name == sys.argv[2]:
        Path(sys.argv[3]).write_text(json.dumps(dict(active=authority.active.get_secret_value(),
            version=authority.version, lease=authority.lease, now=clock.now, expiry=authority.expiry)))
        os._exit(72)
with CredentialStore(Path(sys.argv[1])) as store:
    supervisor(authority, store, clock, checkpoint=checkpoint).run(stop)
raise AssertionError('missing crash point')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(directory),
            point,
            str(remote),
            str(Path(__file__).parent),
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 72, result.stderr.decode()
    data = json.loads(remote.read_bytes())
    clock, stop = Clock(), threading.Event()
    clock.now = data["now"] + 61000
    authority = Authority(clock, stop)
    authority.active, authority.version = SecretStr(data["active"]), data["version"]
    authority.lease, authority.expiry = data["lease"], data["expiry"]
    with state_module().CredentialStore(directory) as store:
        assert supervisor(authority, store, clock).run(stop) == 0
        assert store.load().active_token == authority.active


def test_unconfirmed_cleanup_overrides_clean_stop(tmp_path):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)

    def failed_cleanup():
        raise FleetError("transport_failed")

    authority.close = failed_cleanup
    stop.set()
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        assert supervisor(authority, store, clock).run(stop) == 3


@pytest.mark.parametrize(
    "phase,field,value",
    [
        ("self_state", "installation_id", "wrong-install"),
        ("self_state", "instance_id", "11234567-89ab-cdef-0123-456789abcdef"),
        ("heartbeat", "installation_id", "wrong-install"),
        ("heartbeat", "pool_id", "11234567-89ab-cdef-0123-456789abcdef"),
        ("heartbeat", "generation", 2),
        ("heartbeat", "recovery_epoch", 2),
    ],
)
def test_independently_provisioned_identity_and_changed_responses_deny(
    tmp_path, phase, field, value
):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    original = getattr(authority, phase)

    def changed(*args):
        result = original(*args)
        return result.model_copy(
            update={"identity": result.identity.model_copy(update={field: value})}
        )

    setattr(authority, phase, changed)
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        assert supervisor(authority, store, clock).run(stop) == 3
        assert store.load() == credential()
        assert "prepare" not in authority.calls
        if phase == "self_state":
            assert "acquire" not in authority.calls


@pytest.mark.parametrize(
    "initial_ms,jitter_ms,heartbeat_latency_ms",
    [
        (0, 0, 1000),
        (0, 0, 9000),
        (0, 0, 10000),
        (15000, 0, 9000),
        (15000, 0, 10000),
        (10000, 5000, 10000),
        (10000, 30000, 10000),
        (0, 30000, 10000),
    ],
)
def test_actual_prepare_keeps_rotation_lead_across_heartbeat_latency(
    tmp_path, initial_ms, jitter_ms, heartbeat_latency_ms
):
    clock, stop = Clock(), threading.Event()
    clock.now = initial_ms
    authority = Authority(clock, stop)
    target = authority.expiry - 300000 - jitter_ms
    heartbeat, prepare, activate = authority.heartbeat, authority.prepare, authority.activate
    events, starts, heartbeat_starts = [], [], []

    def heartbeat_with_latency(*args):
        started = clock.now
        # Exercise real authority/lease behavior, not a mocked return. Delaying
        # only the critical window retains the previously reviewed phase alignment.
        assert started < authority.lease
        heartbeat_starts.append(started)
        response = heartbeat(*args)
        if not starts and started >= target - 11000:
            clock.now += heartbeat_latency_ms
        assert clock.now < authority.lease
        events.append(("heartbeat", started, clock.now))
        return response

    def prepare_with_latency(*args):
        started = clock.now
        starts.append((started, authority.expiry - started))
        assert started < authority.lease
        response = prepare(*args)
        clock.now += 10000  # The full allowed prepare attempt is still lease-safe.
        assert clock.now < authority.lease
        events.append(("prepare", started, clock.now))
        return response

    def activate_with_latency(*args):
        started = clock.now
        assert started < authority.lease
        response = activate(*args)
        clock.now += 10000
        assert clock.now < authority.lease
        events.append(("activate", started, clock.now))
        return response

    authority.heartbeat, authority.prepare, authority.activate = (
        heartbeat_with_latency,
        prepare_with_latency,
        activate_with_latency,
    )
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        instance = supervisor_module().RuntimeSupervisor(
            authority,
            store,
            RuntimeObservation(platform="linux", connectors=()),
            installation_id="test-install",
            wall_ms=clock.wall,
            monotonic=clock.monotonic,
            jitter_ms=lambda: jitter_ms,
            wait=clock.wait,
        )
        assert instance.run(stop) == 0
        assert store.load().active_token == SecretStr(P)
    assert starts == [(target, 300000 + jitter_ms)]
    assert any(event[0] == "heartbeat" and event[1] >= starts[0][0] + 20000 for event in events)
    assert len(starts) == 1
    # One early heartbeat may reserve the rotation window; there is no retry storm.
    assert len(heartbeat_starts) < 36


def test_missed_five_minute_minimum_fences_before_actual_prepare(tmp_path):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)

    def overshooting_wait(stop, seconds):
        clock.wait(stop, seconds)
        if clock.now == 600000:
            clock.now += 1
        return stop.is_set()

    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        instance = supervisor_module().RuntimeSupervisor(
            authority,
            store,
            RuntimeObservation(platform="linux", connectors=()),
            installation_id="test-install",
            wall_ms=clock.wall,
            monotonic=clock.monotonic,
            jitter_ms=lambda: 0,
            wait=overshooting_wait,
        )
        assert instance.run(stop) == 3
        assert store.load() == credential()
    assert "prepare" not in authority.calls


def test_jitter_cushion_allows_slightly_late_target_without_extending_minimum(tmp_path):
    clock, stop = Clock(), threading.Event()
    authority = Authority(clock, stop)
    starts = []
    prepare = authority.prepare

    def measured_prepare(*args):
        starts.append((clock.now, authority.expiry - clock.now))
        return prepare(*args)

    authority.prepare = measured_prepare

    def overshooting_wait(stop, seconds):
        clock.wait(stop, seconds)
        if clock.now == 595000:
            clock.now += 1
        return stop.is_set()

    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        instance = supervisor_module().RuntimeSupervisor(
            authority,
            store,
            RuntimeObservation(platform="linux", connectors=()),
            installation_id="test-install",
            wall_ms=clock.wall,
            monotonic=clock.monotonic,
            jitter_ms=lambda: 5000,
            wait=overshooting_wait,
        )
        assert instance.run(stop) == 0
        assert store.load().active_token == SecretStr(P)
    assert starts == [(595001, 304999)]


def test_valid_c_late_startup_recovers_once_then_resumes_normal_rotation(tmp_path):
    clock, stop = Clock(), threading.Event()
    clock.now = 661000
    authority = Authority(clock, stop)
    starts = []
    q = SecretStr("lpr_" + "C" * 42 + "A")
    heartbeat = authority.heartbeat

    def keep_running_heartbeat(*args):
        result = heartbeat(*args)
        if authority.version < 3:
            stop.clear()
        return result

    def prepare_successor(*args):
        starts.append((clock.now, authority.expiry - clock.now))
        assert clock.now < authority.lease and clock.now < authority.expiry
        authority.prepared = True
        return PreparedCredential(
            token=P if authority.version == 1 else q,
            credential_version=authority.version + 1,
            activation_deadline_ms=clock.now + 60000,
            server_time_ms=clock.now,
        )

    def activate_successor(token, session):
        assert session == SESSION and authority.prepared
        assert clock.now < authority.lease and clock.now < authority.expiry
        authority.active, authority.version = token, authority.version + 1
        authority.expiry = clock.now + 900000
        return authority.state()

    authority.heartbeat = keep_running_heartbeat
    authority.prepare, authority.activate = prepare_successor, activate_successor
    with state_module().CredentialStore(private_dir(tmp_path)) as store:
        store.save(None, credential())
        assert supervisor(authority, store, clock).run(stop) == 0
        assert store.load().active_token == q
    assert starts == [(661000, 239000), (1261000, 300000)]
