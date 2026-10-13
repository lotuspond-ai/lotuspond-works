package collector

import (
	"bytes"
	"context"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

const testMetricsToken = "metrics_12345678901234567890123456789012"

func metricsConfig(upstream *url.URL) Config {
	config := testConfig(upstream)
	config.MetricsToken = testMetricsToken
	return config
}

func scrape(t *testing.T, handler http.Handler, token string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	result := httptest.NewRecorder()
	handler.ServeHTTP(result, request)
	return result
}

func TestMetricsAreDisabledByDefaultAndUseSeparateAuthentication(t *testing.T) {
	u, _ := url.Parse("http://127.0.0.1:1")
	disabled, closeDisabled := NewHandler(testConfig(u))
	defer closeDisabled()
	if result := scrape(t, disabled, testMetricsToken); result.Code != http.StatusNotFound {
		t.Fatalf("disabled metrics returned %d", result.Code)
	}

	enabled, closeEnabled := NewHandler(metricsConfig(u))
	defer closeEnabled()
	for name, token := range map[string]string{"missing": "", "ingest": testToken} {
		t.Run(name, func(t *testing.T) {
			if result := scrape(t, enabled, token); result.Code != http.StatusUnauthorized {
				t.Fatalf("expected 401, got %d", result.Code)
			}
		})
	}
	result := scrape(t, enabled, testMetricsToken)
	if result.Code != http.StatusOK {
		t.Fatalf("expected metrics, got %d", result.Code)
	}
	if result.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("metrics response may be cached")
	}
	post := httptest.NewRequest(http.MethodPost, "/metrics", nil)
	post.Header.Set("Authorization", "Bearer "+testMetricsToken)
	postResult := httptest.NewRecorder()
	enabled.ServeHTTP(postResult, post)
	if postResult.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected method rejection, got %d", postResult.Code)
	}
}

func TestMetricsHaveOnlyFixedNonsecretSeries(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted_spans":0}`))
	}))
	defer upstream.Close()
	u, _ := url.Parse(upstream.URL)
	handler, closeWorkers := NewHandler(metricsConfig(u))
	defer closeWorkers()

	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader(`{}`)))
	accepted := httptest.NewRecorder()
	handler.ServeHTTP(accepted, authorizedTraceRequest())
	if accepted.Code != http.StatusAccepted {
		t.Fatalf("expected accepted batch, got %d", accepted.Code)
	}

	result := scrape(t, handler, testMetricsToken)
	body := result.Body.String()
	for _, expected := range []string{
		`lotuspond_collector_requests_total{result="accepted"} 1`,
		`lotuspond_collector_requests_total{result="auth_rejected"} 1`,
		`lotuspond_collector_requests_total{result="queue_full"} 0`,
		`lotuspond_collector_request_duration_seconds_bucket{le="+Inf"} 2`,
		`lotuspond_collector_forward_duration_seconds_count 1`,
		`lotuspond_collector_queue_capacity 1`,
		`lotuspond_collector_workers 1`,
	} {
		if !strings.Contains(body, expected) {
			t.Fatalf("missing metric %q\n%s", expected, body)
		}
	}
	for _, secret := range []string{testMetricsToken, testToken, testProjectID, upstream.URL} {
		if strings.Contains(body, secret) {
			t.Fatalf("metrics exposed sensitive/configuration value %q", secret)
		}
	}
}

func TestMetricsExposeQueueSaturationWithoutUnboundedLabels(t *testing.T) {
	entered := make(chan struct{})
	release := make(chan struct{})
	var enterOnce sync.Once
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		enterOnce.Do(func() { close(entered) })
		<-release
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted_spans":0}`))
	}))
	defer upstream.Close()
	u, _ := url.Parse(upstream.URL)
	config := metricsConfig(u)
	config.RequestTimeout = 2 * time.Second
	handler, closeWorkers := NewHandler(config)
	defer closeWorkers()

	type response struct{ status int }
	responses := make(chan response, 2)
	go func() {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, authorizedTraceRequest())
		responses <- response{w.Code}
	}()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("worker did not reach blocked upstream")
	}
	go func() {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, authorizedTraceRequest())
		responses <- response{w.Code}
	}()
	deadline := time.Now().Add(time.Second)
	for {
		body := scrape(t, handler, testMetricsToken).Body.String()
		if strings.Contains(body, "lotuspond_collector_queue_depth 1") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("request did not enter queue")
		}
		time.Sleep(time.Millisecond)
	}
	full := httptest.NewRecorder()
	handler.ServeHTTP(full, authorizedTraceRequest())
	if full.Code != http.StatusTooManyRequests {
		t.Fatalf("expected queue-full response, got %d", full.Code)
	}
	metrics := scrape(t, handler, testMetricsToken).Body.String()
	if !strings.Contains(metrics, `lotuspond_collector_requests_total{result="queue_full"} 1`) ||
		!strings.Contains(metrics, "lotuspond_collector_in_flight 2") {
		t.Fatalf("saturation was not visible\n%s", metrics)
	}
	close(release)
	for range 2 {
		if result := <-responses; result.status != http.StatusAccepted {
			t.Fatalf("accepted queued request returned %d", result.status)
		}
	}
}

type canceledUploadBody struct {
	cancel context.CancelFunc
}

func (b *canceledUploadBody) Read([]byte) (int, error) {
	b.cancel()
	return 0, context.Canceled
}

func (*canceledUploadBody) Close() error { return nil }

type deadlineUploadBody struct {
	delay time.Duration
}

func (b *deadlineUploadBody) Read([]byte) (int, error) {
	time.Sleep(b.delay)
	return 0, context.DeadlineExceeded
}

func (*deadlineUploadBody) Close() error { return nil }

func TestUploadReadCancellationAndDeadlineHaveDistinctResults(t *testing.T) {
	u, _ := url.Parse("http://127.0.0.1:1")

	cancelHandler, closeCanceled := NewHandler(metricsConfig(u))
	defer closeCanceled()
	ctx, cancel := context.WithCancel(context.Background())
	canceled := authorizedTraceRequest().WithContext(ctx)
	canceled.Body = &canceledUploadBody{cancel: cancel}
	canceledResult := httptest.NewRecorder()
	cancelHandler.ServeHTTP(canceledResult, canceled)
	canceledMetrics := scrape(t, cancelHandler, testMetricsToken).Body.String()
	if !strings.Contains(canceledMetrics, `lotuspond_collector_requests_total{result="client_canceled"} 1`) {
		t.Fatalf("canceled upload was misclassified\n%s", canceledMetrics)
	}
	if canceledResult.Body.Len() != 0 {
		t.Fatal("collector wrote an error after the upload client canceled")
	}

	config := metricsConfig(u)
	config.RequestTimeout = 10 * time.Millisecond
	deadlineHandler, closeDeadline := NewHandler(config)
	defer closeDeadline()
	deadline := authorizedTraceRequest()
	deadline.Body = &deadlineUploadBody{delay: 20 * time.Millisecond}
	deadlineResult := httptest.NewRecorder()
	deadlineHandler.ServeHTTP(deadlineResult, deadline)
	if deadlineResult.Code != http.StatusGatewayTimeout {
		t.Fatalf("deadline upload returned %d", deadlineResult.Code)
	}
	deadlineMetrics := scrape(t, deadlineHandler, testMetricsToken).Body.String()
	if !strings.Contains(deadlineMetrics, `lotuspond_collector_requests_total{result="deadline_exceeded"} 1`) {
		t.Fatalf("deadline upload was misclassified\n%s", deadlineMetrics)
	}
}

func TestCanceledIngestHasFiniteResult(t *testing.T) {
	u, _ := url.Parse("http://127.0.0.1:1")
	handler, closeWorkers := NewHandler(metricsConfig(u))
	defer closeWorkers()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	request := authorizedTraceRequest().WithContext(ctx)
	result := httptest.NewRecorder()
	handler.ServeHTTP(result, request)
	metrics := scrape(t, handler, testMetricsToken).Body.String()
	if !strings.Contains(metrics, `lotuspond_collector_requests_total{result="client_canceled"} 1`) {
		t.Fatalf("client cancellation not recorded\n%s", metrics)
	}
}

func TestMetricsClassifyBoundedUpstreamFailures(t *testing.T) {
	for name, testCase := range map[string]struct {
		handler http.HandlerFunc
		result  string
	}{
		"client rejection": {
			handler: func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusBadRequest) },
			result:  "upstream_rejected_4xx",
		},
		"server failure": {
			handler: func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusInternalServerError) },
			result:  "upstream_rejected_5xx",
		},
		"invalid response": {
			handler: func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusAccepted)
				_, _ = w.Write([]byte("not-json"))
			},
			result: "upstream_invalid_response",
		},
		"timeout": {
			handler: func(w http.ResponseWriter, _ *http.Request) {
				time.Sleep(100 * time.Millisecond)
				w.WriteHeader(http.StatusAccepted)
			},
			result: "deadline_exceeded",
		},
	} {
		t.Run(name, func(t *testing.T) {
			upstream := httptest.NewServer(testCase.handler)
			defer upstream.Close()
			u, _ := url.Parse(upstream.URL)
			config := metricsConfig(u)
			if name == "timeout" {
				config.RequestTimeout = 20 * time.Millisecond
			}
			handler, closeWorkers := NewHandler(config)
			defer closeWorkers()
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, authorizedTraceRequest())
			body := scrape(t, handler, testMetricsToken).Body.String()
			expected := `lotuspond_collector_requests_total{result="` + testCase.result + `"} 1`
			if !strings.Contains(body, expected) {
				t.Fatalf("missing result %s\n%s", testCase.result, body)
			}
		})
	}
}

func TestMetricsClassifyUpstreamTransportFailure(t *testing.T) {
	var logs bytes.Buffer
	originalLogger := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	defer slog.SetDefault(originalLogger)
	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	u, _ := url.Parse(upstream.URL)
	upstream.Close()
	handler, closeWorkers := NewHandler(metricsConfig(u))
	defer closeWorkers()
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedTraceRequest())
	body := scrape(t, handler, testMetricsToken).Body.String()
	if !strings.Contains(body, `lotuspond_collector_requests_total{result="upstream_transport_error"} 1`) {
		t.Fatalf("transport failure not classified\n%s", body)
	}
	for _, value := range []string{testToken, testMetricsToken, testProjectID, upstream.URL} {
		if strings.Contains(logs.String(), value) {
			t.Fatalf("transport log exposed configuration value %q", value)
		}
	}
}
