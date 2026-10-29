"""Task 13 conformance: payloads stay inside their authorized encrypted artifacts.

Unique private sentinels go into each class the plan names, by its real path:

- a prompt: the agent's instructions, which the model must see;
- documents: a knowledge source's content (the fixture connector's reads return
  null by schema, so a read result cannot carry one);
- credentials: the runtime's credential token and the customer's session cookie;
- source IDs: the external source reference in the knowledge source's citation;
- provider errors: text a provider raises;
- plus the execution's input and a signal.

Each is proven to have arrived where it is authorized (a positive control) before
every other surface is searched: projections, decoded events, every row of every
table, and every process log. Connection secrets resolved through E03's mounted
secret files are qualified by E03's suite; the test-mode worker mounts none.
"""

import json
from uuid import uuid4

from driver import (
    Refused,
    _scope_of,
    decoded_events,
    diagnose,
    private_agent,
    string_signal_package,
)
from processes import Processes
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from lotuspond.models import Execution, ExecutionAttempt, ExecutionInbox

# The package is customer-authored configuration: its instructions are stored, as
# published, in the publication rows and nowhere else.
PACKAGE_TABLES = {"executable_publications", "executable_dependencies"}


def sentinel(kind):
    return f"PRIVATE_{kind}_{uuid4().hex}"


def every_row(world):
    """Every row of every table in the world's schema, as PostgreSQL renders it."""
    schema = world.config["schema"]
    with Session(world.engine) as db:
        tables = db.scalars(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
            {"s": schema},
        ).all()
        return {
            table: [
                row[0]
                for row in db.execute(text(f'SELECT t::text FROM "{schema}"."{table}" t')).all()
            ]
            for table in tables
        }


def credentials(world, runtime_name, customer):
    """The real credentials this case used: the runtime's token and the session cookie."""
    from lotuspond_runtime.state import CredentialStore

    store = CredentialStore(world.root / "runtimes" / runtime_name / "state")
    try:
        state = store.load()
    finally:
        store.close()
    return {
        "RUNTIME_CREDENTIAL": state.active_token.get_secret_value(),
        "CUSTOMER_COOKIE": customer.headers["cookie"].split("=", 1)[1],
    }


def test_decoded_events_and_errors_exclude_payloads(system, linux):
    w = system
    operator, customer, processes = w.operator, w.customer, w.processes
    kinds = ("INPUT", "SIGNAL", "REFUSED", "PROVIDER", "PROMPT", "DOCUMENT", "SOURCE")
    secrets = {kind: sentinel(kind) for kind in kinds}
    # Provider error: its text never leaves the runtime's child process.
    read = operator.deploy(customer, "activity_read", dependencies=[operator.documents[0]])
    raising = operator.runtime(customer, "runtime-a", read={"read": {"raise": secrets["PROVIDER"]}})
    failing = operator.start(customer, read)["id"]
    # A provider that raised mid-call has unknown usage: reconciliation, never zero.
    states = {"failed", "succeeded", "reconciliation_required"}
    assert operator.settled(customer, failing, states, 90)["state"] == "reconciliation_required"
    assert Processes.calls(raising["counter"]) >= 1  # positive control: it really raised
    processes.stop("runtime-a")
    # Prompt, documents and a source ID: an agent with pinned knowledge and a read tool.
    document = {"fact": secrets["DOCUMENT"]}
    source_id, version = operator.context_source(customer, document, {"source": secrets["SOURCE"]})
    package, knowledge = private_agent(secrets["PROMPT"], document)
    agent = operator.deploy(
        customer,
        "private-agent",
        package=package,
        dependencies=[operator.documents[0], operator.model_document(), knowledge],
        knowledge={"facts": (source_id, version)},
    )
    # The model refuses (provider_failed) unless each private value reached it: one
    # round, the root and its model child within the agent's 30 s.
    operator.demand(14000)
    rounds = [{"require_seen": [secrets["PROMPT"], secrets["DOCUMENT"]], "output": None}]
    runtime = operator.runtime(customer, "runtime-b", model={"rounds": rounds})
    thinking = operator.start(customer, agent)["id"]
    states = {"succeeded", "failed", "reconciliation_required"}
    view = operator.settled(customer, thinking, states, 120)
    assert view["state"] == "succeeded", diagnose(w, thinking)  # positive control: it arrived
    assert Processes.calls(runtime["model_counter"]) == 1
    # Input and a signal: stored, then readable only through their own artifacts.
    revision = operator.deploy(
        customer, "string-signal", package=string_signal_package(), dependencies=["waits.policy"]
    )
    execution_id = operator.start(customer, revision, value=secrets["INPUT"])["id"]
    wait = operator.open_wait(customer, execution_id, "signal0")
    try:  # a refused request's body is never echoed in its error
        operator.signal(customer, execution_id, wait["id"], "bad", {"x": secrets["REFUSED"]})
    except Refused as refused:
        assert secrets["REFUSED"] not in str(refused)
    response = customer.client.post(
        customer.prefix + f"/executions/{execution_id}/signals",
        json={"signal_id": "bad", "wait_id": wait["id"], "payload": {"x": secrets["REFUSED"]}},
        headers={**customer.headers, "idempotency-key": "x"},
    )
    assert response.status_code >= 400 and secrets["REFUSED"] not in response.text
    operator.signal(customer, execution_id, wait["id"], "good", secrets["SIGNAL"])
    assert operator.settled(customer, execution_id, {"succeeded", "failed"})["state"] == "succeeded"
    processes.stop("runtime-b")  # its private state is opened only once it has ended
    secrets.update(credentials(w, "runtime-b", customer))
    graph = w.graph()
    with Session(w.engine) as db:
        execution = db.get(Execution, execution_id)
        inbox = db.scalar(select(ExecutionInbox).where(ExecutionInbox.execution_id == execution_id))
        scope = _scope_of(execution)

        def opened(prefix):
            return graph.store._get_internal(
                db,
                scope,
                {
                    "id": getattr(execution, prefix + "_artifact_id"),
                    "version": getattr(execution, prefix + "_artifact_version"),
                    "sha256": getattr(execution, prefix + "_sha256"),
                },
            ).decode()

        # Positive controls: each sentinel is in its own authorized encrypted artifact.
        assert secrets["INPUT"] in opened("input")
        assert secrets["SIGNAL"] in opened("result")
        assert inbox is not None
    # The citation, read through its own route under current access.
    citation = operator.last_context["citation"]
    response = customer.client.get(
        customer.prefix + f"/artifacts/{citation['id']}/versions/{citation['version']}",
        headers=customer.headers,
    )
    assert response.status_code == 200 and secrets["SOURCE"] in response.text
    # Nowhere else: projections, decoded events, every row, every log line.
    surfaces = []
    for item in (execution_id, failing, thinking):
        surfaces += [
            ("api", json.dumps(operator.execution(customer, item))),
            ("api", json.dumps(operator.events(customer, item))),
            ("api", json.dumps(operator.waits(customer, item))),
        ]
        surfaces += [("event", json.dumps(content)) for _, content in decoded_events(w, item)]
    for table, rows in every_row(w).items():
        surfaces += [(table, row) for row in rows]
    for log in (w.root / "logs").glob("*"):
        surfaces.append((log.name, log.read_text(errors="replace")))
    found = {
        (kind, where)
        for kind, value in secrets.items()
        for where, surface in surfaces
        if (value in surface or value.encode().hex() in surface)
        and not (kind == "PROMPT" and where in PACKAGE_TABLES)
    }
    assert found == set(), sorted(found)
    # The prompt is where the customer put it: in the published package, as published.
    published = [surface for where, surface in surfaces if where in PACKAGE_TABLES]
    prompt = (secrets["PROMPT"], secrets["PROMPT"].encode().hex())  # bytea renders as hex
    assert any(form in surface for surface in published for form in prompt)
    with Session(w.engine) as db:
        assert (
            db.scalar(
                select(ExecutionAttempt.state).where(ExecutionAttempt.execution_id == failing)
            )
            == "uncertain"
        )
