# Production monitoring

The Lotuspond API accepts bounded OTLP/HTTP trace requests, in binary protobuf or JSON, at the workspace-scoped `/api/v1/telemetry/otlp` endpoint.

## Producer configuration

Send traces to `/api/v1/telemetry/otlp?project_id=<project>` with a workspace API key that holds the `ingest` scope. Set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` to that full URL, because the API does not serve the standard `/v1/traces` path. An OpenTelemetry SDK needs nothing else: its default `http/protobuf` protocol works, as does `http/json`, with or without gzip. For example:

```sh
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://lotuspond.example.com/api/v1/telemetry/otlp?project_id=${LOTUSPOND_PROJECT_ID}"
OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer ${LOTUSPOND_API_KEY}"
```

A protobuf export is answered with the standard `ExportTraceServiceResponse`: empty when every span was kept, otherwise a partial success naming how many were not and why. A JSON export keeps Lotuspond's JSON summary with `accepted_spans`, `rejected_spans` and `evaluation_job_ids`.

Both encodings are stored identically. A protobuf body is decoded into the canonical OTLP/JSON form, with hex trace and span identifiers, integer enums and 64-bit integers as decimal strings, before the same validation and redaction the JSON path applies. Span events and links are not retained by either path.

## Deliberate limits

The endpoint accepts the `Content-Encoding: gzip` that OTLP/HTTP exporters send by default; a gzip body must be a single member and its decompressed size is bounded by the same 2 MiB budget as the wire body, so a small highly compressible payload is refused with 413 rather than expanded. Any other encoding is refused with 415. OTLP metrics, logs, profiles, gRPC and server-side sampling are not implemented. Lotuspond keeps nothing for a producer: a request the API cannot answer is the producer's to retry, so configure the exporter's own retry and queue. A repeated span is an idempotent replay, so retries do not duplicate traces. The Go collector that once relayed and spooled exports was retired on 2026-11-05.

The API's `GET /api/v1/health` is liveness. `GET /api/v1/ready` is readiness and should cover required runtime dependencies. Probe them separately.

Failed and unavailable assessments open distinct, evidence-linked incidents. Use the console to review, resolve, and reopen them. Optional signed webhook delivery runs in a separate worker; see [incidents and alerts](incidents.md) for configuration and delivery semantics.

Do not put prompts, credentials, session cookies, raw response bodies, or confidential customer content in resource attributes. Apply data minimization at the producer before export. As a backstop, Lotuspond stores `[redacted]` for any attribute whose name mentions a password, secret, authorization, cookie, API key or token; a name ending in `tokens` that holds a non-negative integer, such as `gen_ai.usage.input_tokens`, is a usage count and is kept.

## Export

`GET /api/v1/traces/export` returns the retained traces as canonical JSON with its own SHA-256. Lotuspond has no monthly trace allowance and deletes no trace on its own; the trace-plan ladder and its retention job were retired on 2026-11-05.
