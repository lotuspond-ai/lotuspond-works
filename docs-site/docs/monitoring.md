# Production monitoring

The Go collector accepts bounded OTLP/HTTP trace requests, in binary protobuf or JSON, and relays them to the workspace-scoped Lotuspond API.

## Producer configuration

Set the OTLP HTTP endpoint to the collector base URL. The trace path is the standard `/v1/traces`, and the request must carry `Authorization: Bearer <COLLECTOR_INGEST_TOKEN>`. An OpenTelemetry SDK needs nothing else: its default `http/protobuf` protocol works, as does `http/json`, with or without gzip. For example:

```sh
OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.internal:4318
OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer ${COLLECTOR_INGEST_TOKEN}"
```

An exporter can also send straight to the API at `/api/v1/telemetry/otlp?project_id=<project>` with a workspace API key that holds the `ingest` scope; set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` to that full URL, because the API does not serve the standard `/v1/traces` path. A protobuf export is answered with the standard `ExportTraceServiceResponse`: empty when every span was kept, otherwise a partial success naming how many were not and why. A JSON export keeps Lotuspond's JSON summary with `accepted_spans`, `rejected_spans`, `quota_rejected_spans` and `evaluation_job_ids`.

Both encodings are stored identically. A protobuf body is decoded into the canonical OTLP/JSON form, with hex trace and span identifiers, integer enums and 64-bit integers as decimal strings, before the same validation and redaction the JSON path applies. Span events and links are not retained by either path.

The collector validates JSON syntax and the presence of `resourceSpans`, or the protobuf wire format against the OTLP trace schema, limits request size to the API's 2 MiB maximum, applies an in-memory queue bound, disables upstream redirects, and uses a deadline that includes queue residence and upstream execution. It ignores ambient proxies, refuses redirects, and rejects upstream JSON over 64 KiB. Shutdown drains admitted work and rejects late submissions safely. `LOTUSPOND_PROJECT_ID` is required so accepted spans attach to one authorized project and its monitors. It exposes `GET /healthz` for liveness. Queue saturation returns `429` with `Retry-After: 1`.

## Deliberate limits

The collector accepts the `Content-Encoding: gzip` that OTLP/HTTP exporters send by default and forwards plain JSON upstream; a gzip body must be a single member and its decompressed size is bounded by the same budget as the wire body, so a small highly compressible payload is refused with 413 rather than expanded. `POST /api/v1/telemetry/otlp` applies the same rule, and both refuse any other encoding with 415. The collector does not implement OTLP metrics, logs, profiles, gRPC or sampling. By default it keeps nothing on disk: a batch it cannot forward is refused and the producer's own retry policy keeps it. Set `COLLECTOR_SPOOL_DIR` to make it durable: every admitted batch is fsynced before forwarding, a batch the API cannot answer is acknowledged with `202` and `Lotuspond-Delivery: spooled` and replayed until the API stores or refuses it, across restarts and crashes, and a full spool answers 503 so nothing is dropped silently. Delivery is then at least once, which the API absorbs because a repeated span is an idempotent replay. A batch the API refuses during replay is kept in the spool's `rejected/` directory for the operator. See the collector README for the directory rules and metrics, and measure throughput and loss under expected load before using it for release-critical evidence.

The API's `GET /api/v1/health` is liveness. `GET /api/v1/ready` is readiness and should cover required runtime dependencies. Probe them separately.

Failed and unavailable assessments open distinct, evidence-linked incidents. Use the console to review, resolve, and reopen them. Optional signed webhook delivery runs in a separate worker; see [incidents and alerts](incidents.md) for configuration and delivery semantics.

Do not put prompts, credentials, session cookies, raw response bodies, or confidential customer content in resource attributes. Apply data minimization at the producer before export. As a backstop, Lotuspond stores `[redacted]` for any attribute whose name mentions a password, secret, authorization, cookie, API key or token; a name ending in `tokens` that holds a non-negative integer, such as `gen_ai.usage.input_tokens`, is a usage count and is kept.

## Allowance and retention

A workspace's subscription level decides how many traces Lotuspond accepts each month and how long it promises to keep them. Over the allowance, `POST /api/v1/traces` returns 402 and the OTLP endpoint reports `quota_rejected_spans` separately from malformed spans so a producer can tell the two apart. Usage counts server receipt time, so backdating a span does not move it into a different month.

Retention decides deletion, not visibility. Traces stay readable and exportable until `npm run retention` removes them. That job prints what it would delete and changes nothing unless you pass `--apply`; it refuses a run larger than `--max-deletions` because an unexpectedly large expiry is more likely a misconfigured plan than a real one, and deletion cannot be undone. Every workspace it touches gets an audit record naming the plan and cutoff that justified the removal. Run it on a schedule you choose — Lotuspond does not delete evidence on its own.

`GET /api/v1/traces/export` works on every plan and carries its own SHA-256 over the canonical JSON. A subscription limits how much evidence is accepted and kept; it never limits taking your own evidence with you.
