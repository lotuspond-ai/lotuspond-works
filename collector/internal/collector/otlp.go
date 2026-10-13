package collector

import (
	"errors"
	"unicode/utf8"
)

// OTLP/HTTP exporters send binary protobuf by default. The collector checks that a body is a
// well-formed ExportTraceServiceRequest against the stable opentelemetry-proto v1 trace schema
// and forwards the bytes unchanged; the API decodes them. Like the API it skips unknown
// fields, requires each known field's declared wire type and UTF-8 strings, and leaves span
// events and links unread beyond their framing. Its nesting bound is looser than the API's
// attribute-depth bound, so the collector never refuses what the API would accept.

const protobufMediaType = "application/x-protobuf"

// Message levels from the request root: resource spans, scope spans, span, then attribute
// values, each array or key-value list adding two.
const maxMessageDepth = 128

var errInvalidProtobuf = errors.New("body is not a well-formed OTLP protobuf trace export")

const (
	wireVarint  = 0
	wireFixed64 = 1
	wireBytes   = 2
	wireFixed32 = 5
)

type fieldRule struct {
	wire    uint64
	text    bool           // a string: must be UTF-8
	message *messageSchema // a nested message: walked with its own schema
}

type messageSchema struct {
	fields map[uint64]fieldRule
}

var (
	traceRequest  = &messageSchema{}
	resourceSpans = &messageSchema{}
	resource      = &messageSchema{}
	scopeSpans    = &messageSchema{}
	scope         = &messageSchema{}
	span          = &messageSchema{}
	status        = &messageSchema{}
	keyValue      = &messageSchema{}
	anyValue      = &messageSchema{}
	arrayValue    = &messageSchema{}
	keyValueList  = &messageSchema{}

	traceResponse  = &messageSchema{}
	partialSuccess = &messageSchema{}
)

func init() {
	text := fieldRule{wire: wireBytes, text: true}
	raw := fieldRule{wire: wireBytes}
	varint := fieldRule{wire: wireVarint}
	nested := func(schema *messageSchema) fieldRule { return fieldRule{wire: wireBytes, message: schema} }

	traceRequest.fields = map[uint64]fieldRule{1: nested(resourceSpans)}
	resourceSpans.fields = map[uint64]fieldRule{1: nested(resource), 2: nested(scopeSpans), 3: text}
	resource.fields = map[uint64]fieldRule{1: nested(keyValue), 2: varint}
	scopeSpans.fields = map[uint64]fieldRule{1: nested(scope), 2: nested(span), 3: text}
	scope.fields = map[uint64]fieldRule{1: text, 2: text, 3: nested(keyValue), 4: varint}
	span.fields = map[uint64]fieldRule{
		1: raw, 2: raw, 3: text, 4: raw, 5: text, 6: varint,
		7: {wire: wireFixed64}, 8: {wire: wireFixed64}, 9: nested(keyValue), 10: varint,
		11: raw, 12: varint, 13: raw, 14: varint, 15: nested(status), 16: {wire: wireFixed32},
	}
	status.fields = map[uint64]fieldRule{2: text, 3: varint}
	keyValue.fields = map[uint64]fieldRule{1: text, 2: nested(anyValue)}
	anyValue.fields = map[uint64]fieldRule{
		1: text, 2: varint, 3: varint, 4: {wire: wireFixed64},
		5: nested(arrayValue), 6: nested(keyValueList), 7: raw,
	}
	arrayValue.fields = map[uint64]fieldRule{1: nested(anyValue)}
	keyValueList.fields = map[uint64]fieldRule{1: nested(keyValue)}

	traceResponse.fields = map[uint64]fieldRule{1: nested(partialSuccess)}
	partialSuccess.fields = map[uint64]fieldRule{1: varint, 2: text}
}

// validateTraceExport accepts a well-formed request with at least one resource entry, as the
// JSON path requires resourceSpans.
func validateTraceExport(payload []byte) error {
	// The request's only known field is resource_spans, so its known-field count is theirs.
	resources, err := walk(payload, traceRequest, 0)
	if err != nil {
		return err
	}
	if resources == 0 {
		return errInvalidProtobuf
	}
	return nil
}

// validateTraceResponse accepts a well-formed ExportTraceServiceResponse, including the
// empty one that reports complete success.
func validateTraceResponse(payload []byte) error {
	_, err := walk(payload, traceResponse, 0)
	return err
}

func readVarint(data []byte, position int) (uint64, int, error) {
	var value uint64
	for index := 0; index < 10; index++ {
		if position >= len(data) {
			return 0, 0, errInvalidProtobuf
		}
		b := data[position]
		position++
		value |= uint64(b&0x7f) << (7 * index)
		if b < 0x80 {
			return value, position, nil
		}
	}
	return 0, 0, errInvalidProtobuf
}

// walk checks one message and returns how many known fields it held.
func walk(data []byte, schema *messageSchema, depth int) (int, error) {
	if depth > maxMessageDepth {
		return 0, errInvalidProtobuf
	}
	known := 0
	for position := 0; position < len(data); {
		key, next, err := readVarint(data, position)
		if err != nil {
			return 0, err
		}
		position = next
		number, wire := key>>3, key&7
		if number == 0 {
			return 0, errInvalidProtobuf
		}
		var value []byte
		switch wire {
		case wireVarint:
			if _, position, err = readVarint(data, position); err != nil {
				return 0, err
			}
		case wireFixed64, wireFixed32:
			size := 8
			if wire == wireFixed32 {
				size = 4
			}
			if size > len(data)-position {
				return 0, errInvalidProtobuf
			}
			position += size
		case wireBytes:
			length, next, err := readVarint(data, position)
			if err != nil {
				return 0, err
			}
			position = next
			if length > uint64(len(data)-position) {
				return 0, errInvalidProtobuf
			}
			value = data[position : position+int(length)]
			position += int(length)
		default:
			// Groups are long deprecated and absent from OTLP; 6 and 7 are undefined.
			return 0, errInvalidProtobuf
		}
		rule, ok := schema.fields[number]
		if !ok {
			continue
		}
		if rule.wire != wire {
			return 0, errInvalidProtobuf
		}
		known++
		if rule.text && !utf8.Valid(value) {
			return 0, errInvalidProtobuf
		}
		if rule.message != nil {
			if _, err := walk(value, rule.message, depth+1); err != nil {
				return 0, err
			}
		}
	}
	return known, nil
}
