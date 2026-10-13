package collector

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

var errDecompressedTooLarge = errors.New("decompressed payload exceeds the configured limit")

// A compressed body can expand far past its wire size, so the decompressed form gets the
// same explicit budget rather than trusting the ratio a client happens to send.
func decompress(payload []byte, limit int64) ([]byte, error) {
	reader, err := gzip.NewReader(bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	defer func() { _ = reader.Close() }()
	// Read one byte past the limit so a body of exactly the limit still succeeds.
	decoded, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(decoded)) > limit {
		return nil, errDecompressedTooLarge
	}
	return decoded, nil
}

type forwardRequest struct {
	ctx         context.Context
	payload     []byte
	contentType string
	spoolID     string
	result      chan forwardResult
}

type forwardResult struct {
	status      int
	contentType string
	body        []byte
	result      string
	// The API did not answer (a transport failure, a timeout, a 5xx or a 429), so the batch
	// may be sent again; retryAfter carries the API's Retry-After when it gave one.
	retryable  bool
	retryAfter time.Duration
	spooled    bool
}

type handler struct {
	config  Config
	client  *http.Client
	queue   chan forwardRequest
	queueMu sync.Mutex
	closed  bool
	metrics *collectorMetrics
	spool   *spool
	backoff func(attempt int) time.Duration
	stop    chan struct{}
}

// replayBackoff doubles from one second to five minutes, with a fifth of jitter so that
// several collectors recovering together do not retry in step.
func replayBackoff(attempt int) time.Duration {
	delay := time.Second << min(max(attempt-1, 0), 9)
	delay = min(delay, 5*time.Minute)
	return delay - time.Duration(rand.Int64N(int64(delay)/5+1))
}

// NewHandler builds a handler without a spool, or panics if a configured spool cannot open;
// use Open where a spool may be configured.
func NewHandler(config Config) (http.Handler, func()) {
	handler, closeWorkers, err := Open(config)
	if err != nil {
		panic(err)
	}
	return handler, closeWorkers
}

// Open builds the collector handler and, when COLLECTOR_SPOOL_DIR is set, opens its spool and
// starts replaying what an earlier process left there.
func Open(config Config) (http.Handler, func(), error) {
	var durable *spool
	if config.SpoolDir != "" {
		opened, err := openSpool(config.SpoolDir, config.SpoolMaxBytes)
		if err != nil {
			return nil, nil, err
		}
		durable = opened
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	// The operator-selected API is the only destination; ambient proxy variables
	// must not reroute workspace evidence or its authorization header.
	transport.Proxy = nil
	h := &handler{
		config: config,
		client: &http.Client{
			Transport: transport,
			Timeout:   config.RequestTimeout,
			CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
				return errors.New("upstream redirects are disabled")
			},
		},
		queue:   make(chan forwardRequest, config.QueueCapacity),
		metrics: newCollectorMetrics(),
		spool:   durable,
		backoff: replayBackoff,
		stop:    make(chan struct{}),
	}
	if config.replayBackoff != nil {
		h.backoff = config.replayBackoff
	}
	var workers sync.WaitGroup
	for range config.Workers {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for request := range h.queue {
				result := h.settle(request, h.forward(request.ctx, request.payload, request.contentType))
				select {
				case request.result <- result:
				case <-request.ctx.Done():
				}
			}
		}()
	}
	replayed := make(chan struct{})
	if h.spool != nil {
		go h.replayLoop(replayed)
	} else {
		close(replayed)
	}
	var closeOnce sync.Once
	closeWorkers := func() {
		closeOnce.Do(func() {
			// Stop replaying between attempts; spooled batches stay for the next process.
			close(h.stop)
			<-replayed
			// Enqueue and close share the same lock. A handler still reading its
			// body when shutdown finishes must reject work, never send to a closed channel.
			h.queueMu.Lock()
			h.closed = true
			close(h.queue)
			h.queueMu.Unlock()
			workers.Wait()
			transport.CloseIdleConnections()
		})
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", h.health)
	mux.HandleFunc("POST /v1/traces", h.ingest)
	if config.MetricsToken != "" {
		mux.HandleFunc("GET /metrics", h.serveMetrics)
	}
	return mux, closeWorkers, nil
}

// settle decides a spooled batch's fate after its first attempt. An answered batch leaves
// the spool, because its exporter hears the answer; an unanswered one passes to the replayer
// and its exporter is told 202, whether or not that exporter is still waiting.
func (h *handler) settle(request forwardRequest, result forwardResult) forwardResult {
	if request.spoolID == "" {
		return result
	}
	if !result.retryable {
		h.spool.release(request.spoolID)
		return result
	}
	wait := result.retryAfter
	if wait <= 0 {
		wait = h.backoff(1)
	}
	h.spool.handOff(request.spoolID, wait, time.Now())
	if request.contentType == protobufMediaType {
		// An empty ExportTraceServiceResponse; the header says delivery is still pending.
		return forwardResult{status: http.StatusAccepted, contentType: protobufMediaType, result: "spooled", spooled: true}
	}
	return forwardResult{status: http.StatusAccepted, contentType: "application/json", body: []byte(`{"status":"spooled"}`), result: "spooled", spooled: true}
}

func (h *handler) replayLoop(done chan struct{}) {
	defer close(done)
	for {
		select {
		case <-h.stop:
			return
		default:
		}
		entry, wait := h.spool.next(time.Now())
		if entry == nil {
			var timer <-chan time.Time
			if wait >= 0 {
				timer = time.After(wait)
			}
			select {
			case <-h.stop:
				return
			case <-h.spool.wake:
			case <-timer:
			}
			continue
		}
		h.replayOne(entry)
	}
}

func (h *handler) replayOne(entry *spoolEntry) {
	contentType, payload, err := h.spool.load(entry.id)
	if errors.Is(err, os.ErrNotExist) {
		// An operator removed it; there is nothing left to deliver.
		h.spool.release(entry.id)
		return
	}
	if err != nil {
		slog.Error("spooled batch is unreadable and was moved to rejected", "batch", entry.id)
		_ = h.spool.reject(entry.id)
		h.metrics.observeReplay("rejected")
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), h.config.RequestTimeout)
	result := h.forward(ctx, payload, contentType)
	cancel()
	switch {
	case result.result == "accepted":
		h.spool.release(entry.id)
		h.metrics.observeReplay("delivered")
	case result.retryable:
		wait := result.retryAfter
		if wait <= 0 {
			wait = h.backoff(entry.attempts + 1)
		}
		h.spool.retryLater(entry.id, wait, time.Now())
		h.metrics.observeReplay("retry")
	default:
		// No exporter is waiting to hear this, so keep the batch for the operator.
		slog.Warn("the API refused a spooled batch; it was moved to rejected", "batch", entry.id, "result", result.result)
		if err := h.spool.reject(entry.id); err != nil {
			slog.Error("a refused spooled batch could not be moved to rejected", "batch", entry.id)
		}
		h.metrics.observeReplay("rejected")
	}
}

func (h *handler) health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok"}`))
}

func (h *handler) serveMetrics(w http.ResponseWriter, r *http.Request) {
	if !authorized(r.Header.Get("Authorization"), h.config.MetricsToken) {
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	var spooled *spoolStats
	if h.spool != nil {
		stats := h.spool.stats(time.Now())
		spooled = &stats
	}
	h.metrics.write(w, len(h.queue), cap(h.queue), h.config.Workers, spooled)
}

func (h *handler) ingest(w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	resultName := "invalid_request"
	if h.metrics != nil {
		h.metrics.inFlight.Add(1)
		defer func() {
			h.metrics.inFlight.Add(-1)
			h.metrics.observeRequest(resultName, time.Since(started))
		}()
	}
	if !authorized(r.Header.Get("Authorization"), h.config.IngestToken) {
		resultName = "auth_rejected"
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	// This budget includes queue residence, not just the eventual HTTP call.
	ctx, cancel := context.WithTimeout(r.Context(), h.config.RequestTimeout)
	defer cancel()
	// OTLP/HTTP exporters send protobuf by default, or JSON when configured for it.
	mediaType := strings.ToLower(strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0]))
	if mediaType != "application/json" && mediaType != protobufMediaType {
		writeError(w, http.StatusUnsupportedMediaType, "content type must be application/x-protobuf or application/json")
		return
	}
	// OTLP/HTTP exporters commonly gzip by default. Identity and gzip are the only
	// encodings accepted; anything else is refused rather than forwarded unread.
	encoding := strings.ToLower(strings.TrimSpace(r.Header.Get("Content-Encoding")))
	if encoding != "" && encoding != "identity" && encoding != "gzip" {
		resultName = "unsupported_encoding"
		writeError(w, http.StatusUnsupportedMediaType, "content encoding must be gzip or absent")
		return
	}

	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, h.config.MaxBodyBytes))
	if err != nil {
		var maxBytesError *http.MaxBytesError
		if errors.As(err, &maxBytesError) {
			resultName = "payload_too_large"
			writeError(w, http.StatusRequestEntityTooLarge, "payload exceeds configured limit")
			return
		}
		if errors.Is(r.Context().Err(), context.Canceled) || errors.Is(err, context.Canceled) {
			resultName = "client_canceled"
			return
		}
		if errors.Is(r.Context().Err(), context.DeadlineExceeded) ||
			errors.Is(ctx.Err(), context.DeadlineExceeded) ||
			errors.Is(err, context.DeadlineExceeded) {
			resultName = "deadline_exceeded"
			writeError(w, http.StatusGatewayTimeout, "collector request deadline exceeded")
			return
		}
		writeError(w, http.StatusBadRequest, "request body could not be read")
		return
	}
	if encoding == "gzip" {
		decoded, decodeErr := decompress(body, h.config.MaxBodyBytes)
		if decodeErr != nil {
			if errors.Is(decodeErr, errDecompressedTooLarge) {
				resultName = "payload_too_large"
				writeError(w, http.StatusRequestEntityTooLarge, "payload exceeds configured limit")
				return
			}
			writeError(w, http.StatusBadRequest, "gzip request body could not be decoded")
			return
		}
		// The API is forwarded the plain body; the collector does not re-encode evidence.
		body = decoded
	}
	if mediaType == protobufMediaType {
		if validateTraceExport(body) != nil {
			writeError(w, http.StatusBadRequest, "body must be an OTLP protobuf trace request with resource spans")
			return
		}
	} else {
		var envelope struct {
			ResourceSpans []json.RawMessage `json:"resourceSpans"`
		}
		if len(body) == 0 || json.Unmarshal(body, &envelope) != nil || envelope.ResourceSpans == nil {
			writeError(w, http.StatusBadRequest, "body must be an OTLP JSON trace request with resourceSpans")
			return
		}
	}

	request := forwardRequest{ctx: ctx, payload: body, contentType: mediaType, result: make(chan forwardResult, 1)}
	if ctx.Err() != nil {
		if r.Context().Err() != nil {
			resultName = "client_canceled"
		} else {
			resultName = "deadline_exceeded"
			writeError(w, http.StatusGatewayTimeout, "collector request deadline exceeded")
		}
		return
	}
	if h.spool != nil {
		// Written durably before any attempt, so a crash from here on loses nothing.
		id, err := h.spool.put(mediaType, body, started)
		if err != nil {
			resultName = "spool_full"
			if !errors.Is(err, errSpoolFull) {
				resultName = "spool_unavailable"
				slog.Error("telemetry spool write failed")
			}
			w.Header().Set("Retry-After", "5")
			writeError(w, http.StatusServiceUnavailable, "collector spool cannot accept more telemetry")
			return
		}
		request.spoolID = id
	}
	// Until a worker takes the batch, a refusal here is the exporter's to retry.
	refuse := func() {
		if request.spoolID != "" {
			h.spool.release(request.spoolID)
		}
	}
	h.queueMu.Lock()
	if h.closed {
		h.queueMu.Unlock()
		refuse()
		resultName = "shutting_down"
		writeError(w, http.StatusServiceUnavailable, "collector is shutting down")
		return
	}
	select {
	case h.queue <- request:
		h.queueMu.Unlock()
	default:
		h.queueMu.Unlock()
		refuse()
		resultName = "queue_full"
		w.Header().Set("Retry-After", "1")
		writeError(w, http.StatusTooManyRequests, "collector queue is full")
		return
	}
	select {
	case result := <-request.result:
		resultName = result.result
		w.Header().Set("Content-Type", result.contentType)
		if result.spooled {
			w.Header().Set("Lotuspond-Delivery", "spooled")
		}
		w.WriteHeader(result.status)
		_, _ = w.Write(result.body)
	case <-ctx.Done():
		if r.Context().Err() == nil {
			resultName = "deadline_exceeded"
			writeError(w, http.StatusGatewayTimeout, "collector request deadline exceeded")
		} else {
			resultName = "client_canceled"
		}
		return
	}
}

func (h *handler) forward(ctx context.Context, payload []byte, contentType string) forwardResult {
	started := time.Now()
	if h.metrics != nil {
		defer func() { h.metrics.observeForward(time.Since(started)) }()
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, h.config.UpstreamURL.String(), bytes.NewReader(payload))
	if err != nil {
		return safeUpstreamError()
	}
	request.Header.Set("Authorization", "Bearer "+h.config.APIKey)
	request.Header.Set("Content-Type", contentType)
	request.Header.Set("User-Agent", "lotuspond-collector/0.1")
	response, err := h.client.Do(request)
	if err != nil {
		// Transport errors may embed the configured URL and project query. Keep
		// operator logs useful without copying destination or tenant identifiers.
		slog.Warn("telemetry forward failed")
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return forwardResult{status: http.StatusGatewayTimeout, contentType: "application/json", body: []byte(`{"error":"telemetry upstream unavailable"}`), result: "deadline_exceeded", retryable: true}
		}
		return safeUpstreamError()
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		slog.Warn("telemetry upstream rejected request", "status", response.StatusCode)
		status := http.StatusBadGateway
		if response.StatusCode == http.StatusTooManyRequests || response.StatusCode == http.StatusServiceUnavailable {
			status = response.StatusCode
		}
		result := "upstream_rejected_5xx"
		if response.StatusCode < 500 {
			result = "upstream_rejected_4xx"
		}
		unanswered := response.StatusCode >= 500 || response.StatusCode == http.StatusTooManyRequests
		return forwardResult{status: status, contentType: "application/json", body: []byte(`{"error":"upstream rejected telemetry"}`), result: result, retryable: unanswered, retryAfter: retryAfter(response.Header.Get("Retry-After"))}
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, (64<<10)+1))
	// An unreadable answer may hide a stored batch; a replay is safe because the API treats
	// a repeated span as an idempotent replay.
	invalid := forwardResult{status: http.StatusBadGateway, contentType: "application/json", body: []byte(`{"error":"telemetry upstream unavailable"}`), result: "upstream_invalid_response", retryable: true}
	if err != nil || len(body) > 64<<10 {
		return invalid
	}
	// The answer is in the request's encoding: a protobuf export gets an
	// ExportTraceServiceResponse, whose partial success tells the exporter how many spans were
	// not kept. Relay only a well-formed one.
	if contentType == protobufMediaType {
		if validateTraceResponse(body) != nil {
			return invalid
		}
		return forwardResult{status: response.StatusCode, contentType: protobufMediaType, body: body, result: "accepted"}
	}
	if !json.Valid(body) {
		return invalid
	}
	return forwardResult{status: response.StatusCode, contentType: "application/json", body: body, result: "accepted"}
}

func authorized(header, token string) bool {
	const prefix = "Bearer "
	if !strings.HasPrefix(header, prefix) {
		return false
	}
	presented := strings.TrimPrefix(header, prefix)
	return len(presented) == len(token) && subtle.ConstantTimeCompare([]byte(presented), []byte(token)) == 1
}

func safeUpstreamError() forwardResult {
	return forwardResult{status: http.StatusBadGateway, contentType: "application/json", body: []byte(`{"error":"telemetry upstream unavailable"}`), result: "upstream_transport_error", retryable: true}
}

// retryAfter reads a Retry-After of whole seconds, bounded to five minutes.
func retryAfter(value string) time.Duration {
	seconds, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil || seconds <= 0 {
		return 0
	}
	return min(time.Duration(seconds)*time.Second, 5*time.Minute)
}

func writeError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": message})
}
