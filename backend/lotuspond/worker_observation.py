"""Bounded, best-effort worker poll observations stored in Redis."""

from __future__ import annotations

from datetime import UTC, datetime

import redis

from .config import Settings

WORKER_ROLES = ("evaluation", "mail", "alert")
MARKER_TTL_SECONDS = 300
FRESH_FOR_SECONDS = 150
KEY_PREFIX = "lotuspond:worker-observation:v1"


def observation_client(settings: Settings):
    if not settings.redis_url:
        return None
    return redis.Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_timeout=2,
        socket_connect_timeout=2,
        decode_responses=True,
    )


def close_observation_client(client) -> None:
    if client is None:
        return
    try:
        client.close()
    except redis.RedisError:
        pass


def _key(role: str) -> str:
    if role not in WORKER_ROLES:
        raise ValueError("Unknown worker observation role")
    return f"{KEY_PREFIX}:{role}"


def mark_poll(client, role: str, *, observed_at: datetime | None = None) -> bool:
    """Record that a fixed worker role began a poll; never fail worker execution."""

    if client is None:
        return False
    timestamp = (observed_at or datetime.now(UTC)).astimezone(UTC)
    try:
        client.set(_key(role), timestamp.isoformat(), ex=MARKER_TTL_SECONDS)
    except redis.RedisError:
        return False
    return True


def _observation(state: str, *, timestamp=None, age=None, error_code=None) -> dict:
    return {
        "state": state,
        "last_poll_at": timestamp,
        "age_seconds": age,
        "fresh_for_seconds": FRESH_FOR_SECONDS,
        "error_code": error_code,
    }


def read_observations(
    client,
    *,
    configured: bool,
    enabled: dict[str, bool],
    observed_at: datetime,
) -> tuple[dict, dict[str, dict]]:
    """Read all fixed markers with one Redis operation.

    A marker reports only the last poll boundary. Missing or stale markers never
    assert that a process is dead or that an in-flight task is not progressing.
    """

    if not configured:
        dependency = {"state": "not_configured", "error_code": None}
        values = None
    elif client is None:
        dependency = {"state": "unavailable", "error_code": "redis_unavailable"}
        values = None
    else:
        try:
            values = client.mget([_key(role) for role in WORKER_ROLES])
            if not isinstance(values, (list, tuple)) or len(values) != len(WORKER_ROLES):
                values = None
                dependency = {"state": "unavailable", "error_code": "marker_invalid"}
            else:
                dependency = {"state": "ok", "error_code": None}
        except redis.RedisError:
            values = None
            dependency = {"state": "unavailable", "error_code": "redis_unavailable"}
        except UnicodeError:
            values = None
            dependency = {"state": "unavailable", "error_code": "marker_invalid"}

    result: dict[str, dict] = {}
    for index, role in enumerate(WORKER_ROLES):
        if not enabled[role]:
            result[role] = _observation("disabled")
            continue
        if dependency["state"] == "not_configured":
            result[role] = _observation("not_configured")
            continue
        if dependency["state"] == "unavailable":
            result[role] = _observation(
                "unavailable", error_code=dependency["error_code"]
            )
            continue
        raw = values[index] if values else None
        if raw is None:
            result[role] = _observation("missing")
            continue
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="strict")
            timestamp = datetime.fromisoformat(raw)
            if timestamp.tzinfo is None:
                raise ValueError("naive marker")
            timestamp = timestamp.astimezone(UTC)
            delta = (observed_at - timestamp).total_seconds()
            if delta < -5 or delta > MARKER_TTL_SECONDS + 5:
                raise ValueError("marker clock outside bounds")
            age = max(0, int(delta))
        except (TypeError, ValueError, UnicodeError):
            result[role] = _observation("unavailable", error_code="marker_invalid")
            continue
        result[role] = _observation(
            "fresh" if age <= FRESH_FOR_SECONDS else "stale",
            timestamp=timestamp.isoformat(),
            age=age,
        )
    return dependency, result
