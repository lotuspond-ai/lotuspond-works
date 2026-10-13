package collector

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

// Serialized by opentelemetry-proto 1.44.0 (protobuf 7.36.2); the same bytes back the API's
// decoder tests in backend/tests/test_otlp_protobuf.py. One resource, one scope and one span
// carrying every attribute value kind, an event, a link, a status and flags.
const goldenRequestHex = "0ab4030a2e0a170a0c736572766963652e6e616d6512070a056167656e740a130a076170695f6b657912080a" +
	"0673656372657412d8020a160a0f6c6f747573706f6e642e74657374731203312e3012bd020a100102030405" +
	"060708090a0b0c0d0e0f10120811121314151617181a0c76656e646f723d76616c7565220821222324252627" +
	"282a0a6d6f64656c2e63616c6c30033900002a36fe9c97174180b21045fe9c97174a1f0a126c6f747573706f" +
	"6e642e636f73745f7573641209219a9999999999893f4a150a06746f6b656e73120b18d6ffffffffffffffff" +
	"014a0e0a0873747265616d6564120210014a130a0474616773120b2a090a030a01610a0218074a1f0a066e65" +
	"73746564121532130a110a05696e6e657212080a06c3a420e29c934a0e0a0372617712073a0500ff62696e5a" +
	"1f0900e11f3cfe9c9717120572657472791a0d0a07617474656d7074120218026a1c0a100000000000000000" +
	"0000000000000000120800000000000000007a141210757073747265616d2074696d656f7574180285010101" +
	"00001a2768747470733a2f2f6f70656e74656c656d657472792e696f2f736368656d61732f312e32362e30"

// ExportTraceServiceResponse{partial_success{rejected_spans: 3, error_message: "3 spans were rejected"}}.
const goldenResponseHex = "0a190803121533207370616e7320776572652072656a6563746564"

func golden(t testing.TB, encoded string) []byte {
	t.Helper()
	decoded, err := hex.DecodeString(encoded)
	if err != nil {
		t.Fatalf("golden bytes: %v", err)
	}
	return decoded
}

func pbVarint(value uint64) []byte { return binary.AppendUvarint(nil, value) }

// pbField is a varint field for a number and a length-delimited field for bytes.
func pbField(number uint64, payload any) []byte {
	switch value := payload.(type) {
	case uint64:
		return append(pbVarint(number<<3), pbVarint(value)...)
	case []byte:
		out := append(pbVarint(number<<3|2), pbVarint(uint64(len(value)))...)
		return append(out, value...)
	}
	panic("unsupported field payload")
}

func pbJoin(parts ...[]byte) []byte { return bytes.Join(parts, nil) }

func pbSpan(extra ...[]byte) []byte {
	start := append(pbVarint(7<<3|1), binary.LittleEndian.AppendUint64(nil, 1_700_000_000_000_000_000)...)
	end := append(pbVarint(8<<3|1), binary.LittleEndian.AppendUint64(nil, 1_700_000_000_050_000_000)...)
	identifiers := pbJoin(pbField(1, bytes.Repeat([]byte{1}, 16)), pbField(2, bytes.Repeat([]byte{2}, 8)))
	return pbJoin(append([][]byte{identifiers, pbField(5, []byte("call")), start, end}, extra...)...)
}

func pbRequest(spans ...[]byte) []byte {
	var scope []byte
	for _, span := range spans {
		scope = append(scope, pbField(2, span)...)
	}
	return pbField(1, pbField(2, scope))
}

func TestTheOfficialExportAndResponseAreWellFormed(t *testing.T) {
	if err := validateTraceExport(golden(t, goldenRequestHex)); err != nil {
		t.Fatalf("official export refused: %v", err)
	}
	if err := validateTraceResponse(golden(t, goldenResponseHex)); err != nil {
		t.Fatalf("official partial success refused: %v", err)
	}
	// A complete success is the empty response.
	if err := validateTraceResponse(nil); err != nil {
		t.Fatalf("empty response refused: %v", err)
	}
}

func TestUnknownFieldsAreSkippedAtEveryLevel(t *testing.T) {
	unknown := pbJoin(pbField(99, uint64(7)), pbField(98, []byte("future")))
	extended := pbJoin(pbField(1, pbJoin(pbField(2, pbJoin(pbField(2, pbSpan(unknown)), unknown)), unknown)), unknown)
	if err := validateTraceExport(extended); err != nil {
		t.Fatalf("unknown fields refused: %v", err)
	}
}

func TestAttributeNestingIsBoundedButNeverStricterThanTheAPI(t *testing.T) {
	nested := func(levels int) []byte {
		value := pbField(1, []byte("leaf"))
		for range levels {
			value = pbField(5, pbField(1, value))
		}
		return pbRequest(pbSpan(pbField(9, pbJoin(pbField(1, []byte("deep")), pbField(2, value)))))
	}
	// The API accepts attribute values nested 32 deep, so the collector must too.
	if err := validateTraceExport(nested(40)); err != nil {
		t.Fatalf("nesting the API accepts was refused: %v", err)
	}
	if err := validateTraceExport(nested(5_000)); err == nil {
		t.Fatal("unbounded nesting was accepted")
	}
}

func TestMalformedExportsAreRefused(t *testing.T) {
	for name, body := range map[string][]byte{
		"empty":                  nil,
		"truncated-length":       {0x0a},
		"length-past-the-end":    []byte("\x0a\x05abc"),
		"group-wire-type":        {0x0b},
		"undefined-wire-type":    {0x0f},
		"field-number-zero":      {0x00, 0x01},
		"message-sent-as-varint": {0x08, 0x01},
		"eleven-byte-varint":     append(append([]byte{0x10}, bytes.Repeat([]byte{0xff}, 10)...), 0x01),
		"truncated-varint":       {0x10, 0xff},
		"name-not-utf8":          pbRequest(pbSpan(pbField(5, []byte{0xff, 0xfe}))),
		"short-fixed64":          pbRequest(pbSpan(append(pbVarint(7<<3|1), 1, 2, 3))),
		"short-fixed32":          pbRequest(pbSpan(append(pbVarint(16<<3|5), 1))),
		"truncated-inside-span":  pbField(1, pbField(2, pbField(2, []byte("\x2a\x03ab")))),
	} {
		t.Run(name, func(t *testing.T) {
			if err := validateTraceExport(body); err == nil {
				t.Fatal("malformed export accepted")
			}
		})
	}
}

func TestIngestForwardsProtobufUnchangedAndRelaysPartialSuccess(t *testing.T) {
	export := golden(t, goldenRequestHex)
	partial := golden(t, goldenResponseHex)
	received := make(chan []byte, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Content-Type") != protobufMediaType || r.Header.Get("Content-Encoding") != "" {
			t.Errorf("upstream received %q encoded %q", r.Header.Get("Content-Type"), r.Header.Get("Content-Encoding"))
		}
		body, _ := io.ReadAll(r.Body)
		received <- body
		w.Header().Set("Content-Type", protobufMediaType)
		_, _ = w.Write(partial)
	}))
	defer upstream.Close()
	u, _ := url.Parse(upstream.URL)
	h, closeWorkers := NewHandler(testConfig(u))
	defer closeWorkers()

	for name, encoded := range map[string][]byte{"plain": export, "gzip": gzipBytes(t, string(export))} {
		request := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(encoded))
		request.Header.Set("Content-Type", "application/x-protobuf; charset=binary")
		if name == "gzip" {
			request.Header.Set("Content-Encoding", "gzip")
		}
		request.Header.Set("Authorization", "Bearer "+testToken)
		result := httptest.NewRecorder()
		h.ServeHTTP(result, request)
		if result.Code != http.StatusOK || result.Header().Get("Content-Type") != protobufMediaType ||
			!bytes.Equal(result.Body.Bytes(), partial) {
			t.Fatalf("%s: unexpected relay %d %q %x", name, result.Code, result.Header().Get("Content-Type"), result.Body.Bytes())
		}
		// The collector forwards the exporter's bytes; it never re-encodes evidence.
		if forwarded := <-received; !bytes.Equal(forwarded, export) {
			t.Fatalf("%s: upstream payload changed", name)
		}
	}
}

func TestIngestRefusesMalformedProtobufAndUnreadableAnswers(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", protobufMediaType)
		_, _ = w.Write([]byte(`{"accepted_spans":1}`))
	}))
	defer upstream.Close()
	u, _ := url.Parse(upstream.URL)
	h, closeWorkers := NewHandler(testConfig(u))
	defer closeWorkers()
	send := func(contentType string, body []byte) *httptest.ResponseRecorder {
		request := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
		request.Header.Set("Content-Type", contentType)
		request.Header.Set("Authorization", "Bearer "+testToken)
		result := httptest.NewRecorder()
		h.ServeHTTP(result, request)
		return result
	}
	if result := send(protobufMediaType, []byte("\x0a\x05abc")); result.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for a malformed export, got %d", result.Code)
	}
	if result := send("text/plain", pbRequest(pbSpan())); result.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("expected 415 for an unsupported type, got %d", result.Code)
	}
	// An answer that is not an ExportTraceServiceResponse is never relayed to the exporter.
	result := send(protobufMediaType, pbRequest(pbSpan()))
	if result.Code != http.StatusBadGateway || !strings.Contains(result.Body.String(), "unavailable") {
		t.Fatalf("expected 502 for an unreadable answer, got %d %s", result.Code, result.Body.String())
	}
}

// The validator reads untrusted bytes, so it must never panic or accept a body the walk
// could not frame. `go test -fuzz=FuzzValidateTraceExport` explores beyond these seeds.
func FuzzValidateTraceExport(f *testing.F) {
	f.Add(golden(f, goldenRequestHex))
	f.Add(golden(f, goldenResponseHex))
	f.Add(pbRequest(pbSpan()))
	f.Add([]byte("\x0a\x05abc"))
	f.Fuzz(func(t *testing.T, body []byte) {
		if validateTraceExport(body) == nil {
			// A valid export stays valid with unknown fields appended, as protobuf promises.
			if err := validateTraceExport(append(append([]byte{}, body...), pbField(99, uint64(1))...)); err != nil {
				t.Fatalf("appending an unknown field broke a valid export: %v", err)
			}
		}
		_ = validateTraceResponse(body)
	})
}
