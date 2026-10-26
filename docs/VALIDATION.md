# Local validation — 2026-09-21

Latest increment: [repository and platform foundation, 2026-10-13](plans/2026-10-13-foundation-progress.md). The dated records below preserve earlier evidence; their repository/publication statements describe those earlier runs.

This record applies to the current Python-first Lotuspond implementation. It does not establish hosted production readiness or complete the full delivery goal.

| Check                         | Observed evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python API and database       | Full isolated PostgreSQL suite: 162 tests passed with 9 subtests; normal SQLite run: 142 passed and 20 PostgreSQL-only skips; one third-party Starlette deprecation warning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Workspace and identity        | Individual/organization behavior, cross-workspace denial, viewer limits, key scope/expiry/revocation, CSRF, operator restriction and suspension tested; verification/reset/invitation replay and expiry, concurrent single-use consumption, stale mail leases, encrypted outbox binding and production verified-identity gates tested                                                                                                                                                                                                                                                                                                                                                                 |
| Workflow correctness          | Exact frozen result coverage, immutable completed runs, lease replay/cancellation, concurrent capability-aware PostgreSQL claims, 4 MiB frozen-snapshot bound with incremental serialization, independent approval, unknown-result blocking and evidence hashing tested                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Monetary/policy correctness   | Excess policy precision rejected; transactional gateway reservations and retained uncertain/overrun spend; unreported cost stays unknown and blocks configured cost gates; Decimal summation/comparison; mock provider traffic only                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Database evolution            | Additive versions 2/3/4/5/6/7→8 chain adds identity lifecycle, incident/alert evidence, separate offline identity tables, nullable trace cost provenance and MFA factors/challenges/recovery/session bindings while preserving previous column values; malformed prior states and repeated initialization/migration refuse                                                                                                                                                                                                                                                                                                                                                                            |
| Local development schema      | Separate `lotuspond_platform_dev`, schema version 8; private recovery snapshot saved, all 33 previous non-version table contents fingerprint-identical after v7→v8 migration, four new tables initially empty and prior sessions not elevated; no old database imported or reset                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Real HTTP console contract    | Ten flows through Vite port 3001 and Python port 8000: account/org/project/scenario, blocked release, independent approval restriction, admin denial, voice closure, queued-to-unknown assessment and trace detail                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Browser                       | Isolated Playwright verified signup, personal/org preservation, projects/scenarios/runs, incomplete-evidence release block, voice closure and queued-to-unknown assessment, monitor create/disable, changelog, password rotation and session revocation; desktop and 390px mobile screenshots inspected; actual loopback SMTP verification, invite acceptance/revocation and password-reset flows passed, including old-session/password denial and URL-token removal; public auth keyboard, labels and text contrast checked; production verification-first UI transition exercised with a mocked 403 followed by actual local signup                                                                |
| Jev/provider boundaries       | Mocked valid/invalid/timeout responses; total deadline, response decompression bound, no redirects/proxy inheritance, and no retry amplification; no live provider call                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Voice bridge                  | Thirteen mocked tests pass: bounded queues/timeouts, explicit channel roles, finality, airgap denial, ambient-proxy bypass, and retained TLS verification; no real audio sent                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Go collector                  | Go tests and vet passed; shutdown/deadline concurrency subset repeated 20 times; proxy bypass, oversized-response rejection and bounded queue wait tested; rebuilt collector performed authenticated ingestion in the isolated bundle drill                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Rust evidence                 | Three manifest tests passed for ordering, tampering/extra files, and symlink rejection                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Gym sandbox                   | Twenty-two Python tests plus fourteen subtests and five Node adapter tests passed. Baseline synthetic Moodle runner returned false/0. Separate real xterm `hello@1` run `real-78ef91fa99b5af4f` passed upstream verifier true/100, exact terminal text/window/screenshot checks, effective isolation inspection and cleanup; image `sha256:2c37be8d20e6a6b205dbe071624b5154d1307034dc771e857fa10b7208b4eb7c`; bounded agent loop and real API run `1aca3b87-c45f-4c73-936b-0231f26d6941` persisted a pass with unavailable cost and exact receipt hash `19d0a78caea003c573910ef37019eff0b5f0d83fe456c45c07f79e823f3e507a`; deterministic adapter only, no model or wider-application coverage claimed |
| Container packaging           | Current API/UI image `sha256:02ab999b1bfa370bb80b2e2077e68e0d6af102a5311fb74a54c7673650bbe7d5`; collector `sha256:2d812e003b0d9aae18166bdc9f6515524fbcf4e834dc0fb66905a2d5a6a59d07`; customer execution image `sha256:e371d2dc14b4a71ecdfd3e296d5963e08cbe816c1385298be7605200c31eb1da` (Node 24.21.0, uid 10001); Gym real-application image `sha256:2c37be8d20e6a6b205dbe071624b5154d1307034dc771e857fa10b7208b4eb7c`; service images packaged in the locally exercised service bundle; execution and Gym images packaged by immutable ID in the separate execution archive                                                                                                                         |
| Backup and restore            | Network-disabled disposable PostgreSQL 18.6 containers; full schema, all 38 tables and synthetic rows, complete sequence metadata/state compared after restore; 129,613-byte dump, confirmed cleanup; no existing database read                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Offline archive verification  | Twenty-three tests and thirteen subtests passed for both service (`--kind airgap`) and execution (`--kind execution`) archives: streaming verification without extraction, complete manifest coverage, digest/tamper checks, unsafe/duplicate/link/extended-metadata rejection, and compressed/decompressed size bounds; the service bundle installation drill remains the five-container internal-network record below; clean-host transfer and TLS not exercised                                                                                                                                                                                                                                    |
| MCP adapter                   | Seventeen local mock Streamable HTTP tests passed: explicit tool and destination, bearer authentication, separate discovery, cancellation/timeouts, bounded responses, truncation-to-unknown and exact supported expectations; no customer endpoint call                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| HTTP agent adapter            | Seventeen local endpoint tests passed for request mapping, response extraction/assertions, authentication, proxy bypass, redirects, DNS/body deadlines, cancellation, byte limits and airgap restriction; actual Node runner/API/PostgreSQL workflow passed with a synthetic loopback HTTP agent; no customer/provider endpoint call                                                                                                                                                                                                                                                                                                                                                                  |
| Supervised runner integration | Actual local API, new isolated PostgreSQL schema and Node CLI passed all seven retained smoke checks: success, credentials not inherited, CPU timeout unknown, crash unknown, HTTP adapter, cancellation without results and revoked-key abort without results; owned processes/schema cleanup confirmed                                                                                                                                                                                                                                                                                                                                                                                              |
| Documentation                 | MkDocs strict build passed; Git-backed publication workflow authored; no hosting or DNS change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Frontend and runner           | TypeScript lint, 78 web/client/runner/sandbox-adapter tests and production build passed; supervised timeouts, cooperative cancellation, bounded cleanup, origin-or-`/api/v1` API URLs, and direct API transport tested on Node22.22.0; image/CI use Node24.21.0                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Dependency audit              | Updated root npm, voice and MCP packages: zero reported advisories; frozen Python production resolution: zero known vulnerabilities, including newly pinned cryptography 50.0.1; audits are point-in-time, not a security guarantee                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Product cleanup               | No previous product/affiliation matches in active source/config/docs; external recovery archive verified; no inherited Git history or remote                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

The Go collector's in-memory queue is not durable. The API's integration records do not execute endpoints; the separate HTTP and MCP runners are explicitly configured and locally tested against synthetic endpoints. Speech transcription and semantic provider behavior require separately configured credentials and representative real data. Docker build/smoke and synthetic restore evidence are local; production-volume recovery, hosted deployment, internal TLS, human offline identity proofing, wider application sandboxes and customer workloads remain unproven. Trace costs now retain null for unavailable measurements and explicit reported zero; historical values are preserved as unknown provenance and excluded from reported totals. Current-month aggregation has both UTC month bounds. Browser artifacts are retained locally under ignored `output/playwright/`; the real terminal receipt and screen are under `output/gym-real-app/`. Test SMTP/mail-worker processes were stopped after validation, and the local preview was returned to mail-disabled configuration. External email delivery was not tested.

The original workspace remains at `/Users/srujanpapaiahgari/Downloads/Lotuspond-archive-20260919/workspace`. The superseded local TypeScript API/schema/tests were preserved in `transitional-node-20260921.tar.gz` alongside it; all 11 source entries were SHA-256 verified before removal from the active project. Environment files and database contents were not included in that additional archive.

No remote repository, hosted database, cloud resource, DNS record, payment, or customer workload was changed. Outstanding production work is tracked in `CAPABILITIES.md` and the full `GOAL.md`; the goal remains active.

Incident validation covers failed versus unavailable assessments, scoped linkage, concurrent completion, reasoned resolve/reopen, immutable event payloads, HMAC signing, finite retry/expiry, stale claims, disabled/suspended delivery, and bounded mock HTTP. No external receiver was contacted. The optional alert worker and API/evaluation-worker configuration are wired into both Compose variants.

The incident console was browser-checked with a fresh local account: manual report/resolve/reopen, retained history, actual trace and voice unavailable assessments, unchanged unknown verdict after resolution, source inspection, category/project/status filters, empty and request-error recovery states, close-review focus, and desktop/mobile overflow. An isolated one-request 503 mock exercised the error state and was removed. Screenshots and the detailed report remain under ignored `output/playwright/`.

Previous schema6 offline bundle: `output/airgap/lotuspond-local-v6-final.tar.gz`, 216,609,079 bytes, SHA-256 `b9df3fe063b248dd1174d3bfd80ab41888d72c0d96155d9c5767f582f12404a2`. Its retained `local-v6-final-install-report.json` records schema6, distinct failed/unavailable incidents, authenticated collector ingestion, the production verification gate, internal-only network, five containers, two removed anonymous volumes, and confirmed cleanup in 8.1 seconds. This used the existing local Docker daemon and does not prove clean-host transfer or HTTPS termination. The final active-source audit covered 211 files with no prior product/affiliation names and no private or generated paths selected for Git.

Schema7 preservation: the private pre-upgrade dump is `.local/offline-qa/dev-before-v7.dump`, SHA-256 `f2c414e2f6763eb2c85b19112a6748deb2c04166b90ccb9167473a7b8e0e2005`. The verification helper asserted the exact localhost server, port, development database and project data directory before comparing every prior column value in all 31 non-version tables. All matched; both new identity tables were initially empty, and every retained trace had unknown cost provenance.

Offline identity testing covers hashed one-use links, expiry/reissue, explicit organization/project/admin provisioning, pending-account denial, matching composite references, active-attestation production gates, normal login, and mailbox-only invitation acceptance. Competing completion/reissue requests are serialized in PostgreSQL. Independent review reproduced a pending-credential promotion defect; completion now deletes all prior sessions and revokes attributed keys, with a regression confirming they remain unauthorized afterward. Revocation disables the account and credentials while retaining business data.

The synthetic offline installation drill exercises the real packaged CLI and HTTP endpoints in production configuration with authenticated PostgreSQL/Redis, disabled mail/alerts, and no published ports. It proves enrollment does not verify the mailbox, replay is rejected, normal sign-in is required, explicit operator authority works, and revoked sessions/keys fail while project/trace records remain. This is application behavior on synthetic data, not a human identity-proofing or MFA certification.

Offline-origin recovery review also confirmed that an unverified claimed email must not acquire attested authority if an installation later enables mail. Both reset request and consumption now require completed enrollment and actual mailbox verification for these accounts. Regression tests cover pending and completed accounts, verified eligibility, and a stale reset challenge after verification is removed; ordinary signup recovery remains unchanged. No external message was sent.

Browser enrollment proof: both real CLI-created personal and organization accounts completed one-use enrollment, required a separate normal login, retained false mailbox verification with operator-attested identity, and showed the intended workspace/project/admin permissions. Desktop and 390 px mobile screens were checked, including corrected isolated-installation guidance and account-role spacing. Real null/zero/six-decimal traces rendered distinctly; legacy unknown rendering used focused tests. The two synthetic identities were subsequently revoked, their sessions/keys/attestations verified inactive, and three workspaces plus two projects retained. Raw QA passwords, enrollment links and the credential helper were removed; a scan found no credential copies in Playwright logs/reports. The retained reports are `output/playwright/offline-enrollment-qa-report.md` and `offline-identity-cleanup.json`. Final invitation guidance received a focused rendering regression; release assets are `index-BBkrSlgC.js` and `index-D6g97srk.css`.

Previous schema7 release bundle: `output/airgap/lotuspond-local-v7-release.tar.gz`, 216,618,327 bytes, SHA-256 `ebb160cd69026600ea8f929487a590132e1a06d721e49db2aaee501ceeee26ab`. The retained `local-v7-release-install-report.json` records all schema7 offline enrollment, normal-login, replay, organization/operator, revocation/data-retention, trace-cost, monitoring and collector checks passing. Five containers, two anonymous volumes and the internal network were removed and their absence confirmed in 10.2 seconds. Loaded shared images remain cached. This is a local Docker installation drill; no clean-host transfer, internal TLS or production data is claimed. The final active-source scan covered 222 files with zero legacy brand matches and no private/generated paths selected for Git.

Schema8 preservation: `.local/mfa-qa/dev-before-v8.dump` is the private pre-upgrade backup, SHA-256 `b479ae3c8d9f7186fa8d26e9bb32466756606b268889ef7ae76461b33dd5bf52`. The helper verified the exact localhost database, port and project data directory, then confirmed every prior column value in all 33 non-version tables was unchanged. The four MFA tables were initially empty and all existing sessions retained null MFA bindings. No production database was accessed.

MFA validation covers RFC6238 SHA-1 vectors, expiring password-only login challenges, no premature session, encrypted user/factor binding, one-use recovery codes, TOTP replay denial, durable failed-attempt limits, per-account management quotas, missing-key denial, enrollment session rotation, password-change proof preservation, reset cleanup and offline identity revocation. Review identified enrollment confirmation from an older session; starting enrollment now reauthenticates and rotates every session, with a regression. Locked authentication records are explicitly refreshed after waiting for row locks. Competing PostgreSQL challenge completions have one winner. Offline-revocation fixtures contain actual synthetic factor, enrollment, challenge and recovery records before proving their removal.

Previous MFA bundle: `output/airgap/lotuspond-local-v8-mfa.tar.gz`, 216,632,370 bytes, SHA-256 `7fd8a78cef93d4dfa82250892f99e87c8a80e738b097eec6285e74ac53fe8150`. Its `local-v8-mfa-install-report.json` records schema8, the production operator gate before setup, real authenticator enrollment/sign-in, stale-session denial, TOTP/challenge replay denial, single-use recovery codes and refusal to disable production-operator MFA. Existing offline identity, monitoring, collector and revocation checks still passed. All five containers, two anonymous volumes and the internal network were removed and checked absent in 22.9 seconds. All 33 packaged Python modules matched the final source hashes. The bundle includes an MFA runbook, never a configured key.

The v8 isolated restore report is `output/airgap/local-v8-restore-report.json`: 38 tables, a 129,613-byte custom backup, synthetic MFA/session fixtures, exact schema/row/sequence comparison, network disabled, cleanup confirmed in 9.65 seconds. This proves data restoration, not encryption-key custody; protected key backup and restore remains an operator responsibility. TOTP is phishable and no passkey, SSO, human identity-proofing or compliance certification is claimed.

MFA browser evidence: actual local enrollment and normal login, password-authorized setup, invalid-code focus/clearing, confirmation and one-time code acknowledgement, password rotation preserving MFA, challenge cancellation, real TOTP sign-in, recovery sign-in and consumption, rejected reuse, code regeneration and development-only disable all passed. The desktop and 390px mobile screens were visually inspected without visible secret material. No browser storage or URL retained credentials. The sanitized report is `output/playwright/mfa-qa-report.md`; screenshots are `mfa-enabled-desktop.png`, `mfa-challenge-mobile.png` and `mfa-account-mobile.png`. Final assets: `index-DONdGAkg.js` and `index-AqGH9Zta.css`. Production operator enforcement and disable denial were exercised in the packaged HTTP drill, not represented as development-browser policy.

The synthetic browser account was signed out and then revoked through the real offline CLI. Independent checks found zero sessions, factors, pending MFA enrollments, login challenges, recovery codes, active attributed keys and active attestations. Two workspaces and one project were retained, with a revocation audit event. The private QA password/key/recovery helper, enrollment link and bytecode were deleted; the stable local installation key and private migration recovery snapshot remain outside Git. `output/playwright/mfa-cleanup.json` records this proof. A credential scan of retained browser text artifacts found no matches.

Final active-source audit: 230 intentional source/configuration/documentation paths, zero legacy product/affiliation word or domain matches, and no private/generated/archive paths selected. This remains an unpublished local repository with no inherited history or remote. The broader delivery goal remains active; hosted operation, live provider/customer evidence, federation/passkeys, assisted recovery and other declared gaps are not claimed complete.

## Operational visibility, 2026-09-21

The operator-only Operations endpoint exposes fixed aggregate queue states, eligibility/delay/suspension, expired leases, oldest eligible age and separate failed/unknown assessment totals. Fixed Redis role markers record poll boundaries with 150-second freshness and 300-second expiry; missing/corrupt/future markers and Redis outages stay explicit. Existing production identity/MFA gates apply. Six backend contract tests cover authorization, exact timestamp boundaries, suspended/delayed/reclaimable work, observation failures and worker poll integration; the full isolated PostgreSQL suite passed 158 tests. No schema change was needed.

The collector now exposes optional metrics protected by a distinct bearer token, fixed result labels and duration histograms. Go race tests repeated three times and vet passed, including blocked-upstream queue saturation, cancellation during upload, deadlines, transport errors, malformed upstream output and credential separation. The upload cancellation classification issue found during independent review was fixed and regression-tested. Prometheus 3.14.0 validated the scrape configuration syntax, all three alert rules and quiet/pending/firing timing fixtures both natively and in a network-disabled tool container. The CI workflow repeats rule checks and opts into the failure/recovery installation drill; no remote CI execution is claimed.

Real desktop 1440/mobile 390 browser QA showed database-backed counts, an unconfigured Redis observation store, disabled mail/alert queues with retained state, and failed 0 versus unknown 4 assessments. An explicitly injected browser network failure cleared old figures, focused a useful error and recovered after the override was removed. The exact new synthetic operator was then revoked: zero sessions, factors, enrollment/challenge/recovery records, active keys or active attestations; two workspaces and one project retained. Credentials were deleted and the browser was signed out. Reports/screenshots are under `output/playwright/operations-*`.

The previous operations bundle is `output/airgap/lotuspond-local-v8-operations-final.tar.gz`, 216,651,866 bytes, SHA-256 `ad1c15d573572b6563f18a209ef3ef1bcfb6126428bc9c82c77cb32710618aab`. Its `local-v8-operations-final-install-report.json` proves a real stopped evaluation worker with eligible backlog, a stale observation after the unchanged 150-second threshold, restart with a fresh poll and recorded assessment, drained backlog, Redis outage with readiness failure and visible database queue state, then Redis recovery. The protected collector scrape passed Prometheus metric parsing/linting; missing and ingestion credentials were rejected. All five disposable containers, two anonymous volumes and the internal network were removed and checked absent in 202.5 seconds. No ports were published and only synthetic data was used. All 35 packaged Python modules matched the operations-phase source; its UI assets are `index-BzfY2tna.js` and `index-DFza_0Mt.css`.

Final `npm run check` passed (49 JavaScript tests;138 SQLite tests with 20 PostgreSQL-only skips; lint/build), the full isolated PostgreSQL run passed 158 tests with 9 subtests, and deployment checks passed 17 tests with 8 subtests. The active-source audit covered 244 files with no prior brand/affiliation matches, symlinks, or private/generated paths selected for Git. This is local operator-observation and failure-recovery evidence, not production capacity, external alert delivery, hosted deployment, or clean-host transfer proof.

## Supervised evaluation runners, 2026-09-21

Every scenario now executes in a fresh bounded subprocess while the parent renews its lease. Control-plane keys stay in the parent, adapter input/output is bounded, and complete result batches must fit the API limit. CPU loops, crashes, malformed results and incomplete evidence become unknown. Lost authority stops execution and suppresses submission. Cooperative cleanup receives five seconds before forced process-group termination; unconfirmed teardown stops the runner and releases its local handles rather than hanging or claiming more work. Process groups are not a security sandbox and cannot prove remote or escaped-resource cleanup.

The recovered HTTP agent mapping supports plain-input and OpenAI-style chat requests with explicit response paths and exact supported text expectations. The adapter and API client use direct transports, ignore ambient proxies, retain TLS verification and deadlines through response consumption, and bound response bodies. A real local proxy test under Node's `--use-env-proxy` observed zero proxy connections while the intended endpoint received the request. A separate local TLS probe rejected an untrusted certificate even with ambient TLS verification disabled, before any authorized request reached it. No live customer or semantic provider call is claimed.

`output/runner-smoke-schema8-final.json` records all seven cases passing through the actual Node CLI, Uvicorn API and a newly owned PostgreSQL schema: persisted success, no inherited control key, CPU timeout as unknown, crash as unknown, HTTP adapter execution, cancellation without results, and revoked-key abort without results. Both authority-loss cases required nonzero runner exits. The harness verifies the exact localhost test database and data directory, rejects URL overrides, and removes only its own marked schema and processes. Zero temporary smoke schemas remained. The same check is wired into CI with an explicit test-server data directory; that hosted workflow has not run.

The final supervised Gym check used the pinned genuine xterm task and a deterministic loopback fixture. Run `agent-26410ba3e88c477d` passed with upstream/application evidence and receipt SHA-256 `a8c46a49d2701fa3eedb2463c82fdc5e12051b26c1346d2da7bc885e5cf64f7e`. Run `agent-3be7370ee8d8ece6` was cancelled after the agent received an observation. Both owned containers were independently confirmed absent and both supervisor process groups were gone. The report and evidence are under `output/gym-supervised/`; this proves local lifecycle compatibility, not model ability or broader software coverage.

Final source validation passed: `npm run check` (73 JavaScript tests, 142 SQLite tests, 20 PostgreSQL-only skips, 9 subtests, lint/build), the isolated PostgreSQL suite (162 tests and 9 subtests), 34 protocol tests, generated schema consistency, Ruff for the retained smoke harness, and the strict documentation build. The active-source scan covered 253 paths with no obsolete brand/affiliation matches, symlinks, or private/generated paths selected for Git. No production database, cloud resource, hosted domain or remote repository was changed. The delivery goal remains active.

The current API/UI/collector offline bundle is `output/airgap/lotuspond-local-v8-runners.tar.gz`, 216,652,173 bytes, SHA-256 `3075536c1f7d22c5c2a32ed623fe0af64378934751d47e4372fd8ebe9ae5cf69`. Its 13-file manifest verified, and `lotuspond-local-v8-runners-smoke.json` records the normal schema8 installation, identity/MFA/revocation, unknown-versus-failed assessment, cost provenance and collector checks passing in 40.1 seconds. All five containers, two anonymous volumes and the internal network were removed and checked absent. Backend and UI content hashes match the source/build used for this image; no Git commit provenance is claimed because the local repository has no HEAD. The longer worker/Redis recovery drill remains separate evidence from the previous operations bundle.

This archive packages the API, UI, evaluation worker, collector, PostgreSQL and Redis. It does **not** include the customer-side Node runner, protocol adapters, voice bridge or Gym runtime. Those tools are in the separate execution archive recorded below. Clean-host transfer of either archive, live providers, customer workloads and hosted publication remain open; neither bundle name implies those are complete.

## Customer-side execution distribution, 2026-09-21

HTTP, MCP, voice and release-gate commands now ship in a nonroot Node 24.21.0 image (`lotuspond-execution:local`, immutable ID `sha256:e371d2dc14b4a71ecdfd3e296d5963e08cbe816c1385298be7605200c31eb1da`). The image entrypoint is `scripts/execution-cli.mjs` with commands `runner-http`, `runner-mcp`, `voice` and `gate`. Inspected uid/gid are 10001, Python and Docker are absent, and `/var/run/docker.sock` is not present. Gym is not an image entrypoint.

`LOTUSPOND_API_URL` accepts an origin or a base that already ends in `/api/v1`. Voice ingest and the API client share `scripts/api-url.mjs` and `scripts/direct-transport.mjs`: ambient proxies are ignored, and TLS verification stays enabled even when `NODE_TLS_REJECT_UNAUTHORIZED=0`.

The local execution archive is `output/execution/lotuspond-local-v8-execution.tar.gz`, 221,409,153 bytes, SHA-256 `2912f7889ef6271c4cb29dd1ab73777f1caf3c2d31c400de22f1e96ab3a45e64`. `deploy/execution/verify-bundle.sh` verified it without extraction: root `lotuspond-execution-local-v8-execution`, 21 files. The archive carries `images.tar`, `images.json` (`lotuspond.offline-execution-images.v1`), `runtime.env.example`, `INSTALL.md`, and `gym-kit/` (leased-run adapter, standard-library controller, pinned xterm task/verifier/license/provenance). The service archive was not rebuilt.

`output/execution/lotuspond-local-v8-execution-image-smoke.json` records the image-only drill: synthetic HTTP and MCP probes passed inside `--network none` with stdin-attached Node, voice refused with `code=network_disabled` and no provider credentials, cleanup confirmed, 1.9 seconds. `output/execution/lotuspond-local-v8-execution-smoke.json` records the extracted-archive drill: the same HTTP/MCP/voice checks, plus genuine xterm `hello@1` run `real-d49231c42f02c26d` from the extracted kit against Gym image `sha256:2c37be8d20e6a6b205dbe071624b5154d1307034dc771e857fa10b7208b4eb7c`; kit and drill cleanup confirmed; 6.1 seconds. The drill copies the archive into a private directory, does not execute `INSTALL.md`, publishes no ports, and retains no token/secret/password/credential names in the reports.

CI builds `lotuspond-execution:ci` and runs the image-only probes. It does not rebuild the Gym Ubuntu image or package the full archive. Deployment checks: 23 passed with 13 subtests. Protocol adapters: 34 passed. Voice-bridge: 13 passed. `npm run check` passed 78 JavaScript tests, 142 SQLite tests, 20 PostgreSQL-only skips, lint and production build. Strict MkDocs build passed.

This is local packaging evidence on the existing Docker daemon. It does not prove transfer to a clean machine, live speech or customer HTTP/MCP endpoints, representative Gym applications or models, or production capacity. The delivery goal remains active.

## Organization authenticator policy and bounded mail conversations, 2026-09-21

An organization can now require an authenticator for its own access. `workspaces.require_mfa` is schema **9**: an additive column that every migrated workspace starts with disabled, with a database constraint that only an organization can hold it. `POST /api/v1/settings/authenticator-policy` is restricted to an owner or administrator who already holds an enabled factor, so the requirement cannot lock out its author, and each change is audited with the number of members still without one. While it is on, `authenticate` refuses both member sessions and keys whose creator holds no factor with 403 `mfa_required`; an installation without the authenticator encryption key returns 503 instead of degrading. Personal workspaces, `/me`, account security and enrollment stay reachable throughout, and withdrawing the requirement restores the previous access without deleting any factor, key or membership.

`_smtp_send` now bounds the whole SMTP conversation through `LOTUSPOND_SMTP_CONVERSATION_SECONDS` (default 45, never below the single-operation timeout) instead of only each socket operation. A conversation that passes its bound raises `MailDeadlineExceeded`, releases the worker, and records the retryable `smtp_deadline` error with the ciphertext preserved for the next bounded attempt. This closes the limitation previously recorded in `docs/DATABASE.md`.

`npm run check` passed: 83 JavaScript tests, 153 SQLite tests, 23 PostgreSQL-only skips, 9 subtests, lint and production build. The isolated PostgreSQL suite passed **176 tests and 9 subtests**, including the nine new organization-policy contract tests and two version-9 migration tests: one proves every workspace row, kind and name survives with the requirement disabled and the column shaped exactly as a fresh installation, and one proves a partially applied schema is refused with the version left at 8. A PostgreSQL contention test proves two concurrent enable requests both succeed while recording exactly one policy change, because the endpoint locks the acting user and the workspace before its check. Strict MkDocs and the generated schema check passed.

The local development database was migrated forward with `npm run db:migrate` after those tests. It moved from version 8 to 9 with 11 users, 17 workspaces, 18 memberships, 8 projects, 107 audit events and 38 tables unchanged, no workspace requiring an authenticator, and `require_mfa` created NOT NULL with no residual default. The separate `lotuspond_local` database was not touched.

Browser QA ran against a disposable schema in `lotuspond_platform_test`, never the development database, at 1440x900 on the built console. A synthetic organization owner saw the policy card state **Optional** with no control until enrolling an authenticator; after enrollment it offered the change and named the exact exposure, "1 member has no authenticator and will lose access to this organization until they enroll. Keys created by those members stop working too." Enabling it moved the card to **Required**. The synthetic member without a factor then saw the new console notice, an unavailable-projects message and a page-level "This organization requires an authenticator for access" with a retry, while `/me` kept working and reported `require_mfa` per workspace. The `/api/v1/overview` request for that member returned 403 `mfa_required` over HTTP. The disposable schema and its 38 tables were dropped, the temporary environment file was deleted, and no synthetic account, schema or credential remains. Recovery codes displayed during that drill belonged to the destroyed schema only.

This is local implementation and browser evidence. It is not external mail delivery, hosted operation, production-volume validation, or an installation-wide authenticator policy, which remains unimplemented along with out-of-band factor-change notifications.

## Account change notices and installation-wide authenticators, 2026-09-21

Changing authentication material now tells the account mailbox. A password change, a completed password reset, an authenticator enrolment, an authenticator removal and a recovery-code reissue each queue one `security_notice` message naming the change and the time it was recorded. The body deliberately carries no link, token or code, so it cannot be repurposed as a phishing lure and needs no expiry or single-use check, and a disabled or unconfigured mailbox queues nothing while the security change still completes.

Schema **10** widens `mail_outbox` and names both affected constraints. `mail_outbox_kind_check` accepts the new kind; `mail_outbox_reference_check` requires a notice to reference nothing while every other kind still references exactly one expiring record. Both changes only widen what the table accepts, so no existing row can stop satisfying them. The migration step also tolerates a `mail_outbox` that an earlier step in the same chain created from the authoritative model, which already carries the version-10 shape — the same situation the version-4 ledger step already handles — and refuses a table in neither shape.

`LOTUSPOND_REQUIRE_MFA` additionally lets an operator require an authenticator for every workspace on the installation, personal ones included. It refuses to start without the encryption key rather than silently ignoring the requirement, takes precedence over the organization policy in the refusal message, and covers inherited key authority the same way. The console policy card states plainly that changing the organization policy cannot change who reaches the workspace while the installation requirement is on.

`npm run check` passed: 84 JavaScript tests, 164 SQLite tests, 26 PostgreSQL-only skips, 9 subtests, lint and production build. The isolated PostgreSQL suite passed **190 tests and 9 subtests**, including seven new notice tests, four installation-requirement tests and three new version-10 migration tests. The generated schema check and strict MkDocs passed. Notice tests assert the exact change lines for all five events, that no body contains `http`, `token=`, a recovery-code prefix or a six-digit sequence, that a disabled mailbox queues nothing while the change still succeeds, and that the database refuses both a referenced notice and an unreferenced ordinary kind.

A real delivery drill ran the actual `mail-worker --once` process against a loopback SMTP sink on 127.0.0.1:2525, using a disposable schema in `lotuspond_platform_test`. The sink captured a genuine message with the subject `Your Lotuspond sign-in settings changed`, the body line `The authenticator on your account was removed.`, the recorded timestamp and the no-links-no-codes warning; a scan for `http`, `token`, recovery prefixes and six-digit sequences matched nothing. The outbox row finished `sent` with its ciphertext cleared to zero length, `sent_at` set and both references null. The disposable schema, the temporary environment file and the captured message were deleted; no schema, account or credential remains.

The development database was migrated forward to version 10 with 11 users, 17 workspaces, 5 existing mail rows, 107 audit events and 38 tables unchanged, and its migrated constraint definitions match the generated model exactly.

A second browser drill, again on a disposable `lotuspond_platform_test` schema, started the installation with `LOTUSPOND_REQUIRE_MFA=true` and registered a new individual account. Its brand-new **personal** workspace was refused, which the organization policy deliberately never does: the console showed the shell notice "This installation requires an authenticator. Set one up to reach any workspace", an unavailable-projects message in the sidebar and a page-level "This installation requires an authenticator for workspace access" with a retry. Account security stayed fully reachable with the Set up authenticator control available, so a blocked person can always self-recover without an operator. The schema and the temporary environment file were removed afterwards and the development database was untouched throughout.

This is local implementation, loopback delivery and browser evidence. SMTP acceptance by a test sink is not inbox delivery, deliverability, or operation against a real mail provider.

## Compressed OTLP ingestion, 2026-09-21

OTLP/HTTP exporters gzip by default, and neither the Go collector nor `POST /api/v1/telemetry/otlp` accepted a compressed body, so a standard exporter could not reach either without reconfiguration. Both now accept `Content-Encoding: gzip` and refuse any other encoding with 415 `unsupported_encoding` instead of forwarding an unread body.

The decompressed form is bounded explicitly by the same budget as the wire body — `MaxBodyBytes` in the collector, `max_request_bytes` in the API — rather than trusting whatever expansion ratio a client sends. A payload that fits the wire budget but expands past it is refused with 413. The Go reader reads one byte past the limit so a body of exactly the limit still succeeds, and the Python decoder additionally requires a single gzip member, refusing trailing data with 400. The collector forwards plain JSON upstream and never re-encodes workspace evidence, so the API sees exactly the bytes it validated before.

Go evidence ran in the pinned `golang:1.27.1-alpine3.24` image with `--network none` and a read-only source mount: `gofmt` reported nothing, `go vet ./...` and `go build ./...` passed, and `go test -count=3 ./...` plus a single-threaded `GOMAXPROCS=1` run passed. **The race detector was not run in this phase**: the alpine toolchain has no C compiler and installing one needs network access, so the earlier race evidence in this document stands for the code it covered and this phase adds none. The change introduces no new goroutine or shared state — `decompress` is a pure function and the encoding branch is sequential inside the existing handler.

New Go tests cover a gzip body accepted under three header spellings with the upstream receiving unchanged plain JSON and no `Content-Encoding`, a brotli body refused with 415, a 64 KiB compressible payload that fits the wire budget being refused with 413 once expanded, a corrupt gzip body refused with 400, and `decompress` keeping a body of exactly the limit while rejecting one byte more. `unsupported_encoding` was added to the collector's fixed metric result labels.

New API tests cover gzip acceptance with spans persisted, an uppercase header treated as the same encoding, 415 for brotli, 413 for an expanded payload, 400 for a corrupt body, 400 for trailing data after the gzip member, and 400 `invalid_body` for a plain body that is not a JSON object.

`npm run check` passed: 84 JavaScript tests, 167 SQLite tests, 26 PostgreSQL-only skips, 9 subtests, lint and production build. The isolated PostgreSQL suite passed **193 tests and 9 subtests**. Strict MkDocs and the generated schema check passed.

This is local implementation evidence. It is not production-volume ingestion, protobuf or logs/metrics support, a durable queue, or proof against a real customer exporter, all of which remain open.

## Rebuilt offline archives at schema 10, 2026-09-22

Both archives were rebuilt so the delivered artifacts match the current source. The previous ones embedded schema-8 content and trailed the tree by two schema versions and the compressed-ingestion change.

A Go toolchain was installed for this phase: Homebrew Go **1.27.1** at `/opt/homebrew/bin/go`, matching `collector/go.mod`. With cgo available natively the race detector now runs, which the container fallback used in the previous phase could not do. `gofmt` reported nothing, `go vet ./...` and `go build ./...` passed, and `go test -race -count=3 ./...` passed, with the three new gzip tests confirmed running under `-race`. This supersedes the previous phase's note that it added no race evidence.

The service archive is `output/airgap/lotuspond-local-v10.tar.gz`, **216,659,855 bytes**, SHA-256 `5beb096f16842112873861466228d3cb5ca5182491bda22eac375f5d2d59e228`, root `lotuspond-airgap-local-v10`, 13 files, verified without extraction. API image `sha256:55a012a107208aae809d844dde6dbce07b61c0598d4d7680ca3212ccaf4531d0`; collector image `sha256:220da9f6dc088644edd34c8863fe5fa1aa3a2f753e0167a4f48241ec0032cb7d`. Its packaged `schema.sql` declares version 10 and carries both `workspaces.require_mfa` and the widened `mail_outbox` constraints.

The first drill against the rebuilt bundle **failed**, and correctly so: the drill asserted a literal `schema_version == 8` that had silently gone stale. The check now reads the version out of the bundle's own `schema.sql` and asserts the running API reports that number, so the drill is tied to the artifact rather than to a constant that ages. The harness also swallowed the failing command's stderr, which cost a diagnostic round-trip; `command()` now includes a bounded stderr tail with the drill's own ephemeral credentials scrubbed. Four new tests cover the parser, its refusal of a missing or ambiguous declaration, the absence of the literal, and the redaction.

The re-run passed in **184.3 seconds**: schema version 10, deterministic failure and semantic unknown assessments, both incident categories, disabled alerts, authenticated collector ingestion with separately authenticated fixed-label metrics, the production identity gate, offline operator enrollment with the mailbox still unverified, replay rejection, mandatory operator MFA with TOTP replay denial and single-use recovery codes, trace cost null versus reported zero, and the full failure-recovery path — worker backlog, staleness past the unchanged 150-second threshold, restart, drained backlog, Redis outage with readiness failure and visible queue state, then Redis recovery. Offline revocation blocked sessions and keys while retaining business data. Five containers, two anonymous volumes and the internal network were removed and checked absent. No port was published and only synthetic data was used. The rebuilt collector's metrics inside the drill expose the new `unsupported_encoding` result label, which is direct evidence that the packaged image carries the gzip change rather than an older build.

`output/airgap/lotuspond-local-v10-content-proof.json` is now produced by `deploy/checks/content-proof.py` instead of by hand. It records that the API image embeds exactly this working copy: the 35-file `backend/lotuspond` source tree and the image's `/app/backend/lotuspond` share SHA-256 `2cea55fb07db2d6dbcb0e8bcbd8d255c449b9b14bb838888b7d9f07e7f804ebd`, and the locally built four-file `dist` and the image's `/app/dist` share `1bc7477594b1bd6c5ba4da9ff127edd18c24a5c31441c919a8239eaf3a025602`. Four tests pin the hashing rules, including that a rename changes the hash and that the NUL separators stop one tree from being forged as another. No Git provenance is claimed, because the repository still has no HEAD.

The execution archive is `output/execution/lotuspond-local-v10-execution.tar.gz`, **221,410,076 bytes**, SHA-256 `6e9b5f6a693fe64ba83fbfb11029a6ce99fe43397cb48ebed3448d7953d0cdc0`, root `lotuspond-execution-local-v10-execution`, 21 files, verified identically by the Python verifier and `deploy/execution/verify-bundle.sh`. Execution image `sha256:fe10578554f340c8b7be31d1bddcbe1fdc07b1efb25c1ef2c7a3bb77bd5f3acb`, running as uid 10001. The reviewed Gym image is unchanged at `sha256:2c37be8d20e6a6b205dbe071624b5154d1307034dc771e857fa10b7208b4eb7c`. **No execution source changed in the phases since the previous archive** — the runner, client, protocol adapters, voice bridge and Gym kit are untouched — so this is a repackage of unchanged tooling under a consistent version, not a content change.

The image-only drill passed in 1.4 seconds: uid 10001, HTTP and MCP probes inside `--network none`, voice refusing with no provider credentials, cleanup confirmed. The extracted-archive drill passed in 5.6 seconds with the same probes plus the genuine xterm `hello@1` task as run `real-bb34b1d42a6142a8`, cleanup confirmed. Reports are `lotuspond-local-v10-execution-image-smoke.json` and `lotuspond-local-v10-execution-smoke.json`.

Source suites alongside the rebuild: `npm run check` (84 JavaScript, 167 SQLite, 26 PostgreSQL-only skips, 9 subtests, lint and build), the isolated PostgreSQL suite (193 tests, 9 subtests), 34 protocol tests, 13 voice tests, **31 deployment checks with 13 subtests**, the generated schema check and strict MkDocs.

During the final pass the project's local PostgreSQL cluster was found stopped and the isolated suite could not connect. `.local/postgres.log` records an orderly `received smart shutdown request` followed by `database system is shut down` at 17:38:48 — a clean stop, not a crash, and not caused by any drill, which run their own containerized PostgreSQL on internal networks and never touch this cluster. The data directory was intact, `npm run db:start` brought it back without initializing anything, and the development database still held version 10 with 11 users, 17 workspaces, 18 memberships, 8 projects, 5 mail rows, 107 audit events and 38 tables, alongside the untouched `lotuspond_local`. The suite then passed and left no disposable schema behind.

This is local packaging and installation evidence on this machine's Docker daemon. It is not clean-host transfer, hosted operation, production capacity, live provider or customer execution, or published provenance. The earlier archives remain on disk and are not deleted.

## Subscription entitlements, assisted reset, execution boundary and A2A, 2026-09-22

Four decisions the user made or asked for, built together.

**Subscription levels decide traces.** Schema **11** narrows `workspaces.plan` to a static `developer`/`team`/`enterprise` ladder held in server code, so a request body can never name a fourth tier and a stored row can never be one the server cannot enforce. Narrowing is verified against every existing row: a workspace already outside the ladder rolls the whole migration back rather than being rewritten to a tier nobody chose. Only a platform operator can change a tier, through an audited `PATCH /admin/workspaces/:id/plan`; a workspace administrator cannot upgrade their own workspace, because Lotuspond collects no payment and an upgrade is a commercial act performed elsewhere. `payment.status` is always `not_collected`.

The monthly allowance is serialized on the workspace row exactly as gateway spend already is, so concurrent ingestion decides in turn. Usage counts **server receipt time**, never the producer-supplied `occurred_at`, so a span backdated forty days still spends the month it actually arrived in — asserted directly. An idempotent replay stores nothing and therefore spends nothing. `POST /traces` returns 402 `trace_quota_exceeded`; OTLP reports `quota_rejected_spans` separately from malformed `rejected_spans` so a producer can tell a quota wall from bad data, and returns 402 only when nothing at all was stored, because a 202 there would let retry logic record success for evidence that was never kept.

Retention governs deletion, not visibility. A trace stays readable and exportable until `npm run retention` removes it, so a plan change never makes evidence silently disappear — asserted by a test that ages a trace far past its window and confirms it still lists and exports. That job is the only code in Lotuspond that deletes customer evidence, so it reports by default, deletes only with `--apply`, refuses a run above `--max-deletions` because an unexpectedly large expiry is more likely a misconfigured plan than a real one, and writes an audit record per workspace naming the plan and cutoff. `GET /traces/export` carries its own `export_sha256` over canonical JSON and is available on **every** plan: a subscription limits how much evidence is accepted and kept, never whether a customer may take their own evidence with them.

**An organization administrator can authorize a reset.** `POST /auth/mfa/members/:id/reset` clears a member's authenticator when they lost every factor. It requires a different person, the acting administrator's own password, an organization, and refuses a platform operator's factor so a workspace cannot clear what protects the installation. It only removes — factor, recovery codes, pending enrolment, sessions, challenges — and issues no session, factor or password, so it cannot be used to enter the account; a test walks the member all the way back in under their own password and a fresh enrolment. The member is notified through the existing link-free notice channel.

**The process-group question is now decided**, recorded as [ADR 0008](architecture/0008-adapter-execution-boundary.md). A process group is a lifecycle bound, not a privilege bound. That is adequate because the supervised runner is customer-side: it executes the operator's own adapter on the operator's own host under their own account. Code Lotuspond supplies rather than the customer — the Gym task image — runs in a container with no network instead, and the execution image still never receives a Docker socket. `LOTUSPOND_ADAPTER_ROOT` adds defence in depth for operators who run the runner as a long-lived service, confining which directory may supply an adapter and resolving symlinks first so a link inside the root cannot point out of it. Hosted multi-tenant execution is explicitly out of scope under that decision rather than left looking half-built.

**A2A is integrated.** `protocol-adapters/src/a2a-runner.mjs` sends JSON-RPC `message/send` over the same hardened transport the HTTP adapter uses, which was extracted to `http-transport.mjs` so both adapters share one way out to a customer host — the 34 existing protocol tests passed unchanged after that extraction. Only a completed task is judged: `working`, `input-required` and `auth-required` record `task_unfinished`, and `failed`, `canceled` and `rejected` record `task_not_completed`, all unknown with the state kept, because an unfinished exchange is a fact about the conversation rather than an answer. A JSON-RPC error keeps its numeric code and drops the agent's message text; an operator credential echoed back as output is refused as `sensitive_output`. Streaming and task resumption are not implemented. `runner-a2a` is a packaged execution entrypoint alongside `runner-http` and `runner-mcp`.

Suites: `npm run check` passed with 89 JavaScript tests, 181 SQLite tests, 28 PostgreSQL-only skips, 9 subtests, lint and build. The isolated PostgreSQL suite passed **209 tests and 9 subtests**, including two version-11 migration tests. Protocol adapters 42, voice 13, deployment checks 31 with 13 subtests, generated schema check and strict MkDocs all passed. The development database migrated 10 to 11 with 17 workspaces, 5 traces and every row preserved.

Browser QA ran on a disposable `lotuspond_platform_test` schema. After three real trace ingestions the Settings page showed the plan card reading "Developer", "Within allowance", "3 of 10,000 traces used this month, 9,997 remaining", the seven-day retention sentence including that traces stay readable until the retention job removes them, and both statements that no payment instrument is stored and that a workspace administrator cannot change the plan. The schema and the temporary environment file were removed and the development database was untouched.

This is local implementation evidence. No payment provider, customer A2A endpoint, or production-volume ingestion has been exercised, and the offline archives still carry schema-10 content until they are rebuilt again.

## Public introduction, 2026-09-22

The signed-out page was rebuilt as a drawn pond scene followed by an editorial document (`web/src/pages/Landing.tsx`, `web/src/components/Pond.tsx`). No API, schema or authorization code changed apart from a changelog entry and `StatusBadge` giving `approval_required` the documented warning tone.

`npm run check` passed with 89 JavaScript tests, 181 SQLite tests, 28 PostgreSQL-only skips, 9 subtests, lint and build. The isolated PostgreSQL suite was not rerun because no data access changed.

Browser checks ran in the desktop app's Chromium pane against the Vite server and the development API, signed out, at 1440×900, 768×1024 and 375×812. At each width the page had no horizontal scroll and the measured sun disc sat on the headline baseline, which the canvas uses as its horizon. The headline stayed on one line at 1440 and balanced over two lines at 768 and 375. Scrolling measured the reflection's displacement falling from 15.5 to 1.0, the header switching from the scene tone to the page tone past the water, and the policy record's lift easing from 56px to 0. The in-page contents and header links scrolled to their sections without changing `location.hash` and moved focus to the section heading. Keyboard Tab order reached the hero actions, the six contents links and the scrollable code sample with a visible outline. Choosing an organization on the page opened registration with that choice selected, and "Back to overview" returned to the page.

The pane renders frames only when captured, which exposed an entrance that waited on an `IntersectionObserver`; a scene already on screen now starts at mount, verified by reading the animation state immediately after reload. The reflection's SVG displacement filter on HTML content was not checked in Safari or Firefox, reduced motion was verified by code path rather than an emulated preference, and no screen reader pass was made. This is local rendering evidence, not a hosted deployment.

### Animated plates, 2026-09-23

Thirteen canvas plates (`web/src/lib/pondArt.ts`, `web/src/components/PondPlate.tsx`) and four SVG agent signals (`web/src/components/AgentSignal.tsx`) were added to the signed-out page, and Guarantees became a numbered charter. Blender 5.2.2 was installed at the user's request but not used; the plates are drawn live so they match the hero, stay sharp at any density and add no media downloads.

`npm run check` passed with 89 JavaScript tests, 181 SQLite tests, 28 PostgreSQL-only skips, 9 subtests, lint and build. In the Chromium pane at 1440×900 each plate was captured before and after its entrance: passed opened, failed sank and left rings, unknown was covered by fog, missing showed only a dashed outline, the workflow plates reached the snapshot frame, emerged stem, settled ring and open bloom, and the deployment and account plates reached their wall, dome, cables and colony. At 375×812 the outcomes formed a two-by-two grid and the page had no horizontal scroll. Plates are `aria-hidden`; every outcome, step and option keeps its written text. Reduced motion was verified by code path only, and other browsers were not checked.

### Field-record brand theme, 2026-09-23

After reviewing how Anthropic's site layers a warm palette, a pictographic ink glyph set, data-drawn research visuals and textured collage, the introduction adopted its own theme: the pond remains the signature scene (hero, closing band, account plates) and every other section uses a field-record vocabulary — ink glyphs (`InkGlyph`), outcome traces on graph paper (`TraceSpecimen`), a scroll-following workflow thread (`ThreadedSteps`) and an "Immutable" stamp — on a warmer paper scoped to `.landing`. The outcome, workflow and deployment pond scenes were removed from `pondArt.ts`.

In the Chromium pane at 1440×900 the traces reached their completed, broken, dissolving and ghost states; the thread reported a dash offset of 0.56 with two of four glyphs inked mid-scroll and all four once passed; the deployment glyphs and the stamp rendered; the header held the page tone over light sections. At 375×812 the thread was hidden, glyphs inked individually, outcomes formed a two-by-two grid and the page had no horizontal scroll. Reduced motion was verified by code path only.

### Platform preview and recently shipped, 2026-09-23

Following a review of how vercel.com presents its product (UI rebuilt in code with obviously fictional data, streamed logs and charts, an interactive hero and a "recently shipped" grid), the introduction gained a platform tour and a changelog strip. The tour reproduces the console with its own tokens and a single labelled sample workspace; its decisions come from `web/src/lib/policyPreview.ts`, a browser port of `evaluate` in `backend/lotuspond/policy.py`.

`policyPreview.test.ts` passed five cases, including one that reads `policy.py` and requires every preview reason string to appear in it verbatim, and ordering cases for coverage, pass rate, critical failure, latency and regression. In the Chromium pane the release view started blocked with five reasons; cycling the unknown critical outcome through missing to passed produced "Independent approval required" with the single approval reason, and approving produced "Passed this release policy". The run, evidence and gate views streamed to their final states, and the gate output repeated the sample decision's reasons. The tour had advanced on its own while unattended and stopped after a click. "Recently shipped" rendered the three latest entries from the local API. At 375×812 the tabs formed a two-by-two grid, the console sidebar was hidden and the page had no horizontal scroll.

The sample workspace is illustrative and is labelled as such in the window. It is not customer data or a measured result.

### Console redesign, 2026-09-23

The signed-in console was redesigned in the field-record system and the public platform preview was rebuilt from the console's own classes and components. Stylesheets were split into `web/src/styles/console.css` and `web/src/styles/landing.css`; 173 superseded console rules and 31 duplicated preview rules were removed rather than overridden. `DecisionSummary` and `VerdictStamp` are shared by `RecordDetails` and the preview.

Browser review used the new development-only harness `web/dev/console.html`, which renders the real `App` with fixture responses; the production build was confirmed to contain neither the harness nor its fixture strings. At 1440×900: releases, traces, settings, incidents and the create form rendered in the new system; the empty scenarios list drew its glyph and offered "Add scenario"; evaluating a fixture release opened the drawer with a "Blocked" stamp and the preview's five reasons; the drawer took focus, closed on Escape, restored page scrolling and returned focus to its "Open" button. At 375×812 a scrolling table initially widened the page to 622px because a visually hidden, absolutely positioned header label escaped its container; making `.table-wrap` a positioning context fixed it, and the Menu revealed the switchers, navigation and account with the page at 375px. On the landing page the preview's release view cycled to "Approval required" and "Passed" stamps with an `h4` decision heading.

`npm run check` passed with 94 JavaScript tests, 181 SQLite tests, 28 PostgreSQL-only skips, 9 subtests, lint and build. Authenticated flows against a real API were not exercised in this pass; the harness shows layout, not server behaviour.

### Console navigation and onboarding, 2026-09-23

The console was redesigned again after it still read as dated: a daylight frame with an inset workspace, icon-and-label navigation regrouped in workflow order (Home, Build, Evaluate, Production, Workspace), a workspace switcher, a ⌘K command palette, and a Home page with a six-step setup checklist computed from the workspace's records. The approach follows NN/g on left vertical navigation (visible, left-aligned, text labels with front-loaded keywords, less-used items last), GitLab Pajamas on sidebars (a stable header with a context switcher, two levels at most), onboarding practice (a value-ordered checklist, empty states that name one action, a way to skip) and command-palette practice (a shortcut to visible navigation, prefix-first ranking). The public pages keep their warm paper by overriding tokens on `.landing`; the preview window resets to the console tokens and renders `NavList`.

`npm run check` covers new tests for command ranking and setup summarising. In the fixture harness at 1440×900: an empty workspace showed "0 of 6 complete" with "Create project" highlighted; that button opened Projects with its form open; ⌘K with "rel" and Enter reached Releases, and "add sc" with Enter opened Scenarios with its form; the workspace switcher listed both workspaces with the current one checked. At 375×812 the page stayed 375px wide and the Menu revealed the switcher, search and navigation. The landing preview rendered the new frame and navigation. Authenticated flows against a real API were not exercised.

## Accessibility review, 2026-09-23

The console and public pages were reviewed against WCAG 2.2 AA, the last open item of the design-system requirement that needed no customer. Automated scans used axe-core 4.13.0, loaded into the desktop app's Chromium pane from jsDelivr at test time (it is not a project dependency), with the `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` and `best-practice` rules. The console was scanned through the fixture harness: all 23 routes and the not-found route, the `?failing` state, and the open palette (full and empty), record drawer, record form, workspace switcher and account menu, at the pane's desktop width and at 768, 375 and 320px. The public introduction, sign-in, registration and the five identity pages were scanned signed out against the development API at desktop, 768 and 375px, and the introduction also at 320px.

Before the fixes the scans reported six problems: no `h1` on the not-found route, `aria-label` on an unroled `div` (voice sessions), an unnamed icon-only "What's new" link below 860px, a "Forgot password?" target overlapping the sign-in button (2.5.8), the charter numerals at 2.71:1 on the public paper, and an `aria-label` on an unroled `pre`. Manual review found more, which axe cannot see:

- The focus ring (`#86ad9d`) measured 2.48:1 on white and 2.27:1 on the console canvas, below the 3:1 of 1.4.11. Filter placeholders, which are the only visible label on several filters, measured 3.23:1, and text-field edges 1.50:1.
- The header wordmark rendered in ink over the dark pond scene at about 1.1:1. The console redesign had given `.wordmark` a fixed colour, which overrode the header's scene tone; it now inherits the tone again.
- No view set a browser title (2.4.2); every tab read "Lotuspond".
- Loading lists were a `role="status"` with no text, row actions were a column of identical "Open"/"Evaluate" buttons, and filter counts changed silently (4.1.3).
- In the command palette, Tab moved focus onto the scrollable result list and then to the page behind the modal, and once the list had focus Escape no longer closed it.
- Closing the record drawer opened by Evaluate, Cancel, Approve, Reject or Revoke dropped focus on the document, because only Open recorded the opener and the action's refresh re-mounted the rows.
- Choosing a page from the narrow-screen menu left focus on a link that had just been hidden, and an open account panel could cover the navigation items Shift+Tab reached next (2.4.11).
- Home was 332px wide at a 320px viewport (1.4.10), the Incidents "Review" action broke mid-word at desktop width, the sign-in page put an `h2` tagline before its `h1`, and several password and budget rules were not associated with their fields.

After the fixes every scanned state reported no violations and no page scrolled sideways at any width. Keyboard checks in the pane: Tab and Shift+Tab stayed on the palette's field, typing "rel" announced "2 results" in groups named "Go to" and "Create", and Escape closed it and returned focus to "Search or jump to". Evaluate followed by Escape returned focus to "Evaluate Refunds r42". The account panel closed when Shift+Tab moved to Settings, stayed open while Tab moved into it, closed on Escape with focus back on its button, and a click on "Account security" inside it still navigated. At 320px Escape closed the Menu and returned focus to it, and choosing Releases from the Menu moved focus to the page, announced "Releases" and set the title to "Releases · Personal · Lotuspond". Screenshots confirmed the light ring on the scene, the dark ring on paper, the ring drawn inside navigation rows, the legible wordmark and numerals, and the darker field edges. With the WCAG 1.4.12 text-spacing overrides applied, only the sidebar's account email truncated; the full address is on Account security.

`web/src/lib/contrast.test.ts` now reads the tokens out of `console.css` and `landing.css` and holds text to 4.5:1, large display type to 3:1, and text-field edges and the focus ring to 3:1 on every surface they are drawn on; `web/src/lib/title.test.ts` covers view titles. `npm run check` passed with 105 JavaScript tests, 181 SQLite tests, 28 PostgreSQL-only skips, 9 subtests, lint and build. The isolated PostgreSQL suite was not rerun: the only backend change is a changelog entry.

Limits: Chromium only; no screen reader was used, so announcements were verified by reading the accessibility tree and live-region text rather than by listening; forced colours and reduced motion were checked by code path, not emulated; the pane's key injection does not activate native buttons with Enter or Space, so activation used clicks while Tab, Shift+Tab, Escape, arrows and typing used real key events; and the signed-in console was exercised on fixtures, not against the API. This is local evidence of the reviewed states, not a conformance claim.

## OTLP protobuf ingestion, 2026-09-23

OpenTelemetry SDKs export OTLP/HTTP as binary protobuf by default, and until now both the API and the collector accepted only JSON, so a default exporter could not reach Lotuspond without reconfiguration. Both now accept `application/x-protobuf` alongside JSON, with or without gzip.

The API decodes an `ExportTraceServiceRequest` in `backend/lotuspond/otlp_protobuf.py`, a dependency-free reader of the protobuf wire format against the stable opentelemetry-proto v1 trace schema, into the canonical OTLP/JSON form — hex identifiers, integer enums, 64-bit integers as decimal strings, bytes as base64, default-valued scalars left out — which then meets the unchanged JSON validation, allowance and redaction. A protobuf export is answered 200 with an `ExportTraceServiceResponse` whose `partial_success` names how many spans were not kept and why; errors stay JSON. The middleware that confines mutating API requests to `application/json` now also admits `application/x-protobuf` on that one route; like JSON it is not a CORS-safelisted type, and a test confirms every other route still refuses it with 415. The collector (`collector/internal/collector/otlp.go`) checks the wire format against the same schema, forwards the bytes unchanged with their media type, and relays only a well-formed response in the request's encoding.

The tests are anchored to the reference implementation: the request and partial-success bytes were serialized by opentelemetry-proto 1.44.0 (protobuf 7.36.2) in a throwaway environment, and the decoder reproduces that package's own dictionary for the request field for field, while `encode_trace_response` reproduces its response bytes exactly. The same bytes back the Go tests. Twelve malformed-body cases (truncation, lengths past the end, group and undefined wire types, field zero, wrong wire type, an eleven-byte varint, non-UTF-8 strings, short fixed-width fields) are refused identically by both. An endpoint test sends one span as protobuf and its twin as a JSON exporter writes it, and asserts the stored name, status, latency, cost, measurement, time, trace identifier, kind, resource and span attributes are equal, with `api_key` redacted in both. Local mutation runs found no crash: 3,245,827 mutated bodies against the Python decoder in 45 seconds were each decoded (94,616) or refused as invalid (3,151,211), and `go test -fuzz=FuzzValidateTraceExport` ran 5,473,926 executions in 60 seconds, adding 91 interesting inputs and no failure. A seeded 3,000-body slice and the fuzz seeds now run in the ordinary suites.

`scripts/checks/otlp-interop.py` then drove the real thing: the official OpenTelemetry Python SDK 1.44.0 with `opentelemetry-exporter-otlp-proto-http` 1.44.0, left on its default `http/protobuf`, exported two spans in each of four scenarios — straight to the API and through a freshly built collector, each uncompressed and gzipped — against the real API on a throwaway SQLite database in a private temporary directory, all on loopback. Every export returned `SUCCESS`; all 8 stored traces matched the recorded spans' name, status, latency, service, kind and attributes; both processes stopped and the directory was removed.

That run exposed a redaction flaw. Any attribute whose name contained "token" was stored as `[redacted]`, which erased the standard OpenTelemetry GenAI usage counts (`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`) from every instrumented agent. A key ending in `tokens` whose value is a non-negative integer, or OTLP's decimal-string form of one, is now kept as a usage count; `access_token`, `token`, `refresh_tokens` lists, `id_tokens` strings, booleans, negatives, over-long numbers and any key that also names a secret, password, authorization, cookie or API key remain redacted, each asserted in `test_policy.py`. The interop run confirmed `gen_ai.usage.output_tokens` arrives as `"42"`.

Suites: `npm run check` passed with 105 JavaScript tests, 207 SQLite tests, 28 PostgreSQL-only skips, 9 subtests, lint and build. The isolated PostgreSQL suite passed **235 tests and 9 subtests** against `lotuspond_platform_test` on the verified cluster at `127.0.0.1:55439` (data directory `.local/postgres`), leaving no disposable schema. Go: `gofmt`, `go vet` and `go test -race -count=3 ./...` passed. Deployment checks 31, strict MkDocs and the generated schema check passed; no schema change was needed.

Limits: the SDK run used Python only, not the Go, Java or JavaScript exporters; gRPC, OTLP logs and metrics, and span events and links remain unimplemented or unretained; nothing ran under production load; and the offline archives, built before this change, do not yet contain it.

## Signed evidence manifests, 2026-09-23

`lotuspond-evidence` gained publisher identity, the Rust tool's open gap. `keygen` writes an Ed25519 key pair as PKCS#8 and SubjectPublicKeyInfo PEM, the private key owner-only; `sign` signs a well-formed manifest's exact bytes behind the context line `lotuspond-evidence-manifest-v1`; and `verify-signed` trusts only the public key it is handed, refuses another key's signature by naming both key ids, refuses a signature for different manifest bytes, verifies strictly, and then checks the very bytes it verified against the artifact directory. Plain `verify` keeps its integrity-only meaning. Signatures use `ed25519-dalek` 3.0.0, which shares `sha2` 0.11 with the existing tool; key generation reads the operating system's random source through `getrandom` 0.4.3, and seeds are zeroized. `cargo tree -d` reports no duplicate crate versions.

The tests are anchored outside the code under test. RFC 8032's first test vector reproduces its public key and signature. OpenSSL 3.6.2 (Homebrew) independently produced the PKCS#8 and public-key PEM for that RFC key and the signature over the framed test manifest; the Rust code parses both PEMs, writes the same public PEM, and produces a byte-identical signature. Further tests cover a verified round trip, a rewritten manifest, a flipped signature byte, a changed artifact, another publisher's key, unsupported algorithm, context, schema and unknown fields, refusal to overwrite a key or signature, the 0600 private key, and refusal of a key other users can read. `cargo test --locked` passed 10 tests.

The release binary was then driven end to end in a scratch directory: create, keygen (private key `-rw-------`), sign and `verify-signed` succeeded, and OpenSSL alone verified the CLI's signature from the public PEM and the documented message. A changed artifact, another key and an overwrite attempt each exited 1 with a message naming the file or both key ids. `cargo-audit` 0.22.2, installed into the session's scratch directory rather than the user's toolchain, loaded 1,266 RustSec advisories and reported none for the 40 crates in `Cargo.lock`.

Limits: signatures prove which key published a manifest, not when; there is no timestamp authority, revocation, transparency log or hardware custody, the offline archives do not yet sign their own manifests, and no distribution drill was run. Formatting used the default stable toolchain's `rustfmt`, since the pinned 1.98.1 toolchain has the minimal profile.

## Durable collector spool, 2026-09-23

The collector's last named gap was that a batch it could not forward, or held when its process died, depended entirely on the exporter's retry policy. `COLLECTOR_SPOOL_DIR` now enables an fsynced write-ahead spool (`collector/internal/collector/spool.go`). Every admitted batch is written to `tmp/`, synced, renamed into `pending/` and the directory synced before the first attempt. A batch the API answers, with success or a permanent refusal, leaves the spool and the exporter hears that answer unchanged. A batch the API cannot answer (transport failure, timeout, 5xx, 429 or an unreadable reply) is acknowledged `202` with `Lotuspond-Delivery: spooled` and replayed in the background with doubling, jittered backoff that honours `Retry-After`, until the API stores it or refuses it; a refusal during replay moves it to `rejected/` for the operator. Batches carry their media type, size and SHA-256, and one that fails its digest is never forwarded. A full spool answers 503 so the exporter keeps the batch, and a batch the queue cannot take leaves the spool. The directory must be owned by the collector's user and closed to others, and the collector refuses to start otherwise. It stays off unless configured, so the stateless default is unchanged.

`go test -race -count=5 ./...` passed, covering the directory rules (a 0755 directory, a symbolic link and a relative path are refused), 0600 batch files, capacity accounting, recovery after an interrupted process (partial writes discarded, surviving batches due at once and intact), delivery of spooled JSON and protobuf byte for byte once the API returns, release of answered and permanently refused batches, retention of a replay the API refuses, rejection of a batch that fails its digest, survival across a closed process, a full spool, a full queue, the new metrics and the configuration bounds.

`scripts/checks/otlp-interop.py` gained a crash drill with the real OpenTelemetry Python SDK 1.44.0: with the API not yet started, an export through the spooling collector returned `SUCCESS`; the collector was then killed with SIGKILL (exit −9) leaving one pending batch; the API started and a fresh collector over the same spool delivered it in 0.22 seconds with nothing rejected. The four interop scenarios then ran through the spool-enabled collector, and all 10 stored traces matched the recorded spans. The collector image now ships an empty owner-only `/spool`. Built and run read-only with a fresh named volume, the volume came up `700 65532:65532` with `pending/`, `rejected/` and `tmp/` alike, and a batch sent while the API was unreachable was answered `202` with `Lotuspond-Delivery: spooled` and written `-rw------- 65532`. The first image attempt failed that check — `COPY` of a directory recreated it as 0755 and the collector correctly refused to start — and the Dockerfile now copies the directory as an entry of a tree. The container, volume and image were removed afterwards. Both Compose files mount a `collector-spool` volume and pass `COLLECTOR_SPOOL_DIR`, empty by default, and validate with `deploy/example.env`.

Suites: `npm run check` passed with 105 JavaScript tests, 207 SQLite tests, 28 PostgreSQL-only skips, 9 subtests, lint and build; Go `gofmt`, `go vet` and `go test -race -count=3 ./...`; `cargo test --locked` 10 passed; deployment checks 31; strict MkDocs.

Limits: throughput under an fsync per batch and behaviour at production volume were not measured, the spool is single-node and not shared between collector replicas, and neither the offline archives nor the install drill contain it yet.

## Rebuilt offline archives at schema 11, 2026-09-23

Both archives were rebuilt so the delivered artifacts carry the current source: schema 11 with subscription entitlements, the redesigned console and its accessibility fixes, OTLP protobuf ingestion and the token-count redaction rule in the API, protobuf and the durable spool in the collector, and the A2A adapter in the execution image. The Lotuspond images were built from this working copy and packaged with `--local-images`, so no infrastructure image was pulled and PostgreSQL (`sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`) and Redis (`sha256:29e8589c3f9ba699b5f7aa4b3c7733c58852a3626439e619aa0ee78de08c6ca0`) are byte-identical to the schema-10 bundle.

The service archive is `output/airgap/lotuspond-local-v11.tar.gz`, **216,750,453 bytes**, SHA-256 `46d16fee3c557bbf6a09b6a1aab14d808753420ed76a1e6611ca36a79ac5638b`, root `lotuspond-airgap-local-v11`, 13 files, verified without extraction. API image `sha256:b28b8a4ca626e24cc05be328982efc4c8335a6e948c5e9a08caf03c0c2857037`; collector image `sha256:cf2c5889aee151b07369bd973117ab1ea2e21a458c48272b7d6553747e978f47`.

The installation drill with `--failure-recovery` passed in **186 seconds** and reported schema version 11 from the running API, read from the bundle's own `schema.sql`. Two checks were added for this archive and passed: an OpenTelemetry-style protobuf `ExportTraceServiceRequest` sent through the packaged collector was answered 200 with an empty `ExportTraceServiceResponse` and its span listed by the API, and the collector ran with `COLLECTOR_SPOOL_DIR=/spool` on an anonymous volume over the image's owner-only directory, its metrics showing the 256 MiB spool empty after delivery. Everything the previous drill proved passed again: deterministic failure and semantic unknown assessments, both incident categories, disabled alerts, authenticated collector ingestion and separately authenticated fixed-label metrics, the production identity gate, offline operator enrollment with the mailbox unverified and replay rejected, mandatory operator MFA with TOTP replay denial and single-use recovery codes, trace cost null versus reported zero, the worker backlog, staleness, restart and drain, the Redis outage and recovery, and offline revocation that blocked sessions and keys while retaining business data. Five containers, three anonymous volumes (database, cache and spool) and the internal network were removed and checked absent; no port was published and only synthetic data was used.

`deploy/checks/content-proof.py --require-match` passed: the API image's `/app/backend/lotuspond` and the working copy's 38-file `backend/lotuspond` share SHA-256 `6b67851bb7de0b0eee745c78c6840e8d1c6b29732ae1db0f565c5e1d8576a3bb`, and its `/app/dist` and the locally built four-file `dist` share `1ac8081e1c59a7cf6e2ef06f265841591d7594bcdb88a82558ee84018dcbddc8`. The proof is `output/airgap/lotuspond-local-v11-content-proof.json`; no Git provenance is claimed.

The execution archive is `output/execution/lotuspond-local-v11-execution.tar.gz`, **221,417,256 bytes**, SHA-256 `b900f281ca386c0c4d644de979d86e1f281e3d424b2597740248784c31775cfd`, root `lotuspond-execution-local-v11-execution`, 21 files. Execution image `sha256:8b49b2037718d140e6e59ce51245adb4a15a6a82fcd08accd281c997cb128cea`; the reviewed Gym image is unchanged at `sha256:2c37be8d20e6a6b205dbe071624b5154d1307034dc771e857fa10b7208b4eb7c`. The execution drill gained an A2A probe, a loopback JSON-RPC agent answering `message/send` with a completed task, and now requires `runner-a2a` in the image's help. The image-only drill passed in 2.8 seconds (uid 10001, HTTP, MCP and A2A inside `--network none`, voice refusing without provider credentials, cleanup confirmed), and the extracted-archive drill passed in 7.8 seconds with the same probes plus the genuine xterm `hello@1` task as run `real-90f92ac5ef5d25ed`, cleanup confirmed and no sandbox container left.

Reports: `output/airgap/local-v11-install-report.json`, `output/airgap/lotuspond-local-v11-content-proof.json`, `output/execution/lotuspond-local-v11-execution-image-smoke.json` and `output/execution/lotuspond-local-v11-execution-smoke.json`. This is local packaging and installation evidence on this machine's Docker daemon, not clean-host transfer, hosted operation, production capacity or live provider or customer execution. Earlier archives remain on disk.

## Immutable workflow package registry increment

The separate `codex/package-registry` increment extends foundation `c1da02edcb3fb4db57f63a8e403db8e379cf5a2a`. See [registry validation and migration evidence](plans/2026-10-13-package-registry-progress.md) for the restored cloud baseline, exact commands, independent review and limitations. Cloud tests, GitHub publication/CI and deployment remain separate states.

## Connections and runtime identity (E03)

See [the E03 qualification record](validation/2026-10-14-connections-fleet.md) for
actual source/artifact/API/PostgreSQL evidence and remaining limitations. The
installed ordinary CLI lifecycle is separate from an explicitly authorized
fixture-library signed-command/child journey. Production authorities, business
execution, customer restore/custody/network qualification, licensing and UI remain
open. Local qualification, hosted exact-head CI and deployment are separate gates.

## Required PostgreSQL CI allowance with durable execution, 2026-10-22

Branch `codex/durable-execution`. Hosted run `38731740438` at `7bbb92d` failed only because the required PostgreSQL step hit its 30-minute bound. The log shows 998 tests collected and the run at 78% (`tests/test_migrations.py`) with no failed, errored or stalled test: the median gap between results was 1.8 s, and the longest, 307 s, is the designed `tests/fleet/test_acceptance.py::test_migration_then_installed_journey`. The runner smoke was never reached. `tests/execution` adds 282 PostgreSQL tests (998 against main's 716), which took 705 s in that run; main's same step at `ca60864` ran 716 tests in 1089.58 s.

The same 998 tests completed in hosted run `38734866955` (stacked Studio head `e9ff15b`): `998 passed, 1 warning, 97 subtests passed in 1762.94s (0:29:22)`, after which the bound stopped the runner smoke. Compared at the last common test, the slower runners of `38731740438` and `38732845075` were 1.15× and 1.27× slower, projecting 34.0 and 37.4 minutes of pytest. The step bound is now 60 minutes: at least 1.5× the slowest projection, and still a hang bound far below the job's six-hour default. Tests, database guards, owned-schema cleanup, the runner smoke and every retained artifact path, including `fleet-report.json`, are unchanged; nothing is skipped, sharded or reordered. Faster owned-schema fixtures or a split step were not chosen because they change the qualification harness while E04 is still in progress. Later E04 tasks add tests, so revisit the bound from hosted `--durations` output rather than raising it again.

Hosted result at correction head `592fdd4`: run `39739131739` passed every job. The required step took 30 min 41 s, so the old bound would have stopped it again. It reported `998 passed, 1 warning, 97 subtests passed in 1784.93s (0:29:44)`, then the runner smoke reported all seven cases true, cleanup confirmed, no external network and schema version 14. The retained `fleet-postgres-python-3.14` artifact holds 124 files, including six `fleet-report.json` reports. On the stacked Studio head `abe7696` (run `39739133362`), the same step took 39 min 26 s on a slower runner (`2310.62s (0:38:30)` of pytest) and also passed, which leaves about 1.5× headroom against the slowest observed run. This is hosted CI evidence at those heads, not deployment.

## E04 Task 7: PostgreSQL orchestration claim, schema v15 and worker, 2026-10-23

Branch `claude/e04-postgresql-impl` from `7b3a0c7` (PR #13's recorded owner approval of Revision 2). Local evidence only: not pushed, no hosted CI and no deployment.

**Environment.** A private disposable PostgreSQL 14.20 cluster (Homebrew, macOS arm64) on `127.0.0.1:55471`, TCP only, with its own data directory and the `lotuspond_platform_test` database. Its `TimeZone` is UTC to match CI's container. No other cluster, port 5432, port 55439 or development database was used. Python 3.14.3. CI runs PostgreSQL 18.6; the frozen v14 check-constraint texts in `migration.py` were captured on 14 and use the same forms as the v13 texts that already pass there, so hosted CI must confirm them.

**Red before implementation.** The new tests were copied onto unmodified `7b3a0c7`. The contract, orchestrator and worker tests failed at collection because `engine_contracts`, `orchestrator` and `execution_worker` did not exist. The v15 migration tests failed: the initializer refused a v14 database as already current, and `migrate_execution_v15` was missing.

**Green after implementation:**

- The named Task 7 tests in `test_engine_contracts.py`, `test_orchestrator.py` and `test_execution_worker.py`: 28 passed. `test_migration_v15.py` with the existing `test_migration.py`, `test_admission.py`, `test_inbox.py` and `test_outbox.py`: 124 passed. `tests/test_migrations.py` and `tests/fleet/test_migration.py`: 68 passed. No database skips.
- The full backend suite against the same cluster (`pytest -q`, 1,047 tests): 1,039 passed and 8 failed in 1,035 s. All 8 failures are in `tests/fleet/test_acceptance.py`. Its harness refuses non-Linux hosts on purpose (`lotuspond_runtime.state.private_directory` returns `unsafe_storage`), so these tests run only in CI's Linux job. This run used the content before the last review round; that round's engine and test fixes were then rerun with the targeted sets above.
- **Measured.** A worker stopped with SIGSTOP while holding an execution row was ended by `idle_in_transaction_session_timeout` (5 s in the test), and a second worker claimed the execution 4.998 s after the stop. A worker stuck inside a transition exited through the shutdown bound with code 3 after 2.047 s (bound 2 s). Cleanup of the owned sessions was observed in `pg_stat_activity`.
- Ruff is clean and `export_schema.py --check` matches `schema.sql` at version 15.

**Reviews.**

- The independent specification review approved Task 7 with seven minor findings. All were fixed and the reviewer confirmed them.
- The independent quality review required changes for two liveness defects:
  - The workspace `FOR KEY SHARE` lock waited on API transactions, so one tenant could stall every claim.
  - A pending capacity step, or a pass that made no progress, re-marked the execution due at once and spun the worker.

  Both were fixed, and the re-review passed. Its last minor findings were fixed too: continuation after progress with a non-capacity reason, back-off for repeated capacity refusals, and two test hazards.

**Proposed for the owner's acknowledgement** (marked in the plan):

- Locked workspaces are skipped like locked executions. The cost is that a tenant with continuous traffic can have its own executions skipped repeatedly; Task 13 tier 3 measures this.
- Work that made no progress is re-marked only after back-off.
- A new `apply_capacity` interface for Task 8.

**Pre-existing, out of scope.** On unmodified `7b3a0c7`, 7 tests (3 in `test_assurance.py`, 4 in `test_entitlement.py`) fail when the PostgreSQL session time zone is not UTC and pass with UTC. Release-binding evidence compares timestamps serialized in different time zones.

## E04 Task 8: deterministic interpreter v1 as row transitions, 2026-10-23

Same branch, cluster and limits as Task 7. This is local evidence only: not pushed, with no hosted CI and no deployment.

**Red before implementation.** The new tests were copied onto the Task 7 commit `77959d9`. All three modules failed at collection: `lotuspond.execution.expressions` and `lotuspond.execution.program` did not exist. Each later review fix added a test that failed with that fix reverted. Covered: an in-time signal for a wait the sweeper expired; a wait opened beside running compute; retention while paused; back-off of a capacity step that changes nothing; a wait at quota throttling sibling progress; and a sibling retry delayed by the capacity back-off.

**Green:**

- The full backend suite, run from a clean checkout of the final reviewed content (`129948a`, 1,088 tests): 1,080 passed and 8 failed in 821 s. All 8 failures are in `tests/fleet/test_acceptance.py`, which runs only on CI's Linux job (see Task 7).
- 35 golden progression records for `lotuspond.execution-interpreter.v1` re-derive byte for byte. They cover every admitted step kind and all seven actions and store actions, regions and the wake time. Recording them is an explicit command (`python -m tests.execution.test_progression_fixtures`), never part of a test run.
- **Derivation bound.** A 1,000-item iteration with eight body steps (9,001 steps, the largest expansion the SDK admits at 1,000 items; the test proves one more body step is refused) ran 1,280 transitions in 20 transactions. Across 3,079 region loads the largest read 278 rows, against 1,280 committed step instances. Root loads stayed within the 1,000 pinned item paths, and item-region loads within 8 rows.
- **Throughput, a Task 13 follow-up.** Each 64-transition transaction took about 1.5 s on this laptop, with per-transaction decryption and key caches.
- Ruff is clean. The schema is unchanged at version 15.

**Reviews.** The specification and quality reviews each ran four rounds.

- **Round 1** required changes:
  - a pending wait retention blocked every release (Critical);
  - branch IDs could collide with composite records, fixed with the `#` record grammar;
  - oversized values retried forever;
  - a late-consumed signal was refused;
  - provider calls were made under the row lock, fixed by passing the fenced epoch;
  - item typing failed open, the compiled plan was not checked against a recompilation, and inbox decryption errors were swallowed;
  - there were gaps in the tests and goldens.
- **Round 2** found that the fixes had added regressions:
  - an expired wait no longer loaded its in-time signals;
  - a retention deferred at quota did not back off;
  - a wait opened beside running compute was never retained;
  - paused executions polled for retention.
- **Round 3** found that a wait stuck at quota throttled sibling progress: a pass stopped by the transition bound reported capacity. The gate now reads committed state (`_capacity_pending`).
- **Round 4** found that a capacity pass dropped its own retry wake-up.

All of these are fixed. Both reviews approved the final content. The quality review accepted one deliberate Minor: retention defers on `authority_unavailable` and `conflict`, which can hide a database fault or a pending-count underflow. A savepoint rolls back either, and the deferral is logged.

**For the owner's acknowledgement** (marked in the plan under "Conventions fixed by Task 8"):

- the occurrence-path grammar with `#` records;
- failure records;
- wait keys;
- signal timeliness judged at acceptance;
- the capacity step order;
- an unretained wait stays open, so the 10,000 retained-wait ceiling bounds quota, not open waits;
- the epoch passed in, with the key provider as the only bounded call under the row lock;
- `pending` held until the first reservation;
- the 10,000-step target is unreachable at 1,000 items (the maximum is 9,001);
- the files beyond Task 8's ownership row.

**Obligation carried to Tasks 9 and 11:** every result, lapse, stop confirmation and reconciliation that can complete containment calls `mark_runnable` in the same transaction.

## E04 Task 9: signed activity dispatch, leases and reconciliation, 2026-10-23

Same branch, cluster and limits as Task 7. This is local evidence only: not pushed, with no hosted CI and no deployment. Hosted GitHub Actions is currently refusing jobs because of an account billing problem. The runtime distribution is now 0.2.0.

**Red before implementation.** The new tests were copied onto the Task 8 commit `8326b39`. `test_dispatch.py` failed at collection because `lotuspond_runtime.activity_protocol` did not exist. `test_activity_client.py` failed because `lotuspond_runtime.activity_client` did not exist. Each later review fix added a test that failed with the fix reverted:

- unstarted children are closed when a root ends;
- the lease is capped by the reserved active time;
- reconciliation closes children;
- a child's renewal needs its parent's live lease.

**Green:**

- The Task 9 backend tests (`test_dispatch.py`, `test_leases.py`, `test_reconciliation.py`, `test_activity_races.py`): 60 passed; with `test_budgets.py`, 92 passed.
- Runtime `test_activity_client.py`: 11 passed. The whole runtime suite has the same 121 failures as a clean Task 8 checkout, 85 of them `unsafe_storage`: the runtime's private-storage check refuses macOS by design, and CI's Linux job runs them.
- `tests/execution` with `tests/fleet`, after the first review round (E03 command, authentication, rotation and management tests; the Linux-only acceptance module excluded): 786 passed, 0 failed.
- The full backend suite at the reviewed code: `3fcdcf6`, 1,145 tests: 1,137 passed and 8 failed in 805 s (the final commit only adds the pinned race variants, run in the line above). The only failures are the 8 tests in `tests/fleet/test_acceptance.py`, which run only on CI's Linux job.
- Ruff is clean on both distributions. Runtime 0.2.0 metadata, both lock files, CI and the acceptance harness agree.
- Covered:
  - **Pins:** every persisted pin and envelope field (23 variants, each re-signed with trusted keys and refused with `conflict`).
  - **Claims:** claim once with a lost acknowledgement; expiry and revocation while a claim waits on a lock; refusal on cancellation and instance revocation.
  - **Leases:** renewal bounded by the attempt deadline, by 60 s and by the reserved active time.
  - **Retirement:** unclaimed-delivery expiry, and retirement of a spent reservation, each allowing only a new ordinal; a lapse requiring reconciliation.
  - **Results:** result and runnable marker committing together; output-schema and unknown-usage refusals; a lost result response repeatable only by its own session.
  - **Children:**
    - undeclared, wrong-kind, unregistered and mistyped arguments refused;
    - equal and conflicting repeats;
    - another session refused;
    - a parent's end closing its unstarted children and waiting for running ones;
    - a child's lease needing its parent's live lease.
  - **Polling:** a refused head never blocking a pool; a paused execution's delivery not served again.
  - **Bounds:** a stuck trust provider bounded at 5 s; a narrowed pool policy; another pool refused before any provider call; the value cap.
  - **Credentials:** sentinel runtime credentials absent from envelopes, stored rows and decrypted artifacts.
  - **Races:** real PostgreSQL races, each with both outcomes pinned:
    - claim against claim;
    - renewal against result;
    - child request against parent result;
    - claim against delivery expiry;
    - result against lease lapse;
    - renewal against E03 `revoke_instance`.

**Reviews.** Both independent reviews required changes, and both approved the final content.

- Specification review, five rounds. Its main findings:
  - children orphaned when a root ended;
  - renewal ignoring the remaining budget;
  - a timing-fragile child-lease test;
  - a spent reservation stuck until its deadline.
- Quality review, four rounds. Its main findings:
  - the root slot returned while a shared child still ran;
  - a refused head blocking the pool;
  - no recheck after child-admission provider waits;
  - a flaky race test;
  - missing race tests.
- An execution and fleet run before the reviews also caught two Task 5 regressions in child slots. Sharing a parent's slot is now an explicit opt-in.
- Accepted as Minor and carried:
  - no per-transaction keys cache in the activity paths;
  - event rows keep `source_id="orchestrator"`, with the origin recorded only in the encrypted content;
  - the runtime worker (Task 10) must apply and test `validate_lease`.

**For the owner's acknowledgement** (marked in the plan under "Conventions fixed by Task 9"):

- Lock order for runtime routes: account rows after fleet rows under the workspace lock, and the cursor taken with `NOWAIT` when late.
- Deliveries:
  - one delivery per attempt, with zero usage when it is never claimed;
  - leases and deliveries bounded by the reserved active time, with spent reservations retired by the sweeper.
- Leases: the derived lease revision and the session-bound token digest.
- Outcomes:
  - a lapse not marked runnable;
  - unknown result usage leading to reconciliation;
  - results and values accepted without the initiator's current `execute`;
  - reconciliation never producing success.
- Children: they share their parent's step instance, ordinals above 10 and the parent's compute slot; a parent cannot stop while a child may run.
- Tool naming.
- Polling order and bounds.
- The 64 KiB runtime value bound, at most 1,024 per execution.

**Obligations carried to Task 11:**

- The sweeper calls `lapse_lease`, `expire_delivery` and `expire_reservation`.
- Spent reservations need a `SweepReport` field or a recorded amendment, and an efficient due query, because v15 has no index for them.

## E04 Task 10: bounded agent and read activities in the runtime, 2026-10-23

Same branch as Tasks 7–9. This is local evidence only: not pushed, with no hosted CI and no deployment. Hosted GitHub Actions is refusing jobs because of an account billing problem. The Linux results below come from local containers, not from hosted CI.

**Red before implementation.** The five new Task 10 test files were run on the Task 9 commit `fccf5bd`, and all five failed at collection because `lotuspond_runtime.agent` did not exist.

The review fixes were checked by mutation: each fix was reverted in a scratch copy of the runtime, and its witness test was run there. All 27 mutations made their witness fail. One at first did not: the anchor-age test was passing through the wall-clock check, and it was corrected. They cover:

- child binding and targeted child polls;
- lost-claim re-claims, the persisted fence, surviving-group fencing and self-recovery;
- digest-only model rounds;
- the connector pin, every pinned secret, request and response bounds, and tool cost;
- the server-anchored clock, its anchor age and the renewal margin;
- usage that is never zero, and codes that are never retryable, after a provider may have run;
- process-group cleanup and the cancel-aware frame;
- the journal's lock, its non-blocking open and torn-record recovery.

**Green on macOS.** These runs used a dev-only shim that lets the journal accept a 0700 directory off Linux; the shim is never committed.

- The Task 10 files plus `test_activity_client.py`: 81 passed, including a 31 s model round, which no longer hits a fixed child cap. One failed: `test_mounted_secrets_resolve_pins_through_the_operator_mapping` goes through E03's resolver, which refuses macOS by design, like `test_resolver.py`.
- Backend `test_dispatch.py`, `test_leases.py`, `test_reconciliation.py` and `test_activity_races.py` on the local PostgreSQL cluster: 61 passed. That is Task 9's 60 plus the targeted-poll test.
- Ruff is clean on both distributions, and Prettier is clean on the plan.

**Local Linux evidence (not hosted CI).**

Setup:

- Docker Desktop 29.5.2, linux/arm64, with the owner's limits of 4 CPUs and 6 GB; hosted runners are amd64.
- Image `lotuspond-ci-runner:local`: Debian, Python 3.14.9, uv 0.11.16, non-root user `runner` (uid 1001). Every container ran with `--rm` and `--init`.
- The code came from a standalone clone of each commit, mounted read-only, so the harness's `git` checks work. Pytest's `--basetemp` stayed inside the container.
- Dependencies were hash-pinned PyPI wheels installed into a disposable volume, with the fleet harness's exported requirements prewarmed. The suites then ran offline.
- PostgreSQL 18.6 ran in a container on an internal network, listening only on loopback. The tests joined its network namespace.
- Cleanup was confirmed after every run. The check fails if the Docker daemon cannot be reached.

Results:

- Runtime suite at `1f564ce`: 706 passed, 0 failed, including the Linux-only state, journal, resolver and mounted-secrets tests. At `c19618e`: 695 passed.
- Full backend suite at `1f564ce`: 1,150 passed, 97 subtests, 0 failed, 0 skipped, in 16 min 18 s. At `c19618e`: 1,150 passed, 97 subtests, 0 failed, 0 skipped, in 17 min 15 s.
- The 8 fleet acceptance tests in `tests/fleet/test_acceptance.py` pass in both runs. Every earlier entry recorded them as Linux-only failures on macOS, so this also closes that gap for Tasks 7–9.
- An earlier run at the checkpoint `32f8600` failed 3 of them for harness reasons, not code:
  - the worktree's `.git` file pointed outside the mount;
  - the offline uv cache lacked the harness's requirements.
- The final source differs from `1f564ce` only in one comment and one sentence of the plan.

**Reviews.** Both independent reviews required changes, and both approved the final content.

- The specification review ran four rounds. Its main findings:
  - child deliveries not bound to what was invoked;
  - connection pins, secrets and data-handling limits not enforced;
  - frozen provider interfaces unused;
  - resolved context stored as workspace values;
  - a fenced worker still claiming;
  - lost claim acknowledgements stranded;
  - wall-clock trust;
  - untargeted child polls.

  Later rounds added Minors: the renewal margin, renewal between context reads, an unreported refused child lease, persisting the fence at detection, tool cost, the agent-output code and self-recovery.

- The quality review ran four rounds. Its main findings:
  - renewal threads clashing with the single-flight control transport;
  - a 30 s cap on every provider call;
  - process groups not always stopped;
  - transient failures turned into reconciliations;
  - truthful usage on failures;
  - zero usage after a provider was reached;
  - nothing re-anchoring the clock;
  - surviving provider groups after a crash.

**For the owner's acknowledgement** (marked in the plan under "Conventions fixed by Task 10"):

- Process model: provider children, how they are stopped, and the fence.
- Providers and connections:
  - the provider interfaces called inside the child;
  - connector-pin, secret and limit enforcement;
  - **destination enforcement deferred** to the change that installs the first real provider.
- Model rounds: digest-only rounds and remaining limits.
- Children: binding and targeted polls (a change to Task 9's client and service, with a test).
- Time and leases: the server-anchored clock with drift allowance and a 10-minute anchor age; main-thread lease keeping with a 13 s margin.
- Outcomes: the result and usage rules, and the journal phases (`finished` keeps the outcome as evidence).
- Known limitation: an unverifiable pending delivery blocks its session's polls until it expires (at most 5 minutes).

**Obligations carried to Task 12:**

- Runtime routes and their runtime-side clients:
  - the pinned records and agent declarations;
  - fetches of the attempt's input;
  - context reads under current source access;
  - the targeted poll's `parent_attempt_id` body field.
- Re-anchoring of the activity clock from the E03 supervisor's heartbeat loop.
- One activity worker per process.

## Owner-approved addition beyond E04: declining an unverifiable delivery, 2026-10-23

This is not an E04 task. The owner decided "fix outside E04" for Task 10's known limitation, so it is a separate commit on the same branch. It delivers the behaviour behind the service boundary; the HTTP route `POST /activities/decline` and its changelog item are a Task 12 obligation. This is local evidence only: not pushed, with no hosted CI and no deployment.

**Red before the change.** The new tests were run on the Task 10 commit `8f4f790`:

- Backend `test_decline.py`: 3 failed, because `LeaseService.decline_activity` did not exist.
- Runtime: 6 failed, because the client had no `decline` and the worker never declined.
- Two guard tests passed there, as they should, since they assert that nothing is declined.

**Mutations.** 13 reverted fixes, 5 in the backend and 8 in the runtime, each made their witness test fail. An unmutated control copy passed all 6 backend tests. They cover:

- **Backend:** the digest check, the pending-only rule, idempotency, the per-instance allowance and the retry class.
- **Runtime:** declining at all; declining only `command_rejected`; children; the local-state exemptions (`LocalMismatch`, `untrusted_key`); record-source refusals; the circuit breaker; its journaled count.

**Green:**

- Backend activity tests (`test_dispatch.py`, `test_leases.py`, `test_reconciliation.py`, `test_activity_races.py`, `test_decline.py`) on the local PostgreSQL cluster: 67 passed. They include:
  - decline and expiry in either order ending the attempt once;
  - a decline racing a claim with one winner;
  - the 6-per-minute allowance.
- Runtime activity tests on macOS with the dev-only shim: 92 passed, of which 78 were rerun after the last change. One failed: the mounted-secrets test, which is Linux-only by design.

**Local Linux evidence (not hosted CI).** Same container setup as the Task 10 entry, from a standalone clone of the commit.

- At the final source: runtime 717 passed.
- At the review checkpoint just before it, which differs only in counting a decline before sending it: runtime 717 passed; full backend 1,156 passed, 97 subtests, 0 failed, 0 skipped, in 17 min 26 s, including all 8 fleet acceptance tests.
- At the first review checkpoint: runtime 710 passed; backend 1,153 passed, 97 subtests, 0 failed.

**Reviews.** The specification review approved all three rounds, each with Minors:

- a stale trust set declining everything;
- the retry class;
- record-source refusals;
- cross-references;
- the wording of the narrowed limitation;
- `invalid_request`.

The quality review required changes once. Its Important finding was that a runtime broken in its own state (wrong trust, a stale identity, a wrong clock) would decline every delivery and fail its pool's work at poll rate. The fix has three layers:

- local-state refusals decline nothing;
- a journaled circuit breaker allows 3 declines in a row;
- the backend allows 6 declines a minute per instance.

After both approvals, the quality reviewer's last suggestion was applied: count a decline before sending it, so a crash never loses one. It only swaps the order of two calls, and the Linux runtime suite was run again on it.

**For the owner's acknowledgement** (in the plan's "Owner-approved addition beyond E04" section):

- A decline fails its attempt exactly as an expiry does (`timeout_before_dispatch`, zero usage).
- It requires the dispatched session and the delivery's exact digest, but not the initiator's current `execute`.
- **Trade-off:** a runtime with stale trust declines nothing, and its delivery still expires within 5 minutes.

## E04 Task 11: sweeper, controls, interpreter pins and recovery fences, 2026-10-23

Same branch as Tasks 7–10. This is local evidence only: not pushed, with no hosted CI and no deployment. Hosted GitHub Actions is still refusing jobs because of the account billing problem, so the Linux results below come from local containers, not from hosted CI.

**Red before implementation.** The five new Task 11 test files were run on `3682c39`, the commit before Task 11, and all five failed at collection. `lotuspond.execution` had no `sweeper`, `control`, `interpreters` or `projections` module.

**Time and concurrency in the tests.**

- No Task 11 test edits a deadline, lease or expiry column. Due work comes in real database time from five short-time fixtures:
  - `waits_fast`: a 2 s timer and a 3 s external wait;
  - `waits_deadline`: a 5 s deadline;
  - `activity_deadline`: a 4 s deadline;
  - `activity_backoff`: a one-hour retry back-off under a 4 s deadline;
  - `activity_budget`: a 10 microdollar budget.
- Race tests cover concurrent sweepers, the sweeper against the orchestrator, a pause against a claim, a signal against its wait's expiry, and concurrent runtime events. Each racing thread gets its own bounded fleet lookup, as a separate worker process would.

**Mutations.** All 44 reverted fixes made their witness test fail. The worker-pin mutation at first hung rather than failed: with the startup check reverted, the worker ran until stopped. Its test now stops a wrongly started worker after 5 s, so it fails, and the harness has a 10-minute timeout reported apart from caught mutations.

- **Runtime (2):** a refused renewal treated as loss, and a stop treated as a failure. Both were run in the Linux container.
- **Backend (42):** each reverts one fix in a scratch copy and runs its witness test on the local PostgreSQL cluster. They cover:
  - item independence and timers;
  - the deadline pass and its back-off rule;
  - the per-item lock timeout;
  - the restore fence and closing an inconsistent candidate;
  - controls under the execution lock, roles, cancellation finality, and the resume checks (deadline, budget, deployment, an equal repeat without grants);
  - pause release and unstarted reservations charging nothing;
  - interrupted attempts not counted, the 10 root attempts, and pause markers;
  - renewal refused while paused, and an interrupted report kept through reconciliation;
  - signals accepted while paused;
  - the program never loaded before controls, the integrity fence, waits held while fenced, and the resumed state;
  - event gaps, the gap bound, live work, and late abandoned events;
  - reconciliation's authority;
  - interpreter, compiler and worker pins.

**Green on macOS.**

- Backend `tests/execution` on the local PostgreSQL cluster (`PGTZ=UTC`) at the round 2 checkpoint: 476 passed, 1 failed. The failure was the stale budget test fixed in round 3.
- The five Task 11 files at the final source: 37 passed, `test_control.py` 18 of 18.
- Ruff is clean on both distributions, and Prettier is clean on the plan.

**Local Linux evidence (not hosted CI).** Same container setup as the Task 10 entry: a standalone clone of the commit mounted read-only, hash-pinned PyPI wheels, and PostgreSQL 18.6 on an internal network.

- Runtime suite at `cccd2cc`: 722 passed, 0 failed. The runtime is unchanged after it.
- Full backend suite at `8b5626b`: 1,193 passed, 97 subtests, 0 failed, 0 skipped, in 24 min 56 s, including all 8 fleet acceptance tests. Cleanup was confirmed.
- The final source differs from `8b5626b` only in this entry and one plan sentence: the owner's confirmation of the refund.

**Reviews.** Both independent reviews required changes for three rounds and approved the fourth.

- **Round 1** (both reviews):
  - executions whose program could not be loaded were never fenced, and blocked cancellation and the deadline;
  - the compiler-revision rule was missing from the registry;
  - pause failed in-flight work and burned reservations;
  - cancellation of claimed work always ended in reconciliation;
  - wake-ups could outlast the deadline;
  - the spent-reservation scan read every attempt;
  - tests edited time columns and had no race tests;
  - Minors: lock waits, event sources, resume checks, depths, restore candidates.
- **Round 2:**
  - repeated pauses pushed root ordinals into an agent's child range;
  - any `cancelled` report counted as a free restart;
  - never-run reservations were charged on every pause.
- **Round 3:** one stale test, plus Minors recorded for the owner: the pause marker is never cleared, the cap counts releases of never-started work, and Task 9's never-started failures keep their reserved active time.

**Owner decisions** (2026-10-23, "OK to all three, proceed"), recorded in the plan's Task 11 conventions:

1. A paused read or agent starts over on resume, and spent usage stays charged.
2. A provider stopped mid-call has unknown usage, so it needs one owner reconciliation before resume.
3. Signals are accepted while paused, and one accepted before its wait's deadline counts.

On 2026-10-24 ("OK, keep the refund"), the owner also confirmed the reservation refund. A reservation whose attempt no runtime ever claimed is released at zero once its stop is confirmed. That applies to every cancelled, unclaimed attempt the capacity step stops, not just pauses.

**Also for the owner's acknowledgement** (plan, "Conventions fixed by Task 11"):

- Pause now stops claimed work cooperatively, following the spec. Unstarted work is released, and its reservation is refunded (owner-confirmed above).
- A step has at most 10 root attempts, interrupted ones included, so 10 pauses of a queued step fail it although nothing ran.
- A pause marker counts a stop as an interruption only after a pause, and it is never cleared.
- Programs that fail verification are fenced to `reconciliation_required`. The deadline, cancellation and pause need only the execution row.
- Each interpreter version pins the compiler revision whose plans it interprets.
- The sweeper waits at most 500 ms for an item's locks, and never cuts the engine's back-off short.
- Runtime events require live work. Taking a `RuntimePrincipal` is an obligation for the first task that serves them.
- `SweepReport` gains `retired_reservations` and `restored_attempts`.

**Obligations carried to Task 12 and later:**

- The single-call fleet lookup refuses concurrent callers, so the API's wiring must account for it.
- Runtime-event routes, if any, take a `RuntimePrincipal`.
- Task 13 measures the spent-reservation scan, and repeats the restore and kill cases with real processes.

## E04 Task 12: scoped execution APIs and runtime activity routes, 2026-10-26

Same branch as Tasks 7–11. This is local evidence only: not pushed, with no hosted CI and no deployment. Hosted GitHub Actions still refuses jobs because of the account billing problem, so the Linux results below come from local containers, not hosted CI.

**Red before implementation.** The three new Task 12 test files (`test_routes.py`, `test_runtime_routes.py`, `test_boundary.py`) were run on `62856ec`, the commit before Task 12. All three failed at collection, because `lotuspond.execution` had no `boundary` module.

**What it serves.** Every route is part of the opt-in fleet API. With it off, every route answers 404.

- Customer routes under `/api/v1/projects/{project}/environments/{environment}`:
  - executable packages and their exact content;
  - deployments and their status;
  - production executions: start, list, detail, events, waits, signals, control and reconciliation;
  - context sources;
  - execution artifacts.
- Runtime routes:
  - Task 9's fixed `/api/v1/runtime/activities/...` paths, including `/decline`;
  - the worker reads under `/activity-artifacts`: the pinned records, an agent's declarations, the held attempt's input, and its pinned context.
- The runtime client's `records`, `agent`, `input` and `context` reads, and the supervisor's re-anchoring of the activity clock.

**Mutations.**

- **Backend (28):** each reverts one fix or guard in a scratch copy and runs its witness test on the local PostgreSQL cluster. All were rerun at the final source. Their subjects:
  - the request boundary: duplicated headers, the runtime session header, the declared and streamed body bounds, pages, opt-in, idempotency keys;
  - test-only service injection and audit on start;
  - the worker reads: digest, input and pinned context;
  - one pooled connection per request;
  - lock-free reads, and a write's pre-check holding no lock;
  - the runtime pre-check before the cursor, including the session check;
  - closed quota and outage codes, and separate read and write allowances;
  - the replay marker in each service and in audit;
  - serialized context registration;
  - the oversize delivery code.
- **Equivalent mutation (1):** checking `publish` instead of `manage` when creating a deployment. `create_deployment` reauthorizes `manage` itself, so editors still get 403.
- **Runtime (5), run in the Linux container:**
  - read routes missing from the transport;
  - records trusting the answer's text;
  - an unchecked input digest;
  - an unclamped agent step limit;
  - a supervisor that never re-anchors.
- **Harness errors, caught and corrected:**
  - one mutation was itself equivalent (`not x` became `None is x`);
  - one left an empty `if` block, which is a collection error, not a valid kill.

  Both were rewritten and are caught.

**Green on macOS.**

- Backend `tests/execution` on the local PostgreSQL cluster (`PGTZ=UTC`) at the round 1 fixes: 503 passed.
- Every Task 12 file at the final source: 27 (`test_routes`, `test_runtime_routes`, `test_boundary`, `test_api_concurrency`) passed.
- Ruff is clean on both distributions, and Prettier is clean on the plan.

**Local Linux evidence (not hosted CI).** The same container setup as the Task 10 entry: a standalone clone of the commit mounted read-only, hash-pinned PyPI wheels, and PostgreSQL 18.6 on an internal network.

- Runtime suite at `c9e14cc`: 727 passed, 0 failed, as the non-root `runner` user.
- Full backend suite at `c9e14cc`: 1,219 passed, 97 subtests, 1 failed, in 28 min 54 s. That includes all 8 fleet acceptance tests, and cleanup was confirmed.
  - The failure was in Task 11's `test_sweeper_items_are_independent_and_idempotent`: 2 due timers instead of 1. The test assumed a paused execution's 5 s deadline was still ahead at the first sweep. This run shared the machine with the runtime container and the mutation runs, so it was not.
  - Rerun alone five times on Linux, `test_sweeper.py` passed 9 of 9 each time.
  - The next commit removes the timing assumption from the test.
- The final source differs from `c9e14cc` only in this entry and in the plan's list of files beyond Task 12's row.

**Reviews.** Code quality required changes twice and approved round 3. The specification review approved every round, with Minors fixed or recorded.

- **Round 1, quality (Important):**
  - every request held two pooled connections, so concurrent requests could exhaust the pool;
  - unauthenticated runtime requests queued on the installation cursor;
  - reads locked the workspace row `FOR UPDATE`, so they serialized with writers and made the orchestrator skip the tenant.

  Fixes:
  - authorization in a route is a lock-free pre-check in the request's own session;
  - reads are always authorized without locks;
  - a lock-free credential and session check comes before the cursor.

- **Round 1, Minors:**
  - closed codes for quota and outage refusals;
  - an oversize delivery got a 500;
  - replays were audited twice;
  - the waits page read every step instance;
  - one allowance served both reads and writes;
  - context registration raced its replay check;
  - revocation's key was unused.

  All fixed.

- **Round 2, quality (Important):** the audit replay check searched the workspace's audit history under the service's locks. Services now mark a replay on their session instead, and nothing is looked up.
- **Round 3:** both reviews approved. Nit: `db.info` is not transactional, so a future replay check inside a savepoint must clear the marker on rollback.

**For the owner's acknowledgement** (plan, "Conventions fixed by Task 12"), including its "Open for the owner" list:

- Package publication follows Task 3's rule: editors and `write` keys publish, and only owners and admins deploy.
- A revoked knowledge source answers the runtime 503, so its agent attempt ends `unavailable`, which is retryable. The withdrawn deployment dispatches nothing new.
- Reconciliation takes no key: it is idempotent by state.
- `ExecutionServices` are injected only in test mode. Without them, routes that need a service answer 503, and database-only reads still answer.

**Obligations carried to Tasks 13 and 14:**

- Task 14's production wiring must resolve the single-call fleet lookup before the API serves real load.
- Task 13 should measure `ArtifactStore.get`'s row lock on reads.
- Runtime-reported events still have no route. Whichever task serves them takes a `RuntimePrincipal`.

## Sweeper test made independent of machine load, 2026-10-26

Test-only. Task 12's full Linux run showed `test_sweeper_items_are_independent_and_idempotent` assuming that a paused execution's 5 s deadline was still ahead at the first sweep. The test now starts that execution after the first sweep, so its deadline cannot fall due early, and the later pass still sweeps its timer while paused.

- On macOS, with a 6 s stall injected before the first sweep, the reordered test passes.
- Local Linux (not hosted CI), at this commit in the same container setup: `test_sweeper.py` was run five times and passed 9 of 9 each time. Cleanup was confirmed.
