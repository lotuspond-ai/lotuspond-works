from datetime import UTC, datetime, timedelta

import pytest
import redis

from lotuspond import mail_worker
from lotuspond.worker_observation import (
    FRESH_FOR_SECONDS,
    MARKER_TTL_SECONDS,
    WORKER_ROLES,
    mark_poll,
    read_observations,
)


class FakeRedis:
    def __init__(self, *, fail=False, corrupt_decode=False):
        self.values = {}
        self.expiries = {}
        self.fail = fail
        self.corrupt_decode = corrupt_decode

    def set(self, key, value, *, ex):
        if self.fail:
            raise redis.RedisError("unavailable")
        self.values[key] = value
        self.expiries[key] = ex

    def mget(self, keys):
        if self.fail:
            raise redis.RedisError("unavailable")
        if self.corrupt_decode:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return [self.values.get(key) for key in keys]


def test_fixed_role_markers_are_fresh_stale_missing_and_bounded():
    observed = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    fake = FakeRedis()
    dependency, roles = read_observations(
        fake, configured=True, enabled={"mail": True}, observed_at=observed
    )
    assert dependency == {"state": "ok", "error_code": None}
    assert roles["mail"]["state"] == "missing"
    assert roles["mail"]["last_poll_at"] is None
    assert mark_poll(fake, "mail", observed_at=observed - timedelta(seconds=10))
    _, roles = read_observations(fake, configured=True, enabled={"mail": True}, observed_at=observed)
    assert roles["mail"]["state"] == "fresh"
    assert roles["mail"]["age_seconds"] == 10
    assert mark_poll(fake, "mail", observed_at=observed - timedelta(seconds=200))
    _, roles = read_observations(fake, configured=True, enabled={"mail": True}, observed_at=observed)
    assert roles["mail"]["state"] == "stale"
    assert roles["mail"]["age_seconds"] == 200
    assert FRESH_FOR_SECONDS == 150
    assert set(fake.expiries.values()) == {MARKER_TTL_SECONDS}


def test_only_the_mail_role_remains_after_the_assurance_workers_were_retired():
    assert WORKER_ROLES == ("mail",)
    for retired in ("evaluation", "alert"):
        with pytest.raises(ValueError, match="Unknown worker observation role"):
            mark_poll(FakeRedis(), retired)


def test_corrupt_future_and_unavailable_markers_never_claim_freshness():
    observed = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    fake = FakeRedis()
    fake.values["lotuspond:worker-observation:v1:mail"] = "not-a-time"
    _, roles = read_observations(fake, configured=True, enabled={"mail": True}, observed_at=observed)
    assert roles["mail"]["error_code"] == "marker_invalid"
    fake.values["lotuspond:worker-observation:v1:mail"] = (
        observed + timedelta(seconds=30)
    ).isoformat()
    dependency, roles = read_observations(
        fake, configured=True, enabled={"mail": True}, observed_at=observed
    )
    assert dependency["state"] == "ok"
    assert roles["mail"]["error_code"] == "marker_invalid"
    _, roles = read_observations(fake, configured=True, enabled={"mail": False}, observed_at=observed)
    assert roles["mail"]["state"] == "disabled"

    unavailable, roles = read_observations(
        FakeRedis(fail=True), configured=True, enabled={"mail": True}, observed_at=observed
    )
    assert unavailable["state"] == "unavailable"
    assert {item["state"] for item in roles.values()} == {"unavailable"}
    assert mark_poll(FakeRedis(fail=True), "mail", observed_at=observed) is False

    corrupt, roles = read_observations(
        FakeRedis(corrupt_decode=True), configured=True, enabled={"mail": True}, observed_at=observed
    )
    assert corrupt == {"state": "unavailable", "error_code": "marker_invalid"}
    assert {item["error_code"] for item in roles.values()} == {"marker_invalid"}


def test_the_mail_worker_records_a_poll_without_exposing_replica_identity(monkeypatch, settings):
    mail = FakeRedis()
    monkeypatch.setattr(mail_worker, "run_once", lambda *_args, **_kwargs: None)
    mail_worker.run(settings, lambda: None, once=True, poll_seconds=1, observation=mail)
    assert set(mail.values) == {"lotuspond:worker-observation:v1:mail"}
