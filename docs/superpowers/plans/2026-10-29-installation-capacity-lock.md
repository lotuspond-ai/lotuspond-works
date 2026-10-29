# Installation capacity without an installation-wide lock (finding 11)

**Status: proposed 29 October 2026, for the owner's approval. Nothing below is implemented yet.**

This amends the [E04 plan](2026-10-15-durable-execution.md): Task 5's lock order, the orchestrator lock order, Task 9's activity lock order, Task 12's runtime routes and the sweeper's scheduling convention from Task 13. It is the follow-up the owner decided on 28 October 2026 for [finding 11](../../validation/2026-10-15-durable-execution.md#findings), outside E04. It also covers finding 14 (the sweeper waits for the cursor) and finding 3 (one provider lookup at a time per API process), because the cursor was hiding finding 3.

## The problem, as measured

Task 13's tier 3 calibrations (27 October) admitted 5 executions a second into 2 environments:

- each admission took 20–29 s;
- almost nothing was delivered while load ran;
- lock waits on `execution_scheduler_cursors` reached p95 6.5–8 s with one API process, and 10.3 s with three;
- PostgreSQL CPU stayed at 5–7 %.

The plan's review threshold is a cursor lock wait of p95 ≤ 100 ms at target load.

The cause is Task 5's lock order. Every admission, signal, result, decline, child request, expiry, reconciliation and capacity step takes the one installation cursor row `FOR UPDATE` first. It then holds that row for its whole transaction, provider calls included. So the whole installation runs those transactions one at a time, whatever the number of API processes.

## What the cursor protects

The cursor row (`installation_id`, `cursor`, `active`, `active_limit`) holds two pieces of shared data:

1. **The installation's active count.** At most `active_limit` (≤ 25) compute slots exist. `BudgetService.reserve_attempt` increments the count when a reservation takes a slot, and `confirm_stopped` decrements it when that slot's compute stops.
2. **The round-robin position.** Only `Scheduler.schedule_batch` reads and writes it.

Every other caller takes the row only because Task 5's order starts there. None of them reads or writes cursor data:

- admission (`reserve_admission`);
- signal acceptance;
- wait retention and release;
- `release_pending`;
- settlement;
- `release_unstarted`;
- reconciliation.

What they do change is environment-scoped: `pending`, `retained_waits`, `active` and `next_admission_sequence` on `execution_capacities`, each under that environment's row lock. Results, expiries and the capacity step change `active` on both rows.

## Design

### 1. The lock order without the cursor at its head

The order every E04 mutator follows becomes:

1. workspace row (`FOR UPDATE`);
2. the initiating principal's account rows (only while that workspace row is held, as today);
3. E03 fleet rows: pool, connections, instance, sessions, credentials, in E03's order;
4. environment capacity;
5. deployment and revision;
6. execution;
7. steps, attempts, reservations, deliveries and leases, each sorted by ID.

The installation cursor is no longer part of this order. Only a transaction that takes a compute slot takes it, and that transaction takes it first: a scheduling transaction, or a direct non-shared `reserve_attempt`. Nothing else takes it.

| Path | Today | Proposed |
| --- | --- | --- |
| Admission (`AdmissionService.admit_execution`, `reserve_admission`) | Cursor first, held to commit | No cursor. A lock-free read still refuses `capacity_unavailable` when the installation row is not provisioned |
| Signal acceptance (`InboxService.accept_signal`) | Cursor first | No cursor |
| Results, declines, child requests (`ActivityService._locked(counters=True)`) | Route takes the cursor before authentication; service takes it `NOWAIT` | No cursor. The route's lock-free credential check stays. Environment capacity is still locked before the execution |
| Expiry, reservation retirement (`LeaseService._rows(cursor=True)`) | Cursor first | Workspace first, then environment capacity |
| Reconciliation (`ReconciliationService.reconcile_activity`) | Cursor `NOWAIT` after authorization | No cursor |
| Capacity step (`InterpreterV1.apply_capacity`) | Cursor first | Workspace first |
| Stops and releases (`confirm_stopped`, `release_unstarted`, `release_wait`, `release_pending`, `settle_attempt`) | Cursor first | No cursor; only the environment counter changes |
| Scheduling (`Scheduler.schedule_batch`) | Cursor first | Cursor first (unchanged); the only regular holder |
| Non-shared `reserve_attempt` (scheduling, or a direct caller) | Cursor via `_parents` | Cursor first, before `_parents` |
| Shared child reservation (`reserve_attempt(shared=True)`) | Cursor via `_parents` | No cursor: it takes no slot |

### 2. The installation count is the sum of the environment counts

The environment counters already move with the installation counter. Every slot taken increments both, and every stop decrements both. So the authoritative installation count becomes `SUM(execution_capacities.active)`, and stops change only their environment's row.

A reservation that takes a slot holds the cursor. It then reads the sum in a later statement, and refuses with `capacity_unavailable` if the sum has reached `active_limit`. Otherwise it increments its environment counter. Why this never oversubscribes:

- **Increments are serialized.** Only slot-taking reservations increment, and each holds the cursor until it commits. While one holds it, no other increment is in progress. Under READ COMMITTED, the sum statement after the lock sees every earlier increment, since each was committed before the cursor was released.
- **Decrements can only make the check conservative.** A stop in another environment that commits concurrently is either seen or not. Either way the sum is at least the true count.
- **Each environment's own row is locked by the reserving transaction**, so its `active_limit` check is exact, as today.

`execution_scheduler_cursors.active` stays in the schema with its check constraint. Each scheduling transaction and each slot-taking reservation writes the sum into it, under the cursor. So it records the count as of the last such transaction. It is never below the true count, because only those transactions increase the count. No schema change and no migration are needed. The operations guide says the column is a record, and gives the sum as the live count.

### 3. Scheduling: one head per transaction, and never waiting on another scheduler

Without the cursor, a multi-head batch would hold every head's workspace row while it reserves each head in turn, with that head's provider calls. Each of those tenants' admissions, runtime calls and orchestrator claims would wait for the whole batch.

The proposal:

- The sweeper calls `schedule_batch(limit=1)`. Each transaction locks one tenant's rows, reserves at most one attempt and commits. Round-robin and per-environment FIFO are unchanged: the position advances past the environment just served or refused, as today.
- A pass repeats such transactions until one reserves nothing, or the pass's limit is reached. `schedule_batch` itself keeps its contract, and direct callers may still pass up to 25.
- The sweeper takes the cursor `NOWAIT` before scheduling. If another scheduler holds it, the pass skips scheduling instead of waiting. This removes finding 14's wait of up to the worker's 15 s lock timeout. Due timers, lapses and expiries are no longer delayed behind the cursor.

### 4. Provider lookups wait for their slot within their own bound (finding 3)

`execution.publication._Lookup` and `fleet.authority._LookupSlot` allow one provider call at a time per process, and refuse a concurrent caller at once with 503 (`entitlement_unavailable`, `recovery_unavailable`, `authority_unavailable` and so on). The cursor serialized admissions, results and capacity steps, so their lookups rarely collided. Without it, concurrent admissions in one API process collide on every lookup. The tier 3 runner retries only `authority_unavailable`.

The proposal: a caller waits for the slot, and the call's existing timeout (at most 5 s) bounds the wait and the call together. The rest is unchanged:

- still one call in flight per provider per process, so a stuck provider still holds only one thread;
- late answers are still discarded;
- a stuck provider still refuses every caller, after the bound instead of at once.

The plan's Task 12 note asked for this limitation to be resolved before the API serves real load.

This part can be approved or declined separately from parts 1–3. If it is declined, tier 3 will show the refusals, and the runner's retry policy decides what they cost.

## Why Task 5's guarantees still hold

- **FIFO by admission sequence.** Admission sequences come from `next_admission_sequence` under the environment row lock. That lock is held to commit, so sequences commit in order within an environment. The scheduler still takes the lowest admission sequence, then execution ID, among each environment's unreserved attempts.
- **Quota and capacity.**
  - Pending, retained waits and environment active are checked and changed under their environment row lock, as today.
  - Pool concurrency is checked and taken under the pool row lock, which every slot-taking reservation holds. A stop only lowers that count, so one that commits concurrently leaves the check conservative.
  - The installation limit holds as shown in section 2.
- **Reservations never oversubscribed.** Budget checks still run under the execution row lock with the same liability rules. Nothing in this design changes them.
- **No deadlocks.** These hold for the new order:
  - Every transaction that changes a workspace's execution records first takes that workspace row `FOR UPDATE`. The orchestrator's pure transitions and the sweeper's claim are the exception: they take the row `FOR KEY SHARE SKIP LOCKED`, take the execution row `SKIP LOCKED`, and never wait on a parent. Within a tenant, every waiting transaction is therefore serialized at its first lock.
  - Below the execution row, every transaction holds that execution row first, so rows within one execution cannot form a cycle.
  - The only transaction that spans workspaces is a scheduling transaction with a limit above 1. It takes the cursor first, so at most one runs, and then it takes all its workspace rows in sorted order before any other row.
  - User rows are shared across workspaces. Each is taken only by a transaction that already holds its workspace row. A single-tenant transaction locks at most one principal's rows, and then waits only on rows of its own tenant. So a scheduler waiting on a user row cannot be waited on in turn.
  - A non-shared reservation outside the scheduler takes the cursor before any other lock. So the cursor is always first wherever it appears, and the order stays a single total order.

  The PostgreSQL race tests below exercise these pairs.

## What stays serialized, and is measured

These are per tenant or per scheduler, not installation-wide. Tier 3 records lock waits on each of them, not only on the cursor:

- **The workspace row.** Execution authority (`authorize_execution` for writes) and E03 runtime authentication (`lock_pool`) take it `FOR UPDATE`. So a tenant's admissions, signals and runtime calls run one at a time, and the orchestrator skips a tenant's due work while the row is held (Task 7's amendment). The design does not change this; it belongs to E03's and Task 3's authority rules. If it shows up as the next bound, a separate proposal can weaken those locks to `FOR SHARE`.
- **The environment capacity row.** Held from an admission's capacity check to its commit.
- **Scheduling.** One scheduler at a time. Its throughput is one reservation transaction after another, so tier 3 records reservations per second during each load phase.

## Alternatives considered

- **Per-environment or per-workspace cursors.** These already exist as the environment capacity rows and the workspace rows. The installation limit still needs one serialization point, which section 2 keeps to slot-taking reservations.
- **The cursor taken last and briefly, applied at commit.** This keeps `execution_scheduler_cursors.active` exact. But it needs a commit hook and a delta that survives savepoint rollbacks (a transaction-local setting, for example). And every stop would still write one hot row at commit. It is more machinery for a column nothing reads.
- **Slot rows** (one row per compute slot, taken with `SKIP LOCKED`). Exact and contention-free, but it is schema v16 with a data-preserving migration, for a limit that a sum enforces already.
- **A multi-process API.** Measured on 27 October: no improvement, because the cursor was the bound.

## Tests (PostgreSQL, real concurrent connections)

New:

- While another transaction holds the cursor, an admission, a signal, a result, a decline, a child request, an expiry, a reconciliation and a capacity step each commit. Each case also asserts, from `pg_locks`, that none of them locks the cursor.
- **The installation limit under races.** Reservations in several environments race stops, with `active_limit` reached. The sum never exceeds the limit, the recorded `active` is never below the sum, and every committed stop returns its slot.
- **Deadlock races.** Each pair runs many times across two workspaces that share one user (as member and as API-key creator), and must end with no `40P01` and consistent counters:
  - a scheduling batch with limit above 1, spanning both workspaces;
  - admissions;
  - results and declines;
  - capacity steps;
  - expiries.
- **The sweeper's scheduling.** It never waits on another scheduler's cursor. It schedules one head per transaction, in round-robin order.
- **Lookups (if part 4 is approved).** A concurrent caller waits and is served; a stuck provider still refuses within the bound; a late answer is never used.

Existing tests that hold the cursor to make a transaction wait (the expired-entitlement-after-lock-wait cases, and `test_api_concurrency.py`'s account-lock cases) hold the workspace or capacity row instead. Assertions on `ExecutionSchedulerCursor.active` read the sum.

## Measurement (tier 3)

- **Calibration.** `scripts/checks/execution-load.py` with the 27 October parameters, run alone: `--rate 5 --minutes 2 --burst-minutes 1 --recovery-minutes 1 --environments 2 --runtimes-per-environment 2 --waiting 20 --sample-seconds 5`, one API process. Record:
  - cursor lock wait p50, p95 and max;
  - lock waits on workspaces and capacities;
  - admission latency from scheduled arrival;
  - deliveries and claims per load phase;
  - API and PostgreSQL CPU.
- **The planning target.** Then the full run (8 environments of 3 runtimes, 60 minutes at 5 a second, the burst, recovery and 10,000 waiting cases), recorded against every planning-target criterion.
- The runner's sampler adds lock waiters by relation (cursor, workspaces, capacities), so a queue that moves elsewhere is visible.

## Documents to update when implemented

- **The E04 plan:**
  - Task 5's lock-order sentence, the orchestrator lock order and capacity-step order;
  - Task 9's lock order and Task 12's runtime routes;
  - the Task 13 scheduling convention;
  - the thresholds table. A note in each points to this amendment; approved task text is not rewritten.
- **ADR-0011.** The scaling-risk consequence.
- **Module docstrings** in `budgets`, `scheduler`, `admission`, `dispatch`, `leases`, `reconciliation`, `orchestrator`, `progression`, `runtime_routes`, `routes` and `activity_records`.
- **`docs/operations/durable-execution.md`:**
  - the lock order;
  - the cursor row's `active` as a record;
  - the orchestrator's grants, re-measured with the privilege log, since it no longer locks the cursor;
  - the runbook line.
- **The validation record:** finding 11 closed with evidence, finding 14 resolved, finding 3 updated, and `docs/VALIDATION.md`.

## Decisions for the owner

1. **Approve parts 1–3:** the lock order without the cursor, the installation count as the sum of environment counts, and scheduling one head per transaction with a non-waiting scheduler.
2. **Approve or decline part 4:** provider lookups wait for their slot within their bound, in the execution services and in E03's fleet recovery lookup.
