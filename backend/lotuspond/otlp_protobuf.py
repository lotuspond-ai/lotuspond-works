"""OTLP/HTTP binary trace exports, decoded into the OTLP/JSON shape.

Lotuspond ingests one shape: the OTLP/JSON mapping of ``ExportTraceServiceRequest``. Most
OpenTelemetry SDKs export ``application/x-protobuf`` by default, so a binary export is decoded
here into the canonical form a JSON exporter would send for the same spans -- hex trace and span
identifiers, integer enums, 64-bit integers as decimal strings, bytes as base64, and scalars at
their default value left out -- and a span is judged and stored the same way whichever encoding
its exporter chose.

The decoder reads the protobuf wire format against the stable opentelemetry-proto v1 trace
schema and adds no dependency. As protobuf requires, unknown fields are skipped, and a repeated
occurrence of a singular message merges with the earlier ones. Every length is checked against
the bytes that remain, strings must be UTF-8, a known field must arrive with its declared wire
type, and attribute values may nest only ``MAX_VALUE_DEPTH`` levels, so a hostile body fails as
invalid instead of exhausting the stack. Span events and links are not retained by Lotuspond
(the JSON path ignores them too), so only their framing is checked.
"""

from __future__ import annotations

import base64
import math
import struct
from collections.abc import Iterator
from typing import cast

MAX_VALUE_DEPTH = 32

VARINT, I64, LEN, I32 = 0, 1, 2, 5
_MASK64 = (1 << 64) - 1

Raw = int | memoryview


class ProtobufError(ValueError):
    """The body is not a well-formed OTLP protobuf trace export."""


def _varint(data: memoryview, position: int) -> tuple[int, int]:
    result = 0
    for index in range(10):
        if position >= len(data):
            raise ProtobufError("truncated varint")
        byte = data[position]
        position += 1
        result |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            return result & _MASK64, position
    raise ProtobufError("varint longer than ten bytes")


def _fields(data: memoryview) -> Iterator[tuple[int, int, Raw]]:
    position = 0
    while position < len(data):
        key, position = _varint(data, position)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ProtobufError("field number zero")
        value: Raw
        if wire == VARINT:
            value, position = _varint(data, position)
        elif wire in (I64, I32):
            size = 8 if wire == I64 else 4
            if size > len(data) - position:
                raise ProtobufError("truncated fixed-width field")
            value = data[position : position + size]
            position += size
        elif wire == LEN:
            size, position = _varint(data, position)
            if size > len(data) - position:
                raise ProtobufError("length exceeds the remaining body")
            value = data[position : position + size]
            position += size
        else:
            # Groups (3, 4) are long deprecated and absent from OTLP; 6 and 7 are undefined.
            raise ProtobufError("unsupported wire type")
        yield number, wire, value


def _message(data: memoryview, schema: dict[int, int]) -> Iterator[tuple[int, Raw]]:
    """Known fields with their declared wire type; anything else is skipped as unknown."""
    for number, wire, value in _fields(data):
        expected = schema.get(number)
        if expected is None:
            continue
        if wire != expected:
            raise ProtobufError("field has the wrong wire type")
        yield number, value


# `_message` has already matched each field to its wire type, so these only narrow the type.
def _view(raw: Raw) -> memoryview:
    return cast(memoryview, raw)


def _number(raw: Raw) -> int:
    return cast(int, raw)


def _text(raw: Raw) -> str:
    try:
        return str(_view(raw), "utf-8")
    except UnicodeDecodeError as error:
        raise ProtobufError("string field is not UTF-8") from error


def _fixed64(raw: Raw) -> int:
    return cast(int, struct.unpack("<Q", _view(raw))[0])


def _double(raw: Raw) -> float | str:
    (value,) = struct.unpack("<d", _view(raw))
    if math.isfinite(value):
        return cast(float, value)
    # JSON has no NaN or infinity; the protobuf JSON mapping spells them as strings.
    return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"


def _merged(chunks: list[memoryview]) -> memoryview:
    # A repeated singular message is the merge of its occurrences, which is exactly what
    # parsing their concatenation produces.
    return chunks[0] if len(chunks) == 1 else memoryview(b"".join(bytes(c) for c in chunks))


def _set(target: dict[str, object], key: str, value: object) -> None:
    # The canonical JSON form leaves out a scalar that holds its default value.
    if value not in (0, "", False):
        target[key] = value


def _any_value(data: memoryview, depth: int) -> dict[str, object]:
    if depth > MAX_VALUE_DEPTH:
        raise ProtobufError("attribute values nest too deeply")
    value: dict[str, object] = {}
    # A oneof: the member set last on the wire wins, and a member is kept even at its default.
    for number, raw in _message(
        data, {1: LEN, 2: VARINT, 3: VARINT, 4: I64, 5: LEN, 6: LEN, 7: LEN}
    ):
        if number == 1:
            value = {"stringValue": _text(raw)}
        elif number == 2:
            value = {"boolValue": _number(raw) != 0}
        elif number == 3:
            signed = _number(raw)
            value = {"intValue": str(signed - (1 << 64) if signed >> 63 else signed)}
        elif number == 4:
            value = {"doubleValue": _double(raw)}
        elif number == 5:
            items = [
                _any_value(_view(item), depth + 1) for _, item in _message(_view(raw), {1: LEN})
            ]
            value = {"arrayValue": {"values": items}}
        elif number == 6:
            pairs = [
                _key_value(_view(item), depth + 1) for _, item in _message(_view(raw), {1: LEN})
            ]
            value = {"kvlistValue": {"values": pairs}}
        else:
            value = {"bytesValue": base64.b64encode(_view(raw)).decode("ascii")}
    return value


def _key_value(data: memoryview, depth: int = 0) -> dict[str, object]:
    pair: dict[str, object] = {}
    values: list[memoryview] = []
    for number, raw in _message(data, {1: LEN, 2: LEN}):
        if number == 1:
            _set(pair, "key", _text(raw))
        else:
            values.append(_view(raw))
    if values:
        pair["value"] = _any_value(_merged(values), depth + 1)
    return pair


def _status(data: memoryview) -> dict[str, object]:
    status: dict[str, object] = {}
    for number, raw in _message(data, {2: LEN, 3: VARINT}):
        if number == 2:
            _set(status, "message", _text(raw))
        else:
            _set(status, "code", _number(raw))
    return status


_SPAN = {
    1: LEN, 2: LEN, 3: LEN, 4: LEN, 5: LEN, 6: VARINT, 7: I64, 8: I64, 9: LEN,
    10: VARINT, 11: LEN, 12: VARINT, 13: LEN, 14: VARINT, 15: LEN, 16: I32,
}  # fmt: skip


def _span(data: memoryview) -> dict[str, object]:
    span: dict[str, object] = {}
    attributes: list[dict[str, object]] = []
    status: list[memoryview] = []
    for number, raw in _message(data, _SPAN):
        if number == 1:
            _set(span, "traceId", _view(raw).hex())
        elif number == 2:
            _set(span, "spanId", _view(raw).hex())
        elif number == 3:
            _set(span, "traceState", _text(raw))
        elif number == 4:
            _set(span, "parentSpanId", _view(raw).hex())
        elif number == 5:
            _set(span, "name", _text(raw))
        elif number == 6:
            _set(span, "kind", _number(raw))
        elif number == 7:
            # 64-bit integers are decimal strings in OTLP/JSON, so no precision is lost.
            if nanos := _fixed64(raw):
                span["startTimeUnixNano"] = str(nanos)
        elif number == 8:
            if nanos := _fixed64(raw):
                span["endTimeUnixNano"] = str(nanos)
        elif number == 9:
            attributes.append(_key_value(_view(raw)))
        elif number == 10:
            _set(span, "droppedAttributesCount", _number(raw))
        elif number == 15:
            status.append(_view(raw))
        elif number == 16:
            _set(span, "flags", struct.unpack("<I", _view(raw))[0])
        # 11 events, 13 links and their dropped counts (12, 14) are not retained.
    if attributes:
        span["attributes"] = attributes
    if status:
        span["status"] = _status(_merged(status))
    return span


def _scope(data: memoryview) -> dict[str, object]:
    scope: dict[str, object] = {}
    attributes: list[dict[str, object]] = []
    for number, raw in _message(data, {1: LEN, 2: LEN, 3: LEN, 4: VARINT}):
        if number == 1:
            _set(scope, "name", _text(raw))
        elif number == 2:
            _set(scope, "version", _text(raw))
        elif number == 3:
            attributes.append(_key_value(_view(raw)))
        else:
            _set(scope, "droppedAttributesCount", _number(raw))
    if attributes:
        scope["attributes"] = attributes
    return scope


def _resource(data: memoryview) -> dict[str, object]:
    resource: dict[str, object] = {}
    attributes: list[dict[str, object]] = []
    for number, raw in _message(data, {1: LEN, 2: VARINT}):
        if number == 1:
            attributes.append(_key_value(_view(raw)))
        else:
            _set(resource, "droppedAttributesCount", _number(raw))
    if attributes:
        resource["attributes"] = attributes
    return resource


def _scope_spans(data: memoryview) -> dict[str, object]:
    entry: dict[str, object] = {}
    scope: list[memoryview] = []
    spans: list[dict[str, object]] = []
    for number, raw in _message(data, {1: LEN, 2: LEN, 3: LEN}):
        if number == 1:
            scope.append(_view(raw))
        elif number == 2:
            spans.append(_span(_view(raw)))
        else:
            _set(entry, "schemaUrl", _text(raw))
    if scope:
        entry["scope"] = _scope(_merged(scope))
    entry["spans"] = spans
    return entry


def _resource_spans(data: memoryview) -> dict[str, object]:
    entry: dict[str, object] = {}
    resource: list[memoryview] = []
    scopes: list[dict[str, object]] = []
    for number, raw in _message(data, {1: LEN, 2: LEN, 3: LEN}):
        if number == 1:
            resource.append(_view(raw))
        elif number == 2:
            scopes.append(_scope_spans(_view(raw)))
        else:
            _set(entry, "schemaUrl", _text(raw))
    if resource:
        entry["resource"] = _resource(_merged(resource))
    entry["scopeSpans"] = scopes
    return entry


def decode_trace_request(body: bytes) -> dict[str, object]:
    """Decode an ``ExportTraceServiceRequest`` into its OTLP/JSON form, or raise."""
    return {
        "resourceSpans": [
            _resource_spans(_view(raw)) for _, raw in _message(memoryview(body), {1: LEN})
        ]
    }


def _encode_varint(value: int) -> bytes:
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if not value:
            encoded.append(byte)
            return bytes(encoded)
        encoded.append(byte | 0x80)


def _encode_field(number: int, wire: int, payload: bytes | int) -> bytes:
    key = _encode_varint(number << 3 | wire)
    if isinstance(payload, int):
        return key + _encode_varint(payload)
    return key + _encode_varint(len(payload)) + payload


def encode_trace_response(rejected_spans: int, error_message: str = "") -> bytes:
    """An ``ExportTraceServiceResponse``: empty for full success, else a partial success."""
    partial = b""
    if rejected_spans > 0:
        partial += _encode_field(1, VARINT, rejected_spans)
    if error_message:
        partial += _encode_field(2, LEN, error_message.encode("utf-8"))
    return _encode_field(1, LEN, partial) if partial else b""
