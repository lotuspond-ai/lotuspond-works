import argparse
import time

from .config import Settings
from .database import engine_for, sessions_for
from .mail import run_once


def run(settings: Settings, factory, *, once: bool, poll_seconds: float):
    while True:
        result = run_once(factory, settings)
        if once:
            print("Mail outbox item processed." if result is not None else "No mail was queued.")
            return
        if result is None:
            time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="Deliver encrypted Lotuspond mail outbox entries")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()
    if args.poll_seconds < 0.25 or args.poll_seconds > 60:
        parser.error("poll seconds must be between 0.25 and 60")
    settings = Settings()
    if not settings.mail_enabled:
        raise SystemExit("Mail delivery is disabled; no messages were sent.")
    engine = engine_for(settings)
    factory = sessions_for(engine)
    try:
        run(settings, factory, once=args.once, poll_seconds=args.poll_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
