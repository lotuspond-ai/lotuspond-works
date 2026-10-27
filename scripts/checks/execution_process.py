"""Independent outer supervision for durable-execution qualification (Task 13).

Each owned child runs in its own process group under a bounded timeout, with the
cleanup E03's `fleet_process` already proves (an unconfirmed group is reported,
never inferred clean). On top of that, a census of the other resources a run may
own: Docker containers and volumes carrying the qualification label, and PostgreSQL
schemas with the conformance prefix. A census that cannot be read proves nothing.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

from fleet_process import (
    Cancelled,
    Outcome,
    cancellation_signals,
    run_owned,
    wait_owned,
)

LABEL = "com.lotuspond.conformance"
SCHEMA_PREFIXES = ("conformance_",)

__all__ = [
    "Cancelled",
    "Census",
    "Outcome",
    "cancellation_signals",
    "census",
    "run_owned",
    "wait_owned",
]


@dataclass
class Census:
    containers: list = field(default_factory=list)
    volumes: list = field(default_factory=list)
    schemas: list = field(default_factory=list)
    readable: bool = False

    def owned_since(self, before: Census) -> dict:
        """What appeared after `before` and is still here: never inferred gone."""
        return {
            "containers": sorted(set(self.containers) - set(before.containers)),
            "volumes": sorted(set(self.volumes) - set(before.volumes)),
            "schemas": sorted(set(self.schemas) - set(before.schemas)),
        }

    def receipt(self, before: Census) -> dict:
        left = self.owned_since(before)
        return {
            **left,
            "census_readable": self.readable and before.readable,
            "cleanup_confirmed": self.readable
            and before.readable
            and not any(left.values()),
        }


def _docker(*args):
    result = subprocess.run(
        ["docker", *args], capture_output=True, timeout=60, check=False
    )
    if result.returncode:
        raise RuntimeError("docker census unavailable")
    return [line for line in result.stdout.decode().split() if line]


def census(database_url: str | None = None, *, docker: bool = True) -> Census:
    """The resources a qualification run owns, as they are now."""
    found = Census()
    try:
        if docker:
            found.containers = _docker(
                "ps", "--all", "--quiet", "--filter", f"label={LABEL}"
            )
            found.volumes = _docker(
                "volume", "ls", "--quiet", "--filter", f"label={LABEL}"
            )
        if database_url is not None:
            from sqlalchemy import create_engine, text

            engine = create_engine(database_url, hide_parameters=True)
            try:
                with engine.connect() as connection:
                    found.schemas = sorted(
                        name
                        for name in connection.scalars(
                            text("SELECT nspname FROM pg_namespace")
                        )
                        if name.startswith(SCHEMA_PREFIXES)
                    )
            finally:
                engine.dispose()
        found.readable = True
    except Exception:  # noqa: BLE001 - an unreadable census is reported as such
        found.readable = False
    return found


if __name__ == "__main__":
    print(json.dumps(census(docker=True).__dict__, indent=2))
