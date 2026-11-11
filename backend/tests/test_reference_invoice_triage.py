"""The invoice-triage reference workflow (milestone 1): its documents, rules and stand-in.

No database or runtime: the conformance case in
`tests/execution/conformance/test_reference_triage.py` runs the prefix package.
"""

import datetime
import importlib.util
import json
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from lotuspond_sdk.executable import compile_package

from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.expressions import ValueEnvironment, evaluate_mapping, typed_schema
from lotuspond.execution.publication import _unsupported

ROOT = Path(__file__).resolve().parents[2] / "reference" / "invoice-triage"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = load("invoice_triage_generate", ROOT / "generate.py")
standin = load("invoice_triage_standin", ROOT / "standin" / "server.py")
SCENARIOS = json.loads((ROOT / "scenarios.json").read_text())["scenarios"]
CATALOG = [gen.connector, gen.model, gen.clerks, gen.hold_policy, gen.credit_policy]


def stored(path):
    return json.loads((ROOT / path).read_text())


def test_committed_documents_are_what_the_generator_writes():
    assert stored("package.json") == gen.full
    assert stored("prefix.json") == gen.prefix
    for document in CATALOG:
        assert stored(f"catalog/{document['name']}.json") == document


def test_both_packages_compile_against_exactly_their_catalog():
    full = compile_package(gen.full, dependencies=[], catalog={"documents": CATALOG})
    prefix = compile_package(
        gen.prefix, dependencies=[], catalog={"documents": [gen.connector, gen.model, gen.clerks]}
    )
    kinds = {step["kind"] for step in json.loads(full.model_dump_json())["steps"]}
    assert {"action_intent", "action_dispatch", "outcome_verification"} <= kinds
    assert not {"action_intent", "action_dispatch"} & {
        step["kind"] for step in json.loads(prefix.model_dump_json())["steps"]
    }


def walk(body):
    for step in body["steps"]:
        yield step
        for key in ("then_body", "else_body"):
            if key in step:
                yield from walk(step[key])


def test_each_action_waits_for_review_under_its_policy():
    steps = {step["id"]: step for step in walk(gen.full["body"])}
    hold, credit = steps["hold_intent"], steps["credit_intent"]
    assert (hold["operation"], hold["independent_review"]) == ("set_payment_hold", False)
    assert (credit["operation"], credit["independent_review"]) == ("request_credit_note", True)
    for prefix in ("hold", "credit"):
        assert steps[f"{prefix}_review"]["mode"] == "review"
        assert steps[f"{prefix}_dispatch"]["decision"]["source"]["id"] == f"{prefix}_review"
        assert steps[f"{prefix}_verify"]["receipt"]["source"]["id"] == f"{prefix}_dispatch"


def test_the_agent_reads_only_and_never_writes():
    agent = next(step for step in walk(gen.full["body"]) if step["kind"] == "agent")
    effects = {op["id"]: op["effect"] for op in gen.connector["definition"]["operations"]}
    assert {effects[tool["operation"]] for tool in agent["read_tools"]} == {"read"}


def test_e04_refuses_the_full_package_and_admits_the_prefix_shape():
    with pytest.raises(ExecutionError) as refused:
        _unsupported(gen.full)
    assert refused.value.code == "unsupported_step"
    _unsupported(gen.prefix)  # no refusal


# ------------------------------------------------------------------ the deterministic match


def matched(invoice_id):
    """Evaluate the package's own variance and tolerance mappings on stand-in records."""
    ledger = standin.Ledger()
    invoice = ledger.answer("get_invoice", {"invoice_id": invoice_id})
    steps = {
        "invoice": typed_schema(invoice, gen.INVOICE),
        "order": typed_schema(
            ledger.answer("get_purchase_order", {"po_number": invoice["po_number"]}), gen.ORDER
        ),
        "receipt": typed_schema(
            ledger.answer("get_goods_receipt", {"po_number": invoice["po_number"]}), gen.RECEIPT
        ),
    }
    variance = next(step for step in gen.reads if step["id"] == "variance")
    steps["variance"] = evaluate_mapping(variance["mapping"], ValueEnvironment(None, steps), ())
    return evaluate_mapping(gen.within_tolerance, ValueEnvironment(None, steps), ())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_the_tolerance_rule_sends_exactly_the_exceptions_onward(scenario):
    assert matched(scenario["input"]["invoice_id"]) is (scenario["expected"]["status"] == "matched")


@pytest.mark.parametrize(
    "scenario", [s for s in SCENARIOS if "model_rounds" in s], ids=lambda s: s["id"]
)
def test_scripted_proposals_cite_only_declared_tools_they_called(scenario):
    rounds = scenario["model_rounds"]
    called = {call["name"] for item in rounds for call in item.get("tool_calls", [])}
    declared = {f"ap-standin-{op}" for op in gen.TOOLS}
    assert called <= declared
    proposal = rounds[-1]["output"]
    for reason in proposal["reasons"]:
        assert reason["tool"] == "input" or reason["tool"] in called
    expected = scenario["expected"]
    assert (proposal["exception_class"], proposal["resolution"], proposal["amount"]) == (
        expected["exception_class"],
        expected["resolution"],
        expected["amount"],
    )


# ------------------------------------------------------------------ the stand-in


def test_writes_are_idempotent_per_key_and_read_back():
    ledger = standin.Ledger()
    target, value = {"invoice_id": "INV-1004"}, {"amount": "90.00", "reason": "short"}
    assert ledger.answer("get_payment_hold", target)["exists"] is False
    first = ledger.answer("set_payment_hold", target, value, "key-1")
    assert ledger.answer("set_payment_hold", target, value, "key-1") == first
    with pytest.raises(standin.Invalid):
        ledger.answer("set_payment_hold", target, {**value, "amount": "91.00"}, "key-1")
    assert ledger.answer("get_payment_hold", target) == {
        "exists": True,
        "amount": "90.00",
        "reason": "short",
    }


def test_a_write_that_times_out_after_committing_is_visible_on_readback():
    ledger = standin.Ledger()
    ledger.fail_after_commit = 1
    target = {"invoice_id": "INV-1003"}
    value = {"supplier_id": "SUP-ACME", "amount": "60.00", "reason": "overcharge"}
    with pytest.raises(TimeoutError):
        ledger.answer("request_credit_note", target, value, "key-2")
    assert ledger.answer("get_credit_note_request", target)["exists"] is True
    assert ledger.answer("request_credit_note", target, value, "key-2") == {
        "reference": "CNR-INV-1003"
    }


def certificate(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def test_the_https_service_requires_its_token_and_maps_routes(tmp_path):
    cert, key = certificate(tmp_path)
    server, _ = standin.serve(0, cert, key, "synthetic-token", host="localhost")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = ssl.create_default_context(cafile=str(cert))
    base = f"https://localhost:{server.server_address[1]}"

    def fetch(path, *, token="synthetic-token", body=None, key=None):
        headers = {"Authorization": f"Bearer {token}"}
        if key:
            headers["Idempotency-Key"] = key
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(base + path, data=data, headers=headers)
        with urllib.request.urlopen(request, context=context, timeout=5) as response:
            return response.status, json.loads(response.read())

    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            fetch("/invoices/INV-1001", token="wrong")
        assert refused.value.code == 401
        assert fetch("/invoices/INV-1001")[1]["po_number"] == "PO-5001"
        status, listed = fetch("/suppliers/SUP-ACME/purchase-orders/PO-5006/invoices")
        assert [item["invoice_id"] for item in listed["invoices"]] == ["INV-0990", "INV-1006"]
        hold = {"amount": "450.00", "reason": "duplicate of INV-0990"}
        assert fetch("/invoices/INV-1006/payment-hold", body=hold, key="k") == (
            201,
            {"reference": "HOLD-INV-1006"},
        )
        assert fetch("/invoices/INV-1006/payment-hold")[1]["exists"] is True
        with pytest.raises(urllib.error.HTTPError) as missing:
            fetch("/invoices/INV-9999")
        assert missing.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
