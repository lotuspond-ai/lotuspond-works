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
