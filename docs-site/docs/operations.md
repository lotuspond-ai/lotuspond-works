# Operations

Open **Administration → Operations** with a platform-operator account. Production requires an MFA-verified session. Refresh retrieves aggregate state across the installation; it does not expose workspace identifiers, prompts, transcripts, addresses, or credentials. Each response has a reference time. The page does not poll automatically or retain old figures after a failed refresh.

## Queues and worker observations

The evaluation, identity-mail, and incident-alert sections show recorded job states, work eligible for a claim, delayed retries, work in suspended workspaces, expired leases, and the age of the oldest eligible record. Eligibility respects retry limits and active references. Disabled queues can still contain eligible records. These classifications overlap the state totals; do not add them together. Oldest age starts at record creation, including earlier retry time.

Workers record the start of a poll in Redis under three fixed role keys. A marker is fresh for 150 seconds and expires after 300 seconds. A fresh marker means at least one process for that role polled recently. It does not count replicas, confirm task progress, or establish successful delivery. A long evaluation can make a functioning worker's marker stale. Missing, stale, invalid, and unavailable observations need investigation; none alone proves the process has stopped. Configure all API and worker instances to use the same installation's Redis, and synchronize their clocks.

Redis failure leaves database-derived queue counts available, with worker observations explicitly unavailable. Without Redis in development, observations are not configured. A failed database query returns a sanitized error instead of zero counts. Counts come from separate aggregate queries and can change while the snapshot is assembled; this is an operational view, not a transactionally consistent accounting report. Large installations should measure query cost against their expected retained data volume before setting an external polling interval.

The final section counts failed and unknown assessments in the preceding half-open 24-hour UTC window. An unknown result has no conclusive verdict. Queue failures, assessment failures, SMTP acceptance, and webhook acceptance have different meanings; acceptance does not establish human receipt.

## Collector metrics

Set `COLLECTOR_METRICS_TOKEN` through the collector's protected runtime configuration. Use a separate random token of 32–1024 URL-safe characters, distinct from both ingestion and upstream API credentials. An empty setting leaves `/metrics` absent. Authenticated `GET /metrics` returns Prometheus text format with `Cache-Control: no-store`; ingestion credentials cannot scrape it. Restrict network access and terminate HTTPS before exposing it beyond loopback. `/healthz` remains process liveness only.

| Metric | Meaning |
| --- | --- |
| `lotuspond_collector_requests_total{result}` | Completed ingestion HTTP batches classified by a fixed result set |
| `lotuspond_collector_request_duration_seconds` | Histogram of ingestion duration, including queue wait |
| `lotuspond_collector_forward_duration_seconds` | Histogram of upstream forwarding duration |
| `lotuspond_collector_in_flight` | Current ingestion handlers |
| `lotuspond_collector_queue_depth` / `lotuspond_collector_queue_capacity` | Waiting requests and configured in-memory capacity |
| `lotuspond_collector_workers` | Configured forwarding concurrency, not a health measurement |
| `lotuspond_collector_uptime_seconds` | Time since process startup |

The counter results are `accepted`, `auth_rejected`, `invalid_request`, `payload_too_large`, `queue_full`, `deadline_exceeded`, `client_canceled`, `shutting_down`, `upstream_rejected_4xx`, `upstream_rejected_5xx`, `upstream_transport_error`, and `upstream_invalid_response`. Accepted means successful upstream HTTP batch forwarding. Inspect the ingestion response to determine individual span acceptance; this metric says nothing about an assessment verdict. Scrapes do not count as ingestion. Metrics reset on process restart and have no tenant, URL, token, or raw-error labels.

## Scraping and alerts

The repository's `deploy/monitoring/` contains a scrape example, three alert rules, and rule tests. Mount the rules at `/etc/prometheus/lotuspond-alerts.yml` and the dedicated token at `/run/secrets/lotuspond_collector_metrics_token`. Replace the example HTTPS target with your authorized collector endpoint and configure a trusted CA if needed. The files do not install a monitoring service or configure notification delivery.

Initial warnings cover an unavailable scrape for two minutes, queue occupancy above 80% for five minutes, and sustained queue-full rejections. They are starting thresholds for a pilot, not service objectives. Tune them from observed traffic and configure your receiver separately. Validate before applying:

```bash
promtool check config --syntax-only deploy/monitoring/prometheus.example.yml
promtool check rules deploy/monitoring/lotuspond-alerts.yml
promtool test rules deploy/monitoring/alerts.test.yml
```

The example uses Prometheus's [file-based scrape credential configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/). Fixed result labels follow its [instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/); the test fixtures use [alert rule unit tests](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/).

## Investigating failures

1. If eligible work grows, check whether processing is enabled, the workspace is suspended, retry limits are exhausted, or leases have expired. Inspect the corresponding worker process and sanitized logs. A fresh poll with a growing queue can indicate slow work or insufficient capacity.
2. If observations are missing or stale, inspect Redis connectivity, worker configuration and clock alignment. Check running work before restarting a worker; a recent poll is not a progress signal. Do not delete jobs or reset leases to clear the display.
3. If collector rejections increase, inspect upstream readiness and latency before increasing concurrency or capacity. Producers need bounded retries. The collector queue is memory-only and is not preserved across a restart.
4. If `/api/v1/ready` fails, inspect database/schema and Redis availability. Worker observations are intentionally not readiness requirements. Confirm recovery using readiness, a new Operations snapshot, and the outcome of a known synthetic request.

For an isolated local failure drill, use the reviewed image bundle with `deploy/checks/airgap-smoke.py <archive> <trusted-checksum> --failure-recovery`. It stops only its own disposable evaluation worker, observes queued work and a stale poll, restarts it, and checks drain and Redis outage recovery. Allow several minutes for the real freshness threshold. This does not measure production capacity, deployment failover, alert delivery, or customer traffic.
