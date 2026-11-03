"""Task 13 conformance: executable-v2 regions the spike never ran (REG-1 to REG-3, REG-6)."""

import time
from datetime import UTC

import pytest
from driver import (
    Refused,
    flow,
    parallel_failure_after_success,
    parent_of,
    read_iteration,
    serving,
    strict_child,
)
from processes import Processes, wait_for
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from lotuspond.models import (
    Execution,
    ExecutionAttempt,
    ExecutionStepInstance,
    ExecutionUsageReservation,
)


def attempt_states(world, execution_id):
    with Session(world.engine) as db:
        rows = db.execute(
            select(ExecutionStepInstance.occurrence_path, ExecutionAttempt.state)
            .join(ExecutionAttempt, ExecutionAttempt.step_instance_id == ExecutionStepInstance.id)
            .where(ExecutionAttempt.execution_id == execution_id)
        ).all()
    return {path: state for path, state in rows}


def test_a_failing_branch_cancels_undispatched_siblings_and_keeps_results(system, linux):
    """REG-1: all-join; one branch fails after another succeeded; no join.

    Four branches, sequenced deterministically:
    - `fast` reads at once, on a runtime that is then stopped: it succeeds first;
    - `slow` reads after 8 s; a hand-driven runtime claims it and never reports, so
      it is in flight when `bad` fails at 15 s, and its result stays uncertain;
    - `late` would read after 25 s.
    The fast runtime restarts after the failure and stays alive past 25 s, so a
    sibling that was not cancelled would be dispatched and called: none is.
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    # Both reads fit the package's 100 s root budget; the claimed read's lease (30 s)
    # outlives the failure.
    operator.demand(30000)
    revision = operator.deploy(
        customer,
        "parallel-failure",
        package=parallel_failure_after_success(fail_after=15, hold=25, slow=8),
        dependencies=[operator.documents[0]],
        capacity=3,
    )
    hand = operator.hand(customer, "hand")
    fast = operator.runtime(customer, "runtime-fast")
    serving(w, fast)  # up and polling before the clock starts
    try:
        started = time.monotonic()
        execution_id = operator.start(customer, revision, value=0)["id"]  # divide by zero
        # Well inside the slow branch's 8 s timer, after which this runtime must be gone.
        wait_for(lambda: attempt_states(w, execution_id).get("both:fast/read") == "succeeded", 7)
        processes.stop("runtime-fast")
        assert Processes.calls(fast["counter"]) == 1
        # At most one poll a second: the fleet allows 60 a minute per instance.
        # At most one poll a second: the fleet allows 60 a minute per instance.
        delivery = wait_for(lambda: hand.activity.poll(), 30, interval=1.0)
        hand.activity.claim(delivery)  # in flight from here, never reported
        view = operator.settled(
            customer, execution_id, {"succeeded", "failed", "reconciliation_required"}, 60
        )
        assert view["state"] != "succeeded", view
        processes.start_runtime_process("runtime-fast")
        time.sleep(max(0, started + 40 - time.monotonic()))  # past the late timer
        assert processes.children["runtime-fast"].poll() is None  # alive, polling
    finally:
        hand.close()
    states = attempt_states(w, execution_id)
    assert states["both:fast/read"] == "succeeded", states  # the completed result is kept
    assert states["both:slow/held"] in ("claimed", "uncertain"), states  # kept, not undone
    assert states["both:bad"] == "failed", states
    with Session(w.engine) as db:
        late = db.scalar(
            select(func.count())
            .select_from(ExecutionAttempt)
            .join(
                ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id
            )
            .where(
                ExecutionAttempt.execution_id == execution_id,
                ExecutionStepInstance.occurrence_path.like("%late%"),
            )
        )
        execution = db.get(Execution, execution_id)
    assert late == 0  # the late branch never reached its read: nothing to dispatch
    assert execution.result_artifact_id is None  # no join
    assert Processes.calls(fast["counter"]) == 1  # nothing ran after the failure


def test_an_iteration_keeps_order_bounds_concurrency_and_runs_each_item_once(system, linux):
    """REG-2: 1,000 items, concurrency 5, the orchestrator killed mid-iteration."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    operator.demand(30000)  # each lease may run to its read's 30 s deadline
    runtimes = [operator.runtime(customer, f"runtime-{n}") for n in range(5)]
    revision = operator.deploy(
        customer,
        "read-iteration",
        package=read_iteration(1000, 5),
        dependencies=[operator.documents[0]],
        capacity=5,
    )
    items = list(range(1000, 0, -1))  # a non-trivial order
    execution_id = operator.start(customer, revision, value=items)["id"]
    deadline = time.monotonic() + 1200
    while sum(Processes.calls(r["counter"]) for r in runtimes) < 300:
        assert time.monotonic() < deadline, "the iteration stalled"
        time.sleep(0.5)
    processes.kill("orchestrator-a")
    processes.start_worker("orchestrator", "orchestrator-b")
    view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 2400)
    assert view["state"] == "succeeded", view
    assert sum(Processes.calls(r["counter"]) for r in runtimes) == 1000  # each item once
    graph = w.graph()
    with Session(w.engine) as db:
        execution = db.get(Execution, execution_id)
        import json

        from driver import _scope_of

        output = json.loads(
            graph.store._get_internal(
                db,
                _scope_of(execution),
                {
                    "id": execution.result_artifact_id,
                    "version": execution.result_artifact_version,
                    "sha256": execution.result_sha256,
                },
            )
        )
        spans = [
            (int(row.created_at.astimezone(UTC).timestamp() * 1000), row.compute_stopped_at_ms)
            for row in db.scalars(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.execution_id == execution_id
                )
            )
        ]
    assert output == items  # output order equals input order
    points = sorted([(start, 1) for start, _ in spans] + [(stop, -1) for _, stop in spans])
    active = peak = 0
    for _, change in points:
        active += change
        peak = max(peak, active)
    assert peak <= 5, peak  # never more than the declared concurrency


def test_a_stricter_subworkflow_limit_applies_to_the_whole_execution(system, linux):
    """REG-3: inherited limits apply, and the root budget covers parent and child."""
    w = system
    operator, customer = w.operator, w.customer
    operator.runtime(customer, "runtime-a")
    results = {}
    for active_seconds in (100, 15):
        child = strict_child(active_seconds)
        revision = operator.deploy(
            customer,
            f"parent-{active_seconds}",
            package=parent_of(child),
            dependencies=[operator.documents[0], child],
        )
        execution_id = operator.start(customer, revision)["id"]
        results[active_seconds] = execution_id
    # Control: the child's own limit allows both reads.
    assert (
        operator.settled(customer, results[100], {"succeeded", "failed"}, 90)["state"]
        == "succeeded"
    )
    # Strict: the child's 15 s bound the whole execution; the parent's 10 s read
    # already holds 10 s, so the child's read can never be reserved.
    time.sleep(8)
    with Session(w.engine) as db:
        reserved = list(
            db.scalars(
                select(ExecutionUsageReservation).where(
                    ExecutionUsageReservation.execution_id == results[15]
                )
            )
        )
        execution = db.get(Execution, results[15])
    assert len(reserved) == 1, len(reserved)
    assert execution.state in ("running", "pending") and execution.reason == "budget"
    operator.control(customer, results[15], "cancel")


def test_the_largest_expansion_runs_and_one_more_step_is_refused(system):
    """REG-6: the 10,000-step ceiling holds; transitions read one region, not all of it."""
    w = system
    operator, customer = w.operator, w.customer
    grown = flow("wide")
    chain = grown["body"]["steps"][0]["body"]["steps"]
    chain.append({**chain[0], "id": "extra"})
    grown["package_id"] = "wide-plus-one"
    with pytest.raises(Refused) as refused:
        operator.publish(customer, grown)
    # Refused at publication. (Task 3 reports every compile refusal, this one too, as
    # `dependency_unavailable`; recorded for the owner in the validation document.)
    assert refused.value.code == "dependency_unavailable"
    revision = operator.deploy(customer, "wide")
    schema = w.config["schema"]

    def rows_read():
        """Rows read from the table so far: by sequential scans, and through indexes."""
        with Session(w.engine) as db:
            return db.execute(
                text(
                    "SELECT coalesce(seq_tup_read, 0), coalesce(idx_tup_fetch, 0) "
                    "FROM pg_stat_user_tables WHERE schemaname = :schema "
                    "AND relname = 'execution_step_instances'"
                ),
                {"schema": schema},
            ).one()

    before = rows_read()
    execution_id = operator.start(customer, revision, value=list(range(1000)))["id"]
    view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 600)
    assert view["state"] == "succeeded", view
    time.sleep(1.5)  # statistics are flushed at most every second
    with Session(w.engine) as db:
        instances = db.scalar(
            select(text("count(*)"))
            .select_from(ExecutionStepInstance)
            .where(ExecutionStepInstance.execution_id == execution_id)
        )
    after = rows_read()
    sequential, indexed = (after[0] - before[0]) / instances, (after[1] - before[1]) / instances
    per_instance = sequential + indexed
    print(
        f"REG-6: {instances} step instances, {per_instance:.0f} rows read per instance"
        f" ({sequential:.0f} by sequential scans, {indexed:.0f} through indexes)"
    )
    # This catches whole-expansion reads on every transition: reading all 9,001 steps
    # each time is at least 9,001 rows per instance. Region-scoped loads cost about
    # 1,000 rows per instance: the root region's 1,000 pinned items, or one item's
    # eight steps. The disposable table grows from empty faster than autovacuum
    # analyses it, so plans vary: 1,000 to 3,569 were measured (VALIDATION, 28 October).
    assert instances >= 9001 and per_instance < 9001, (instances, sequential, indexed)
