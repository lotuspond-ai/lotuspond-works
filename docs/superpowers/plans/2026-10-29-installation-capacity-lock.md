# Installation capacity without an installation-wide lock (finding 11)

**Status: approved 29 October 2026, with one amendment by the owner: the installation cursor is taken last, not first. Part 4 (lookups wait for their slot) is approved as proposed.**

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

### 1. The lock order: the cursor leaves the head and becomes a leaf

The order every E04 mutator follows becomes:

1. workspace row (`FOR UPDATE`);
2. the initiating principal's account rows (only while that workspace row is held, as today);
3. E03 fleet rows: pool, connections, instance, sessions, credentials, in E03's order;
4. environment capacity;
5. deployment and revision;
6. execution;
7. steps, attempts, reservations, deliveries and leases, each sorted by ID;
8. **the installation cursor, last.** It is taken only by a transaction that takes a compute slot or moves the round-robin position.

**The leaf rule.** A transaction takes the cursor last. Before it, it has done all of the following:

- its authorization and provider lookups;
- its budget and capacity checks;
- every other row lock;
- a flush of its writes.

After the cursor, it runs only these statements, then commits:

- the sum of the environment counts (section 2);
- the write of `active` and, when scheduling, of the round-robin position.

It never waits on another lock while it holds the cursor. So the cursor cannot be part of a deadlock cycle. A cycle needs every member to be waiting, and the holder never is. The cursor is held for a few statements and a commit, not for a transaction.

| Path | Today | Proposed |
| --- | --- | --- |
| Admission (`AdmissionService.admit_execution`, `reserve_admission`) | Cursor first, held to commit | No cursor. A lock-free read still refuses `capacity_unavailable` when the installation row is not provisioned |
| Signal acceptance (`InboxService.accept_signal`) | Cursor first | No cursor |
| Results, declines, child requests (`ActivityService._locked(counters=True)`) | Route takes the cursor before authentication; service takes it `NOWAIT` | No cursor. The route's lock-free credential check stays. Environment capacity is still locked before the execution |
| Expiry, reservation retirement (`LeaseService._rows(cursor=True)`) | Cursor first | Workspace first, then environment capacity |
| Reconciliation (`ReconciliationService.reconcile_activity`) | Cursor `NOWAIT` after authorization | No cursor |
| Capacity step (`InterpreterV1.apply_capacity`) | Cursor first | Workspace first |
| Stops and releases (`confirm_stopped`, `release_unstarted`, `release_wait`, `release_pending`, `settle_attempt`) | Cursor first | No cursor; only the environment counter changes |
| Non-shared `reserve_attempt` (scheduling, or a direct caller) | Cursor first, held through lookups (about 0.6 s per reservation in tier 1) | Cursor last, after every lookup and lock, for the sum check, the increment's record and commit |
| Scheduling (`Scheduler.schedule_batch`) | Cursor first, held for the batch | One head per transaction. Position read without a lock; cursor last, to write the position and the count |
| Shared child reservation (`reserve_attempt(shared=True)`) | Cursor via `_parents` | No cursor: it takes no slot |

### 2. The installation count is the sum of the environment counts

The environment counters already move with the installation counter. Every slot taken increments both, and every stop decrements both. So the authoritative installation count becomes `SUM(execution_capacities.active)`, and stops change only their environment's row.

A slot-taking reservation does all its work and increments its own environment's `active` under that environment's row lock, and flushes. Only then does it take the cursor. It reads the sum in a later statement, which includes its own uncommitted increment. If the sum exceeds `active_limit`, it refuses with `capacity_unavailable`. The refusal rolls back the reservation, its savepoint or transaction, and the increment, and the work already done is wasted, which the owner accepted. Otherwise it writes the sum into `execution_scheduler_cursors.active` and commits.

Why this never oversubscribes:

- **Every increment is validated by its own transaction under the cursor.** No reservation can commit an increment without first holding the cursor through its sum check.
- **Checks never interleave.** Each holder keeps the cursor from its sum read to its commit. While it does, no other increment can pass its check or commit.
- **The next holder sees what the last one committed.** It reads the sum only after acquiring the cursor. So under READ COMMITTED its statement sees every increment committed before. Its own uncommitted increment is included too.
- **Increments not yet validated are each validated later.** An increment another transaction has made but not validated is invisible to the current holder. That transaction validates it itself after the holder commits. So each committed state satisfies sum ≤ `active_limit`.
- **Decrements only make the check conservative.** A stop in another environment that commits concurrently is either seen or not. Either way the sum is at least the true count. A stop in the same environment waits on that environment's row.
- **The environment check is exact.** Each environment's own `active_limit` check runs under its row lock, as today.

To avoid wasted work, a reservation first reads the sum without any lock. If the installation is visibly full, it refuses before its provider lookups.

`execution_scheduler_cursors.active` stays in the schema with its check constraint. Every holder writes the sum into it at the leaf. So it records the count as of the last reservation or scheduling transaction, and is never below the true count, since only those transactions increase it. No schema change and no migration are needed. The operations guide says the column is a record, and gives the sum as the live count.

### 3. Scheduling: one head per transaction

A scheduling transaction holds the rows of each head it reserves. Under the leaf rule it may not lock another head's rows after taking the cursor for the first. So no scheduling transaction spans heads:

- **`Scheduler.schedule_batch(db, installation_id, limit=1, *, epoch)` reserves at most one head, for every caller.** Its `limit` must be 1, and any other value is refused with `invalid_request`. A caller that wants more runs more transactions; none silently gets fewer than it asked for. This changes Task 5's frozen "batches ≤ 25" contract. The plan records the change, and the tests that passed larger limits loop instead.
- **Fairness is unchanged.** The transaction reads the round-robin position and the candidate heads without locks. It reserves the first eligible head, and writes the next position at the leaf. Round-robin among environments and FIFO within one are as today. The position advances past the environment just served or refused.
- **The sweeper loops.** Each one-head transaction is its own, under the fence, until one reserves nothing or the pass's limit is reached.
- **The sweeper's lock-free checks.** It schedules only when an attempt awaits a reservation and the installation is not visibly full. Both are read without locks, so a saturated backlog no longer makes every pass take the cursor (finding 14). There is no `NOWAIT` skip: the cursor is a leaf held for a few statements, so waiting for it is brief and safe.
- **A second sweeper is safe but redundant.** Two schedulers may select the same head. The second then waits for that head's rows and finds it already reserved. Its write of the position may also overwrite the first's, so fairness becomes approximate across two sweepers. The operations guide keeps one sweeper per installation.

### 4. Provider lookups wait for their slot within their own bound (finding 3; approved)

`execution.publication._Lookup` and `fleet.authority._LookupSlot` allow one provider call at a time per process. They refuse a concurrent caller at once with 503: `entitlement_unavailable`, `recovery_unavailable`, `authority_unavailable` and so on. The cursor serialized admissions, results and capacity steps, so their lookups rarely collided. Without it, concurrent admissions in one API process collide on every lookup.

A caller now waits for the slot. The call's existing timeout (at most 5 s) bounds the wait and the call together: the call gets only what remains after the wait, and if nothing remains, it releases the slot and refuses. The rest is unchanged:

- still one call in flight per provider per process;
- late answers are still discarded;
- a stuck provider holds one thread and its slot. Every caller is refused within its own bound, never later.

### 5. Pure validation is memoized per process (approved 29 October 2026, after the first calibration)

**Why.** With the cursor gone, the 29 October calibrations moved the queue to each tenant's workspace row. Every write in a tenant locks it `FOR UPDATE`, and an admission held it while it spent about 0.31 s of request-thread CPU.

A single-threaded cProfile of the API showed what that CPU was. About two thirds of it was the SDK re-validating and re-canonicalizing the same immutable stored bytes: JSON-domain checks, JCS (`rfc8785`) and pydantic. Per admission there were about:

- 6 `load_publication` calls;
- 6 plan compilations;
- 6 signature verifications;
- 34 package validations;
- 20 catalog validations.

SQL was about 14 %, and backend code under 1 %.

**What.** `execution/memo.py` keeps the results of pure SDK functions per process in one bounded LRU of 512 entries:

- package and catalog validation;
- canonical package and catalog bytes;
- plan compilation with its canonical plan bytes;
- package signature verification;
- the budget service's step list.

Each entry is keyed by SHA-256 of the exact inputs, serialized with `marshal` format 2. That format has no object references and decodes back to the same structure, so two different inputs can never share a key. Tuple and list, int and bool, and str and bytes stay distinct. An input `marshal` cannot encode is not memoized. A signature verdict is keyed by the package, the envelope and the exact trust set as dumped, and only a `valid` verdict is kept. Signature verification reads no clock, so a revoked or replaced key changes the trust set and misses the cache.

**Rules.**

- Only successes are kept. A refusal raises before anything is stored, and is recomputed every time.
- Cached models are never shared: every caller gets its own deep copy (about 0.05 ms, against about 2.8 ms to validate the tier 3 package).
- Nothing here is authority. Every authority input is still read or fetched on every call, and after each provider wait as before (Review Focus 2):
  - rows and their status, digests and dependency links;
  - the current trust snapshot, its expiry and catalog membership;
  - grants, tenant policy, recovery and assurance.
- The cache lives in process memory only, is dropped on restart, and returns nothing a caller did not supply the bytes for.

### 6. After the planning-target run: admission looks up first; an expired or refused head no longer stalls scheduling (approved 29 October 2026)

The planning-target run (full1, 3 API processes) met the cursor criterion (wait p95 0.96 ms) but delivered work only for its first 24.5 minutes. Three causes were measured:

1. **The single sweeper reserved about 3 attempts a second,** about 330 ms per one-head transaction, mostly waiting on tenant `workspaces` rows. Those rows are held by admissions, at 8–10 waiters on average.
2. **Expired heads.** Once queue age reached the 1,000 s deadline, every head arrived expired. `reserve_attempt` refused it `forbidden`, which the scheduler recorded as `authority_revoked`.
3. **The pass ended at the first refusal.** `_schedule` stopped at the first transaction that reserved nothing, so one refused head stopped every environment's scheduling.

Approved changes:

- **Admission looks up before its first lock** (`AdmissionService._lookups`). The publisher snapshot, the grants (recovery, entitlement, tenant policy) and the artifact key are looked up before `reauthorize`.
  - Under the locks, each is checked exactly as one looked up there would be (`load_deployment(snapshot=...)`, `_expiry`, `ArtifactStore.put(key=...)`).
  - The pending-capacity step runs on the parents and limits already held (`_count_admission`), instead of `reserve_admission` loading them all again.
  - The final guard (`_final`) is unchanged: it repeats every lookup and compares, so authority is current at the end as before.
  - Under the workspace row, an admission now loads the deployment twice instead of four times, and makes the provider round twice instead of four times.
  - A lookup that refuses early is left out and repeated at its usual place, so nothing is disclosed before authorization.
  - The request is copied before the first provider call, as before.
- **An execution past its deadline is not a head.** `schedule_batch` and the sweeper's plain pre-check pass over it, and the sweeper fails it. A head that expires between being chosen and being reserved is labelled `deadline_exceeded`.
- **A pass gives every environment a turn.** `_schedule` repeats transactions until `limit` reservations, or until as many transactions in a row as there are environments reserve nothing.

Not changed: one sweeper per installation, one head per transaction, and the cursor as a leaf.

### 7. After the diagnostic run: a head whose step no longer fits before its deadline fails at once (approved 30 October 2026)

The 32-minute diagnostic on 8aa0ff9 (`diag1`, 30 October) showed why goodput still fell to zero once queue age neared the deadline. Part 6 was not enough.

1. **Each step reserves its active time.** In the load world that is 30 s (`active_ms`), against a 1,000 s deadline. `reserve_attempt` refuses a step that no longer fits before the deadline, as `budget_exceeded`.
2. **A refused head stays the head.** It is not past its deadline, so part 6 does not skip it, and FIFO blocks its environment until it expires. The next head is in the same position.
3. **So nothing is ever reserved again.** From minute 18 on, every head had less than 30 s left. Three scheduling probes each refused a live head that still had 0.5–1.9 s left. The capacity counters were exact throughout, and the installation never had more than 12 of 25 slots active.

Approved change:

- **An execution that can never run fails at once, instead of being refused on every turn.**
  - `schedule_batch` looks up demands before its first lock, for up to `FRONT` (25) candidates from one environment's front, in FIFO order.
  - Each one whose step no longer fits before its deadline (`_out_of_time`) is retired. Time only shrinks and an attempt's demand is its own, so it can never be reserved. No program step proceeds past a step that never runs, so the execution can only fail.
  - A retired execution is locked with the head's rows, in the same order. It carries reason `deadline_exceeded` and is marked due now.
  - The candidate query and the sweeper's plain pre-check never return it again.
  - Progression treats it as at its deadline (`orchestrator.past_deadline`), so the next orchestrator pass fails it with `deadline_exceeded`, as it would at the deadline, instead of when the deadline comes. Its pending place is freed, and its client learns the outcome, at once. In production a step may reserve far more than 30 s.
  - The first candidate that fits is the head, and is reserved with the demand already looked up.
  - A head that runs out of time between the lookup and its reservation is retired the same way.
- **Retiring is cheap enough to keep up.** Under overload, executions run out of time at about the arrival rate minus the service rate, about 4 a second in the diagnostic. Retiring one per transaction would never reach a head that can still run, because the sweeper makes only about 1.4 transactions a second there. Up to 25 per transaction can keep up.
- **The scan is bounded by time.** If lookups run slow, a transaction that has already retired some stops scanning after the provider timeout, and the next turn goes on.

Not changed: FIFO among executions that can still run; one environment's rows per transaction; the cursor as a leaf. A transaction that only retires still counts as one that reserved nothing, so a pass stays bounded.

Not decided here: whether admission should refuse work an environment's queue cannot serve before its deadline.

## Why Task 5's guarantees still hold

- **FIFO by admission sequence.** Admission sequences come from `next_admission_sequence` under the environment row lock. That lock is held to commit, so sequences commit in order within an environment. The scheduler still takes the lowest admission sequence, then execution ID, among each environment's unreserved attempts that can still run (parts 6 and 7: not past their deadline, and not retired as out of time).
- **Quota and capacity.**
  - Pending, retained waits and environment active are checked and changed under their environment row lock, as today.
  - Pool concurrency is checked and taken under the pool row lock, which every slot-taking reservation holds. A stop only lowers that count, so one that commits concurrently leaves the check conservative.
  - The installation limit holds as shown in section 2.
- **Reservations never oversubscribed.** Budget checks still run under the execution row lock with the same liability rules. Nothing here changes them.
- **No deadlocks.** These hold for the new order:
  1. **The cursor is a leaf.** Its holder never waits, so no cycle passes through it.
  2. **Every other execution write transaction is confined to one workspace.** It takes that workspace row `FOR UPDATE` before any other row of it. Its other locks are:
     - the account rows of exactly one principal (the initiator), taken after the workspace row;
     - rows of that workspace only.

     Scheduling now reserves one head per transaction, so nothing spans workspaces. Within a workspace, every waiting transaction is serialized at its first lock.
  3. **Below the execution row,** every transaction holds that execution row first.
  4. **Orchestrator claims never wait on a parent.** The orchestrator's pure transitions and the sweeper's claims take the workspace row `FOR KEY SHARE SKIP LOCKED` and the execution row `SKIP LOCKED`.
  5. **Shared user rows close no cycle.** User rows are the only rows shared across workspaces. Suppose transaction A holds workspace W1 and waits for user U, held by B in workspace W2. B waits only on rows of W2 or on the cursor. W2's rows are held by no other transaction of this kind, because B holds W2 `FOR UPDATE`. The cursor's holder never waits.

  So no cycle forms among execution transactions. Transactions outside execution (account lifecycle, assurance, fleet management) never took the cursor, so their interaction with execution is as it was. The PostgreSQL race tests below exercise these pairs.

## What stays serialized, and is measured

These are per tenant, not installation-wide. Tier 3 records lock waits on each of them, and on the cursor:

- **The workspace row.** Execution authority (`authorize_execution` for writes) and E03 runtime authentication (`lock_pool`) take it `FOR UPDATE`. So a tenant's admissions, signals, scheduling and runtime calls run one at a time, and the orchestrator skips a tenant's due work while the row is held (Task 7's amendment). The design does not change this; it belongs to E03's and Task 3's authority rules. If it shows up as the next bound, a separate proposal can weaken those locks to `FOR SHARE`.
- **The environment capacity row.** Held from an admission's capacity check to its commit.
- **The cursor.** Held only at the leaf. Tier 3 records both how long acquisitions wait and how long the cursor is held.

## Alternatives considered

- **The cursor first, only for slot-taking reservations** (the first version of this proposal). It removes the cursor from every other path. But a reservation would still hold it through its provider lookups: about 0.6 s per reservation in tier 1, so about 1–2 slots a second for the whole installation, below the planning target. Rejected by the owner.
- **Per-environment or per-workspace cursors.** These already exist as the environment capacity rows and the workspace rows. The installation limit still needs one serialization point, which section 2 keeps to the leaf.
- **A commit-time counter.** Every transaction would apply deltas to the cursor in a commit hook. This keeps `execution_scheduler_cursors.active` exact on every stop. But it needs a delta that survives savepoint rollbacks, and every stop would still write one hot row. That is more machinery for a column nothing reads.
- **Slot rows** (one row per compute slot, taken with `SKIP LOCKED`). Exact and contention-free, but it needs schema v16 and a data-preserving migration, for a limit the sum already enforces.
- **A multi-process API.** Measured on 27 October: no improvement, because the cursor was the bound.

## Tests (PostgreSQL, real concurrent connections)

New:

- **The leaf, from `pg_locks`.** A slot-taking reservation and a scheduling transaction are each paused just after acquiring the cursor, and again just before commit. From another connection:
  - none of the transaction's locks is waiting;
  - its lock set gains nothing after the cursor except the cursor relation's own entries;
  - a statement log shows only the sum, the cursor write and the commit after the cursor statement.
- **No cursor elsewhere.** While another transaction holds the cursor, each of these commits:
  - an admission;
  - a signal;
  - a result;
  - a decline;
  - a child request;
  - an expiry;
  - a reconciliation;
  - a capacity step.

  Each case also asserts that it never locked the cursor.
- **The installation limit under races.** Reservations in several environments race stops, with `active_limit` reached. Then:
  - the committed sum never exceeds the limit;
  - a reservation that loses at the leaf leaves no reservation and no increment;
  - the recorded `active` is never below the sum;
  - every committed stop returns its slot.
- **Deadlock races.** Each case runs many times across two workspaces that share one user, as member and as API-key creator, alongside the one-head scheduling transactions. Each must end with no `40P01` and consistent counters:
  - admissions;
  - results and declines;
  - capacity steps;
  - expiries.
- **Scheduling.**
  - `schedule_batch` refuses a limit other than 1.
  - The sweeper's pass reserves one head per transaction in round-robin order.
  - It takes no lock when nothing awaits a reservation or the installation is visibly full.
- **Lookups.**
  - A concurrent caller waits and is served.
  - With a stuck provider, a waiting caller is refused within its own timeout, wait included.
  - A late answer is never used.

Existing tests that hold the cursor to make a transaction wait (the expired-entitlement-after-lock-wait cases, and `test_api_concurrency.py`'s account-lock cases) hold the workspace or capacity row instead. Assertions on `ExecutionSchedulerCursor.active` read the sum.

## Measurement (tier 3)

- **Calibration.** `scripts/checks/execution-load.py` with the 27 October parameters, run alone: `--rate 5 --minutes 2 --burst-minutes 1 --recovery-minutes 1 --environments 2 --runtimes-per-environment 2 --waiting 20 --sample-seconds 5`, one API process. Record:
  - cursor lock wait p50, p95 and max, and cursor hold time;
  - lock waiters sampled by relation (cursor, workspaces, capacities);
  - admission latency from scheduled arrival;
  - reservations, deliveries and claims per load phase, with reservations per second;
  - provider-lookup callers that waited for a slot, their waits, and refusals after the bound;
  - API and PostgreSQL CPU.
- **The planning target.** Then the full run (8 environments of 3 runtimes, 60 minutes at 5 a second, the burst, recovery and 10,000 waiting cases), recorded against every planning-target criterion.

## Documents to update when implemented

- **The E04 plan:**
  - Task 5's lock-order sentence and `schedule_batch` contract;
  - the orchestrator lock order and capacity-step order;
  - Task 9's lock order and Task 12's runtime routes;
  - the Task 13 scheduling convention;
  - the thresholds table.

  A note in each points to this amendment; approved task text is not rewritten.
- **ADR-0011.** The scaling-risk consequence.
- **Module docstrings** in `budgets`, `scheduler`, `admission`, `dispatch`, `leases`, `reconciliation`, `orchestrator`, `progression`, `runtime_routes`, `routes`, `activity_records`, `sweeper` and `publication`, and in `fleet/authority.py`.
- **`docs/operations/durable-execution.md`:**
  - the lock order;
  - the cursor row's `active` as a record;
  - one sweeper per installation;
  - the orchestrator's grants, re-measured with the privilege log, since it no longer locks the cursor;
  - the runbook line.
- **The validation record:** finding 11 closed with evidence, findings 14 and 3 updated, and `docs/VALIDATION.md`.

## Owner decisions (29 October 2026)

1. **Parts 1–3: approved, with the cursor taken last.** Every transaction that takes it takes it last. No scheduling transaction spans heads. A refusal at the leaf rolls back and the wasted work is accepted. A `pg_locks` test proves the leaf. Tier 3 records reservations per second per phase and the cursor's hold time.
   - **Direct `schedule_batch` callers.** Decided in this amendment: a limit other than 1 is refused, never silently reduced.
2. **Part 4: approved.** Lookups wait for their slot within the existing bound. Tier 3 records how many callers waited.
3. **Part 5: approved after the first calibrations** ("go ahead with A"): memoize pure validation per process, keyed by exact content, with authority rechecks unchanged.
4. **Part 6: approved after the planning-target run** ("go ahead with the two fixes plus (a)"):
   - (a) admission looks up before its first lock;
   - the scheduling pass continues past a refused head;
   - an expired execution is never a head and is labelled `deadline_exceeded`.

   Not chosen: reserving the first attempt at admission, more than one sweeper, or accepting the measured ceiling.
5. **Part 7: approved after the diagnostic run** ("go ahead with the engine fix"; the owner's decisions of 30 October: "Fail unfittable heads"): a head whose step no longer fits before its deadline is failed at once as `deadline_exceeded`, up to 25 per transaction, instead of being refused on every turn.
   - Tests: an overload test in PostgreSQL that fails on 8aa0ff9 and passes with the fix, and a test that a head with enough time left is still reserved.
   - Admission shedding: recorded as an open finding, to decide later.
   - Throughput: options written up for approval before any code.
