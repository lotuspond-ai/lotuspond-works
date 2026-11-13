"""The `https-json` connector's read side against a synthetic local TLS server."""

import json
from types import SimpleNamespace

import pytest
import rfc8785
from lotuspond_sdk.executable.package import ArtifactRef
from tls_fixtures import tls_server

from lotuspond_runtime.https_json import HttpsJsonReadProvider, routes_digest, validate_routes
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


def test_routes_must_hash_to_the_pinned_artifact_and_only_read(tmp_path):
    changed = routes()
    changed["operations"]["get_invoice"]["path"] = "/other/{invoice_id}"
    with pytest.raises(ValueError):
        validate_routes(changed, pin())  # same pin, different routes
    writing = routes()
    writing["operations"]["get_invoice"]["method"] = "POST"
    with pytest.raises(ValueError):
        validate_routes(writing, pin(writing))
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
