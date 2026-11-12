"""E05 Task 7: the action client's routes, bodies and answer checks."""

import hashlib
import json

import pytest
from action_env import FakeActionPlane, identity
from lotuspond_sdk.activity import UsageObservation
from lotuspond_sdk.executable.wire import ActionReceipt
from pydantic import SecretStr

from lotuspond_runtime.action_client import ActionClient, claim_of
from lotuspond_runtime.action_protocol import (
    ActionReport,
    ActionStop,
    decode_records,
    encode_records,
    validate_action_lease,
)
from lotuspond_runtime.commands import delivery_wire
from lotuspond_runtime.control_transport import ControlTransport, response_bound
from lotuspond_runtime.errors import FleetError

TOKEN = SecretStr("lpr_" + "A" * 43)
SESSION = SecretStr("lps_" + "A" * 43)


class Transport:
    def __init__(self, responses):
        self.responses, self.calls = dict(responses), []

    def request(self, method, route, body, token, session=None):
        self.calls.append((method, route, body, token, session))
        key = "values" if route.endswith("/values") else route
        response = self.responses[key]
        return response if type(response) is bytes else json.dumps(response).encode()

    def close(self):
        pass


def case():
    plane = FakeActionPlane(identity(), port=443)
    return plane, plane.action()


def lease_of(action, **changes):
    return {
        "attempt_id": action.attempt_id,
        "command_id": action.command.command_id,
        "ordinal": 1,
        "lease_revision": 1,
        "expires_at_ms": 1800000060000,
        "deadline_ms": 1800000300000,
        **changes,
    }


def usage():
    return UsageObservation(input_tokens=0, output_tokens=0, cost_microusd=0, completed_at_ms=1)


def test_routes_bodies_and_session_credentials():
    plane, action = case()
    wire = json.loads(delivery_wire(action.delivery))
    content = b'{"id":"record-1"}'
    handle = {"id": action.attempt_id, "version": 1, "sha256": hashlib.sha256(content).hexdigest()}
    transport = Transport(
        {
            "/actions/poll": wire,
            "/actions/claim": lease_of(action),
            "/actions/records": encode_records(action.records),
            "/actions/renew": lease_of(action, lease_revision=2, expires_at_ms=1800000065000),
            "values": handle,
            "/actions/receipts": {},
            "/actions/stops": {},
        }
    )
    client = ActionClient(transport, TOKEN, SESSION)
    delivery = client.poll()
    assert delivery == action.delivery
    lease = client.claim(delivery)
    assert lease.ordinal == 1 and lease.command_id == action.command.command_id
    assert client.records(delivery) == action.records
    assert client.renew(lease).lease_revision == 2
    assert client.store(action.attempt_id, content).sha256 == handle["sha256"]
    receipt = ActionReceipt(
        receipt_id=action.attempt_id,
        action_id=action.command.action_id,
        intent_sha256=action.command.intent_sha256,
        attempt=1,
        target_operation_ref="record-1",
        status="accepted",
        evidence_refs=[],
    )
    client.receipt(
        ActionReport(
            attempt_id=action.attempt_id,
            lease_revision=2,
            receipt=receipt,
            response=None,
            usage=usage(),
        )
    )
    client.stop(
        ActionStop(
            attempt_id=action.attempt_id, lease_revision=2, code="not_started", usage=usage()
        )
    )
    routes = [call[1] for call in transport.calls]
    assert routes == [
        "/actions/poll",
        "/actions/claim",
        "/actions/records",
        "/actions/renew",
        f"/actions/{action.attempt_id}/values",
        "/actions/receipts",
        "/actions/stops",
    ]
    assert all(call[3] is TOKEN and call[4] is SESSION for call in transport.calls)
    claim = json.loads(transport.calls[1][2])
    assert claim == {
        "command_id": action.command.command_id,
        "command_sha256": hashlib.sha256(action.delivery.command_bytes).hexdigest(),
    }


def test_answers_that_do_not_match_the_request_are_refused():
    plane, action = case()
    other = plane.action()
    bad = {
        "/actions/claim": lease_of(other),
        "/actions/renew": lease_of(action, ordinal=2),
        "/actions/receipts": {"ok": True},
    }
    client = ActionClient(Transport(bad), TOKEN, SESSION)
    with pytest.raises(FleetError):
        client.claim(action.delivery)
    lease = validate_action_lease(lease_of(action), now_ms=1800000000000)
    with pytest.raises(FleetError):
        client.renew(lease)
    stored = ActionClient(
        Transport({"values": {"id": action.attempt_id, "version": 1, "sha256": "0" * 64}}),
        TOKEN,
        SESSION,
    )
    with pytest.raises(FleetError):
        stored.store(action.attempt_id, b'{"a":1}')
    with pytest.raises(FleetError):
        stored.store(action.attempt_id, b'{ "a": 1 }')  # not canonical: never sent


def test_records_wire_round_trips_and_is_checked():
    from lotuspond_runtime.commands import validate_delivery

    plane, action = case()
    raw = encode_records(action.records)
    assert decode_records(raw) == action.records

    def check(records):
        return validate_delivery(
            action.delivery,
            identity=plane.identity,
            records=records,
            trust=plane.current(None),
            now_ms=plane.now_ms(),
        )

    assert check(decode_records(raw)) == action.command  # positive control
    # An input other than the pinned digest decodes, but E03's check refuses it.
    with pytest.raises(FleetError):
        check(decode_records({**raw, "actual_input": '{"amount":9}'}))
    for change in (
        {"extra": 1},
        {"secret_bindings": [{"reference_id": "x", "version": 1}]},
    ):
        with pytest.raises(FleetError):
            decode_records({**raw, **change})


def test_lease_bounds():
    _, action = case()
    now = 1800000000000
    assert validate_action_lease(lease_of(action), now_ms=now).lease_revision == 1
    for changes in (
        {"expires_at_ms": now},
        {"expires_at_ms": now + 60001, "deadline_ms": now + 90000},
        {"expires_at_ms": now + 30000, "deadline_ms": now + 20000},
    ):
        with pytest.raises(FleetError):
            validate_action_lease(lease_of(action, **changes), now_ms=now)


def test_claims_name_the_exact_command_bytes():
    _, action = case()
    assert (
        claim_of(action.delivery).command_sha256
        == hashlib.sha256(action.delivery.command_bytes).hexdigest()
    )
    altered = type(action.delivery)(
        action.delivery.command_bytes.replace(b'"record_type"', b' "record_type"'),
        action.delivery.signature_bytes,
        1,
    )
    with pytest.raises(FleetError):
        claim_of(altered)


def test_the_transport_allows_exactly_the_action_routes(tmp_path):
    transport = ControlTransport.__new__(ControlTransport)
    transport._closed = True  # refuses at the network step, after route checks
    import threading

    transport._serial = threading.Lock()
    for route in ("/actions/poll", "/actions/records", "/actions/stops"):
        with pytest.raises(FleetError) as error:
            transport.request("POST", route, b"{}", TOKEN, SESSION)
        assert error.value.code == "transport_failed"
    for route in ("/actions/delete", "/actions/x/values", "/actions/poll/"):
        with pytest.raises(FleetError) as error:
            transport.request("POST", route, b"{}", TOKEN, SESSION)
        assert error.value.code == "invalid_request"
    with pytest.raises(FleetError) as error:
        transport.request("POST", "/actions/poll", b"{}", TOKEN)  # a session is required
    assert error.value.code == "invalid_request"
    assert response_bound("/actions/records") == 1048576
    assert response_bound("/actions/poll") == 131072
