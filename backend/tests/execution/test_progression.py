"""Interpreter v1: pure region derivation and database row transitions."""

import json
from pathlib import Path

import pytest
from lotuspond_sdk.executable import validate_executable_package
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lotuspond.execution.contracts import AdmissionRequest, DependencyBinding
from lotuspond.execution.deployments import create_deployment, set_deployment_status
from lotuspond.execution.engine_contracts import INTERPRETER_V1, WorkerIdentity
from lotuspond.execution.expressions import MISSING, data_export, typed_export
from lotuspond.execution.program import ProgramError, program_from, resolve_occurrence
from lotuspond.execution.progression import (
    AttemptRecord,
    CancelUndispatched,
    Complete,
    ConsumeSignal,
    CreateAttempt,
    Fail,
    InterpreterV1,
    OccurrenceRecord,
    OpenWait,
    ProgressionState,
    RecordPure,
    SignalRecord,
    WaitRecord,
    derive,
    next_actions,
    signal_digest,
)
from lotuspond.models import (
    Execution,
    ExecutionArtifact,
    ExecutionAttempt,
    ExecutionCapacity,
    ExecutionEvent,
    ExecutionStepInstance,
    ExecutionWait,
)

from .engine_fixtures import FixtureFence, FixtureSweeper
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_deployments import deployment_request, within
from .test_publication import harness as harness

FLOW = Path(__file__).parent / "fixtures/control-flow"
EXECUTION = "00000000-0000-4000-8000-000000000001"
NOW = 1_800_000_000_000
DEADLINE = NOW + 86_400_000


def raw(name):
    return json.loads((FLOW / f"{name}.json").read_text())


def program(name, *sources):
    package = validate_executable_package(raw(name))
    return program_from(package, dict(enumerate(raw(s) for s in sources)))


def done(path, value, ordinal=1):
    return OccurrenceRecord(path, (AttemptRecord(f"a-{path}", ordinal, "succeeded", value),))


def failed(path, code, ordinal=1, at=NOW):
    return OccurrenceRecord(
        path, (AttemptRecord(f"a-{path}", ordinal, "failed", MISSING, code, at),)
    )


def state(plan, region="", records=(), input=None, item=MISSING, index=None, outer=None, **kw):
    if input is not None:
        input = typed_export(input, data_export(plan.root.input_schema, plan.root.schemas))
    return ProgressionState(
        execution_id=EXECUTION,
        region_path=region,
        requested_state=kw.get("requested_state", "running"),
        deadline_ms=kw.get("deadline_ms", DEADLINE),
        input=input,
        item=item,
        index=index,
        outer=outer or {},
        occurrences={r.path: r for r in records},
    )


REGIONS_INPUT = {"amount": "1.005", "items": [3, 1, 2], "flag": True}


def test_structured_regions_and_occurrence_identity():
    plan = program("regions")
    root = lambda *records: state(plan, "", records, input=REGIONS_INPUT)  # noqa: E731
    # Exact decimal: 1.005 + 0.000 at scale 2, half even.
    assert next_actions(plan, root(), NOW) == (RecordPure("total", "total", value="1.00"),)
    total = done("total", "1.00")
    assert next_actions(plan, root(total), NOW) == (
        RecordPure("choose#select", "choose", value={"branch": "then"}),
    )
    select = done("choose#select", {"branch": "then"})
    assert derive(plan, root(total, select), NOW).regions == ("choose:then",)
    branch = state(plan, "choose:then", (), input=REGIONS_INPUT)
    assert next_actions(plan, branch, NOW) == (
        RecordPure("choose:then/high", "high", value="high"),
    )
    branch = state(plan, "choose:then", (done("choose:then/high", "high"),), input=REGIONS_INPUT)
    # A single-region composite completes with its region's result.
    assert next_actions(plan, branch, NOW) == (RecordPure("choose", "choose", value="high"),)
    chosen = done("choose", "high")
    assert next_actions(plan, root(total, select, chosen), NOW) == (
        RecordPure("each#items", "each", value=[3, 1, 2]),
    )
    items = done("each#items", [3, 1, 2])
    # Declared concurrency 2: only the first incomplete items, in input order.
    assert derive(plan, root(total, select, chosen, items), NOW).regions == ("each:0", "each:1")
    item = state(plan, "each:1", (), input=REGIONS_INPUT, item=1, index=1)
    assert next_actions(plan, item, NOW) == (RecordPure("each:1/double", "double", value=2),)
    item = state(plan, "each:1", (done("each:1/double", 2),), input=REGIONS_INPUT, item=1, index=1)
    assert next_actions(plan, item, NOW) == (RecordPure("each:1", "each", value=2),)
    # Item 1 finished first; the window moves on without starting item 2 early.
    assert derive(plan, root(total, select, chosen, items, done("each:1", 2)), NOW).regions == (
        "each:0",
        "each:2",
    )
    finished = [done("each:2", 4), done("each:1", 2), done("each:0", 6)]
    iteration_result = next_actions(plan, root(total, select, chosen, items, *finished), NOW)
    input_order_expected = (RecordPure("each", "each", value=[6, 2, 4]),)
    assert iteration_result == input_order_expected
    each = done("each", [6, 2, 4])
    head = (total, select, chosen, items, each)
    assert derive(plan, root(*head), NOW).regions == ("both:left", "both:right")
    branches = (done("both:left", "L"), done("both:right", "R"))
    assert next_actions(plan, root(*head, *branches), NOW) == (
        RecordPure("both", "both", value={"left": "L", "right": "R"}),
    )
    both = done("both", {"left": "L", "right": "R"})
    # coalesce selects the fallback only because `note` is missing.
    assert next_actions(plan, root(*head, both), NOW) == (
        RecordPure("fallback", "fallback", value="none"),
    )
    complete = next_actions(plan, root(*head, both, done("fallback", "none")), NOW)
    assert complete == (
        Complete(
            {
                "total": "1.00",
                "choose": "high",
                "each": [6, 2, 4],
                "both": {"left": "L", "right": "R"},
                "note": "none",
            }
        ),
    )
    for path, kind in (
        ("choose:then/high", "transform"),
        ("each:4/double", "transform"),
        ("both:right/rval", "transform"),
        ("each#items", "iteration"),
        ("choose", "decision"),
    ):
        assert resolve_occurrence(plan, path).kind == kind
    for invalid in (
        "nope",
        "choose:maybe",
        "each:5",
        "each:x/double",
        "high",
        "choose:then/low",
        "each:01",
    ):
        with pytest.raises(ProgramError):
            resolve_occurrence(plan, invalid)


def test_parallel_failure_stops_undispatched_siblings():
    plan = program("regions")
    head = (
        done("total", "1.00"),
        done("choose#select", {"branch": "else"}),
        done("choose", "low"),
        done("each#items", []),
        done("each", []),
    )
    actions = next_actions(
        plan,
        state(
            plan,
            "",
            (*head, done("both:left", "L"), failed("both:right", "type_mismatch")),
            input=REGIONS_INPUT,
        ),
        NOW,
    )
    assert actions == (
        CancelUndispatched("both:"),
        RecordPure("both", "both", failure="type_mismatch"),
    )
    # An empty iteration completes at once with an empty, ordered result.
    empty = next_actions(plan, state(plan, "", head[:4], input=REGIONS_INPUT), NOW)
    assert empty == (RecordPure("each", "each", value=[]),)
    # A failed composite fails the root: undispatched work stops, then terminal.
    actions = next_actions(
        plan, state(plan, "", (*head, failed("both", "type_mismatch")), input=REGIONS_INPUT), NOW
    )
    assert actions == (CancelUndispatched(""), Fail("type_mismatch"))


def test_progression_is_deterministic_from_rows():
    plan = program("regions")
    rows = (
        done("total", "1.00"),
        done("choose#select", {"branch": "then"}),
        done("choose", "high"),
    )
    first = state(plan, "", rows, input=REGIONS_INPUT)
    second = state(plan, "", tuple(reversed(rows)), input=REGIONS_INPUT)
    actions_from_worker_a = next_actions(plan, first, NOW)
    actions_from_worker_b = next_actions(plan, second, NOW)
    assert actions_from_worker_a == actions_from_worker_b
    # Database time is input, never read: the same rows and time give the same result.
    assert next_actions(plan, first, NOW) == next_actions(plan, first, NOW)


def wait_rows(plan, *records):
    return state(plan, "", records, input=None)


def test_wait_deadline_does_not_restart():
    plan = program("waits", "waits.policy")
    opened = next_actions(plan, wait_rows(plan), NOW)
    assert opened == (OpenWait("pause", "pause", NOW + 60_000),)
    # The execution deadline caps a longer wait.
    short = state(plan, "", (), input=None, deadline_ms=NOW + 1_000)
    assert next_actions(plan, short, NOW) == (OpenWait("pause", "pause", NOW + 1_000),)
    original_deadline_ms = NOW + 60_000
    waiting = OccurrenceRecord("pause", (), WaitRecord("w-1", "open", original_deadline_ms))
    # Later derivations never reopen or move an existing wait; timers need the sweeper.
    for later in (NOW + 1, NOW + 59_999, NOW + 3_600_000):
        assert next_actions(plan, wait_rows(plan, waiting), later) == ()
    satisfied = OccurrenceRecord("pause", (), WaitRecord("w-1", "satisfied", original_deadline_ms))
    assert next_actions(plan, wait_rows(plan, satisfied), NOW) == (
        RecordPure("pause", "pause", value=None),
    )
    expired = OccurrenceRecord("callback", (), WaitRecord("w-2", "expired", NOW))
    assert next_actions(plan, wait_rows(plan, done("pause", None), expired), NOW) == (
        RecordPure("callback", "callback", failure="wait_expired"),
    )


def signal(
    id, wait_id="w-2", payload=7, signal_id=None, expires=NOW + 1000, digest=None, at=NOW - 10
):
    signal_id = signal_id or id
    return SignalRecord(
        id,
        signal_id,
        digest or signal_digest(EXECUTION, signal_id, wait_id, payload),
        at,
        expires,
        payload,
    )


def test_signal_consumption_requires_exact_wait_binding():
    plan = program("waits", "waits.policy")
    pause = done("pause", None)

    def callback(*signals, deadline=NOW + 5000):
        return OccurrenceRecord("callback", (), WaitRecord("w-2", "open", deadline), signals)

    def consume(*signals, deadline=NOW + 5000):
        return next_actions(
            plan, wait_rows(plan, pause, callback(*signals, deadline=deadline)), NOW
        )

    assert consume() == ()
    # Another wait's digest, a changed payload digest, a signal accepted after its
    # expiry or a payload outside the declared schema satisfies nothing.
    other_wait = signal("s1", digest=signal_digest(EXECUTION, "s1", "w-other", 7), at=NOW - 50)
    tampered = signal("s2", digest=signal_digest(EXECUTION, "s2", "w-2", 8), at=NOW - 40)
    late = signal("s3", expires=NOW - 40, at=NOW - 30)
    wrong_type = signal("s4", payload="seven", at=NOW - 20)
    valid = signal("s5", at=NOW - 10)
    later_valid = signal("s6", at=NOW - 5)
    actions = consume(other_wait, tampered, late, wrong_type, valid, later_valid)
    assert actions == (ConsumeSignal("callback", "w-2", "s5", ("s1", "s2", "s3", "s4", "s6")),)
    assert consume(other_wait) == (ConsumeSignal("callback", "w-2", None, ("s1",)),)
    # Timeliness is fixed at acceptance: a signal accepted in time is consumed even
    # when the worker runs after the wait's deadline; one accepted after it is not.
    assert consume(valid, deadline=NOW) == (ConsumeSignal("callback", "w-2", "s5", ()),)
    after = signal("s7", at=NOW + 5, expires=NOW + 1000)
    assert consume(after, deadline=NOW) == (ConsumeSignal("callback", "w-2", None, ("s7",)),)

    def expired_callback(*signals):
        record = OccurrenceRecord("callback", (), WaitRecord("w-2", "expired", NOW), signals)
        return next_actions(plan, wait_rows(plan, pause, record), NOW + 10)

    # The sweeper may expire the wait first; an in-time signal still wins.
    assert expired_callback(signal("s8", at=NOW - 1, expires=NOW)) == (
        ConsumeSignal("callback", "w-2", "s8", ()),
    )
    assert expired_callback(after) == (
        ConsumeSignal("callback", "w-2", None, ("s7",)),
        RecordPure("callback", "callback", failure="wait_expired"),
    )


def test_activity_attempts_retry_only_declared_codes_within_bounds():
    plan = program("activities", *[])
    plan = program_from(
        validate_executable_package(raw("activities")),
        dict(
            enumerate(
                json.loads(
                    (
                        Path(__file__).resolve().parents[3]
                        / "contracts/examples/executable-v2/catalog.json"
                    ).read_text()
                )["documents"][:2]
            )
        ),
    )
    rows = lambda *records: state(plan, "", records, input=None)  # noqa: E731
    assert next_actions(plan, rows(), NOW) == (
        CreateAttempt("read", "read", 1, {"target": None, "input": None}),
    )
    reserved = OccurrenceRecord("read", (AttemptRecord("a1", 1, "reserved"),))
    assert next_actions(plan, rows(reserved), NOW) == ()
    retryable = failed("read", "unavailable", at=NOW - 1_000)
    # Backoff 5 s from the recorded failure time: a wake-up, then a new ordinal.
    assert derive(plan, rows(retryable), NOW).wake_at_ms == NOW + 4_000
    assert next_actions(plan, rows(retryable), NOW + 4_000) == (
        CreateAttempt("read", "read", 2, {"target": None, "input": None}),
    )
    exhausted = failed("read", "unavailable", ordinal=3, at=NOW - 10_000)
    assert next_actions(plan, rows(exhausted), NOW) == (
        CancelUndispatched(""),
        Fail("retries_exhausted"),
    )
    undeclared = failed("read", "provider_failed")
    assert next_actions(plan, rows(undeclared), NOW) == (
        CancelUndispatched(""),
        Fail("provider_failed"),
    )
    uncertain = OccurrenceRecord("read", (AttemptRecord("a1", 1, "uncertain"),))
    assert next_actions(plan, rows(uncertain), NOW) == ()
    assert next_actions(plan, rows(done("read", None)), NOW) == (
        CreateAttempt("think", "think", 1, None),
    )


def test_subworkflow_pins_input_and_completes_with_child_result():
    plan = program("subworkflow", "subworkflow.child")
    assert next_actions(plan, state(plan, "", (), input=5), NOW) == (
        RecordPure("invoke#input", "invoke", value=5),
    )
    pinned = done("invoke#input", 5)
    assert derive(plan, state(plan, "", (pinned,), input=5), NOW).regions == ("invoke:call",)
    child = state(plan, "invoke:call", (), input=None)
    child = ProgressionState(**{**child.__dict__, "input": 5})
    assert next_actions(plan, child, NOW) == (RecordPure("invoke:call/triple", "triple", value=15),)
    child = ProgressionState(
        **{**child.__dict__, "occurrences": {"invoke:call/triple": done("invoke:call/triple", 15)}}
    )
    assert next_actions(plan, child, NOW) == (RecordPure("invoke", "invoke", value=15),)
    assert next_actions(plan, state(plan, "", (pinned, done("invoke", 15)), input=5), NOW) == (
        Complete(15),
    )
    assert resolve_occurrence(plan, "invoke:call/triple").package_sha256 != plan.root_sha256


def test_paused_and_overdue_executions():
    plan = program("regions")
    assert (
        next_actions(plan, state(plan, "", (), input=REGIONS_INPUT, requested_state="paused"), NOW)
        == ()
    )
    assert next_actions(plan, state(plan, "", (), input=REGIONS_INPUT, deadline_ms=NOW), NOW) == (
        CancelUndispatched(""),
        Fail("deadline_exceeded"),
    )


# ---------------------------------------------------------------- database layer

WORKER = WorkerIdentity(
    worker_id="interpreter", role="orchestrator", interpreter_ids=[INTERPRETER_V1]
)
CATALOG_DOCS = Path(__file__).resolve().parents[3] / "contracts/examples/executable-v2/catalog.json"


@pytest.fixture
def flows(admission):
    from lotuspond.execution.engine import PostgresExecutionEngine

    h = admission
    h.interpreter = InterpreterV1(
        artifacts=h.store,
        artifact_policy=h.policy,
        budgets=h.budgets,
        recovery=FixtureFence(),
        installation_id="test-installation",
        test_mode=True,
    )
    h.engine_v1 = PostgresExecutionEngine(
        h.interpreter,
        FixtureSweeper(),
        FixtureFence(),
        interpreter_ids=[INTERPRETER_V1],
        test_mode=True,
    )
    h.sessions = sessionmaker(h.engine, expire_on_commit=False)
    h.deployed = {}

    def deploy(name, *dependencies):
        if name in h.deployed:
            return h.deployed[name]
        documents = json.loads(CATALOG_DOCS.read_text())["documents"]
        with Session(h.engine) as db, db.begin():
            published, refs = [], []
            for dependency in dependencies:
                source = (
                    documents[int(dependency.split(":")[1])]
                    if dependency.startswith("catalog:")
                    else raw(dependency)
                )
                definition = source.get("definition", {})
                # A catalog document with no model or tool requirements has an empty
                # closure; anything else is published after what precedes it.
                standalone = "schema_version" not in source and not (
                    definition.get("model_ref") or definition.get("read_tools")
                )
                row = h.publish(
                    db,
                    source,
                    () if standalone else tuple(p.id for p in published),
                    request_key=f"{name}-{dependency}",
                )
                published.append(row)
            package = raw(name)
            for family in ("models", "connectors", "policies", "subworkflows", "agents"):
                refs.extend(d["ref"] for d in package[family])
            publication = h.publish(db, package, tuple(p.id for p in published), request_key=name)
            request = within(deployment_request(h, publication.id), package)
            request = request.model_copy(
                update={
                    "configuration": request.configuration.model_copy(
                        update={
                            "bindings": tuple(DependencyBinding(dependency=ref) for ref in refs)
                        }
                    )
                }
            )
            view = create_deployment(db, h.actor, request, name, dependencies=h.dependencies)
            set_deployment_status(
                db,
                h.actor,
                view.id,
                view.revision_id,
                "enabled",
                name + "-enable",
                dependencies=h.dependencies,
            )
        h.deployed[name] = view.revision_id
        return view.revision_id

    def start(name, value, *dependencies, key=None):
        revision = deploy(name, *dependencies)
        with Session(h.engine) as db, db.begin():
            return h.service.admit_execution(
                db,
                h.actor,
                AdmissionRequest(deployment_revision_id=revision, input=value),
                key or name,
            ).id

    def drive(rounds=200):
        reports = []
        for _ in range(rounds):
            report = h.engine_v1.progress(h.sessions, WORKER, limit=10)
            reports.append(report)
            if report.claimed == 0:
                break
        return reports

    h.deploy, h.start, h.drive = deploy, start, drive
    return h


def fresh_engine(h):
    """A new engine and interpreter, as a restarted worker process would build."""
    from lotuspond.execution.engine import PostgresExecutionEngine

    interpreter = InterpreterV1(
        artifacts=h.store,
        artifact_policy=h.policy,
        budgets=h.budgets,
        recovery=FixtureFence(),
        installation_id="test-installation",
        test_mode=True,
    )
    return PostgresExecutionEngine(
        interpreter,
        FixtureSweeper(),
        FixtureFence(),
        interpreter_ids=[INTERPRETER_V1],
        test_mode=True,
    )


def rows(db, model, execution_id):
    return db.scalar(
        select(func.count()).select_from(model).where(model.execution_id == execution_id)
    )


def result(h, execution_id):
    with Session(h.engine) as db:
        row = db.get(Execution, execution_id)
        if row.result_artifact_id is None:
            return row.state, None
        content = h.store._get_internal(
            db,
            h.scope,
            {
                "id": row.result_artifact_id,
                "version": row.result_artifact_version,
                "sha256": row.result_sha256,
            },
        )
        return row.state, json.loads(content)


def test_pure_regions_complete_with_events_and_released_admission(flows):
    h = flows
    with Session(h.engine) as db:
        pending_before = db.scalar(select(ExecutionCapacity.pending))
    execution_id = h.start("regions", {"amount": "1.005", "items": [3, 1, 2], "flag": False})
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionCapacity.pending)) == pending_before + 1
    h.drive()
    state, value = result(h, execution_id)
    assert state == "succeeded"
    assert value == {
        "total": "1.00",
        "choose": "low",
        "each": [6, 2, 4],
        "both": {"left": "L", "right": "R"},
        "note": "none",
    }
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        assert (execution.reason, execution.runnable_at_ms) == ("completed", None)
        # The admission slot of an execution that never ran an activity is released.
        assert db.scalar(select(ExecutionCapacity.pending)) == pending_before
        events = list(
            db.scalars(
                select(ExecutionEvent)
                .where(ExecutionEvent.execution_id == execution_id)
                .order_by(ExecutionEvent.sequence)
            )
        )
        # Gap-free orchestrator sequence; codes and artifact references only.
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        assert execution.next_event_sequence == len(events) + 1
        assert {e.source_id for e in events} == {"orchestrator"}
        assert events[0].code == "started" and events[-1].code == "succeeded"
        assert {e.code for e in events} <= {
            "started",
            "attempt_succeeded",
            "attempt_failed",
            "succeeded",
        }
        assert all(e.event_artifact_id and len(e.digest) == 64 for e in events)
        # One attempt per recorded occurrence: no duplicate or retried pure work.
        paths = list(
            db.scalars(
                select(ExecutionStepInstance.occurrence_path).where(
                    ExecutionStepInstance.execution_id == execution_id
                )
            )
        )
        assert len(paths) == len(set(paths))
        assert "choose:else/low" in paths and "choose:then/high" not in paths
        assert rows(db, ExecutionAttempt, execution_id) == len(paths)


def test_repeated_transition_is_noop(flows):
    h = flows
    execution_id = h.start("scoped", [1, 2, 3])
    h.drive()
    assert result(h, execution_id) == ("succeeded", [0, 12, 13])
    with Session(h.engine) as db, db.begin():
        before = {
            m.__name__: rows(db, m, execution_id)
            for m in (ExecutionStepInstance, ExecutionAttempt, ExecutionEvent)
        }
        execution = db.scalar(
            select(Execution).where(Execution.id == execution_id).with_for_update()
        )
        artifacts = db.scalar(select(func.count()).select_from(ExecutionArtifact))
        epoch = 1
        from lotuspond.execution.progression import RecordPure

        for action in (
            RecordPure("base", "base", value=10),
            RecordPure("each#items", "each", value=[1, 2, 3]),
            RecordPure("each:1/pick#select", "pick", value={"branch": "then"}),
        ):
            assert h.interpreter._apply(db, execution, epoch, action) is False
        after = {
            m.__name__: rows(db, m, execution_id)
            for m in (ExecutionStepInstance, ExecutionAttempt, ExecutionEvent)
        }
        rows_created_by_repeated_transition = sum(after.values()) - sum(before.values())
        assert rows_created_by_repeated_transition == 0
        assert db.scalar(select(func.count()).select_from(ExecutionArtifact)) == artifacts


def test_progression_is_deterministic_from_committed_rows(flows):
    h = flows
    execution_id = h.start(
        "regions", {"amount": "2.000", "items": [5, 6], "flag": True}, key="determinism"
    )
    # Advance part of the way, then let two independent sessions derive from the same rows.
    h.engine_v1.progress(h.sessions, WORKER, limit=1)
    derived = []
    for _ in range(2):
        with Session(h.engine) as db:
            execution = db.get(Execution, execution_id)
            from lotuspond.execution.program import load_program

            plan = load_program(db, h.scope, execution)
            state = h.interpreter.load_progression(db, execution, "", program=plan)
            derived.append(next_actions(plan, state, NOW))
    actions_from_worker_a, actions_from_worker_b = derived
    assert actions_from_worker_a == actions_from_worker_b


def test_worker_killed_between_transitions_resumes_without_duplicates(flows):
    h = flows
    execution_id = h.start(
        "regions", {"amount": "0.500", "items": [1], "flag": True}, key="restart"
    )
    # A transaction that dies before commit leaves nothing behind.
    with Session(h.engine) as db:
        db.begin()
        from lotuspond.execution.orchestrator import claim_runnable

        execution = claim_runnable(db, WORKER)
        h.interpreter.advance_execution(db, WORKER, execution, bound=64)
        assert rows(db, ExecutionAttempt, execution_id) > 0
        db.rollback()
    with Session(h.engine) as db:
        assert rows(db, ExecutionAttempt, execution_id) == 0
        assert db.get(Execution, execution_id).runnable_at_ms is not None
    # A fresh worker (new engine, new interpreter instance) finishes the same work once.
    restarted = fresh_engine(h)
    for _ in range(50):
        if restarted.progress(h.sessions, WORKER, limit=10).claimed == 0:
            break
    assert result(h, execution_id)[0] == "succeeded"
    with Session(h.engine) as db:
        paths = list(
            db.scalars(
                select(ExecutionStepInstance.occurrence_path).where(
                    ExecutionStepInstance.execution_id == execution_id
                )
            )
        )
        assert len(paths) == len(set(paths))


def test_empty_iteration_and_subworkflow_complete(flows):
    h = flows
    empty = h.start("regions", {"amount": "0.000", "items": [], "flag": True}, key="empty")
    child = h.start("subworkflow", 7, "subworkflow.child")
    h.drive()
    assert result(h, empty)[1]["each"] == []
    assert result(h, child) == ("succeeded", 21)
    with Session(h.engine) as db:
        paths = set(
            db.scalars(
                select(ExecutionStepInstance.occurrence_path).where(
                    ExecutionStepInstance.execution_id == child
                )
            )
        )
        assert paths == {"invoke#input", "invoke:call/triple", "invoke"}


def satisfy_timer(h, execution_id, path):
    """Stand-in for the Task 11 sweeper: satisfy a due timer and mark the execution."""
    from lotuspond.execution.orchestrator import mark_runnable
    from lotuspond.execution.progression import wait_key
    from lotuspond.fleet.authority import database_now_ms

    with Session(h.engine) as db, db.begin():
        execution = db.scalar(
            select(Execution).where(Execution.id == execution_id).with_for_update()
        )
        wait = db.scalar(
            select(ExecutionWait).where(ExecutionWait.wait_key == wait_key(path)).with_for_update()
        )
        wait.state = "satisfied"
        mark_runnable(db, execution, database_now_ms(db))


def test_waits_signals_and_forms_through_real_acceptance(flows):
    from lotuspond.execution.contracts import SignalRequest
    from lotuspond.execution.errors import ExecutionError
    from lotuspond.execution.inbox import InboxService
    from lotuspond.execution.progression import wait_key
    from lotuspond.models import ExecutionInbox, ExecutionRequest

    h = flows
    execution_id = h.start("waits", None, "waits.policy")
    h.drive()
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        timer = db.scalar(select(ExecutionWait).where(ExecutionWait.wait_key == wait_key("pause")))
        original_deadline_ms = timer.deadline_ms
        assert timer.state == "open" and original_deadline_ms <= execution.deadline_ms
        # The capacity step retained wait quota and moved the execution to waiting.
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExecutionRequest)
                .where(ExecutionRequest.request_key == "capacity.wait:" + timer.id)
            )
            == 1
        )
        assert (execution.state, execution.runnable_at_ms) == ("waiting", None)
    # A restarted worker woken for this execution re-derives from the committed
    # rows without reopening or moving the wait.
    from lotuspond.execution.orchestrator import mark_runnable
    from lotuspond.fleet.authority import database_now_ms

    with Session(h.engine) as db, db.begin():
        locked = db.scalar(select(Execution).where(Execution.id == execution_id).with_for_update())
        mark_runnable(db, locked, database_now_ms(db))
    restarted = fresh_engine(h)
    assert restarted.progress(h.sessions, WORKER, limit=5).claimed >= 1
    with Session(h.engine) as db:
        resumed_wait = db.scalar(
            select(ExecutionWait).where(ExecutionWait.wait_key == wait_key("pause"))
        )
        assert resumed_wait.deadline_ms == original_deadline_ms
        assert rows(db, ExecutionWait, execution_id) == 1
    satisfy_timer(h, execution_id, "pause")
    h.drive()
    inbox = InboxService(budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy)
    with Session(h.engine) as db:
        callback = db.scalar(
            select(ExecutionWait).where(ExecutionWait.wait_key == wait_key("callback"))
        )
        # The timer's quota was returned and the next wait retained in its place.
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 1
    for signal_id, payload in (("first", 7), ("second", 8)):
        with Session(h.engine) as db, db.begin():
            inbox.accept_signal(
                db,
                h.actor,
                execution_id,
                SignalRequest(signal_id=signal_id, wait_id=callback.id, payload=payload),
            )
    with Session(h.engine) as db, db.begin():
        # A duplicate equal delivery is the same inbox row, not a second signal.
        inbox.accept_signal(
            db,
            h.actor,
            execution_id,
            SignalRequest(signal_id="first", wait_id=callback.id, payload=7),
        )
    h.drive()
    with Session(h.engine) as db:
        statuses = dict(
            db.execute(
                select(ExecutionInbox.signal_id, ExecutionInbox.status).where(
                    ExecutionInbox.wait_id == callback.id
                )
            ).all()
        )
        assert statuses == {"first": "delivered", "second": "conflict"}
        assert db.get(ExecutionWait, callback.id).state == "satisfied"
        form = db.scalar(select(ExecutionWait).where(ExecutionWait.wait_key == wait_key("review")))
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError):
        # A signal for a satisfied wait is refused at acceptance.
        inbox.accept_signal(
            db,
            h.actor,
            execution_id,
            SignalRequest(signal_id="late", wait_id=callback.id, payload=9),
        )
    with Session(h.engine) as db, db.begin():
        inbox.accept_signal(
            db,
            h.actor,
            execution_id,
            SignalRequest(signal_id="form", wait_id=form.id, payload={"approved": True}),
        )
    h.drive()
    assert result(h, execution_id) == ("succeeded", {"signal": 7, "form": {"approved": True}})
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionCapacity.retained_waits)) == 0


def test_parallel_failure_cancels_undispatched_sibling_attempt(flows):
    h = flows
    execution_id = h.start("parallel_failure", 0, "catalog:1")
    h.drive()
    state, _ = result(h, execution_id)
    with Session(h.engine) as db:
        attempts = {
            path: status
            for path, status in db.execute(
                select(ExecutionStepInstance.occurrence_path, ExecutionAttempt.state)
                .join(
                    ExecutionAttempt, ExecutionAttempt.step_instance_id == ExecutionStepInstance.id
                )
                .where(ExecutionStepInstance.execution_id == execution_id)
            ).all()
        }
        execution = db.get(Execution, execution_id)
        assert attempts["both:slow/think"] == "cancelled"
        assert attempts["both:bad/divide"] == "failed"
        assert attempts["both"] == "failed"
        assert (state, execution.reason) == ("failed", "invalid_result")


def test_region_scoped_derivation_bound(flows, monkeypatch):
    """At 1,000 items and the largest admitted expansion, loads stay within one region."""
    from copy import deepcopy

    from lotuspond_sdk.executable import compile_package
    from lotuspond_sdk.executable.errors import ExecutableError

    # Each item region counts itself plus its eight steps: 1 + 1,000 x 9 = 9,001.
    # One more body step would exceed the SDK's 10,000-step cap, so this fixture is
    # the largest expansion admitted at 1,000 items.
    grown = deepcopy(raw("wide"))
    chain = grown["body"]["steps"][0]["body"]["steps"]
    chain.append({**deepcopy(chain[0]), "id": "extra"})
    with pytest.raises(ExecutableError) as refused:
        compile_package(grown, dependencies=[], catalog={"documents": []})
    assert refused.value.code == "expansion_exceeded"
    h = flows
    loads = []
    original = InterpreterV1._occurrences

    def counted(self, db, execution, paths, *, values_for):
        found = original(self, db, execution, paths, values_for=values_for)
        loads.append(
            {
                "paths": len(paths),
                "instances": len(found),
                "attempts": sum(len(r.attempts) for r in found.values()),
            }
        )
        return found

    monkeypatch.setattr(InterpreterV1, "_occurrences", counted)
    execution_id = h.start("wide", list(range(1000)), key="wide")
    report = h.engine_v1.progress(h.sessions, WORKER, limit=20)
    assert report.claimed >= 1 and report.transitions > 100
    with Session(h.engine) as db:
        instances = rows(db, ExecutionStepInstance, execution_id)
    per_transaction = max(load["instances"] + load["attempts"] for load in loads)
    # Root region: its iteration and pinned items, then exactly the pinned item
    # regions (never `max_items`); an item region: its eight body steps. No load
    # reads the whole expansion.
    assert all(load["instances"] <= load["paths"] <= 1000 for load in loads)
    assert max(load["paths"] for load in loads) == 1000  # the pinned 1,000 items
    assert all(load["instances"] <= 8 for load in loads if load["paths"] <= 8)
    assert per_transaction < instances
    print(
        {
            "derivation_bound": {
                "items": 1000,
                "transitions": report.transitions,
                "region_loads": len(loads),
                "max_rows_per_region_load": per_transaction,
                "instances_committed": instances,
            }
        }
    )


def test_inbox_resolves_a_wait_inside_a_parallel_branch(flows):
    from lotuspond.execution.contracts import SignalRequest
    from lotuspond.execution.inbox import InboxService
    from lotuspond.execution.progression import wait_key

    h = flows
    execution_id = h.start("nested_wait", None, "waits.policy")
    h.drive()
    inbox = InboxService(budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy)
    with Session(h.engine) as db:
        wait = db.scalar(
            select(ExecutionWait).where(ExecutionWait.wait_key == wait_key("both:a/hold"))
        )
        assert db.get(ExecutionStepInstance, wait.step_instance_id).occurrence_path == "both:a/hold"
    with Session(h.engine) as db, db.begin():
        receipt = inbox.accept_signal(
            db, h.actor, execution_id, SignalRequest(signal_id="nested", wait_id=wait.id, payload=4)
        )
    assert receipt.status == "pending"
    h.drive()
    assert result(h, execution_id) == ("succeeded", None)


def set_wait_limit(h, limit):
    with Session(h.engine) as db, db.begin():
        db.scalar(select(ExecutionCapacity).with_for_update()).wait_limit = limit


def retained(h):
    with Session(h.engine) as db:
        return db.scalar(select(ExecutionCapacity.retained_waits))


def accept(h, execution_id, path, signal_id, payload):
    from lotuspond.execution.contracts import SignalRequest
    from lotuspond.execution.inbox import InboxService
    from lotuspond.execution.progression import wait_key

    inbox = InboxService(budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy)
    with Session(h.engine) as db, db.begin():
        wait = db.scalar(
            select(ExecutionWait).where(
                ExecutionWait.execution_id == execution_id, ExecutionWait.wait_key == wait_key(path)
            )
        )
        inbox.accept_signal(
            db,
            h.actor,
            execution_id,
            SignalRequest(signal_id=signal_id, wait_id=wait.id, payload=payload),
        )


def close_wait(h, execution_id, path, state):
    """Stand-in for the Task 11 sweeper: close one execution's wait and mark it due."""
    from lotuspond.execution.orchestrator import mark_runnable
    from lotuspond.execution.progression import wait_key
    from lotuspond.fleet.authority import database_now_ms

    with Session(h.engine) as db, db.begin():
        execution = db.scalar(
            select(Execution).where(Execution.id == execution_id).with_for_update()
        )
        wait = db.scalar(
            select(ExecutionWait)
            .where(
                ExecutionWait.execution_id == execution_id, ExecutionWait.wait_key == wait_key(path)
            )
            .with_for_update()
        )
        wait.state = state
        mark_runnable(db, execution, database_now_ms(db))


def test_in_time_signal_satisfies_a_wait_the_sweeper_expired(flows):
    from lotuspond.execution.progression import wait_key
    from lotuspond.models import ExecutionInbox

    h = flows
    answered = h.start("waits", None, "waits.policy", key="answered")
    unanswered = h.start("waits", None, "waits.policy", key="unanswered")
    h.drive()
    for execution_id in (answered, unanswered):
        close_wait(h, execution_id, "pause", "satisfied")
    h.drive()
    accept(h, answered, "callback", "in-time", 7)
    # The sweeper expires both waits before any worker consumes the signal.
    for execution_id in (answered, unanswered):
        close_wait(h, execution_id, "callback", "expired")
    h.drive()
    with Session(h.engine) as db:

        def state(execution_id, path):
            return db.scalar(
                select(ExecutionWait.state).where(
                    ExecutionWait.execution_id == execution_id,
                    ExecutionWait.wait_key == wait_key(path),
                )
            )

        assert state(answered, "callback") == "satisfied"
        status = db.scalar(
            select(ExecutionInbox.status).where(ExecutionInbox.signal_id == "in-time")
        )
        assert status == "delivered"
        # The execution went on to its form wait instead of failing.
        assert state(answered, "review") == "open"
    # Positive control: without an in-time signal the expired wait fails the step.
    assert result(h, unanswered) == ("failed", None)


def test_wait_quota_releases_before_retaining_sequential_waits(flows):
    h = flows
    set_wait_limit(h, 1)
    execution_id = h.start("waits", None, "waits.policy")
    h.drive()
    assert retained(h) == 1
    satisfy_timer(h, execution_id, "pause")
    # One capacity step releases the timer's slot and then retains the next wait.
    h.drive()
    assert retained(h) == 1
    accept(h, execution_id, "callback", "one", 7)
    h.drive()
    assert retained(h) == 1
    accept(h, execution_id, "review", "form", {"approved": False})
    h.drive()
    assert result(h, execution_id) == ("succeeded", {"signal": 7, "form": {"approved": False}})
    assert retained(h) == 0


def test_retention_at_quota_is_deferred_without_blocking_releases(flows, caplog):
    import logging

    from lotuspond.execution.progression import wait_key
    from lotuspond.models import ExecutionRequest

    h = flows
    set_wait_limit(h, 2)
    first = h.start("waits", None, "waits.policy", key="first")
    second = h.start("waits", None, "waits.policy", key="second")
    h.drive()
    assert retained(h) == 2
    satisfy_timer(h, second, "pause")
    h.drive()  # releases `second`'s timer slot, then retains its callback: still 2
    assert retained(h) == 2
    third = h.start("waits", None, "waits.policy", key="third")
    with caplog.at_level(logging.WARNING, logger="lotuspond.execution"):
        h.drive()
    # At quota the third execution's wait is open but unretained; nothing escaped.
    assert "Execution wait retention deferred: capacity_unavailable" in caplog.text
    assert retained(h) == 2
    with Session(h.engine) as db:
        wait = db.scalar(
            select(ExecutionWait).where(
                ExecutionWait.execution_id == third, ExecutionWait.wait_key == wait_key("pause")
            )
        )
        assert wait.state == "open"
        assert not db.scalar(
            select(ExecutionRequest).where(
                ExecutionRequest.request_key == "capacity.wait:" + wait.id
            )
        )
        assert db.get(Execution, third).state not in ("succeeded", "failed")
        assert db.get(Execution, first).state == "waiting"


def test_parallel_failure_keeps_completed_dispatched_and_uncertain_siblings(flows):
    from lotuspond.execution.orchestrator import claim_runnable

    h = flows
    execution_id = h.start("parallel_mixed", 0, "catalog:1")
    with Session(h.engine) as db, db.begin():
        execution = claim_runnable(db, WORKER)
        # One bounded pass starts all five branches; the failure is not yet joined.
        outcome = h.interpreter.advance_execution(db, WORKER, execution, bound=5)
        assert outcome.transitions == 5
    with Session(h.engine) as db, db.begin():
        # Stand-ins for Task 9: one sibling was dispatched, one lost its lease.
        for path, state in (("both:flying/fly", "dispatched"), ("both:unsure/guess", "uncertain")):
            instance = db.scalar(
                select(ExecutionStepInstance).where(ExecutionStepInstance.occurrence_path == path)
            )
            db.scalar(
                select(ExecutionAttempt).where(ExecutionAttempt.step_instance_id == instance.id)
            ).state = state
    h.drive()
    with Session(h.engine) as db:
        states = dict(
            db.execute(
                select(ExecutionStepInstance.occurrence_path, ExecutionAttempt.state)
                .join(
                    ExecutionAttempt, ExecutionAttempt.step_instance_id == ExecutionStepInstance.id
                )
                .where(ExecutionStepInstance.execution_id == execution_id)
            ).all()
        )
        execution = db.get(Execution, execution_id)
    assert states["both:done/ready"] == "succeeded"
    assert states["both:flying/fly"] == "dispatched"  # kept: it is reported as it ends
    assert states["both:unsure/guess"] == "uncertain"  # kept: reconciliation decides
    assert states["both:queued/wait"] == "cancelled"  # undispatched: stopped
    assert states["both:bad/divide"] == "failed" and states["both"] == "failed"
    # The terminal state waits for containment of the in-flight siblings.
    assert execution.state not in ("succeeded", "failed")


def test_wait_opened_beside_running_compute_is_retained_once_it_stops(flows):
    from lotuspond.execution.orchestrator import mark_runnable
    from lotuspond.execution.progression import wait_key
    from lotuspond.fleet.authority import database_now_ms
    from lotuspond.models import ExecutionReconciliationObservation, ExecutionRequest

    from .test_budgets import demand

    h = flows
    execution_id = h.start("parallel_wait", 5, "catalog:1", "waits.policy")
    h.drive()
    with Session(h.engine) as db:
        hold = db.scalar(
            select(ExecutionWait).where(ExecutionWait.wait_key == wait_key("both:held/hold"))
        )
        think = db.scalar(
            select(ExecutionAttempt.id)
            .join(
                ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id
            )
            .where(ExecutionStepInstance.occurrence_path == "both:slow/think")
        )
        # Not retained while the sibling's attempt is in flight.
        assert hold.state == "open" and retained(h) == 0
    # Stand-in for Tasks 5 and 9: reserve, report a result and confirm the stop.
    with Session(h.engine) as db, db.begin():
        h.budgets.reserve_attempt(db, h.actor, think, demand(1))
    with Session(h.engine) as db, db.begin():
        execution = db.scalar(
            select(Execution).where(Execution.id == execution_id).with_for_update()
        )
        handle = h.store._put_internal(db, h.scope, b"null", h.policy)
        attempt = db.get(ExecutionAttempt, think)
        attempt.state = "succeeded"
        attempt.result_artifact_id = handle.id
        attempt.result_artifact_version = handle.version
        attempt.result_sha256 = handle.sha256
        observation = ExecutionReconciliationObservation(
            **h.scope.model_dump(),
            execution_id=execution_id,
            attempt_id=think,
            source_id="runtime",
            observation_id="stopped",
            digest="b" * 64,
            code="compute_stopped",
            observed_at_ms=database_now_ms(db),
        )
        db.add(observation)
        db.flush()
        h.budgets.confirm_stopped(db, think, observation.id)
        mark_runnable(db, execution, database_now_ms(db))
    h.drive()
    with Session(h.engine) as db:
        assert retained(h) == 1
        assert db.scalar(
            select(ExecutionRequest.id).where(
                ExecutionRequest.request_key == "capacity.wait:" + hold.id
            )
        )
        execution = db.get(Execution, execution_id)
        assert (execution.state, execution.runnable_at_ms) == ("waiting", None)


def test_wait_at_quota_never_throttles_sibling_progress(flows):
    h = flows
    set_wait_limit(h, 0)
    execution_id = h.start("parallel_busy", list(range(40)), "waits.policy")
    # One worker call: passes stopped by the 64-transition bound continue at once
    # even though the sibling wait cannot be retained.
    report = h.engine_v1.progress(h.sessions, WORKER, limit=20)
    assert report.transitions > 5 * 64
    with Session(h.engine) as db:
        each = db.scalar(
            select(ExecutionAttempt.state)
            .join(
                ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id
            )
            .where(
                ExecutionStepInstance.execution_id == execution_id,
                ExecutionStepInstance.occurrence_path == "both:busy/each",
            )
        )
        assert each == "succeeded"
        # The wait is still unretained, so the capacity step stays pending (backed off).
        execution = db.get(Execution, execution_id)
        assert h.interpreter._capacity_pending(db, execution)
        assert execution.runnable_at_ms is not None
    assert retained(h) == 0


def test_wait_at_quota_never_delays_a_sibling_retry(flows):
    from lotuspond.execution.orchestrator import mark_runnable
    from lotuspond.fleet.authority import database_now_ms

    h = flows
    set_wait_limit(h, 0)
    execution_id = h.start("parallel_retry", 5, "catalog:1", "waits.policy")
    h.drive()
    # Stand-in for Task 9: the agent's attempt failed with a retryable code.
    with Session(h.engine) as db, db.begin():
        execution = db.scalar(
            select(Execution).where(Execution.id == execution_id).with_for_update()
        )
        attempt = db.scalar(
            select(ExecutionAttempt)
            .join(
                ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id
            )
            .where(ExecutionStepInstance.occurrence_path == "both:slow/think")
        )
        failed_at = database_now_ms(db)
        handle = h.store._put_internal(
            db,
            h.scope,
            json.dumps(
                {"failed_at_ms": failed_at, "failure": "unavailable"},
                separators=(",", ":"),
                sort_keys=True,
            ).encode(),
            h.policy,
        )
        attempt.state = "failed"
        attempt.result_artifact_id = handle.id
        attempt.result_artifact_version = handle.version
        attempt.result_sha256 = handle.sha256
        mark_runnable(db, execution, database_now_ms(db))
    # The sibling wait has been stuck at quota for a while: a long capacity back-off.
    h.engine_v1._failures[execution_id] = 7
    h.engine_v1.progress(h.sessions, WORKER, limit=1)
    with Session(h.engine) as db:
        due = db.get(Execution, execution_id).runnable_at_ms
    # Due at the retry's back-off (2 s after the failure), not the capacity back-off.
    assert due is not None and due <= failed_at + 2000


def test_cancelling_a_reserved_attempt_returns_its_compute_slot(flows):
    from lotuspond.models import ExecutionSchedulerCursor, ExecutionUsageReservation

    from .test_budgets import demand

    h = flows
    execution_id = h.start("parallel_wait", 5, "catalog:1", "waits.policy")
    h.drive()
    with Session(h.engine) as db:
        think = db.scalar(
            select(ExecutionAttempt.id)
            .join(
                ExecutionStepInstance, ExecutionStepInstance.id == ExecutionAttempt.step_instance_id
            )
            .where(ExecutionStepInstance.occurrence_path == "both:slow/think")
        )
    with Session(h.engine) as db, db.begin():
        h.budgets.reserve_attempt(db, h.actor, think, demand(1))
    with Session(h.engine) as db:
        assert db.scalar(select(ExecutionCapacity.active)) == 1
    # The sibling wait expires, so the parallel fails and cancels the reserved attempt.
    close_wait(h, execution_id, "both:held/hold", "expired")
    h.drive()
    with Session(h.engine) as db:
        reservation = db.scalar(
            select(ExecutionUsageReservation).where(ExecutionUsageReservation.attempt_id == think)
        )
        assert db.get(ExecutionAttempt, think).state == "cancelled"
        assert reservation.compute_stopped_at_ms is not None
        assert db.scalar(select(ExecutionCapacity.active)) == 0
        assert db.scalar(select(ExecutionSchedulerCursor.active)) == 0
    assert result(h, execution_id) == ("failed", None)


def test_paused_execution_does_not_poll_for_wait_retention(flows):
    from lotuspond.execution.orchestrator import clear_runnable, mark_runnable
    from lotuspond.fleet.authority import database_now_ms

    h = flows
    set_wait_limit(h, 0)
    execution_id = h.start("waits", None, "waits.policy")
    h.drive()
    assert retained(h) == 0
    with Session(h.engine) as db, db.begin():
        # Stand-in for Task 11's pause control.
        execution = db.scalar(
            select(Execution).where(Execution.id == execution_id).with_for_update()
        )
        execution.requested_state = "paused"
        clear_runnable(db, execution)
        mark_runnable(db, execution, database_now_ms(db))
    calls = []
    original = h.interpreter.apply_capacity

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    h.interpreter.apply_capacity = counted
    h.drive()
    with Session(h.engine) as db:
        # No retention is tried while paused, so the execution is not kept due.
        assert db.get(Execution, execution_id).runnable_at_ms is None
    assert calls == [] and retained(h) == 0


def test_unsupported_nodes_and_policies_fail_with_admission_codes():
    package = raw("waits")
    package["body"]["steps"][1]["escalation_policy_ref"] = "admins"
    plan = program_from(
        validate_executable_package(package), dict(enumerate([raw("waits.policy")]))
    )
    rows_ = (OccurrenceRecord("pause", (AttemptRecord("a", 1, "succeeded", None),)),)
    assert next_actions(plan, state(plan, "", rows_, input=None), NOW) == (
        CancelUndispatched(""),
        Fail("unsupported_policy"),
    )
    documents = json.loads(CATALOG_DOCS.read_text())["documents"]
    connector = documents[0]
    package = raw("waits")
    package["connectors"] = [
        {
            "id": "source",
            "ref": {
                "kind": "connector",
                "name": connector["name"],
                "version": connector["version"],
                "sha256": _catalog_sha(connector),
            },
        }
    ]
    package["body"]["steps"] = [
        {
            "id": "review",
            "kind": "human_task",
            "mode": "review",
            "assignment_policy_ref": "admins",
            "timeout_seconds": 60,
            "intent": {"kind": "ref", "source": {"kind": "step", "id": "intent"}, "pointer": ""},
        },
        {
            "id": "intent",
            "kind": "action_intent",
            "connector_ref": "source",
            "operation": "write",
            "target": {"kind": "literal", "schema": {"kind": "null"}, "value": None},
            "input": {"kind": "literal", "schema": {"kind": "null"}, "value": None},
            "policy_ref": "admins",
            "approval": "none",
            "independent_review": False,
            "expiry_seconds": 60,
        },
    ]
    package["body"]["result"] = {"kind": "literal", "schema": {"kind": "null"}, "value": None}
    package["body"]["result_schema"] = {"kind": "data", "schema": {"kind": "null"}}
    package["output_schema"] = {"kind": "null"}
    plan = program_from(
        validate_executable_package(package), dict(enumerate([raw("waits.policy"), connector]))
    )
    assert next_actions(plan, state(plan, "", (), input=None), NOW) == (
        CancelUndispatched(""),
        Fail("unsupported_step"),
    )
    # An effect node alone (no review before it) is refused with the same code.
    package["body"]["steps"] = package["body"]["steps"][1:]
    plan = program_from(
        validate_executable_package(package), dict(enumerate([raw("waits.policy"), connector]))
    )
    assert next_actions(plan, state(plan, "", (), input=None), NOW) == (
        RecordPure("intent", "intent", failure="unsupported_step"),
    )


def _catalog_sha(document):
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    return catalog_document_digest(document)


def test_oversized_activity_input_fails_the_step(flows):
    from lotuspond.execution.orchestrator import claim_runnable
    from lotuspond.execution.progression import CreateAttempt as Create

    h = flows
    execution_id = h.start("parallel_failure", 5, "catalog:1", key="oversized")
    with Session(h.engine) as db, db.begin():
        execution = claim_runnable(db, WORKER)
        assert execution.id == execution_id
        big = Create("both:slow/think", "think", 1, {"value": "x" * 1_100_000})
        assert h.interpreter._apply(db, execution, 1, big) is True
        instance = db.scalar(
            select(ExecutionStepInstance).where(
                ExecutionStepInstance.occurrence_path == "both:slow/think"
            )
        )
        attempt = db.scalar(
            select(ExecutionAttempt).where(ExecutionAttempt.step_instance_id == instance.id)
        )
        assert attempt.state == "failed"
        record = json.loads(
            h.store._get_internal(
                db,
                h.scope,
                {"id": attempt.result_artifact_id, "version": 1, "sha256": attempt.result_sha256},
            )
        )
        assert record["failure"] == "value_too_large"
        db.rollback()
