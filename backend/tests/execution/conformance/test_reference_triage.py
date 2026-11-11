"""Reference workflow, milestone 1: the invoice-triage prefix package on real processes.

The prefix is the reference workflow before E05: three reads, the decimal tolerance
decision, the tool-using investigation and the clerk's form, with both action branches
replaced by the form. Providers are the synthetic test-mode fixtures, scripted from the
stand-in's own records (`reference/invoice-triage/standin/server.py`), so the reads
return what the HTTPS stand-in serves. No real model, connector or AP system is used.
"""

import importlib.util
import json
import time
from pathlib import Path

import driver as drivers
import pytest
from driver import _scope_of, diagnose
from processes import Processes
from sqlalchemy.orm import Session

from lotuspond.models import Execution

REFERENCE = Path(__file__).resolve().parents[4] / "reference" / "invoice-triage"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load("invoice_triage_generate", REFERENCE / "generate.py")
standin = _load("invoice_triage_standin", REFERENCE / "standin" / "server.py")
SCENARIOS = json.loads((REFERENCE / "scenarios.json").read_text())["scenarios"]


def reads_for(invoice_id):
    """Each read operation's answer for this invoice, as the stand-in gives it."""
    ledger = standin.Ledger()
    invoice = ledger.answer("get_invoice", {"invoice_id": invoice_id})
    supplier, po = invoice["supplier_id"], invoice["po_number"]
    answers = {
        "get_invoice": ledger.answer("get_invoice", {"invoice_id": invoice_id}),
        "get_purchase_order": ledger.answer("get_purchase_order", {"po_number": po}),
        "get_goods_receipt": ledger.answer("get_goods_receipt", {"po_number": po}),
        "get_supplier_contract": ledger.answer("get_supplier_contract", {"supplier_id": supplier}),
        "list_supplier_invoices": ledger.answer(
            "list_supplier_invoices", {"supplier_id": supplier, "po_number": po}
        ),
        "list_credit_notes": ledger.answer("list_credit_notes", {"supplier_id": supplier}),
    }
    reads = {op: {"value": value} for op, value in answers.items()}
    reads["get_invoice"]["require_seen"] = invoice_id  # the package input reached the read
    reads["get_purchase_order"]["require_seen"] = po  # and the invoice's PO the next ones
    reads["get_goods_receipt"]["require_seen"] = po
    return {"reads": reads}


def result_of(world, execution_id):
    graph = world.graph()
    with Session(world.engine) as db:
        execution = db.get(Execution, execution_id)
        return json.loads(
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


def open_form(w, operator, customer, execution_id, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        view = operator.execution(customer, execution_id)
        if view["state"] in {"succeeded", "failed", "cancelled"}:
            raise AssertionError(f"settled before the form: {view} {diagnose(w, execution_id)}")
        for item in operator.waits(customer, execution_id):
            if item["state"] == "open" and view["state"] != "pending":
                return item
        time.sleep(0.2)
    raise AssertionError("no open clerk form")


def prefix_expectation(scenario):
    """What the prefix returns: an action scenario ends at the clerk's form instead."""
    expected = dict(scenario["expected"])
    if expected["status"] != "matched":
        expected["status"] = "clerk_decided"
        expected["clerk_decision"] = scenario["clerk_form"]["decision"]
    return expected


@pytest.fixture
def reference(system, linux):
    w = system
    operator = drivers.Operator(w, w.processes, connector=gen.connector)
    customer = operator.tenant(w.engine, label="Reference")
    # The execution's active budget is the smallest declared timeout (the agent's 240 s),
    # and every attempt is charged its reservation: 3 reads, the agent root and up to 4
    # children at 20 s each fit (160 s), where 60 s each would not.
    operator.demand(20000)
    revision = operator.deploy(
        customer,
        "invoice-triage-prefix",
        package=gen.prefix,
        dependencies=[gen.connector, gen.model, gen.clerks],  # a model's closure is its connector
    )
    yield w, operator, customer, revision
    customer.close()


def test_the_prefix_triages_every_scenario_as_specified(reference):
    w, operator, customer, revision = reference
    for scenario in SCENARIOS:
        name = f"runtime-{scenario['id']}"
        rounds = scenario.get("model_rounds", [{"output": None}])
        runtime = operator.runtime(
            customer,
            name,
            model_document=gen.model,
            model={"rounds": rounds},
            read=reads_for(scenario["input"]["invoice_id"]),
        )
        print("scenario", scenario["id"], flush=True)
        try:
            execution_id = operator.start(customer, revision, scenario["input"])["id"]
            if scenario["expected"]["status"] != "matched":
                form = open_form(w, operator, customer, execution_id)
                operator.signal(customer, execution_id, form["id"], "clerk", scenario["clerk_form"])
            view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 180)
            assert view["state"] == "succeeded", (scenario["id"], diagnose(w, execution_id))
            result = result_of(w, execution_id)
            for key, value in prefix_expectation(scenario).items():
                assert result[key] == value, (scenario["id"], key, result)
            reads = Processes.calls(runtime["counter"])
            models = Processes.calls(runtime["model_counter"])
            tools = sum(
                len(item.get("tool_calls", [])) for item in scenario.get("model_rounds", [])
            )
            if scenario["expected"]["status"] == "matched":
                assert (reads, models) == (3, 0), scenario["id"]  # no model, no person
            else:
                assert (reads, models) == (3 + tools, len(rounds)), scenario["id"]
                assert result["reasons"] == rounds[-1]["output"]["reasons"]
        except AssertionError as error:
            logs = w.root / "logs"
            tails = {
                path.name: path.read_text(errors="replace")[-3000:]
                for path in sorted(logs.glob(f"{name}.*")) + sorted(logs.glob("api*.stderr"))
            }
            raise AssertionError(f"{error}\n{json.dumps(tails, indent=1)}") from None
        finally:
            w.processes.stop(name)
