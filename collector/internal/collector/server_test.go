package collector

import (
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

const testToken = "123456789012345678901234"
const testProjectID = "12345678-1234-1234-1234-123456789abc"

func testConfig(upstream *url.URL) Config {
	configured, err := parseUpstream(upstream.String(), testProjectID)
	if err != nil {
		panic(err)
	}
	return Config{ListenAddress: ":4318", IngestToken: testToken, APIKey: testToken, ProjectID: testProjectID, UpstreamURL: configured, MaxBodyBytes: 1024, QueueCapacity: 1, Workers: 1, RequestTimeout: time.Second}
}

func TestIngestRequiresAuthAndForwardsPayload(t *testing.T) {
	payload := `{"resourceSpans":[]}`
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			t.Fatalf("unexpected upstream authorization")
		}
		body, _ := io.ReadAll(r.Body)
		if string(body) != payload {
			t.Fatalf("payload changed: %s", body)
		}
		if r.URL.Query().Get("project_id") != testProjectID {
			t.Fatalf("project id was not forwarded")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted_spans":0}`))
	}))
	defer upstream.Close()
	u, _ := url.Parse(upstream.URL)
	h, closeWorkers := NewHandler(testConfig(u))
	defer closeWorkers()

	unauthorized := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader(payload))
	unauthorized.Header.Set("Content-Type", "application/json")
	unauthorizedResult := httptest.NewRecorder()
	h.ServeHTTP(unauthorizedResult, unauthorized)
	if unauthorizedResult.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", unauthorizedResult.Code)
	}

	request := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader(payload))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+testToken)
	result := httptest.NewRecorder()
	h.ServeHTTP(result, request)
	if result.Code != http.StatusAccepted || result.Body.String() != `{"accepted_spans":0}` {
		t.Fatalf("unexpected response %d %s", result.Code, result.Body.String())
	}
}

func TestIngestRejectsInvalidOrOversizedPayload(t *testing.T) {
	u, _ := url.Parse("http://127.0.0.1:1")
	config := testConfig(u)
	config.MaxBodyBytes = 32
	h, closeWorkers := NewHandler(config)
	defer closeWorkers()
	for name, testCase := range map[string]struct {
		payload string
		status  int
	}{
		"invalid":   {`{"wrong":[]}`, http.StatusBadRequest},
		"oversized": {`{"resourceSpans":["xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"]}`, http.StatusRequestEntityTooLarge},
	} {
		t.Run(name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader(testCase.payload))
			request.Header.Set("Content-Type", "application/json")
			request.Header.Set("Authorization", "Bearer "+testToken)
			result := httptest.NewRecorder()
			h.ServeHTTP(result, request)
			if result.Code != testCase.status {
				t.Fatalf("expected %d, got %d", testCase.status, result.Code)
			}
		})
	}
}

func TestParseUpstreamRejectsInsecureRemoteHTTP(t *testing.T) {
	if _, err := parseUpstream("http://example.com", testProjectID); err == nil {
		t.Fatal("expected insecure remote upstream to be rejected")
	}
	if _, err := parseUpstream("http://127.0.0.1:8000", testProjectID); err != nil {
		t.Fatalf("expected loopback HTTP to be allowed: %v", err)
	}
}

func TestMetricsTokenConfigurationIsOptionalStrongAndDistinct(t *testing.T) {
	t.Setenv("LOTUSPOND_PROJECT_ID", testProjectID)
	t.Setenv("LOTUSPOND_API_URL", "http://127.0.0.1:8000")
	t.Setenv("COLLECTOR_INGEST_TOKEN", testToken)
	t.Setenv("LOTUSPOND_API_KEY", "api_key_123456789012345678901234")
	for name, token := range map[string]string{
		"short":  "too-short",
		"spaces": "metrics token 123456789012345678901234567890",
		"ingest": testToken,
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("COLLECTOR_METRICS_TOKEN", token)
			if _, err := ConfigFromEnv(); err == nil {
				t.Fatal("invalid metrics token was accepted")
			}
		})
	}
	t.Setenv("COLLECTOR_METRICS_TOKEN", testMetricsToken)
	config, err := ConfigFromEnv()
	if err != nil {
		t.Fatalf("valid metrics token rejected: %v", err)
	}
	if config.MetricsToken != testMetricsToken {
		t.Fatal("metrics token was not retained")
	}
}

func TestAmbientProxyCannotReceiveWorkspaceEvidence(t *testing.T) {
	proxyCalled := make(chan struct{}, 1)
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		proxyCalled <- struct{}{}
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer proxy.Close()
	t.Setenv("HTTP_PROXY", proxy.URL)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted_spans":0}`))
	}))
	defer upstream.Close()
	realURL, _ := url.Parse(upstream.URL)
	config := testConfig(realURL)
	config.UpstreamURL.Host = "collector-upstream.example.invalid:80"
	originalTransport := http.DefaultTransport
	defer func() { http.DefaultTransport = originalTransport }()
	// Resolve the fixture hostname locally. A retained proxy function routes to
	// the proxy fixture instead; no public DNS or network is involved.
	http.DefaultTransport = &http.Transport{
		Proxy: func(r *http.Request) (*url.URL, error) { return url.Parse(os.Getenv("HTTP_PROXY")) },
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			if addr == config.UpstreamURL.Host {
				addr = realURL.Host
			}
			return (&net.Dialer{}).DialContext(ctx, network, addr)
		},
	}
	h, closeWorkers := NewHandler(config)
	defer closeWorkers()
	request := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader(`{"resourceSpans":[]}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+testToken)
	result := httptest.NewRecorder()
	h.ServeHTTP(result, request)
	if result.Code != http.StatusAccepted {
		t.Fatalf("expected direct upstream success, got %d", result.Code)
	}
	select {
	case <-proxyCalled:
		t.Fatal("ambient proxy received workspace evidence")
	default:
	}
}

func TestOversizedUpstreamResponseIsNotAcceptedAsTruncatedJSON(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusAccepted)
		// The first 64 KiB is valid JSON plus whitespace. Ignoring the final byte
		// would falsely accept an over-limit response.
		prefix := `{"accepted_spans":0}`
		_, _ = w.Write([]byte(prefix + strings.Repeat(" ", (64<<10)-len(prefix)) + "x"))
	}))
	defer upstream.Close()
	u, _ := url.Parse(upstream.URL)
	h, closeWorkers := NewHandler(testConfig(u))
	defer closeWorkers()
	request := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader(`{"resourceSpans":[]}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+testToken)
	result := httptest.NewRecorder()
	h.ServeHTTP(result, request)
	if result.Code != http.StatusBadGateway {
		t.Fatalf("oversized upstream response was accepted: %d", result.Code)
	}
}

type gatedBody struct {
	reader  io.Reader
	started chan struct{}
	release chan struct{}
	once    sync.Once
}

func (b *gatedBody) Read(p []byte) (int, error) {
	b.once.Do(func() { close(b.started) })
	<-b.release
	return b.reader.Read(p)
}

func (b *gatedBody) Close() error { return nil }

func authorizedTraceRequest() *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader(`{"resourceSpans":[]}`))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer "+testToken)
	return r
}

func TestShutdownRejectsHandlerStillReadingBodyAndIsIdempotent(t *testing.T) {
	u, _ := url.Parse("http://127.0.0.1:1")
	h, closeWorkers := NewHandler(testConfig(u))
	defer closeWorkers()
	body := &gatedBody{reader: strings.NewReader(`{"resourceSpans":[]}`), started: make(chan struct{}), release: make(chan struct{})}
	unblock := sync.OnceFunc(func() { close(body.release) })
	defer unblock()
	r := authorizedTraceRequest()
	r.Body = body
	w := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { h.ServeHTTP(w, r); close(done) }()
	select {
	case <-body.started:
	case <-time.After(time.Second):
		t.Fatal("handler did not start reading its body")
	}
	var closing sync.WaitGroup
	for range 8 {
		closing.Add(1)
		go func() { defer closing.Done(); closeWorkers() }()
	}
	closing.Wait()
	unblock()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("handler did not return after shutdown")
	}
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected shutdown rejection, got %d", w.Code)
	}
}

func TestQueueResidenceUsesTheRequestDeadline(t *testing.T) {
	u, _ := url.Parse("http://127.0.0.1:1")
	config := testConfig(u)
	config.RequestTimeout = 30 * time.Millisecond
	// Keep a request queued without a worker to model a saturated worker pool.
	// The request must time out even though no upstream HTTP call has begun.
	h := &handler{config: config, queue: make(chan forwardRequest, 1)}
	w := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { h.ingest(w, authorizedTraceRequest()); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("queue wait exceeded the configured request deadline")
	}
	if w.Code != http.StatusGatewayTimeout {
		t.Fatalf("expected queue timeout, got %d", w.Code)
	}
	queued := <-h.queue
	if queued.ctx.Err() != context.DeadlineExceeded {
		t.Fatalf("queued forwarding context must also expire: %v", queued.ctx.Err())
	}
}

func TestShutdownDrainsAlreadyAcceptedWork(t *testing.T) {
	entered := make(chan struct{})
	release := make(chan struct{})
	unblock := sync.OnceFunc(func() { close(release) })
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		<-release
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted_spans":0}`))
	}))
	defer upstream.Close()
	defer unblock()
	u, _ := url.Parse(upstream.URL)
	h, closeWorkers := NewHandler(testConfig(u))
	defer closeWorkers()
	w := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { h.ServeHTTP(w, authorizedTraceRequest()); close(done) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("accepted work did not reach the upstream")
	}
	closed := make(chan struct{})
	go func() { closeWorkers(); close(closed) }()
	select {
	case <-closed:
		t.Fatal("shutdown returned before accepted work completed")
	case <-time.After(10 * time.Millisecond):
	}
	unblock()
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("shutdown did not complete after accepted work finished")
	}
	<-done
	if w.Code != http.StatusAccepted {
		t.Fatalf("accepted work was lost during shutdown: %d", w.Code)
	}
}

func gzipBytes(t *testing.T, payload string) []byte {
	t.Helper()
	var buffer bytes.Buffer
	writer := gzip.NewWriter(&buffer)
	if _, err := writer.Write([]byte(payload)); err != nil {
		t.Fatalf("gzip write failed: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("gzip close failed: %v", err)
	}
	return buffer.Bytes()
}

func TestIngestAcceptsGzipAndForwardsPlainJSON(t *testing.T) {
	payload := `{"resourceSpans":[{"scopeSpans":[]}]}`
	received := make(chan string, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		received <- string(body)
		if r.Header.Get("Content-Encoding") != "" {
			t.Errorf("upstream received an encoded body: %q", r.Header.Get("Content-Encoding"))
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted_spans":0}`))
	}))
	defer upstream.Close()
	u, _ := url.Parse(upstream.URL)
	h, closeWorkers := NewHandler(testConfig(u))
	defer closeWorkers()

	for _, encoding := range []string{"gzip", "GZIP", " gzip "} {
		request := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(gzipBytes(t, payload)))
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Content-Encoding", encoding)
		request.Header.Set("Authorization", "Bearer "+testToken)
		result := httptest.NewRecorder()
		h.ServeHTTP(result, request)
		if result.Code != http.StatusAccepted {
			t.Fatalf("encoding %q: expected 202, got %d %s", encoding, result.Code, result.Body.String())
		}
		// The collector normalizes to plain JSON; it never re-encodes workspace evidence.
		if forwarded := <-received; forwarded != payload {
			t.Fatalf("encoding %q: upstream payload changed: %s", encoding, forwarded)
		}
	}
}

func TestIngestRefusesUnsupportedEncodingAndDecompressionBombs(t *testing.T) {
	u, _ := url.Parse("http://127.0.0.1:1")
	config := testConfig(u)
	config.MaxBodyBytes = 1024
	h, closeWorkers := NewHandler(config)
	defer closeWorkers()

	unsupported := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader(`{"resourceSpans":[]}`))
	unsupported.Header.Set("Content-Type", "application/json")
	unsupported.Header.Set("Content-Encoding", "br")
	unsupported.Header.Set("Authorization", "Bearer "+testToken)
	unsupportedResult := httptest.NewRecorder()
	h.ServeHTTP(unsupportedResult, unsupported)
	if unsupportedResult.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("expected 415 for brotli, got %d", unsupportedResult.Code)
	}

	// Highly compressible padding stays small on the wire and large once expanded.
	bomb := `{"resourceSpans":[],"pad":"` + strings.Repeat("a", 64*1024) + `"}`
	compressed := gzipBytes(t, bomb)
	if len(compressed) >= int(config.MaxBodyBytes) {
		t.Fatalf("the compressed fixture must fit the wire budget, got %d bytes", len(compressed))
	}
	oversized := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(compressed))
	oversized.Header.Set("Content-Type", "application/json")
	oversized.Header.Set("Content-Encoding", "gzip")
	oversized.Header.Set("Authorization", "Bearer "+testToken)
	oversizedResult := httptest.NewRecorder()
	h.ServeHTTP(oversizedResult, oversized)
	if oversizedResult.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected 413 for an expanded payload, got %d %s", oversizedResult.Code, oversizedResult.Body.String())
	}

	corrupt := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader("not actually gzip"))
	corrupt.Header.Set("Content-Type", "application/json")
	corrupt.Header.Set("Content-Encoding", "gzip")
	corrupt.Header.Set("Authorization", "Bearer "+testToken)
	corruptResult := httptest.NewRecorder()
	h.ServeHTTP(corruptResult, corrupt)
	if corruptResult.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for a corrupt gzip body, got %d", corruptResult.Code)
	}
}

func TestDecompressKeepsAnExactlyBudgetedBody(t *testing.T) {
	payload := strings.Repeat("b", 512)
	decoded, err := decompress(gzipBytes(t, payload), 512)
	if err != nil || string(decoded) != payload {
		t.Fatalf("expected an exact-limit body to decode, got %v %d bytes", err, len(decoded))
	}
	if _, err := decompress(gzipBytes(t, payload), 511); !errors.Is(err, errDecompressedTooLarge) {
		t.Fatalf("expected the limit to be enforced, got %v", err)
	}
}
