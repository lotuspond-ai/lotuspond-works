"""The `https-json` connector against a synthetic local TLS server: reads and writes."""

import json
from types import SimpleNamespace

import pytest
import rfc8785
from lotuspond_sdk.executable.package import ArtifactRef
from tls_fixtures import tls_server

from lotuspond_runtime.https_json import (
    HttpsJsonReadProvider,
    HttpsJsonWriteProvider,
    routes_digest,
    validate_routes,
)
from lotuspond_runtime.protocol import (
    ConnectionConfig,
    ConnectorPin,
    DataHandling,
    DestinationRule,
    SecretPin,
)
from lotuspond_runtime.providers import ActivityFailure, ProviderDescriptor

SECRET = SecretPin(reference_id="11111111-1111-4111-8111-111111111111", version=1)


def routes():
    return {
        "format": "lotuspond.https-json.routes.v1",
        "connector": {"name": "ap-standin", "version": "0.1.0"},
        "operations": {"get_invoice": {"method": "GET", "path": "/invoices/{invoice_id}"}},
    }


def pin(document=None):
    return ConnectorPin(
        name="ap-standin", version="0.1.0", artifact_sha256=routes_digest(document or routes())
    )


def descriptor(ca, *, document=None, loopback=True):
    return ProviderDescriptor(
        "https-json",
        ArtifactRef(kind="connector", name="ap-standin", version="0.1.0", sha256="a" * 64),
        "read",
        frozenset({"get_invoice"}),
        connector=pin(document),
        settings={
            "routes": document or routes(),
            "ca_file": str(ca),
            "egress": {"allowed_private_cidrs": ["127.0.0.0/8", "::1/128"]},
            "test_loopback": loopback,
        },
    )


def records(origin, operation="get_invoice"):
    port = int(origin.rsplit(":", 1)[1])
    connection = ConnectionConfig(
        connector=pin(),
        operations=(operation,),
        destinations=(DestinationRule(host="localhost", port=port, path_prefix="/"),),
        secrets=(SECRET,),
        data_handling=DataHandling(timeout_ms=5000),
    )
    return SimpleNamespace(envelope=SimpleNamespace(operation=operation), connection=connection)


def ok(value):
    body = json.dumps(value).encode()
    return b"HTTP/1.0 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body)


def test_a_read_sends_a_bearer_get_to_the_route_and_returns_json(tmp_path):
    invoice = {"invoice_id": "INV-1", "total": "1.00"}
    with tls_server(tmp_path, ok(invoice)) as (origin, ca, requests):
        provider = HttpsJsonReadProvider(descriptor(ca), (b"synthetic-token",), test_mode=True)
        result = provider.invoke(
            records(origin), {"target": {"invoice_id": "INV-1"}, "input": None}, 0
        )
    assert result.value == invoice
    assert (result.usage.input_tokens, result.usage.cost_microusd) == (0, 0)
    request = requests[0].decode()
    assert request.startswith("GET /invoices/INV-1 HTTP/1.1\r\n")
    assert "\r\nAuthorization: Bearer synthetic-token\r\n" in request


@pytest.mark.parametrize("value", ["../etc", "INV 1", "", "INV-1/x", 7])
def test_a_target_value_that_is_not_one_plain_segment_is_never_sent(tmp_path, value):
    with tls_server(tmp_path, ok({})) as (origin, ca, requests):
        provider = HttpsJsonReadProvider(descriptor(ca), (b"t",), test_mode=True)
        with pytest.raises(ActivityFailure) as refused:
            provider.invoke(records(origin), {"target": {"invoice_id": value}, "input": None}, 0)
    assert (refused.value.code, refused.value.reached) == ("provider_failed", False)
    assert requests == []


def test_loopback_needs_a_test_mode_worker(tmp_path):
    with tls_server(tmp_path, ok({})) as (origin, ca, requests):
        provider = HttpsJsonReadProvider(descriptor(ca), (b"t",), test_mode=False)
        with pytest.raises(ActivityFailure) as refused:
            provider.invoke(records(origin), {"target": {"invoice_id": "INV-1"}, "input": None}, 0)
    assert (refused.value.code, refused.value.reached) == ("unavailable", False)
    assert requests == []


def test_an_error_status_after_sending_is_a_provider_failure(tmp_path):
    reply = b"HTTP/1.0 404 Not Found\r\nContent-Length: 2\r\n\r\n{}"
    with tls_server(tmp_path, reply) as (origin, ca, requests):
        provider = HttpsJsonReadProvider(descriptor(ca), (b"t",), test_mode=True)
        with pytest.raises(ActivityFailure) as refused:
            provider.invoke(records(origin), {"target": {"invoice_id": "INV-1"}, "input": None}, 0)
    assert refused.value.code == "provider_failed" and refused.value.reached is None
    assert len(requests) == 1


def test_routes_must_hash_to_the_pinned_artifact_and_name_get_or_post(tmp_path):
    changed = routes()
    changed["operations"]["get_invoice"]["path"] = "/other/{invoice_id}"
    with pytest.raises(ValueError):
        validate_routes(changed, pin())  # same pin, different routes
    for bad in (
        {"method": "PUT", "path": "/x"},
        {"method": "POST", "path": "/x", "input": "ignored"},  # only a read-back ignores input
        {"method": "GET", "path": "/x", "input": "sent"},
    ):
        document = routes()
        document["operations"]["get_invoice"] = bad
        with pytest.raises(ValueError):
            validate_routes(document, pin(document))
    assert (
        routes_digest(routes()) == __import__("hashlib").sha256(rfc8785.dumps(routes())).hexdigest()
    )


def test_an_operation_outside_the_descriptor_is_unavailable(tmp_path):
    with tls_server(tmp_path, ok({})) as (origin, ca, requests):
        provider = HttpsJsonReadProvider(descriptor(ca), (b"t",), test_mode=True)
        with pytest.raises(ActivityFailure) as refused:
            provider.invoke(
                records(origin, "get_contract"), {"target": {"invoice_id": "I"}, "input": None}, 0
            )
    assert (refused.value.code, refused.value.reached) == ("unavailable", False)
    assert requests == []


# ------------------------------------------------------------------ the write side


def write_routes():
    document = routes()
    document["operations"].update(
        {
            "get_payment_hold": {
                "method": "GET",
                "path": "/invoices/{invoice_id}/payment-hold",
                "input": "ignored",
            },
            "set_payment_hold": {"method": "POST", "path": "/invoices/{invoice_id}/payment-hold"},
        }
    )
    return document


def write_descriptor(ca, *, document=None, operations=None):
    from lotuspond_runtime.action_invocation import WriteDescriptor

    document = document or write_routes()
    return WriteDescriptor(
        "https-json",
        pin(document),
        operations or {"set_payment_hold": "target_key"},
        settings={
            "routes": document,
            "ca_file": str(ca),
            "egress": {"allowed_private_cidrs": ["127.0.0.0/8", "::1/128"]},
            "test_loopback": True,
        },
    )


def write_records(origin, *, operation="set_payment_hold", target=None, value=None):
    port = int(origin.rsplit(":", 1)[1])
    connection = ConnectionConfig(
        connector=pin(write_routes()),
        operations=(operation, "get_payment_hold"),
        destinations=(DestinationRule(host="localhost", port=port, path_prefix="/"),),
        secrets=(SECRET,),
        data_handling=DataHandling(timeout_ms=5000),
    )
    return SimpleNamespace(
        intent=SimpleNamespace(binding=SimpleNamespace(operation=operation)),
        configuration=connection,
        actual_target=json.dumps(target or {"invoice_id": "INV-1"}),
        actual_input=json.dumps(value or {"reason": "short", "amount": "90.00"}),
    )


def test_a_write_posts_the_exact_input_with_its_idempotency_key(tmp_path):
    answer = b'{"reference": "HOLD-INV"}'
    reply = b"HTTP/1.0 201 Created\r\nContent-Length: %d\r\n\r\n%s" % (len(answer), answer)
    with tls_server(tmp_path, reply) as (origin, ca, requests):
        provider = HttpsJsonWriteProvider(write_descriptor(ca), (b"tok",), test_mode=True)
        result = provider.invoke(write_records(origin), "key-1", 0)
    assert (result.status, result.response) == ("accepted", {"reference": "HOLD-INV"})
    assert (result.usage.input_tokens, result.usage.cost_microusd) == (0, 0)
    request = requests[0]
    head, _, body = request.partition(b"\r\n\r\n")
    assert head.startswith(b"POST /invoices/INV-1/payment-hold HTTP/1.1\r\n")
    assert b"\r\nAuthorization: Bearer tok" in head
    assert b"\r\nIdempotency-Key: key-1" in head
    assert body == rfc8785.dumps({"reason": "short", "amount": "90.00"})


def test_a_sent_write_without_a_usable_answer_is_unknown_never_a_failure(tmp_path):
    for reply in (
        b"HTTP/1.0 500 Oops\r\nContent-Length: 2\r\n\r\n{}",
        b"HTTP/1.0 201 Created\r\nContent-Length: 3\r\n\r\nnot",
    ):
        with tls_server(tmp_path, reply) as (origin, ca, requests):
            provider = HttpsJsonWriteProvider(write_descriptor(ca), (b"tok",), test_mode=True)
            result = provider.invoke(write_records(origin), "key-2", 0)
        assert (result.status, result.response) == ("unknown", None)
        assert len(requests) == 1


def test_a_write_that_cannot_be_sent_is_unavailable_and_unsent(tmp_path):
    with tls_server(tmp_path, ok({})) as (origin, ca, requests):
        provider = HttpsJsonWriteProvider(write_descriptor(ca), (b"tok",), test_mode=False)
        with pytest.raises(ActivityFailure) as refused:  # loopback needs test mode
            provider.invoke(write_records(origin), "key-3", 0)
        assert (refused.value.code, refused.value.reached) == ("unavailable", False)
        provider = HttpsJsonWriteProvider(write_descriptor(ca), (b"tok",), test_mode=True)
        with pytest.raises(ActivityFailure) as keyless:  # a target_key write needs its key
            provider.invoke(write_records(origin), None, 0)
        assert (keyless.value.code, keyless.value.reached) == ("unavailable", False)
    assert requests == []


def test_a_write_provider_needs_a_post_route_for_each_write():
    with pytest.raises(ValueError):
        write_descriptor_for_get = write_descriptor(
            "/ca.pem", operations={"get_payment_hold": "target_key"}
        )
        HttpsJsonWriteProvider(write_descriptor_for_get, ())


def test_a_read_back_route_accepts_the_writes_input_and_reads_by_target(tmp_path):
    document = write_routes()
    hold = {"exists": True, "amount": "90.00"}
    with tls_server(tmp_path, ok(hold)) as (origin, ca, requests):
        read = ProviderDescriptor(
            "https-json",
            ArtifactRef(kind="connector", name="ap-standin", version="0.1.0", sha256="a" * 64),
            "read",
            frozenset({"get_invoice", "get_payment_hold"}),
            connector=pin(document),
            settings={
                "routes": document,
                "ca_file": str(ca),
                "egress": {"allowed_private_cidrs": ["127.0.0.0/8", "::1/128"]},
                "test_loopback": True,
            },
        )
        provider = HttpsJsonReadProvider(read, (b"tok",), test_mode=True)
        records_ = SimpleNamespace(
            envelope=SimpleNamespace(operation="get_payment_hold"),
            connection=write_records(origin).configuration,
        )
        value = provider.invoke(
            records_, {"target": {"invoice_id": "INV-1"}, "input": {"amount": "90.00"}}, 0
        ).value
        assert value == hold
        # A read whose route does not ignore input still refuses one, unsent.
        plain = SimpleNamespace(
            envelope=SimpleNamespace(operation="get_invoice"),
            connection=write_records(origin).configuration,
        )
        with pytest.raises(ActivityFailure) as refused:
            provider.invoke(plain, {"target": {"invoice_id": "INV-1"}, "input": {"x": 1}}, 0)
        assert (refused.value.code, refused.value.reached) == ("provider_failed", False)
    assert len(requests) == 1 and requests[0].startswith(
        b"GET /invoices/INV-1/payment-hold HTTP/1.1\r\n"
    )
