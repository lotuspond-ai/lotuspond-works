"""Invoice exception triage's full package on real processes (milestones 4 to 6).

The first reference workflow, end to end: its full package (both reviewed writes, not
the prefix) runs on the API, orchestrator, sweeper and one enrolled runtime. The server's
authorities are an operator directory provisioned with `python -m lotuspond.authority`
only, as production's are: release decisions required, the `ap-standin` connector and its
secret binding installed, and a command key. The runtime is in test mode only to reach
the loopback stand-in AP system, which a production runtime's egress policy refuses. Its
`https-json` connector reads and writes the stand-in, and `anthropic-messages` calls the
local Messages API double. Nothing leaves the machine.

The package is proven on an evaluation deployment and released; the release admits it.
Then every scenario runs on the released version: a clean match needs no one, a justified
price variance ends at the clerk's form, each hold is allowed by one admin, and the
credit-note request by a second, independent admin. Each write is read back and verified,
and a hold whose answer is lost after it commits is settled by read-back, never resent.
"""

import importlib.util
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

import driver as drivers
import pytest
from console import CONSOLE_JOURNEY, SKIP_REASON, journey
from driver import catalog_ref
from test_reference_triage import (
    AP_TOKEN,
    MODEL_KEY,
    SCENARIOS,
    certificate,
    doubles,
    gen,
    mounted_secrets,
    result_of,
    standin,
)

# The review, decision and release helpers the late purchase order cases use. Loaded by
# path: `tests/test_reference_late_po.py` (the unit tests) shares the module name.
_late_po = importlib.util.spec_from_file_location(
    "conformance_reference_late_po", Path(__file__).with_name("test_reference_late_po.py")
)
late_po = importlib.util.module_from_spec(_late_po)
_late_po.loader.exec_module(late_po)
decide, facts, logs = late_po.decide, late_po.facts, late_po.logs
review_of, workspace = late_po.review_of, late_po.workspace

WRITES = ("set_payment_hold", "request_credit_note")
DEPENDENCIES = [gen.connector, gen.model, gen.clerks, gen.hold_policy, gen.credit_policy]
# The write each exception status makes.
OPERATIONS = {"held": "set_payment_hold", "credit_requested": "request_credit_note"}
RUNTIME = "runtime-triage"


def pin_of(document):
    return {
        "name": document["name"],
        "version": document["version"],
        "artifact_sha256": document["definition"]["artifact_sha256"],
    }


@pytest.fixture
def triage(world, processes, linux):
    w = world
    drivers.worlds.operator_mode(w, release_required=True)
    command_trust_set = drivers.worlds.operator_actions(w, [gen.connector], [("ap_token", 1)])[
        "trust"
    ]
    processes.start_api()
    processes.start_worker("orchestrator", "orchestrator-a", wait=False)
    processes.start_worker("sweeper", "sweeper-a", wait=False)
    processes.wait_ready("orchestrator-a", "sweeper-a")
    w.processes = processes
    cert, key = certificate(drivers.worlds.private_directory(w.root / "tls"))
    server, ledger = standin.serve(0, cert, key, AP_TOKEN, host="127.0.0.1")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    double = doubles.ModelDouble(cert, key, token=MODEL_KEY).start()
    destination = {"host": "localhost", "port": server.server_address[1], "path_prefix": "/"}
    operator = drivers.Operator(
        w,
        w.processes,
        connections=[
            {
                "connector": gen.connector,
                "destinations": [destination],
                "secret_binding": "ap_token",
                "writes": WRITES,
            }
        ],
    )
    # Each attempt's reserved active time, as for late purchase orders: the default 20 s
    # can let a model round's lease lapse on a loaded host.
    drivers.worlds.operator_demand(w, 40000)
    customer = operator.tenant(w.engine, label="Invoice triage")
    model_pin = {"reference_id": str(uuid4()), "version": 1}
    # The token under its activity binding and under the connection's secret binding,
    # which an action's command names.
    root, mapping = mounted_secrets(
        w.root, {"ap-token": AP_TOKEN, "ap_token": AP_TOKEN, "model-key": MODEL_KEY}
    )
    command_trust = w.root / "command-trust.json"
    drivers.worlds.write_private(command_trust, command_trust_set)
    settings = {
        "routes": gen.ROUTES,
        "ca_file": str(cert),
        "egress": {"allowed_private_cidrs": ["127.0.0.0/8", "::1/128"]},
        "test_loopback": True,
    }
    reads = [op["id"] for op in gen.connector["definition"]["operations"] if op["effect"] == "read"]

    def activity(trust, journal):
        ap_pin = customer.connection["by_connector"][gen.connector["name"]]["secrets"][0]
        return {
            "trust_file": str(trust),
            "journal_directory": str(journal),
            "secrets": {
                "root": str(root),
                "mapping_file": str(mapping),
                "bindings": [
                    {**ap_pin, "binding": "ap-token"},
                    {**model_pin, "binding": "model-key"},
                ],
            },
            "providers": [
                {
                    "provider_id": "https-json",
                    "dependency": catalog_ref(gen.connector),
                    "operations": reads,
                    "connector": pin_of(gen.connector),
                    "settings": settings,
                },
                {
                    "provider_id": "anthropic-messages",
                    "dependency": catalog_ref(gen.model),
                    "credentials": [model_pin],
                    "settings": {
                        "model_document": gen.model,
                        "configuration": gen.MODEL_CONFIGURATION,
                        "base_url": f"https://localhost:{double.port}",
                        "ca_file": str(cert),
                    },
                },
            ],
            # The https-json write side for both of the package's writes.
            "actions": {
                "trust_file": str(command_trust),
                "providers": [
                    {
                        "provider_id": "https-json",
                        "connector": pin_of(gen.connector),
                        "operations": {operation: "target_key" for operation in WRITES},
                        "settings": settings,
                    }
                ],
            },
        }

    runtime = operator.runtime(
        customer,
        RUNTIME,
        model_document=gen.model,
        activity=activity,
        writes=[pin_of(gen.connector)],
    )
    yield w, operator, customer, ledger, double, runtime
    customer.close()
    double.stop()
    server.shutdown()
    server.server_close()


def expectation(scenario):
    """What a package evaluation observes: a matched invoice's result, the clerk's form
    a justified variance asks for, or the exact write an exception proposes."""
    expected = scenario["expected"]
    if expected["status"] == "matched":
        return {"state": "succeeded", "result": expected}
    if expected["resolution"] == "no_action":
        return {"state": "form"}
    operation = OPERATIONS[expected["status"]]
    return {
        "state": "review",
        "proposal": {
            "operation": operation,
            "target": {"invoice_id": scenario["input"]["invoice_id"]},
            "input": {"amount": expected["amount"]},
        },
    }


def prove_and_release(operator, customer, ledger, double):
    """All six scenarios on an evaluation deployment (nothing written), then a release of
    that run under a policy with a second administrator's approval."""
    double.by_key = {
        s["input"]["invoice_id"]: s["model_rounds"] for s in SCENARIOS if "model_rounds" in s
    }
    candidate = operator.deploy(
        customer,
        "triage-evaluation",
        package=gen.full,
        dependencies=DEPENDENCIES,
        purpose="evaluation",
    )
    project = customer.scope["project_id"]
    scenario_ids = [
        workspace(
            customer,
            "POST",
            "/scenarios",
            {
                "project_id": project,
                "name": scenario["id"],
                "input": scenario["input"],
                "expected": expectation(scenario),
                "critical": True,
            },
        )["id"]
        for scenario in SCENARIOS
    ]
    run = customer.call(
        "POST",
        "/package-evaluations",
        {
            "deployment_revision_id": candidate,
            "name": "Triage candidate",
            "scenario_ids": scenario_ids,
        },
    )
    deadline = time.monotonic() + 300
    while run["status"] != "completed" and time.monotonic() < deadline:
        run = customer.call("POST", f"/package-evaluations/{run['id']}/advance", {})
        if run["status"] != "completed":
            time.sleep(0.5)
    assert run["status"] == "completed", run
    failed = [r for r in run["results"] if r["passed"] is not True]
    assert failed == [], failed
    assert not [r for r in ledger.requests if r[0] == "POST"]  # proving wrote nothing
    policy = workspace(customer, "POST", "/policies", {"name": "Invoice triage release"})
    release = workspace(
        customer,
        "POST",
        "/releases",
        {
            "project_id": project,
            "name": "Invoice triage 0.1.0",
            "revision": run["revision"],
            "candidate_run_id": run["id"],
            "policy_id": policy["id"],
        },
    )
    approval = workspace(
        customer,
        "POST",
        "/approvals",
        {"release_id": release["id"], "action": "release", "reason": "Evaluated on stand-ins"},
    )
    second = operator.member(customer, "admin")
    workspace(second, "POST", f"/approvals/{approval['id']}/decide", {"status": "approved"})
    decision = workspace(customer, "POST", f"/releases/{release['id']}/evaluate", {})
    assert decision["verdict"] == "pass", decision
    double.by_key = {}
    return release["id"], candidate


def test_the_full_package_is_proven_released_and_writes_after_review(triage):
    """Milestones 4 to 6 of the first reference workflow, on production's authorities."""
    w, operator, customer, ledger, double, runtime = triage
    with pytest.raises(drivers.Refused) as refused:
        operator.deploy(customer, "triage-unreleased", package=gen.full, dependencies=DEPENDENCIES)
    assert (refused.value.status, refused.value.code) == (503, "assurance_unavailable")
    release_id, candidate = prove_and_release(operator, customer, ledger, double)
    with pytest.raises(drivers.Refused) as refused:
        operator.start(customer, candidate, SCENARIOS[0]["input"])
    assert refused.value.code == "deployment_unavailable"
    revision = operator.deploy(
        customer,
        "invoice-triage",
        package=gen.full,
        dependencies=DEPENDENCIES,
        release_id=release_id,
    )
    second = operator.member(customer, "admin")
    names = ("api", RUNTIME)
    lost = False
    for scenario in SCENARIOS:
        double.rounds = scenario.get("model_rounds", [{"output": None}])
        expected = scenario["expected"]
        invoice = scenario["input"]["invoice_id"]
        posts_before = len([r for r in ledger.requests if r[0] == "POST"])
        if expected["status"] == "held" and not lost:
            ledger.fail_after_commit, lost = 1, True  # the hold commits, its answer is lost
        execution_id = operator.start(customer, revision, scenario["input"])["id"]
        writes = 0
        if expected["status"] == "clerk_decided":
            # Under interpreter v2 the clerk's form is a human task: a second admin (the
            # clerks policy is independent) claims it and submits the decision.
            form = review_of(w, operator, customer, execution_id)
            assert form["kind"] == "form", form
            second.call("POST", f"/human-tasks/{form['id']}/claim", {})
            second.call(
                "POST",
                f"/human-tasks/{form['id']}/submission",
                {"submission_id": f"s-{uuid4().hex[:8]}", "payload": scenario["clerk_form"]},
            )
        elif expected["status"] != "matched":
            task = review_of(w, operator, customer, execution_id)
            # The credit-note request reaches the supplier: a second admin allows it. A
            # hold is internal and reversible: the requester may.
            person = second if expected["status"] == "credit_requested" else customer
            proposal = decide(person, task)
            assert proposal["operation"] == OPERATIONS[expected["status"]]
            assert proposal["independent_approval"] is (person is second)
            assert proposal["target"] == {"invoice_id": invoice}
            writes = 1
        view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 240)
        if view["state"] != "succeeded":
            found = w.root / f"facts-{scenario['id']}.json"
            found.write_text(
                json.dumps(
                    {"facts": facts(w, execution_id), "logs": logs(w, names)},
                    default=str,
                    indent=1,
                )
            )
            raise AssertionError((scenario["id"], view["state"], view["reason"], str(found)))
        result = result_of(w, execution_id)
        for key, value in expected.items():
            assert result[key] == value, (scenario["id"], key, result)
        posts = [r for r in ledger.requests if r[0] == "POST"][posts_before:]
        assert len(posts) == writes, (scenario["id"], posts)  # applied once, never resent
        assert all(authorized for _, _, authorized in posts)
        written = {"held": ledger.holds, "credit_requested": ledger.credit_requests}
        if expected["status"] in written:
            assert written[expected["status"]][invoice]["amount"] == expected["amount"]
        trace = customer.call("GET", f"/executions/{execution_id}/trace")
        assert len(trace["actions"]) == writes, trace
        if writes:
            assert trace["outcome"] == {"state": "verified", "exception": False}, trace
            action = trace["actions"][0]
            assert action["review"]["decision"]["decision"] == "allow"
            assert action["verification"]["state"] == "verified", action
        assert invoice not in str(trace)  # digests and identifiers only, never values
    assert lost and ledger.fail_after_commit == 0
    assert set(ledger.holds) == {
        s["input"]["invoice_id"] for s in SCENARIOS if s["expected"]["status"] == "held"
    }
    assert set(ledger.credit_requests) == {
        s["input"]["invoice_id"] for s in SCENARIOS if s["expected"]["status"] == "credit_requested"
    }


@pytest.mark.skipif(not CONSOLE_JOURNEY, reason=SKIP_REASON)
def test_admins_decide_invoice_exceptions_in_the_console(triage):
    """Milestone 6, seen: three invoices of the released package decided in the built
    console by two admins with their own sessions (console_journey.mjs).

    - A short receipt's payment hold: the requester allows it.
    - An overcharge's credit-note request needs independent approval: the requester's
      Allow is refused and released, and a second admin allows it on a tablet.
    - A justified price variance: the second admin completes the clerk's form on a phone.

    Control Room shows each execution and its verified write, and the server agrees."""
    w, operator, customer, ledger, double, runtime = triage
    release_id, _ = prove_and_release(operator, customer, ledger, double)
    revision = operator.deploy(
        customer,
        "invoice-triage",
        package=gen.full,
        dependencies=DEPENDENCIES,
        release_id=release_id,
    )
    chosen = {s["id"]: s for s in SCENARIOS}
    hold, credit, form = (chosen[n] for n in ("short-receipt", "overcharge", "justified-price"))
    double.by_key = {s["input"]["invoice_id"]: s["model_rounds"] for s in (hold, credit, form)}
    started = {
        s["id"]: operator.start(customer, revision, s["input"])["id"] for s in (hold, credit, form)
    }
    second = operator.member(customer, "admin")
    runs = [
        {
            "name": "hold",
            "execution_id": started["short-receipt"],
            "writes": 1,
            "tasks": [{"who": "requester", "kind": "review", "focus": True}],
        },
        {
            "name": "credit",
            "execution_id": started["overcharge"],
            "writes": 1,
            "tasks": [
                {
                    "who": "second",
                    "width": "tablet",
                    "kind": "review",
                    "refused_first": "requester",
                }
            ],
        },
        {
            "name": "clerk",
            "execution_id": started["justified-price"],
            "writes": 0,
            "tasks": [
                {"who": "second", "width": "phone", "kind": "form", "payload": form["clerk_form"]}
            ],
        },
    ]
    try:
        seen = journey(
            w,
            workspace="Invoice triage",  # the organization workspace and its one project
            project="Invoice triage",
            people={"requester": customer, "second": second},
            runs=runs,
        )
    except AssertionError as failed:
        raise AssertionError(
            (failed, {name: facts(w, e) for name, e in started.items()}, logs(w, ("api", RUNTIME)))
        ) from None
    assert seen["errors"] == [], seen["errors"]
    [allowed] = seen["runs"]["hold"]["tasks"]
    assert (allowed["operation"], allowed["allowed"]) == ("set_payment_hold", True)
    [independent] = seen["runs"]["credit"]["tasks"]
    assert independent["refused_operation"] == independent["operation"] == "request_credit_note"
    assert independent["independent"] is True and independent["allowed"]
    assert "independent approval" in independent["refusal"], independent
    [clerk] = seen["runs"]["clerk"]["tasks"]
    assert (clerk["kind"], clerk["submitted"]) == ("form", True)
    # The form says what it asks before anyone claims it (its declared schema).
    assert clerk["asks"] == (
        "Decision Text, up to 32 characters Comment Text, up to 1,000 characters"
    ), clerk
    for name, writes in (("hold", 1), ("credit", 1), ("clerk", 0)):
        shown = seen["runs"][name]["control_room"]
        assert "succeeded" in shown["state"].lower(), (name, shown)
        assert len(shown["writes"]) == writes, (name, shown)
        assert all("outcome: verified" in t.lower() for t in shown["writes"]), (name, shown)
    clerk_run = seen["runs"]["clerk"]
    assert clerk_run["overflow_tablet"] <= 0 and clerk_run["overflow_phone"] <= 0, clerk_run
    assert seen["focus"][0]["label"] == "Claim", seen["focus"]

    # The server agrees: each write applied once and allowed by the person who did.
    for scenario in (hold, credit, form):
        execution_id = started[scenario["id"]]
        view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 240)
        assert view["state"] == "succeeded", (scenario["id"], facts(w, execution_id))
        result = result_of(w, execution_id)
        for key, value in scenario["expected"].items():
            assert result[key] == value, (scenario["id"], key, result)
    assert ledger.holds[hold["input"]["invoice_id"]]["amount"] == hold["expected"]["amount"]
    assert set(ledger.credit_requests) == {credit["input"]["invoice_id"]}
    assert len([r for r in ledger.requests if r[0] == "POST"]) == 2
    held = customer.call("GET", f"/executions/{started['short-receipt']}/trace")
    credited = customer.call("GET", f"/executions/{started['overcharge']}/trace")
    deciders = [
        trace["actions"][0]["review"]["decision"]["decided_by"]["user_id"]
        for trace in (held, credited)
    ]
    assert deciders[1] == second.user_id != deciders[0]
