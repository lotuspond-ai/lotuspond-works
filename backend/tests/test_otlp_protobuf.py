"""The OTLP protobuf decoder, checked against bytes from the official implementation.

GOLDEN_REQUEST and GOLDEN_RESPONSE were serialized by opentelemetry-proto 1.44.0 (protobuf
7.36.2) from the messages described beside them, so these tests compare the decoder with the
reference encoder rather than with an encoder written alongside it.
"""

from __future__ import annotations

import gzip
import random
import struct

import pytest
from sqlalchemy import select

from lotuspond.models import AuditEvent
from lotuspond.otlp_protobuf import (
    MAX_VALUE_DEPTH,
    ProtobufError,
    decode_trace_request,
    encode_trace_response,
)

# ExportTraceServiceRequest with one resource (service.name=agent, api_key=secret), scope
# lotuspond.tests 1.0 and one CLIENT span "model.call" carrying every AnyValue kind, an event, a
# link, an ERROR status and flags 257, under schema URL .../schemas/1.26.0.
GOLDEN_REQUEST = bytes.fromhex(
    "0ab4030a2e0a170a0c736572766963652e6e616d6512070a056167656e740a130a076170695f6b657912080a"
    "0673656372657412d8020a160a0f6c6f747573706f6e642e74657374731203312e3012bd020a100102030405"
    "060708090a0b0c0d0e0f10120811121314151617181a0c76656e646f723d76616c7565220821222324252627"
    "282a0a6d6f64656c2e63616c6c30033900002a36fe9c97174180b21045fe9c97174a1f0a126c6f747573706f"
    "6e642e636f73745f7573641209219a9999999999893f4a150a06746f6b656e73120b18d6ffffffffffffffff"
    "014a0e0a0873747265616d6564120210014a130a0474616773120b2a090a030a01610a0218074a1f0a066e65"
    "73746564121532130a110a05696e6e657212080a06c3a420e29c934a0e0a0372617712073a0500ff62696e5a"
    "1f0900e11f3cfe9c9717120572657472791a0d0a07617474656d7074120218026a1c0a100000000000000000"
    "0000000000000000120800000000000000007a141210757073747265616d2074696d656f7574180285010101"
    "00001a2768747470733a2f2f6f70656e74656c656d657472792e696f2f736368656d61732f312e32362e30"
)
# ExportTraceServiceResponse{partial_success{rejected_spans: 3, error_message: "3 spans were
# rejected"}}.
GOLDEN_RESPONSE = bytes.fromhex("0a190803121533207370616e7320776572652072656a6563746564")


def varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append(value & 0x7F | 0x80)
        value >>= 7
    return bytes(encoded + bytes([value]))


def field(number: int, payload: bytes | int) -> bytes:
    """A varint field for an int, a length-delimited field for bytes."""
    if isinstance(payload, int):
        return varint(number << 3) + varint(payload)
    return varint(number << 3 | 2) + varint(len(payload)) + payload


def fixed(number: int, payload: bytes) -> bytes:
    """A fixed64 or fixed32 field, which carries no length prefix."""
    return varint(number << 3 | (1 if len(payload) == 8 else 5)) + payload


def request(*spans: bytes, resource: bytes = b"") -> bytes:
    scope_spans = b"".join(field(2, span) for span in spans)
    return field(1, resource + field(2, scope_spans))


def span(**extra: bytes) -> bytes:
    body = field(1, bytes(range(16))) + field(2, bytes(range(8))) + field(5, b"call")
    return body + b"".join(extra.values())


def test_the_official_export_decodes_to_the_otlp_json_form() -> None:
    assert decode_trace_request(GOLDEN_REQUEST) == {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "agent"}},
                        {"key": "api_key", "value": {"stringValue": "secret"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "lotuspond.tests", "version": "1.0"},
                        "spans": [
                            {
                                # OTLP/JSON writes identifiers in hex, not base64.
                                "traceId": "0102030405060708090a0b0c0d0e0f10",
                                "spanId": "1112131415161718",
                                "traceState": "vendor=value",
                                "parentSpanId": "2122232425262728",
                                "flags": 257,
                                "name": "model.call",
                                "kind": 3,
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000000250000000",
                                "attributes": [
                                    {"key": "lotuspond.cost_usd", "value": {"doubleValue": 0.0125}},
                                    {"key": "tokens", "value": {"intValue": "-42"}},
                                    {"key": "streamed", "value": {"boolValue": True}},
                                    {
                                        "key": "tags",
                                        "value": {
                                            "arrayValue": {
                                                "values": [{"stringValue": "a"}, {"intValue": "7"}]
                                            }
                                        },
                                    },
                                    {
                                        "key": "nested",
                                        "value": {
                                            "kvlistValue": {
                                                "values": [
                                                    {
                                                        "key": "inner",
                                                        "value": {"stringValue": "ä ✓"},
                                                    }
                                                ]
                                            }
                                        },
                                    },
                                    {"key": "raw", "value": {"bytesValue": "AP9iaW4="}},
                                ],
                                "status": {"message": "upstream timeout", "code": 2},
                            }
                        ],
                    }
                ],
                "schemaUrl": "https://opentelemetry.io/schemas/1.26.0",
            }
        ]
    }


def test_the_response_matches_the_official_encoding() -> None:
    assert encode_trace_response(3, "3 spans were rejected") == GOLDEN_RESPONSE
    # Full success is an empty ExportTraceServiceResponse.
    assert encode_trace_response(0) == b""
    assert decode_trace_request(b"") == {"resourceSpans": []}


def test_unknown_fields_are_skipped_at_every_level() -> None:
    plain = request(span())
    unknown = field(99, 7) + field(98, b"future")
    extended = field(1, field(2, field(2, span(unknown=unknown)) + unknown) + unknown) + unknown
    assert decode_trace_request(extended) == decode_trace_request(plain)


def test_a_scalar_at_its_default_is_left_out_as_the_json_form_leaves_it_out() -> None:
    decoded = decode_trace_request(request(span(kind=field(6, 0), start=fixed(7, bytes(8)))))
    only = decoded["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    # No timestamps: ingestion rejects this span exactly as it rejects the same JSON span.
    assert only == {
        "traceId": bytes(range(16)).hex(),
        "spanId": bytes(range(8)).hex(),
        "name": "call",
    }


def test_a_repeated_singular_message_merges_as_protobuf_defines() -> None:
    first = field(1, field(1, b"service.name") + field(2, field(1, b"agent")))
    second = field(1, field(1, b"region") + field(2, field(1, b"eu")))
    decoded = decode_trace_request(request(span(), resource=field(1, first) + field(1, second)))
    keys = [item["key"] for item in decoded["resourceSpans"][0]["resource"]["attributes"]]
    assert keys == ["service.name", "region"]


def test_extreme_numbers_keep_their_exact_value() -> None:
    def value_of(any_value: bytes) -> dict[str, object]:
        attribute = field(9, field(1, b"k") + field(2, any_value))
        decoded = decode_trace_request(request(span(attribute=attribute)))
        return decoded["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"][0]["value"]

    assert value_of(field(3, (1 << 64) - 1)) == {"intValue": "-1"}
    assert value_of(field(3, 1 << 63)) == {"intValue": str(-(1 << 63))}
    assert value_of(field(3, 0)) == {"intValue": "0"}
    assert value_of(field(2, 0)) == {"boolValue": False}
    for number, text in (
        (float("nan"), "NaN"),
        (float("inf"), "Infinity"),
        (float("-inf"), "-Infinity"),
    ):
        assert value_of(fixed(4, struct.pack("<d", number))) == {"doubleValue": text}
    latest = fixed(8, struct.pack("<Q", (1 << 64) - 1))
    decoded = decode_trace_request(request(span(end=latest)))
    assert decoded["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["endTimeUnixNano"] == str(
        (1 << 64) - 1
    )


def test_nesting_is_bounded_before_it_can_exhaust_the_stack() -> None:
    def nested(levels: int) -> bytes:
        value = field(1, b"leaf")
        for _ in range(levels):
            value = field(5, field(1, value))
        return request(span(attribute=field(9, field(1, b"deep") + field(2, value))))

    decode_trace_request(nested(MAX_VALUE_DEPTH - 2))
    with pytest.raises(ProtobufError):
        decode_trace_request(nested(MAX_VALUE_DEPTH + 1))
    with pytest.raises(ProtobufError):
        decode_trace_request(nested(5_000))


def test_mutated_exports_decode_or_are_refused_but_never_fail_otherwise() -> None:
    # A seeded slice of a longer local mutation run: a hostile body may only be decoded or
    # refused as invalid, never raise anything a caller would not expect.
    rng = random.Random(20260923)
    for _ in range(3_000):
        body = bytearray(GOLDEN_REQUEST)
        for _ in range(rng.randint(1, 6)):
            position = rng.randrange(len(body) + 1)
            choice = rng.random()
            if choice < 0.4 and position < len(body):
                body[position] = rng.randrange(256)
            elif choice < 0.7:
                body.insert(position, rng.randrange(256))
            elif position < len(body):
                del body[position]
        try:
            decode_trace_request(bytes(body))
        except ProtobufError:
            pass


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"\x0a", id="truncated-length"),
        pytest.param(b"\x0a\x05abc", id="length-past-the-end"),
        pytest.param(b"\x0b", id="group-wire-type"),
        pytest.param(b"\x0f", id="undefined-wire-type"),
        pytest.param(b"\x00\x01", id="field-number-zero"),
        pytest.param(b"\x08\x01", id="message-sent-as-varint"),
        pytest.param(b"\x10" + b"\xff" * 10 + b"\x01", id="eleven-byte-varint"),
        pytest.param(b"\x10\xff", id="truncated-varint"),
        pytest.param(request(span(name=field(5, b"\xff\xfe"))), id="name-not-utf8"),
        pytest.param(request(span(start=varint(7 << 3 | 1) + b"\x01\x02\x03")), id="short-fixed64"),
        pytest.param(request(span(flags=varint(16 << 3 | 5) + b"\x01")), id="short-fixed32"),
        pytest.param(field(1, field(2, field(2, b"\x2a\x03ab"))), id="truncated-inside-a-span"),
    ],
)
def test_malformed_bodies_are_refused(body: bytes) -> None:
    with pytest.raises(ProtobufError):
        decode_trace_request(body)


# The endpoint, driven the way a default OpenTelemetry exporter drives it.

PROTOBUF = {"content-type": "application/x-protobuf"}


def export(client, project_id: str, body: bytes, **headers: str):
    return client.post(
        f"/api/v1/telemetry/otlp?project_id={project_id}",
        content=body,
        headers={**PROTOBUF, **headers},
    )


def timed_span(**extra: bytes) -> bytes:
    start = fixed(7, struct.pack("<Q", 1_700_000_000_000_000_000))
    end = fixed(8, struct.pack("<Q", 1_700_000_000_050_000_000))
    return span(start=start, end=end, **extra)


def test_a_protobuf_export_is_stored_exactly_as_its_json_form_is(client, account, db_factory):
    project = client.post("/api/v1/projects", json={"name": "OTLP encodings"}).json()
    binary = export(client, project["id"], GOLDEN_REQUEST)
    assert binary.status_code == 200, binary.text
    assert binary.headers["content-type"] == "application/x-protobuf"
    assert binary.content == b""  # a complete success is an empty response

    # The same span as a JSON exporter writes it, under a different span id.
    attributes = [
        {"key": "lotuspond.cost_usd", "value": {"doubleValue": 0.0125}},
        {"key": "tokens", "value": {"intValue": "-42"}},
        {"key": "streamed", "value": {"boolValue": True}},
        {
            "key": "tags",
            "value": {"arrayValue": {"values": [{"stringValue": "a"}, {"intValue": "7"}]}},
        },
        {
            "key": "nested",
            "value": {
                "kvlistValue": {"values": [{"key": "inner", "value": {"stringValue": "ä ✓"}}]}
            },
        },
        {"key": "raw", "value": {"bytesValue": "AP9iaW4="}},
    ]
    resource = [
        {"key": "service.name", "value": {"stringValue": "agent"}},
        {"key": "api_key", "value": {"stringValue": "secret"}},
    ]
    textual = client.post(
        f"/api/v1/telemetry/otlp?project_id={project['id']}",
        json={
            "resourceSpans": [
                {
                    "resource": {"attributes": resource},
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "0102030405060708090a0b0c0d0e0f10",
                                    "spanId": "3132333435363738",
                                    "name": "model.call",
                                    "kind": 3,
                                    "startTimeUnixNano": "1700000000000000000",
                                    "endTimeUnixNano": "1700000000250000000",
                                    "attributes": attributes,
                                    "status": {"message": "upstream timeout", "code": 2},
                                }
                            ]
                        }
                    ],
                }
            ]
        },
    )
    assert textual.status_code == 202, textual.text
    assert textual.json()["accepted_spans"] == 1

    items = client.get(f"/api/v1/traces?project_id={project['id']}").json()["items"]
    by_span = {item["attributes"]["otel"]["span_id"]: item for item in items}
    from_binary, from_json = by_span["1112131415161718"], by_span["3132333435363738"]
    for key in ("name", "status", "latency_ms", "cost_usd", "cost_measurement", "occurred_at"):
        assert from_binary[key] == from_json[key], key
    for key in ("trace_id", "kind", "resource", "span"):
        assert from_binary["attributes"]["otel"][key] == from_json["attributes"]["otel"][key], key
    assert from_binary["status"] == "error"
    assert from_binary["latency_ms"] == 250
    assert from_binary["cost_usd"] == 0.0125
    assert from_binary["attributes"]["otel"]["resource"]["api_key"] == "[redacted]"

    with db_factory() as db:
        encodings = [
            event.details["encoding"]
            for event in db.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "telemetry.otlp_ingested")
                .order_by(AuditEvent.created_at)
            )
        ]
    assert encodings == ["protobuf", "json"]


def test_a_protobuf_export_reports_the_spans_it_did_not_keep(client, account):
    project = client.post("/api/v1/projects", json={"name": "Partial"}).json()
    short_id = field(1, bytes(8)) + field(2, bytes(range(8))) + field(5, b"short trace id")
    response = export(client, project["id"], request(timed_span(), short_id))
    assert response.status_code == 200, response.text
    assert response.content == encode_trace_response(
        1, "Spans not stored: 1 malformed or conflicting with a recorded span."
    )
    items = client.get(f"/api/v1/traces?project_id={project['id']}").json()["items"]
    assert [item["name"] for item in items] == ["call"]


def test_a_gzipped_protobuf_export_is_accepted(client, account):
    project = client.post("/api/v1/projects", json={"name": "Compressed"}).json()
    response = export(
        client,
        project["id"],
        gzip.compress(request(timed_span())),
        **{"content-encoding": "gzip", "content-type": "application/x-protobuf; charset=binary"},
    )
    assert response.status_code == 200, response.text
    assert response.content == b""


def test_a_malformed_or_empty_protobuf_export_is_refused(client, account):
    project = client.post("/api/v1/projects", json={"name": "Refused"}).json()
    malformed = export(client, project["id"], b"\x0a\x05abc")
    assert malformed.status_code == 400
    assert malformed.json() == {
        "error": "invalid_body",
        "message": "The request body is not a valid OTLP protobuf trace export.",
    }
    empty = export(client, project["id"], b"")
    assert empty.status_code == 422
    assert empty.json()["error"] == "invalid_otlp"


def test_protobuf_is_accepted_only_where_trace_exports_arrive(client, account):
    # Every other mutating API route still takes JSON alone.
    refused = client.post("/api/v1/projects", content=b"\x0a\x00", headers=PROTOBUF)
    assert refused.status_code == 415
    assert refused.json()["error"] == "unsupported_media_type"
