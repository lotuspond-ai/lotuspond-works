# Actions and Human Tasks (E05) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admitted workflow propose an exact consequential action, have an eligible independent person review it, send it at most once under current authority, settle uncertain sends by read-back, and verify the outcome, all against a synthetic target.

**Architecture:** E04's PostgreSQL engine gains row-locked transitions for intents, review and form tasks, dispatch, read-back and verification ([ADR-0011](../../architecture/0011-postgresql-durable-execution.md)). The API signs E03 `RuntimeCommand`s at runtime poll time and admits them through E03's `CommandService` in the same transaction. Runtimes claim them under a short action lease, journal before writing, and report receipts over dedicated runtime routes. Read-backs and verification reads are E04 connector-read activities.

**Tech Stack:** Python 3.13/3.14, SQLAlchemy/PostgreSQL (psycopg), FastAPI, existing Pydantic/rfc8785/cryptography contracts, E03 runtime identity and command signing, E04 engine. No new dependency and no SDK change.

**Spec:** [E05 specification](../specs/2026-10-26-actions-human-tasks-design.md). Read it with this plan. Prepared 26 October 2026 (Asia/Kolkata). **Status: approved by the owner on 29 October 2026, with decisions 1 and 13 restated; not implemented.** The owner's selected subagent execution, with independent specification and quality review after each task, applies.

## Preconditions and base

E05 changes files that E04 Tasks 7–12 created, and its conformance suite reuses Task 13's harness. E04 Tasks 7–14 merged to `main` on 29 October 2026 (PR #17, `533b3d6`). The redesign for E04 finding 11, the installation cursor, changes the lock order, `schedule_batch` and the harness in those same files. Its proposal, `docs/superpowers/plans/2026-10-29-installation-capacity-lock.md` on `claude/e04-cursor-redesign`, was approved on 29 October with the owner's amendment. Implementation therefore starts from `main` after that redesign merges (owner decision 1, restated). Each task names E04 files by their path on `main`; an implementer re-reads them at the actual base before starting. If the redesign's final review changes an interface named here, the affected task is re-planned for the owner's acknowledgement rather than silently adapted.

E04 items this plan depends on:

- **The single-call authority lookup (E04 finding 3, Task 12's "known limitation").** E04 Task 14 did not change it: each API process still makes one call per provider at a time. The redesign's Part 4 makes a concurrent caller wait for the slot within the call's 5 s bound instead of refusing at once. Owner decision (29 October 2026): Part 4 is enough to start E05, and Task 12 measures the lookup waits that E05's decisions, claims, renewals, dispatches and receipts add.
- **E04 Task 13's fixtures.** Required mode, owned clusters, the TCP proxy and the database-time clock are on `main` under `backend/tests/execution/conformance/`, and the redesign extends them. E05 reuses them as the redesign leaves them.

E04 findings still open for the owner that E05 meets:

- **Finding 2: a listed-open wait can refuse its signal.** Between the transaction that opens a wait and the capacity step that retains it, the wait is listed but a signal is refused with 403. v1 forms keep that gap (decision 8). E05's tasks are retained waits opened the same way, so Task 5 needs the owner's choice for finding 2 before it starts, and its decision and submission routes follow the same rule as E04's signals.
- **Finding 21: no route settles an uncertain attempt with evidence.** An uncertain E04 read or model attempt keeps its slot and liability, and five fill an environment's default limit. E05's resolution route (Task 8) settles action attempts only. An uncertain E04 read in the same execution still has no route until the owner decides finding 21.
- **Finding 24: a signal accepted after an immediate restart was not applied for 120 s, once.** Task 12's tier 2 restart cases with reviews may meet it. They record the same evidence on failure as E04's case does.

## Global Constraints

- “No real model or tool provider exists.” Every effect in E05 is against a synthetic target. Fixed synthetic connectors, providers and the synthetic target are constructor-injected test fixtures, unselectable in production. Nothing claims live-provider evidence.
- “Current revocation wins over historical pinning, and a missing provider denies.” Every decision, dispatch, claim, renewal and receipt rechecks authority after acquiring its locks, using database time.
- “Approval alone is never enough.” A changed target, input, connection, policy, configuration, package, epoch or initiator authority refuses before any command exists.
- “An uncertain write never schedules a blind replacement.” A resend needs a qualified absence, `idempotency: target_key` and retry attempts left; it reuses the same intent and idempotency key under a new command.
- “A model assertion, a transport acknowledgement or missing evidence never sets `observed` or `verified`.”
- E03 command bytes, E04 activity bytes, SDK bytes, v1 registry behaviour and interpreter v1 semantics are unchanged. V1 golden progression fixtures must re-derive byte-identically.
- Commands live at most 300,000 ms. Action leases last at most 60,000 ms and are renewed at most every 20,000 ms. Claims on human tasks last 900,000 ms. At most 5 automatic read-backs run per uncertain write. All are bounded by the attempt, task, intent and execution deadlines.
- E04's ceilings stand: 25 active attempts per installation and 5 per environment, 1,000 pending executions and 10,000 retained waits per environment. After finding 11, the installation's count is the sum of its environments' counts. Tasks are retained waits.
- No destructive downgrade, and the empty-database initializer is never an upgrade. Advance exact schema v15 to v16, preserving all data.
- Local synthetic fixtures qualify no real provider, connector, workload, customer outcome, HA, RPO/RTO or service availability. No merge, push, customer connection, production database, public distribution or deployment is authorized by this plan.

## Review Focus

1. A reviewer approves, then the target, input, connection, policy, initiator permission or the reviewer's own role changes before dispatch: no command is admitted and nothing is written (Tasks 1, 4, 6, 10, 12).
2. A runtime dies or disconnects after the target applied a write but before its receipt reached the API: read-back observes it and nothing is resent. A request lost before the target is resent once, under the same idempotency key, only when absence is qualified (Tasks 6–8, 12).
3. Two reviewers, the requester or an API key race to decide one task: one claim, independence and the current role hold after lock waits (Tasks 5, 11, 12).
4. A database is restored from before a write or a revocation: older-epoch intents are held and read back, and no write repeats and no restored permission authorizes one (Tasks 6, 10, 12).
5. Proposal values are private: only the authorized proposal route returns them, and decoded events, commands, receipts, logs and errors carry digests only (Tasks 4, 7, 11, 12).

## Owner decisions

On 29 October 2026 the owner accepted all 18 recommended options, with two restated: "Accept all 18 E05 decisions as recommended, with 1 restated (start from main after finding 11 merges) and 13 restated (read back older-epoch intents at the owner's release under E04's hold)." Rows 1 and 13 below give the restated option, then the original proposal. The owner also decided the lookup precondition: "Treat the redesign's Part 4 as enough to start, with Task 12 measuring lookup waits."

| #   | Decision                                    | Recommended option                                                                                                                                                                                                                                                                                                                                                                      | Alternative                                                                                                                                                                                                | Affects  |
| --- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| 1   | When implementation starts                  | **Restated:** after the finding 11 redesign merges to `main`; E05 cuts a fresh branch from that `main`. (Proposed: after E04 Tasks 12–14 merge, which they did on 29 October as PR #17.)                                                                                                                                                                                                | Stack on `claude/e04-cursor-redesign` before it merges, accepting rebases whenever its review changes                                                                                                      | All      |
| 2   | Outcome verification in E05 or E06          | E05 executes `outcome_verification` steps and records SDK `OutcomeRecord`s, because the compiler refuses any dispatch without one; E06 keeps cases, business outcomes, exception ownership and export                                                                                                                                                                                   | Defer to E06; then no effect package is admissible until E06                                                                                                                                               | 3, 9     |
| 3   | Unreviewed actions (`approval: none`)       | Admit them as the pinned policy declares; deployment detail lists every operation that would dispatch without review                                                                                                                                                                                                                                                                    | Refuse `approval: none` in E05, so every write needs a review                                                                                                                                              | 3, 4, 6  |
| 4   | Reviewer groups                             | Closed role vocabulary `workspace_owners`, `workspace_admins`, `workspace_editors`; viewers, keys, runtimes and platform admins never decide                                                                                                                                                                                                                                            | New named reviewer-group records, which belong with E11 governance                                                                                                                                         | 3, 5     |
| 5   | Escalation                                  | `after_half_timeout` only: at half the task timeout the escalation group becomes eligible and any claim is released; nothing is delivered                                                                                                                                                                                                                                               | Keep refusing escalation (`unsupported_policy`) until a notification channel exists                                                                                                                        | 3, 5     |
| 6   | Claims                                      | Explicit claim of 15 minutes, renewable and releasable; a decision or submission needs the actor's live claim                                                                                                                                                                                                                                                                           | No claims; the first valid decision wins                                                                                                                                                                   | 5, 11    |
| 7   | A reviewer loses their role after approving | The decision stops counting: dispatch refuses with `approval_revoked`, and the execution fails without a write                                                                                                                                                                                                                                                                          | A decision stands once accepted                                                                                                                                                                            | 6, 10    |
| 8   | Form tasks                                  | Forms in executions pinned to interpreter v2 use the same task records, assignment, claim, expiry and escalation; v1 executions keep E04's form signals                                                                                                                                                                                                                                 | Review tasks only; forms stay E04 signals                                                                                                                                                                  | 3, 5, 11 |
| 9   | Action delivery                             | E03's signed `RuntimeCommand` path plus an E05 action lease, with lapse leading to read-back                                                                                                                                                                                                                                                                                            | E03 commands without a lease, so uncertainty is detected only at the step timeout (up to 24 hours)                                                                                                         | 6, 7     |
| 10  | Automatic resend after absence              | Only after a qualified absence, for operations declaring `idempotency: target_key`, with retry attempts left, reusing the same intent and idempotency key                                                                                                                                                                                                                               | Never automatic; an owner or admin decides every resend                                                                                                                                                    | 8        |
| 11  | Read-backs that stay unknown                | At most 5 automatic read-backs within the attempt deadline, then `reconciliation_required` for an owner or admin                                                                                                                                                                                                                                                                        | Unbounded read-backs until the execution deadline                                                                                                                                                          | 8        |
| 12  | Cancellation with dispatched writes         | In-flight writes finish and are recorded; read-backs of uncertain writes continue; declared verification steps do not run                                                                                                                                                                                                                                                               | Stop all reads at cancellation, leaving uncertain writes unknown                                                                                                                                           | 8, 10    |
| 13  | After a restore                             | **Restated:** E04's restore hold applies unchanged, and no read-back runs while an execution is held. At the owner's release, read back every unsettled older-epoch intent under the new epoch; present → observed; qualified absence → `recovery_epoch_changed`, with no automatic re-proposal; unknown → decision 11. (Proposed: hold and read back automatically after the restore.) | Let read-backs run during the hold, an exception to E04's rule that nothing of a held execution reaches a runtime before its release; or re-propose automatically under the new epoch, with a fresh review | 10, 12   |
| 14  | Compensation                                | Refuse packages that declare it (`unsupported_step`) and settle its trigger, budget and terminal-state semantics in a follow-up specification                                                                                                                                                                                                                                           | Implement owner- or admin-requested compensation flows on terminal executions now, adding about two tasks                                                                                                  | 3        |
| 15  | Interpreter version                         | A new `lotuspond.execution-interpreter.v2`, pinned only by plans that use E05 features                                                                                                                                                                                                                                                                                                  | Extend v1 in place, which E04's rule forbids unless v1 fixtures and executions are provably unaffected                                                                                                     | 3, 4     |
| 16  | Synthetic target                            | A separate test-only process over loopback HTTPS, reached only through E03's `DestinationTransport` under a test egress policy                                                                                                                                                                                                                                                          | An in-process store, which leaves the first real provider's network wiring untested                                                                                                                        | 7, 12    |
| 17  | Denial and expiry semantics                 | Accept the v2 contract: a denied or expired review fails the execution with a fixed code; record decision branching as a later contract candidate                                                                                                                                                                                                                                       | Specify an executable-v3 contract with decision branching before E05                                                                                                                                       | 4, 5     |
| 18  | Reconciliation read input                   | Call the reconciliation read with the intent's exact target and input; deployment refuses a write whose read schemas do not accept them                                                                                                                                                                                                                                                 | Extend the catalog contract with explicit reconciliation mappings first                                                                                                                                    | 3, 8     |

---

## File ownership and task sequence

| Task | Exact ownership (brace lists expand to individual files)                                                                                                                                                                                          | Responsibility                                                     |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 1    | `backend/lotuspond/execution/action_contracts.py`                                                                                                                                                                                                 | Inert action contracts, closed vocabularies and derivation vectors |
| 2    | Existing `models.py`, `initialize.py`, `schema_version.py`, `backend/schema.sql`, `execution/migration.py` (v16 step only)                                                                                                                        | Schema v16                                                         |
| 3    | `backend/lotuspond/execution/action_policies.py`; existing `execution/{publication,deployments,admission,interpreters,program}.py`                                                                                                                | Admitting effect packages under interpreter v2                     |
| 4    | `backend/lotuspond/execution/actions.py`; existing `execution/progression.py` (intent transitions only)                                                                                                                                           | Intents as row transitions                                         |
| 5    | `backend/lotuspond/execution/human_tasks.py`; existing `execution/{inbox,progression,sweeper,authorization}.py` (task paths only)                                                                                                                 | Review and form tasks                                              |
| 6    | `backend/lotuspond/execution/{action_dispatch,action_leases,action_records,action_receipts}.py`; existing `fleet/commands.py` (claim hook and refusal only), `execution/budgets.py` (uncertain-parent shared reservation only)                    | Fresh authority, signed commands, leases and receipts              |
| 7    | `runtime/python/src/lotuspond_runtime/{action_protocol,action_client,action_worker,action_invocation,action_fixture}.py`; existing `cli.py`, `supervisor.py`, `control_transport.py`, `activity_state.py` (action records only)                   | Runtime action worker and synthetic target                         |
| 8    | `backend/lotuspond/execution/{action_rules,action_reconciliation}.py`; existing `execution/{progression,program,activity_records,sweeper}.py` (read-back paths only)                                                                              | Read-back, qualified absence and resolution                        |
| 9    | `backend/lotuspond/execution/verification.py`; existing `execution/{progression,activity_records}.py` (verification paths only)                                                                                                                   | Outcome verification and the completion rule                       |
| 10   | `backend/lotuspond/execution/action_projections.py`; existing `execution/{control,recovery,projections,sweeper}.py` (action paths only)                                                                                                           | Controls, revocation, restore and projections                      |
| 11   | `backend/lotuspond/execution/{task_routes,action_routes,action_runtime_routes}.py`; existing `app.py`, `schemas.py`, `execution/boundary.py`, `fleet/runtime_routes.py`                                                                           | Customer and runtime APIs                                          |
| 12   | `backend/tests/execution/conformance/actions/`; `backend/tests/execution/conformance/disruptive/test_action_restore.py`; existing `scripts/checks/{execution-smoke.py,execution-load.py}`, `backend/tests/execution/test_installed_acceptance.py` | Conformance, restore and load qualification                        |
| 13   | CI, packaging, operations and capability documentation listed in Task 13                                                                                                                                                                          | Reproducible delivery and complete evidence                        |

Existing files named without a full path are under `backend/lotuspond/`. Tests live in `backend/tests/execution/actions/` unless a task says otherwise. Each task lists its tests and fixtures. Keep `action_contracts.py` and runtime `action_protocol.py` free of database sessions, environment reads, files, sockets, threads and provider loading.

Tasks run in sequence; review each before the next. Every implementer gets the specification, the frozen contracts below, their task and the interfaces of the tasks it depends on. Do not delegate a whole subsystem with permission to skip task gates. Preserve existing worktrees, evidence and failed attempts. Record each file changed beyond a task's row, with its reason, for the owner's acknowledgement, as E04 did.

## Frozen cross-task contracts

### Vocabulary and states (Task 1)

`action_contracts.py` defines, with no behaviour beyond pure derivation:

- `INTERPRETER_V2 = "lotuspond.execution-interpreter.v2"`.
- `ASSIGNMENT_ROLES = {"workspace_owners": {"owner"}, "workspace_admins": {"owner", "admin"}, "workspace_editors": {"owner", "admin", "editor"}}`. `ESCALATIONS = {"after_half_timeout"}`.
- `TASK_CLAIM_MS = 900_000`, `ACTION_LEASE_MS = 60_000`, `ACTION_RENEW_MS = 20_000`, `MAX_READBACKS = 5`. The command lifetime is the SDK's `COMMAND_LIFETIME_MS` (300,000).
- Intent authorization: `proposed`, `awaiting_review`, `approved`, `denied`, `review_expired`, `expired`, `withdrawn`, `superseded`.
- Intent effect: `not_dispatched`, `dispatching`, `accepted`, `observed`, `unknown`, `absent`.
- Intent outcome: `unknown`, `verified`, `not_verified`.
- Task: kind `review|form`; state `open`, `decided`, `submitted`, `expired`, `cancelled`.
- Action command: `admitted`, `claimed`, `stopped`, `uncertain`, `expired`.
- Observation result: `effect_present`, `effect_absent`, `unknown`. Observation source: `readback`, `restore_readback`, `historical`, `resolution`.
- Resolution kinds: `readback` (another bounded read-back), `compute_stopped` (an owner's or admin's confirmed-stop observation), `effect_present` (citing a read-back result or historical receipt already recorded for this action), `close_unknown` (close without resending; the effect stays `unknown`).
- Attempt failure codes added to E04's: `approval_denied`, `review_expired`, `approval_expired`, `approval_revoked`, `binding_refused`, `not_applied`, `outcome_not_verified`, `outcome_unknown`, `recovery_epoch_changed`.
- Execution reasons added to v15's: `review`, `action_uncertain`, `containment`, and the nine failure codes above.
- No new HTTP error codes. Refusals use E04's closed codes (`forbidden`, `conflict`, `not_found`, `unsupported_step`, `unsupported_policy`, `invalid_request`, the `*_unavailable` family).

Strict DTOs, with unknown fields and coercions refused:

- `HumanTaskView(id, execution_id, kind, state, deadline_ms, escalated: bool, escalate_at_ms: int|None, claim: Literal['none','caller','other'], claim_expires_at_ms: int|None, intent_sha256: str|None)`;
- `ProposalView(intent_sha256, connector_name, connector_version, operation, policy_name, policy_version, approval_mode, independent_approval, requester_id, issued_at_ms, expires_at_ms, target: JsonValue, input: JsonValue)`;
- `DecisionRequest(decision_id: OpaqueId, intent_sha256: Digest, decision: Literal['allow','deny'])`;
- `SubmissionRequest(submission_id: OpaqueId, payload: JsonValue)`;
- `ActionView(action_id, step_id, occurrence_path, intent_sha256, authorization, effect, outcome, task_id: str|None, receipt_status: str|None, evidence: tuple[ArtifactHandle, ...])`;
- `ResolutionRequest(resolution_id: OpaqueId, kind, evidence: ArtifactHandle|None)`.

The runtime shares these shapes through `lotuspond_runtime.action_protocol` (Task 7), never by importing backend modules:

- `ActionLease(attempt_id, command_id, lease_revision, expires_at_ms, deadline_ms)`;
- `ActionReport(attempt_id, lease_revision, receipt: ActionReceipt, response: ArtifactHandle|None, usage: UsageObservation)`;
- `ActionStop(attempt_id, lease_revision, code: Literal['not_started','stopped_unknown'], usage: UsageObservation)`.

### Derivation and literal vectors (Task 1)

`BindingFields` is a frozen dataclass holding exactly the sourced values in the specification's table. `derive_binding(fields) -> ActionBinding` sets `expires_at_ms = min(issued_at_ms + expiry_seconds * 1000, execution_deadline_ms)`. `target_sha256` and `input_sha256` are SHA-256 of the evaluated values' canonical JSON, which equals their artifact digests. `idempotency_key(intent) = intent.binding.action_id`.

`decision_context(intent, now_ms) -> BindingContext` copies the binding's scope, step, action, digests and requester, with `worker_id="control-plane"` and `fencing_generation=0`. The SDK ignores those two fields when no command is checked. `command_context` uses the recipient's instance and generation instead.

`derive_command(intent, decision, recipient, command_id, replay_id, now_ms, attempt_deadline_ms) -> RuntimeCommand` sets `expires_at_ms = min(now_ms + 300000, attempt_deadline_ms)` and refuses unless `expires_at_ms > now_ms`.

Literal vector. Construct UUID `u(n) = '00000000-0000-4000-8000-' + twelve decimal digits of n`. The fields are:

- workspace, project, environment, execution and action IDs `u(1)` through `u(5)`, requester `u(6)`;
- installation `fixture-installation`, epoch 1, step `intent`;
- package, plan and configuration digests `'a'*64`, `'b'*64` and `'c'*64`;
- connector `synthetic-target` `1.0.0`, artifact `'e'*64`, operation `submit`;
- target `{"record_id":"R-100"}`, input `{"amount_minor":75000,"currency":"EUR"}`;
- policy `'f'*64`, `approval_mode` `human`, `independent_approval` true;
- issue `1800000000000`, `expiry_seconds` 3600, execution deadline `1800086400000`.

It gives:

| Value                                                                                                               | Literal                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `target_sha256`                                                                                                     | `1ed920890df5183566108eb5e0f8672654bda98219224f53b442133de4e58c11`                                                        |
| `input_sha256`                                                                                                      | `eaca23342e72e4e14f20729db7d8d45cedae7099f1a060636df4d929ed6d77c0`                                                        |
| Binding JCS                                                                                                         | **1245 bytes**; intent digest **`2c976a61d1ad7c137b0e453862854019b3c47c575f9e6f07303efd6fb0da81bd`**                      |
| Decision `u(7)`, `allow`, actor `u(8)` (`human`), policy `'f'*64`, issue `1800000060000`, expiry `1800003600000`    | **419 bytes**; decision digest **`c4887c888df1e8b9e797445071416630bd39db8fcb3298433c1ddbffef9b4ad6`**                     |
| Command `u(9)`, replay `u(10)`, worker `u(11)` generation 1, issue `1800000120000`, attempt deadline issue + 30,000 | Expiry `1800000150000`; **783 bytes**; wire digest **`678f627d787de45584199fd8aa5906fabb4a85c205b93c9b79c698d59431a9e9`** |

These were calculated during planning with sorted compact ASCII JSON and SHA-256. That method reproduces the SDK's own frozen oracle (`action-binding.jcs`, `e3860ea5…`). They were then cross-checked with the SDK's `canonical_binding_json`, `decision_digest` and `wire_digest`. The SDK also reported `binding_valid` for the decision and the command, `stale_epoch` for the decision under epoch 2, and `independence_required` when the requester decides. Task 1 freezes these as fixture files.

### Records (Task 2)

All rows carry full workspace, project and environment scope with composite foreign keys, as E04's do. Action commands also carry E03's machine scope, so they can reference `runtime_command_admissions`. Bytes are `LargeBinary`, at most 65,536 per canonical record. Timestamps are safe nonnegative integers.

| Record                       | Key columns and constraints                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ExecutionActionIntent`      | Execution and step instance (unique: one intent per occurrence); `action_id` unique in scope; canonical binding bytes and `intent_sha256` (unique in scope); `recovery_epoch`; connection ID and version; target and input artifact (ID, version, digest equal to the binding's); approval mode and independence; requester user; issue and expiry; the three state columns |
| `ExecutionHumanTask`         | Execution, step instance and wait (unique); kind; intent (required exactly for `review`); assignment and escalation assignment; `escalate_at_ms`, `escalated_at_ms`; `deadline_ms`; state; claim user, claim expiry and claim revision (all null or all set)                                                                                                                |
| `ExecutionActionDecision`    | Task (unique: one decision per task) and intent; `decision_id` unique per task; canonical decision bytes and digest; `allow` or `deny`; actor user; issue and expiry, with expiry not later than the intent's                                                                                                                                                               |
| `ExecutionActionCommand`     | Attempt (unique), intent and optional decision; E03 admission (composite machine-scope foreign key) with command ID and digest; lease token digest, `claimed_at_ms`, `renewed_at_ms`, `lease_expires_at_ms` (at most `renewed + 60000`); status                                                                                                                             |
| `ExecutionActionReceipt`     | Attempt and intent; `receipt_id` unique per intent; canonical SDK `ActionReceipt` bytes and digest; status; optional response artifact; source `live` or `historical`                                                                                                                                                                                                       |
| `ExecutionActionObservation` | Intent and optional read-back attempt; `observation_id` unique per intent; canonical SDK `ReconciliationObservedPayload` bytes and digest; result; `qualified`; rule ID and version; source; optional actor user                                                                                                                                                            |
| `ExecutionActionOutcome`     | Intent (unique) and receipt; canonical SDK `OutcomeRecord` bytes and digest; state; rule ID and version; observed time                                                                                                                                                                                                                                                      |

Indexes for the sweeper are global and partial, as v15's are:

- `execution_task_due_idx (deadline_ms, id) WHERE state = 'open'`;
- `execution_task_escalation_idx (escalate_at_ms, id) WHERE state = 'open' AND escalated_at_ms IS NULL AND escalate_at_ms IS NOT NULL`;
- `execution_task_claim_idx (claim_expires_at_ms, id) WHERE claim_user_id IS NOT NULL`;
- `execution_action_lease_due_idx (lease_expires_at_ms, id) WHERE status = 'claimed'`;
- `execution_action_hold_idx (recovery_epoch, id) WHERE effect IN ('not_dispatched','dispatching','accepted','unknown') AND authorization NOT IN ('denied','review_expired','expired','withdrawn','superseded')`.

Unclaimed action commands expire through E03's admission expiry. Task 6's sweeper hook finds them from the action command's `admitted` status, joined by attempt.

### Service and lock contracts (Tasks 4–10)

- **Lock order.** E05 services follow E04's order as the finding 11 redesign leaves it (amended 29 October 2026; the cursor was first in the proposed text):
  1. the workspace row;
  2. the initiating principal's account rows, while that workspace row is held;
  3. E03 fleet rows, in E03's order, which is where E03's `CommandService` locks fleet identity;
  4. environment capacity;
  5. deployment and revision;
  6. execution;
  7. steps, attempts, intent, task, decision, action command, reservations, deliveries and leases, each sorted by ID;
  8. the installation cursor, last, and only in a transaction that takes a compute slot or moves the scheduler's position.

  Decisions lock the task before the intent's execution row is reloaded under lock. PostgreSQL deadlock races cover every new pairing.

- **The cursor as a leaf.** No E05 service takes the installation cursor itself. Dispatch attempts and verification reads take their compute slots through E04's scheduler, one head per transaction, with the cursor last, after every lookup and lock. Signing and admitting a command at poll time, claims, renewals, receipts, stops, lapses, decisions, submissions, resolutions, task expiries and escalations take no cursor. A stop or release changes only the environment's count, under its capacity row. Read-backs reserve against the uncertain write's retained slot (`shared=True`), which takes no slot and so no cursor. Tests reuse the redesign's `cursor_checks` helpers, which use `pg_locks` and the statement log.
- **Pure transitions.** Orchestrator transitions add closed `Action` variants: `CreateIntent` (Task 4), `OpenTask` and `ConsumeDecision` (Task 5), `CreateDispatch` (Task 6), `CreateReadback` (Task 8) and `RecordOutcome` (Task 9). They never call a provider other than the artifact key provider, and never sign.
- **Occurrence paths.** E04's `#` record names gain `reconcile` (read-back attempts on the dispatch step instance) and `outcome` (the verification step's evaluated record). Task 8 extends `program.resolve_occurrence`.
- **E03 hooks.** `ExecutionPinnedRecordSource` and `ExecutionDispatchAuthority` (Task 6) are constructed only with explicit dependencies. The production deny defaults stay. E04 Task 14 shipped no qualified provider, so they stay until a change installs one.
- **Mark runnable.** Every service that can change progression calls E04's `mark_runnable` in the same transaction. That covers decisions, submissions, receipts, stops, lapses, command expiries, read-back results, resolutions, task expiries and escalations.

## Verification convention

Run from the implementation worktree root after frozen sync. `B = uv run --frozen --project backend pytest` and `R = uv run --frozen --project runtime/python pytest` are command prefixes, not scripts.

Each task's Step 2 runs its stated tests before implementation and records the missing behaviour. A missing new module is an acceptable initial red; a malformed test or an unrelated setup failure is not. Step 4 repeats them after implementation and requires PASS. Then run Ruff on changed files only (`ruff check` and `ruff format --check` with explicit paths), never the whole package. Both independent reviews inspect code and evidence, not merely the implementer's summary.

PostgreSQL tests follow E04's guarded `LOTUSPOND_PLATFORM_TEST_DATABASE_URL`, unique owned schemas, separate connections, deterministic barriers and zero database-related skips. Test time is database time plus the harness offset; no test edits deadline, claim, lease or expiry columns. Writes are counted at the synthetic target, outside every database snapshot, never by report counters. Each denial assertion has a positive control first, so "nothing happened" cannot pass because a service was dead. GitHub Actions has been blocked by billing since 23 October 2026, so CI jobs are run locally in Linux containers, and the evidence record says so.

## Task 1: Freeze the action contracts and derivation vectors

**Files:** Create `backend/lotuspond/execution/action_contracts.py`, `backend/tests/execution/actions/{__init__.py,test_action_contracts.py}` and `backend/tests/execution/actions/fixtures/{records.json,binding.canonical.json,decision.canonical.json,command.canonical.json}`.

**Interfaces:** Everything under "Vocabulary and states" and "Derivation and literal vectors": `derive_binding`, `decision_context`, `command_context`, `derive_command`, `idempotency_key`, the DTOs and the runtime-shared shapes as data models only.

- [ ] **Step 1:** Add `test_literal_binding_decision_and_command_vectors`, `test_each_bound_field_changes_the_digest`, `test_decision_context_old_epoch_and_independence`, `test_command_expiry_is_bounded_by_the_attempt`, `test_closed_vocabularies` and `test_contracts_module_is_inert`. Assertions:
  - The vectors equal the frozen literals, and the canonical files equal the SDK's canonical output byte for byte.
  - Changing any one of the 23 binding fields changes the intent digest. A decision quoting the old digest fails `check_decision` with `intent_mismatch`.
  - Under epoch 2 the decision is `stale_epoch`; when the requester decides it is `independence_required`; an actor kind other than `human` is `invalid_approver`.
  - Expiry is the earlier of 300,000 ms and the attempt deadline, and a command whose expiry would not exceed `now_ms` is refused.
  - Unknown assignments, escalations, states and DTO fields are refused.
  - Importing the module loads no SQLAlchemy, FastAPI, socket, thread or provider module.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert len(binding_canonical) == 1245
assert intent.sha256 == "2c976a61d1ad7c137b0e453862854019b3c47c575f9e6f07303efd6fb0da81bd"
assert decision_sha256 == "c4887c888df1e8b9e797445071416630bd39db8fcb3298433c1ddbffef9b4ad6"
assert command_sha256 == "678f627d787de45584199fd8aa5906fabb4a85c205b93c9b79c698d59431a9e9"
assert old_epoch_report.code == "stale_epoch"
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_action_contracts.py -q`; record red.
- [ ] **Step 3:** Implement the pure module with SDK validators and digests only, never a second canonicalizer. Freeze the four fixture files from the planning vector. Verify the canonical bytes independently with sorted compact ASCII JSON in the test, as the planning calculation did.
- [ ] **Step 4:** Repeat Step 2 plus `uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_actions.py sdk/python/tests/executable/test_wire.py -q`; PASS with unchanged SDK fixtures.
- [ ] **Step 5:** Commit Task 1 files as `feat: freeze action binding derivation and closed E05 vocabularies`.

## Task 2: Preserve data through additive schema v16

**Files:** Task 2's row; `backend/tests/execution/test_migration_v16.py`, `backend/tests/execution/actions/test_schema.py` and `backend/tests/execution/fixtures/schema-v15.sql`, produced from the committed E04 baseline. Set backend metadata to 0.4.0.

**Interfaces:** `migrate_execution_v16(connection) -> None` runs under the existing caller transaction and advisory lock. It refuses anything but exact v15 or partial v16 objects, and writes version 16 last. It:

- creates the seven records and their indexes;
- replaces the `executions.reason` check, and the `execution_control` code list of the `execution_events` check, with supersets: the reasons above and the codes `task_opened`, `task_claimed`, `task_released`, `task_escalated`, `task_expired`, `task_decided`, `task_submitted`, `intent_held`, `intent_superseded` and `action_resolved`;
- alters no other column, constraint or row.

The `sdk_runtime_event` family already allows the SDK action kinds.

- [ ] **Step 1:** Add `test_v15_upgrade_preserves_rows`, `test_fresh_upgrade_signature_equal`, `test_partial_v15_refused_atomically`, `test_action_full_scope_fk_matrix`, `test_intent_task_and_decision_uniqueness`, `test_widened_checks_are_supersets` and `test_action_command_references_e03_admission`. Assertions:
  - Populated v15 rows, including executions in every state and events of both families, are unchanged.
  - Fresh and migrated signatures are equal.
  - A failed preflight leaves version 15.
  - A cross-environment intent, task, decision, command, receipt, observation or outcome link raises an integrity error.
  - A second intent for one step instance, a second decision for one task, or a lease longer than 60,000 ms raises.
  - Every v15 reason and code is still accepted.
  - An action command cannot reference an admission in another workspace or installation epoch.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert after_legacy_rows == before_legacy_rows
assert fresh_signature == migrated_signature
assert failed_upgrade_version == 15
assert accepted_v15_reasons == all_v15_reasons
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_migration_v16.py backend/tests/execution/actions/test_schema.py -q` against isolated PostgreSQL; record red.
- [ ] **Step 3:** Implement the exact v15 preflight and additive objects with E04's helpers (`fleet_scoped`, `execution_parent`, `execution_hashes`, `execution_safe`). Use the existing schema generation and parity machinery.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/test_migrations.py backend/tests/fleet/test_migration.py backend/tests/execution/test_migration.py backend/tests/execution/test_migration_v15.py -q`; PASS with zero required database skips.
- [ ] **Step 5:** Commit Task 2 files as `feat: persist scoped action, task and outcome records with v16 migration`.

## Task 3: Admit effect packages under interpreter v2

**Files:** Task 3's row; `backend/tests/execution/actions/{test_admission.py,test_policies.py}`; control-flow fixtures under `backend/tests/execution/fixtures/actions/`. These are synthetic v2 packages and catalogs for the synthetic target connector, built from `contracts/examples/executable-v2/reviewed_action.json` with real schemas.

**Interfaces:**

- `PlanFeatures(effects: bool, review: bool, e05_forms: bool)` and `classify_plan(program) -> PlanFeatures`;
- `select_interpreter(features) -> str`, which returns v1 unless a feature is present;
- `check_policy(document, *, use: Literal['action','review','form','escalation']) -> None`, raising `unsupported_policy`;
- `check_write_bindings(program, configuration, catalog) -> None`, raising `dependency_unavailable` or `unsupported_operation`;
- `unreviewed_operations(program) -> tuple[str, ...]`, added to the deployment view;
- `interpreters.REGISTERED` gains v2, and `COMPILERS[v2] = "lotuspond.workflow-compiler.v2.1"`.

E04's `_unsupported` keeps refusing `compensation` (owner decision 14) and any unrecognized policy form.

- [ ] **Step 1:** Add `test_effect_steps_admitted_under_v2_only`, `test_v1_plans_keep_v1_and_golden_fixtures`, `test_policy_vocabulary_is_closed`, `test_escalation_needs_both_halves`, `test_write_connection_binding_is_exact`, `test_reconciliation_input_is_assignable`, `test_compensation_is_refused` and `test_unreviewed_operations_are_listed`. Assertions:
  - The reviewed-action package pins `INTERPRETER_V2`; the same package without effect or review steps pins v1, and every v1 golden fixture re-derives byte-identically.
  - Assignment `reviewers` is `unsupported_policy`, and so is an escalation policy reference without the review policy's `after_half_timeout`, or the reverse.
  - A connection version whose connector artifact digest differs from the catalog's, or which does not allow the write or its read-back operation, is `dependency_unavailable`.
  - A read operation whose target schema does not accept the write's target schema is `unsupported_operation`.
  - A dispatch with `compensation` is `unsupported_step`.
  - An `approval: none` policy is admitted (decision 3) and listed.
  - A worker registered only for v1 never claims a v2 execution.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert reviewed_execution.interpreter_id == "lotuspond.execution-interpreter.v2"
assert plain_execution.interpreter_id == "lotuspond.execution-interpreter.v1"
assert compensation_error.code == "unsupported_step"
assert v1_fixture_bytes_after == v1_fixture_bytes_before
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_admission.py backend/tests/execution/actions/test_policies.py -q`; record red.
- [ ] **Step 3:** Replace the blanket effect refusal with `classify_plan`, the closed policy checks and the write-binding checks. Apply them at publication, deployment and admission, and again in `load_program`, so a stored package never bypasses them. Keep E04's form-only rule (`workspace_admins`, no escalation) for plans that select v1.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/execution/test_publication.py backend/tests/execution/test_deployments.py backend/tests/execution/test_interpreters.py backend/tests/execution/test_progression_fixtures.py -q`; PASS.
- [ ] **Step 5:** Commit Task 3 files as `feat: admit reviewed effect packages under a pinned v2 interpreter`.

## Task 4: Create intents as deterministic row transitions

**Files:** Task 4's row; `backend/tests/execution/actions/test_intents.py`; golden progression fixtures under `backend/tests/execution/fixtures/progression/lotuspond.execution-interpreter.v2/`.

**Interfaces:**

- `IntentService.create(db, execution, step_instance, target, input, *, epoch) -> ExecutionActionIntent` is called only inside the orchestrator transaction that holds the execution row. It evaluates nothing itself: progression passes the evaluated values. It stores both values with E04's internal workspace-policy artifact path, derives the binding with Task 1's `derive_binding` from persisted records, and sets authorization to `proposed` for `approval: none` or `awaiting_review` otherwise.
- `load_intent(db, scope, intent_id) -> VerifiedIntent` recomputes the binding bytes, the intent digest and both artifact digests. A mismatch raises `ProgramError('integrity')`, which E04 fences.
- The step's output is an opaque `action_intent` handle that names the intent ID; mappings cannot read inside it.

- [ ] **Step 1:** Add `test_binding_comes_from_records_only`, `test_intent_is_immutable_and_unique_per_occurrence`, `test_tampered_intent_or_artifact_denies`, `test_iteration_occurrences_get_distinct_actions`, `test_expiry_is_capped_by_the_deadline`, `test_connection_artifact_mismatch_denies_at_creation` and `test_v2_golden_fixtures_rederive`. Assertions:
  - With records equal to the Task 1 vector, the derived intent digest equals the literal.
  - A model-produced value can reach target or input only through the compiled mapping; no execution input chooses scope, connector, operation, policy or requester.
  - Re-running a committed transition creates no row.
  - A tampered binding byte, artifact byte or digest fences the execution `reconciliation_required` with detail `integrity`, and nothing is dispatched.
  - Three iteration items give three action IDs and three digests.
  - `expires_at_ms <= execution.deadline_ms`.
  - Golden v2 fixtures re-derive byte-identically.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert derived_intent_sha256 == "2c976a61d1ad7c137b0e453862854019b3c47c575f9e6f07303efd6fb0da81bd"
assert rows_created_by_repeated_transition == 0
assert len({i.action_id for i in iteration_intents}) == 3
assert tampered_execution.state == "reconciliation_required"
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_intents.py -q`; record red.
- [ ] **Step 3:** Add `CreateIntent` to progression. The transition appends the SDK `intent_created` event with the exact intent record in its encrypted content, and leaves the step succeeded with the handle. Load the policy and connection pins from the verified program and deployment, never from the request. Record the v2 golden fixtures.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/execution/test_progression.py backend/tests/execution/test_progression_fixtures.py -q`; PASS.
- [ ] **Step 5:** Commit Task 4 files as `feat: record immutable action intents as interpreter-v2 transitions`.

## Task 5: Assign, claim, decide, escalate and expire human tasks

**Files:** Task 5's row; `backend/tests/execution/actions/{test_tasks.py,test_task_races.py,test_forms_v2.py}`.

**Interfaces:**

- `HumanTaskService(artifacts, budgets, recovery)`, with these methods:
  - `list(db, actor, *, state, after_id, limit) -> Page[HumanTaskView]`;
  - `get(db, actor, task_id) -> HumanTaskView`;
  - `proposal(db, actor, task_id) -> ProposalView`;
  - `claim(db, actor, task_id) -> HumanTaskView`;
  - `release(db, actor, task_id) -> HumanTaskView`;
  - `decide(db, actor, task_id, request: DecisionRequest) -> HumanTaskView`;
  - `submit(db, actor, task_id, request: SubmissionRequest) -> HumanTaskView`.
- Sweeper hooks `escalate_task`, `expire_task` and `expire_claim(db, task_id, *, epoch)`, each in its own transaction.
- Progression `OpenTask`, for `human_task` steps under v2. It creates the wait (retained through E04's `retain_wait`) and the task, with `escalate_at_ms = opened + timeout / 2` when escalation is declared.
- **Between opening and retention (E04 finding 2).** `list`, `claim`, `decide` and `submit` treat a task whose wait is not yet retained by the rule the owner chooses for finding 2's signals. That choice is needed before Task 5 starts. `test_tasks.py` covers the gap with the capacity step held back by a barrier.
- Progression `ConsumeDecision`. It reads only the decision row bound to the task, then sets the intent to `approved` or `denied` and the task's step to succeeded with a `human_decision` handle, or to failed with `approval_denied`.
- `authorize_execution` gains the fixed operation `review`, which sessions with an eligible role hold and keys never do.

Decision acceptance takes these steps under one transaction:

1. lock in order, then reauthorize the actor's current role;
2. check the task is open with the actor's live claim, and that the actor is eligible, counting the escalation group once escalated;
3. check independence and that the actor can currently read both proposal artifacts;
4. check the intent is `awaiting_review` and unexpired, and that the quoted digest equals the stored one;
5. build the SDK `HumanDecision` with Task 1's `decision_context`, and require `binding_valid` for `allow`, or `approval_denied` for `deny`;
6. persist the decision bytes, mark the task decided, append the SDK `decision_recorded` event and mark the execution runnable.

An equal `decision_id` replays the original; any other decision on a decided task conflicts. Paused executions accept decisions; cancelled ones refuse them.

Form submissions in v2 executions follow the same claim and eligibility rules, then reuse E04's typed, encrypted inbox path. V1 executions keep E04's signal route.

- [ ] **Step 1:** Add `test_claim_race_has_one_holder`, `test_decision_requires_live_claim_and_current_role_after_lock_wait`, `test_independence_and_actor_kind`, `test_quoted_digest_must_match`, `test_decision_idempotency_and_conflict`, `test_proposal_requires_current_source_access`, `test_escalation_releases_claim_and_widens_eligibility`, `test_task_expiry_fails_without_a_command`, `test_denial_fails_without_a_command`, `test_paused_accepts_and_cancelled_refuses`, `test_v1_form_signals_unchanged` and `test_v2_form_submission_uses_task_rules`. Assertions:
  - Of two concurrent claims, exactly one holds. A claim expires at 900,000 ms or the task deadline, whichever is first.
  - A reviewer demoted while waiting for the task lock is refused (`forbidden`). The requester is refused (SDK `independence_required`). A `lp_` key is refused before any lock. A viewer is refused.
  - A digest other than the stored one conflicts and leaves the task open.
  - A reviewer without current source access to the proposal is refused on both the proposal and the decision.
  - At half the timeout the task is escalated, its claim is cleared and the escalation group can claim. No notification row or outbox message is created.
  - An expired task, and a denial, each fail the execution (`review_expired`, `approval_denied`) with zero action commands.
  - E04's v1 form signal tests pass unchanged.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert sum(result.ok for result in concurrent_claims) == 1
assert requester_decision_error.code == "forbidden"
assert escalated_task.escalated is True and escalated_task.claim == "none"
assert action_commands_after_expiry == 0
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_tasks.py backend/tests/execution/actions/test_task_races.py backend/tests/execution/actions/test_forms_v2.py -q`; record red with real concurrent connections.
- [ ] **Step 3:** Implement the service and transitions in the lock order frozen above. Use the v16 due indexes for sweeper work, each item in its own transaction with E04's 500 ms lock timeout and `SKIP LOCKED`. Audit nothing here; Task 11 audits at the route.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/execution/test_inbox.py backend/tests/execution/test_sweeper.py -q`; PASS, including a reviewer removed from the workspace during a claim, and a sweeper killed mid-batch.
- [ ] **Step 5:** Commit Task 5 files as `feat: assign, claim, decide and escalate human tasks under current authority`.

## Task 6: Dispatch with fresh authority, signed commands, leases and receipts

**Files:** Task 6's row; `backend/tests/execution/actions/{test_dispatch.py,test_action_leases.py,test_receipts.py,test_action_races.py}`.

**Interfaces:**

- **E03 hooks.**
  - `ExecutionPinnedRecordSource.load(db, command) -> PinnedCommandRecords` loads the intent and decision with `load_intent`, then the connection version, secret bindings and exact target and input bytes. It runs under locks and checks current authority: the initiator's `execute`, a deployment revision that may still run, entitlement, the requested state `running`, the current epoch equal to the intent's, unexpired intent and decision, and the reviewer's current eligible role (decision 7).
  - `ExecutionDispatchAuthority.authorize(identity, command_digest, pins) -> None` runs the SDK's `check_command_binding` with `command_context`, and the bounded price and maximum-charge lookup.
- **Dispatch.** Progression's `CreateDispatch` creates the dispatch attempt. E04's scheduler reserves it like any other attempt: one head per transaction, with the installation cursor last (finding 11). Progression never reserves it inline. `ActionDispatchService.poll(db, principal) -> CommandDelivery | None` returns a pending action command for this session, or dispatches the next reserved action attempt of this pool. Dispatching builds `derive_command`, signs it with the operator command key provider and calls `CommandService.admit` in the same transaction.
- **Records.** `ActionDispatchService.records(db, principal, command_id, command_sha256) -> PinnedCommandRecords` serves only the session the command was dispatched to.
- **Leases.** `ActionLeaseService` has `claim(db, principal, command_id, command_sha256) -> ActionLease`, which calls `CommandService.claim(..., claim_hook=...)` and creates the lease in that transaction, and `renew(db, principal, lease) -> ActionLease`. Sweeper hooks: `lapse(db, command_row_id, *, epoch)` and `expire_command(db, command_row_id, *, epoch)`.
- **Receipts.** `ActionReceiptService` has `store_value(db, principal, attempt_id, content) -> ArtifactHandle`, `record_receipt(db, principal, report: ActionReport) -> None` and `record_stop(db, principal, stop: ActionStop) -> None`.
- **E03 changes.** `CommandService.claim(..., *, claim_hook=None)`. E03's generic claim route refuses, with `conflict`, any admission that an `ExecutionActionCommand` references.
- **Budgets.** `BudgetService.reserve_attempt(..., shared=True)` also accepts a parent that is `uncertain` with a retained slot, for Task 8's read-backs.

Receipts and stops:

- A receipt needs the live lease, the same session and generation, and a receipt whose `intent_sha256`, `action_id` and attempt equal the command's.
- `accepted` and `observed_effect` move the effect state, settle known usage, stop the lease, call E03's `record_status(terminal)` and confirm the compute stop. `unknown` makes the effect `unknown` and the attempt `uncertain`, calls `record_status(uncertain)`, confirms the stop and lets Task 8 read back.
- `not_started` confirms the stop with zero usage but still requires a read-back before any resend. `stopped_unknown` is treated like `unknown`.
- A lapse leaves the lease and attempt `uncertain`, keeps the slot and liability, and sets the effect to `unknown` and the execution reason to `action_uncertain`. It does not make the execution `reconciliation_required`. Progression creates the read-back.
- An unclaimed command past its expiry fails the attempt with `timeout_before_dispatch` and zero usage, as E04's delivery expiry does.

- [ ] **Step 1:** Add `test_each_authority_check_runs_after_locks`, `test_approval_alone_is_insufficient`, `test_command_bytes_expiry_and_recipient`, `test_generic_command_claim_is_refused`, `test_claim_once_and_lease_bounds`, `test_receipt_requires_live_lease_and_matching_intent`, `test_unclaimed_command_expiry_writes_nothing`, `test_lapse_makes_effect_unknown_without_owner_fence`, `test_uncertain_parent_shared_reservation`, `test_dispatch_races_revocation_and_cancellation` and `test_action_paths_keep_the_cursor_a_leaf`. Assertions:
  - For each of these, changed after approval and before dispatch, no admission row and no command exist: intent bytes, decision bytes, connection status or version, pool policy connector, initiator `execute`, deployment revision status, entitlement, epoch, requested state, and the reviewer's role. Each case runs a positive control first.
  - The command's canonical bytes satisfy the SDK. Its expiry is at most 300,000 ms and at most the attempt deadline, and its recipient is the polling instance and generation.
  - E03's generic route claiming an action command conflicts.
  - A second claim by the same session returns the same live lease; another session is refused.
  - A lease lasts at most 60,000 ms and ends no later than the attempt deadline.
  - A receipt naming another intent conflicts.
  - An expired unclaimed command leaves zero target writes and `timeout_before_dispatch`.
  - A lapse leaves the execution `running` with reason `action_uncertain`, and a `#reconcile` attempt reserved with `shared=True`.
  - Deadlock races of dispatch against revocation, cancellation and renewal complete without deadlock.
  - Polling, signing and admitting a command, claiming, renewing, receipts, stops and lapses never lock `execution_scheduler_cursors`. The dispatch attempt's reservation takes it last, after every lookup and lock. Both are checked with the redesign's `cursor_checks` helpers, and each path commits while another connection holds the cursor.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert admitted_commands_after_change == {}
assert command.expires_at_ms - command.issued_at_ms <= 300000
assert lease.expires_at_ms <= lease.deadline_ms
assert generic_claim_error.code == "conflict"
assert lapsed_execution.reason == "action_uncertain"
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_dispatch.py backend/tests/execution/actions/test_action_leases.py backend/tests/execution/actions/test_receipts.py backend/tests/execution/actions/test_action_races.py -q`; record red.
- [ ] **Step 3:** Implement the hooks, services and transitions:
  - Follow E04 Task 9's polling bound: at most 10 candidates, each in its own savepoint, so one refused head never blocks a pool.
  - Sign only in the API process, with a constructor-provided command key provider. Production wiring stays deny-by-default.
  - Never call `record_status` for historical closure.
  - Late receipts after a lapse go to Task 8 as historical observations.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/fleet -q` and `B backend/tests/execution/test_dispatch.py backend/tests/execution/test_leases.py backend/tests/execution/test_budgets.py backend/tests/execution/test_installation_capacity.py -q`; PASS, with E03 command behaviour unchanged for commands no execution references.
- [ ] **Step 5:** Commit Task 6 files as `feat: dispatch reviewed actions as signed commands under fresh authority`.

## Task 7: Run writes in the runtime against a synthetic target

**Files:** Task 7's row; `runtime/python/tests/{test_action_worker.py,test_action_client.py,test_action_invocation.py,test_synthetic_target.py}`; test-only `runtime/python/tests/fixtures/synthetic_target.py` and `runtime/python/tests/fixtures/synthetic_target_tls/` (generated per test run, never committed keys). Introduce runtime 0.3.0 metadata and lock; update the backend requirement.

**Interfaces:**

- **Providers.** `WriteProvider.invoke(records: PinnedCommandRecords, idempotency_key: str, deadline_ms: int) -> WriteResult(status: Literal['accepted','observed_effect','unknown'], response: object | None, usage: UsageObservation)`. The existing E04 `ReadProvider` serves the synthetic target's read operation for Tasks 8 and 9.
- **Running an action.** `run_action(delivery, lease, dependencies) -> ActionReport | ActionStop`.
- **Client.** `ActionClient` has `poll`, `records`, `claim`, `renew`, `store`, `receipt` and `stop`, over the existing verified control transport and session credentials.
- **Synthetic target.** `action_fixture.py` packages the synthetic target connector's write and read providers, like `activity_fixture.py`. They are selectable only with test-mode descriptors and reach the target only through `DestinationTransport`. The test-only `SyntheticTarget(store_dir, tls)` serves loopback HTTPS. It keeps owner-only per-key records and an append-only write log, and injects `lose_request`, `lose_response`, `delay_ms` and `crash_after_apply` through a harness-only channel.

The runtime's behaviour:

1. Verify each delivery with E03's `validate_delivery` against fetched records. Check the connector artifact digest, the operation's catalog effect class (`write`), the destinations and the secret pins.
2. Journal `claimed`, then `started`, durably, before any connector code runs. Use the E04 journal's file rules with an `action` record kind.
3. Resolve secrets immediately before the call, in the provider child. Never log or journal them.
4. Send the intent's `action_id` as the idempotency key when the operation declares `target_key`.
5. Store response values, then report.
6. Renew outside the blocked call. A refused renewal of a live lease lets the write finish; it does not cancel it.
7. After a restart, report every `started` attempt from the journal as historical evidence and never invoke it again. A `claimed`-only attempt reports `not_started`.

- [ ] **Step 1:** Add `test_journal_started_before_connector`, `test_restart_never_reinvokes_a_started_write`, `test_lost_response_reports_unknown`, `test_idempotency_key_is_the_action_id`, `test_write_goes_through_destination_transport`, `test_fixture_unselectable_in_production`, `test_refused_renewal_lets_the_write_finish`, `test_secrets_resolved_late_and_never_logged` and `test_target_counts_are_observed`. Assertions:
  - A crash-point hook between `started` and the socket open leaves a journal record, and the target sees no request.
  - SIGKILL after the target applied a write, then restart: the provider-call and target-write counts are unchanged.
  - A target configured `lose_response` makes the runtime report `unknown` with known zero usage.
  - The key the target received equals `action_id`.
  - An egress policy that refuses loopback produces no target request.
  - Production settings make the provider `unavailable`.
  - A secret sentinel is absent from logs, journal, receipts and values.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert target_writes_after_restart == target_writes_before_restart
assert journal_phase_before_socket == "started"
assert received_idempotency_key == intent_action_id
assert secret_sentinel not in captured_logs_and_journal
```

- [ ] **Step 2:** Run `R runtime/python/tests/test_action_worker.py runtime/python/tests/test_action_client.py runtime/python/tests/test_action_invocation.py runtime/python/tests/test_synthetic_target.py -q`; record red. Journal tests run on Linux only, as E04's do.
- [ ] **Step 3:** Reuse E04's provider-child supervision, cleanup fence, server-anchored clock and journal rules. Add no code loading by path, and no network path for any provider except through `DestinationTransport`. This is the first wiring of that transport for an execution provider, which E04 deferred to "whichever change installs" one.
- [ ] **Step 4:** Repeat Step 2 and the existing runtime suite; PASS with positive controls proving that the fixture ran, the target counted and children were cleaned up.
- [ ] **Step 5:** Commit Task 7 files as `feat: run journaled action writes against a synthetic target`.

## Task 8: Settle uncertain writes by read-back

**Files:** Task 8's row; `backend/tests/execution/actions/{test_rules.py,test_readback.py,test_resolution.py}`.

**Interfaces:**

- `evaluate_rules(rules, readback: JsonValue, expected: ValueEnvironment) -> bool`, with E04's expression semantics for `expected` mappings and the SDK's typed `readback_pointer` selection. A missing pointer is false for `present` and an error for comparisons, never a coercion.
- `ReadbackService.schedule(db, intent, *, reason)`, which progression calls through `CreateReadback`, and `ReadbackService.record(db, attempt) -> ExecutionActionObservation`.
- `ResolutionService.resolve(db, actor, execution_id, action_id, request: ResolutionRequest) -> ActionView`.
- `ActivityRecordSource` serves `#reconcile` attempts: the write's reconciliation read operation, connector pin and connection, with the intent's exact target and input as the attempt input.

Qualification and the resulting actions:

- `qualified` is true only when the absence rules hold and either the operation declares `target_key` or a stop is confirmed. A stop is confirmed by a live `not_started` or terminal report, a historical journal report, or an owner's or admin's `compute_stopped` resolution.
- A qualified absence with `target_key` and retry attempts left creates the next dispatch ordinal, with the same intent, decision and key and a new command. A qualified absence without them fails the step with `not_applied`.
- An unknown result repeats up to `MAX_READBACKS`, then makes the execution `reconciliation_required` with reason `action_uncertain`.
- Resolutions need an owner or admin session, the current recovery epoch and, for `effect_present`, an evidence handle already recorded for this action.
- A `compute_stopped` resolution is also accepted for an action attempt of an execution that E04 holds after a restore (Task 10). E04 refuses to release an execution whose write was claimed under the older epoch (409 `conflict`) until that write's stop is confirmed, and this resolution is that confirmation. It resends nothing and never marks an effect present. E04's reads still have no such route (finding 21).

- [ ] **Step 1:** Add `test_rule_semantics`, `test_effect_present_stops_any_resend`, `test_qualified_absence_resends_once_with_target_key`, `test_absence_without_target_key_is_not_applied`, `test_unqualified_absence_waits_for_an_owner`, `test_unknown_is_bounded_then_needs_reconciliation`, `test_resolution_requires_owner_and_recorded_evidence`, `test_late_receipt_is_historical_only` and `test_readback_never_waits_for_new_capacity`. Assertions:
  - The rule truth table matches expectations for every operator, nested `and` and `or`, and missing pointers.
  - A lost response leads to an `effect_present` read-back: one command and one target write.
  - A lost request with `target_key` leads to two commands, one target write and one shared idempotency key.
  - The same lost request without `target_key` ends `not_applied` with one command and zero writes.
  - After five unknown read-backs the execution is `reconciliation_required`.
  - An editor's resolution is forbidden. `effect_present` citing an artifact not recorded for this action conflicts. `close_unknown` fails the step with `outcome_unknown` and leaves the effect `unknown`.
  - A receipt arriving after a lapse is recorded as `historical` and changes no lease.
  - With all five environment slots held by uncertain writes, their read-backs still run.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert (lost_response_commands, lost_response_writes) == (1, 1)
assert (lost_request_commands, lost_request_writes) == (2, 1)
assert len(set(lost_request_idempotency_keys)) == 1
assert unknown_execution.state == "reconciliation_required"
assert editor_resolution_error.code == "forbidden"
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_rules.py backend/tests/execution/actions/test_readback.py backend/tests/execution/actions/test_resolution.py -q`; record red.
- [ ] **Step 3:** Implement the rule evaluator over the SDK's rule models, the `#reconcile` occurrences, read-back scheduling with the read operation's own retry policy and backoff, the qualification rules and the resolution service. A read-back result changes only the observation and effect columns. Progression decides what follows.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/execution/test_reconciliation.py backend/tests/execution/test_dispatch.py -q`; PASS.
- [ ] **Step 5:** Commit Task 8 files as `feat: settle uncertain writes by read-back before any resend`.

## Task 9: Verify outcomes and gate success on them

**Files:** Task 9's row; `backend/tests/execution/actions/test_verification.py`.

**Interfaces:**

- Progression creates an `outcome_verification` step's read attempt from its own mappings, through `ActivityRecordSource` with the declared read operation.
- `VerificationService.record(db, attempt) -> ExecutionActionOutcome` applies the step's rules with Task 8's evaluator. It writes an SDK `OutcomeRecord` naming the intent digest, the receipt (or the read-back observation acting as receipt evidence), the rule ID and version, and evidence. It appends `outcome_recorded`.
- `completion_allowed(state) -> bool` requires every dispatched action's outcome to be `verified` before `Complete`.

- [ ] **Step 1:** Add `test_verified_requires_evidence_and_rules`, `test_not_verified_fails_with_the_effect_visible`, `test_unknown_after_read_retries`, `test_success_requires_every_dispatched_outcome`, `test_computational_success_is_unchanged` and `test_outcome_binds_the_exact_intent_and_receipt`. Assertions:
  - A verified outcome has at least one evidence reference.
  - A failing rule fails the execution `outcome_not_verified`, with the effect `observed`.
  - A read failing after its retries fails it `outcome_unknown`.
  - In a parallel region with two dispatches, one verified and one not, the execution does not succeed.
  - A package without effects succeeds as in E04.
  - An outcome cannot reference another intent's receipt.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert verified_outcome.evidence_refs
assert not_verified_execution.state == "failed"
assert not_verified_execution.reason == "outcome_not_verified"
assert mixed_parallel_execution.state != "succeeded"
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_verification.py -q`; record red.
- [ ] **Step 3:** Implement verification and the completion rule. Never infer an outcome from a receipt, a model or a projection.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/execution/test_progression.py -q`; PASS.
- [ ] **Step 5:** Commit Task 9 files as `feat: verify action outcomes and gate execution success on them`.

## Task 10: Keep controls, revocation and restore safe around actions

**Files:** Task 10's row; `backend/tests/execution/actions/{test_action_controls.py,test_action_recovery.py,test_action_projections.py}`.

**Interfaces:**

- **Pause.** No new intents, tasks or dispatches. E04 already releases reserved and unclaimed dispatch attempts. Claimed writes continue, and tasks stay open.
- **Cancel.** Withdraws undecided intents, cancels open tasks and releases undispatched attempts. Claimed writes finish and record. Read-backs of uncertain writes continue, but declared verification does not (decision 12). The terminal state waits for containment.
- **Restore (decision 13, restated).** E04's restore hold (owner decision, 2026-10-28) applies unchanged. `recovery.hold_restored` holds every older-epoch execution that has not ended, and nothing of it is scheduled, dispatched or delivered until an owner releases it through `ControlService.reconcile_execution` (`POST …/executions/{id}/reconciliation`).
  - **Hold.** In the same transaction as `hold_restored`, `hold_old_epoch_intents(db, execution, *, epoch) -> int` marks each unsettled intent whose `recovery_epoch` is older as held, using `execution_action_hold_idx`, and appends `intent_held`. It schedules nothing. Its open tasks keep their deadlines, and a decision recorded during the hold cannot dispatch.
  - **Release.** When the owner releases the execution, each held intent gets a read-back under the new epoch, with current identity and grants, in place of any re-delivery of its write. The release never reserves an older-epoch dispatch attempt again. Each read-back then settles as follows:
    - present → effect `observed`, and verification continues;
    - qualified absence → authorization `superseded`, and the step fails `recovery_epoch_changed`, with no automatic re-proposal;
    - unknown → decision 11's bound, then `reconciliation_required`.
  - **A write claimed under the older epoch** stays uncertain, as E04's attempts do. The release is refused with 409 `conflict` until its stop is confirmed, by the runtime's journal report or an owner's `compute_stopped` resolution (Task 8).
  - **Cancel, then release** follows E04's runbook. Read-backs of held intents still run after the release, because they contain an existing effect (decision 12).
- **Projections.** `ActionProjection.list(db, actor, execution_id, *, after_id, limit)` and `.get(...)` return `ActionView`s. They are read without locks, as E04's reads are. `unknown` is never shown as a value, and success is never inferred.
- E04's `request_control`, `RecoveryFence`, `hold_restored` and `reconcile_execution` are unchanged apart from these action paths.

- [ ] **Step 1:** Add `test_cancel_during_an_in_flight_write_records_a_completed_effect`, `test_cancel_during_review_writes_nothing`, `test_pause_keeps_review_open_and_blocks_dispatch`, `test_revocations_between_approval_and_claim`, `test_disconnected_runtime_cannot_receive_new_commands`, `test_old_epoch_intents_are_held_and_read_back`, `test_no_readback_before_the_owner_releases`, `test_release_refused_until_an_old_epoch_write_stops`, `test_restored_grant_or_decision_never_authorizes` and `test_projection_never_infers_success`. Assertions:
  - Cancelling during a claimed write: the write finishes, its receipt is recorded, no later step runs and the terminal state is `cancelled` after containment, with the effect `observed`.
  - Cancelling during a review: zero commands; a later decision is refused.
  - Paused: the task stays open and a decision is accepted, but no command appears until resume.
  - A revocation committed between dispatch and claim refuses the claim.
  - An epoch increment holds every unsettled intent with its execution and refuses old-epoch commands and leases. No read-back attempt exists until the owner releases the execution. After the release, each held intent is settled by read-back.
  - A write claimed under the older epoch: the release is refused with `conflict` until its stop is confirmed, then succeeds and reads back.
  - A decision restored from before the backup cannot dispatch under the new epoch (`stale_epoch`).
  - `ActionView.outcome` is `unknown` until verified.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert cancelled_in_flight.effect == "observed" and cancelled_in_flight_execution.state == "cancelled"
assert commands_after_review_cancel == 0
assert held_intents == unsettled_old_epoch_intents
assert readback_attempts_before_release == 0
assert release_before_stop_error.code == "conflict"
assert restored_decision_report.code == "stale_epoch"
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_action_controls.py backend/tests/execution/actions/test_action_recovery.py backend/tests/execution/actions/test_action_projections.py -q`; record red.
- [ ] **Step 3:** Implement the control, revocation and restore paths and the projections. Deliberately skip nothing that Tasks 5–9 left as a sweeper obligation.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/execution/test_control.py backend/tests/execution/test_recovery.py backend/tests/execution/test_projections.py backend/tests/execution/test_sweeper.py -q`; PASS.
- [ ] **Step 5:** Commit Task 10 files as `feat: keep actions safe through controls, revocation and restore`.

## Task 11: Expose task, action and runtime action APIs

**Files:** Task 11's row; `backend/tests/execution/actions/{test_task_routes.py,test_action_routes.py,test_action_runtime_routes.py}`. Add a changelog item in `app.py` only for implemented behaviour.

**Interfaces:** With `M=/api/v1/projects/{project_id}/environments/{environment_id}`:

- **Tasks:** `GET M/human-tasks`, `GET M/human-tasks/{task_id}`, `GET M/human-tasks/{task_id}/proposal`, `POST M/human-tasks/{task_id}/claim`, `/release`, `/decision` and `/submission`.
- **Actions:** `GET M/executions/{id}/actions`, `GET M/executions/{id}/actions/{action_id}` and `POST M/executions/{id}/actions/{action_id}/resolution`.
- **Runtime:** `POST /api/v1/runtime/actions/{poll,records,claim,renew,receipt,stop}` and `POST /api/v1/runtime/actions/{attempt_id}/values`, added to the control transport's session-only allowlist.

The rules follow E04 Task 12's conventions:

- the opt-in fleet API, test-mode service construction, closed errors and `no-store`;
- keyset pages (`after_id`, `limit` 1–100, no counts);
- 1 MiB customer bodies; 64 KiB runtime bodies, and 131,072-byte command deliveries;
- separate read and write allowances.

The mutation rules:

- Claim and release are idempotent by state. Decisions, submissions and resolutions carry their own identity, so they need no `Idempotency-Key`.
- Claims, decisions and submissions need a human session with E04's origin, content-type, verification and MFA protections. Keys are refused before any lock.
- Each mutation writes an `AuditEvent` with fixed identifiers only: `executions.tasks.claimed`, `.released`, `.decided`, `.submitted`, and `executions.actions.resolved`.
- Proposal reads are audited as `executions.tasks.proposal_read`, with no values.
- E03's generic command claim route refuses action commands.

- [ ] **Step 1:** Add `test_route_role_matrix`, `test_keys_never_claim_decide_submit_or_resolve`, `test_proposal_is_no_store_and_audited_without_values`, `test_runtime_action_routes_are_session_bound`, `test_boundary_and_body_limits`, `test_replay_is_not_audited_twice` and `test_openapi_names`. Assertions:
  - Owners, admins and editors act per assignment; viewers read metadata and proposals but never claim.
  - A key claiming or deciding gets 403.
  - The proposal response has `cache-control: no-store`, and its audit row contains no target or input sentinel.
  - Another session's claim or receipt is refused.
  - Duplicated auth or session headers are refused.
  - OpenAPI names contain `production_execution_action` and `human_task`, and none names an evaluation run.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert key_decision_response.status_code == 403
assert proposal_response.headers["cache-control"] == "no-store"
assert private_sentinel not in proposal_audit_row
assert other_session_receipt_response.status_code in {401, 403, 409}
```

- [ ] **Step 2:** Run `B backend/tests/execution/actions/test_task_routes.py backend/tests/execution/actions/test_action_routes.py backend/tests/execution/actions/test_action_runtime_routes.py -q`; record red.
- [ ] **Step 3:** Wire the services through explicit DTOs with E04's pre-check and service-reauthorization pattern. Runtime receipt, stop and claim routes keep E04's lock-free credential look-up and take no installation cursor, as E04's result routes do after finding 11. Where a count changes, they lock environment capacity before the execution. (Amended 29 October 2026: the proposed text took the cursor before runtime authentication.)
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/execution/test_routes.py backend/tests/execution/test_runtime_routes.py backend/tests/execution/test_boundary.py backend/tests/fleet -q`; PASS with production-deny startup defaults.
- [ ] **Step 5:** Commit Task 11 files as `feat: expose human task, action and runtime action APIs`.

## Task 12: Qualify actions with the E04 conformance harness

**Files:**

- tier 1: `backend/tests/execution/conformance/actions/{__init__,conftest,target,test_spike_writes,test_reviews,test_reconciliation,test_controls,test_revocation,test_disconnection,test_isolation,test_privacy}.py`;
- tier 2: `backend/tests/execution/conformance/disruptive/test_action_restore.py`;
- tier 3 and smoke: action scenarios added to `scripts/checks/execution-smoke.py` and `scripts/checks/execution-load.py`;
- installed acceptance: the reviewed-write journey added to `backend/tests/execution/test_installed_acceptance.py`.

Create `docs/validation/<date>-actions-human-tasks.md` recording failures and observed limits.

**Interfaces:** Reuse E04 Task 13's harness, as the finding 11 redesign leaves it, unchanged:

- the `engine` fixture;
- separate killable API, orchestrator, sweeper and runtime processes;
- the TCP proxy;
- the database-time clock;
- `execution_process.py` supervision and cleanup census;
- required mode (`LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS=1`) and the disruptive flag.

`conftest.py` adds the synthetic target as one more owned process, with its store outside every database cluster and backup. Write counts come only from the target's log. A harness reviewer acts through the public task routes with real sessions.

**Spike checks for E05.** The numbers follow `results/pg-demo.txt` of the [spike](../../plans/2026-10-15-durable-engine-spike.md):

| Spike checks                          | E05 conformance case                                                                                                                                                                                                                                                                                                            | Tier |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 1–7 with a review and a write         | SIGKILL the orchestrator, sweeper and runtime during a review; immediate PostgreSQL restart on the owned cluster (tier 2). The intent bytes and digest are unchanged; a decision quoting another digest is refused; fresh processes finish; exactly one write carries the reviewed values; worker and runtime identities differ | 1, 2 |
| 15: duplicated trigger, one write     | 25 concurrent admissions under one key with a reviewed write: one execution and one target write                                                                                                                                                                                                                                | 1    |
| 16–17: response lost after apply      | `lose_response`: receipt `unknown`, read-back `effect_present`, observed; one command and one write                                                                                                                                                                                                                             | 1    |
| 18–19: request lost before the target | `lose_request` with `target_key`: qualified absence, resend under the same key; two commands and one write                                                                                                                                                                                                                      | 1    |
| 20–22: runtime dies after apply       | `crash_after_apply` plus SIGKILL of the runtime: the lease lapses, read-back finds the effect and nothing is resent; one write and one command; the restarted runtime's journal report is historical                                                                                                                            | 1    |
| 26, write half                        | Cancelling while a write is in flight: the write finishes, the receipt is recorded, it is reported as a completed effect and no later step runs                                                                                                                                                                                 | 1    |
| 28: no write for a cancelled review   | Cancelling during a review leaves zero commands and writes; a later decision is refused                                                                                                                                                                                                                                         | 1    |
| 34 with a review                      | An unanswered review expires through the sweeper: `review_expired`, zero commands                                                                                                                                                                                                                                               | 1    |
| 35: change after approval             | After an `allow`: tamper with the stored binding, a target or input artifact (integrity case), or revoke or change the connection; or present another occurrence's decision. Each is refused before any command; zero writes                                                                                                    | 1    |

**Review and action cases:**

| ID     | Case                                                                | Required result                                                                                   |
| ------ | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| ACT-1  | Requester, API key, viewer and ineligible editor try to decide      | All refused; the eligible independent reviewer succeeds                                           |
| ACT-2  | Two reviewers race to claim; a claim expires; an admin releases one | One holder; an expired claim frees the task; a decision without a live claim is refused           |
| ACT-3  | Escalation at half the timeout                                      | Escalated, the claim released, the escalation group eligible; no notification row                 |
| ACT-4  | Intent expires after `allow`, before dispatch                       | No command; `approval_expired`                                                                    |
| ACT-5  | Denial                                                              | No command; `approval_denied`                                                                     |
| ACT-6  | Reviewer demoted after `allow` (decision 7)                         | No command; `approval_revoked`                                                                    |
| ACT-7  | `approval: none` policy                                             | Dispatched without a task; listed in the deployment's unreviewed operations                       |
| ACT-8  | Read-back stays unknown                                             | Five read-backs, then `reconciliation_required`; an owner's `close_unknown` fails without a write |
| ACT-9  | Verification fails                                                  | `outcome_not_verified`; the effect stays visible with its evidence                                |
| ACT-10 | Lost request without `target_key`                                   | No resend; `not_applied` after a confirmed stop                                                   |
| ACT-11 | Command expires unclaimed                                           | `timeout_before_dispatch`; zero writes; another runtime may take the retry within bounds          |
| ACT-12 | Reviewer without source access to the proposal                      | Proposal and decision refused                                                                     |

**Revocation, disconnection and restore:**

| ID        | Case                                                                                    | Required result                                                                                                                                                                                                |
| --------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| REVA-1    | Connection revoked after `allow`                                                        | No command                                                                                                                                                                                                     |
| REVA-2    | Runtime revoked while holding an action lease                                           | Renewal and receipt refused; the lease lapses; read-back by another runtime settles the effect; one write                                                                                                      |
| REVA-3    | Pool policy drops the connector between dispatch and claim                              | Claim refused; the command expires; zero writes                                                                                                                                                                |
| REVA-4    | Entitlement or price authority expires                                                  | No new dispatch; reads, exports and resolutions still work                                                                                                                                                     |
| REVA-5    | Initiator loses `execute`                                                               | No new dispatch; in-flight writes finish and record                                                                                                                                                            |
| DISCA-1   | Runtime disconnected (proxy severed) after claim, with the write in flight              | Lease lapses; read-back settles it; on reconnection the late receipt is historical only; one write                                                                                                             |
| DISCA-2   | Runtime disconnected and revoked                                                        | Refused on reconnection; no further writes                                                                                                                                                                     |
| DISCA-3   | Every runtime unreachable for several lease periods                                     | Reviews and waits unchanged; no command is claimed; recovery completes after reconnection                                                                                                                      |
| RESTORE-1 | Backup before a successful write; restore (tier 2)                                      | E04's hold holds the execution and its intent; no read-back before the owner's release; after it, read-back observes the write; no repeated write; verification continues                                      |
| RESTORE-2 | Backup during a review; afterwards a decision, a dispatch and a write; restore (tier 2) | The older-epoch intent is held with its execution; after the owner's release, read-back finds the effect, so it is observed with no resend. The absent variant fails `recovery_epoch_changed` with zero writes |
| RESTORE-3 | Backup before a connection or runtime revocation (tier 2)                               | The restored grant cannot authorize; no command                                                                                                                                                                |
| RESTORE-4 | RecoveryAuthority unavailable after a restore (tier 2)                                  | No dispatch, claim or read-back; reads and exports work                                                                                                                                                        |
| RESTORE-5 | Backup while a write is claimed; the runtime applies it; restore (tier 2)               | The release is refused with `conflict` until the write's stop is confirmed (journal report or `compute_stopped`); then read-back observes it; one write, no resend                                             |

**Isolation and privacy:**

| ID     | Case                                                                                                                                                | Required result                                                                         |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| TEN-A1 | Another workspace lists, reads, claims, decides or resolves                                                                                         | `not_found`, indistinguishable from a missing record                                    |
| TEN-A2 | A reviewer in two workspaces                                                                                                                        | Each task list shows only its own scope                                                 |
| TEN-A3 | Another pool's runtime claims, renews or reports a known command ID                                                                                 | Refused without revealing existence                                                     |
| TEN-A4 | Rows referencing another scope, written directly                                                                                                    | Rejected by composite foreign keys                                                      |
| PRIV-1 | Sentinels in target, input, response and provider errors; decode events, commands, receipts, observations, outcomes, logs, errors, inbox and outbox | Sentinels appear only in their encrypted artifacts and the authorized proposal response |

**Load (tier 3).** Extend E04's workload: 20 % of cases carry one reviewed write. A harness reviewer decides it after a log-normal delay (mean 30 s), and the synthetic target adds 1 s mean latency. Record dispatch latency from decision to claim, the retained-wait count, read-back counts and target writes per intent. Require at most one target write per intent and E04's admission and queue-age criteria. A local result describes this harness on this hardware only.

Also record the provider lookups, as the redesign's tier 3 does (owner decision, 29 October 2026). For decisions, claims, renewals, dispatches and receipts, per load phase, record:

- callers that waited for a lookup slot, and their waits;
- refusals after the 5 s bound;
- the cursor's hold time and lock waiters by relation.

These numbers decide whether the single-call lookup (E04 finding 3) must change before the API serves real load. A renewal refused after the bound must leave its write to finish and lapse into read-back (Task 7).

- [ ] **Step 1:** Add the cases above and the installed-artifact acceptance, which asserts:
  - independently installed API, runtime and SDK origins;
  - actual TLS between runtime and API, and between the provider and the synthetic target;
  - the reviewed-write journey's outcome, with measured command, write and read counts;
  - rollback injection before and after the intent, decision, dispatch, claim, receipt, read-back and outcome commits.

  Add a positive control before each denial assertion.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert required_integration_skips == 0
assert all(item["passed"] for item in observed_scenarios)
assert max_target_writes_per_intent == 1
assert all(sentinel not in decoded_records for sentinel in private_sentinels)
assert cleanup_receipt["confirmed"] is True
```

- [ ] **Step 2:** Run tier 1 with the required flags: `B backend/tests/execution/conformance/actions backend/tests/execution/test_installed_acceptance.py -q`. Run tier 2 against an owned cluster: `B backend/tests/execution/conformance/disruptive/test_action_restore.py -q`. Record red.
- [ ] **Step 3:** Extend the harness only through fixtures and the target process. Keep the spike code out of the product and the harness.
- [ ] **Step 4:** Repeat Step 2, plus the smoke check and one tier-3 run on recorded hardware. Require every named scenario true, zero required skips, no sentinel and confirmed cleanup. Record the exact source tree, artifacts, durations, counters and limitations, including failed runs.
- [ ] **Step 5:** Commit Task 12 tests, harness changes and the evidence document as `test: qualify reviewed actions across failure, restore and load`.

## Task 13: Complete packaging, documentation and final review

**Files:**

- Modify `.github/workflows/ci.yml` (runtime and backend version pins, plus tier jobs if their duration requires), `runtime/python/README.md`, `docs/{CAPABILITIES,VALIDATION,API}.md`, `docs-site/docs/runtime-foundation.md`, `docs-site/mkdocs.yml` (one nav entry) and the image metadata assertions for API 0.4.0 and runtime 0.3.0.
- Create `docs-site/docs/actions-human-tasks.md`, `docs/operations/actions-human-tasks.md`, `docs/superpowers/progress/<date>-actions-human-tasks.md` and `scripts/checks/test_action_packaging.py`.
- Review `web/src/lib/roadmap.ts` against every changed `CAPABILITIES.md` row. Human tasks stays a Planned page, because E05 adds no UI.

**Interfaces:** `npm run check` keeps its members. Conformance tiers run as E04 Task 14 arranged, in local Linux containers while hosted Actions remain blocked. Starting workers stays an explicit operator action.

- [ ] **Step 1:** Add `test_action_fixtures_unselectable_in_production`, `test_missing_command_key_is_not_readiness_success`, `test_required_action_scenarios_cannot_skip` and `test_capabilities_do_not_claim_ui_or_live_providers`. Assertions:
  - Production settings refuse the synthetic target and providers.
  - A missing command key provider keeps dispatch unavailable rather than healthy.
  - Omitting any Task 12 scenario fails the report.
  - The capability text names synthetic evidence only.

Core assertions for the named Step 1 tests (variables come from that test's fixture/observations):

```python
assert production_provider_selection_error.code == "unavailable"
assert dispatch_readiness_without_key is False
assert required_report_missing_error.code == "missing_required_evidence"
```

- [ ] **Step 2:** Run `B scripts/checks/test_action_packaging.py -q`; record red.
- [ ] **Step 3:** Wire the builds and document:
  - `docs/operations/actions-human-tasks.md`: command-key custody and rotation; action leases; the synthetic target's test-only status; restore, as an extension of E04's procedure: the hold, the journal review, confirming the stop of a write claimed under the older epoch, the release, and the read-back of held intents that follows it; runbooks for held intents, unknown read-backs, owner resolutions and expired reviews; the escalation semantics, including that no notification is sent.
  - The open qualifications: real connectors and providers, notification delivery, compensation (decision 14), E06 cases and business outcomes, E07 UI, E08 signed entitlements and E09 operations.
- [ ] **Step 4:** Run `npm run check`, the isolated PostgreSQL suite with tiers 1 and 2, runtime artifact suites on both Python versions, schema parity, `git diff --check` and `uv run --with mkdocs==1.6.1 mkdocs build --strict --config-file docs-site/mkdocs.yml`. Run an independent whole-branch review and resolve its findings. Record actual failures, skips and artifact hashes.
- [ ] **Step 5:** Commit Task 13 files as `build: package and document reviewed actions and human tasks`. Ask the owner before pushing or opening a draft PR. Do not merge or claim production readiness.

## Migration notes

- **Upgrade path.** v15 to v16 only, through `npm run db:migrate` (`initialize.py`), in one caller transaction with strict preflight and the version written last. A database at v14 first takes E04's v15 step.
- **Data.** All v15 rows are preserved. No v15 execution contains an effect or review step (E04 refused them), so nothing is backfilled and no intent is synthesized. V1-pinned executions keep v1 semantics until they finish.
- **Widened checks.** The `executions.reason` and `execution_events` checks are replaced by supersets inside the migration transaction. Tests prove that every v15 value is still accepted.
- **Rollback.** There is no destructive downgrade. Stopping E05 means turning the fleet API off, or disabling deployments that pin v2. An API older than v16 is not supported against a v16 database; recovery is forward repair, preserving uncertain records. Held or unknown actions are never deleted to make a rollback possible.
- **Operator provisioning.** The command signing key provider and its independently provisioned verification trust (E03), test-mode-only synthetic providers, and capacity and scheduler rows as E04 Task 14 documents them. Missing setup keeps denying.
- **Compatibility.** No SDK, E03 command, E04 activity or v1 registry byte changes. Backend 0.4.0, runtime 0.3.0 and SDK unchanged.

## Author self-review and scope trace

Completed by the plan author before handoff; this is not a delegated implementation review.

| Specification requirement                                  | Owning tasks / evidence                                                      |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Durable intent before effects, exact binding               | 1, 2, 4; literal vectors, immutability and integrity fences                  |
| Independent reviewer assignment, claim, expiry, escalation | 3, 5, 11, 12; closed vocabulary, claim races, ACT-1–3                        |
| Fresh authority before dispatch; denial and expiry         | 6, 10, 12; per-field refusal matrix, ACT-4–6, REVA, spike row 35             |
| Idempotency, receipts, ambiguous-effect reconciliation     | 6–8, 12; leases, journal, qualified absence, spike rows 16–22                |
| Outcome verification (E05/E06 boundary)                    | 9, 12; `OutcomeRecord` evidence, success gate                                |
| Recovery authority: disconnection, fencing, restore        | 6, 10, 12; DISCA, RESTORE-1–5, held intents read back at release             |
| Privacy, isolation, capacity                               | 2, 4, 6, 8, 11, 12; composite keys, sentinels, shared read-back reservations |
| Explicitly authorized compensation                         | Not implemented; decision 14 keeps it refused and open in the register       |
| Honest limits, packaging, documentation                    | 13; capability text, operations runbook, validation record                   |

Author checks:

- every Review Focus case has named tests;
- shared types have one owner and identical consumers;
- the planning vectors precede integration and agree with the SDK;
- task bodies prescribe interfaces and assertions, not implementations;
- no SDK, E03 or E04 byte is reinterpreted;
- no real provider, notification or UI is assumed.

One integrated plan is appropriate because intent, review, dispatch, read-back and verification must hold together for any effect to be safe. The first customer, their systems, permitted autonomy and acceptance owner remain open inputs for later qualification. They do not block these synthetic tasks or authorize scope removal.
