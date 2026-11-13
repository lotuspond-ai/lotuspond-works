"""The late purchase order reference workflow (milestone 1): documents, rules and stand-in.

No database or runtime. The conformance case on real processes follows once the
conformance driver binds more than one connector per package.
"""

import datetime
import importlib.util
import json
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from lotuspond_sdk.executable import compile_package

from lotuspond.execution.action_policies import Closure, admit_closure, classify_plan
from lotuspond.execution.expressions import ValueEnvironment, evaluate_mapping, typed_schema

ROOT = Path(__file__).resolve().parents[2] / "reference" / "late-po"
TOKENS = {"erp": "erp-token", "carrier": "carrier-token", "inbox": "inbox-token"}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = load("late_po_generate", ROOT / "generate.py")
standin = load("late_po_standin", ROOT / "standin" / "server.py")
SCENARIOS = json.loads((ROOT / "scenarios.json").read_text())["scenarios"]


def stored(path):
    return json.loads((ROOT / path).read_text())


def walk(body):
    for step in body["steps"]:
        yield step
        for key in ("then_body", "else_body"):
            if key in step:
                yield from walk(step[key])


STEPS = {step["id"]: step for step in walk(gen.package["body"])}


# ------------------------------------------------------------------ documents


def test_committed_documents_are_what_the_generator_writes():
    assert stored("package.json") == gen.package
    for document in gen.CATALOG:
        assert stored(f"catalog/{document['name']}.json") == document
    for system in gen.ROUTES:
        assert stored(f"catalog/{system}-standin.routes.json") == gen.routes_document(system)


def test_the_package_compiles_against_exactly_its_catalog():
    plan = compile_package(gen.package, dependencies=[], catalog={"documents": gen.CATALOG})
    kinds = {step["kind"] for step in json.loads(plan.model_dump_json())["steps"]}
    assert {"agent", "action_intent", "action_dispatch", "outcome_verification"} <= kinds
    with pytest.raises(Exception):
        compile_package(gen.package, dependencies=[], catalog={"documents": gen.CATALOG[:-1]})


def test_each_system_is_its_own_connection_with_its_own_secret():
    connectors = {c["id"]: c for c in gen.package["connectors"]}
    assert set(connectors) == {"erp", "carrier", "inbox"}
    assert {c["credential_ref"] for c in connectors.values()} == set(gen.package["secret_refs"])
    assert len(set(gen.package["secret_refs"])) == 3
    for system, document in gen.CONNECTORS.items():
        assert connectors[system]["ref"] == gen.ref_of(document)


def test_both_actions_wait_for_review_under_their_policies():
    reschedule, notify = STEPS["reschedule_intent"], STEPS["notify_intent"]
    assert (reschedule["connector_ref"], reschedule["operation"]) == ("erp", "set_promised_date")
    assert (notify["connector_ref"], notify["operation"]) == ("inbox", "send_customer_update")
    assert (reschedule["policy_ref"], reschedule["independent_review"]) == ("planners", False)
    assert (notify["policy_ref"], notify["independent_review"]) == ("customer_messages", True)
    assert gen.planners["definition"]["independent_review"] is False
    assert gen.customer_messages["definition"]["independent_review"] is True
    for prefix in ("reschedule", "notify"):
        assert STEPS[f"{prefix}_review"]["mode"] == "review"
        assert STEPS[f"{prefix}_dispatch"]["decision"]["source"]["id"] == f"{prefix}_review"
        assert STEPS[f"{prefix}_verify"]["receipt"]["source"]["id"] == f"{prefix}_dispatch"


def test_the_customer_is_told_only_after_the_date_change_and_only_when_affected():
    late = STEPS["on_time"]["else_body"]["steps"]
    order = [step["id"] for step in late]
    assert order.index("reschedule_verify") < order.index("tell")
    assert STEPS["tell"]["condition"] == gen.step("investigate", "/notify_customer")
    assert [s["id"] for s in STEPS["tell"]["else_body"]["steps"]] == []


def test_the_agent_reads_only_from_each_system_and_never_writes():
    agent = STEPS["investigate"]
    effects = {
        (system, op["id"]): op["effect"]
        for system, document in gen.CONNECTORS.items()
        for op in document["definition"]["operations"]
    }
    tools = {(tool["connector_ref"], tool["operation"]) for tool in agent["read_tools"]}
    assert tools == set(gen.TOOLS)
    assert {effects[tool] for tool in tools} == {"read"}
    assert {tool[0] for tool in tools} == {"erp", "carrier", "inbox"}


def test_the_package_pins_interpreter_v2():
    from lotuspond_sdk.executable import validate_executable_package
    from lotuspond_sdk.executable.catalog import catalog_document_digest, validate_catalog

    closure = Closure(
        packages=(validate_executable_package(gen.package),),
        catalog={
            catalog_document_digest(raw): validate_catalog({"documents": [raw]}).documents[0]
            for raw in gen.CATALOG
        },
    )
    assert classify_plan(closure).effects
    assert admit_closure(closure) == "lotuspond.execution-interpreter.v2"


def test_the_connectors_pin_their_routes_and_the_model_its_configuration():
    from lotuspond_runtime.anthropic_messages import configuration_digest
    from lotuspond_runtime.https_json import routes_digest

    for system, document in gen.CONNECTORS.items():
        routes = gen.routes_document(system)
        assert document["definition"]["artifact_sha256"] == routes_digest(routes)
        reads = {op["id"] for op in document["definition"]["operations"] if op["effect"] == "read"}
        assert set(routes["operations"]) == reads
    assert gen.model["definition"]["configuration_sha256"] == configuration_digest(
        gen.MODEL_CONFIGURATION
    )
    assert gen.MODEL_CONFIGURATION["model"] == "claude-haiku-5-5"


# ------------------------------------------------------------------ the deterministic check


def on_time(po_number):
    """Evaluate the package's own on-time mapping on stand-in records."""
    ledger = standin.Ledger()
    steps = {
        "order": typed_schema(
            ledger.answer("get_purchase_order", {"po_number": po_number}), gen.ORDER
        ),
        "shipment": typed_schema(
            ledger.answer("get_shipment", {"po_number": po_number}), gen.SHIPMENT
        ),
    }
    return evaluate_mapping(gen.on_time, ValueEnvironment(None, steps), ())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_the_on_time_rule_sends_exactly_the_late_orders_onward(scenario):
    expected = scenario["expected"]["status"] == "on_time"
    assert on_time(scenario["input"]["po_number"]) is expected


@pytest.mark.parametrize(
    "scenario", [s for s in SCENARIOS if "model_rounds" in s], ids=lambda s: s["id"]
)
def test_scripted_proposals_cite_only_tools_they_called_and_match_the_records(scenario):
    rounds = scenario["model_rounds"]
    called = {c["name"] for item in rounds for c in item.get("tool_calls", [])}
    declared = {f"{system}-standin-{op}" for system, op in gen.TOOLS}
    assert called <= declared
    proposal = rounds[-1]["output"]
    for reason in proposal["reasons"]:
        assert reason["tool"] == "input" or reason["tool"] in called
    expected = scenario["expected"]
    assert proposal["new_promised_day"] == expected["new_promised_day"]
    assert proposal["notify_customer"] is expected["notify_customer"]
    ledger = standin.Ledger()
    order = ledger.answer("get_purchase_order", scenario["input"])
    shipment = ledger.answer("get_shipment", scenario["input"])
    # The proposal keeps the instructions: never before the carrier's estimate, and the
    # customer is told exactly when the new day passes the day they asked for.
    assert proposal["new_promised_day"] >= shipment["estimated_day"]
    assert proposal["new_promised_day"] > order["promised_day"]
    assert proposal["notify_customer"] is (
        proposal["new_promised_day"] > order["customer_requested_day"]
    )
    assert set(scenario["reviews"]) == (
        {"reschedule", "notify"} if expected["notify_customer"] else {"reschedule"}
    )


# ------------------------------------------------------------------ the stand-in


def test_the_date_change_is_idempotent_reads_back_and_moves_the_order():
    ledger = standin.Ledger()
    target, change = {"po_number": "PO-7002"}, {"promised_day": 20779, "reason": "supplier"}
    assert ledger.answer("get_promised_date_change", target)["exists"] is False
    first = ledger.answer("set_promised_date", target, change, "key-1")
    assert ledger.answer("set_promised_date", target, change, "key-1") == first
    with pytest.raises(standin.Invalid):
        ledger.answer("set_promised_date", target, {**change, "promised_day": 20780}, "key-1")
    assert ledger.answer("get_promised_date_change", target) == {
        "exists": True,
        "promised_day": 20779,
        "reason": "supplier",
    }
    # A second start for the same order now finds it on time: nothing is written twice.
    assert ledger.answer("get_purchase_order", target)["promised_day"] == 20779
    assert on_time_on(ledger, "PO-7002") is True


def on_time_on(ledger, po_number):
    steps = {
        "order": typed_schema(
            ledger.answer("get_purchase_order", {"po_number": po_number}), gen.ORDER
        ),
        "shipment": typed_schema(
            ledger.answer("get_shipment", {"po_number": po_number}), gen.SHIPMENT
        ),
    }
    return evaluate_mapping(gen.on_time, ValueEnvironment(None, steps), ())


def test_a_customer_update_that_times_out_after_sending_is_visible_on_readback():
    ledger = standin.Ledger()
    ledger.fail_after_commit = 1
    target, change = {"sales_order_id": "SO-3004"}, {"promised_day": 20779, "reason": "delay"}
    with pytest.raises(TimeoutError):
        ledger.answer("send_customer_update", target, change, "key-2")
    assert ledger.answer("get_customer_update", target)["exists"] is True
    assert ledger.answer("send_customer_update", target, change, "key-2") == {
        "reference": "MAIL-SO-3004"
    }
    assert ledger.customer_updates["SO-3004"]["message"].startswith(
        "Your order SO-3004 will now arrive on day 20779."
    )


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


@pytest.fixture
def https(tmp_path):
    cert, key = certificate(tmp_path)
    server, ledger = standin.serve(0, cert, key, TOKENS, host="127.0.0.1")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield cert, server.server_address[1], ledger
    server.shutdown()
    server.server_close()


def test_each_system_accepts_only_its_own_token(https):
    cert, port, _ = https
    context = ssl.create_default_context(cafile=str(cert))

    def fetch(path, token, body=None, key=None):
        headers = {"Authorization": f"Bearer {token}"}
        if key:
            headers["Idempotency-Key"] = key
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"https://localhost:{port}{path}", data=data, headers=headers
        )
        with urllib.request.urlopen(request, context=context, timeout=5) as response:
            return response.status, json.loads(response.read())

    assert fetch("/erp/purchase-orders/PO-7001", "erp-token")[1]["sales_order_id"] == "SO-3001"
    for path, wrong in (
        ("/erp/purchase-orders/PO-7001", "carrier-token"),
        ("/carrier/shipments/PO-7001", "inbox-token"),
        ("/inbox/purchase-orders/PO-7002", "erp-token"),
    ):
        with pytest.raises(urllib.error.HTTPError) as refused:
            fetch(path, wrong)
        assert refused.value.code == 401
    sent = fetch(
        "/inbox/sent/SO-3004",
        "inbox-token",
        body={"promised_day": 20779, "reason": "delay"},
        key="k",
    )
    assert sent == (201, {"reference": "MAIL-SO-3004"})
    assert fetch("/inbox/sent/SO-3004", "inbox-token")[1]["exists"] is True
    with pytest.raises(urllib.error.HTTPError) as missing:
        fetch("/carrier/shipments/PO-9999", "carrier-token")
    assert missing.value.code == 404


def test_each_route_reaches_its_stand_in_operation():
    for system in gen.ROUTES:
        for operation, route in gen.ROUTES[system].items():
            path = route["path"].replace("{po_number}", "PO-1").replace("{sku}", "SKU-1")
            path = path.replace("{sales_order_id}", "SO-1")
            assert standin.route("GET", path)[:2] == (system, operation)


def test_the_https_json_connector_reads_each_system_with_its_own_secret(https):
    from lotuspond_runtime.https_json import HttpsJsonReadProvider, routes_digest
    from lotuspond_runtime.protocol import (
        ConnectionConfig,
        ConnectorPin,
        DataHandling,
        DestinationRule,
        SecretPin,
    )
    from lotuspond_runtime.providers import ProviderDescriptor
    from lotuspond_sdk.executable.package import ArtifactRef

    cert, port, ledger = https
    reference = standin.Ledger()

    def reader(system):
        routes = gen.routes_document(system)
        pin = ConnectorPin(
            name=f"{system}-standin", version=gen.VERSION, artifact_sha256=routes_digest(routes)
        )
        descriptor = ProviderDescriptor(
            "https-json",
            ArtifactRef.model_validate(gen.ref_of(gen.CONNECTORS[system])),
            "read",
            frozenset(routes["operations"]),
            connector=pin,
            settings={
                "routes": routes,
                "ca_file": str(cert),
                "egress": {"allowed_private_cidrs": ["127.0.0.0/8", "::1/128"]},
                "test_loopback": True,
            },
        )
        provider = HttpsJsonReadProvider(descriptor, (TOKENS[system].encode(),), test_mode=True)
        connection = ConnectionConfig(
            connector=pin,
            operations=tuple(routes["operations"]),
            destinations=(DestinationRule(host="localhost", port=port, path_prefix="/"),),
            secrets=(SecretPin(reference_id="11111111-1111-4111-8111-111111111111", version=1),),
            data_handling=DataHandling(timeout_ms=5000),
        )

        def read(operation, target):
            records = SimpleNamespace(
                envelope=SimpleNamespace(operation=operation), connection=connection
            )
            return provider.invoke(records, {"target": target, "input": None}, 0).value

        return read

    erp, carrier, inbox = reader("erp"), reader("carrier"), reader("inbox")
    assert erp("get_purchase_order", {"po_number": "PO-7004"}) == reference.answer(
        "get_purchase_order", {"po_number": "PO-7004"}
    )
    assert erp("get_stock_cover", {"sku": "TAPE-48"}) == {"sku": "TAPE-48", "days_of_cover": 2}
    events = carrier("list_tracking_events", {"po_number": "PO-7003"})["events"]
    assert events[-1]["code"] == "customs_hold"
    messages = inbox("list_supplier_messages", {"po_number": "PO-7006"})["messages"]
    assert messages[0]["message_id"] == "MSG-802"
    assert ledger.requests == [
        ("GET", "/erp/purchase-orders/PO-7004", True),
        ("GET", "/erp/stock/TAPE-48", True),
        ("GET", "/carrier/shipments/PO-7003/events", True),
        ("GET", "/inbox/purchase-orders/PO-7006", True),
    ]


def test_deployment_admits_both_writes_through_their_own_connections():
    """Each write is bound to its own system's connection, and its read-back accepts
    the write's exact target and input (E05 owner decision 18)."""
    from lotuspond_runtime.protocol import ConnectorPin
    from lotuspond_sdk.executable import validate_executable_package
    from lotuspond_sdk.executable.catalog import catalog_document_digest, validate_catalog

    from lotuspond.execution.action_policies import check_write_bindings

    closure = Closure(
        packages=(validate_executable_package(gen.package),),
        catalog={
            catalog_document_digest(raw): validate_catalog({"documents": [raw]}).documents[0]
            for raw in gen.CATALOG
        },
    )
    connections = {}
    for document in gen.CONNECTORS.values():
        pin = ConnectorPin(
            name=document["name"],
            version=document["version"],
            artifact_sha256=document["definition"]["artifact_sha256"],
        )
        allowed = [op["id"] for op in document["definition"]["operations"]]
        connections[catalog_document_digest(document)] = (pin, allowed)
    check_write_bindings(closure, connections)
