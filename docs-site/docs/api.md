# API contracts

The product API uses JSON at `/api/v1`. Obtain the complete machine-readable OpenAPI document from your own deployment at `/openapi.json`. The source contract and worked execution instructions live in the repository's `docs/API.md`.

## Authenticate

Browser sessions are HttpOnly and require the configured Origin for mutations. Workspace requests identify membership through `x-lotuspond-workspace`. Server integrations use a workspace-bound scoped Bearer key. Do not embed that key in browser code.

An MFA-enabled account receives HTTP 202 from `/auth/login` with `mfa_required`, `challenge_token` and `expires_at`, without an authenticated session. Send the challenge and a fresh authenticator or unused recovery `code` to `/auth/mfa/challenge/complete`. Only successful completion issues the session cookie. Keep both codes and challenges out of persistent storage and logs. `/me.mfa` reports availability, enrollment, current session verification and production operator requirements. See [authenticator and recovery](mfa.md).

## Run evaluations

Create a project and scenarios, register a runner, and queue `/runs`. The runner claims `/runners/:id/claim`, receives a lease and frozen scenarios, and submits exactly one result for each scenario to `/runs/:id/results`. Renew long-running claims. Unknown outcomes are null and block release.

Create `/releases`, then call `/releases/:id/evaluate`. A pass is the only deployable result. Independent approvals use action `release`, must be decided by a different administrator, and require re-evaluation afterward. Export `/releases/:id/evidence` to retain the complete decision.

## Stored traces

Trace ingestion, monitors and trace assessments were retired on 2026-11-11. Traces stored before then stay readable: `GET /traces`, `GET /traces/:id` (`{trace,assessments}`) and `GET /traces/export`. Their nullable `cost_usd` keeps its `reported`, `unavailable` or `unknown` marker, and `/overview` returns a reported-cost subtotal with counts for each state. These are producer measurements, not reconciled billing. `POST /traces` and `POST /telemetry/otlp` now return 405.


## Errors and limits

Errors contain `error` and `message`; input errors identify field paths without echoing submitted credentials. Requests default to a 2 MiB bound. Core workflow lists use `limit`, `before`, and `before_id`. API keys require explicit read/write/run/execute/execution_signal scopes. Expired keys, suspended workspaces, stale leases, missing evidence, and missing provider configuration fail closed.

## Stored incidents

Incidents and their signed alerts were retired on 2026-11-11; nothing opens, resolves, reopens or delivers them. `GET /incidents` still supports project, state, category and cursor filters, and `GET /incidents/:id` returns linked evidence, lifecycle history and stored alert status.

## Workflow package registry

Publish raw workflow-package v1 JSON to `POST /projects/:project_id/packages` with `write` authority and an optional 1–128 visible-ASCII `Idempotency-Key`. Package bodies are bounded to 1 MiB while reading and checked by the complete validator. New publication returns 201, identical canonical replay returns 200, and conflicting version/key content returns 409. The request key is workspace-scoped and binds project plus canonical content.

Use `GET /packages?project_id=...&limit=...&cursor=...` for metadata only (`limit` 1–100), `GET /packages/:id` for one publication and `/packages/:id/content` for exact stored canonical bytes with SHA-256 and canonicalizer headers. Pass list `next` as the next cursor. Every read requires explicit `read` scope; write keys cannot read implicitly. Foreign tenants receive 404. No update/delete API exists.

`GET /package-contracts` advertises admission schema/validator/canonicalizer revisions and the effective publication byte bound (the smaller of `LOTUSPOND_MAX_REQUEST_BYTES` and 1 MiB). Publication stores a validated package; it does not grant trust, release approval, runtime compatibility or execution authority. Content reads preserve old bytes and revisions without revalidation. Names/instructions/manifests never appear in lists or audits. See the repository's `docs/API.md` and reviewed v11-to-v12 plan in `docs/DATABASE.md` for full semantics.
