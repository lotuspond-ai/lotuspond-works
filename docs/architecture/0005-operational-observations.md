# 0005: Aggregate operational observations

Status: accepted for the local implementation, 2026-09-21.

Operators need to distinguish pending evaluations, unavailable infrastructure and inconclusive evidence. The API exposes a read-only installation-wide aggregate endpoint under existing platform-operator authorization, including production MFA. It queries the existing queue tables without a schema change and returns no customer evidence or identifiers. Failed database queries return an error; Redis observation failures do not suppress database counts.

Three expiring Redis keys record poll boundaries for evaluation, mail and alert roles. Writes are best effort and cannot fail the worker's business operation. Fixed keys avoid per-replica cardinality and retain only a short observation window. A marker describes at least one poll, not complete fleet health or progress of a long-running task. Readiness continues to require its database/schema and configured Redis dependencies, independent of optional worker observations.

The Go collector offers an optional separately authenticated Prometheus endpoint with fixed result labels, histograms and queue/concurrency gauges. The existing standard library suffices for this bounded exposition. Ingestion success counts HTTP batches and does not claim individual span acceptance. Prometheus rule examples and tests remain operator-owned configuration; the product does not silently provision a monitoring service or notification destination.

Queue aggregates use separate database reads and manual console refresh. They are not an atomic accounting snapshot, nor a capacity benchmark. Role freshness is 150 seconds and marker retention is 300 seconds. Future changes to task progress signals, replica inventory, historical metrics, SLOs or large-volume aggregate costs require separate measured requirements.

See the [operations runbook](../../docs-site/docs/operations.md) for semantics, limitations and failure handling.
