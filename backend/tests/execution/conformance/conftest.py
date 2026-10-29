"""Task 13 conformance fixtures: one owned world per case, over each registered engine.

`LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS=1` turns every missing prerequisite (the
test database, or Linux for real runtime processes) into a failure, never a skip.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import driver as drivers  # noqa: E402
import world as worlds  # noqa: E402
from processes import Processes  # noqa: E402

# Registered `ExecutionEngine` implementations. A replacement engine registers here
# and must pass this suite; `postgresql` is the only one.
ENGINES = ("postgresql",)
REQUIRED = os.environ.get("LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS") == "1"


def missing(reason):
    if REQUIRED:
        pytest.fail(f"required durable execution prerequisite missing: {reason}")
    pytest.skip(reason)


@pytest.fixture(params=ENGINES)
def engine_name(request):
    return request.param


@pytest.fixture
def world(engine_name, tmp_path):
    url = os.environ.get("LOTUSPOND_PLATFORM_TEST_DATABASE_URL")
    if not url:
        missing("LOTUSPOND_PLATFORM_TEST_DATABASE_URL is not set")
    with worlds.owned(url, tmp_path / "world") as created:
        yield created


@pytest.fixture
def processes(world):
    owned = Processes(world)
    try:
        yield owned
    finally:
        receipt = owned.close()
        worlds.write_private(world.root / "cleanup.json", receipt)
        # Shown only for a failing case: the tail of each child's own output.
        for log in (
            sorted((world.root / "logs").glob("*")) if (world.root / "logs").exists() else ()
        ):
            tail = log.read_text(errors="replace").splitlines()[-15:]
            if tail:
                print(f"--- {log.name}", *tail, sep="\n")
        assert receipt["confirmed"], receipt


@pytest.fixture
def system(world, processes):
    """The API, one orchestrator, one sweeper and a provisioned tenant."""
    processes.start_api()
    processes.start_worker("orchestrator", "orchestrator-a", wait=False)
    processes.start_worker("sweeper", "sweeper-a", wait=False)
    processes.wait_ready("orchestrator-a", "sweeper-a")
    operator = drivers.Operator(world, processes)
    customer = operator.tenant(world.engine)
    world.operator, world.customer, world.processes = operator, customer, processes
    yield world
    customer.close()


@pytest.fixture
def linux():
    if sys.platform != "linux":
        missing("real runtime processes need Linux private state")


owned_world = worlds.owned  # tier 2 builds its world on its own cluster
