"""Reference workflow on real processes, production wiring and real providers.

The invoice-triage prefix (the workflow before E05) runs on the API, orchestrator,
sweeper and one enrolled runtime. The API and workers are built from an operator
authority directory (`python -m lotuspond.authority`) with test mode off, as production
builds them (milestone 3); only the runtime is in test mode, to reach loopback stand-ins. The runtime's `https-json` connector reads the HTTPS
stand-in AP system (`reference/invoice-triage/standin/server.py`) with the connection's
bearer token from a mounted secret, and its `anthropic-messages` provider calls a local
Messages API double (`standin/model_double.py`) scripted from each scenario. Nothing
leaves the machine: no real model, AP system or key is used.
"""

import datetime
import importlib.util
import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

import driver as drivers
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from driver import _scope_of, catalog_ref, diagnose
from sqlalchemy.orm import Session

from lotuspond.models import Execution

REFERENCE = Path(__file__).resolve().parents[4] / "reference" / "invoice-triage"
AP_TOKEN, MODEL_KEY = "synthetic-ap-token", "synthetic-model-key"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load("invoice_triage_generate", REFERENCE / "generate.py")
standin = _load("invoice_triage_standin", REFERENCE / "standin" / "server.py")
doubles = _load("invoice_triage_model_double", REFERENCE / "standin" / "model_double.py")
SCENARIOS = json.loads((REFERENCE / "scenarios.json").read_text())["scenarios"]


def certificate(directory):
    """A synthetic localhost certificate, its own CA; never production trust."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=6))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "standin-cert.pem", directory / "standin-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(key_path, 0o600)
    return cert_path, key_path


def mounted_secrets(world_root, pins):
    """E03's mounted layout: private secret files and a private mapping file."""
    root = drivers.worlds.private_directory(world_root / "secrets" / "root")
    mapping_dir = drivers.worlds.private_directory(world_root / "secrets" / "mapping")
    rows = []
    for binding, value in pins.items():
        drivers.worlds.write_private(root / binding, value)
        rows.append({"binding_id": binding, "version": 1, "filename": binding})
    drivers.worlds.write_private(mapping_dir / "mapping.json", {"bindings": rows})
    return root, mapping_dir / "mapping.json"


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
def reference(world, processes, linux):
    w = world
    drivers.worlds.operator_mode(w)  # before any process reads the world's authorities
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
        connector=gen.connector,
        destinations=[destination],
        secret_binding="ap_token",
    )
    customer = operator.tenant(w.engine, label="Reference")
    # Each attempt's demand is the operator directory's (20 s of active time by default),
    # well inside the agent's 600 s, which is the execution's active budget.
    revision = operator.deploy(
        customer,
        "invoice-triage-prefix",
        package=gen.prefix,
        dependencies=[gen.connector, gen.model, gen.clerks],  # a model's closure is its connector
    )
    ap_pin = customer.connection["secrets"][0]
    model_pin = {"reference_id": str(uuid4()), "version": 1}
    root, mapping = mounted_secrets(w.root, {"ap-token": AP_TOKEN, "model-key": MODEL_KEY})

    def activity(trust, journal):
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
                    "operations": list(operator.read_operations),
                    "connector": {
                        "name": gen.connector["name"],
                        "version": gen.connector["version"],
                        "artifact_sha256": gen.connector["definition"]["artifact_sha256"],
                    },
                    "settings": {
                        "routes": gen.ROUTES,
                        "ca_file": str(cert),
                        "egress": {"allowed_private_cidrs": ["127.0.0.0/8", "::1/128"]},
                        "test_loopback": True,
                    },
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
        }

    runtime = operator.runtime(customer, "runtime-reference", activity=activity)
    yield w, operator, customer, revision, ledger, double, runtime
    customer.close()
    double.stop()
    server.shutdown()
    server.server_close()


def test_the_prefix_runs_every_scenario_on_the_real_providers(reference):
    w, operator, customer, revision, ledger, double, runtime = reference
    for scenario in SCENARIOS:
        rounds = scenario.get("model_rounds", [{"output": None}])
        double.rounds = rounds
        reads_before, models_before = len(ledger.requests), len(double.requests)
        execution_id = operator.start(customer, revision, scenario["input"])["id"]
        if scenario["expected"]["status"] != "matched":
            form = open_form(w, operator, customer, execution_id)
            operator.signal(customer, execution_id, form["id"], "clerk", scenario["clerk_form"])
        view = operator.settled(customer, execution_id, {"succeeded", "failed"}, 180)
        assert view["state"] == "succeeded", (scenario["id"], diagnose(w, execution_id))
        result = result_of(w, execution_id)
        for key, value in prefix_expectation(scenario).items():
            assert result[key] == value, (scenario["id"], key, result)

        reads = ledger.requests[reads_before:]
        models = double.requests[models_before:]
        tools = [c for r in scenario.get("model_rounds", []) for c in r.get("tool_calls", [])]
        assert all(method == "GET" and authorized for method, _, authorized in reads), reads
        invoice = scenario["input"]["invoice_id"]
        assert reads[0][1] == f"/invoices/{invoice}", reads
        if scenario["expected"]["status"] == "matched":
            assert (len(reads), len(models)) == (3, 0), scenario["id"]  # no model, no person
            continue
        assert (len(reads), len(models)) == (3 + len(tools), len(rounds)), scenario["id"]
        assert result["reasons"] == rounds[-1]["output"]["reasons"]
        first = models[0]["body"]
        assert first["model"] == "claude-haiku-5-5"
        assert first["output_config"]["effort"] == "medium"
        assert not {"temperature", "top_p", "top_k"} & set(first)
        assert {t["name"] for t in first["tools"]} == {f"ap-standin-{op}" for op in gen.TOOLS}
        assert all(t["strict"] for t in first["tools"])
        if len(models) > 1:
            replayed = models[1]["body"]["messages"]
            assert replayed[1]["role"] == "assistant"
            assert replayed[1]["content"][0]["type"] == "thinking"  # replayed unchanged
            assert replayed[2]["content"][0]["type"] == "tool_result"
