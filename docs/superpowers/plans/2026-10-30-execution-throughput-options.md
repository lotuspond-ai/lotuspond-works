# Execution throughput: options for the owner (30 October 2026)

**Status: for approval. No throughput code is written until one option is approved** (owner decision, 30 October 2026: "Write up options").

Context:
- The [installation capacity amendment](2026-10-29-installation-capacity-lock.md) removed the installation-wide cursor wait (finding 11).
- Part 7 stops overload from taking goodput to zero.
- What is left is a throughput ceiling: the planning target (5 admissions a second, 2 steps each, so about 10 reservations a second) is not met.

## What limits throughput, as measured

| | full1, `9d97c50`, host at 1× | diag1, `8aa0ff9`, host at about 2.1× |
| --- | --- | --- |
| Reservations by the single sweeper | about 3 a second, about 330 ms each | about 1.4 a second, about 0.7 s each |
| Needed | about 10 a second | about 10 a second |
| Lock waits (sampled every 10 s) | `workspaces` rows only: p95 16 waiters; longest wait per sample p95 0.69 s | `workspaces` rows only: p50 8, p95 26 waiters; longest wait per sample p50 0.47 s, p95 2.0 s |
| Holders | almost all API admissions, active or idle in transaction | — |
| VM CPU | not sampled | about 95 % busy, CPU pressure 80–89 % |
| Admission, CPU / wall (p50) | — | 78 ms / 823 ms |
| Claim, CPU / wall (p50) | — | 82 ms / 455 ms |

Two readings:
- **Each reservation spends almost all its time queued on its tenant's `workspaces` row, behind API calls.** In the diagnostic the API used only about 0.1 CPU-seconds per second per workspace. The queues come from how long each holder keeps the row exclusively, not from the work done while holding it.
- **On the 2× host the VM is CPU-bound,** so every lock holder also waits for CPU while holding its lock. Any option must be judged on a quiet host at 1×, with the speed gate passing.

**Who takes a tenant's `workspaces` row `FOR UPDATE`, and keeps it to the end of the request's transaction:**
- `authorize_execution` (`execution/authorization.py`), for every execution write. That includes admission, signals and controls, and `reauthorize` inside the scheduler's reservation. It also takes `users`, `api_keys` and `memberships` `FOR UPDATE`.
- E03's `lock_pool` (`fleet/authentication.py`), through `authenticate_runtime`, for every runtime call. That includes polls, claims, renewals, results and stored values. It also takes the `runtime_pools` and `connections` rows `FOR UPDATE`. A poll that finds nothing still holds the workspace row until it commits.
- The scheduler's pre-acquire, `BudgetService._settlement_rows`, `LeaseService._rows`, and progression's capacity step (`_apply_capacity`).

The same paths also hold these exclusively:
- the deployment row (admission's active-revision check);
- the environment's `execution_capacities` row (admission, from `_parents` to commit; reservation; stop);
- the runtime instance row (per runtime).

## Option A: one scheduler per group of environments

Environments are split into N groups, for example by a hash of `environment_id`, and each group is scheduled by its own loop. The loops can be N threads in the sweeper or N sweeper processes. Each loop still makes one-head transactions.

- **The cursor leaf holds.** Every slot-taking reservation still takes the installation cursor last, only for its sum and its write (`BudgetService._leaf`). So reservations from all groups still meet the installation limit one at a time.
  - Holds are short: p95 4–8 ms in every redesign run. At about 10 a second, the cursor would be busy about 5–8 % of the time.
- **The installation sum is unchanged:** `SUM(execution_capacities.active)`, read under the cursor at the leaf.
- **Round-robin fairness holds within each group and is independent between groups.** Each group needs its own persisted position, for example a small row per group, written just before the cursor at the leaf.
  - A quiet group's environments are served sooner than a busy group's. No environment can hold up another group's.
  - Today's single position over every environment goes away.
- **The gain is bounded by distinct workspaces, not by N.** Two groups whose environments share a workspace still queue on the same row.
  - In the load world (8 workspaces), 8 loops could reach about 8 times today's rate, if each workspace's queue lets a scheduler in as often as it does now.
  - A large tenant with one workspace and many environments gains nothing.
- **Nothing else changes:**
  - Admission p95 and the API's own queues stay as they are. The schedulers add about 1.25 transactions a second to each workspace's queue at the target, so admission p95 (0.83 s at 1×, bound 1 s) may get worse.
  - The lock model is unchanged.
- **New failure modes:**
  - a group whose loop has stopped is not scheduled. That needs a fence or failover for N processes, but nothing extra for N threads in one sweeper.
  - "one sweeper per installation" no longer holds for scheduling, and must be re-recorded.

## Option B: reserve the first attempt during admission

Not recommended. It does not help throughput under overload:
- **FIFO allows it only when the environment has no older unreserved attempt and a slot is free.** That is exactly when there is no backlog. Under overload it never applies.
- **The first attempt does not exist at admission.** Progression creates it when it derives the first step, in an orchestrator transaction. Reserving at admission would mean deriving the step inside admission, under the workspace row admission already holds longest. Every admission would then hold that row longer.
- **Later steps still go through the sweeper,** so in the load world it would remove at most half the reservations, and only at low load.
- What it would improve is first-step latency at low load.

## Option C: authority rows `FOR SHARE` in execution and runtime paths

Execution writes and runtime calls would lock the authority rows `FOR SHARE` instead of `FOR UPDATE`. Those rows are workspace, user, API key, membership, runtime pool, connection and deployment. The paths are `authorize_execution(locked=True)`, E03's `lock_pool`, `_pool`, admission's deployment check, the scheduler's pre-acquire, settlement and the capacity step.

Changing authority still takes these rows exclusively. That covers workspace status, membership, key revocation, pool policy and connection revocation.

- **The fence still holds.** A revocation's `UPDATE` (or `FOR UPDATE`) conflicts with `FOR SHARE`. So a revocation still cannot commit between an execution write's check and its commit, which is the reason these locks exist ("Writes lock the account rows").
- **Two execution writes in one workspace no longer queue for each other on these rows.** The orchestrator's `FOR KEY SHARE SKIP LOCKED` claim does not conflict with `FOR SHARE`. So orchestrators would stop skipping busy workspaces.
- **What stays exclusive:**
  - the environment's capacity counters;
  - execution, step, attempt and reservation rows;
  - the runtime instance;
  - the cursor, as a leaf.

  Admission holds the capacity row from `_parents` to commit. So within one environment, admissions, reservations and stops would still queue for each other, and the queue may move there. If calibration shows it has, a second step takes admission's capacity row last, for its counter step only, the way the cursor became a leaf. Its pending-room check is repeated there under the lock.
- **Deadlock review:**
  1. *Upgrades.* Two transactions that hold the same row `FOR SHARE` and then both ask for `FOR UPDATE` on it deadlock. So every execution and runtime path must switch together, and no path may take one of these rows `FOR UPDATE` or write it after sharing it.
     - Today several paths take the workspace `FOR UPDATE` more than once in one transaction: the scheduler (its pre-acquire, then `reauthorize` in the reservation), the capacity step (`_parents`, then its own lock), settlement, and runtime calls (`lock_pool`, then the initiator's `authorize_execution`, then `LeaseService._rows`). All of them change.
     - No execution path is known to write these rows. A lock audit test that fails on any `FOR UPDATE` of them outside authority changes would confirm it.
  2. *Order.* Shared and exclusive takers keep today's global order: workspace, account rows, pool, connections, deployment, then the environment's rows. A revocation and an execution write cannot wait on each other in a cycle.
  3. *Mixed transactions.* Authority changes, such as status, membership, keys, policy and connections, stay `FOR UPDATE` from their first lock, and never run inside an execution transaction.
  4. *Starvation.* While share locks overlap without a gap, a revocation waiting for `FOR UPDATE` may wait a long time. This needs a test that a revocation under sustained execution load completes within a bound, and a tier 3 measurement of its wait.
  5. *MultiXacts.* Concurrent share lockers on one row create MultiXact IDs. Tier 3 should record MultiXact growth and the wraparound margin.
- **Scope:** E03's `lock_pool` and runtime authentication change too. That needs E03's own review of its revocation tests.

## Recommendation

**C, staged.** The measurements point at exclusive authority locks held for whole requests as the cause. Evidence:
- the waits are on `workspaces` only;
- the holders are API calls;
- a poll that changes nothing holds the row;
- calls take 5–10 times their own CPU in wall time.

C is the only option that removes that cause. It helps every path, including admission p95, claims and orchestrator claims, not only the sweeper, and it keeps one sweeper and one head per transaction. A cannot go past what a workspace row can serve, and adds load to the queues it waits on. B does not apply under overload.

**Steps, each measured at 1× on a quiet host on AC before the next:**
1. **The lock audit test, written first.** It records every `FOR UPDATE` and `FOR SHARE` of the authority rows, by path.
2. **The authority rows `FOR SHARE`, in every execution and runtime path at once.** Tests:
   - the deadlock review's upgrade and order cases, with real concurrent connections;
   - a revocation that still fences an in-flight write, and still completes under load.
3. **A calibration.** It records lock waits by relation, the sweeper's time per reservation, admission p95 and MultiXact growth. If the queue has moved to `execution_capacities`, admission's capacity step becomes a leaf, measured the same way.
4. **The planning target,** with the busy apps closed and the Mac on AC.

**Fallback:** A, as threads in the sweeper, if the review in step 2 finds a revocation path that cannot be made to share safely. It is the smaller change, but its ceiling is a workspace row's own capacity.

**For the owner:**
- C changes E03's runtime authentication. A changes "one sweeper per installation". Approve one, or neither.
