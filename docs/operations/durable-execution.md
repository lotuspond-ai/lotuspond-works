# Durable execution operations

E04 adds durable execution of admitted executable packages on PostgreSQL ([ADR-0011](../architecture/0011-postgresql-durable-execution.md)). This guide is for the operator of a connected installation.

It does not authorize a production rollout. This release ships no qualified production providers. The API's execution routes that need a provider answer 503 `authority_unavailable`, and a worker with a complete configuration still refuses to start (`engine_unavailable`). Stored evidence stays readable throughout. Each production qualification listed under [Still open](#still-open) must be met first.

## Topology and trust boundaries

| Component | Runs as | Holds | Never holds |
| --- | --- | --- | --- |
| API | `lotuspond-api` image, `uvicorn` (the default command) | Customer routes, the runtime protocol, provider lookups | Worker loops: importing the API starts nothing |
| Orchestrator | The same image: `execution-worker --role orchestrator` | Claims due executions; derives each transition in one transaction; applies capacity steps, including wait retention | Customer authority, provider secrets |
| Sweeper | The same image: `execution-worker --role sweeper`, one per installation | Lapses leases, expires deliveries and waits, applies restore fences and holds, and schedules reserved attempts | Customer authority, provider secrets |
| Runtime | `lotuspond-runtime` on the customer's Linux host | Its E03 credential and session; runs activities it was delivered | Any credential of another pool; any way to choose its work |
| PostgreSQL | One primary | Every execution and business record, in one schema | The recovery epoch, grants or revocations |
| Independent authorities | Outside the database | Recovery epoch, entitlement and price, tenant policy, deployment trust, artifact keys and access, activity trust and signing | — |

- **Separate processes.** Workers and the API are separate processes of the same image. There is no separate engine server and no Temporal component.
- **Workers decide, they don't act.** A worker only advances rows. Every effect runs in a runtime, under a signed, single-use delivery and a short lease.
- **Results are evidence.** A runtime's result is recorded as historical evidence. It never grants authority, and a late or fenced result changes nothing.
- **A restore proves nothing.** Restored rows cannot establish the current epoch, a grant, a revocation or an identity. Those come only from the independent authorities.

## Database roles, grants and timeouts

Run the orchestrator and the sweeper each under its own least-privilege PostgreSQL role, never the migration owner. The API keeps the role it already runs as: the shipped compose files use the database owner (`lotuspond`) for it, and E04 adds no separate API role or grant set. Migrations run only through `lotuspond.initialize migrate` under the owner role. Nothing initializes or resets execution state when a worker starts.

**Worker grants.** The orchestrator and sweeper roles need:

- `SELECT` on the definition tables they read;
- `SELECT`, `INSERT` and `UPDATE` on the mutable execution tables;
- `SELECT` and `INSERT` only on `execution_events`;
- `DELETE` and `TRUNCATE` on nothing.

PostgreSQL also requires `UPDATE` privilege on any table whose rows a statement locks (`SELECT ... FOR UPDATE` or `FOR SHARE`). The engine locks parent rows in its fixed order:

1. workspace;
2. fleet rows;
3. capacity;
4. deployment;
5. execution;
6. step;
7. attempt;
8. reservation;
9. delivery;
10. lease;
11. installation cursor, last, and only by a reservation that takes a compute slot or by scheduling (the sweeper). Its holder runs a few statements and commits; it never waits on another lock (29 October 2026, the [installation capacity amendment](../superpowers/plans/2026-10-29-installation-capacity-lock.md)).

So a role needs `UPDATE` on each table it locks, even where it never changes a row. Most of the tables below were measured: a test-only listener recorded every table each worker role read, wrote or locked across the whole tier 1 conformance suite (see [the validation record](../validation/2026-10-15-durable-execution.md)). The rest cover paths tier 1 never ran, found in the code by the whole-branch review. Those are:

- a capacity step that stops an attempt reserved but never claimed, after a pause or a cancellation;
- authorizing an execution an API key started;
- authorizing in a workspace that requires MFA.

Tier 2's ENG-4 case runs both workers under exactly these grants, including the first two paths. Only the MFA read is derived and not run.

The grants, per role:

```sql
-- The orchestrator role: every table it read, wrote or row-locked in tier 1, then more.
GRANT USAGE ON SCHEMA lotuspond TO lotuspond_orchestrator;
GRANT SELECT ON
  connection_version_secrets, connection_versions, connections,
  deployment_assurance_bindings, deployment_bindings, environments,
  executable_dependencies, executable_publications, execution_artifacts,
  execution_attempts, execution_capacities, execution_deployment_revisions,
  execution_deployments, execution_events, execution_inbox, execution_leases,
  execution_outbox, execution_requests, execution_scheduler_cursors,
  execution_step_instances, execution_usage_reservations, execution_waits, executions,
  memberships, offline_enrollments, projects, runtime_pool_policies,
  runtime_pool_policy_connections, runtime_pools, schema_version, users, workspaces
  TO lotuspond_orchestrator;
GRANT INSERT ON execution_artifacts, execution_attempts, execution_events, execution_requests, execution_step_instances, execution_waits TO lotuspond_orchestrator;
GRANT UPDATE ON
  execution_attempts, execution_capacities, execution_inbox, execution_outbox,
  execution_waits, executions
  TO lotuspond_orchestrator;
-- Locked, never changed: the narrowest grant PostgreSQL accepts for a row lock.
GRANT UPDATE (id) ON
  connections, execution_deployment_revisions, execution_deployments,
  execution_step_instances, runtime_pools, users, workspaces
  TO lotuspond_orchestrator;
GRANT UPDATE (workspace_id) ON memberships TO lotuspond_orchestrator;  -- no id column
-- Paths tier 1 never ran: stopping a reserved attempt frees its reservation and
-- its cursor and capacity slots; an API key's authorization locks the key.
GRANT SELECT ON
  api_keys, execution_activity_deliveries, execution_reconciliation_observations,
  mfa_factors
  TO lotuspond_orchestrator;
GRANT INSERT ON execution_reconciliation_observations TO lotuspond_orchestrator;
GRANT UPDATE ON
  execution_activity_deliveries, execution_usage_reservations
  TO lotuspond_orchestrator;
GRANT UPDATE (id) ON api_keys TO lotuspond_orchestrator;
-- E05 (measured over Task 12's tier 1 action cases): intents, reviews, read-backs and
-- outcomes; decisions, commands and receipts are read only.
GRANT SELECT ON
  execution_action_commands, execution_action_decisions, execution_action_intents,
  execution_action_observations, execution_action_outcomes, execution_action_receipts,
  execution_human_tasks
  TO lotuspond_orchestrator;
GRANT INSERT ON
  execution_action_intents, execution_action_observations, execution_action_outcomes,
  execution_human_tasks
  TO lotuspond_orchestrator;
GRANT UPDATE ON execution_action_intents, execution_human_tasks TO lotuspond_orchestrator;
```

```sql
-- The sweeper role: every table it read, wrote or row-locked in tier 1, then more.
GRANT USAGE ON SCHEMA lotuspond TO lotuspond_sweeper;
GRANT SELECT ON
  connection_version_secrets, connection_versions, connections,
  deployment_assurance_bindings, deployment_bindings, environments,
  executable_dependencies, executable_publications, execution_activity_deliveries,
  execution_artifacts, execution_attempts, execution_capacities,
  execution_context_sources, execution_context_versions,
  execution_deployment_revisions, execution_deployments, execution_events,
  execution_leases, execution_reconciliation_observations, execution_requests,
  execution_scheduler_cursors, execution_step_instances, execution_usage_reservations,
  execution_waits, executions, memberships, offline_enrollments, projects,
  runtime_pool_policies, runtime_pool_policy_connections, runtime_pools,
  schema_version, users, workspaces
  TO lotuspond_sweeper;
GRANT INSERT ON execution_artifacts, execution_events, execution_reconciliation_observations, execution_usage_reservations TO lotuspond_sweeper;
GRANT UPDATE ON
  execution_activity_deliveries, execution_attempts, execution_capacities,
  execution_leases, execution_scheduler_cursors, execution_usage_reservations,
  execution_waits, executions
  TO lotuspond_sweeper;
-- Locked, never changed: the narrowest grant PostgreSQL accepts for a row lock.
GRANT UPDATE (id) ON
  connections, execution_deployment_revisions, execution_deployments,
  execution_step_instances, runtime_pools, users, workspaces
  TO lotuspond_sweeper;
GRANT UPDATE (workspace_id) ON memberships TO lotuspond_sweeper;  -- no id column
-- Paths tier 1 never ran: scheduling authorizes an API key's execution under lock.
GRANT SELECT ON api_keys, mfa_factors TO lotuspond_sweeper;
GRANT UPDATE (id) ON api_keys TO lotuspond_sweeper;
-- E05 (measured over Task 12's tier 1 action cases): task escalation and expiry,
-- action lease lapses and unclaimed command expiry (E03's admission row with them).
GRANT SELECT ON
  execution_action_commands, execution_action_intents, execution_human_tasks,
  runtime_command_admissions
  TO lotuspond_sweeper;
GRANT INSERT ON execution_requests TO lotuspond_sweeper;
GRANT UPDATE ON
  execution_action_commands, execution_action_intents, execution_human_tasks,
  runtime_command_admissions
  TO lotuspond_sweeper;
```

Nothing else is granted: no `DELETE` or `TRUNCATE` on any table, and no `UPDATE` on `execution_events`. The schema name is this installation's.

A path neither tier ran may still need a grant. PostgreSQL then refuses with `permission denied` and logs the statement. A worker logs only a fixed code: `authority_unavailable` (which looks like a provider outage) or `unexpected`. So when workers keep refusing, check PostgreSQL's log first. A refusal on a scheduling path stops scheduling for the whole installation until it is fixed: the batch fails closed. Grant only after review, and record why.

**Timeouts.** Every worker connection sets `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout` (15 s by default, configurable from 100 ms to 15 s), and the worker verifies them at startup. PostgreSQL then ends and rolls back a stalled worker's transaction, and another worker continues the execution. Tier 1's ENG-6 case stops an orchestrator inside a transaction and observes this.

**Configuration.** `deploy/execution/execution-worker.env.example` lists every setting. The database URL accepts only libpq TLS and timeout parameters, and each worker process needs its own worker ID: its session's `application_name` is `lotuspond-<role>:<worker id>`. Start each worker container with `--no-healthcheck`: the image's healthcheck probes the API's port, which a worker never opens.

## Provisioning capacity and the scheduler cursor

Missing setup keeps denying: without these rows, admission and scheduling refuse with `capacity_unavailable`. Provision them explicitly as the migration owner, once per installation and once per environment that may run executions:

```sql
-- One row per installation: at most 25 attempts active across it.
INSERT INTO execution_scheduler_cursors (id, created_at, installation_id, cursor, active, active_limit)
VALUES (gen_random_uuid()::text, now(), :installation_id, 0, 0, 25);

-- One row per environment: at most 5 active attempts, 1,000 pending executions
-- and 10,000 retained waits.
INSERT INTO execution_capacities
  (id, created_at, workspace_id, project_id, environment_id,
   active, pending, retained_waits, next_admission_sequence,
   active_limit, pending_limit, wait_limit)
VALUES (gen_random_uuid()::text, now(), :workspace_id, :project_id, :environment_id,
        0, 0, 0, 1, 5, 1000, 10000);
```

The limits are safety ceilings, not licensing; the schema refuses larger values. Lower a limit to throttle an environment. Never edit the `active`, `pending` or `retained_waits` counters by hand: the engine keeps them in step with reservations and waits.

The installation's live active count is the sum of the environment counts, `SELECT sum(active) FROM execution_capacities`. The cursor row's `active` is a record of that sum as of the last reservation or scheduling transaction, and never below it. It must not exceed `active_limit`, so to lower the installation limit, set both in one statement: `UPDATE execution_scheduler_cursors SET active_limit = :limit, active = (SELECT coalesce(sum(active), 0) FROM execution_capacities)`. This statement is refused while more than `:limit` attempts are active.

## Autovacuum

Execution tables are updated often, and the scheduler cursor and capacity rows are each a single hot row. In tier 3's calibration run, after about 4 minutes of load:

- the cursor row was 83 % dead tuples, and the capacity rows 71 %;
- `executions` was 16 % dead, `execution_waits` 18 % and `execution_attempts` 10 %;
- `execution_events` and `execution_artifacts` had no dead tuples, being insert-only.

Initial settings, to recalibrate from measurements:

```sql
ALTER TABLE execution_scheduler_cursors SET (autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 200, fillfactor = 50);
ALTER TABLE execution_capacities        SET (autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 200, fillfactor = 50);
ALTER TABLE executions                  SET (autovacuum_vacuum_scale_factor = 0.02, fillfactor = 80);
ALTER TABLE execution_attempts          SET (autovacuum_vacuum_scale_factor = 0.02, fillfactor = 80);
ALTER TABLE execution_waits             SET (autovacuum_vacuum_scale_factor = 0.02, fillfactor = 80);
ALTER TABLE execution_leases            SET (autovacuum_vacuum_scale_factor = 0.02, fillfactor = 80);
ALTER TABLE execution_activity_deliveries SET (autovacuum_vacuum_scale_factor = 0.02, fillfactor = 80);
ALTER TABLE execution_usage_reservations SET (autovacuum_vacuum_scale_factor = 0.02, fillfactor = 80);
ALTER TABLE execution_events            SET (autovacuum_vacuum_insert_scale_factor = 0.05);
```

These are storage parameters, applied by the migration owner. They change no row and no schema version.

## Thresholds

Act when a threshold is measured, not assumed. Tier 3 calibrates these; the values are initial.

| Signal | Initial threshold | Action |
| --- | --- | --- |
| `execution_events` rows, or its insert-triggered vacuum | Over 50 M rows, or vacuum over 1 hour | Review partitioning by execution creation month. Converting a populated table is a reviewed, data-preserving migration |
| Execution tables' share of database storage | Over 30 % or 200 GB | A retention policy must exist before growth continues. It never deletes the events of a retained execution |
| `executions` rows | Over 100 M | Batch retention; partitioning a foreign-key target needs its own review |
| Projection p95 at 50 events per execution | Over 100 ms | Investigate plans; never index event payload columns |
| Claim p95 at target load, or execution-table WAL | Over 50 ms, or over 50 % of measured primary capacity | ADR-0011 revisit trigger |
| Lock wait on `execution_scheduler_cursors` at target load | p95 over 100 ms (proposed) | Review Task 5's installation-wide serialization |

**Measured on 27 October 2026, locally.** The calibration run already exceeded the cursor-wait threshold at a fraction of the planning target: p95 6.5 s, with 21–22 waiters at every sample. Until that review, do not plan for the planning-target load; the [validation record](../validation/2026-10-15-durable-execution.md) has the numbers. The sampler in `scripts/checks/execution-load.py` shows how each signal can be read from `pg_stat_activity`, `pg_locks` and `pg_stat_user_tables`.

## Backups and restore

Back up the whole database: execution and business records together, from one consistent snapshot (`pg_dump -Fc`, or a physical base backup with WAL). Back up the independent authorities' state separately, under their own custody.

To restore (the procedure tier 2's RESTORE-1 to RESTORE-3 cases follow):

1. **Fence.** Stop every orchestrator, sweeper and API process. Runtimes may stay up; they will be refused.
2. **Restore** the database from the backup.
3. **Advance the epoch.** Have the RecoveryAuthority record a new recovery epoch. Do this before any worker starts. Until the authority answers, orchestration, sweeping and dispatch refuse, and only reads and exports work (RESTORE-3).
4. **Start** the API, then the workers. A worker refuses to start if its first fenced cycle fails, so start them only once the authority answers.
5. **Every restored execution is held** (owner decision, 2026-10-28). Nothing in the restored rows proves what ran after the backup. So every execution admitted before the restore and not yet ended is held for reconciliation, and none of its attempts reaches a runtime until its owner releases it. Executions admitted after the restore are not held.
   - The sweeper holds them on its first pass, or the first orchestrator to meet one does. Before that, scheduling, dispatch and reservation retirement already skip them.
   - A started execution becomes `reconciliation_required`. One still `pending` stays `pending` and keeps its admission slot. Its event list gains `reconciliation_required`.
   - Its attempts that the restored rows show reserved and undelivered become uncertain. So do those dispatched or claimed at backup time.
   - Nothing adopts an old-epoch result as success.
6. **Reconcile the runtime journals, then release.** Before restarting a runtime that ran after the backup, read its journal directory. It holds one `attempt-<id>.json` file per attempt, with its phase, never a value. Then decide for each held execution:
   - **No journal holds any of its attempts:** release it with `POST /executions/{id}/reconciliation` (owner or admin). Its undelivered attempts are reserved again and run once under the new epoch, and a wait resumes.
   - **A journal shows that one of its attempts, undelivered in the restored rows, started or finished:** that work must not run again. Cancel the execution (`POST /executions/{id}/control`, `cancel`), then release it. The attempt is cancelled before any dispatch, and the execution ends `cancelled`. The run's usage is in the journal only; no route records it yet (see [Still open](#still-open)).
   - **A journal holds an attempt the restored rows don't know:** some execution of that runtime's environment progressed after the backup, and the journal can't say which. Cancel, then release, the held executions there that may have run it. Or release them, accepting that a step may run twice: in this release that means a second read or model call, which costs money and repeats data exposure but writes nothing to a business system.
   - **An attempt delivered under the older epoch** stays uncertain, as after a lapse. Its execution's release is refused with 409 `conflict` until its compute stop is confirmed, and no route accepts that evidence yet. The execution keeps its compute slot and its reserved liability (see [Runbooks](#runbooks)).
7. **Resubmit.** Admissions and signals accepted after the backup are gone. Clients resubmit them under their original idempotency key or signal ID, and each is accepted once.

Old-epoch deliveries and runtime sessions are refused. A runtime revoked after the backup cannot return: its E03 generation and the epoch refuse it (RESTORE-2). Waits keep their absolute deadlines.

## Interpreter coexistence during upgrades

An execution pins its interpreter version at admission.

- **Starting a worker.** A worker refuses to start while a non-terminal execution pins a version its build lacks. A build that serves `v1` and a later version can run beside one that serves only `v1`.
- **Upgrading.** Deploy workers that serve both versions first, then admit work to the new version.
- **Retiring a version.** Remove it only once no non-terminal execution pins it (ENG-7).
- **Pinned revisions.** An execution pins its deployment revision too. Activating a new revision changes only later admissions; executions already admitted finish on their own revision (spike 29–32).

## Runbooks

- **Orchestrator outage.**
  - Executions stop advancing, and nothing is lost.
  - Admissions, signals and reads still answer.
  - Restart the workers; each resumes from rows. Tier 1's spike 1–7 cases kill every process mid-flight and observe one provider call per attempt afterwards.
- **Backlog.** Watch due executions (`runnable_at_ms` at or before now) and reserved attempts without a usage reservation. If the backlog grows:
  - check lock waiters by relation: `pg_locks` entries not granted, attributed to the relation of their tuple lock, as the sampler in `scripts/checks/execution-load.py` does. Waits on `workspaces` are one tenant's own serialization; waits on `execution_scheduler_cursors` should stay brief, since it is held only at a transaction's end;
  - check the API and worker processes' CPU;
  - add orchestrators only when they are the bound; keep one sweeper per installation;
  - lower environment limits to shed load.
- **Lapsed leases.**
  - A lease lapses when a runtime stops renewing it (lost connection, stopped process). The sweeper marks the attempt uncertain and its execution `reconciliation_required`. The attempt is never re-leased, and a late result is evidence only.
  - The execution then holds its compute slot and its reserved liability. Cancellation and the deadline wait for the attempt to be contained, so they do not end it either.
  - Reconciling needs the runtime journal's evidence of stop and usage. No route accepts that evidence yet, so `POST /executions/{id}/reconciliation` refuses with 409 `conflict`. Five lapses in one environment exhaust its default compute limit. Watch `reconciliation_required` executions per environment (see [Still open](#still-open)).
  - The conformance cases reconcile in the test process instead. There, the step fails with the uncertain record; no new ordinal runs (spike 8–10, DISC-2).
- **Runtime exits.** Runtime 0.2.0 ends its process (exit 3, `unavailable`) when a control call is refused or takes more than 10 s, and leaves restarting it to the host's service manager. Run it under one that restarts it, such as a systemd unit with `Restart=on-failure`. A restarted runtime first waits out its previous session lease. Tier 3 saw every runtime exit this way under load.
- **Uncertain usage.** An uncertain attempt keeps its reservation as uncertain liability, never zero, until reconciliation records stop and usage from the runtime journal. Uncertain attempts are never retried automatically.
- **Forward repair.**
  - Repair forward with a reviewed, additive migration.
  - Never delete or rewrite execution events, and never reset a stuck execution's rows: fence it for reconciliation.
  - Preserve every uncertain record.

## Artifact custody and retention

Inputs, results, signals, event contents and model and tool values are stored as encrypted artifacts. Events carry metadata and an artifact reference, never the payload. The key provider holds the keys, outside the database; back them up and rotate them under that provider's custody.

- **Access.** Access is checked on every read, against current source access.
- **Retention.** Each artifact records its classification and retention time. Retention never removes the events of a retained execution, and the plan's thresholds above say when a retention policy must exist.
- **Tested privacy.** Tier 1's privacy case decodes every event artifact and finds no private sentinel outside its authorized encrypted artifact.

## Still open

Each of these must be qualified before production use. Every goal remains in the completion register.

- The actual customer workflow.
- Real read and model providers. This release ships synthetic test-mode providers only, and the runtime's activity worker refuses outside test mode.
- Signed commercial issuance: entitlement, price and tenant-policy providers.
- E05/E06 effects and verified outcomes.
- The agent-first E07 builder and operations experience. No UI or engine-console parity is implied.
- Enterprise, topology, security, capacity and service-level evidence. Hosted CI has not run this work, and tier 3 measured an application-level serialization limit far below the planning target.
- Production wiring of the API's single in-flight authority lookup (Task 12's recorded limitation).
- A way to reconcile an uncertain attempt: a route, or a runtime report, that carries the journal's evidence of stop and usage. Until then:
  - a lapsed or restore-fenced attempt holds its execution's slot and liability, as described under [Runbooks](#runbooks);
  - a held attempt that a journal shows ran can only be cancelled, and its usage goes uncharged ([Backups and restore](#backups-and-restore), step 6).
