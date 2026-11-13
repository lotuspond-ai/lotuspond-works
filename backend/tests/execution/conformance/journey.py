"""Task 13 installed-artifact journey; copied outside the checkout by execution-smoke.

Runs with installed `lotuspond-api`, `lotuspond-runtime` and `lotuspond-sdk` only,
against an owned schema on the given test database. Writes `result.json` and
`cleanup.json` into the private case directory. Test-only; never a bootstrap.
"""

from __future__ import annotations

import importlib.metadata as metadata
import json
import socket
import ssl
import sys
import time
import traceback
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent))
import driver as drivers  # noqa: E402
import world as worlds  # noqa: E402
from processes import Processes, wait_for  # noqa: E402

VERSIONS = {"lotuspond-api": "0.4.0", "lotuspond-runtime": "0.3.0", "lotuspond-sdk": "0.3.0"}
POINTS = ("admission", "signal", "claim", "result", "transition", "projection")
# E05: the reviewed write's commits, in the API (decision, dispatch, claim, receipt) and
# in the orchestrator (intent, read-back, outcome).
ACTION_POINTS = ("intent", "decision", "dispatch", "action_claim", "receipt", "readback", "outcome")
ORCHESTRATOR_POINTS = ("intent", "readback", "outcome")


class Journey:
    def __init__(self, world, processes):
        self.world, self.processes = world, processes
        self.scenarios = []
        self.counters = []  # every runtime's read and model call counters
        self.target_writes = []  # writes per intent, from the synthetic target's log
        self.orchestrators = ["orchestrator-a"]

    def keep_orchestrator(self):
        """A dead orchestrator is restarted, as its service manager would."""
        alive = [
            name
            for name, process in self.processes.children.items()
            if name.startswith("orchestrator") and process.poll() is None
        ]
        if not alive:
            name = f"orchestrator-r{len(self.orchestrators)}"
            self.orchestrators.append(name)
            self.processes.start_worker("orchestrator", name)

    def runtime(self, operator, customer, name, **scripts):
        """A runtime for one scenario, counted for the whole journey."""
        runtime = operator.runtime(customer, name, **scripts)
        self.counters += [runtime["counter"], runtime["model_counter"]]
        return runtime

    def stop(self, *runtimes):
        """End a scenario's runtimes, so a later scenario's work goes only to its own."""
        for runtime in runtimes:
            name = runtime["home"].name
            if self.processes.children[name].poll() is None:
                self.processes.stop(name)

    def scenario(self, name, check):
        started = time.monotonic()
        try:
            passed, detail = check()
        except Exception as error:  # noqa: BLE001 - a fixed summary only, no values
            passed, detail = False, type(error).__name__ + ": " + _safe(error)
        self.scenarios.append(
            {
                "name": name,
                "passed": bool(passed),
                "detail": detail,
                "seconds": round(time.monotonic() - started, 2),
            }
        )


def _safe(error):
    text = str(error)
    return text[:160] if "PRIVATE_" not in text else "redacted"


def installed_origins():
    origins = {}
    for name, version in VERSIONS.items():
        distribution = metadata.distribution(name)
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        location = Path(distribution.locate_file("")).resolve()
        origins[name] = {
            "version": distribution.version,
            "location": str(location),
            "editable": bool(direct.get("dir_info", {}).get("editable", False)),
            "in_environment": location.is_relative_to(Path(sys.prefix).resolve()),
        }
    passed = all(
        item["version"] == VERSIONS[name] and not item["editable"] and item["in_environment"]
        for name, item in origins.items()
    )
    return passed, origins


def tls_authentication(journey):
    """The API speaks only TLS under the synthetic CA; other trust and plain HTTP fail."""
    port, ca = journey.processes.api_port, journey.processes.ca
    trusted = ssl.create_default_context(cafile=str(ca))
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with trusted.wrap_socket(raw, server_hostname="localhost") as stream:
            verified = stream.version() is not None and stream.getpeercert() is not None
    refused_other_trust = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname="localhost"):
                pass
    except ssl.SSLError:
        refused_other_trust = True
    plain_refused = False
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        raw.sendall(b"GET /api/v1/health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        raw.settimeout(5)
        try:
            answer = raw.recv(64)
        except (TimeoutError, ConnectionError):
            answer = b""
        # Plain HTTP gets no HTTP answer at all: a closed connection or a TLS alert.
        plain_refused = answer == b"" or answer[:1] == b"\x15"
    passed = verified and refused_other_trust and plain_refused
    return passed, {
        "verified": verified,
        "other_trust": refused_other_trust,
        "plain": plain_refused,
    }


def reference_workflow(journey, operator, customer):
    """The synthetic reference agent: one tool read, two model rounds, each once."""
    operator.demand(50000)  # the root and its three children within the agent's 240 s
    package, model = drivers.patient_agent()
    revision = operator.deploy(
        customer, "agent_tools_240", package=package, dependencies=[operator.documents[0], model]
    )
    rounds = [
        {
            "tool_calls": [
                {"name": "connector-fixture-read", "arguments": {"target": None, "input": None}}
            ]
        },
        {"output": None},
    ]
    runtimes = [
        journey.runtime(
            operator, customer, f"reference-{n}", model_document=model, model={"rounds": rounds}
        )
        for n in range(2)
    ]
    try:
        execution_id = operator.start(customer, revision)["id"]
        view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 120)
    finally:
        journey.stop(*runtimes)
    reads = sum(Processes.calls(r["counter"]) for r in runtimes)
    models = sum(Processes.calls(r["model_counter"]) for r in runtimes)
    attempts = _attempts(journey.world, execution_id)
    # Claimed attempts: the agent's root and its three children (two rounds, one read).
    passed = view["state"] == "succeeded" and (models, reads) == (2, 1) and attempts == 4
    return passed, {
        "state": view["state"],
        "model_calls": models,
        "read_calls": reads,
        "attempts": attempts,
    }


def _attempts(world, execution_id):
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from lotuspond.models import ExecutionAttempt, ExecutionLease

    with Session(world.engine) as db:
        # Attempts that a runtime claimed: each may invoke its provider once.
        return db.scalar(
            select(func.count(func.distinct(ExecutionLease.attempt_id)))
            .join(ExecutionAttempt, ExecutionAttempt.id == ExecutionLease.attempt_id)
            .where(ExecutionAttempt.execution_id == execution_id)
        )


def privacy(journey, operator, customer):
    """Unique sentinels stay inside their authorized encrypted artifacts (positive controls).

    As tier 1's privacy case: a provider error, the execution's input and a signal, an
    agent's prompt, a knowledge source's content and source ID, and the runtime's and
    the customer's credentials.
    """
    from lotuspond_runtime.state import CredentialStore
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from lotuspond.models import Execution

    kinds = ("INPUT", "SIGNAL", "PROVIDER", "PROMPT", "DOCUMENT", "SOURCE")
    secrets = {kind: f"PRIVATE_{kind}_{uuid4().hex}" for kind in kinds}
    read = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    runtime = journey.runtime(
        operator, customer, "privacy", read={"read": {"raise": secrets["PROVIDER"]}}
    )
    try:
        failing = operator.start(customer, read)["id"]
        operator.settled(customer, failing, {"failed", "succeeded", "reconciliation_required"}, 90)
    finally:
        journey.stop(runtime)
    raised = Processes.calls(runtime["counter"]) >= 1
    document = {"fact": secrets["DOCUMENT"]}
    source_id, version = operator.context_source(customer, document, {"source": secrets["SOURCE"]})
    package, knowledge = drivers.private_agent(secrets["PROMPT"], document)
    agent = operator.deploy(
        customer,
        "private-agent",
        package=package,
        dependencies=[operator.documents[0], operator.model_document(), knowledge],
        knowledge={"facts": (source_id, version)},
    )
    operator.demand(14000)
    rounds = [{"require_seen": [secrets["PROMPT"], secrets["DOCUMENT"]], "output": None}]
    thinker = journey.runtime(operator, customer, "privacy-agent", model={"rounds": rounds})
    try:
        thinking = operator.start(customer, agent)["id"]
        states = {"succeeded", "failed", "reconciliation_required"}
        thought = operator.settled(customer, thinking, states, 120)["state"] == "succeeded"
    finally:
        journey.stop(thinker)
    store = CredentialStore(thinker["home"] / "state")
    try:
        secrets["RUNTIME_CREDENTIAL"] = store.load().active_token.get_secret_value()
    finally:
        store.close()
    secrets["CUSTOMER_COOKIE"] = customer.headers["cookie"].split("=", 1)[1]
    revision = operator.deploy(
        customer,
        "string-signal",
        package=drivers.string_signal_package(),
        dependencies=["waits.policy"],
    )
    execution_id = operator.start(customer, revision, value=secrets["INPUT"])["id"]
    wait = operator.open_wait(customer, execution_id, "signal0")
    operator.signal(customer, execution_id, wait["id"], "s", secrets["SIGNAL"])
    done = operator.settled(customer, execution_id, {"succeeded", "failed"})["state"] == "succeeded"
    graph = journey.world.graph()
    with Session(journey.world.engine) as db:
        row = db.get(Execution, execution_id)
        scope = drivers._scope_of(row)

        def opened(prefix):
            handle = {
                "id": getattr(row, prefix + "_artifact_id"),
                "version": getattr(row, prefix + "_artifact_version"),
                "sha256": getattr(row, prefix + "_sha256"),
            }
            return graph.store._get_internal(db, scope, handle).decode()

        stored_input, stored_result = opened("input"), opened("result")
        schema = journey.world.config["schema"]
        tables = db.scalars(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
            {"s": schema},
        ).all()
        surfaces = [
            (table, item[0])
            for table in tables
            for item in db.execute(text(f'SELECT t::text FROM "{schema}"."{table}" t'))
        ]
    for execution in (execution_id, failing, thinking):
        surfaces.append(("api", json.dumps(operator.execution(customer, execution))))
        surfaces.append(("api", json.dumps(operator.events(customer, execution))))
        surfaces += [
            ("event", json.dumps(content))
            for _, content in drivers.decoded_events(journey.world, execution)
        ]
    logs = (journey.world.root / "logs").glob("*")
    surfaces += [(log.name, log.read_text(errors="replace")) for log in logs]
    published = {"executable_publications", "executable_dependencies"}
    found = sorted(
        {
            kind
            for kind, value in secrets.items()
            for where, surface in surfaces
            if (value in surface or value.encode().hex() in surface)
            and not (kind == "PROMPT" and where in published)
        }
    )
    controls = (
        raised
        and thought
        and done
        and secrets["INPUT"] in stored_input
        and secrets["SIGNAL"] in stored_result
    )
    journey.sentinels_found = found
    return controls and not found, {"controls": controls, "found": found}


def arm(world, point, when):
    world.write("faults", {"point": point, "when": when, "remaining": 1, "fired": 0})


def fired(world):
    return world.state("faults").get("fired", 0)


def rollback(journey, operator, customer, point, when):
    """One commit of `point` fails before it, or is lost after it; the work happens once."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from lotuspond.execution.admission import request_identity
    from lotuspond.models import ExecutionEvent, ExecutionInbox, ExecutionRequest

    world = journey.world
    if point == "admission":
        revision = operator.deploy(
            customer, "form", package=drivers.form_package(), dependencies=["waits.policy"]
        )
        arm(world, point, when)
        key = "rollback-" + when
        first = _attempt(lambda: operator.start(customer, revision, key=key))
        second = operator.start(customer, revision, key=key)
        with Session(world.engine) as db:
            count = db.scalar(
                select(func.count())
                .select_from(ExecutionRequest)
                .where(ExecutionRequest.request_key == request_identity(key))
            )
        once = first is None or first["id"] == second["id"]
        return fired(world) == 1 and once and count == 1, {"fired": fired(world), "rows": count}
    if point == "signal":
        revision = operator.deploy(
            customer, "form", package=drivers.form_package(), dependencies=["waits.policy"]
        )
        execution_id = operator.start(customer, revision)["id"]
        wait = operator.open_wait(customer, execution_id, "review")
        arm(world, point, when)
        _attempt(
            lambda: operator.signal(customer, execution_id, wait["id"], "form", {"approved": True})
        )
        operator.signal(customer, execution_id, wait["id"], "form", {"approved": True})
        state = operator.settled(customer, execution_id, {"succeeded", "failed"})["state"]
        with Session(world.engine) as db:
            accepted = db.scalar(
                select(func.count())
                .select_from(ExecutionInbox)
                .where(ExecutionInbox.execution_id == execution_id)
            )
        return fired(world) == 1 and state == "succeeded" and accepted == 1, {
            "state": state,
            "accepted": accepted,
        }
    if point in ("claim", "result", "transition"):
        # The read must complete after the injected failure. The fixture read's 2 s step
        # deadline is also its lease, which a retried claim or report can outlast.
        revision = operator.deploy(
            customer,
            "activity_read_30",
            package=drivers.patient_read(),
            dependencies=[operator.documents[0]],
        )
        arm(world, point, when)
        runtime = journey.runtime(operator, customer, f"rollback-{point}-{when}")
        try:
            execution_id = operator.start(customer, revision)["id"]
            if point == "transition" and when == "after":
                # The orchestrator died right after a transition's commit: its service
                # manager restarts it.
                orchestrator = journey.processes.children["orchestrator-a"]
                wait_for(lambda: orchestrator.poll() is not None, 60)
                journey.processes.start_worker("orchestrator", "orchestrator-restarted")
            state = operator.settled(customer, execution_id, {"succeeded", "failed"}, 120)
            state = state["state"]
        finally:
            journey.stop(runtime)
        calls = Processes.calls(runtime["counter"])
        with Session(world.engine) as db:
            sequences = list(
                db.scalars(
                    select(ExecutionEvent.sequence)
                    .where(ExecutionEvent.execution_id == execution_id)
                    .order_by(ExecutionEvent.sequence)
                )
            )
        gap_free = sequences == list(range(1, len(sequences) + 1))
        passed = fired(world) == 1 and state == "succeeded" and calls == 1 and gap_free
        return passed, {"state": state, "calls": calls, "gap_free": gap_free}
    if point == "projection":
        return projection_rollback(journey, operator, customer, when)
    raise ValueError(point)


def _attempt(call):
    try:
        return call()
    except drivers.Refused:
        return None


def projection_rollback(journey, operator, customer, when):
    """A runtime event's projection commit fails before, or its answer is lost after."""
    from hashlib import sha256

    from lotuspond_sdk.executable.wire import canonical_event_payload_json
    from sqlalchemy import event as listen
    from sqlalchemy import func, select
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from lotuspond.fleet.authority import database_now_ms
    from lotuspond.models import ExecutionEvent

    world = journey.world
    revision = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    hand = operator.hand(customer, f"projection-{when}")
    try:
        execution_id = operator.start(customer, revision)["id"]
        # A pending delivery: live work. At most one poll a second (60 a minute allowed).
        wait_for(lambda: hand.activity.poll(), 60, interval=1.0)
        identity = hand.identity
        with Session(world.engine) as db:
            at = database_now_ms(db)
        payload = {"reason_code": "owner_requested"}
        report = {
            "record_type": "runtime_event",
            "format_version": 1,
            "event_id": "projection-1",
            "installation_id": worlds.INSTALLATION,
            "recovery_epoch": world.state("recovery")["epoch"],
            **customer.scope,
            "execution_id": execution_id,
            "step_id": None,
            "action_id": None,
            "attempt": None,
            "source_id": identity.instance_id,
            "source_generation": identity.generation,
            "source_sequence": 1,
            "event_kind": "cancellation_requested",
            "observed_at_ms": at,
            "payload": payload,
            "payload_sha256": sha256(
                canonical_event_payload_json("cancellation_requested", payload)
            ).hexdigest(),
        }
        graph = world.graph()
        fault = {"armed": True}

        def before(session):
            if when == "before" and fault["armed"]:
                fault["armed"] = False
                raise OperationalError("injected rollback before commit", None, Exception())

        def apply():
            with Session(world.engine) as db:
                listen.listen(db, "before_commit", before)
                with db.begin():
                    graph.events.apply_event(db, identity.instance_id, report)

        try:
            apply()
            lost = when == "after"  # committed; its answer is treated as lost
        except OperationalError:
            lost = True
        if lost:
            apply()  # the runtime reports it again, byte for byte
        with Session(world.engine) as db:
            rows = db.scalar(
                select(func.count())
                .select_from(ExecutionEvent)
                .where(
                    ExecutionEvent.execution_id == execution_id,
                    ExecutionEvent.family == "sdk_runtime_event",
                )
            )
        return rows == 1 and not fault["armed"] if when == "before" else rows == 1, {"rows": rows}
    finally:
        hand.close()


EXPECTED = (
    "noneditable_installed_api_runtime_sdk",
    "tls_authentication",
    "reference_workflow_result_and_calls",
    "decoded_events_and_errors_exclude_payloads",
    *(f"rollback_{point}_{when}_commit" for point in POINTS for when in ("before", "after")),
    "reviewed_write_result_and_counts",
    *(
        f"rollback_action_{point}_{when}_commit"
        for point in ACTION_POINTS
        for when in ("before", "after")
    ),
)


# ------------------------------------------------------------------ E05 reviewed writes


class Reviewed:
    """One environment's reviewed writes through the public routes and the target."""

    def __init__(self, journey, operator, customer, target):
        self.journey, self.operator, self.customer, self.target = (
            journey,
            operator,
            customer,
            target,
        )
        self.world = journey.world

    def deploy(self, record, max_attempts=1):
        return self.operator.deploy(
            self.customer,
            f"reviewed-{record}",
            package=drivers.reviewed_action(record, max_attempts=max_attempts),
            dependencies=[self.operator.connector, drivers.action_document("reviewers")],
        )

    def writes(self, record):
        log = self.target["directory"] / "writes.jsonl"
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [r for r in rows if r["target"]["record_id"] == record]

    def fault(self, *faults):
        worlds.write_private(self.target["directory"] / "faults.json", list(faults))

    def commands(self, execution_id):
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        from lotuspond.models import ExecutionActionCommand

        with Session(self.world.engine) as db:
            return db.scalar(
                select(func.count())
                .select_from(ExecutionActionCommand)
                .where(ExecutionActionCommand.execution_id == execution_id)
            )

    def task(self, execution_id, person, seconds=60):
        def found():
            self.journey.keep_orchestrator()  # one that died after a commit restarts
            items = person.call("GET", "/human-tasks")["items"]
            return next((t for t in items if t["execution_id"] == execution_id), None)

        return wait_for(found, seconds)

    def approve(self, execution_id, person, *, retry=False):
        """Claim and `allow`; `retry` repeats a refused or lost call with its own ID."""
        task = self.task(execution_id, person)
        path = f"/human-tasks/{task['id']}"
        body = {
            "decision_id": f"d-{uuid4().hex[:8]}",
            "intent_sha256": task["intent_sha256"],
            "decision": "allow",
        }
        person.call("POST", path + "/claim", {})
        first = _attempt(lambda: person.call("POST", path + "/decision", body))
        if first is None or retry:
            person.call("POST", path + "/decision", body)

    def reviewer(self):
        """The journey's one reviewer (registered once: the API rate-limits sign-ups),
        a member of the owner's workspace, acting in this scenario's environment."""
        person = self.journey.reviewer
        return drivers.Customer(
            self.operator.base_url,
            self.journey.processes.ca,
            self.world.config["origin"],
            person.headers["cookie"],
            self.customer.workspace,
            self.customer.prefix,
        )

    def gap_free(self, execution_id):
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from lotuspond.models import ExecutionEvent

        with Session(self.world.engine) as db:
            sequences = list(
                db.scalars(
                    select(ExecutionEvent.sequence)
                    .where(ExecutionEvent.execution_id == execution_id)
                    .order_by(ExecutionEvent.sequence)
                )
            )
        return sequences == list(range(1, len(sequences) + 1))


def reviewed_write(journey, reviewed):
    """The reviewed-write journey: one task, one decision, one command, one write and
    one verification read, each measured."""
    record = "R-journey"
    revision = reviewed.deploy(record)
    runtime = journey.runtime(reviewed.operator, reviewed.customer, "reviewed-write")
    try:
        execution_id = reviewed.operator.start(reviewed.customer, revision)["id"]
        person = reviewed.reviewer()
        try:
            reviewed.approve(execution_id, person)
        finally:
            person.close()
        view = reviewed.operator.settled(
            reviewed.customer, execution_id, {"succeeded", "failed"}, 180
        )
    finally:
        journey.stop(runtime)
    writes, commands = len(reviewed.writes(record)), reviewed.commands(execution_id)
    reads = Processes.calls(runtime["counter"])
    journey.target_writes.append(writes)
    passed = view["state"] == "succeeded" and (writes, commands, reads) == (1, 1, 1)
    return passed, {"state": view["state"], "writes": writes, "commands": commands, "reads": reads}


def action_rollback(journey, reviewed, point, when):
    """One commit of a reviewed write fails before it, or is lost after it (an API's
    answer dropped, or the orchestrator dying at once); the write still happens once."""
    world = journey.world
    record = f"R-{point}-{when}".replace("_", "-")
    revision = reviewed.deploy(record)
    runtime = journey.runtime(reviewed.operator, reviewed.customer, f"action-{point}-{when}")
    person = reviewed.reviewer()
    try:
        if point == "readback":
            reviewed.fault("lose_response")  # so a read-back runs and records
        arm(world, point, when)
        execution_id = reviewed.operator.start(reviewed.customer, revision)["id"]
        reviewed.approve(execution_id, person, retry=point == "decision")

        def settled():
            if point in ORCHESTRATOR_POINTS and when == "after":
                journey.keep_orchestrator()
            view = reviewed.operator.execution(reviewed.customer, execution_id)
            return view if view["state"] in ("succeeded", "failed") else None

        view = wait_for(settled, 240, 0.5)
    finally:
        person.close()
        journey.stop(runtime)
    writes, commands = len(reviewed.writes(record)), reviewed.commands(execution_id)
    journey.target_writes.append(writes)
    gap_free = reviewed.gap_free(execution_id)
    passed = (
        fired(world) == 1
        and view["state"] == "succeeded"
        and writes == 1
        and commands == 1
        and gap_free
    )
    return passed, {
        "fired": fired(world),
        "state": view["state"],
        "writes": writes,
        "commands": commands,
        "gap_free": gap_free,
    }


def _terminate(signum, frame):
    import signal

    for other in (signal.SIGTERM, signal.SIGINT):  # a second signal never cuts cleanup
        signal.signal(other, signal.SIG_IGN)
    raise SystemExit(128 + signum)  # unwinds: processes end and the schema is dropped


def main():
    import signal

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, _terminate)
    raw = Path(sys.argv[1]).read_text().strip()
    case = Path(sys.argv[2])
    result = {"scenarios": [], "required_integration_skips": len(EXPECTED), "outcome": "failed"}
    cleanup = {"confirmed": False}
    try:
        passed, origins = installed_origins()
        result["installed_origins"] = origins
        with worlds.owned(raw, case / "world") as world:
            # E05: action authorities before any process starts (interpreter v2 then
            # serves every execution of this world, v1 ones by v1's rules).
            connector = drivers.decisive_connector()
            pin = worlds.actions_mode(world, connector)
            processes = Processes(world, python=sys.executable, cwd=case)
            journey = Journey(world, processes)
            journey.sentinels_found = None
            journey.scenario("noneditable_installed_api_runtime_sdk", lambda: (passed, "checked"))
            try:
                processes.start_api()
                processes.start_worker("orchestrator", "orchestrator-a")
                processes.start_worker("sweeper", "sweeper-a")
                operator = drivers.Operator(world, processes)

                owner = operator.tenant(world.engine)

                def isolated(check, label):
                    """Each scenario has its own environment, as each tier 1 case has
                    its own world: an earlier scenario's uncertain liability never holds
                    a later one's capacity."""

                    def run():
                        customer = operator.environment(world.engine, owner, label=label)
                        try:
                            return check(customer)
                        finally:
                            customer.close()

                    return run

                journey.scenario("tls_authentication", lambda: tls_authentication(journey))
                journey.scenario(
                    "reference_workflow_result_and_calls",
                    isolated(lambda c: reference_workflow(journey, operator, c), "Reference"),
                )
                journey.scenario(
                    "decoded_events_and_errors_exclude_payloads",
                    isolated(lambda c: privacy(journey, operator, c), "Privacy"),
                )
                for point in POINTS:
                    for when in ("before", "after"):
                        journey.scenario(
                            f"rollback_{point}_{when}_commit",
                            isolated(
                                lambda c, point=point, when=when: rollback(
                                    journey, operator, c, point, when
                                ),
                                f"Rollback {point} {when}",
                            ),
                        )
                        if world.path("faults.json").exists():
                            world.path("faults.json").unlink()
                        if processes.children["orchestrator-a"].poll() is not None and (
                            "orchestrator-restarted" not in processes.children
                        ):
                            processes.start_worker("orchestrator", "orchestrator-restarted")
                target = processes.start_target()
                operator.demand(60000)  # a write's lease ends with its reservation
                actions = drivers.Operator(
                    world,
                    processes,
                    connector=connector,
                    destinations=[
                        {"host": "localhost", "port": target["port"], "path_prefix": "/target"}
                    ],
                    actions={"pin": pin},
                )

                journey.reviewer = actions.member(owner, "admin")

                def reviewed(check, label):
                    def run():
                        customer = actions.environment(world.engine, owner, label=label)
                        try:
                            return check(Reviewed(journey, actions, customer, target))
                        finally:
                            customer.close()

                    return run

                journey.scenario(
                    "reviewed_write_result_and_counts",
                    reviewed(lambda r: reviewed_write(journey, r), "Reviewed"),
                )
                for point in ACTION_POINTS:
                    for when in ("before", "after"):
                        journey.scenario(
                            f"rollback_action_{point}_{when}_commit",
                            reviewed(
                                lambda r, point=point, when=when: action_rollback(
                                    journey, r, point, when
                                ),
                                f"Action {point} {when}",
                            ),
                        )
                        if world.path("faults.json").exists():
                            world.path("faults.json").unlink()
                        journey.keep_orchestrator()
                journey.reviewer.close()
                owner.close()
            finally:
                receipt = processes.close()
                cleanup["processes"] = receipt
            result["scenarios"] = journey.scenarios
            ran = {item["name"] for item in journey.scenarios}
            result["required_integration_skips"] = sum(name not in ran for name in EXPECTED)
            result["private_sentinels_found"] = journey.sentinels_found
            # Measured at the target, never from receipts: at most one write per intent.
            result["max_target_writes_per_intent"] = (
                max(journey.target_writes) if journey.target_writes else None
            )
            # Observed per attempt, over every runtime the journey started.
            per_attempt = Processes.calls_by_attempt(*journey.counters)
            result["provider_calls"] = sum(per_attempt.values())
            # An unattributed call cannot be shown to be its attempt's only one.
            result["max_provider_calls_per_attempt"] = (
                max(per_attempt.values()) if per_attempt and "unknown" not in per_attempt else None
            )
        cleanup["schema_dropped"] = True  # `owned` asserts the drop before returning
        cleanup["confirmed"] = cleanup["processes"]["confirmed"] and cleanup["schema_dropped"]
        result["cleanup_confirmed"] = cleanup["confirmed"]
        result["outcome"] = (
            "passed"
            if all(item["passed"] for item in result["scenarios"]) and cleanup["confirmed"]
            else "failed"
        )
    except BaseException:  # noqa: BLE001 - the case directory keeps a value-free trace
        (case / "journey-error.txt").write_text(
            "".join(traceback.format_exc().splitlines(keepends=True)[-12:])
        )
    finally:
        worlds.write_private(case / "result.json", result)
        worlds.write_private(case / "cleanup.json", cleanup)
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
