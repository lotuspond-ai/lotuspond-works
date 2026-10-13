# API contracts

The product API uses JSON at `/api/v1`. Obtain the complete machine-readable OpenAPI document from your own deployment at `/openapi.json`. The source contract and worked execution instructions live in the repository's `docs/API.md`.

## Authenticate

Browser sessions are HttpOnly and require the configured Origin for mutations. Workspace requests identify membership through `x-lotuspond-workspace`. Server integrations use a workspace-bound scoped Bearer key. Do not embed that key in browser code.

An MFA-enabled account receives HTTP 202 from `/auth/login` with `mfa_required`, `challenge_token` and `expires_at`, without an authenticated session. Send the challenge and a fresh authenticator or unused recovery `code` to `/auth/mfa/challenge/complete`. Only successful completion issues the session cookie. Keep both codes and challenges out of persistent storage and logs. `/me.mfa` reports availability, enrollment, current session verification and production operator requirements. See [authenticator and recovery](mfa.md).

## Run evaluations

Create a project and scenarios, register a runner, and queue `/runs`. The runner claims `/runners/:id/claim`, receives a lease and frozen scenarios, and submits exactly one result for each scenario to `/runs/:id/results`. Renew long-running claims. Unknown outcomes are null and block release.

Create `/releases`, then call `/releases/:id/evaluate`. A pass is the only deployable result. Independent approvals use action `release`, must be decided by a different administrator, and require re-evaluation afterward. Export `/releases/:id/evidence` to retain the complete decision.

## Monitor and assess

Create `/monitors` with a project, criteria, and `sample_basis_points` between 0 and 10000. Ingest traces via `/traces` or OTLP/HTTP, protobuf or JSON, at `/telemetry/otlp?project_id=...`. Identical external identifiers are idempotent retries; changed evidence conflicts.

Manual `/traces/:id/assess` and `/voice/sessions/:id/assess` return HTTP 202 and a job. Poll `/evaluation-jobs/:id` for `{job,assessments}`. A completed job can contain an **unknown** assessment: completion means the attempt was recorded, not that the agent passed.

Trace `cost_usd` is nullable. Supplied amounts, including zero, are marked `reported`; omitted amounts are `unavailable`. Historical values of uncertain provenance are `unknown`. `/overview` returns a reported-cost subtotal and counts for each measurement state. OTLP producers may supply the exact span attribute `lotuspond.cost_usd`. These are producer measurements, not reconciled billing.

## Offline identity

Airgap installations use `/auth/offline-enrollment/inspect` with `{token}` and `/auth/offline-enrollment/complete` with `{token,password}`. Completion consumes a pending token and requires normal login afterward. The `/me` identity source distinguishes mailbox verification from operator attestation. Read the [offline identity guide](offline-identity.md) before provisioning an account.

## Voice evidence

Create `/voice/sessions`, append `{segments:[...]}` at `/voice/sessions/:id/segments`, then close finalized evidence at `/voice/sessions/:id/close`. Segment fields are `sequence`, `speaker`, `text`, `start_ms`, `end_ms`, and `is_final`. Final segments cannot change. A transcription application supplies the text; the API does not claim to transcribe audio by recording a session.

## Errors and limits

Errors contain `error` and `message`; input errors identify field paths without echoing submitted credentials. Requests default to a 2 MiB bound. Core workflow lists use `limit`, `before`, and `before_id`. API keys require explicit read/write/run/ingest/gateway scopes. Expired keys, suspended workspaces, stale leases, missing evidence, and missing provider configuration fail closed.

## Incidents

Failed assessments and unavailable evaluations open distinct incidents. `GET /incidents` supports project, state, category and cursor filters. `GET /incidents/:id` returns linked evidence, lifecycle history, and safe alert status. Use `POST /incidents/:id/resolve` with an optional reason, or `/reopen` with a required reason. Repeating the current state does not append duplicate history. Read the [incident runbook](incidents.md) for authorization and optional signed webhook delivery.

## Operational state

`GET /api/v1/admin/operations` requires a platform-operator session, including MFA in production. It returns timestamped installation aggregates for evaluation, mail and alert queues; worker poll observations; database/Redis dependency state; and separate failed/unknown assessment counts for the preceding 24 hours. It contains no tenant identifiers or customer evidence. See [Operations](operations.md) for field meanings, overlapping counts and failure handling.
