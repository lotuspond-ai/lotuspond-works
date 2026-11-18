"""Task 13 conformance: engine invariants (ENG-1, ENG-4 sequences, ENG-5, ENG-6, ENG-8).

ENG-4's grant half needs a cluster-wide role and runs in tier 2, on the harness's own
disposable cluster only (owner decision, 2026-10-26).
"""

import signal
import time

import pytest
from driver import Refused, decoded_events, flow, form_package, patient_read, reconcile_from_journal
from processes import Processes, wait_for
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from lotuspond.models import (
    ExecutablePublication,
    Execution,
    ExecutionArtifact,
    ExecutionDeploymentRevision,
    ExecutionEvent,
    ExecutionStepInstance,
    ExecutionWait,
)


def test_the_execution_deadline_stops_activities_and_waits(system, linux):
    """ENG-1: work stops at the deadline; uncertain work reconciles."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    operator.demand(1500)
    runtime = operator.runtime(customer, "runtime-a", read={"read": {"value": None, "sleep_s": 8}})
    revision = operator.deploy(customer, "activity_deadline", dependencies=[operator.documents[0]])
    activity = operator.start(customer, revision)["id"]
    package = form_package()
    package["package_id"] = "form-deadline"
    package["limits"] = {**package["limits"], "elapsed_seconds": 5}
    waiting_revision = operator.deploy(
        customer, "form-deadline", package=package, dependencies=["waits.policy"]
    )
    waiting = operator.start(customer, waiting_revision)["id"]
    wait = operator.open_wait(customer, waiting, "review")
    # The wait: the deadline ends the execution and closes its wait.
    assert operator.settled(customer, waiting, {"failed", "succeeded"}, 60)["state"] == "failed"
    with Session(w.engine) as db:
        assert db.get(ExecutionWait, wait["id"]).state != "open"
    # The activity: its lease never outlives the boundary; what may have run is reconciled.
    view = operator.settled(
        customer, activity, {"reconciliation_required", "failed", "succeeded"}, 90
    )
    if view["state"] == "reconciliation_required":
        # Only a claimed lease that lapsed leaves an attempt uncertain, and the runtime
        # journals its claim before sending it, so the journal holds the entry whether or
        # not the provider was reached: on a slow runner the lease lapsed between claim
        # and call, and nothing was called (CI, 2026-11-18).
        processes.stop("runtime-a")
        reconcile_from_journal(w, customer, runtime, activity)
        view = operator.settled(customer, activity, {"failed", "succeeded"}, 60)
    assert view["state"] == "failed", view
    # At most the one call that started before the boundary; when the deadline won
    # before dispatch, none (both observed).
    assert Processes.calls(runtime["counter"]) <= 1


def test_event_sequences_are_gap_free_under_concurrent_transitions(system):
    """ENG-4 (tier 1): two orchestrators, many executions, one gap-free sequence each."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    processes.start_worker("orchestrator", "orchestrator-b")
    # A package's `retained_waits` is applied environment-wide (validation finding 25):
    # `waits_fast`'s 10 would let only 10 of these 12 executions ever retain a wait, and
    # the others stay `pending` (finding 2). This case is about event order, not quota,
    # so its package covers both waits of all 12 (owner decision, 2026-10-29).
    package = flow("waits_fast")
    package["package_id"] = "waits-fast-wide"
    package["limits"]["retained_waits"] = 24
    revision = operator.deploy(
        customer,
        "waits_fast",
        package=package,
        label="waits-fast-wide",
        dependencies=["waits.policy"],
    )
    executions = [operator.start(customer, revision)["id"] for _ in range(12)]
    # Each runs its whole life: timer, callback and the callback's expiry. Watching
    # each 3 s callback in turn can miss one on a slow host, where a wait past its
    # deadline is never retained and its execution stays `pending`, as designed.
    for execution_id in executions:
        operator.settled(customer, execution_id, TERMINAL, 180)
        paths = {item["occurrence_path"] for item in operator.waits(customer, execution_id)}
        assert "callback" in paths, paths
    with Session(w.engine) as db:
        for execution_id in executions:
            sequences = list(
                db.scalars(
                    select(ExecutionEvent.sequence)
                    .where(ExecutionEvent.execution_id == execution_id)
                    .order_by(ExecutionEvent.sequence)
                )
            )
            assert sequences == list(range(1, len(sequences) + 1)), sequences


TERMINAL = {"succeeded", "failed", "cancelled"}


def replayed(codes):
    """The observed state from the control events alone.

    A first reservation (`pending` to `running`) and the last closed wait (`waiting`
    to `running`) emit no event (Task 11), so `running` is inferred for both.
    """
    state = "pending"
    for code in codes:
        if code in TERMINAL:
            state = code
        elif code in ("waiting", "paused", "reconciliation_required"):
            state = code
        elif code in ("started", "resumed", "reconciled", "attempt_reserved", "signal_received"):
            state = "running" if state not in TERMINAL else state
    return state


def test_replaying_events_reproduces_the_projected_state(system, linux):
    """ENG-5."""
    w = system
    operator, customer = w.operator, w.customer
    operator.runtime(customer, "runtime-a")
    operator.demand(30000)  # the lease may run to the read's 30 s deadline
    read = operator.deploy(
        customer, "activity_read_30", package=patient_read(), dependencies=[operator.documents[0]]
    )
    form = operator.deploy(customer, "form", package=form_package(), dependencies=["waits.policy"])
    done = operator.start(customer, read)["id"]
    waiting = operator.start(customer, form)["id"]
    cancelled = operator.start(customer, form)["id"]
    operator.settled(customer, done, TERMINAL, 90)
    operator.open_wait(customer, waiting, "review")
    operator.open_wait(customer, cancelled, "review")
    operator.control(customer, cancelled, "cancel")
    operator.settled(customer, cancelled, TERMINAL)
    for execution_id in (done, waiting, cancelled):
        codes = [content["code"] for _, content in decoded_events(w, execution_id)]
        assert replayed(codes) == operator.execution(customer, execution_id)["state"], codes


def test_a_stalled_orchestrator_is_ended_by_its_role_timeouts(system):
    """ENG-6 and spike 8–9 (orchestrator): SIGSTOP inside a transaction."""
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    processes.kill("orchestrator-a")
    processes.start_worker("orchestrator", "stalled", timeout_ms=2000)
    revision = operator.deploy(customer, "wide")
    execution_id = operator.start(customer, revision, value=list(range(1000)))["id"]

    def open_transaction():
        with Session(w.engine) as db:
            return db.scalar(
                text(
                    "SELECT pid FROM pg_stat_activity WHERE application_name = "
                    "'lotuspond-orchestrator:stalled' AND xact_start IS NOT NULL"
                )
            )

    caught = None
    for _ in range(200):  # stop it while one of its transactions is open
        processes.signal("stalled", signal.SIGSTOP)
        caught = open_transaction()
        if caught:
            break
        processes.signal("stalled", signal.SIGCONT)
        time.sleep(0.01)
    assert caught, "never caught the orchestrator inside a transaction"
    # PostgreSQL ends the stalled transaction within the role's 2 s bounds.
    wait_for(lambda: open_transaction() != caught, 15)
    processes.start_worker("orchestrator", "orchestrator-b")
    view = operator.settled(customer, execution_id, TERMINAL, 600)
    assert view["state"] == "succeeded", view  # another worker advanced it
    processes.signal("stalled", signal.SIGCONT)


def test_tampered_stored_bytes_are_refused_on_load(system):
    """ENG-8 (integrity case): stored bytes no longer match their digest.

    A signal that must load the plan is refused; a due execution is fenced for
    reconciliation instead of running on the tampered bytes.
    """
    w = system
    operator, customer = w.operator, w.customer
    form = operator.deploy(customer, "form", package=form_package(), dependencies=["waits.policy"])
    timed = operator.deploy(customer, "waits_fast", dependencies=["waits.policy"])
    waiting = operator.start(customer, form)["id"]
    wait = operator.open_wait(customer, waiting, "review")
    due = operator.start(customer, timed)["id"]
    operator.open_wait(customer, due, "pause")
    with Session(w.engine) as db, db.begin():
        for revision in (form, timed):
            publication = db.get(
                ExecutablePublication, db.get(ExecutionDeploymentRevision, revision).publication_id
            )
            publication.canonical_plan = bytes(publication.canonical_plan) + b" "
    try:
        operator.signal(customer, waiting, wait["id"], "form", {"approved": True})
    except Refused as refused:
        assert refused.code == "dependency_unavailable", refused
    else:
        raise AssertionError("a signal was accepted against a tampered plan")
    with Session(w.engine) as db:
        assert db.get(ExecutionWait, wait["id"]).state == "open"
    # The timer falls due: loading the plan fails on every try, so its next step never
    # opens and it never completes. (Fencing for reconciliation applies to started
    # executions; this one is still `pending`, so it stays refused, unfenced: recorded.)
    time.sleep(12)
    with Session(w.engine) as db:
        opened = db.scalars(select(ExecutionWait.id).where(ExecutionWait.execution_id == due)).all()
        execution = db.get(Execution, due)
    assert len(opened) == 1, opened  # only the timer: nothing ran on the tampered plan
    assert execution.state in ("pending", "reconciliation_required"), execution.state


def test_tampered_package_and_input_bytes_are_refused_on_load(system):
    """ENG-8 (integrity case), package and input: each is checked against its digest.

    A signal must load the deployment's package: its tampered bytes refuse it. An
    execution's tampered input artifact is refused when progression first opens it.
    Each has an untampered twin that proceeds (positive control).
    """
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    tampered = operator.deploy(
        customer, "form", package=form_package(), dependencies=["waits.policy"]
    )
    intact = operator.deploy(
        customer,
        "form-intact",
        package={**form_package(), "package_id": "form-intact"},
        dependencies=["waits.policy"],
    )
    forms = {revision: operator.start(customer, revision)["id"] for revision in (tampered, intact)}
    waits = {
        revision: operator.open_wait(customer, forms[revision], "review") for revision in forms
    }
    with Session(w.engine) as db, db.begin():
        publication = db.get(
            ExecutablePublication, db.get(ExecutionDeploymentRevision, tampered).publication_id
        )
        publication.canonical_package = bytes(publication.canonical_package) + b" "
    with pytest.raises(Refused) as refused:
        operator.signal(
            customer, forms[tampered], waits[tampered]["id"], "form", {"approved": True}
        )
    assert refused.value.code in ("dependency_unavailable", "artifact_corrupt"), refused.value
    operator.signal(customer, forms[intact], waits[intact]["id"], "form", {"approved": True})
    assert (
        operator.settled(customer, forms[intact], {"succeeded", "failed"})["state"] == "succeeded"
    )
    with Session(w.engine) as db:
        assert db.get(ExecutionWait, waits[tampered]["id"]).state == "open"
    # Input: admitted while no orchestrator runs, then its ciphertext changes.
    processes.kill("orchestrator-a")
    wide = operator.deploy(customer, "wide")
    bad = operator.start(customer, wide, value=list(range(5)))["id"]
    good = operator.start(customer, wide, value=list(range(5)))["id"]
    with Session(w.engine) as db, db.begin():
        execution = db.get(Execution, bad)
        artifact = db.scalar(
            select(ExecutionArtifact).where(
                ExecutionArtifact.id == execution.input_artifact_id,
                ExecutionArtifact.version == execution.input_artifact_version,
            )
        )
        artifact.ciphertext = bytes([artifact.ciphertext[0] ^ 1]) + bytes(artifact.ciphertext[1:])
    processes.start_worker("orchestrator", "orchestrator-b")
    assert operator.settled(customer, good, {"succeeded", "failed"}, 120)["state"] == "succeeded"
    time.sleep(5)  # the tampered one has had as long, and more
    with Session(w.engine) as db:
        execution = db.get(Execution, bad)
        steps = db.scalar(
            select(func.count())
            .select_from(ExecutionStepInstance)
            .where(ExecutionStepInstance.execution_id == bad)
        )
    assert execution.state in ("pending", "reconciliation_required"), execution.state
    assert execution.result_artifact_id is None and steps == 0  # nothing ran on it
