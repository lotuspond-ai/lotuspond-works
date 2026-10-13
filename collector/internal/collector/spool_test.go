package collector

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// fakeAPI answers like the Lotuspond API in one of four modes and records what it stored.
type fakeAPI struct {
	server  *httptest.Server
	mode    atomic.Value // "down" (503), "ok", "refuse" (402) or "block"
	mu      sync.Mutex
	stored  []storedBatch
	gate    chan struct{}
	arrived atomic.Int64
}

type storedBatch struct {
	contentType string
	body        []byte
}

func newFakeAPI(t *testing.T, mode string) *fakeAPI {
	t.Helper()
	api := &fakeAPI{gate: make(chan struct{})}
	api.mode.Store(mode)
	api.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		api.arrived.Add(1)
		switch api.mode.Load().(string) {
		case "down":
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		case "refuse":
			w.WriteHeader(http.StatusPaymentRequired)
			return
		case "block":
			<-api.gate
		}
		api.mu.Lock()
		api.stored = append(api.stored, storedBatch{r.Header.Get("Content-Type"), body})
		api.mu.Unlock()
		if r.Header.Get("Content-Type") == protobufMediaType {
			w.Header().Set("Content-Type", protobufMediaType)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted_spans":1}`))
	}))
	t.Cleanup(api.server.Close)
	return api
}

func (a *fakeAPI) batches() []storedBatch {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]storedBatch(nil), a.stored...)
}

func spoolConfig(t *testing.T, api *fakeAPI, dir string) Config {
	t.Helper()
	u, _ := url.Parse(api.server.URL)
	config := testConfig(u)
	config.QueueCapacity = 4
	config.SpoolDir = dir
	config.SpoolMaxBytes = 1 << 20
	config.MetricsToken = strings.Repeat("m", 32)
	config.replayBackoff = func(int) time.Duration { return 5 * time.Millisecond }
	return config
}

func openSpooled(t *testing.T, config Config) (http.Handler, func()) {
	t.Helper()
	h, closeWorkers, err := Open(config)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(closeWorkers)
	return h, closeWorkers
}

func send(h http.Handler, contentType string, body []byte) *httptest.ResponseRecorder {
	request := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	request.Header.Set("Content-Type", contentType)
	request.Header.Set("Authorization", "Bearer "+testToken)
	result := httptest.NewRecorder()
	h.ServeHTTP(result, request)
	return result
}

func files(t *testing.T, dir, state string) []string {
	t.Helper()
	entries, err := os.ReadDir(filepath.Join(dir, state))
	if err != nil {
		t.Fatalf("read %s: %v", state, err)
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	return names
}

func eventually(t *testing.T, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !condition() {
		if time.Now().After(deadline) {
			t.Fatal("condition not reached within five seconds")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func metricsText(t *testing.T, h http.Handler, config Config) string {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	request.Header.Set("Authorization", "Bearer "+config.MetricsToken)
	result := httptest.NewRecorder()
	h.ServeHTTP(result, request)
	return result.Body.String()
}

const jsonBatch = `{"resourceSpans":[{"scopeSpans":[]}]}`

func TestSpoolRefusesADirectoryOthersCanReach(t *testing.T) {
	root := t.TempDir()
	shared := filepath.Join(root, "shared")
	if err := os.Mkdir(shared, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(shared, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, err := openSpool(shared, 1<<20); err == nil || !strings.Contains(err.Error(), "chmod 700") {
		t.Fatalf("a group-readable spool was accepted: %v", err)
	}
	private := filepath.Join(root, "private")
	if _, err := openSpool(private, 1<<20); err != nil {
		t.Fatalf("a new private spool was refused: %v", err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(private, link); err != nil {
		t.Fatal(err)
	}
	if _, err := openSpool(link, 1<<20); err == nil {
		t.Fatal("a symbolic link was accepted as the spool")
	}
	if _, err := openSpool("relative/spool", 1<<20); err == nil {
		t.Fatal("a relative spool path was accepted")
	}
}

func TestSpoolWritesDurablyWithinItsCapacity(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "spool")
	s, err := openSpool(dir, 700)
	if err != nil {
		t.Fatal(err)
	}
	first, err := s.put("application/json", bytes.Repeat([]byte("a"), 300), time.Now())
	if err != nil {
		t.Fatalf("first batch refused: %v", err)
	}
	info, err := os.Stat(s.path("pending", first))
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("spooled batch must be owner-only: %v %v", err, info.Mode())
	}
	if _, err := s.put("application/json", bytes.Repeat([]byte("b"), 300), time.Now()); err != errSpoolFull {
		t.Fatalf("expected the capacity to refuse a second batch, got %v", err)
	}
	if got := files(t, dir, "tmp"); len(got) != 0 {
		t.Fatalf("a refused batch left a partial file: %v", got)
	}
	s.release(first)
	if stats := s.stats(time.Now()); stats.pending != 0 || stats.bytes != 0 {
		t.Fatalf("release did not free the batch: %+v", stats)
	}
	if _, err := s.put("application/json", bytes.Repeat([]byte("b"), 300), time.Now()); err != nil {
		t.Fatalf("released capacity was not reusable: %v", err)
	}
}

func TestSpoolRecoversWhatAnInterruptedProcessLeft(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "spool")
	before, err := openSpool(dir, 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	payload := golden(t, goldenRequestHex)
	id, err := before.put(protobufMediaType, payload, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	// A write the crash interrupted before it reached pending/.
	if err := os.WriteFile(filepath.Join(dir, "tmp", "partial"), []byte("half"), 0o600); err != nil {
		t.Fatal(err)
	}

	after, err := openSpool(dir, 1<<20)
	if err != nil {
		t.Fatalf("reopening after a crash failed: %v", err)
	}
	if got := files(t, dir, "tmp"); len(got) != 0 {
		t.Fatalf("partial writes survived a restart: %v", got)
	}
	entry, _ := after.next(time.Now())
	if entry == nil || entry.id != id {
		t.Fatalf("the surviving batch is not due for replay: %+v", entry)
	}
	contentType, restored, err := after.load(id)
	if err != nil || contentType != protobufMediaType || !bytes.Equal(restored, payload) {
		t.Fatalf("the batch did not survive intact: %v %q", err, contentType)
	}
}

func TestHandlerSpoolsWhileTheAPIIsDownAndDeliversLater(t *testing.T) {
	api := newFakeAPI(t, "down")
	dir := filepath.Join(t.TempDir(), "spool")
	config := spoolConfig(t, api, dir)
	h, _ := openSpooled(t, config)
	export := golden(t, goldenRequestHex)

	for _, sent := range []struct {
		contentType string
		body        []byte
	}{{"application/json", []byte(jsonBatch)}, {protobufMediaType, export}} {
		result := send(h, sent.contentType, sent.body)
		if result.Code != http.StatusAccepted || result.Header().Get("Lotuspond-Delivery") != "spooled" {
			t.Fatalf("%s: expected a spooled 202, got %d %q", sent.contentType, result.Code, result.Header().Get("Lotuspond-Delivery"))
		}
		if sent.contentType == protobufMediaType && result.Body.Len() != 0 {
			t.Fatalf("protobuf exporters must receive an empty ExportTraceServiceResponse, got %x", result.Body.Bytes())
		}
	}
	if pending := files(t, dir, "pending"); len(pending) != 2 {
		t.Fatalf("expected two durable batches, found %v", pending)
	}

	api.mode.Store("ok")
	eventually(t, func() bool { return len(files(t, dir, "pending")) == 0 })
	stored := api.batches()
	if len(stored) != 2 {
		t.Fatalf("expected both batches delivered, got %d", len(stored))
	}
	delivered := map[string][]byte{}
	for _, batch := range stored {
		delivered[batch.contentType] = batch.body
	}
	if string(delivered["application/json"]) != jsonBatch || !bytes.Equal(delivered[protobufMediaType], export) {
		t.Fatal("replayed batches differ from what the exporters sent")
	}
	// The counter follows the file's removal, so wait for it rather than racing it.
	eventually(t, func() bool {
		return strings.Contains(metricsText(t, h, config), `lotuspond_collector_spool_replays_total{outcome="delivered"} 2`)
	})
	text := metricsText(t, h, config)
	for _, want := range []string{
		`lotuspond_collector_requests_total{result="spooled"} 2`,
		`lotuspond_collector_spool_replays_total{outcome="delivered"} 2`,
		"lotuspond_collector_spool_pending_batches 0",
		"lotuspond_collector_spool_bytes 0",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("metrics missing %q:\n%s", want, text)
		}
	}
}

func TestAnsweredBatchesLeaveTheSpool(t *testing.T) {
	api := newFakeAPI(t, "ok")
	dir := filepath.Join(t.TempDir(), "spool")
	h, _ := openSpooled(t, spoolConfig(t, api, dir))
	if result := send(h, "application/json", []byte(jsonBatch)); result.Code != http.StatusAccepted || result.Header().Get("Lotuspond-Delivery") != "" {
		t.Fatalf("expected the API's own answer, got %d %q", result.Code, result.Header().Get("Lotuspond-Delivery"))
	}
	// A permanent refusal is relayed to the exporter, which keeps responsibility for it.
	api.mode.Store("refuse")
	if result := send(h, "application/json", []byte(jsonBatch)); result.Code != http.StatusBadGateway {
		t.Fatalf("expected the refusal to reach the exporter, got %d", result.Code)
	}
	if pending := files(t, dir, "pending"); len(pending) != 0 {
		t.Fatalf("answered batches stayed in the spool: %v", pending)
	}
	if rejected := files(t, dir, "rejected"); len(rejected) != 0 {
		t.Fatalf("a refusal the exporter heard was kept: %v", rejected)
	}
}

func TestAReplayTheAPIRefusesIsKeptForTheOperator(t *testing.T) {
	api := newFakeAPI(t, "down")
	dir := filepath.Join(t.TempDir(), "spool")
	config := spoolConfig(t, api, dir)
	h, _ := openSpooled(t, config)
	if result := send(h, "application/json", []byte(jsonBatch)); result.Code != http.StatusAccepted {
		t.Fatalf("expected a spooled 202, got %d", result.Code)
	}
	api.mode.Store("refuse")
	eventually(t, func() bool { return len(files(t, dir, "rejected")) == 1 })
	if pending := files(t, dir, "pending"); len(pending) != 0 {
		t.Fatalf("a refused batch is still pending: %v", pending)
	}
	eventually(t, func() bool {
		text := metricsText(t, h, config)
		return strings.Contains(text, "lotuspond_collector_spool_rejected_batches 1") &&
			strings.Contains(text, `lotuspond_collector_spool_replays_total{outcome="rejected"} 1`)
	})
}

func TestAnUnreadableBatchIsKeptForTheOperator(t *testing.T) {
	api := newFakeAPI(t, "ok")
	dir := filepath.Join(t.TempDir(), "spool")
	s, err := openSpool(dir, 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	id, err := s.put("application/json", []byte(jsonBatch), time.Now())
	if err != nil {
		t.Fatal(err)
	}
	path := s.path("pending", id)
	record, _ := os.ReadFile(path)
	if err := os.WriteFile(path, bytes.Replace(record, []byte("scopeSpans"), []byte("scopeSpanz"), 1), 0o600); err != nil {
		t.Fatal(err)
	}
	openSpooled(t, spoolConfig(t, api, dir))
	eventually(t, func() bool { return len(files(t, dir, "rejected")) == 1 })
	if len(api.batches()) != 0 {
		t.Fatal("a batch that failed its digest was delivered")
	}
}

func TestSpooledBatchesSurviveARestart(t *testing.T) {
	api := newFakeAPI(t, "down")
	dir := filepath.Join(t.TempDir(), "spool")
	config := spoolConfig(t, api, dir)
	// A long schedule, so the first process never retries before it stops.
	config.replayBackoff = func(int) time.Duration { return time.Hour }
	h, closeWorkers := openSpooled(t, config)
	if result := send(h, protobufMediaType, golden(t, goldenRequestHex)); result.Code != http.StatusAccepted {
		t.Fatalf("expected a spooled 202, got %d", result.Code)
	}
	closeWorkers()
	if pending := files(t, dir, "pending"); len(pending) != 1 {
		t.Fatalf("the batch did not outlive its process: %v", pending)
	}

	api.mode.Store("ok")
	openSpooled(t, spoolConfig(t, api, dir))
	eventually(t, func() bool { return len(files(t, dir, "pending")) == 0 })
	if stored := api.batches(); len(stored) != 1 || stored[0].contentType != protobufMediaType {
		t.Fatalf("the next process did not deliver the batch: %+v", stored)
	}
}

func TestAFullSpoolRefusesSoTheExporterKeepsTheBatch(t *testing.T) {
	api := newFakeAPI(t, "down")
	dir := filepath.Join(t.TempDir(), "spool")
	config := spoolConfig(t, api, dir)
	config.SpoolMaxBytes = 64
	h, _ := openSpooled(t, config)
	result := send(h, "application/json", []byte(jsonBatch))
	if result.Code != http.StatusServiceUnavailable || result.Header().Get("Retry-After") == "" {
		t.Fatalf("expected 503 with Retry-After, got %d", result.Code)
	}
	if pending := files(t, dir, "pending"); len(pending) != 0 {
		t.Fatalf("a refused batch was written: %v", pending)
	}
	if text := metricsText(t, h, config); !strings.Contains(text, `lotuspond_collector_requests_total{result="spool_full"} 1`) {
		t.Fatalf("a full spool is not visible in metrics:\n%s", text)
	}
}

func TestABatchTheQueueCannotTakeLeavesTheSpool(t *testing.T) {
	api := newFakeAPI(t, "block")
	dir := filepath.Join(t.TempDir(), "spool")
	config := spoolConfig(t, api, dir)
	config.QueueCapacity = 1
	config.RequestTimeout = 5 * time.Second
	h, _ := openSpooled(t, config)

	var answered sync.WaitGroup
	for range 2 {
		answered.Add(1)
		go func() {
			defer answered.Done()
			send(h, "application/json", []byte(jsonBatch))
		}()
	}
	// One batch is with the worker, held by the API, and the other waits in the queue.
	eventually(t, func() bool {
		return api.arrived.Load() == 1 && strings.Contains(metricsText(t, h, config), "lotuspond_collector_queue_depth 1\n")
	})
	if result := send(h, "application/json", []byte(jsonBatch)); result.Code != http.StatusTooManyRequests {
		t.Fatalf("expected the full queue to refuse, got %d", result.Code)
	}
	if pending := files(t, dir, "pending"); len(pending) != 2 {
		t.Fatalf("a batch the queue refused stayed in the spool: %v", pending)
	}
	close(api.gate)
	answered.Wait()
	eventually(t, func() bool { return len(files(t, dir, "pending")) == 0 })
}

func TestSpoolConfigurationIsExplicitAndBounded(t *testing.T) {
	t.Setenv("LOTUSPOND_PROJECT_ID", testProjectID)
	t.Setenv("LOTUSPOND_API_URL", "http://127.0.0.1:8000")
	t.Setenv("COLLECTOR_INGEST_TOKEN", testToken)
	t.Setenv("LOTUSPOND_API_KEY", "api_key_123456789012345678901234")
	config, err := ConfigFromEnv()
	if err != nil || config.SpoolDir != "" {
		t.Fatalf("the spool must stay off unless configured: %v %q", err, config.SpoolDir)
	}
	t.Setenv("COLLECTOR_SPOOL_DIR", "relative/spool")
	if _, err := ConfigFromEnv(); err == nil {
		t.Fatal("a relative spool directory was accepted")
	}
	t.Setenv("COLLECTOR_SPOOL_DIR", "/var/spool/lotuspond")
	t.Setenv("COLLECTOR_SPOOL_MAX_BYTES", "1024")
	if _, err := ConfigFromEnv(); err == nil {
		t.Fatal("a spool smaller than two batches was accepted")
	}
	t.Setenv("COLLECTOR_SPOOL_MAX_BYTES", "")
	config, err = ConfigFromEnv()
	if err != nil || config.SpoolMaxBytes != 256<<20 {
		t.Fatalf("expected the 256 MiB default, got %v %d", err, config.SpoolMaxBytes)
	}
}
