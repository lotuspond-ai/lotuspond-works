package collector

import (
	"fmt"
	"io"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

var metricResults = [...]string{
	"accepted",
	"auth_rejected",
	"invalid_request",
	"unsupported_encoding",
	"payload_too_large",
	"queue_full",
	"deadline_exceeded",
	"client_canceled",
	"shutting_down",
	"upstream_rejected_4xx",
	"upstream_rejected_5xx",
	"upstream_transport_error",
	"upstream_invalid_response",
	"spooled",
	"spool_full",
	"spool_unavailable",
}

// Outcomes of background replay attempts for spooled batches.
var replayOutcomes = [...]string{"delivered", "retry", "rejected"}

var durationBuckets = [...]float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30}

type histogram struct {
	count   uint64
	sum     float64
	buckets [len(durationBuckets)]uint64
}

type collectorMetrics struct {
	started  time.Time
	inFlight atomic.Int64
	mu       sync.Mutex
	requests map[string]uint64
	replays  map[string]uint64
	request  histogram
	forward  histogram
}

func newCollectorMetrics() *collectorMetrics {
	requests := make(map[string]uint64, len(metricResults))
	for _, result := range metricResults {
		requests[result] = 0
	}
	replays := make(map[string]uint64, len(replayOutcomes))
	for _, outcome := range replayOutcomes {
		replays[outcome] = 0
	}
	return &collectorMetrics{started: time.Now(), requests: requests, replays: replays}
}

func (m *collectorMetrics) observeReplay(outcome string) {
	m.mu.Lock()
	m.replays[outcome]++
	m.mu.Unlock()
}

func (m *collectorMetrics) observeRequest(result string, elapsed time.Duration) {
	m.mu.Lock()
	m.requests[result]++
	observeHistogram(&m.request, elapsed)
	m.mu.Unlock()
}

func (m *collectorMetrics) observeForward(elapsed time.Duration) {
	m.mu.Lock()
	observeHistogram(&m.forward, elapsed)
	m.mu.Unlock()
}

func observeHistogram(histogram *histogram, elapsed time.Duration) {
	seconds := elapsed.Seconds()
	histogram.count++
	histogram.sum += seconds
	for index, bound := range durationBuckets {
		if seconds <= bound {
			histogram.buckets[index]++
		}
	}
}

func (m *collectorMetrics) write(w io.Writer, queueDepth, queueCapacity, workers int, spooled *spoolStats) {
	m.mu.Lock()
	requests := make(map[string]uint64, len(m.requests))
	for result, count := range m.requests {
		requests[result] = count
	}
	replays := make(map[string]uint64, len(m.replays))
	for outcome, count := range m.replays {
		replays[outcome] = count
	}
	request := m.request
	forward := m.forward
	m.mu.Unlock()

	writeHelpType(w, "lotuspond_collector_requests_total", "Completed ingest HTTP batches by bounded forwarding result; accepted means upstream HTTP batch success.", "counter")
	for _, result := range metricResults {
		_, _ = fmt.Fprintf(w, "lotuspond_collector_requests_total{result=%q} %d\n", result, requests[result])
	}
	writeHistogram(w, "lotuspond_collector_request_duration_seconds", "Ingest request wall-clock duration in seconds.", request)
	writeHistogram(w, "lotuspond_collector_forward_duration_seconds", "Upstream forwarding duration in seconds.", forward)
	writeGauge(w, "lotuspond_collector_in_flight", "Ingest handlers currently in flight.", m.inFlight.Load())
	writeGauge(w, "lotuspond_collector_queue_depth", "Requests currently waiting in the in-memory queue.", int64(queueDepth))
	writeGauge(w, "lotuspond_collector_queue_capacity", "Configured in-memory queue capacity.", int64(queueCapacity))
	writeGauge(w, "lotuspond_collector_workers", "Configured forwarding worker count.", int64(workers))
	writeFloatGauge(w, "lotuspond_collector_uptime_seconds", "Collector process uptime in seconds.", time.Since(m.started).Seconds())
	if spooled == nil {
		return
	}
	writeHelpType(w, "lotuspond_collector_spool_replays_total", "Background delivery attempts for spooled batches by outcome.", "counter")
	for _, outcome := range replayOutcomes {
		_, _ = fmt.Fprintf(w, "lotuspond_collector_spool_replays_total{outcome=%q} %d\n", outcome, replays[outcome])
	}
	writeGauge(w, "lotuspond_collector_spool_pending_batches", "Spooled batches not yet stored or refused by the API.", int64(spooled.pending))
	writeGauge(w, "lotuspond_collector_spool_rejected_batches", "Spooled batches the API refused, kept for the operator in rejected/.", int64(spooled.rejected))
	writeGauge(w, "lotuspond_collector_spool_bytes", "Bytes held in pending and rejected spool files.", spooled.bytes)
	writeGauge(w, "lotuspond_collector_spool_capacity_bytes", "Configured spool capacity in bytes.", spooled.capacity)
	writeFloatGauge(w, "lotuspond_collector_spool_oldest_pending_seconds", "Age of the oldest pending spooled batch, or zero when none is pending.", spooled.oldest.Seconds())
}

func writeHelpType(w io.Writer, name, help, metricType string) {
	_, _ = fmt.Fprintf(w, "# HELP %s %s\n# TYPE %s %s\n", name, help, name, metricType)
}

func writeGauge(w io.Writer, name, help string, value int64) {
	writeHelpType(w, name, help, "gauge")
	_, _ = fmt.Fprintf(w, "%s %d\n", name, value)
}

func writeFloatGauge(w io.Writer, name, help string, value float64) {
	writeHelpType(w, name, help, "gauge")
	_, _ = fmt.Fprintf(w, "%s %s\n", name, formatFloat(value))
}

func writeHistogram(w io.Writer, name, help string, value histogram) {
	writeHelpType(w, name, help, "histogram")
	for index, bound := range durationBuckets {
		_, _ = fmt.Fprintf(w, "%s_bucket{le=%q} %d\n", name, formatFloat(bound), value.buckets[index])
	}
	_, _ = fmt.Fprintf(w, "%s_bucket{le=\"+Inf\"} %d\n", name, value.count)
	_, _ = fmt.Fprintf(w, "%s_sum %s\n%s_count %d\n", name, formatFloat(value.sum), name, value.count)
}

func formatFloat(value float64) string {
	return strings.ToLower(strconv.FormatFloat(value, 'g', -1, 64))
}
