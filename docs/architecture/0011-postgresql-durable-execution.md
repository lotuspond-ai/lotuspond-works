# ADR-0011: Durable execution on a PostgreSQL-backed state machine

Date: 2026-10-22. Status: **accepted. The owner confirmed on 2026-10-22 that E04 uses a Lotuspond-owned PostgreSQL state machine, not Temporal.**

A local draft of this record, dated 15 October 2026, recorded the owner's acceptance after the [durable-engine spike](../plans/2026-10-15-durable-engine-spike.md). It was never published. Meanwhile the E04 specification merged in PR #10 selected Temporal. The 22 October confirmation resolves that conflict in favour of this record.

Three parts of the text below revise the draft to fit the contracts already merged on `origin`. Decision 2's scope now follows executable-v2. Decision 3 makes the row lock the orchestration fence. Decision 6 settles packaging. The owner accepted these revisions on 22 October 2026 by approving Revision 2 of the [revised E04 plan](../superpowers/plans/2026-10-15-durable-execution.md). No engine code, schema v15 or dependency exists yet.

This record:

- supersedes the Temporal recommendation in the [architecture proposal](../plans/2026-10-13-platform-architecture.md) §1 item 4;
- supersedes the engine recommendation of the [engine probe](../plans/2026-10-14-durable-engine-probe.md);
- supersedes the engine-choice section of the [E04 specification](../superpowers/specs/2026-10-15-durable-execution-design.md) as merged in PR #10;
- resolves [ADR-0009](0009-agent-automation-platform.md) target boundary 2.

## Context

E04 needs durable execution. That means restart during long waits, duplicate admission, typed control flow, timers and external or human waits, pause and cancellation, retries, pinned versions, bounded agents, budgets and fair capacity (the E04 row of the [completion design](../superpowers/specs/2026-10-14-product-completion-design.md)). Two efforts on 14–15 October reached different engine choices.

**The probe and PR #10 chose Temporal.** The approved engine probe of 14 October ran Temporal server 1.32.1 against a separate SQLite authority simulator and recommended Temporal. The E04 specification and plan (PR #10) adopted Temporal server 1.32.1 and Python SDK 1.34.0 behind a narrow adapter. Tasks 1–6 of that plan are implemented on draft PR #11: schema v14, publication, artifacts, budgets, admission and signals. None of them imports an engine.

**The local spike chose PostgreSQL.** On 15 October it ran Temporal and a minimal PostgreSQL state machine against the same example, synthetic target and injected faults. Both passed every criterion: PostgreSQL passed 35/35 checks in each of five runs, and Temporal 18/18 in each of the last three.

Capability did not separate them; ownership and operations did. These observations weighed against Temporal for this product:

- It adds a second durable store of execution state beside product PostgreSQL.
- A workflow ID started with a different payload silently returned the existing run.
- Under the default ID-reuse policy, a late duplicate started a second run.
- An interpreter change made replay of a waiting execution fail with `NON_DETERMINISTIC_ERROR` until the change was patched.
- Workflow inputs and step outputs are stored as plain JSON in engine history.
- Temporal's documentation requires upgrading the server "sequentially, one minor version at a time" with `temporal-sql-tool` schema upgrades, and fixes the history-shard count at first start ([upgrade guide](https://docs.temporal.io/self-hosted-guide/upgrade-server), [configuration reference](https://docs.temporal.io/references/configuration)).

Each of these becomes part of every customer-managed and air-gapped installation, upgrade and restore.

The spike's evidence has two limits:

- **Vocabulary.** It ran the withdrawn [ADR-0010](0010-workflow-package-v2-graph-proposal.md) prototype, which is sequential and acyclic. Neither engine ran executable-v2's parallel all-join, bounded iteration, pinned subworkflows or timers.
- **Process split.** One process did both orchestration and effects. Production splits them (decision 5).

## Decision

1. **Engine.** E04's durable orchestration is a Lotuspond-owned state machine. Its state is rows in product PostgreSQL, advanced by orchestrator worker processes inside the control plane. Admission, waits, signals, attempts, budgets and events commit in the same database as the business records, and in the same transaction where they must. The schema-v14 records from Tasks 1–6 are the engine's state. Schema v15 adds only the claim marker and sweep indexes the engine needs.
2. **Scope.** The engine interprets only the compiled plan of an admitted executable-v2 package, under a pinned interpreter version:
   - Supported steps: typed transforms, decisions, parallel all-join, bounded iteration, pinned subworkflows, timers, authenticated external waits, typed human-information waits (form mode), connector reads and bounded agents.
   - Contract limits apply: at most 128 authored steps, control nesting of 8, iterations of 1,000 items and an expansion of 10,000 steps.
   - Effect nodes (action intent, action review, action dispatch and outcome verification) are refused at deployment admission until E05/E06 supply them.
   - Anything outside the contract needs a new contract version, a new interpreter version and matching conformance tests.
3. **Invariants.** Each becomes a conformance test:
   - **Admission.** The key is unique per scope and idempotency key, plus a request digest. The same key and payload return the original execution; a changed payload conflicts. This is already implemented in Task 6.
   - **Pinning.** At admission an execution pins its deployment revision, package, plan and configuration digests, and interpreter version. Upgrades affect new executions only, and a worker advances only executions whose interpreter version it implements.
   - **The row lock is the orchestration fence.** A transition is computed and committed inside the transaction that holds the execution row `FOR UPDATE SKIP LOCKED`, and no network call happens inside it. If a worker crashes or stalls, its transaction rolls back or is ended by the statement, lock and idle-in-transaction timeouts on its database role. Tokens and expiries (leases) appear only where work spans transactions: runtime activity attempts.
   - **Waits hold no worker.** A timer, external wait or human-information wait is a row with an absolute deadline. A sweeper finds due rows by index, using database time.
   - **Activities.** A runtime executes every read and model call as an attempt, through the signed, leased `lotuspond.runtime-activity.v1` protocol. Lease expiry never makes an attempt reclaimable: the attempt becomes uncertain and is reconciled. A retry is a new attempt ordinal under fresh authority and a fresh budget reservation.
   - **Control.** Cancellation and pause stop new dispatch at the authority transaction boundary. In-flight work receives cooperative cancellation and is reported as it actually ends.
   - **History.** An append-only execution event log is the audit trail and the source of projections. It carries only codes, identifiers, digests and encrypted artifact references. Replaying it reproduces the projected state.
   - **Restore.** One database restore covers execution state and business records together. The current recovery epoch, revocations and identities come from the independent RecoveryAuthority, never from restored rows.
4. **Adapter boundary.** Orchestration sits behind a narrow internal interface, `ExecutionEngine`, with progress, sweep, describe and interpreter-check operations. Beside it are the engine-neutral services of Tasks 3–6 (publish, deploy, admit, signal) and of Tasks 9–12 (dispatch, control, reconcile, project). No engine type appears in public APIs or package contracts. The conformance suite drives only those services and the runtime HTTP protocol, so any replacement engine must pass the same suite.
5. **Trust placement.** Only the API, the orchestrator and the sweeper hold product-database credentials, and they run inside the control-plane boundary. The orchestrator and sweeper use a least-privilege role whose grants cover execution tables only; their access to events is append-only. Runtimes never receive database credentials. They claim work over the authenticated activity protocol with their E03 identity.
6. **Dependencies.** There is no new production dependency: `psycopg` and SQLAlchemy are already backend dependencies. The orchestrator and sweeper ship as a worker command of the API distribution; no separate orchestrator distribution or engine server is built. Temporal is not adopted, and nothing from the spike is copied into the product.

## Alternatives considered

- **Temporal, self-hosted (PR #10's selection).** It passed every spike criterion and the probe's restart, restore and replay experiments. It was not chosen for three reasons:
  - **Dual state authority.** Every admission and effect needs outbox, projection and reconciliation-by-ID machinery between two stores.
  - **Per-installation operations.** It brings a server, persistence and visibility schemas, sequential upgrades, a fixed shard count and a payload codec for sensitive history.
  - **Replay determinism.** A generic interpreter needs replay-determinism versioning.

  Its SDK and server are MIT-licensed, so licensing did not decide. Temporal remains the named fallback behind the adapter.

- **Temporal Cloud.** Not evaluated. It cannot serve air-gapped installations, and it moves history outside the customer's data boundary.
- **PostgreSQL-backed libraries.** On 15 October a documentation-only review of about an hour covered DBOS Transact 3.2.0, Procrastinate 3.10.0, PGQueuer 1.6.0 and Absurd 0.5.0. Nothing was installed or run. None supplies the parts that carry these invariants:
  - DBOS, the only real candidate, has a library-owned schema and migration chain outside `initialize.py`, global workflow IDs without tenant columns, version-hash recovery that needs patches, and a pickle default serializer.
  - Procrastinate and PGQueuer are queues that would duplicate the record of runnable work.
  - Absurd is alpha software, and its logic lives in vendored stored functions.
- **A queue as the execution record (Redis, Celery or similar).** Rejected by the architecture.
- **Hatchet, Inngest, Restate and cloud step-function services.** Not evaluated, because each needs its own server.

## Consequences

- **Engine code.** Lotuspond owns code that Temporal would otherwise provide: claim and fencing, the sweeper for timers, deadlines and lapsed leases, retry backoff, interpreter versioning and the event log. The spike's engine was about 700 lines for sequential packages only. Executable-v2's regions need considerably more, and the conformance suite, not spike evidence, establishes that their behavior is correct.
- **Restore.** One backup and one restore cover execution and business state, so the probe's problem of restoring two stores coherently disappears. Activity attempts in flight at backup time still reconcile, and the independent RecoveryAuthority still fences them.
- **Schema.** The v14 records need one more reviewed additive migration (v15). Event growth, vacuum, partitioning and polling load become product operations work; the revised plan records the thresholds.
- **Leftovers in Tasks 1–6.** They contain elements shaped for Temporal: an unused `engine_identity` column, outbox kinds named `engine_start` and `engine_signal`, a two-transaction outbox lease and acknowledgement protocol, and the reconciliation code `engine_missing`. They remain valid and are reinterpreted rather than removed; the revised plan lists each one.
- **Scaling risk.** Schema v14 serializes admission, attempt reservation and signal acceptance on one installation scheduler-cursor row, whatever the engine. The load test must measure that contention. Task 13's tier 3 measured it far over its threshold (finding 11). The owner-approved follow-up of 29 October 2026 removes that installation-wide serialization without a schema change: the [installation capacity amendment](../superpowers/plans/2026-10-29-installation-capacity-lock.md) takes the cursor last, only for compute slots and scheduling.
- **Unmeasured throughput.** Throughput at the planning targets has not been measured: 5 admissions per second, 25 active steps and 10,000 waiting cases.

## Revisit triggers

Reconsider Temporal, or another engine behind the same adapter, if any of these becomes true:

- the contract adds unbounded loops, race or quorum joins, fan-out beyond executable-v2's bounds, or general long-running timers;
- measured load approaches the limits of a single PostgreSQL primary for execution tables;
- deriving progression at the 10,000-step expansion ceiling exceeds its measured per-transition bound;
- durable waits or timers dominate operator incidents;
- a customer requires an existing Temporal estate.

## Evidence required before E04 is reported complete

- A conformance suite that runs in CI against isolated PostgreSQL, covering the spike scenarios that apply to E04 plus new cases for parallel, iteration, subworkflow and timer regions.
- Revocation, disconnected-runtime, restore-from-older-backup and tenant-isolation cases.
- A load test at the planning targets on recorded hardware.
- The control-plane/runtime split implemented and tested with installed artifacts.

Spike and probe results remain design evidence only.
