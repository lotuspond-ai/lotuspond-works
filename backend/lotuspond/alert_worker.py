"""Command-line worker for durable incident alert delivery."""

import argparse
import asyncio

from .alerts import run_once
from .config import Settings
from .database import engine_for, sessions_for
from .worker_observation import close_observation_client, mark_poll, observation_client


async def run(settings: Settings, *, once: bool, poll_seconds: float, observation=None) -> None:
    engine = engine_for(settings)
    sessions = sessions_for(engine)
    try:
        while True:
            mark_poll(observation, "alert")
            result = await run_once(sessions, settings)
            if once:
                print("Alert outbox item processed." if result is not None else "No alert was queued.")
                return
            if result is None:
                await asyncio.sleep(poll_seconds)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver signed Lotuspond incident webhooks")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()
    if args.poll_seconds < 0.25 or args.poll_seconds > 60:
        parser.error("poll seconds must be between 0.25 and 60")
    settings = Settings()
    if not any(item.enabled for item in settings.alert_webhooks):
        raise SystemExit("Alert delivery is disabled; no webhooks were sent.")
    observation = observation_client(settings)
    try:
        asyncio.run(
            run(
                settings,
                once=args.once,
                poll_seconds=args.poll_seconds,
                observation=observation,
            )
        )
    finally:
        close_observation_client(observation)


if __name__ == "__main__":
    main()
