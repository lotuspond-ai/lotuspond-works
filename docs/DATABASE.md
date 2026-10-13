# Lotuspond database

The authoritative model is `backend/lotuspond/models.py`, using SQLAlchemy 2 with PostgreSQL, UTC timestamps, generated UUID identifiers, explicit state constraints, and workspace-scoped composite foreign keys. Queries are parameterized. This is a separate database; the transitional TypeScript schema does not apply to it.

| Area        | Tables                                                                                       | Purpose                                                                                                                                                       |
| ----------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity    | users, sessions, identity_challenges, invitations, mail_outbox, offline_identity_attestations, offline_enrollments, mfa_factors, mfa_enrollments, mfa_login_challenges, mfa_recovery_codes | Salted Argon2 password hashes, hashed expiring tokens, separate mailbox and offline identity state, encrypted mail, explicit operator flag |
| Tenancy     | workspaces, memberships, api_keys                                                            | Personal versus organization ownership, role membership, suspension, the organization authenticator requirement, expiring scoped hashed keys                  |
| Evaluation  | projects, environments, scenarios, runners, runs, run_results                                | Project context, frozen scenarios, runner leases, conclusive/unknown outcomes                                                                                 |
| Releases    | policies, releases, release_decisions, receipts                                              | Immutable policy versions, comparison, independent decisions, recorded outcomes                                                                               |
| Governance  | approvals, integrations, audit_events                                                        | Different-person review, unverified endpoints, action history                                                                                                 |
| Monitoring  | traces, incidents, incident_events, alert_deliveries, monitors, evaluation_jobs, assessments | Project traces, deterministic sampling, durable job and alert leases, incident lifecycle, model/input provenance                                              |
| Voice       | voice_sessions, voice_segments                                                               | Session state, sequence/revision, immutable finalized transcripts                                                                                             |
| Model usage | gateway_usage                                                                                | Additive budget reservation and provider settlement ledger                                                                                                    |
| Schema      | schema_version                                                                               | Explicit application schema version                                                                                                                           |

## Safe local initialization

`npm run db:start` creates `lotuspond_platform_dev` on the project's local cluster at `127.0.0.1:55439`. `npm run db:init` is allowed only for the exact development/test names on loopback, refuses production mode, obtains an advisory lock, and refuses nonempty tables. It seeds no account, results, approvals, or imported records.

The earlier `lotuspond_local` database remains separate and is not used by the Python API. Environment configuration from the archive is never loaded. Tests accept only a loopback `lotuspond_platform_test` URL and create unique disposable schemas; no existing schema is reset.

## Reviewed additive upgrade plan

Version 2 introduced the Python model. Version 3 added `gateway_usage` for model cost reservations and settlement. Version 4 widened ledger money precision to NUMERIC(30,6) and token counters to BIGINT, retaining valid provider overruns instead of undercounting known spend. Version 5 added single-use mailbox challenges and organization invitations, the encrypted mail outbox, and the nullable API-key creator reference used by the production verified-identity gate. Version 6 added evidence-linked incident categories, resolution/reopening metadata, append-only incident events, and the signed webhook delivery outbox. Version 7 makes trace cost nullable, marks every historical amount `unknown`, and introduces separate offline attestation and enrollment tables. Version 8 adds encrypted authenticator factors, provisional enrollment, expiring login challenges, one-use recovery codes and nullable MFA bindings on sessions. Version 9 adds the organization authenticator requirement as a `workspaces.require_mfa` column that every migrated workspace starts with disabled, constrained so only an organization can hold it. Version 10 widens `mail_outbox` to accept the `security_notice` kind and names both affected constraints, so a notice carries no reference while every other kind still carries exactly one. Version 11 narrows `workspaces.plan` to the static subscription ladder, so a stored tier can never be one the server cannot enforce; a workspace already outside the ladder rolls the whole migration back rather than being rewritten. New databases start at version 11 (38 tables). Upgrades preserve every previous column value, add manual category/source defaults to historical incidents, and create empty new tables. They do not rename or drop existing columns/tables, import old data, or alter ownership.

The local `npm run db:migrate` command checks the exact loopback database allowlist and version under the initialization advisory lock. It accepts only the reviewed version-2 through version-10 shapes, applies the required forward steps through version 11 in one transaction, and refuses unexpected pre-existing identity/incident/offline/MFA tables, session-binding columns, cost-provenance columns, an existing workspace policy column, or a mail_outbox in neither the version-9 nor the version-10 constraint shape. Failure rolls back both data definition and version changes. Repeating the command refuses an already current schema.

Validation must prove existing account/project IDs, previous incident values, and row counts are unchanged, a ledger created from version 2 begins empty, existing version-3/4 ledger rows remain intact, the API readiness version matches, and budget reservation tests pass. To recover from an application defect, stop the new gateway feature and preserve its evidence; do not reverse the change by dropping user data. Existing installations must follow a reviewed forward migration rather than rerun initialization.

Mail bodies are AES-256-GCM ciphertext bound to their outbox identifier. Tokens are stored only as SHA-256 digests outside that ciphertext, and sent, terminally failed, expired, revoked, or consumed work is cleared or cancelled by the worker. SMTP delivery is disabled by default. `LOTUSPOND_SMTP_TIMEOUT_SECONDS` bounds one socket operation and `LOTUSPOND_SMTP_CONVERSATION_SECONDS` bounds the whole SMTP conversation; a conversation that passes its bound is recorded as the retryable `smtp_deadline` error rather than holding the worker. Still supervise and restart the worker, and expect a duplicate submission after an ambiguous SMTP acceptance or an expired worker lease. Challenge and invitation single-use checks make duplicate links harmless. A `security_notice` row carries no challenge or invitation reference and no link, so nothing can make it stale; the worker always attempts it and a duplicate is only a repeated warning.

These commands are intentionally not production database administration tools. Hosted/on-prem bootstrap and upgrades need their own authorized deployment plan and backup/restore evidence.

## Evidence and authorization

Workspace predicates and composite foreign keys complement one another. API keys cannot grant membership or decide human approvals. A release approval must use the `release` action and be decided by a different administrator.

Evidence exports identify `sorted-json-utf8-v1`: Python `lotuspond.security.canonical` recursively sorts object keys, preserves array order, emits compact UTF-8 JSON, and refuses non-finite numbers. Hash the exact canonical encoding with SHA-256. The format is explicitly versioned; do not assume cross-language number encodings are interchangeable. Unknown outcomes stay null and block release.

## Reviewable schema snapshot

`backend/schema.sql` is generated from the model by `backend/export_schema.py` without connecting to a database. CI checks that it matches the model. It contains only empty-schema creation in a transaction and refuses existing tables; it is neither an upgrade nor a reset script. It is available for an independently authorized deployment operator to review, rather than bypassing the local tools' production restrictions.

## Incident delivery data

An assessment creates at most one incident through a workspace-scoped unique constraint. Each state change retains an incident event; enabled operator destinations receive one outbox row per event and name. The payload is immutable metadata and excludes user text. Delivery uses expiring hash-only claims, five attempts, and guarded terminal updates. Repeated lifecycle actions and stale evaluation/alert leases do not duplicate events or overwrite current claims. See the [incident runbook](../docs-site/docs/incidents.md) for signing and receiver deduplication.

## Identity and cost provenance

An offline enrollment belongs to one user and that same user's attestation through a composite foreign key. Token digests are unique; consumed and revoked states are mutually exclusive. Provisioning creates new accounts only, and enrollment is required before authentication. Mailbox state is unchanged. See the [design decision](architecture/0003-offline-identity-and-cost-provenance.md) and [operator guide](../docs-site/docs/offline-identity.md).

Trace costs store a nullable amount and a required `reported`, `unavailable`, or `unknown` marker. Reported cost requires an amount, unavailable cost requires null, and legacy unknown retains its original value. The API derives the marker. Monthly totals include reported amounts only and expose counts of incomplete measurements. A historical zero is never assumed to be measured zero.

## Authenticator persistence

Factor/version composite foreign keys bind MFA challenges, recovery codes and verified sessions to the same user. Existing sessions receive null MFA fields during migration; no authentication strength is inferred. TOTP replay counters and recovery consumption are transactional. Secrets are encrypted with a separate operator key; only challenge and recovery-code digests are stored. See [the authentication design](architecture/0004-authenticator-mfa.md). A database restore also needs the separately protected encryption key; the synthetic restore drill verifies structure and rows, not secret custody.
