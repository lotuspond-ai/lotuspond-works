# Connections and runtime identity implementation evidence

The approved [E03 plan](../plans/2026-10-14-connections-fleet.md) is being implemented on `codex/connections-fleet`, separately from the preserved design branch. Completion of this foundation does not establish production agent execution or production qualification.

## Task 1 — strict protocol and runtime distribution

Implementation `1372dc187047fe2bf4fcc30657f8cdb39e4fcbf0` and correction `96163e01a603fd1db9e60e2fdffcf49c41f839a1` passed independent specification and code-quality reviews after one correction round, with no open findings. The corrections reject native adapter JSON parsing, per-call relaxation of unknown-field checks, and malformed existing secret wrappers. Supported JSON callers use the bounded decoder followed by Python validation or the bounded class entry point.

- Runtime/protocol and external direct-wheel/sdist-wheel tests: **313 passed, zero skips** on each of Python **3.13.13** and **3.14.5**, at the corrected source. Installed consumers use `python -I` outside the checkout and independently built SDK artifacts.
- Both interpreter runs passed runtime/backend Ruff and three backend SDK compatibility tests. Frozen backend installation and dependency compatibility passed. SDK source, public contracts and canonical fixtures remain unchanged.
- The earlier full root check passed with the existing local process-reaping and serial Node-test harness: Node 105, backend 300 plus 97 subtests, SDK 1,472 plus 68 subtests, and runtime 165. Backend had 39 database-related skips. The raw root run had two runner process-cleanup failures. This broader run preceded the correction and is not fresh corrected-head evidence; Task 9 requires full final checks.
- Exact source hashes and retained artifact hashes were independently compared by both closure reviewers. Runtime direct and rebuilt wheel SHA256: `a7b99eb134893d5a7e8c27055e5077f2900593b1640439c103548e9d99662ead`; runtime sdist: `8dc826c0417e57d106b54c8c92504aac205dc517156066bd14be6748ed60eddd`; SDK wheel: `ed5aeaa0fd4cc417eaa4a9ba69627e5dccb7c9e000e4716397a4d2ea5e216118`.

Public Pydantic deferred schema construction preserves inert imports; mapped IPv6 protocol text uses stable hexadecimal rendering across the supported interpreters. These choices have regression coverage. Actual destination enforcement, secure persistence, route privacy controls, database integrity/races, the complete installed journey, API-image verification and hosted final-head CI remain with their subsequent tasks.

Detailed red/green logs, artifact receipts, task reports and independent review reports are preserved in the plan's ignored local SDD workspace. They are not shipped package contents. No production database, deployment or additional merge occurred.

## Task 2 — scoped records and additive schema v13

Implementation `70371a6f81d01bf38bb62b6cb5d94f791c3eb0d2`, entry-point correction `d79518d43e837c0a7dee41587952f1313cffdbd9`, and review correction `3587ae9f1ca7a750e22084cfd424f178a39e1611` passed independent specification and code-quality review with no open findings. Thirteen fleet tables bind workspace/project/environment and machine identity, normalize secret and policy references, enforce credential/session uniqueness, and retain command replay records. Existing environment configuration and application records are preserved.

The additive migration validates the frozen v12 schema before creating fleet objects and writes version 13 last in the caller's transaction. It refuses altered CHECK literals, disabled foreign-key enforcement, changed index definitions, malformed ledgers and other tested partial/corrupt states without repairing or deleting existing data. Exported SQL now emits the named current-policy foreign key after table creation; tests execute that SQL and compare it with ORM-created and upgraded schemas.

- At the reviewed correction: **133 affected PostgreSQL tests passed, zero skips**, including 49 fleet cases; backend Ruff, schema snapshot and diff checks passed. Correction red tests reproduced 18 failures before the fix, then the focused set passed 23 cases.
- Before the two narrow corrections, the full backend suite passed **361 tests and 97 subtests with zero skips** on independently verified localhost PostgreSQL. The root check passed through the existing local harness: Node 105; backend SQLite 309 plus 97 subtests, with 52 skips; SDK 1,472 plus 68 subtests; runtime 313; lint/contracts/build passed. Those broader receipts belong to the earlier implementation source; Task 9 will run final checks again.
- Frozen v12 SQL exactly matches the committed baseline, SHA256 `424051bc108443e4c7ad9561e63ecbe908266339f5057166afbd6696ad3aa142`. Preservation tests compare every legacy table and populated environment, evaluation, MFA and registry values; injected DDL failures verify complete rollback. No runtime authority is backfilled.

Earlier migration-chain tests reconstruct prior schemas from the current model. Immutable historical v2–v11 deployment snapshots are unavailable; the checked shapes are qualified locally, and unknown historical/customized shapes are conservatively refused. One rewind fixture was corrected to remove a later incident constraint before reconstructing v5. Real historical variants require explicit review. Recovery is forward repair, with no destructive downgrade; production migration roles, lock duration and operational qualification remain later work.

Publication immutability, current authorization, terminal revocation, canonical-document/link consistency and safe service DTOs remain with the subsequent service tasks. This record qualifies the database foundation, not production execution.


## Task 3 — scoped connection and pool administration

Implementation `5127fe0cc888e30ab163b1a9e505ddb217cf4541` and correction `0ccc7f4e33b11a491dc01fde36c5ab773a9ff7e5` passed independent specification and code-quality reviews with no open findings. Sixteen management routes require eligible owner/admin customer sessions, preserve current verification/MFA rules, and provide immutable connection/pool revisions, opaque secret-reference metadata, bounded inventories and closed audit fields. Fleet defaults to disabled, and the production connector catalog grants nothing.

Publication and request-key results share the caller transaction. Full-scope parent/reference checks, terminal revocation and canonical-document/link integrity apply before use. The independent quality review found that a caller Session could retain old active status after another transaction revoked a row; the correction refreshes current attributes in the three locking reads. PostgreSQL regressions cover workspace, connection, pool and secret revocation, terminal transitions, replay, and a stale current-policy pointer. Two narrowly scoped parent/ID indexes support bounded revision inventories.

- At the reviewed correction, **68 management PostgreSQL tests passed, zero skips**, including eight new regressions that first failed; backend Ruff and diff checks passed. Both closure reviewers verified the final source and evidence manifests.
- The prior broader PostgreSQL gate passed **246 tests, zero skips**, across management/schema, existing authentication/MFA, projects/evaluation and registry. It preceded the isolated UTC timestamp correction and later lock-refresh fix; the final affected gate covers those changes. The runtime protocol gate passed **328 tests, zero skips**, at unchanged current runtime bytes. Overlapping counts are not summed.
- A non-UTC timestamp conversion issue was reproduced and corrected before independent review. The only remaining test warning is the existing upstream Starlette/AnyIO deprecation.

Enrollment, machine sessions, rotation and signed command admission remain subsequent tasks. Final installed artifacts must be rebuilt because protocol additions supersede Task 1's artifact bytes. Full root checks, API-image build and hosted CI remain Task 9; local administration evidence does not establish production readiness.


## Task 4 — enrollment, runtime identity and process sessions

Implementation `2df4dac5e356a16c806a70615ff0e20684acbdb9`, lock-order correction `9e79f77e38d7753d2318692667475bf8783ddddb` and review correction `a6e37da70e9b834aefe4aaf761e30f6989eef007` passed independent specification and code-quality reviews with no open findings. One-use enrollment creates digest-only runtime credentials; process sessions prevent copied credentials from displacing a live process. Current workspace/pool policy, independent recovery authority, fresh database time and scoped refreshed locks govern acquisition, heartbeat and revocation. Runtime principals remain separate from human authentication. Production recovery and connector defaults grant no authority.

Clock-fence denials roll back authority changes before a bounded audit-only transaction records the fixed event. Recovery lookups have one bounded in-flight slot and deny on timeout or unavailable authority. Three narrow fleet indexes support enrollment inventories, live-enrollment quotas and active-instance queries; index usability tests do not establish production load capacity.

Independent review found two Important defects: session acquisition could decide expiry before waiting for a pending-credential lock, and synchronous quota I/O could block the ASGI event loop. The correction performs final authority/time/expiry checks after that lock and before mutation, and awaits quota checks through the existing bounded worker facility. Five regressions first failed and then passed, covering changed epoch, expired credential, valid delayed acquisition, and unrelated request progress during blocked peer/token quotas.

- At the final correction, **75 affected PostgreSQL tests passed, zero skips**; backend Ruff and diff checks passed. Both closure reviewers checked the final source manifest and retained receipts.
- The preceding all-fleet gate passed **187 tests, zero skips**, at `9e79f77`; the protocol gate passed **335 tests, zero skips**, at unchanged runtime source. A broader earlier 317-test PostgreSQL receipt predates later identity corrections. These overlapping counts are not summed or attributed to the final correction.
- A restricted local execution run exhibited worker-wakeup timeouts; the same source passed with the documented execution permission. The environment cause is not proven. Failed receipts remain preserved, and no product workaround was added.

Credential rotation, the runtime client and signed command admission remain subsequent tasks. Production recovery/Redis integrations, resource and outage qualification, rebuilt installed artifacts, the full root check, API-image verification and hosted CI remain later gates. This local identity evidence is not a production-readiness claim.


## Task 5 — two-phase credential rotation

Implementation `a0adc8f534b30914bfb4ffc84a1f9960bedfa494` passed independent specification and code-quality reviews on the first review, with no findings. A worker prepares a pending successor while its current credential retains its original expiry, persists both locally in the next task, and activates the successor using the same live process session. Activation checks the exact preparation policy revision and current authority after lock/quota waits, then atomically revokes the predecessor and sets a fixed fifteen-minute expiry.

A separate active-successor retry returns safe state after a lost response without requiring the old credential or session memory, extending expiry, granting a session, changing audit history, or disturbing a newer pending successor. Cancellation affects only a still-pending reciprocal successor; takeover and instance revocation invalidate pending authority. Credential versions never repeat. Three fixed audit events contain only IDs and versions, and one shared rotation quota permits ten requests per minute per instance.

- **192 affected PostgreSQL tests passed, zero skips**, covering rotation, enrollment, sessions, runtime authentication and recovery authority; backend/runtime Ruff and diff checks passed. Both reviewers verified all six source hashes and retained evidence hashes.
- Independent-connection races and held-row tests cover overlapping transitions, expiry or epoch changes during waits, and rollback after intermediate writes. Self-review reproduced a missing explicit scope predicate and corrected it before the final run. Earlier 55- and 112-test receipts are successive, overlapping runs and are not additional totals.
- Tests use synthetic credentials and deterministic clocks with real PostgreSQL transactions. They do not exhaust all scheduler interleavings or qualify a production recovery/Redis deployment. No reviewer repeated already-passing suites solely for reassurance.

Worker file durability, restart recovery and late responses remain Task 6; command admission remains Task 8. The consolidated API changelog and final installed/root/image/hosted checks remain Task 9. This establishes locally reviewed server rotation behavior, not production business execution.
