# Durable execution qualification (E04 Task 13)

Approved E04 plan: Revision 2 (PR #13), 22 October 2026. Task 13 ran 26–27 October 2026.

This is local foundation evidence from Linux containers on Docker Desktop (arm64, 4 CPUs, 6 GB). GitHub Actions has refused every job for this repository since 23 October (billing), so nothing here is hosted CI, a deployment, or production or customer qualification. Providers are synthetic throughout: the owner has no provider to supply.

## What is tested, and how

**A world per case.** Each case owns a PostgreSQL schema (tier 1, on the shared test database) or a whole `postgres:18.6` container (tiers 2 and 3, on a private 127.0.0.1 port, never 5432 or 55439). It also owns a private directory of file-backed test authorities:

- recovery epoch and availability;
- entitlement, price and demand;
- tenant policy;
- deployment trust and the admitted catalog;
- artifact keys and source access;
- activity trust and signing.

These are reread on every call and live outside the database, so a restore never rewinds them. Provider calls are counted in files beside each runtime, also outside every database.

**Real processes.** Each runs in its own process group, so a case can SIGKILL, SIGSTOP or SIGCONT it:

- the API, under uvicorn over TLS with a synthetic CA;
- orchestrator and sweeper workers, through `run_execution_worker`, with the worker role's database timeouts;
- installed runtimes: `lotuspond_runtime enroll`, then `activity-worker` in test mode. They reach the API only through a TCP proxy that can stall or sever their connections.

Customers act only through HTTP routes and the runtime protocol. A small, labelled set of integrity cases writes rows directly to prove that reads fail closed.

**Setup outside E04, written as labelled rows:**

- projects and environments (E01 has no route for them);
- operator provisioning of the scheduler cursor and capacity rows (Task 14's procedure);
- the pool policy and connection for the catalog's `connector-fixture`, which E03's installed catalog does not know.

**Clock.** Real database time. Fixtures use short durations; no test edits a deadline, lease or expiry column.

**Fault injection, test-only.** One armed commit kind fails before its commit (it rolls back) or is lost after it (the API answers 503; an orchestrator exits at once). The kinds are admission, signal, claim, result, transition and projection.

## Cases

Tier 1 has 47 cases and tier 2 has 6; installed acceptance has 16 named scenarios per artifact variant.

| Plan cases                                                                                                    | File                                  | Cases |
| ------------------------------------------------------------------------------------------------------------- | ------------------------------------- | ----- |
| Spike 1–7, REG-4 (tier 1)                                                                                     | `test_restart.py`                     | 3     |
| Spike 11–14, ENG-7                                                                                            | `test_admission.py`                   | 3     |
| Spike 8–10 (runtime), ENG-2, ENG-3 (with single use), reference agent                                         | `test_activities.py`                  | 6     |
| Spike 34, REG-5                                                                                               | `test_waits.py`                       | 2     |
| REG-1, REG-2, REG-3, REG-6                                                                                    | `test_regions.py`                     | 4     |
| Spike 23–27                                                                                                   | `test_cancellation.py`                | 3     |
| Spike 29–33, REV-5                                                                                            | `test_versions.py`                    | 3     |
| REV-1 to REV-4, REV-6                                                                                         | `test_revocation.py`                  | 5     |
| DISC-1 to DISC-4                                                                                              | `test_disconnection.py`               | 4     |
| TEN-1 to TEN-7                                                                                                | `test_isolation.py`                   | 7     |
| Privacy (`test_decoded_events_and_errors_exclude_payloads`)                                                   | `test_privacy.py`                     | 1     |
| ENG-1, ENG-4 (sequences), ENG-5, ENG-6 (with spike 8–9 for the orchestrator), ENG-8 (plan; package and input) | `test_invariants.py`                  | 6     |
| Spike 1–2 (immediate restart), REG-4 (tier 2), ENG-4 (grants)                                                 | `disruptive/test_database_restart.py` | 3     |
| RESTORE-1 to RESTORE-3                                                                                        | `disruptive/test_restore.py`          | 3     |

**Installed acceptance** (`test_installed_acceptance.py`, through `scripts/checks/execution-smoke.py`):

- It builds the SDK, runtime and API wheels outside the checkout, plus a runtime wheel rebuilt from its sdist.
- It installs each set into its own isolated environment and runs the copied journey with `python -I`.
- For each artifact variant it checks:
  - noneditable installed origins (API 0.3.0, runtime 0.2.0, SDK 0.3.0);
  - actual TLS authentication;
  - the reference workflow's result and counted read and model calls;
  - decoded-event privacy;
  - rollback before and after the admission, signal, claim, result, transition and projection commits.

The report is refused unless every scenario passed, there were zero required skips and no private sentinel, at most one provider call ran per attempt, and cleanup was confirmed.

## Deviations from the plan's wording

- **Spike 14.** E04 has no schedule triggers. The replacement case is an equal retry after a new revision is enabled: it returns the original execution, pinned to revision 1.
- **Spike 8–10.** The plan expects that "after reconciliation a new ordinal completes once". In E04 no retry class covers uncertain work, so the reconciled step fails and gets no new ordinal; the case asserts exactly one attempt.
  - The replacement is a new execution, which runs once on a fresh runtime.
  - The stopped runtime's journal fence correctly keeps it from serving again.
- **Spike 5–7.** Events record their origin (orchestrator or activity) and the delivery's runtime instance. They do not record which orchestrator or sweeper made them.
- **ENG-4.** The least-privilege role is created only on tier 2's disposable cluster (owner decision, 26 October 2026). Tier 1 covers gap-free sequences.
- **Privacy, credentials.** The credential sentinels are the runtime's own credential token and the customer's session cookie: the credentials E04's paths carry. Connection secrets reach a provider only through E03's mounted-secret resolver. E03's suite qualifies that path, and the test-mode worker mounts none.
- **Privacy, the prompt.** An agent's instructions are the customer's own package content, stored as published (plaintext) in `executable_publications` and `executable_dependencies`. The prompt sentinel is therefore allowed there and nowhere else, and the case asserts it is present there.
- **Privacy, documents.** The document sentinel is a knowledge source's content. The fixture connector's reads return null by schema, so a read result cannot carry one.
- **Journal evidence.** The cases reconcile an uncertain attempt as its owner, through the reconciliation service, with the runtime's journal entry as evidence. They require that entry to exist and assert what it says. No route yet accepts a runtime's journal evidence, so this runs in the test process.
- **DISC-1** models a disconnection as a hand-driven runtime that polls and never claims. The other disconnection cases sever the proxy.
- **RESTORE-2** cannot separate its two refusals: under the new epoch every older instance is refused, whatever its generation.
- **Tier 3.** Changes from the plan's wording:
  - **Workload.** Every case reads twice, and 20 % then wait on a form that stays open.
  - **Thresholds.** The plan gives none for "sustained growth" or the "pre-burst range". The runner fails growth over the final 30 minutes above the larger of 10 executions and 10 % of the mean backlog. The pre-burst range is the backlog's p95 over the last 10 steady minutes, at least 10.
  - **Queue age.** Measured two ways, and the larger counts against the 30 s bound. One is the plan's runnable queue: due executions plus attempts not yet reserved. The other is the age from admission of every execution not finished and not waiting, so a deferral's back-off cannot hide starvation.
  - **Service manager.** The runner restarts a runtime that exits outside a planned kill after 5 s, as a service manager would (finding 18), and reports every restart.
  - **Enrollment.** The runner enrolls at most 15 runtimes a minute, below the API's allowance for one peer (finding 3).
  - **Lock waits.** Every process times each statement that locks the cursor, including those a lock timeout ends. In a CPU-saturated process this client-side figure also includes the time the thread waited to run, so it is acquisition latency as the client saw it. The sampled server-side waiters cross-check it. PostgreSQL's own log holds only waits over 100 ms that were granted.

## Findings

Each finding was observed in a run; none is inferred.

1. **No worker scheduled attempts (fixed, amendment).** Nothing called Task 5's `schedule_batch`, so an execution with an activity stayed `pending` forever. The sweeper now schedules after each pass (`SweepReport.scheduled_attempts`). This was the first red of milestone A.
2. **A listed-open wait can refuse its signal (open for the owner).**
   - Progression commits a new wait as `open` in one transaction. The capacity step retains it, and moves a `pending` execution to `waiting`, in the next.
   - In between, `GET /waits` lists the wait as open, but a signal is refused with 403 `forbidden`, because the signal check accepts only `running`, `waiting` and `paused` executions.
   - The gap is normally milliseconds. Tier 2 widened it, because an immediate PostgreSQL restart landed in it: the case failed 2 times in 5 before the harness waited for the execution to leave `pending`.
   - A retention deferred by quota or authority keeps the gap open for as long as the deferral lasts.
   - A client polling waits cannot tell the cases apart, and 403 does not invite a retry.
   - Options: accept signals for a `pending` execution with an open wait, list a wait only once retained, or document a retry.
3. **One authority lookup at a time per API process (Task 12's recorded limitation, measured).** Concurrent callers get 503 `authority_unavailable`. Customers and runtimes retry. The runtime's one-shot `enroll` command does not, so the harness reruns a refused enrollment as an operator would. Tier 3 measures the effect under load.
    - The API also allows each peer 20 enrollment exchanges a minute, and each token 5. Every tier 3 runtime enrolls from one host, and the harness's reruns count too. The first two planning-target runs failed setup on that 429, which the runtime CLI reports only as `unavailable`. The load runner now paces enrollment at 15 a minute.
    - **Changed on 29 October 2026 (owner decision, part 4 of the [installation capacity amendment](../superpowers/plans/2026-10-29-installation-capacity-lock.md)).** A concurrent caller now waits for the slot, and the call's own timeout (at most 5 s) bounds the wait and the call together; still one call in flight per provider per process. This applies to the execution services' lookups and E03's fleet recovery lookup. The cursor had been serializing admissions, so their lookups rarely collided; without it they would have. Measured in the 29 October calibrations: 275–777 callers waited per run, p95 0.7–1.1 ms, and none was refused after its bound.
4. **A worker refuses to start if its first cycle meets a database outage.** This is by design: its service manager restarts it. The harness waits for each worker's first completed cycle before disrupting anything.
5. **Connection call bound.** A connection's `DataHandling.timeout_ms` defaults to 10 s (maximum 30 s), and an agent step's `timeout_seconds` caps the merged active limit. Reads longer than the connection's bound are stopped by the runtime.
6. **Publish-time compile errors are 503 `dependency_unavailable`** (REG-6, one step past the 10,000-step ceiling), not a 4xx.
7. **Revoking a deployment's active revision blocks admissions to its older revisions too** (REV-5 uses a separate deployment for disable).
8. **The tamper fence skips `pending` executions** (ENG-8). A due execution whose stored plan no longer matches its digest stays refused, without being fenced for reconciliation.
9. **An activity runtime that lacks one provider still receives that provider's deliveries,** and reports `unavailable`. A runtime with no activity capability at all never receives one (spike 33).
10. **An uncertain attempt is never retried.** The retry codes are limited to `unavailable`, `rate_limited` and `timeout_before_dispatch`; uncertain work waits for reconciliation.
11. **The installation-wide cursor serializes the engine under load (tier 3; owner decision, 2026-10-28: a follow-up redesign outside E04).** At 5 admissions per second the queue on `execution_scheduler_cursors` never drained, and the scheduler starved (see Runs). This exceeds the plan's proposed threshold for reviewing Task 5's installation-wide serialization (cursor lock wait p95 over 100 ms). PostgreSQL itself was nearly idle, so this does not show the limits of a single primary, which is ADR-0011's engine trigger. The bottleneck is in the application: one installation-wide lock that every admission, signal, result and capacity step takes first.
    - **Diagnosed (owner decision, 2026-10-28: "Diagnose, then decide").** A calibration with three API processes took the single saturated process out of the way. Each process stayed below one core. Yet the API's total CPU stayed at one core, admissions were no faster, cursor waits grew (p95 10.3 s), and only 10 deliveries happened during the load (see Runs).
    - So the bound is the cursor itself, held for each admission's whole transaction, not the API process. A multi-process API is not a fix, and is not documented as a deployment requirement.
    - Task 5's locking is not redesigned in E04. Owner decision (2026-10-28): this finding stays open, as a follow-up redesign outside E04 (cheaper work under the cursor, or a cursor not held across it). No further owner decision is needed for E04.
    - Evidence, in the local evidence folder outside the repository: the planning-target run, `~/Downloads/LotusPond-worktrees/e04-evidence/task13/load-full4/` (its report, and the database counts and timing logs saved before the stop under `snapshot/`); the three-process calibration, `~/Downloads/LotusPond-worktrees/e04-evidence/task13/load-workers/`.
12. **A restore ran an activity again if it was claimed after the backup (RESTORE-1; fixed by the owner's decision, 2026-10-28).**
    - **Found.** The backup holds a read admitted but not yet delivered. After the backup that read is claimed and completes once. After the restore, the restored rows show the attempt as never delivered. The restore fence knows only deliveries signed under an older epoch, so it left that attempt alone, and a fresh runtime under the new epoch was delivered it and ran it a second time: two provider calls for one attempt. The red run's output is kept in the local evidence folder outside the repository, `~/Downloads/LotusPond-worktrees/e04-evidence/task13/restore1-red.txt`, like earlier tasks' raw logs.
    - The other RESTORE-1 requirements held: older-epoch sessions and deliveries are refused, the attempt delivered before the backup is fenced uncertain, and the original signal is accepted once.
    - It was the consequence of the residual open since the reservation refund change (`docs/VALIDATION.md`, 26 October): "an attempt restored as `reserved` with no delivery carries no epoch" and was treated as never started.
    - **Owner decision (2026-10-28): hold all after restore.** After a restore, every non-terminal execution is held for reconciliation until the runtime journals are reconciled, and no restored attempt is delivered to a runtime under the new epoch before that. Rejected: a route for runtimes to report journal entries, a dispatch record kept outside the database, and documenting the gap until E05.
    - **The fix.**
      - Each execution records the recovery epoch it was admitted under (`executions.recovery_epoch`, schema v15).
      - One admitted under an older epoch is held, by the sweeper or by the first orchestrator to meet it. Every attempt of it that has not ended becomes uncertain, with its reservation. That covers those the restored rows show reserved and undelivered, and those in flight under the older epoch, which the hold fences itself. A started execution becomes `reconciliation_required`. One still `pending` keeps its state and admission slot, and derives no work.
      - Until the hold applies, scheduling, dispatch and reservation retirement skip the execution, and journal reconciliation refuses it.
      - Its owner closes an attempt a runtime's journal shows ran, with the journal entry as evidence, even though the restored rows hold no delivery for it. Closing it can only fail it. Closing a parent this way also cancels its children that the hold made uncertain and that were never delivered, and they keep their uncertain liability.
      - The owner then releases the execution (`POST .../reconciliation`). An attempt with no journal evidence is reserved again and may run once under the new epoch. One delivered under the older epoch still needs its confirmed stop.
      - While held, nothing ends it until nothing it started may still run: a cancellation or its deadline takes effect after the release, at the latest.
      - The operator procedure is in Task 14's operations guide, `docs/operations/durable-execution.md`.
    - **RESTORE-1 now asserts the hold.**
      - All three restored executions are held.
      - A fresh runtime serves new work, and is never delivered a restored attempt.
      - The read claimed after the backup reconciles as uncertain from runtime-a's journal, and fails.
      - The read delivered before the backup cannot be released without its stop.
      - The released form waits again, and its original signal is accepted once.
      - RESTORE-3 releases its restored read before a fresh runtime runs it.
    - **Residuals.** The refund change's first residual is closed: a restored reserved attempt is uncertain, never released at zero. Its second is closed as well. The hold fences every attempt in flight under the older epoch itself, whether or not the sweeper reached its delivery, and nothing can reconcile or release the execution before the hold. So a pause cannot release a restored root, and a parent's reconciliation cannot close an old-epoch child as never started.
    - **Open, with the missing reconciliation route.** Known usage in a journal entry is not counted for an attempt that the restored rows never reserved, such as RESTORE-1's queued read. The evidence closes the attempt, but no reservation exists to settle.
    - **Rows from before v15** read epoch 0. Under any later epoch they are held like restored rows, for an owner's release. No v14 build had an execution route, so no such rows exist outside tests.
    - **Left to the operator.** A runtime journal names attempts, not executions. An entry for an attempt the restored rows don't know shows that some execution of that runtime's environment progressed after the backup. The journal can't say which one. The procedure covers that case.
13. **An equal signal replay after the signal took effect was refused (fixed).** Installed acceptance's "signal, after commit" rollback found it.
    - The commit held but its answer was lost, and progression consumed the signal at once. The client's equal retry then got 403, because the replay check ran after the wait-state check.
    - Task 12's documented contract is that an equal retry replays the first answer.
    - Now a delivered signal replays its receipt, under current authorization. An undelivered one is still judged by its wait, so Task 6's late-replay refusal stands.
    - Fixed in this commit, with a unit test in `test_inbox.py`.
14. **The sweeper may now wait up to the worker's lock timeout for the cursor (a consequence of the scheduling amendment).** It schedules after a pass's items and waits for the cursor as any request does, 15 s by default, because a shorter bound let admissions starve it. Under contention the next pass, and with it due timers, lapsed leases and expiries, can start up to that long late. The second calibration shows sweeper cycles at p95 11 s. REG-4's timers fire once and never early, but not necessarily at their deadline under load. Its lock-free check sees only whether some attempt awaits a reservation, not whether there is capacity for it. So under a saturated backlog the sweeper still takes the cursor on every pass. Bounding scheduling per pass, or running it apart from the item sweep, is for the owner to weigh with finding 11.
    - **Resolved by finding 11's redesign (29 October 2026).**
      - Scheduling reserves one head per transaction and takes the cursor last, for a few statements. Nothing else takes it, so the sweeper never waits behind admissions for the cursor.
      - A plain read skips scheduling while the installation is visibly full, so a saturated installation takes no lock at all.
      - Measured: sweep cycles p95 0.49 s at `2067363`, against 11 s on 27 October. Cursor acquisitions waited at most 1.64 ms.
15. **Active time is charged at least its reservation.** A settled attempt keeps its full reserved active time against the root budget, even when the call ended sooner (Task 5's conservative liability). Demand must be sized so that every attempt's reservation fits the root budget (REG-1 needed 30 s, not 60 s, for two reads in a 100 s budget).
16. **REG-6's bound.** At the 10,000-step ceiling, one more step is refused at publication. The case also asserts a bound on the rows read from `execution_step_instances`, per step instance created, from `pg_stat_user_tables`.
    - **The bound is fewer than 9,001** (changed on 28 October from 9,001 / 3, the case's own margin; the plan's criterion is "within the recorded region bound"). Reading the whole expansion on every transition reads at least 9,001 rows per instance. Region-scoped loads cost about 1,000: the root region's 1,000 pinned items, or one item's eight steps.
    - **The measure varies with PostgreSQL's plans:** 1,000 to 3,569 rows per instance were measured on 28 October, at the merged head and before the merge alike. The disposable table grows from empty to 9,002 rows in about 3 minutes, faster than autovacuum analyses it. So the plans for the same exact-path loads swing between index lookups, sequential scans and loosely bounded index scans. It is not an engine or merge regression, and `_cancel`'s `LIKE` prefix query is not on this path.
    - The case prints the rows read by sequential scans and through indexes separately. CI's `-ra` shows them only when the case fails; `-rP` shows them for a pass.
17. **Throughput observed in tier 1** (REG-2, 1,000 items, concurrency 5): about 1 item per second. Reserving an attempt takes about 0.6 s, iterations run in waves of 4–5 s, and spawning a child takes about 1 s.
18. **Runtime 0.2.0 exits under load (by design, measured).**
    - The runtime ends its process with exit 3 (`unavailable`) when a control call is refused or takes more than 10 s, and leaves restarting it to its service manager. A restarted runtime first waits out its previous session lease.
    - Under tier 3 every runtime exited this way, over and over. In the first full-scale run all 24 had exited within minutes, so that run was stopped.
    - The load runner now stands in for a service manager. It restarts a runtime that exits outside a planned kill after 5 s, and records each restart.
      - The 5-minute check: 780 restarts across all 24 runtimes, every one exit 3.
      - The planning-target run: 11,250.
    - The two 27 October calibrations had no restarter, and their runtimes ended with exit 3 as well. So their dispatch counts show what stopped runtimes served, not what the engine could dispatch. Their cursor measurements stand.
    - The operations guide's runbook asks for a restarting service manager.
    - **29 October 2026:** with the cursor no longer queuing results and declines, and the memo shortening every hold, the calibration at `2067363` saw no restart at all. At `5a38912`, before the memo, there were 125.
    - While a restarted runtime waits out its old lease, deliveries to it expire. A read whose dispatch retries run out in that window fails with `provider_unavailable`, unless another runtime serves the pool. Task 14's final tier 2 run showed this in ENG-4, which now resumes its paused read onto a freshly enrolled runtime.

Found by Task 14's whole-branch review (27 October), after the tiers above had run:

19. **The worker grants missed paths tier 1 never ran (fixed in Task 14).**
    - Tier 1 recorded the grants of the paths it exercised. It never exercised a capacity step that stops a reserved, unclaimed attempt after a pause or a cancellation. Nor did it authorize an execution that an API key started, or one in an MFA workspace.
    - The operations guide grants those too.
    - ENG-4 runs the first two under exactly the guide's grants.
    - Applying the grant blocks also showed that `memberships` has no `id` column, so its row-lock grant is `UPDATE (workspace_id)`.
20. **Scheduling fails closed on a database refusal (open for the owner).** The scheduler authorizes each head inside its batch. A missing grant, or any other database error there, aborts the whole batch, so nothing is scheduled anywhere until it is fixed. The worker logs only `authority_unavailable` or `unexpected`; PostgreSQL's own log names the cause.
21. **No route reconciles an uncertain attempt (open for the owner).** No route or runtime report carries a journal's evidence of stop and usage. So `POST .../reconciliation` refuses with 409 for a lapsed or restore-fenced attempt, and the execution keeps its slot and liability: five such attempts exhaust an environment's default limit. Under the restore hold (finding 12), an attempt that a journal shows ran can only be cancelled, and its usage goes uncharged. The conformance cases reconcile in the test process instead (see Deviations).
22. **One tenant's due work could hide another's from claims (fixed in Task 14).**
    - A claim read only the 25 earliest due candidates. Skipping a workspace held `FOR UPDATE` skipped all of them.
    - A claim now reads past skipped workspaces and executions, for at most 4 rounds.
    - `test_a_locked_tenant_filling_the_candidate_window_hides_no_other_tenant` failed before the fix.
23. **A sweep item could raise a lower lock timeout (fixed in Task 14).** Its 500 ms bound now only ever lowers the worker's lock timeout. `test_a_sweep_item_only_ever_lowers_the_worker_lock_timeout` failed for 200 ms before the fix.

Found in Task 14's final runs:

24. **A signal accepted after an immediate restart was not applied for 120 s, once (open for the owner: cause not established).**
    - **What happened.** In the first final tier 2 run, `test_a_form_wait_survives_an_immediate_restart` sent its signal after the restart. It was accepted on the first try: the API logged no error. The execution then stayed `waiting` for the case's full 120 s.
    - **The logs.** During the restart the orchestrator logged two refused cycles (`authority_unavailable`), and the sweeper one (`invalid_request`, the engine's code for any database error outside a service). Neither logged a deferred transition, capacity step or retention.
    - **Checked in the code, and sound:**
      - the signal's inbox row, its outbox wake-up and the runnable marker commit in one transaction;
      - a claim skips a candidate only while another transaction holds its workspace or execution row, and reads are authorized without locks;
      - a connection lost inside a claim rolls the whole transaction back, marker included;
      - in the harness a worker polls at most every 250 ms, with pre-pinged connections;
      - due times use `clock_timestamp()`;
      - the form flow's terminal capacity step has no attempt in flight, so it completes at once.

      None of these loses a wake-up.
    - **Not reproduced.** 37 other runs of the case passed, isolated or in full tier 2 runs, before and after. It never failed in Task 13's runs.
    - **No cause from this run.** Its database went with its disposable cluster, so the cause cannot be established from it.
    - **If it stalls again,** the case now records the evidence: the execution's state, marker and epoch; its outbox, inbox and wait rows; and every open transaction, with its locks on `workspaces` and `executions`.
    - **Raw output:** `~/Downloads/LotusPond-worktrees/e04-evidence/final/disruptive-3.14-r1.log`.

Found while qualifying the installation cursor redesign (finding 11, 29 October):

25. **A package's `retained_waits` is applied environment-wide, and the retained-wait quota is not first come, first served (open for the owner; no engine change).** Both facts are on `main` (`533b3d6`). The [installation capacity amendment](../superpowers/plans/2026-10-29-installation-capacity-lock.md) leaves them unchanged: `retain_wait` is byte-identical, and the redesign only removed the cursor from the capacity step's lock order.
    - **Environment-wide.** `BudgetService.retain_wait` (`backend/lotuspond/execution/budgets.py:742`) compares the environment's `capacity.retained_waits` with `min(capacity.wait_limit, limits["retained_waits"])`.
      - `_limits` folds in the package's own `retained_waits`. The [executable-v2 plan](../superpowers/plans/2026-10-14-executable-package-v2.md) (line 75) declares that limit alongside per-execution limits such as `max_steps`.
      - There is no per-execution counter. So a package that declares 10 is refused once its environment holds 10 retained waits, from any execution of any deployment.
    - **Not first come, first served.** A refused retention backs off like any capacity step that changes nothing, up to 60 s, and the execution stays `pending` while its wait is unretained (finding 2). A retained wait keeps its slot until it closes. A capacity step releases a closed wait and retains the same execution's next wait in one transaction, so the slot passes to that execution's next wait.
    - **Measured in ENG-4** (`test_event_sequences_are_gap_free_under_concurrent_transitions`). The case starts 12 executions of `waits_fast` (`retained_waits: 10`) in one environment, with two orchestrators, and never signals them. It waits for each execution to have an open callback and to have left `pending`.
      - A failure-state dump from three failures, identical each time: executions 0–9 retained their pause and then their callback, holding all 10 slots. Executions 10–11 never retained a wait, stayed `pending`, and their callbacks expired. The orchestrators logged retention deferred `capacity_unavailable`, plus a few `conflict`: a 2 s pause satisfied or expired before its capacity step ran.
      - Executions 10–11 can get in only in a short window, when another execution has released its pause in one capacity step and not yet retained its callback in the next. So the case passes or fails on timing:
        - `main` (`533b3d6`), alone: failed 1 of 3;
        - the redesigned head (`2067363`), alone: failed 5 of 6, and in the full tier 1 run;
        - every full E04 tier 1 run before the redesign passed (4 of 4).

        The redesign's capacity steps no longer queue on the cursor and run faster, so those windows are shorter. Raw output: `~/Downloads/LotusPond-worktrees/cursor-evidence/suites/eng4-*.log`.
      - **Test-only fix (owner decision, 2026-10-29: option A).** ENG-4 keeps 12 executions and two orchestrators, with a copy of `waits_fast` whose `retained_waits` is 24, covering both waits of all 12. The case is about event order, not quota.
    - **For the owner:** whether a package's `retained_waits` should cap each execution or the whole environment, and whether the retained-wait quota should be granted in admission order.

Found in the overload diagnostic (finding 11, 30 October):

26. **Admission accepts work the queue cannot serve before its deadline (open for the owner; owner decision, 2026-10-30: record it, decide later; admission unchanged).** Admission refuses only when an environment's pending count reaches its limit, 1,000 by default. It does not consider how old the queue already is, or how fast it is being served.
    - **Measured in the 32-minute diagnostic** (`diag1`, at `8aa0ff9`):
      - The setup: 8 environments of 3 runtimes, 3 API processes, 5 admissions per second. The machine ran at about 2.1× full1's per-call CPU.
      - Reservations kept pace with about 0.7 executions a second (two steps each), against 5 arriving.
      - Admission kept accepting: about 9,600 in 32 minutes, p95 1.9 s, none refused. Meanwhile queue age grew by about 50 s every minute.
      - From minute 18, nothing admitted could run before its deadline. All 3,532 executions admitted from minute 3 to minute 14 were failed with `deadline_exceeded` without running a single step, after about 16 minutes (1,000 s) in the queue. Another 228 were failed partway through.
      - Only the 575 admitted in the first 3 minutes succeeded.
    - **Part 7 of the [installation capacity amendment](../superpowers/plans/2026-10-29-installation-capacity-lock.md) does not change this.** It fails an execution as soon as its next step no longer fits before the deadline. So under sustained overload, queue age stays about one step's reserved time short of the deadline (30 s in the load world), and clients still wait about 16 minutes to be failed. What part 7 changes is that work that can still run keeps being served.
    - **For the owner:** whether admission should refuse, with `capacity_unavailable`, work that an environment's queue cannot serve before its deadline. For example, it could bound pending by the measured service rate times the deadline, or refuse while the oldest runnable execution is older than some share of the deadline.
    - Evidence: `~/Downloads/LotusPond-worktrees/cursor-evidence/diag1/results.md`, with its snapshots, scheduling probes and table copies.

## Runs

Results are recorded below as each run completes. Every run is a local Linux container on Docker Desktop (arm64, 4 CPUs, 6 GB), PostgreSQL 18.6, Python 3.14.9. The worktree is mounted read-only. Raw output stays in the local evidence folder outside the repository.

### Tier 1, required mode, 27 October 2026

- **Run.** At `860aa97`, clean tree, 13:57–14:35 UTC: `tests/execution/conformance`, excluding `disruptive/` (tier 2 runs on its own), with `LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS=1`.
- **Result:** 42 passed and 5 failed, in 38 min 13 s. No skips.
- **The five failures, each a defect in the case, not in the engine:**
  - **REG-2:** the package's default elapsed limit of 1,000 s was shorter than the about 1,000 s 1,000 items take (finding 17). The limit was raised to 7,200 s; rerun alone, it passed in 1,273 s.
  - **ENG-1:** the case assumed the read always starts before the deadline. When the deadline won first, no call was made. Now at most one call is allowed.
  - **ENG-3, single use:** a holder's equal claim is an idempotent replay by design (the same lease, no second one). The case now asserts the replay, one lease row, and another session's refusal.
  - **DISC-4:** a read admitted during the 150 s outage failed by its declared dispatch timeout, 2 s three times. That is correct; the case admits its read after reconnection again.
  - **REV-6:** the new positive control was blocked by the deployment's single active slot, held by the refused read as uncertain liability. It now has two slots, and asserts no attempt runs twice.
- **REV-6, intermittent.** In one rerun out of three, the control read itself ended uncertain after the authority returned, with no API refusal logged. It was not diagnosed then; the case now captures the runtime's journal and the lease rows if it recurs.
  - **Diagnosed on 28 October**, when it recurred after the merge with `main` ([VALIDATION](../VALIDATION.md), "Merge with `main` and reruns"). The fixture read's 2 s step deadline is also its lease. On a loaded host, claim, read and report outlasted it, so the attempt was uncertain, as designed. The case's reads now have a 30 s deadline.
- **Reruns.** After the fixes, each changed case was rerun alone or in small batches, and all passed: REG-2, ENG-1, ENG-3, DISC-4, REV-6, privacy (with the citation's positive control) and REG-1.
- **REG-6:** 9,002 step instances, 1,082 rows read per instance, against the bound of 3,000.
- **Slowest cases:**
  - REG-2: 1,003 s in the run, 1,273 s alone;
  - ENG-6: 183 s;
  - REG-6: 167 s;
  - DISC-4: 152 s;
  - REV-6: 118 s.
- **Grants.** The run recorded the worker grants: 3,892 entries, in 34 tables for the orchestrator and 36 for the sweeper. Task 14's operations guide gives them.

### Tier 1, final run, 27 October 2026

- **Run.** At `16c6fea`, after every correction, clean tree, 16:21–16:52 UTC, required mode.
- **Result:** **47 passed, 0 failed, 0 skipped**, in 31 min 34 s.
- **REG-6:** 9,002 step instances, 1,111 rows read per instance, against the bound of 3,000.
- **Slowest cases:**
  - REG-2: 820 s;
  - DISC-4: 154 s;
  - ENG-6: 150 s;
  - REG-6: 130 s.

### Tier 2, final run, 27 October 2026

- **Run.** At `16c6fea`, run alone.
- **Result:** 5 passed and 1 red, in 1 min 11 s. The red case is RESTORE-1 (finding 12).
- **Cleanup.** Clusters and volumes were confirmed removed.

### Installed acceptance, 27 October 2026

- **Run.** On a standalone clone of `16c6fea` (`source_dirty: false`), under `execution_process.run_owned`: **1 passed**, in 90 s.
- **Both artifact variants passed all 16 named scenarios:** the wheels, and a runtime wheel rebuilt from the sdist.
- **Observed.**
  - Each journey made 11 provider calls, at most one per attempt.
  - No private sentinel was found on any surface.
  - Zero required skips.
  - Cleanup was confirmed: 16 owned commands, every process group, no schema, container or volume left.
- **Distributions** (SHA-256):
  - `api/lotuspond_api-0.3.0-py3-none-any.whl`: `ad1cc88bcbcf4237a1ab6e8de56b49cbe4cbd923d2dffb694a7e65692c18b126`
  - `rebuilt/lotuspond_runtime-0.2.0-py3-none-any.whl`: `77c06c86282fcd2f51808a92384f03dbda92b97e4019c765c44629b67f4f9ce0`
  - `runtime/lotuspond_runtime-0.2.0-py3-none-any.whl`: `77c06c86282fcd2f51808a92384f03dbda92b97e4019c765c44629b67f4f9ce0`
  - `runtime/lotuspond_runtime-0.2.0.tar.gz`: `5d0dea7a837df107e136a3882e758c745e7d8f7ac39987b9b7b73430f08b9b55`
  - `sdk/lotuspond_sdk-0.3.0-py3-none-any.whl`: `11f47aebe91ac6cdc16396fdd5d2caa0189b6ebb989c4c713b03a09726a5db0c`
- **Source manifest** SHA-256: `f8556381bc3561dadc3d01148e7ae08b503e662c8a5f212e8b4c5a6f06d14396`.
- **Earlier failed attempts, corrected:**
  - The journey shared one tenant, so uncertain liability from one scenario held later scenarios' capacity. Each scenario now has its own environment.
  - The deploy and publish caches were keyed by workspace.
  - The reference check expected 3 claimed attempts instead of 4.
  - A hand-driven poll was rate-limited.
  - One engine defect: an equal signal replay was refused after the signal took effect (finding 13).
  - Two failures were local setup, not code: git from a worktree inside the container, and an incomplete offline package cache. Both were fixed in the local loop.

### Runtime suite, 27 October 2026

- **Run.** At `16c6fea` on Linux, with network access as in CI, because the installed-wheel fixtures build with `uv`: **733 passed**.

### Tier 2, first run, 27 October 2026

- **Run.** At `28b0c07`, with `LOTUSPOND_DISRUPTIVE_EXECUTION_TESTS=1` and required mode.
- **Result:** 5 passed and 1 failed, in 1 min 52 s. The failure is RESTORE-1, red against the plan as finding 12: the read claimed after the backup ran a second time after the restore.
- **Cleanup.** Every disposable cluster and its volumes were confirmed removed, and no labelled container remained.

### Tier 3 calibration, 27 October 2026

`execution-load.py` at a reduced scale, ahead of the full run, to check the runner end to end:

- 2 environments, each with 2 runtimes;
- 2 minutes at 5 admissions per second, 1 minute at 10, then 1 minute at 5;
- 50 waiting cases.

The cluster was `postgres:18.6` with default settings, except `max_connections=300`, `log_lock_waits=on` and `deadlock_timeout=100ms`. The run was on the hardware above.

**The engine did not keep up.**

- All 1,489 admissions were accepted (201), but each took about 20–29 s. Admissions were tagged by the phase in which they finished, a runner bug fixed afterwards, so the per-phase counts in that report are not usable.
- No attempt was delivered during the load phases: 0 deliveries, 0 claims, 0 provider calls.
- Executions piled up `pending`: 1,500 by the end, with 20 waiting.
- At every sample, 21–22 backends waited for the row lock on `execution_scheduler_cursors`, and the longest current wait was 5.8–7.5 s.
- PostgreSQL logged 3,683 waits over 100 ms by statements on the cursor (p50 384 ms, p95 6.5 s, max 8.0 s). The plan's proposed trigger is p95 over 100 ms.
- The API process used 111 CPU-seconds in the 120 s steady phase, one saturated core. The orchestrators used 9 and the sweeper 2.5.
- PostgreSQL itself was nearly idle: CPU p50 6 %, p95 16 %; WAL about 110 kB/s.

**What this shows.**

- Every admission, signal, result and capacity step takes the installation-wide cursor row first (Task 5's order). The scheduler and the orchestrators' capacity steps need it too.
- With the single API process saturated, a request holding the cursor proceeds slowly, and the queue on the cursor never drains. Starved of the cursor, the scheduler never reserved an attempt.
- This exceeds the plan's proposed threshold for reviewing Task 5's installation-wide serialization, measured, not assumed. PostgreSQL was not the limit.
- Four idle runtimes then polled every 0.2 s, about 20 requests per second to the same API process. The test-mode activity loop now backs off while idle to E03's one-second ceiling.

**Waiting phase.**

- The 50 form executions never reached `waiting` within the 30-minute bound: the orchestrators could not get the cursor either.
- After every worker and runtime was killed and restarted, the workers were ready in 1.2 s.
- The 20 new reads all ended `failed` (p50 188 s, max 366 s). That run did not record why; the runner now records state and reason.
- 5 sweep passes over the remaining set took 36–44 ms.
- Resident memory: API 208 MB, orchestrators 261 MB in total, sweeper 132 MB.

Cleanup was confirmed: every process group ended, the schema was dropped and the cluster removed. A local result is evidence about this harness on this hardware, not hosted capacity or a service level.

### Tier 3 calibration, second run, 27 October 2026

Same scale, after the review corrections:

- admissions timed from their scheduled arrival;
- every cursor acquisition timed by its own process;
- queue age also measured from admission;
- the real workers' cycle durations;
- the plan's two-reads-then-form case;
- the fair scheduler.

**Admissions.**

- 1,289 admissions; one was refused, 503 `recovery_unavailable`: the one-at-a-time lookup.
- Each request took 22–29 s.
- Timed from their scheduled arrival, p95 was 124 s in the steady phase and 270 s in the burst.

**The cursor.**

- Of 5,382 acquisitions, 1,854 waited over 100 ms (p95 8.0 s, max 11.8 s), and 342 were ended by a lock timeout.
- By role: the API made 4,380 (p95 8.2 s), the sweeper 990 (p95 2.2 s), the orchestrators 12.
- At every sample the queue was about 20 deep. The holders ahead of it were API requests, one of them idle in its transaction: an API thread holding the cursor while it computes.

**The API was the bound.**

- 229 CPU-seconds in 240 s of load: one saturated core, about 0.15 CPU-seconds per admission.
- PostgreSQL CPU was p50 5 %.
- The orchestrators' cycles took p50 3 ms. The sweeper's took p95 11 s, waiting for the cursor.

**Outcome.**

- 6 deliveries and 2 claims; 2 provider calls, one per attempt (the one-call criterion passed).
- 1,445 executions were still `pending` at the end.
- After the kill and restart, the 20 waiting cases had not reached their wait. All 20 resumed reads ended `failed` with reason `provider_unavailable`, after p50 122 s.
- The restarted sweeper's passes took p50 0.04 s.

**What this adds.** The first run's conclusion stands, now measured at the holder. Each admission spends about 0.15 CPU-seconds in a single API process, and holds the installation-wide cursor while it does. So everything that needs the cursor queues behind a saturated process: scheduling, claims, results and capacity steps. The planning target needs either cheaper admission work under the cursor, or a cursor that is not held across it. Both are design changes for the owner.

### Tier 3, planning-target attempts, 27 October 2026

Three attempts at the planning target (8 environments of 3 runtimes, 60 minutes at 5 admissions per second) did not reach their load:

- At `bdb71c0` and `293adde`, setup failed: "runtime enrollment refused: unavailable". A diagnostic run showed a 429 from the API's per-peer enrollment allowance (finding 3). Enrollment is now paced.
- At `13279a5`, every runtime had exited with code 3 within minutes of load (finding 18), so the run was stopped. Cleanup was confirmed.

### Tier 3, five minutes at full scale, 27 October 2026

With the restarter, at `8d12453`: 8 environments of 3 runtimes, 3 minutes at 5 admissions per second, 1 at 10 and 1 at 5, no waiting phase.

- **Admissions.** 1,789 accepted. Each request took about 17 s (p50). Timed from scheduled arrival, p95 was 78 s steady, 164 s in the burst and 188 s in recovery. Some were refused with 503 `entitlement_unavailable`, `recovery_unavailable` and `trust_unavailable`: the one-at-a-time lookups (finding 3).
- **Work.** 149 deliveries (dispatch p95 23.8 s) and 118 claims. At most one provider call per attempt (passed).
- **The cursor.** 7,316 acquisitions: 2,667 over 100 ms, p95 5.3 s, max 10.1 s, and 593 ended by a lock timeout.
- **Load.**
  - The API used 164 CPU-seconds in the 180 s steady phase.
  - PostgreSQL CPU was p50 5 %.
  - Sweep cycles took p95 17 s.
- **Runtimes.** 780 restarts, all exit 3.
- The four throughput criteria failed. Cleanup was confirmed.

### Tier 3, planning-target run, 27 October 2026

At `8d12453`, 17:34–19:20 UTC: the plan's workload, 8 environments of 3 runtimes, 60 minutes at 5 admissions per second, 5 at 10 and 10 at 5, then 10,000 waiting cases.

- **Admissions.**
  - In the steady hour, 9,436 were accepted. Another 8,596 were refused, almost all 429 `capacity_unavailable`: the backlog filled every environment's pending capacity of 1,000.
  - Each request took 17 s (p50). Timed from scheduled arrival, p50 was 290 s and p95 566 s.
- **The API was the bound.** It used 3,132 CPU-seconds in the 3,600 s hour, one core at 87 %. The orchestrators used 110 and the sweeper 251.
- **Work.**
  - From admission to the stop: 20 deliveries, 9 claims, 1 provider call.
  - At 105 minutes: 8,000 executions `pending`, 4,848 `failed` with `deadline_exceeded`, 8 `reconciliation_required` after lapses, and 1 running.
- **The cursor.** 90,644 acquisitions: 31,402 over 100 ms, p95 5.0 s, max 7.4 s, and 4,472 ended by a lock timeout or error. By role:
  - the API: 72,165 (p95 5.1 s);
  - the orchestrators: 8,693 (p95 4.8 s);
  - the sweeper: 9,786 (p95 2.2 s).
- **PostgreSQL was not.**
  - Its CPU was p50 6.6 % and p95 9.5 %, and WAL ran at 218 kB/s.
  - Sweep cycles took p95 13.8 s.
- **Runtimes.** 11,250 restarts, all exit 3.
- **Criteria.**
  - Failed: admission p95, queue age (p95 2,670 s) and backlog growth (1,067 over the final 30 minutes).
  - Burst recovery passed only because the backlog was pinned at its limit (a band of 8,009).
  - One provider call per attempt passed.
- **Stopped in the waiting phase.** 28 minutes in, none of its cases had reached their wait, when the owner's decision to calibrate with several API processes arrived. The run was stopped so that calibration could run alone.
  - The report keeps the load phases.
  - The database counts and the timing logs were saved to the evidence folder first.
  - Cleanup was confirmed: every process group ended and the cluster was removed.
- **Evidence:** `~/Downloads/LotusPond-worktrees/e04-evidence/task13/load-full4/` (`run/load-report.json`, and `snapshot/` with the database counts, timing logs and process logs).

### Tier 3 calibration with three API processes, 27 October 2026

The owner's diagnosis for finding 11 (decision, 2026-10-28: "Diagnose, then decide"). Same scale as the second calibration (2 environments of 2 runtimes; 2 minutes at 5 admissions per second, 1 at 10, 1 at 5; 20 waiting cases), with the API run as three processes on one socket (`--api-workers 3`), at `de358db`, 19:26–20:09 UTC, run alone. Everything else is as on 27 October, plus the restarter and enrollment pacing (findings 3 and 18).

- **No API process was saturated.** In the 120 s steady phase the three used 73, 28 and 16 CPU-seconds (61 %, 24 % and 14 % of a core). The kernel spread the connections unevenly. Their total, 117 CPU-seconds, is the one core a single process used (114 in the second calibration).
- **Admissions were no faster.**
  - 1,436 were accepted. Some recovery-phase arrivals met 503 `entitlement_unavailable`, the one-at-a-time lookup.
  - Each request took p50 26 s (23 s with one process).
  - Timed from scheduled arrival, p95 was 85 s steady, 232 s in the burst and 271 s in recovery.
- **The cursor waited longer.**
  - 6,255 acquisitions: p50 0.2 ms, p95 10.3 s, max 14.4 s.
  - 1,839 of them waited over 100 ms, and 250 were ended by a lock timeout.
  - By role, the API's p95 was 10.4 s, the sweeper's 503 ms and the orchestrators' 281 ms.
  - At each sample about 28 backends waited (max 45), the longest for p50 9.2 s.
- **Work during load.**
  - 10 deliveries and 6 claims, all in the steady phase; none in the burst or recovery.
  - The backlog grew to 658 by the end of the load.
  - The run's 148 deliveries, 144 claims and 141 provider calls came almost all after the load stopped. That was at most one call per attempt, which passed.
- **PostgreSQL.** CPU p50 5 % and p95 11 %; WAL 128 kB/s.
- **Waiting phase.**
  - The 20 form cases did not reach their wait within 30 minutes.
  - After every worker and runtime was killed and restarted, the workers were ready in 1.4 s, and all 20 resumed reads succeeded (p50 57 s).
  - Claims after the restart took p95 679 ms, and sweep passes p95 0.39 s.
- **Runtimes.** 66 restarts, all exit 3.
- **Memory.** The three API processes used 594 MB in total.
- The four throughput criteria failed. Cleanup was confirmed.
- **Evidence:** `~/Downloads/LotusPond-worktrees/e04-evidence/task13/load-workers/` (`run/load-report.json` and the console).

**What this shows.** Taking the single API process out of the way changed nothing that matters. The installation-wide cursor is the bound. Every admission takes it first and holds it for its whole transaction, so admissions, scheduling, claims and results go through it one transaction at a time, however many API processes there are. Finding 11 stays open as a follow-up redesign outside E04 (owner decision, 2026-10-28). A multi-process API is not a deployment requirement.

### Installation capacity redesign: calibrations, profiles and suites, 29 October 2026

Finding 11's follow-up, the [installation capacity amendment](../superpowers/plans/2026-10-29-installation-capacity-lock.md), was owner-approved on 29 October 2026 (cursor last; lookups wait for their slot; later, the memo of pure validation). Each run below is on local Docker Desktop (arm64, 4 CPUs, 6 GB), run alone, from a clean clone, with synthetic providers.

All calibrations use the 27 October parameters: 2 environments of 2 runtimes; 2 minutes at 5 admissions a second, 1 at 10 and 1 at 5; 20 waiting cases. Tier 3 now also records:

- cursor hold time;
- lock waiters by relation, with who blocks them;
- provider lookups that waited for their slot;
- reservations, deliveries and claims per phase, with their rates;
- admission and runtime calls timed in their process (wall and request-thread CPU);
- deadlocks PostgreSQL logged.

| Run (head, API processes) | Cursor wait p95 / max; hold p95 | Workspace-row waiters per sample p50 / p95, longest p95 | Admission p95 from arrival (steady / burst) | Reservations / deliveries / claims during load | API CPU, steady 120 s | Deadlocks |
| --- | --- | --- | --- | --- | --- | --- |
| 27 Oct, 1 process (before; second calibration) | 8.0 s / 11.8 s; not measured | not measured | 124 s / 270 s | – / 6 / 2 (whole run) | about 1 core (229 CPU-s in 240 s of load) | – |
| 27 Oct, 3 processes (before) | 10.3 s / 14.4 s; not measured | not measured | 85 s / 232 s | – / 10 / 6 | 117 CPU-s | – |
| `5a38912`, 1 process (cursor last) | 0.16 ms / 2.15 ms; 1.5 ms | 19 / 19, 7.9 s | 82 s / 228 s | 5 / 2 / 1 | 122 CPU-s | 0 |
| `6403930`, 3 processes | 0.22 ms / 1.26 ms; 2.3 ms | 34 / 43, 10.7 s | 29 s / 119 s | 3 / 3 / 3 | 165 CPU-s | 0 |
| `2067363`, 1 process (memo) | 0.28 ms / 1.64 ms; 1.8 ms | 1 / 15, 0.95 s | **0.63 s** / 1.69 s | **329 / 321 / 320** | 101 CPU-s | 0 |

**The cursor.**
- From `5a38912` on, only the sweeper's scheduling took it: 348–1,096 acquisitions per run.
- None of them waited over 100 ms, and none was ended by a lock timeout.
- No backend waited on it at any sample.

**The queue moved to each tenant's workspace row.** Execution authority and E03 runtime authentication lock it `FOR UPDATE`.
- The samples show its holder: nearly always an API admission idle in its transaction, its thread busy in Python.
- Orchestrator claims skip a tenant while its row is held, and scheduling waits behind admissions. So with two tenants, almost no work ran during load.

**Where an admission's CPU went.** Single-threaded profiles (`--profile`, no runtimes, 0.5 admissions a second, whole-process cProfile):
- At `6403930`, about two thirds of the real work was the SDK re-validating and re-canonicalizing the same stored bytes. That was JSON-domain checks, JCS (`rfc8785`) and pydantic. Per admission there were about:
  - 6 publication loads;
  - 6 plan compilations;
  - 6 signature verifications;
  - 34 package validations;
  - 20 catalog validations.

  SQL was about 14 %, and backend code under 1 %.
- The memo (part 5, `2067363`) cut request-thread CPU per admission from 992 to 141 ms (profiled), and wall time from 2.0 to 0.16 s. Compilations fell from 278 to 6, verifications from 276 to 6, package validations from 1,510 to 32, and catalog validations from 901 to 18.
- The authority checks ran as often as before: 269 → 266 publication loads, 179 → 172 grant lookups, 503 → 493 reauthorizations.
- Unprofiled, in the calibration, an admission costs 78 ms of request-thread CPU (p50), against 308 ms before.

**After the memo (`2067363`, one API process).**
- Admission p95 was 0.63 s from scheduled arrival, which passes the plan's criterion. At most one provider call per attempt passed too: 540 calls for 540 claimed attempts.
- The engine kept up with what the runtimes could take:
  - reservations ran at 1.6 a second, and deliveries and claims tracked them;
  - due executions stayed near zero in the steady phase;
  - runtimes restarted 0 times, against 125 at `5a38912` and 11,250 in the 27 October planning-target run.
- The other three throughput criteria failed on this scale's own capacity, not on a lock:
  - a runtime runs one activity at a time, and this scale has 4;
  - each read took about 2.5 s from reservation to result (dispatch p50 2.3 s, claim p50 0.35 s), which caps throughput at about 1.6 a second against a demand of 10.
- Provider lookups that waited for their slot (part 4): 749 waiters, p95 1.07 ms, none refused after the bound.
- Sweep cycles: p95 0.49 s, against 11 s on 27 October.
- After the kill and restart, all 20 resumed reads succeeded (p50 55 s), with claims at p95 249 ms.

**Suites, one at a time on Linux** (`ci-local.py` against a clean clone):

| Job | Head | Result |
| --- | --- | --- |
| `validate` | `2067363` | Passed. The required PostgreSQL 18.6 suite ran 1,275 passed, 0 failed, 0 skipped |
| Tier 1 and installed acceptance | `2067363` | 47 passed, 1 failed: ENG-4 (finding 25) |
| ENG-4 alone, 3 times | `9d97c50` (its quota fix) | 3 passed |
| Tier 1 and installed acceptance | `9d97c50` | 48 passed, 0 failed, 0 skipped |
| Tier 2 | `9d97c50` | 6 passed. ENG-4 runs both workers under the guide's grants, so the orchestrator no longer needs `UPDATE` on the cursor |

`9d97c50` changes only that conformance case and documents after `2067363`.

**Evidence:** `~/Downloads/LotusPond-worktrees/cursor-evidence/`:
- `cal1`, `cal3` and `cal4`;
- `profile1` to `profile3`;
- `suites/`.

### Tier 3, planning-target run with the redesign, 29 October 2026

At `9d97c50`, 11:52–13:48 UTC, alone, with 3 API processes. The workload matched 27 October's planning-target run: 8 environments of 3 runtimes, 60 minutes at 5 admissions per second, 5 at 10 and 10 at 5, then 10,000 waiting cases. Outcome: failed.

| | 27 October, `8d12453`, 1 API process | 29 October, `9d97c50`, 3 API processes |
| --- | --- | --- |
| Cursor wait | 90,644 acquisitions; p95 5.0 s; 31,402 over 100 ms | 24,473, all by the sweeper; p50 0.11 ms, p95 0.96 ms, max 7.95 ms; none over 100 ms |
| Cursor hold | not recorded | p95 4.15 ms, max 136 ms |
| Admission from arrival, steady | p50 290 s, p95 566 s; 8,596 refused | p50 0.33 s, p95 0.83 s; none refused (burst p95 0.54 s, recovery 0.47 s) |
| Reservations = deliveries = claims | 20 deliveries, 9 claims | steady 4,346 (1.21/s, all in minutes 0–24.5); burst 0; recovery 0 |
| Queue age p95 | 2,670 s | 937 s |
| API CPU, steady hour | 3,132 s (1 process) | 3,684 s (1,624 / 971 / 1,090) |
| Orchestrators / sweeper CPU | 110 s / 251 s | 1,036 s / 1,034 s |
| PostgreSQL CPU | p50 6.6 % | p50 42 %, p95 88 % |
| Runtime restarts | 11,250 | 3, all in the waiting phase |
| Deadlocks | 0 | 0 |

- **Lookup slots.** 25,890 callers waited, all on the API: 8,971 on the execution slot and 16,919 on the fleet slot. Waits were p50 1.43 ms, p95 6.58 ms and max 110.6 ms, and none was refused after the bound.
- **Lock waits** were on tenant `workspaces` rows only: p95 16 waiters (max 45), each waiting p95 0.69 s (max 4.2 s). The holders were almost all API admissions, active or idle in transaction.
- **Where the time went** (minutes 0–24.5, from the run's own tables):
  - A slot was held 3.6 s per attempt (p50; p95 6.2 s), so about 11 of the 25 installation slots were in use.
  - The single sweeper was busy all of the time at about 3 reservations a second, about 330 ms each, against about 10 needed (5 cases a second, two reads each).
  - It used little CPU in that window, so it was mostly waiting on those rows.
- **Then the queue collapsed.** At minute 24.5, queue age reached the 1,000 s deadline.
  - Every head then arrived expired, and `reserve_attempt` refused it `forbidden`. The scheduler recorded that as `authority_revoked`: 414 executions were labelled that way, though nothing was revoked.
  - The pass ended at the first refusal, so scheduling stopped for all 8 environments until the run ended.
  - Expired executions were failed within about a second of their deadline (mean), at the arrival rate.
- **Criteria.**
  - Failed: queue age, burst recovery and work in the load phases.
  - Passed: admission p95, and one provider call per attempt.
  - Backlog growth passed only because expiries matched arrivals.
- **Waiting phase.** Not exercised: 2,274 of the 10,000 were admitted (the rest were refused because the pending capacity was full), and none reached its wait in 1,800 s.
  - Restart: workers were ready in 1.5 s; 20 probe cases resumed and completed (p50 56 s); claims after the restart took p50 655 ms.
- **Reading.**
  - The cursor redesign removed the installation-wide wait.
  - Three API processes kept admission under its bound.
  - The engine's ceiling moved to the single sweeper waiting on tenant rows that admissions hold.
  - Overload then took goodput to zero instead of holding it at that ceiling.
  - The owner approved part 6 of the amendment in response.

**Evidence:** `~/Downloads/LotusPond-worktrees/cursor-evidence/full1/`:
- `results.md`;
- `interim-diagnosis.md`;
- `run/load-report.json`;
- `live/`: the logs and table copies taken during the run.
