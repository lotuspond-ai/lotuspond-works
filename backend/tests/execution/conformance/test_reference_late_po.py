"""Late purchase order recovery on real processes, three connections and real providers.

The second reference workflow runs on the API, orchestrator, sweeper and one enrolled
runtime in an E05 action world (test mode: production composition still wires
interpreter v1 only). Each system is its own connection with its own secret: the
runtime's `https-json` connector reads the stand-in ERP, carrier and inbox
(`reference/late-po/standin/server.py`), each with its own bearer token from a mounted
secret, and its `anthropic-messages` provider calls the local Messages API double,
scripted from each scenario. Nothing leaves the machine.

Writes are not dispatched here: the `https-json` connector has no write side yet
(milestone 3). A late order instead stops at the planner's review of the exact date
change, which is checked and denied, so the execution fails `approval_denied` and the
stand-in records no write.
"""

import importlib.util
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

import driver as drivers
import pytest
from driver import catalog_ref, diagnose
from test_reference_triage import certificate, doubles, mounted_secrets, result_of

REFERENCE = Path(__file__).resolve().parents[4] / "reference" / "late-po"
TOKENS = {"erp": "synthetic-erp-token", "carrier": "synthetic-carrier-token"}
TOKENS["inbox"] = "synthetic-inbox-token"
MODEL_KEY = "synthetic-model-key"
WRITES = {"erp": ("set_promised_date",), "carrier": (), "inbox": ("send_customer_update",)}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load("late_po_generate", REFERENCE / "generate.py")
standin = _load("late_po_standin", REFERENCE / "standin" / "server.py")
SCENARIOS = json.loads((REFERENCE / "scenarios.json").read_text())["scenarios"]


def review_of(w, operator, customer, execution_id, seconds=180):
    """The open review of an execution; a settled or stalled execution is diagnosed."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for task in customer.call("GET", "/human-tasks")["items"]:
            if task["execution_id"] == execution_id and task["state"] == "open":
                return task
        view = operator.execution(customer, execution_id)
        if view["state"] in {"succeeded", "failed", "cancelled"}:
            raise AssertionError(("settled before its review", view, diagnose(w, execution_id)))
        time.sleep(0.2)
    raise AssertionError(("no open review", diagnose(w, execution_id)))


@pytest.fixture
def late_po(world, processes, linux):
    w = world
    drivers.worlds.actions_mode(w, list(gen.CONNECTORS.values()))
    processes.start_api()
    processes.start_worker("orchestrator", "orchestrator-a", wait=False)
    processes.start_worker("sweeper", "sweeper-a", wait=False)
    processes.wait_ready("orchestrator-a", "sweeper-a")
    w.processes = processes
    cert, key = certificate(drivers.worlds.private_directory(w.root / "tls"))
    server, ledger = standin.serve(0, cert, key, TOKENS, host="127.0.0.1")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    double = doubles.ModelDouble(cert, key, token=MODEL_KEY).start()
    destination = {"host": "localhost", "port": server.server_address[1], "path_prefix": "/"}
    operator = drivers.Operator(
        w,
        w.processes,
        connections=[
            {
                "connector": document,
                "destinations": [destination],
                "secret_binding": f"{system}_token",
                "writes": WRITES[system],
            }
            for system, document in gen.CONNECTORS.items()
        ],
    )
    # Each attempt's reserved active time. The 20 s default let a model round's lease
    # lapse on a loaded host (the attempt went uncertain); 40 s keeps the most attempts
    # any scenario makes (2 reads, 3 rounds, 2 tool calls) inside the agent's 600 s.
    operator.demand(40000)
    customer = operator.tenant(w.engine, label="Late PO")
    revision = operator.deploy(
        customer,
        "late-po",
        package=gen.package,
        # Connectors first: the model's closure is exactly its three read-tool connectors.
        dependencies=[*gen.CONNECTORS.values(), gen.model, gen.planners, gen.customer_messages],
    )
    by_connector = customer.connection["by_connector"]
    pins = {
        system: by_connector[document["name"]]["secrets"][0]
        for system, document in gen.CONNECTORS.items()
    }
    model_pin = {"reference_id": str(uuid4()), "version": 1}
    root, mapping = mounted_secrets(
        w.root,
        {**{f"{system}-token": token for system, token in TOKENS.items()}, "model-key": MODEL_KEY},
    )

    def activity(trust, journal):
        providers = [
            {
                "provider_id": "https-json",
                "dependency": catalog_ref(document),
                "operations": [
                    op["id"]
                    for op in document["definition"]["operations"]
                    if op["effect"] == "read"
                ],
                "connector": {
                    "name": document["name"],
                    "version": document["version"],
                    "artifact_sha256": document["definition"]["artifact_sha256"],
                },
                "settings": {
                    "routes": gen.routes_document(system),
                    "ca_file": str(cert),
                    "egress": {"allowed_private_cidrs": ["127.0.0.0/8", "::1/128"]},
                    "test_loopback": True,
                },
            }
            for system, document in gen.CONNECTORS.items()
        ]
        providers.append(
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
            }
        )
        return {
            "trust_file": str(trust),
            "journal_directory": str(journal),
            "secrets": {
                "root": str(root),
                "mapping_file": str(mapping),
                "bindings": [
                    *({**pins[system], "binding": f"{system}-token"} for system in TOKENS),
                    {**model_pin, "binding": "model-key"},
                ],
            },
            "providers": providers,
        }

    runtime = operator.runtime(
        customer, "runtime-late-po", model_document=gen.model, activity=activity
    )
    yield w, operator, customer, revision, ledger, double, runtime
    customer.close()
    double.stop()
    server.shutdown()
    server.server_close()


def test_every_scenario_reads_three_systems_and_waits_for_the_exact_date_change(late_po):
    w, operator, customer, revision, ledger, double, runtime = late_po
    for scenario in SCENARIOS:
        rounds = scenario.get("model_rounds", [{"output": None}])
        double.rounds = rounds
        reads_before, models_before = len(ledger.requests), len(double.requests)
        execution_id = operator.start(customer, revision, scenario["input"])["id"]
        expected = scenario["expected"]
        po = scenario["input"]["po_number"]

        if expected["status"] == "on_time":
            view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 180)
            assert view["state"] == "succeeded", (scenario["id"], diagnose(w, execution_id))
            result = result_of(w, execution_id)
            for key, value in expected.items():
                assert result[key] == value, (scenario["id"], key, result)
            reads = ledger.requests[reads_before:]
            assert [path for _, path, _ in reads] == [
                f"/erp/purchase-orders/{po}",
                f"/carrier/shipments/{po}",
            ] or [path for _, path, _ in reads] == [
                f"/carrier/shipments/{po}",
                f"/erp/purchase-orders/{po}",
            ], reads
            assert len(double.requests) == models_before, scenario["id"]  # no model, no person
            continue

        # Late: the agent investigates across the three systems, then a planner reviews
        # exactly the proposed date change.
        task = review_of(w, operator, customer, execution_id)
        proposal = customer.call("GET", f"/human-tasks/{task['id']}/proposal")
        output = rounds[-1]["output"]
        assert (proposal["connector_name"], proposal["operation"]) == (
            "erp-standin",
            "set_promised_date",
        )
        assert proposal["target"] == {"po_number": po}
        assert proposal["input"] == {
            "promised_day": output["new_promised_day"],
            "reason": output["reasons"][0]["text"],
        }
        assert proposal["independent_approval"] is False

        reads = ledger.requests[reads_before:]
        models = double.requests[models_before:]
        tools = [c for r in rounds for c in r.get("tool_calls", [])]
        assert all(method == "GET" and authorized for method, _, authorized in reads), reads
        assert len(reads) == 2 + len(tools), (scenario["id"], reads)
        assert len(models) == len(rounds), scenario["id"]
        first = models[0]["body"]
        assert first["model"] == "claude-haiku-5-5"
        declared = {f"{system}-standin-{op}" for system, op in gen.TOOLS}
        assert {t["name"] for t in first["tools"]} == declared

        customer.call("POST", f"/human-tasks/{task['id']}/claim", {})
        customer.call(
            "POST",
            f"/human-tasks/{task['id']}/decision",
            {
                "decision_id": f"d-{uuid4().hex[:8]}",
                "intent_sha256": proposal["intent_sha256"],
                "decision": "deny",
            },
        )
        view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 180)
        assert (view["state"], view.get("reason")) == ("failed", "approval_denied"), (
            scenario["id"],
            diagnose(w, execution_id),
        )

    # A denied review sends nothing: no write reached any system.
    assert not [r for r in ledger.requests if r[0] == "POST"], ledger.requests
    assert ledger.date_changes == {} and ledger.customer_updates == {}
