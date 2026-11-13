# Lotuspond API and workers

The API root is `/api/v1`. JSON errors contain `error` and `message`; validation errors list field paths without echoing submitted secrets. Mutations require `application/json`. The normal request limit is 2 MiB. Readiness is `/ready`; `/health` only reports process liveness. `/changelog` returns the console's `items` collection.

## Authentication and ownership

`POST /auth/register` accepts `{email,name,password,account_type,workspace?,invitation_token?}`. Public registration defaults to disabled; enable it explicitly for open signup. An active matching invitation allows individual signup on a closed installation, but it does not accept the invitation or grant organization membership. `account_type` defaults to `individual`; `organization` requires `workspace`. Every account gets a personal workspace. Development organization signup creates a separate organization and returns its `workspaceId`. Production requires individual signup and mailbox verification before explicit organization creation; an organization signup request returns `identity_unverified`. `POST /organizations {name}` adds an organization later. `GET /me` returns the user, including `email_verified`, their workspaces/roles, and `capabilities` booleans for `mail_enabled` and `registration_enabled`.

`GET /me` also includes `user.identity_verified` and `user.identity_verification_source` (`mailbox` or null). These identity fields do not replace `email_verified`; since ADR-0014 only a verified mailbox verifies an identity.

`POST /auth/login` and `/auth/logout` manage HttpOnly, SameSite=Strict sessions. Mutating browser requests require the configured Origin; production cookies are Secure. Workspace requests carry `x-lotuspond-workspace`. Read-only members cannot mutate data. An account with a stored, unfinished offline enrollment still cannot use password login, sessions, or attributed API keys.

`GET /auth/sessions` lists the signed-in user's active sessions with `id`, `created_at`, `expires_at`, and `current`; no session token or hash is returned. `DELETE /auth/sessions/:id` with an empty JSON body revokes an owned session and returns `{ok,signed_out}`. Other accounts' identifiers return 404. A user can keep at most 20 active sessions; a new sign-in prunes expired sessions and the oldest excess sessions.

`POST /auth/password` accepts `{current_password,new_password}`. The new password must have 12–256 characters and differ from the current password. This operation verifies the current password, revokes every existing browser session, and issues a fresh cookie to the current browser atomically. Account events are audited in the personal workspace. Concurrent sign-in checks lock the account so an old password cannot create a session after the password change commits. Password changes invalidate pending recovery challenges. Forgotten-password recovery uses the separate email challenge flow below.

Headless integrations use `Authorization: Bearer <key>`. Its embedded workspace ownership overrides any client workspace header. Available scopes are `read`, `write`, `run`, `execute` and `execution_signal`. Keys issued with the retired `gateway` or `ingest` scope keep it, and it grants nothing. Human administrator sessions are required to add members, manage keys, create governance policies, or decide approvals. Production organization access and operator actions require verified mailbox ownership. Organization API keys also require an attributed, active, verified creator. Existing unattributed keys require deliberate replacement by a verified administrator; migration never guesses an owner.

## Authenticator MFA

For an account with an enabled authenticator, `POST /auth/login {email,password}` returns HTTP 202 `{mfa_required:true,challenge_token,expires_at}` and creates no authenticated session. Keep the challenge in memory. Complete it using `POST /auth/mfa/challenge/complete {challenge_token,code}` with the current six-digit TOTP or an unused recovery code. Successful completion returns `{ok:true}` and an HttpOnly session cookie. Challenges expire after five minutes and allow at most eight failed code attempts, in addition to account/IP rate limits. Repeating a consumed challenge or code fails.

`GET /me` includes `mfa: {available,enabled,session_verified,recovery_codes_remaining,required_for_privileged}`. These fields are distinct from mailbox identity verification. `required_for_privileged` applies to production platform operators; `/admin/*` enforces verified identity, operator role, configured MFA, and a session bound to the current factor. API keys cannot access platform administration.

| Method/path                                | Contract                                                                                                                                                                  |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| POST `/auth/mfa/enrollment/start`          | Session and `{current_password}` required; rotates sessions and returns `{secret,otpauth_uri,expires_at}` for provisional setup. An enabled factor cannot be overwritten. |
| POST `/auth/mfa/enrollment/confirm`        | Session and `{code}` required; consumes a valid pending setup, enables MFA, rotates sessions and returns `{ok,recovery_codes,message}` once.                              |
| POST `/auth/mfa/recovery-codes/regenerate` | Session and `{current_password,code}` required; verifies a fresh factor/recovery code, replaces all codes and sessions, returns new recovery codes once.                  |
| POST `/auth/mfa/disable`                   | Session and `{current_password,code}` required; removes the factor and recovery material and rotates sessions. Production platform operators receive 403.                 |

### Account change notices

When mail is configured, a password change, a completed password reset, an authenticator enrolment, an authenticator removal and a recovery-code reissue each queue one `security_notice` message to the account mailbox. It names the change and the time it was recorded and deliberately carries no link, token or code, so it cannot be turned into a phishing lure and needs no expiry. It references no challenge or invitation, so the worker never cancels it as stale. A disabled or unconfigured mailbox queues nothing and never blocks the security change; the API response is identical either way and never reports delivery.

### Assisted authenticator reset

Losing both the authenticator and every recovery code has no self-service path, so an organization owner or administrator can clear it for a member: `POST /auth/mfa/members/:id/reset {current_password,reason}`. It must be a different person (`different_administrator_required`), requires the acting administrator's own password, works only inside an organization (`organization_required`), and refuses a platform operator's factor so a workspace cannot clear what protects the installation. It only removes — the factor, recovery codes, pending enrolment, sessions and login challenges — and issues no session, factor or password, so it cannot be used to take over the account. The member is notified out of band and enrols again with their own password. The change is audited with the reason.

Only factor-management responses disclose setup/recovery material, and all API responses carry `Cache-Control: no-store`. No token, code, secret or ciphertext appears in account state or audit details. Password changes preserve the current session's legitimate MFA binding; password reset revokes sessions/challenges but leaves MFA enabled. Configure the separate encryption key and follow the [MFA runbook](../docs-site/docs/mfa.md). Losing all second-factor material has no self-service reset.

## Verification, recovery, and invitations

| Method/path                         | Contract                                                                                                                                            |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| POST `/auth/verification/request`   | Session required; `{}` queues a verification challenge and returns 202 `{status:"accepted"}`. Mail unavailable returns 503.                         |
| POST `/auth/verification/confirm`   | `{token}` consumes the expiring verification challenge once; returns `{ok:true}`.                                                                   |
| POST `/auth/password/reset/request` | `{email}` returns the same 202 `{status:"accepted"}` for eligible and unknown accounts, including when mail is disabled.                            |
| POST `/auth/password/reset/confirm` | `{token,new_password}` consumes the recovery challenge, changes the password, revokes sessions, and returns `{ok:true}` without signing in.         |
| GET `/invitations`                  | Organization administrator lists up to 100 invitations; no token is returned.                                                                       |
| POST `/invitations`                 | `{email,role,days?}` creates an invitation and encrypted queued mail; roles `admin`, `editor`, `viewer`; 1–30 days, default 7.                      |
| POST `/invitations/:id/revoke`      | `{}` revokes a pending invitation in the administrator's organization.                                                                              |
| POST `/invitations/accept`          | `{token}` requires a signed-in account with the matching verified mailbox; membership and consumption are atomic; returns `{ok,workspace_id,role}`. |

Verification and reset challenges expire after 30 minutes. Requests are rate limited; reissuance invalidates older pending challenges of the same kind. Invitation creation does not alter membership, and acceptance does not change an existing member's role. Expired, used, revoked, mismatched, or suspended-organization invitations fail closed.

The mail worker is `npm run worker:mail`, or `python -m lotuspond.mail_worker`. See [identity mail](../docs-site/docs/identity-mail.md) for the protected configuration and opt-in Compose profile. A 202 response means only that the request was accepted; it does not prove SMTP submission or inbox delivery. Outbox ciphertext and token hashes are omitted from API and audit output.

## Stored trace costs

Trace ingestion was retired on 2026-11-11 ([ADR-0013](architecture/0013-trim-to-the-automation-core.md)). Traces stored before then keep nullable `cost_usd` and the server-set `cost_measurement`: `reported` for a supplied value including zero, `unavailable` for an omitted one, and `unknown` for amounts retained through schema upgrades. `GET /overview` returns `monthly_cost_usd`, a nullable subtotal of reported current-month trace costs, plus `monthly_cost_counts: {reported,unavailable,unknown}`. No reported traces means a null subtotal. Nonzero unavailable/unknown counts mean incomplete measurement coverage. Producer-reported costs are not reconciled billing.

## Product endpoints

| Method/path                                                     | Contract                                                                                                                                                     |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| GET/POST `/projects`, `/environments`, `/scenarios`, `/runners` | Scoped resource creation/listing; scenario modality is text, voice, tool or multimodal                                                                       |
| GET/POST `/runs`; GET `/runs/:id`                               | Freeze 1–1,000 scenarios within a 4 MiB canonical UTF-8 snapshot; return run metadata and measured results                                                   |
| POST `/runners/:id/claim`, `/heartbeat`                         | Atomically claim queued/expired work; declared capabilities constrain matching                                                                               |
| POST `/runs/:id/renew`, `/results`, `/cancel`                   | Renew/submit using current lease; exactly one result per frozen scenario; cancel active work                                                                 |
| GET/POST `/policies`, `/releases`                               | Immutable policies and candidate/baseline release definitions                                                                                                |
| POST `/releases/:id/evaluate`; GET `/releases/:id/evidence`     | Retained pass/block/approval_required decisions and versioned hashed exports                                                                                 |
| GET/POST `/approvals`; POST `/approvals/:id/decide`             | Release approval only: a request needs `release_id` and action `release`, or it is refused with 400; a different admin decides once                          |
| GET `/traces`, `/traces/:id`, `/traces/export`                  | Read-only stored traces: `{trace,assessments}` detail and canonical JSON export with `export_sha256`                                                         |
| GET `/incidents`, `/incidents/:id`; GET `/receipts`             | Read-only stored incidents with history and alert records, and stored release receipts                                                                       |
| GET `/audit-events`, `/overview`                                | Workspace action history and measured counts                                                                                                                 |
| GET/POST `/members`, `/api-keys`; POST `/api-keys/:id/revoke`   | Membership and expiring scoped integration access; raw key returned once                                                                                     |
| GET/PATCH `/settings`                                           | Workspace name; GET adds `authenticator_policy`                                                                                                              |
| POST `/auth/mfa/members/:id/reset`                              | `{current_password,reason}`; organization administrator clears another member's lost authenticator                                                           |
| GET `/admin/overview`, `/admin/workspaces`, `/admin/users`      | Explicit platform operator only                                                                                                                              |
| PATCH `/admin/workspaces/:id`                                   | `{status:active                                                                                                                                              | suspended,reason}`; audited; suspension blocks workspace access |

## Retired components

On 2026-11-05 voice sessions, the model gateway and the trace-plan ladder were retired ([ADR-0012](architecture/0012-retire-assurance-extras.md)). Their read routes return 404 and their write routes 405, `/settings` no longer reports `billing` or accepts `monthly_budget_usd`, workspace responses omit `plan` and `monthly_budget_usd`, there is no monthly trace allowance, and `npm run retention` is gone. Stored voice sessions, gateway usage and plans stay in the database untouched. An incident that links a voice session still returns it as `voice_session`.

On 2026-11-11 trace ingestion (`POST /traces` and `POST /telemetry/otlp`), monitors, trace assessments and evaluation jobs, incident reporting and state changes, signed incident alerts, receipt submission and endpoint registration (`/integrations`) were retired ([ADR-0013](architecture/0013-trim-to-the-automation-core.md)). Their write routes return 405 and their read-only routes 404, the assessment and alert workers are gone, and new keys cannot take the `ingest` scope.

Also on 2026-11-11, ADR-0014 retired offline enrollment (`/auth/offline-enrollment/*`), the organization and installation authenticator requirements (`POST /settings/authenticator-policy`, `LOTUSPOND_REQUIRE_MFA`), tool authorization (`POST /authorize` and a policy's `blocked_tools`/`approval_tools` input), and `/admin/operations`. `GET /settings` no longer returns `authenticator_policy`, workspace responses omit `require_mfa`, and `GET /me` no longer reports `capabilities.offline_enrollment_enabled` or `mfa.required_by_installation`. The administrator-authorized `POST /auth/mfa/members/:id/reset` for a member who lost every factor stays. `GET /overview` no longer reports `open_incidents` and `GET /admin/overview` no longer reports `incidents`. Stored traces, assessments, incidents and receipts stay readable through the routes above, the release evidence export keeps its `receipts` array, and every table keeps its rows.

Core workflow lists accept `limit` (1–100), `before`, `before_id`; responses include `items` and `next`. Project-aware lists accept `project_id`. The trace list returns a bounded current collection rather than the full core cursor contract. Read the generated OpenAPI schema at `/openapi.json` and `backend/lotuspond/schemas.py` for exact input bounds. Policy rates accept up to four decimal places; monetary limits accept up to six, preventing silent database rounding from weakening a gate.

## Execution worker

Set `LOTUSPOND_API_URL` (default `http://127.0.0.1:8000/api/v1`), `LOTUSPOND_API_KEY`, and `LOTUSPOND_RUNNER_ID`:

```sh
npm run runner -- /absolute/path/to/adapter.mjs
```

The local adapter exports `async evaluate(input, expected, {revision,signal})`. It executes the real workload and returns `{passed:boolean|null,output:object,costUsd?:number,evidence?:object}`. Honor cancellation. Unknown evidence must remain null; every run result is required. The adapter executes in a customer-controlled process, not inside the API. Use a configured isolated sandbox for untrusted code.

Infrastructure errors and timeouts are unknown outcomes. Omitted cost, or explicit `evidence.cost_measurement="unavailable"`, is not measured zero: aggregate cost is null and a configured maximum-cost policy blocks. Explicitly supplied finite nonnegative cost, including zero, is treated as producer-reported. The HTTP adapter marks cost unavailable because an agent response carries no billing measurement. It supports exact `contains` or `equals` expectations; truncated or unsupported output remains unknown. See `protocol-adapters/README.md` for setup and its local-test evidence boundary.

## Stored incidents

`GET /incidents` accepts `status` (`open` or `resolved`), `category` (`manual`, `assessment_failed`, or `assessment_unavailable`), `project_id`, and the core `limit`/`before`/`before_id` cursor. `GET /incidents/:id` returns `{incident,project,trace,voice_session,assessment,monitor,history,alerts}` with nullable linked records. Alert payloads, endpoint URLs, signing secrets and lease hashes are excluded. Nothing opens, resolves, reopens or delivers incidents any more; incidents for Lotuspond's own executions are planned with human tasks (E05) and verified outcomes (E06).

New run creation rejects larger frozen snapshots with HTTP 413 and `run_snapshot_too_large` before creating a run or audit record. Capability matching occurs before claim row locking; all frozen scenario modalities must be supported, missing modality defaults to text, and an empty runner capability list remains unrestricted. Leases last 15 minutes and renewal resets expiry to 15 minutes from server time. Customer-side clients accept `LOTUSPOND_API_URL` as an origin or as the `/api/v1` base. See [runner execution](../docs-site/docs/runners.md) for subprocess deadlines, cancellation, packaged commands and credential handling.

## Immutable workflow package registry

Publication stores a fully validated declaration; it does not install, approve, trust or execute it. The complete semantic validator checks graphs and references as well as the strict v1 schema. Private manifest text remains workspace data. Secret references remain identifiers and are never resolved here.

| Method/path | Contract |
| --- | --- |
| POST `/projects/:project_id/packages` | Raw v1 manifest JSON, `write` authority, optional `Idempotency-Key`. Returns publication metadata: 201 new version, 200 identical canonical replay, 409 conflicting immutable version or request key. |
| GET `/packages?project_id=...` | `read` authority, metadata only, `limit` 1–100 (default 50), optional opaque `cursor` up to 1024 characters. Returns `{items,next}`; pass `next` as the next request's cursor. |
| GET `/packages/:id` | Scoped publication metadata. |
| GET `/packages/:id/content` | Exact stored canonical JSON bytes with `X-Package-SHA256`, `X-Package-Canonicalization` and quoted digest `ETag`; no revalidation or rewriting on read. |
| GET `/package-contracts` | `read` authority; supported admission schema, validator revision, canonicalization identifier and effective publication byte bound (the smaller of `LOTUSPOND_MAX_REQUEST_BYTES` and 1 MiB). No runtime compatibility claim. |

Workspace membership controls projects; there is no additional project ACL in this slice. Owner/admin/editor can publish, viewer can read. API-key scopes are exact: a `write` key has no implicit `read`. Key ownership overrides the workspace header. Foreign-tenant projects and versions return the normal 404; platform operators have no bypass. Current suspension, expiry/revocation, active creator, verified identity and MFA gates remain applicable on every request.

Send exactly `application/json` (optional charset parameter) with identity encoding and the existing permitted Origin for browser requests. A package is bounded to 1 MiB while reading regardless of Content-Length; its complete normalized document must also fit 1 MiB, so optional default expansion can cause early admission rejection. Duplicate Content-Encoding headers are rejected on this identity-only route. Header values use HTTP Latin-1 decoding; request keys still require visible ASCII and invalid raw octets return a controlled 422. The shared CLI/API decoder rejects duplicate keys, malformed UTF-8/JSON, nonfinite or oversized numbers, excessive nesting and invalid Unicode; the full validator rejects extra fields, unsupported schemas and invalid graphs/references. Errors carry safe codes and fixed paths/messages without submitted values.

A publication's identity is `(workspace_id, project_id, package_id, version)`. Identical canonical bytes reuse the original version and admission metadata, including when no request key is supplied. Different canonical bytes cannot overwrite it. Array order remains meaningful; JSON key order and formatting do not. Request keys are optional, preserved exactly, 1–128 visible ASCII characters without spaces, and unique within a workspace. The normalized request hash uses SHA-256 over the fixed `lotuspond.package-publication.v1` context, NUL, normalized project UUID, NUL and canonical content. Reuse with another payload or project returns 409. Keys have no expiry in this increment; choose a fresh key for a new request.

Database uniqueness and one outer transaction cover version, key and audit. Concurrent identical publication emits one publication audit event; conflicting key races roll back any candidate version. Successful replays emit no second publication audit. No update/delete endpoint exists. No global cross-workspace content deduplication exists.

Metadata includes server `id`, UTC `created_at`, authenticated `workspace_id`, `project_id`, logical `package_id`, `version`, `schema_version`, `validator_revision`, `canonicalization`, `sha256`, `content_bytes`, `publisher_id` and `publisher_kind` (`user` or `api_key`). Names, instructions, manifest objects and request keys are excluded from metadata/list/audit responses. Audit details contain project/package/version/digest identifiers only.

Current admission identifiers are `lotuspond.workflow-package.validator.v1.1` and `lotuspond.workflow-package.canonical-json.v1`. The canonicalizer uses fully validated values with defaults, sorted object keys, UTF-8, compact JSON separators, finite numbers and preserved array order. These names permanently describe the stored content's admission, not execution compatibility. Future validator revisions must use a new server identifier and preserve existing stored bytes/identifiers.

Pagination orders by descending `(created_at,id)`, so equal timestamps retain stable identity ordering. Cursors bind workspace and project; they confer no authority. New publications do not change the older rows reached by an existing cursor.

## Reviewed actions and human tasks (E05)

Every route is under `/api/v1/projects/{project_id}/environments/{environment_id}`, with E04's execution conventions: strict JSON bodies, keyset pages (`limit`, `after_id`; the response carries `next_after_id`), browser origin and MFA checks, and errors as `{error, message}` with fixed codes. Without a command key provider every route below answers 503 `authority_unavailable`, and only interpreter v1 runs.

**Human tasks.** A task is listed only once its execution's wait is retained; before that its reads answer `not_found` and its mutations `conflict`.

| Route | Who | Notes |
| --- | --- | --- |
| `GET /human-tasks` | `read` | Open tasks: `id`, `execution_id`, `kind` (`review`, `form`), `state`, `deadline_ms`, `escalated`, `escalate_at_ms`, `claim` (`none`, `caller`, `other`), `claim_expires_at_ms`, `intent_sha256` |
| `GET /human-tasks/{id}` | `read` | One task, same shape |
| `GET /human-tasks/{id}/proposal` | a person with `read` | The exact connector, operation, policy, target and input under review. `Cache-Control: no-store`; audited without its values. API keys are refused |
| `POST /human-tasks/{id}/claim`, `/release` | a person, `review` | Empty body; idempotent by state. A claim lasts 15 minutes, never past the task's deadline; an owner or admin may release another person's claim |
| `POST /human-tasks/{id}/decision` | a person, `review` | `{decision_id, intent_sha256, decision: "allow" \| "deny"}`. A digest other than the intent's is 409 `conflict`; with independent approval the requester is refused |
| `POST /human-tasks/{id}/submission` | a person, `review` | `{submission_id, payload}` for a `form` task |

API keys, runtimes, viewers and platform administrators never claim, decide or submit, whatever a key's scopes. A decision and a submission carry their own identity, so neither needs an `Idempotency-Key`; an equal retry is replayed and not audited again.

**Actions.** The projection is metadata only, never a proposal's values.

| Route | Who | Notes |
| --- | --- | --- |
| `GET /executions/{id}/actions` | `read` | `action_id`, `step_id`, `occurrence_path`, `intent_sha256`, `authorization`, `effect`, `outcome` |
| `GET /executions/{id}/actions/{action_id}` | `read` | One action |
| `POST /executions/{id}/actions/{action_id}/resolution` | owner or admin, by session | `{resolution_id, kind, evidence}`; `kind` is `readback`, `compute_stopped`, `effect_present` (citing an artifact already recorded for this intent) or `close_unknown`. Never resends |

`authorization` is one of `proposed`, `awaiting_review`, `approved`, `denied`, `review_expired`, `expired`, `withdrawn`, `superseded`; `effect` one of `not_dispatched`, `dispatching`, `accepted`, `observed`, `unknown`, `absent`; `outcome` one of `unknown`, `verified`, `not_verified`. An execution that fails on an action names the cause in its `reason`: `approval_denied`, `review_expired`, `approval_expired`, `approval_revoked`, `binding_refused`, `not_applied`, `outcome_not_verified`, `outcome_unknown` or `recovery_epoch_changed`.

**Runtime action routes** (`/api/v1/runtime/actions/…`: `poll`, `records`, `claim`, `renew`, `receipts`, `stops`, `{attempt_id}/values`) serve only an enrolled runtime's own session, under E03's command trust. A receipt or stop of a write already claimed is recorded even if its initiator has since lost `execute`; integrity, epoch, recipient, expiry and connection still decide. Operations: `docs/operations/actions-human-tasks.md`.
