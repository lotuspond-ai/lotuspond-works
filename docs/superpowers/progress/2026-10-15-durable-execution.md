# Durable execution implementation evidence (E04)

The approved [E04 plan](../plans/2026-10-15-durable-execution.md) is implemented on two branches:

- **Tasks 1–6** are on `codex/durable-execution`, merged to `main` in PR #11. They were completed before Revision 2; the plan's "Temporal assumptions in completed Tasks 1–6" section records how they carry over.
- **Tasks 7–14** are on `claude/e04-postgresql-impl`, under Revision 2 (PostgreSQL engine, [ADR-0011](../../architecture/0011-postgresql-durable-execution.md); plan PR #13, approved 22 October 2026).

Completion of this foundation does not establish production agent execution or production qualification. Each task's red and green evidence, reviews and limits are in [VALIDATION.md](../../VALIDATION.md) under its own heading. Task 13's qualification record is [the validation document](../../validation/2026-10-15-durable-execution.md). This page indexes them.

| Task | Commit | Delivers |
| --- | --- | --- |
| 1 | `0c7c8ac` | Separately signed read and model activity contract (SDK 0.3.0) |
| 2 | `1b14020` | Scoped durable execution records, additive schema v14 |
| 3 | `399379a` | Admission of exact v2 packages and deployment revisions under current release authority |
| 4 | `a271b4a` | Encrypted execution artifacts and current knowledge access |
| 5 | `86a6547` | Entitlement, shared usage and fair bounded capacity |
| 6 | `02ceb75`, `7bbb92d` | Transactional, idempotent admission and signal delivery |
| 7 | `77959d9` | PostgreSQL orchestration claim, schema v15 and the worker, behind the engine adapter |
| 8 | `8326b39` | Deterministic interpreter v1 as row transitions |
| 9 | `fccf5bd` | Signed activity dispatch, leases and historical reconciliation |
| 10 | `8f4f790` | Bounded agent and read activities in the runtime |
| Beyond E04 | `3682c39` | A runtime declines a delivery it cannot verify (owner-approved, 23 October) |
| 11 | `62856ec` | Sweeper, controls, interpreter pins and recovery fences |
| 12 | `63732a0`, `5e1ccd1` | Scoped execution APIs and runtime activity routes |
| Owner change | `df94536` | Every never-claimed reservation is released at zero (26 October) |
| 13 | `d707bd4` | Conformance tiers 1–3, installed acceptance and the qualification record |
| Owner change | `0b5cd42` | Every restored execution is held until its owner releases it (28 October) |
| 14 | this commit | Packaging, CI jobs, the operator guide and final review |

## Open for the owner

These are recorded with their evidence in the validation document and the plan's conventions blocks:

- **RESTORE-1 (decided, 28 October).** A restore ran an activity again if it was claimed after the backup. The owner chose "Hold all after restore": every restored execution is held for reconciliation until its owner releases it, once the runtime journals are reconciled. That is implemented, and RESTORE-1 is green. The operator procedure is in the operations guide. What stays open is how to bring a journal's evidence back, below.
- **No way to reconcile an uncertain attempt.** No route or runtime report carries the journal's evidence of stop and usage. So a lapsed or restore-fenced attempt holds its execution's slot and liability, and five such attempts exhaust an environment's default limit. A held attempt that a journal shows ran after a backup can only be cancelled, and its usage goes uncharged.
- **Signal refused for an open wait.** A listed-open wait refuses its signal (403) while its execution is still `pending`.
- **Cursor serialization (diagnosed, 28 October; a follow-up redesign).** The installation-wide scheduler cursor serializes the engine under load. At the planning target, one API process held a core at 87 % for the hour, and the engine delivered 20 attempts, with cursor waits at p95 5.0 s. The owner asked for a diagnosis first. With three API processes, none saturated, it starved the same way: cursor waits p95 10.3 s, and 10 deliveries during the load. So the bound is the cursor, held for each admission's whole transaction, not the API process. Redesigning Task 5's serialization is a follow-up outside E04.
- **Runtime exits under load.** Runtime 0.2.0 ends its process on a refused or slow (over 10 s) control call and relies on a service manager to restart it. Under tier 3's load every runtime exited, again and again.
- **One authority lookup.** The API's single in-flight authority lookup refuses concurrent callers (Task 12).
- **Scheduling fails closed on a database refusal.** A missing grant, or any database error, while authorizing one head aborts the whole scheduling batch, so nothing is scheduled anywhere until it is fixed.
- **One stalled signal after an immediate restart (cause not established).** In Task 14's final tier 2 run, a signal accepted after a PostgreSQL restart was not applied for 120 s. It happened once in 38 runs of the case, and the run's database was gone. The case now records the rows that would show the cause (validation finding 24).
- **The sweeper's cursor wait.** Since the scheduling amendment, a sweep pass can start up to the worker's lock timeout (15 s) late under contention.
- **Task 12 items.**
  - Editors may publish packages.
  - A revoked knowledge source answers 503.
  - Reconciliation takes no key.
- **Residual restore behaviours.**
  - Revoking a deployment's active revision blocks admissions to its older revisions too.
  - The tamper fence skips `pending` executions.

## Not established

- Real providers, the actual customer workflow, signed commercial issuance, and E05/E06 effects and outcomes.
- The E07 builder and operations experience, enterprise and topology evidence, HA, RPO and RTO.
- Hosted CI: GitHub Actions has refused jobs for this repository since 23 October 2026, so every run here is local Linux evidence.
