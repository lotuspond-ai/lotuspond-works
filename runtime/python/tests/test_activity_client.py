"""Activity client: exact routes, bounded responses checked against what was sent."""

import hashlib
import importlib
import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from tls_fixtures import tls_server

from lotuspond_runtime.errors import FleetError

TOKEN = SecretStr("lpr_" + "A" * 43)
SESSION = SecretStr("lps_" + "A" * 43)
VECTORS = Path(__file__).resolve().parents[3] / "sdk/python/tests/activity/fixtures"
SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
ATTEMPT = "00000000-0000-4000-8000-000000000006"
HANDLE = dict(id="00000000-0000-4000-8000-000000000099", version=1)


def client_module():
    assert importlib.util.find_spec("lotuspond_runtime.activity_client"), "missing client"
    return importlib.import_module("lotuspond_runtime.activity_client")


def delivery_bytes(envelope=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from lotuspond_sdk.activity import canonical_activity_json, sign_activity

    raw = envelope or json.loads((VECTORS / "read.json").read_text())
    signature = sign_activity(raw, Ed25519PrivateKey.from_private_bytes(SEED))
    return json.dumps(
        {
            "envelope": canonical_activity_json(raw).decode(),
            "signature": json.dumps(signature.model_dump(), separators=(",", ":")),
        }
    ).encode()


def lease(**changes):
    return {
        "attempt_id": ATTEMPT,
        "generation": 1,
        "lease_revision": 1,
        "expires_at_ms": 1800000060000,
        "deadline_ms": 1800000300000,
        **changes,
    }


class Transport:
    def __init__(self, responses):
        self.responses, self.calls = dict(responses), []

    def request(self, method, route, body, token, session=None):
        self.calls.append((method, route, body, token, session))
        response = self.responses[route.split("/")[2] if route.count("/") > 2 else route]
        return response if type(response) is bytes else json.dumps(response).encode()

    def close(self):
        pass


def test_routes_bodies_and_session_credentials():
    module = client_module()
    from lotuspond_runtime.activity_protocol import ActivityResult, ChildActivityRequest

    content = b'{"rows":[1,2]}'
    handle = {**HANDLE, "sha256": hashlib.sha256(content).hexdigest()}
    transport = Transport(
        {
            "/activities/poll": b"null",
            "/activities/claim": lease(),
            "/activities/renew": lease(lease_revision=5, expires_at_ms=1800000065000),
            ATTEMPT: handle,
            "/activities/children": {"attempt_id": HANDLE["id"]},
            "/activities/results": {},
        }
    )
    client = module.ActivityClient(transport, TOKEN, SESSION)
    assert client.poll() is None
    from lotuspond_sdk.activity import ActivityDelivery

    delivery = ActivityDelivery.model_validate(json.loads(delivery_bytes()))
    claimed = client.claim(delivery)
    assert claimed.attempt_id == ATTEMPT
    assert client.renew(claimed).lease_revision == 5
    assert client.store(ATTEMPT, content).id == HANDLE["id"]
    child = ChildActivityRequest(
        request_id="round-1",
        parent_attempt_id=ATTEMPT,
        kind="model",
        declaration_name="model-fixture",
        arguments=handle,
    )
    assert client.request_child(child) == HANDLE["id"]
    client.result(
        ActivityResult(
            attempt_id=ATTEMPT,
            lease_revision=5,
            result=handle,
            code="succeeded",
            usage=dict(input_tokens=1, output_tokens=1, cost_microusd=1, completed_at_ms=1),
        )
    )
    routes = [call[1] for call in transport.calls]
    assert routes == [
        "/activities/poll",
        "/activities/claim",
        "/activities/renew",
        f"/activities/{ATTEMPT}/values",
        "/activities/children",
        "/activities/results",
    ]
    assert all(
        call[0] == "POST" and call[3] is TOKEN and call[4] is SESSION for call in transport.calls
    )
    claim_body = json.loads(transport.calls[1][2])
    envelope = json.loads(delivery.envelope)
    assert claim_body == {
        "delivery_id": envelope["delivery_id"],
        "delivery_sha256": hashlib.sha256(delivery.envelope.encode()).hexdigest(),
    }
    assert transport.calls[3][2] == content  # the exact stored bytes, nothing added


def test_poll_returns_only_exact_canonical_deliveries():
    module = client_module()
    exact = delivery_bytes()
    client = module.ActivityClient(Transport({"/activities/poll": exact}), TOKEN, SESSION)
    delivery = client.poll()
    assert delivery.envelope == json.loads(exact)["envelope"]
    # Same envelope content, but not its canonical bytes: refused.
    spaced = json.loads(exact)
    spaced["envelope"] = json.dumps(json.loads(spaced["envelope"]), indent=1)
    for bad in (
        json.dumps(spaced).encode(),
        b'{"envelope":"{}","signature":"{}"}',
        b'{"envelope":1}',
        b"[]",
    ):
        client = module.ActivityClient(Transport({"/activities/poll": bad}), TOKEN, SESSION)
        with pytest.raises(FleetError, match="command_rejected"):
            client.poll()


@pytest.mark.parametrize(
    "response",
    [
        lease(attempt_id="00000000-0000-4000-8000-000000000007"),
        lease(generation=2),
        lease(expires_at_ms=1800000300001),
    ],
)
def test_claim_rejects_a_lease_for_another_attempt_or_generation(response):
    from lotuspond_sdk.activity import ActivityDelivery

    module = client_module()
    client = module.ActivityClient(Transport({"/activities/claim": response}), TOKEN, SESSION)
    with pytest.raises(FleetError, match="command_rejected"):
        client.claim(ActivityDelivery.model_validate(json.loads(delivery_bytes())))


@pytest.mark.parametrize(
    "response",
    [
        lease(deadline_ms=1800000400000),
        lease(lease_revision=1, expires_at_ms=1800000050000),
        lease(attempt_id="00000000-0000-4000-8000-000000000007"),
        lease(lease_revision=1, generation=2),
    ],
)
def test_renewal_cannot_move_the_deadline_or_go_backwards(response):
    from lotuspond_runtime.activity_protocol import ActivityLease

    module = client_module()
    client = module.ActivityClient(Transport({"/activities/renew": response}), TOKEN, SESSION)
    with pytest.raises(FleetError, match="command_rejected"):
        client.renew(ActivityLease.model_validate(lease(lease_revision=2)))


def test_store_requires_bounded_canonical_content_and_its_digest():
    module = client_module()
    content = b'{"a":1}'
    good = {**HANDLE, "sha256": hashlib.sha256(content).hexdigest()}
    wrong = {**HANDLE, "sha256": "0" * 64}
    transport = Transport({ATTEMPT: wrong})
    client = module.ActivityClient(transport, TOKEN, SESSION)
    with pytest.raises(FleetError, match="command_rejected"):
        client.store(ATTEMPT, content)
    client = module.ActivityClient(Transport({ATTEMPT: good}), TOKEN, SESSION)
    for bad in (b'{ "a": 1 }', b'{"a":1,"a":2}', b"", b"NaN", "text"):
        with pytest.raises(FleetError):
            client.store(ATTEMPT, bad)
    with pytest.raises(FleetError, match="input_limit"):
        client.store(ATTEMPT, b'"' + b"x" * 65536 + b'"')
    with pytest.raises(FleetError):
        client.store("not-a-uuid", content)
    assert client.store(ATTEMPT, content) == client.store(ATTEMPT, content)


def test_transport_allows_activity_routes_only_with_a_session(tmp_path):
    transport = importlib.import_module("lotuspond_runtime.control_transport")
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nnull"
    with tls_server(tmp_path, response) as (origin, ca, _):
        t = transport.ControlTransport(origin, ca)
        for route in (
            "/activities/poll",
            "/activities/claim",
            "/activities/renew",
            "/activities/children",
            "/activities/results",
            "/activities/decline",
            f"/activities/{ATTEMPT}/values",
        ):
            with pytest.raises(FleetError, match="invalid_request"):
                t.request("POST", route, b"{}", TOKEN)  # no session
            with pytest.raises(FleetError, match="invalid_request"):
                t.request("GET", route, None, TOKEN, SESSION)
        for route in (
            "/activities/other",
            "/activities/ABC/values",
            f"/activities/{ATTEMPT}/values/extra",
            "/activities/../commands/poll",
        ):
            with pytest.raises(FleetError, match="invalid_request"):
                t.request("POST", route, b"{}", TOKEN, SESSION)
        assert t.request("POST", "/activities/poll", b"{}", TOKEN, SESSION) == b"null"


def test_a_targeted_poll_names_its_parent_and_nothing_else():
    module = client_module()
    transport = Transport({"/activities/poll": b"null"})
    client = module.ActivityClient(transport, TOKEN, SESSION)
    assert client.poll(parent_attempt_id=ATTEMPT) is None
    assert json.loads(transport.calls[0][2]) == {"parent_attempt_id": ATTEMPT}
    assert client.poll() is None and transport.calls[1][2] == b"{}"
    with pytest.raises(FleetError, match="invalid_request"):
        client.poll(parent_attempt_id="not-a-uuid")


def test_a_decline_names_the_exact_delivery_and_nothing_else():
    from lotuspond_sdk.activity import ActivityDelivery

    module = client_module()
    transport = Transport({"/activities/decline": {}})
    client = module.ActivityClient(transport, TOKEN, SESSION)
    delivery = ActivityDelivery.model_validate(json.loads(delivery_bytes()))
    assert client.decline(delivery) is None
    method, route, body, token, session = transport.calls[0]
    envelope = json.loads(delivery.envelope)
    assert (method, route, token, session) == ("POST", "/activities/decline", TOKEN, SESSION)
    assert json.loads(body) == {
        "delivery_id": envelope["delivery_id"],
        "delivery_sha256": hashlib.sha256(delivery.envelope.encode()).hexdigest(),
    }
    transport.responses["/activities/decline"] = {"declined": True}
    with pytest.raises(FleetError, match="command_rejected"):
        client.decline(delivery)  # anything but the empty acknowledgement


class Answers:
    """A fake control plane answering by exact route; an exception is a refusal."""

    def __init__(self, answers):
        self.answers, self.calls = dict(answers), []

    def request(self, method, route, body, token, session=None):
        self.calls.append((method, route, body, token, session))
        answer = self.answers[route]
        if isinstance(answer, Exception):
            raise answer
        return answer if type(answer) is bytes else json.dumps(answer).encode()

    def close(self):
        pass


READ = json.loads((VECTORS / "read.json").read_text())
CONNECTOR = dict(kind="connector", name="connector-fixture", version="1.0.0", sha256="a" * 64)
MODEL = dict(kind="model", name="model-fixture", version="1.0.0", sha256="b" * 64)


def records_answer(**changes):
    from lotuspond_sdk.activity import canonical_activity_json

    return {
        "envelope": canonical_activity_json(READ).decode(),
        "connection": None,
        "dependency": READ["dependency"],
        "deadline_ms": READ["expires_at_ms"],
        "input_schema": {"schema": None, "definitions": []},
        "output_schema": {"schema": None, "definitions": []},
        "request_sha256": READ["input"]["sha256"],
        **changes,
    }


def agent_answer(**changes):
    return {
        "model": MODEL,
        "model_name": "model-fixture",
        "instructions": "Synthetic",
        "tools": [
            {
                "name": "connector-fixture-read",
                "dependency": CONNECTOR,
                "operation": "read",
                "input_schema": {"target": None, "input": None, "definitions": []},
                "output_schema": {"schema": None, "definitions": []},
            }
        ],
        "context": [{"source_id": ATTEMPT, "version": 2}],
        "max_steps": 10,
        "max_input_tokens": 1000,
        "max_output_tokens": 1000,
        "max_cost_microusd": 100000,
        **changes,
    }


def test_records_name_the_exact_delivery_and_are_checked():
    from lotuspond_sdk.activity import canonical_activity_json, validate_activity

    module = client_module()
    envelope = validate_activity(READ)
    transport = Answers({"/activity-artifacts/records": records_answer()})
    client = module.ActivityClient(transport, TOKEN, SESSION)
    records = client.records(envelope)
    assert records.envelope == envelope and records.request_sha256 == READ["input"]["sha256"]
    method, route, body, token, session = transport.calls[0]
    assert (method, token, session) == ("POST", TOKEN, SESSION)
    digest = hashlib.sha256(canonical_activity_json(envelope)).hexdigest()
    assert json.loads(body) == {"delivery_id": READ["delivery_id"], "delivery_sha256": digest}
    # A malformed answer is the control plane's fault, never a reason to decline.
    for changes in (
        {"extra": 1},
        {"request_sha256": "X" * 64},
        {"deadline_ms": -1},
        {"envelope": json.dumps(READ)},  # not its canonical bytes
        {"input_schema": []},
    ):
        transport.answers["/activity-artifacts/records"] = records_answer(**changes)
        with pytest.raises(FleetError, match="transport_failed"):
            client.records(envelope)
    # A refusal passes through, so the worker can tell a bad delivery from an outage.
    transport.answers["/activity-artifacts/records"] = FleetError("not_found")
    with pytest.raises(FleetError, match="not_found"):
        client.records(envelope)


def test_agent_declarations_are_typed_and_rounds_bounded_by_the_runtime():
    from lotuspond_sdk.activity import validate_activity

    from lotuspond_runtime.context import ContextRef

    module = client_module()
    transport = Answers({"/activity-artifacts/agent": agent_answer(max_steps=1000)})
    client = module.ActivityClient(transport, TOKEN, SESSION)
    agent = client.agent(validate_activity(READ))
    assert agent.model.name == "model-fixture" and agent.context == (ContextRef(ATTEMPT, 2),)
    assert [tool.name for tool in agent.tools] == ["connector-fixture-read"]
    assert agent.max_steps == 64  # this runtime never runs more rounds, whatever is declared
    for changes in (
        {"tools": [{**agent_answer()["tools"][0], "name": "another-name"}]},
        {"context": [{"source_id": "not-a-uuid", "version": 1}]},
        {"context": [{"source_id": ATTEMPT, "version": 0}]},
        {"max_steps": 0},
        {"model": CONNECTOR},
        {"unexpected": True},
    ):
        transport.answers["/activity-artifacts/agent"] = agent_answer(**changes)
        with pytest.raises(FleetError, match="transport_failed"):
            client.agent(validate_activity(READ))


def test_input_and_context_are_read_for_one_held_attempt():
    from lotuspond_runtime.context import ContextRef

    module = client_module()
    content = b'{"input":null,"target":null}'
    handle = dict(HANDLE, sha256=hashlib.sha256(content).hexdigest())
    transport = Answers(
        {
            f"/activity-artifacts/{ATTEMPT}/input": content,
            f"/activity-artifacts/{ATTEMPT}/context": b'{"fact":"synthetic"}',
        }
    )
    client = module.ActivityClient(transport, TOKEN, SESSION)
    sources = module.ControlPlaneSources(client)
    assert sources.fetch(ATTEMPT, handle) == content
    assert json.loads(transport.calls[0][2]) == handle
    assert sources.read(ATTEMPT, ContextRef(ATTEMPT, 3)) == {"fact": "synthetic"}
    assert json.loads(transport.calls[1][2]) == {"source_id": ATTEMPT, "version": 3}
    # Bytes that are not the named input are never returned.
    with pytest.raises(FleetError, match="transport_failed"):
        client.input(ATTEMPT, dict(handle, sha256="c" * 64))
    for call in (
        lambda: client.input("not-a-uuid", handle),
        lambda: client.context(ATTEMPT, ("source", 1)),
    ):
        with pytest.raises(FleetError, match="invalid_request"):
            call()


def test_transport_serves_the_worker_reads_with_their_own_bounds(tmp_path):
    transport = importlib.import_module("lotuspond_runtime.control_transport")
    reads = (
        "/activity-artifacts/records",
        "/activity-artifacts/agent",
        f"/activity-artifacts/{ATTEMPT}/input",
        f"/activity-artifacts/{ATTEMPT}/context",
    )
    assert transport.response_bound("/activities/poll") == 131072
    assert {transport.response_bound(route) for route in reads} == {1048576}
    assert transport.response_bound("/activities/claim") == 65536
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nnull"
    with tls_server(tmp_path, response) as (origin, ca, _):
        t = transport.ControlTransport(origin, ca)
        for route in reads:
            with pytest.raises(FleetError, match="invalid_request"):
                t.request("POST", route, b"{}", TOKEN)  # no session
        for route in (
            "/activity-artifacts/other",
            "/activity-artifacts/ABC/input",
            f"/activity-artifacts/{ATTEMPT}/values",
            f"/activity-artifacts/{ATTEMPT}/input/extra",
        ):
            with pytest.raises(FleetError, match="invalid_request"):
                t.request("POST", route, b"{}", TOKEN, SESSION)
        assert t.request("POST", reads[0], b"{}", TOKEN, SESSION) == b"null"
