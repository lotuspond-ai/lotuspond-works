# Incidents and alerts

An assessment that finishes with `failed` opens an **Agent assessment failed** incident. An `unknown` assessment opens an **Assessment unavailable** incident. The latter means the evaluation could not establish an outcome; it is not evidence that the agent failed. Successful assessments do not open incidents. The same behavior applies to monitored traces, manually requested trace assessments, closed voice-session assessments, and exhausted evaluation-job leases.

Each automatic incident links its project, assessment, trace or voice session, and monitor when applicable. It records the evaluator's retained provenance. An assessment creates at most one incident, and a stale worker lease cannot create a second result. Existing assessments are not retrospectively converted during database migration.

## Review and resolve

Open **Production evidence → Incidents** in the console. Filter by project, state, or category, inspect the linked evidence, and record a resolution reason. Reopening a resolved incident requires a reason. History preserves creation, resolution, and reopening events; a repeated resolve/reopen request in the same state does not add another event. A later successful assessment does not automatically resolve an earlier incident.

Viewers can inspect records. Editors, administrators, and owners can report and change incidents; API keys need the corresponding `read` or `write` scope. Every operation remains workspace-scoped. Manual incidents remain available for failures discovered outside the evaluator.

## Optional signed webhooks

The operator configures destinations using `LOTUSPOND_ALERT_WEBHOOKS`, a JSON array of:

```json
[
  {
    "name": "operations",
    "url": "https://alerts.example.com/lotuspond",
    "secret": "resolve-a-unique-secret-of-at-least-32-characters",
    "enabled": false
  }
]
```

Resolve real secrets outside the repository. Destinations are disabled by default and must be explicitly enabled. These are trusted operator receivers for **all workspaces** on this installation. They are not customer-managed per-organization destinations. API clients cannot select a URL, change an endpoint, or supply a signing secret.

Use the same configuration for the API, evaluation worker, and alert worker. Start `npm run worker:alerts` locally or enable the `alerts` Compose profile. The API and evaluation worker can enqueue records while the delivery worker is stopped; monitor queue age and worker liveness separately. Airgap installations keep the list empty and the profile disabled.

Events are `incident.opened`, `incident.resolved`, and `incident.reopened`. Their payload contains version, event id, occurrence time, and incident identifiers, category, source, severity, and state. It excludes names, titles, reasons, prompts, transcript text, credentials, and model output. Receivers with additional authorization can fetch the detailed incident separately.

The worker sends these headers:

| Header                  | Meaning                                               |
| ----------------------- | ----------------------------------------------------- |
| `X-Lotuspond-Delivery`  | Stable delivery id; deduplicate retries using this id |
| `X-Lotuspond-Event`     | Event type                                            |
| `X-Lotuspond-Timestamp` | Current Unix timestamp in seconds                     |
| `X-Lotuspond-Signature` | `v1=` followed by lowercase HMAC-SHA256 hex           |

Verify the signature over the exact timestamp header, a literal period, and the raw request bytes: `HMAC_SHA256(secret, timestamp + "." + body)`. Use a constant-time comparison, reject timestamps outside your accepted clock-skew window, and persist delivery ids for duplicate detection. Parse JSON only after signature verification. Retries keep the same event/payload/delivery id but receive a fresh timestamp and signature.

Only HTTPS is accepted. URLs cannot include embedded credentials, query parameters, or fragments. The client ignores ambient proxies and refuses redirects. The configured timeout covers the whole HTTP attempt and defaults to 10 seconds, with a maximum of 30. Payloads are capped at 32 KiB; response bodies are not read or retained. HTTP 2xx records **Webhook accepted**, not human acknowledgement or successful downstream handling.

Attempts are leased and limited to five. Connection failures, timeouts, HTTP 408/425/429, and 5xx responses retry with bounded exponential delays. Other responses fail terminally. Missing or disabled destinations fail closed; suspended workspaces cannot be newly claimed. An expired worker cannot overwrite a newer lease. Delivery can be duplicated after ambiguous network acceptance, so receiver deduplication remains required. A configuration change affects queued deliveries with that destination name; drain queued work before repurposing a destination.

## Evidence boundary

Local tests exercise creation, tenancy, lifecycle concurrency, signing, retries, stale leases, disabled delivery, and mocked HTTP responses. The offline installation drill exercises incident persistence with alerts disabled. No external alert receiver is configured or verified by these checks.
