import asyncio
from datetime import UTC, datetime, timedelta

import redis

from lotuspond import alert_worker, mail_worker, worker
from lotuspond.worker_observation import (
    FRESH_FOR_SECONDS,
    MARKER_TTL_SECONDS,
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
    fake = FakeRedis()
    observed = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    assert mark_poll(fake, "evaluation", observed_at=observed - timedelta(seconds=10))
    assert mark_poll(fake, "mail", observed_at=observed - timedelta(seconds=200))
    dependency, roles = read_observations(
        fake,
        configured=True,
        enabled={"evaluation": True, "mail": True, "alert": True},
        observed_at=observed,
    )
    assert dependency == {"state": "ok", "error_code": None}
    assert roles["evaluation"]["state"] == "fresh"
    assert roles["evaluation"]["age_seconds"] == 10
    assert roles["mail"]["state"] == "stale"
    assert roles["mail"]["age_seconds"] == 200
    assert roles["alert"]["state"] == "missing"
    assert roles["alert"]["last_poll_at"] is None
    assert FRESH_FOR_SECONDS == 150
    assert set(fake.expiries.values()) == {MARKER_TTL_SECONDS}


def test_corrupt_future_and_unavailable_markers_never_claim_freshness():
    observed = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    fake = FakeRedis()
    fake.values["lotuspond:worker-observation:v1:evaluation"] = "not-a-time"
    fake.values["lotuspond:worker-observation:v1:mail"] = (
        observed + timedelta(seconds=30)
    ).isoformat()
    dependency, roles = read_observations(
        fake,
        configured=True,
        enabled={"evaluation": True, "mail": True, "alert": False},
        observed_at=observed,
    )
    assert dependency["state"] == "ok"
    assert roles["evaluation"]["error_code"] == "marker_invalid"
    assert roles["mail"]["error_code"] == "marker_invalid"
    assert roles["alert"]["state"] == "disabled"

    unavailable, roles = read_observations(
        FakeRedis(fail=True),
        configured=True,
        enabled={"evaluation": True, "mail": True, "alert": True},
        observed_at=observed,
    )
    assert unavailable["state"] == "unavailable"
    assert {item["state"] for item in roles.values()} == {"unavailable"}
    assert mark_poll(FakeRedis(fail=True), "evaluation", observed_at=observed) is False

    corrupt, roles = read_observations(
        FakeRedis(corrupt_decode=True),
        configured=True,
        enabled={"evaluation": True, "mail": True, "alert": True},
        observed_at=observed,
    )
    assert corrupt == {"state": "unavailable", "error_code": "marker_invalid"}
    assert {item["error_code"] for item in roles.values()} == {"marker_invalid"}


def test_each_worker_records_a_poll_without_exposing_replica_identity(monkeypatch, settings):
    evaluation = FakeRedis()

    async def stop_evaluation(*_args, **_kwargs):
        raise RuntimeError("stop")

    monkeypatch.setattr(worker, "run_once", stop_evaluation)
    try:
        asyncio.run(worker.run_worker(lambda: None, settings, observation=evaluation))
    except RuntimeError as error:
        assert str(error) == "stop"
    assert set(evaluation.values) == {"lotuspond:worker-observation:v1:evaluation"}

    mail = FakeRedis()
    monkeypatch.setattr(mail_worker, "run_once", lambda *_args, **_kwargs: None)
    mail_worker.run(settings, lambda: None, once=True, poll_seconds=1, observation=mail)
    assert set(mail.values) == {"lotuspond:worker-observation:v1:mail"}

    alert = FakeRedis()

    async def stop_alert(*_args, **_kwargs):
        raise RuntimeError("stop")

    monkeypatch.setattr(alert_worker, "run_once", stop_alert)
    try:
        asyncio.run(
            alert_worker.run(
                settings,
                once=True,
                poll_seconds=1,
                observation=alert,
            )
        )
    except RuntimeError as error:
        assert str(error) == "stop"
    assert set(alert.values) == {"lotuspond:worker-observation:v1:alert"}
