# Lotuspond telemetry collector

This bounded collector receives OTLP/HTTP traces at `POST /v1/traces` and forwards the unchanged request to the Lotuspond API at `POST /api/v1/telemetry/otlp`. It accepts binary protobuf (`application/x-protobuf`, the OpenTelemetry SDK default) and JSON (`application/json`), and the `Content-Encoding: gzip` that exporters commonly add, forwarding the plain body upstream with its original media type and bounding the decompressed body by the same `MaxBodyBytes` budget as the wire body. A protobuf body is checked as a well-formed `ExportTraceServiceRequest` against the opentelemetry-proto v1 trace schema (`internal/collector/otlp.go`: known fields with their declared wire types, UTF-8 strings, bounded nesting, unknown fields skipped) but never re-encoded; the API decodes it. The collector intentionally does not claim general OpenTelemetry Collector compatibility: metrics, logs, profiles and batching are outside this component, and retries across API outages exist only through the optional spool below.

`GET /healthz` is an unauthenticated liveness endpoint and contains no configuration data. Ingest requires `Authorization: Bearer <COLLECTOR_INGEST_TOKEN>`. The upstream request uses the separate, workspace-bound `LOTUSPOND_API_KEY`, which must have only the telemetry ingest scope.

`GET /metrics` is disabled unless `COLLECTOR_METRICS_TOKEN` is set to a distinct 32–1024 character URL-safe random token. When enabled, it requires `Authorization: Bearer <COLLECTOR_METRICS_TOKEN>`; the ingest token is not accepted. The Prometheus text response contains fixed-label collector request outcomes, request and forwarding duration histograms, queue depth and capacity, in-flight handlers, configured worker count, and process uptime. The `accepted` result means the upstream accepted the HTTP ingestion batch; it does not claim that every span was retained or that any assessment passed. Metrics never include project IDs, upstream URLs, tokens, payloads, error text, or arbitrary status-code labels.

Required configuration:

| Variable                  | Purpose                                                            |
| ------------------------- | ------------------------------------------------------------------ |
| `COLLECTOR_INGEST_TOKEN`  | At least 24 characters; authenticates local producers              |
| `LOTUSPOND_API_URL`       | HTTPS API base URL, or loopback HTTP for local development         |
| `LOTUSPOND_API_KEY`       | At least 24 characters; scoped API key for upstream ingest         |
| `LOTUSPOND_PROJECT_ID`    | UUID of the authorized project whose monitors receive these traces |
| `COLLECTOR_METRICS_TOKEN` | Optional distinct metrics bearer token; absent disables `/metrics` |
| `COLLECTOR_SPOOL_DIR`     | Optional absolute path of the durable spool; absent keeps it off   |

Bounds can be adjusted with `COLLECTOR_MAX_BODY_BYTES` (default and maximum 2 MiB to match the API request limit), `COLLECTOR_QUEUE_CAPACITY` (128), `COLLECTOR_WORKERS` (4), and `COLLECTOR_REQUEST_TIMEOUT_MS` (5000). `COLLECTOR_LISTEN_ADDRESS` defaults to `:4318`.

The upstream client ignores ambient HTTP/HTTPS proxy variables and refuses redirects. A response must be in the request's encoding and within 64 KiB: valid JSON for a JSON export, or a well-formed `ExportTraceServiceResponse` for a protobuf export, whose partial success tells the exporter how many spans were not kept. Anything else, or an over-limit response, fails with 502 instead of being relayed after truncation. Put any required network routing in the reviewed deployment configuration.

The request deadline includes time spent in the queue. Expired queued work cannot start a fresh full-timeout upstream request. Shutdown drains already admitted work, rejects late submissions with 503, and serializes queue closure so concurrent handlers cannot write to a closed channel. These controls do not make the in-memory queue durable; the spool does.

## Durable spool

Without `COLLECTOR_SPOOL_DIR` the collector is stateless: a batch it cannot forward is answered with 502, 503 or 504 and the exporter keeps it and retries. Set `COLLECTOR_SPOOL_DIR` to an absolute path, `/spool` in the image, and the collector writes every admitted batch there and fsyncs it before forwarding (`internal/collector/spool.go`):

- When the API answers, with success or a permanent refusal such as 400, 401, 402 or 422, the batch leaves the spool and the exporter receives that answer as before.
- When the API cannot answer (a transport failure, a timeout, a 5xx, a 429 or an unreadable response), the batch stays, the exporter receives `202` with `Lotuspond-Delivery: spooled` (an empty `ExportTraceServiceResponse` for protobuf, `{"status":"spooled"}` for JSON), and a background replayer retries it from one second, doubling to five minutes with jitter and honouring `Retry-After`, until the API stores or refuses it. It survives restarts and crashes: a new process replays whatever the last one left.
- A replay the API refuses moves to `rejected/`, because no exporter is waiting to hear it. Inspect it, fix the cause (a revoked key, an exhausted allowance), and move it back to `pending/` to retry, or delete it.
- Delivery is at least once. The API stores a repeated span as an idempotent replay, so a batch sent again after an ambiguous failure never duplicates a trace.
- `COLLECTOR_SPOOL_MAX_BYTES` (default 256 MiB, at least two request bodies) bounds `pending/` and `rejected/` together. A full spool answers 503 with `Retry-After`, so the exporter keeps the batch; nothing is dropped silently.

The directory must be owned by the collector's user and closed to everyone else (mode 700), and the collector refuses to start otherwise; batches are written 0600 and hold workspace evidence but no credentials. Encrypt the volume if the host requires evidence at rest to be encrypted. Each admitted batch costs one fsync, so measure throughput on the target disk. The image ships an empty owner-only `/spool`, and the Compose files mount the `collector-spool` volume there; set `COLLECTOR_SPOOL_DIR=/spool` to use it. With metrics enabled, `lotuspond_collector_spool_pending_batches`, `_rejected_batches`, `_bytes`, `_capacity_bytes`, `_oldest_pending_seconds` and `lotuspond_collector_spool_replays_total{outcome}` show its state, and request results gain `spooled`, `spool_full` and `spool_unavailable`.

`/healthz` reports process liveness only. It does not claim queue capacity, upstream reachability, or successful forwarding; monitor those conditions through the protected metrics endpoint.

Metric names, bounded labels, counters, gauges, and histograms follow the [Prometheus instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/) and [text exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/).
