# Durable Agent Execution (E04) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute admitted agent workflows durably, with current authority, bounded usage, private data handling and truthful progress through restart and recovery.

**Architecture:** PostgreSQL owns admission, artifacts, authority, usage, customer projections and durable progression ([ADR-0011](../../architecture/0011-postgresql-durable-execution.md)). Orchestrator workers advance executions over the schema-v14 records in short transactions that hold the execution row lock, and a sweeper handles due waits, deadlines, lapsed leases and expired deliveries. Separately supervised runtime workers perform bounded read/model activities over the signed, leased activity protocol. Existing evaluation, v1 publication and action-command paths retain their contracts.

**Tech Stack:** Python 3.13/3.14, SQLAlchemy/PostgreSQL (psycopg), FastAPI, existing Pydantic/rfc8785/cryptography contracts. There is no engine server and no new dependency: the orchestrator and sweeper are worker commands of the API distribution.

**Spec:** [Approved E04 specification](../specs/2026-10-15-durable-execution-design.md). Read it with this plan. Source baseline: E03 `462ba12d8c9d4933ab0359f8699b6f27f68c3381`. Prepared 15 October 2026 (Asia/Calcutta); owner approved the plan and implementation on 15 October 2026. The owner's selected subagent execution with independent specification and quality review after each task remains in force.

**Revision 2 (22 October 2026)** re-plans Tasks 7–14 for ADR-0011 on top of the implemented Tasks 1–6 (`7bbb92d`); see [Revision 2](#revision-2-22-october-2026). The owner approved Revision 2 on 22 October 2026 (“approved for revision 2”). Implementation of Tasks 7–14 has not started and begins when the owner asks for it. Tasks 1–6 keep their approved text.

## Global Constraints

- “Keep v1 endpoints, canonical bytes, IDs and declaration-only meaning unchanged; there is no implicit conversion or execution of v1 content.”
- “Keep orchestration code outside the pure SDK and the existing evaluation runner.”
- “Current revocation wins over historical pinning. Missing providers deny activity.”
- “Use at most 65,536 bytes for the combined activity envelope/signature” and a “five-minute maximum dispatch-command lifetime”; running leases are “at most 60 seconds.”
- “The initial artifact interface limits each admitted JSON value to 1 MiB and the existing SDK depth/item limits.” Use 1,048,576 bytes, depth 32, 10,000 items.
- “Initial engineering ceilings are 25 active activities per installation, 5 per environment, 1,000 pending executions and 10,000 retained waits per environment.” Only downward configuration is allowed without new qualification.
- “Per-environment FIFO plus round-robin selection among eligible environments” applies; callers cannot select priority, workflow type or task queue.
- “Unknown usage remains explicitly reserved/uncertain until reconciliation, never measured zero.” Existing telemetry subscription levels do not grant execution entitlement.
- “Intent, action-review, action-dispatch and outcome-verification nodes remain explicitly unsupported at E04 deployment admission until E05/E06 supply their reviewed complete implementation.” Human `mode=form` is supported; `mode=review` is refused.
- “No always-allow provider is added to make production startup appear successful.” Fixed synthetic catalogs, model/read adapters, authority and key providers are constructor-injected test fixtures and production-unselectable.
- “No destructive downgrade or empty-database initializer as upgrade.” Advance exact schema v13 to v14 (implemented) and exact v14 to v15, preserving all existing data. Engine state is product schema (Revision 2).
- The execution row lock is the orchestration fence. Tokened leases exist only for runtime activity attempts, and orchestrator transitions follow the frozen orchestrator lock order (Revision 2).
- “Local deterministic/model fixtures do not qualify real providers, workload, customer outcomes, HA, hostile-tenant isolation, RPO/RTO or service availability.” No merge, customer connection, production database, public distribution or deployment is authorized by this plan.

## Review Focus

1. A runtime dies after a provider accepted a call but before its result reached the API, or the API commits but its HTTP response is lost: the same identity reconciles without a second execution, a second provider call or fictitious zero usage (Tasks 6, 9, 13).
2. Revocation or expiry occurs while a request waits for a database lock: authority and database wall time are checked after acquiring locks (Tasks 3, 5, 9, 11).
3. A validly signed activity has altered scope, dependency, recipient or actual tool arguments: persisted binding and current authorization still refuse it (Tasks 1, 9, 10).
4. A source is revoked after admission, or a provider includes private payload in an exception: lookup denies and decoded events/errors remain free of sentinels (Tasks 4, 8, 10, 13).
5. A busy environment, nested tool calls or restored timers exhaust resources: shared budgets, fair scheduling and original deadlines survive retries, waits and restore (Tasks 5, 8, 11, 13).

## Revision 2 (22 October 2026)

On 22 October 2026 the owner confirmed [ADR-0011](../../architecture/0011-postgresql-durable-execution.md): E04's durable orchestration is a Lotuspond-owned state machine in product PostgreSQL, not Temporal. This revision re-plans Tasks 7–14 on top of the implemented Tasks 1–6 (`7bbb92d`), whose approved text is unchanged apart from the notes below. Nothing from either version of Tasks 7–14 is implemented. **The owner approved this revision on 22 October 2026. Implementation of Tasks 7–14 starts when the owner asks for it.**

What changes, in short:

- **Engine.** Orchestrator workers advance executions in short transactions over the schema-v14 records, holding the execution row lock as the fence. A sweeper handles due waits, deadlines, lapsed leases and expired deliveries. There is no engine server, no separate orchestrator distribution and no new dependency.
- **Schema.** One additive v15 migration adds the runnable marker `executions.runnable_at_ms` and global partial indexes for claims and sweeps.
- **Outbox.** It stays the durable record of start, signal and control requests, but the orchestrator consumes it in the transaction that applies it. There is no lost-acknowledgement window between stores.
- **Replay.** Temporal history replay and continue-as-new disappear. Progression is a deterministic function of committed rows, interpreter versions are pinned and claimed only by workers that implement them, and golden progression fixtures guard interpreter changes.
- **Tasks 9, 10 and 12** were already engine-neutral and change only where they meet orchestration. **Tasks 7, 8, 11, 13 and 14** are rewritten.
- **Spike checks.** The checks that need consequential writes move to E05's suite, which reuses Task 13's harness, rather than being weakened.

### Temporal assumptions in completed Tasks 1–6

None of Tasks 1–6 imports an engine, but several of their records and texts were shaped for Temporal. Each stays valid and is reused as follows.

| Where                                                                                                                                                                   | Temporal assumption                                                             | Under ADR-0011                                                                                        | Change and owning task                                                                                                                             |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Global constraint "keep engine persistence separate"; `execution/migration.py` docstring "No engine persistence … belongs here"                                         | Engine state lives in Temporal's own database                                   | Engine state is product schema                                                                        | Constraint revised below; Task 7 adds v15 through `initialize.py`                                                                                  |
| `executions.engine_identity`, unique and set to a random ID by `AdmissionService.admit_execution`                                                                       | Opaque Temporal workflow ID                                                     | No external engine. The PostgreSQL engine never reads or exposes it                                   | Kept and still populated, reserved for a replacement adapter's external identity. No schema change                                                 |
| `executions.interpreter_id`                                                                                                                                             | Routed histories to a Temporal workflow type and task queue                     | Pins interpreter semantics; workers claim only versions they implement                                | Kept. Task 7 claim filter; Task 11 registry                                                                                                        |
| `execution_outbox.kind` values `engine_start`, `engine_signal`, `activity`, `control`, written by `admission.py` and `inbox.py`                                         | Commands published to a second store                                            | Durable record of each wake-up cause, consumed in the orchestrator's transaction                      | Names and check constraint kept. `activity` is unused by the PostgreSQL engine. Task 7 adds the runnable marker to admission and signal acceptance |
| `outbox.py` `lease_outbox` and `ack_outbox`; docstring "A matching engine acknowledgement is required in a new caller-owned transaction"                                | Two-transaction delivery to Temporal with a lost-acknowledgement window         | No cross-store window                                                                                 | Kept as the protocol for external consumers and a replacement engine; the PostgreSQL engine never calls them. Task 7 rewords the docstring         |
| `execution_reconciliation_observations.code` value `engine_missing`                                                                                                     | A restored engine database lacks a workflow                                     | Cannot occur with one store                                                                           | Kept as a reserved code for replacement adapters                                                                                                   |
| `execution_events` source identity, generation, sequence and up to 100 gap slots                                                                                        | Engine-delivered events arrive out of order                                     | Orchestrator events are gap-free inside its transaction; runtime-reported events still need gap rules | Kept. Task 8 writes orchestrator events; Task 11 applies gap rules to runtime events                                                               |
| Task 5 lock order: installation cursor, workspace, E03 fleet rows, environment capacity, deployment, execution, step, attempt, reservation                              | Progression ran in Temporal activities that called Task 5 services from the top | The orchestrator holds an execution row lock and must not acquire those parents afterwards            | Orchestrator lock order (frozen contracts); deadlock races in Tasks 7 and 13                                                                       |
| Task 5's single `execution_scheduler_cursors` row, locked first by admission, reservation and signal acceptance                                                         | None (engine-neutral)                                                           | Serializes those operations installation-wide                                                         | Measured in Task 13 tier 3 against the thresholds below; redesigned only on evidence                                                               |
| Plan Task 6 Step 4: "Task 13 supplies the real lost-start-ack test"                                                                                                     | A start acknowledgement can be lost between stores                              | That window does not exist                                                                            | Task 13 injects crashes before and after admission and transition commits instead                                                                  |
| Review Focus 1: "dies after remote acceptance but before acknowledgement" (Tasks 6, 9, 13)                                                                              | Applied to engine start and activities                                          | Applies to runtime activity results and to lost HTTP responses                                        | Review Focus 1 revised                                                                                                                             |
| Specification "Engine history carries opaque references"; Task 13 "decoded exported engine histories"                                                                   | Payloads would enter Temporal history                                           | Events and orchestration rows carry codes, digests and artifact references                            | Specification revised; Task 13 decodes event artifacts, projections, logs and errors                                                               |
| Task 1 activity envelope (including `replay_id`, an anti-replay identity); Tasks 3–4 publication, deployments, artifacts and context; Task 5 budget and scheduler logic | None                                                                            | Unchanged                                                                                             | None                                                                                                                                               |

The SDK README's statement that `lotuspond_sdk.activity` "imports no engine or Temporal client" and the SDK distribution test that forbids `temporalio` remain true and stay as guards.

### Findings carried from Tasks 1–6

PR #11's Task 6 review left these for later tasks. Revision 2 assigns them:

- Minor: first-time wait retention needs a final check of its own shorter deadline after provider waits. Fix in Task 8 before progression calls `BudgetService.retain_wait`.
- Minor: standalone `check_release_binding` needs a final publisher-snapshot expiry check. Final branch review, Task 14 Step 4.
- Gate: exact workflow occurrence and source binding (`InboxService` currently denies ambiguous node IDs). Task 8's `resolve_occurrence`.
- Gate: the remaining capacity lifecycle, that is, releases on terminal state, wait satisfaction or expiry, and reconciliation. Tasks 8 and 11, always through Task 5 services.
- Gate: explicit operator capacity provisioning of capacity and scheduler-cursor rows. Task 14's operations procedure; missing setup keeps denying.

### Owner decisions recorded with this revision

The owner approved Revision 2 on 22 October 2026 with the words “approved for revision 2”. Decisions 2–7 were not answered individually. They are recorded as accepted with the recommended option, approved through Revision 2. The owner can reopen any of them before the task it affects is implemented.

| #   | Decision                                                                                                                                                                                 | Recorded outcome (the recommended option)                                                                                                                                      |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Approve Revision 2 of the specification and Tasks 7–14                                                                                                                                   | Approved on 22 October 2026. Implementation has not been requested yet                                                                                                         |
| 2   | The row lock as orchestration fence: transitions are single row-locked transactions, and tokened leases apply only to runtime activity attempts                                          | Accepted. This was an open question in the local design; ADR-0011 decision 3 adopts it                                                                                         |
| 3   | Add the engine's schema as an additive v15, or amend v14 before PR #11 merges (v14 has not been released)                                                                                | Accepted: additive v15. Amending v14 would invalidate Task 2's migration evidence                                                                                              |
| 4   | `execution_events` is unpartitioned in v14. Keep it with the thresholds below and convert through a reviewed, data-preserving migration when one is measured, or partition it now in v14 | Accepted: unpartitioned for E04, with the thresholds                                                                                                                           |
| 5   | A lapsed lease on a read or model attempt                                                                                                                                                | Accepted: the approved specification's rule. The attempt becomes uncertain, and an owner or admin reconciles before progression retries. The spike retried reads automatically |
| 6   | Task 5 serializes admission, reservation and signal acceptance on one installation cursor row                                                                                            | Accepted for now. Measured in Task 13 tier 3; redesigned only if a threshold is exceeded                                                                                       |
| 7   | Spike code and transcripts                                                                                                                                                               | Accepted: kept outside the repository, with SHA-256 receipts in the spike report, as the probe did                                                                             |

---

## File ownership and task sequence

These tasks implement one integrated execution boundary. Independent future product subsystems retain their own approved specifications; they are not folded into this plan.

| Task | Exact ownership (brace lists expand to individual files)                                                                                                                                                                                                                                                                                   | Responsibility                                                                    |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| 1    | `sdk/python/src/lotuspond_sdk/activity/{__init__,models,canonical,signing,schema}.py`; `contracts/runtime-activity.v1.schema.json`                                                                                                                                                                                                         | Additive pure activity wire/signature contract                                    |
| 2    | `backend/lotuspond/execution/{__init__,migration}.py`; existing `models.py`, `initialize.py`, `schema_version.py`, `backend/schema.sql`                                                                                                                                                                                                    | Durable records and strict v14 migration                                          |
| 3    | `backend/lotuspond/execution/{contracts,errors,authorization,publication,deployments,assurance}.py`                                                                                                                                                                                                                                        | V2 publication, pins, release binding and authority                               |
| 4    | `backend/lotuspond/execution/{artifacts,context}.py`                                                                                                                                                                                                                                                                                       | Encrypted values and current source access                                        |
| 5    | `backend/lotuspond/execution/{entitlement,budgets,scheduler}.py`                                                                                                                                                                                                                                                                           | Capacity, shared reservations and fair scheduling                                 |
| 6    | `backend/lotuspond/execution/{admission,inbox,outbox}.py`                                                                                                                                                                                                                                                                                  | Transactional admission and durable delivery                                      |
| 7    | `backend/lotuspond/execution/{engine_contracts,engine,orchestrator}.py`; `backend/lotuspond/execution_worker.py`; existing `models.py`, `initialize.py`, `schema_version.py`, `backend/schema.sql`, `execution/{migration,admission,inbox,outbox}.py` (v15, runnable marker and docstring only), `scripts/python.mjs`, root `package.json` | Engine adapter, v15 claim marker, row-locked claim and worker command             |
| 8    | `backend/lotuspond/execution/{expressions,progression}.py`; existing `execution/inbox.py` (occurrence resolver only)                                                                                                                                                                                                                       | Deterministic typed progression as row transitions                                |
| 9    | `backend/lotuspond/execution/{activity_records,dispatch,leases,reconciliation}.py`; `runtime/python/src/lotuspond_runtime/{activity_protocol,activity_client}.py`                                                                                                                                                                          | Signed dispatch, leases, lapse and expiry handling, historical evidence           |
| 10   | `runtime/python/src/lotuspond_runtime/{activity_worker,activity_invocation,activity_state,agent,providers,context}.py`                                                                                                                                                                                                                     | Bounded read/model runtime, invocation journal and tool validation                |
| 11   | `backend/lotuspond/execution/{control,projections,recovery,sweeper,interpreters}.py`                                                                                                                                                                                                                                                       | Sweeper, pause/cancel/resume, projections, interpreter pinning and restore fences |
| 12   | `backend/lotuspond/execution/{routes,runtime_routes,boundary}.py`; existing `app.py`, `schemas.py`                                                                                                                                                                                                                                         | Authenticated customer/runtime API wiring                                         |
| 13   | `backend/tests/execution/conformance/`; `scripts/checks/{execution-smoke.py,execution_process.py,execution-load.py}`; `backend/tests/execution/test_installed_acceptance.py`; `deploy/checks/test_durable_execution_smoke.py`                                                                                                              | Conformance tiers, real restart/restore/load and failure qualification            |
| 14   | CI, packaging, operations and capability documentation listed in Task 14                                                                                                                                                                                                                                                                   | Reproducible delivery and complete evidence                                       |

All new directories get inert `__init__.py` where needed. Existing files named without a full path in this table are under `backend/lotuspond/`. Tests live beside their owning distribution as listed per task. Keep pure protocol imports free of environment reads, files, sockets, threads and provider loading.

Tasks run in sequence; review each before the next. Every implementer gets the spec, global contracts, their task and immediate dependency interfaces. Do not delegate a whole subsystem with permission to skip task gates. Preserve existing worktrees, evidence and failed attempts. Use the retained subagent workflow, then whole-branch review; resolve findings before final qualification.

## Frozen cross-task contracts

### Activity wire and vectors (Task 1)

New namespace `lotuspond_sdk.activity` has no Temporal or runtime dependency. Reuse public SDK `ArtifactRef`, safe integer/identifier/version and JSON-domain rules without modifying old validators/frames. Types are frozen/strict, unknown fields and coercions refused; exported helpers revalidate constructed or mutated instances. All IDs below except `installation_id` are canonical lowercase UUID strings; installation uses existing 1..128 ASCII opaque-ID grammar. Digests are 64 lowercase hex; timestamps/epochs/generation are exact safe integers, timestamps nonnegative, generation at least 1. Named versions and operations use existing SDK grammars.

`ArtifactHandle(id: str, version: int, sha256: str)` uses version 1..2^31-1. `ActivityEnvelope` has exactly these fields, all required:

| Fields                                                                     | Type / invariant                                                                                          |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `schema_version`                                                           | literal `lotuspond.runtime-activity.v1`                                                                   |
| `installation_id`, `recovery_epoch`                                        | opaque installation, current epoch >=0                                                                    |
| `workspace_id`, `project_id`, `environment_id`                             | complete tenant scope                                                                                     |
| `execution_id`, `step_instance_id`, `attempt_id`, `deployment_revision_id` | exact persisted identities                                                                                |
| `package_sha256`, `plan_sha256`, `configuration_sha256`                    | pinned identities, never interchangeable with connection digest                                           |
| `input`                                                                    | `ArtifactHandle`; no embedded payload                                                                     |
| `kind`, `dependency`, `operation`                                          | `connector_read` + connector `ArtifactRef` + operation, or `model` + model `ArtifactRef` + null operation |
| `pool_id`, `instance_id`, `generation`                                     | exact recipient                                                                                           |
| `delivery_id`, `replay_id`                                                 | independently unique IDs in installation/epoch/workspace domain                                           |
| `issued_at_ms`, `expires_at_ms`                                            | `0 < expires-issued <= 300000`; runtime also checks current time and persisted attempt deadline           |

`ActivitySignature` mirrors the existing detached header shape with fixed values: `format_version=1`, `algorithm=ed25519`, `context=lotuspond-runtime-activity-signature-v1`, `schema_version=lotuspond.runtime-activity.v1`, `canonicalization=lotuspond.runtime-activity.jcs.v1`, `key_id=SHA256(raw public key)`, `sha256=SHA256(canonical activity)`, `signature=128 lowercase hex`. Frame = ASCII context + NUL + 8-byte big-endian header length + sorted compact ASCII JSON header without signature + 8-byte big-endian content length + captured JCS bytes. No trailing newline in either canonical byte identity. Never select trust from a key included by the sender. The new verification report has `valid`, `code` in `verified|untrusted_key|revoked_key|digest_mismatch|invalid_signature`, `sha256`, `key_id`, `binding_status='not_checked'`, `authority_granted=False`; it does not extend existing report enums.

Transport `ActivityDelivery(envelope: str, signature: str)` carries the exact UTF-8 JSON documents as strings; after unescaping, their **combined** length is <=65536. Bound the outer JSON body to 131072 bytes as well, depth 32 and 10000 items; reject duplicate keys, invalid Unicode and nonfinite numbers before validation. Structural signature/content validation does not authorize claim.

Literal vector: construct UUID `u(n) = '00000000-0000-4000-8000-' + twelve decimal digits of n`. Set workspace/project/environment/execution/step/attempt/deployment/input/pool/instance/delivery/replay IDs to `u(1)` through `u(12)` respectively, input version 1, installation `fixture-installation`, epoch/generation 1, kind `connector_read`, operation `inspect`, dependency `{kind:'connector', name:'fixture-reader', version:'1.0.0', sha256:'e'*64}`, package/plan/config/input digests `'a'*64`/`'b'*64`/`'c'*64`/`'d'*64`, issue `1800000000000`, expiry `1800000300000`. Include all required fields and no others. Its compact JCS is **1375 bytes**, SHA256 **`06abd378a53efb058bf9b880d97f1952d23db32a50b8ffd15d8a658fc06e2203`**.

Test-only RFC8032 seed `9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60` gives public key `d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a`, key ID `21fe31dfa154a261626bf854046fd2271b7bed4b6abe45aa58877ef47f9721b9`, and signature **`c67f75cdd0794a433183c5afe18cdd4e699504e89f2efd2c3f01410b6eec5bf3dbaa7c88d8925f2fa203f36d8295782140dd992625bed07cdc8bae9f0bf74608`**. Calculated during planning independently of the proposed SDK code using sorted scalar JSON, SHA256 and cryptography, then independently verified with Node SHA256/Ed25519; Task 1 freezes literal files, verifies JCS/Node agreement and wrong-context rejection before backend/runtime integration. This public test key is never accepted in production.

### Authority, records and service types (Tasks 2–6)

Use existing `lotuspond_runtime.protocol.Scope`, `RuntimeIdentity`, `ConnectionConfig`, `ConnectorPin`, `SecretPin`; existing SQLAlchemy `Session`; existing `security.Principal` only as an authenticated transport result. Task 3 defines `ExecutionActor(user_id, api_key_id: str|None, scope: Scope, role)` after reloading current creator/membership. Service operations lock and reauthorize persisted actor/deployment/identity records; an `ExecutionActor` is not a reusable authorization token. Existing `authenticate()` returns `role='service'` for keys without proving creator membership, so new execution authorization explicitly resolves that membership and role on every operation without widening legacy routes.

Task 3 `contracts.py` owns strict request/DTO types: `PublicationRequest(package: object, dependency_ids: tuple[str,...], signature: object)`; `PublicationView(id, package_sha256, plan_sha256, validator_id, compiler_id)`; `DeploymentConfig(bindings: tuple[DependencyBinding,...], limits: ExecutionLimits)`; `DependencyBinding(dependency: ArtifactRef, connection_id: str|None, connection_version: int|None, source_id: str|None, source_version: int|None)` with complete pairs and exactly the combination required by the catalog; `DeploymentRequest(deployment_id: str|None, publication_id, pool_id, configuration: DeploymentConfig, release_id: str|None)`; `DeploymentView(id, revision_id, status)`; `ExecutionView(id, state, requested_state, reason, admitted_at_ms, deadline_ms, result: ArtifactHandle|None, next_event_sequence)`; `AdmissionRequest(deployment_revision_id, input: object)`; `SignalRequest(signal_id, wait_id, payload: object)`; `SignalReceipt(id, status)`; `ControlRequest(request_id, action: Literal['pause','resume','cancel'])`. `ExecutionLimits` contains the package's seven exact step/time/wait/token/cost limits, plus active and pending capacities bounded by the global ceilings; ceilings never create permission to spend.

Common IDs in services are canonical strings. All customer records carry workspace/project/environment composite FKs, including immutable publication records (v2 is admitted into an environment), dependency links, artifacts, budgets, waits, inbox/outbox and event rows. Every referenced parent ID is constrained with the same scope; global installation scheduler rows contain no customer payload. Immutable artifacts/source pins are not stored as unenforced JSON links. Database uniqueness enforces idempotency, delivery/replay identities, source sequences, attempt ordinals and usage settlement. Task 2 adds these ORM records to the existing authoritative `Base`:

`ExecutablePublication`, `ExecutableDependency`, `ExecutionDeployment`, `ExecutionDeploymentRevision`, `DeploymentBinding`, `DeploymentAssuranceBinding`, `ExecutionArtifact`, `ExecutionContextSource`, `ExecutionContextVersion`, `Execution`, `ExecutionStepInstance`, `ExecutionAttempt`, `ExecutionWait`, `ExecutionRequest`, `ExecutionOutbox`, `ExecutionInbox`, `ExecutionEvent`, `ExecutionControlRequest`, `ExecutionUsageReservation`, `ExecutionCapacity`, `ExecutionSchedulerCursor`, `ExecutionActivityDelivery`, `ExecutionLease`, `ExecutionReconciliationObservation`. Model names are distinct from SDK wire types and evaluation `Run`.

Persist exact canonical package/plan/config/dependency bytes, hashes and validator/compiler identifiers. Dependencies reference existing admitted exact v2 publications or admitted catalog records; no request contains a URL to resolve. Store normalized pin/source/connection/secret links with full-scope FKs and verify agreement with canonical bytes on read. Execution pins deployment revision, interpreter ID, opaque engine identity (unused by the PostgreSQL engine; see Revision 2), input artifact, initiating user/key, absolute deadline and admission digest. Step instances key on execution plus canonical occurrence path (region/branch/iteration/subworkflow); attempts have increasing ordinal and immutable input identity. No attempt becomes reclaimable merely because its lease expires. Child attempts additionally have a full-scope parent-attempt FK and unique parent/request ID+digest. A deployment may gain a new immutable revision without invalidating already admitted revisions; global disable/revoke and explicit per-revision disable/revoke override old pins. Activation selects the revision for future work, not a silent migration of existing executions.

Execution states: `pending`, `running`, `waiting`, `paused`, `cancelled`, `succeeded`, `failed`, `reconciliation_required`; requested state is separately `running|paused|cancelled`. Attempt states: `reserved`, `dispatched`, `claimed`, `succeeded`, `failed`, `uncertain`, `cancelled`. Wait states: `open`, `satisfied`, `expired`, `cancelled`. Outbox/inbox use `pending`, `leased`, `delivered`, `conflict`; retries do not delete evidence. `succeeded` is a workflow result, never a verified business outcome. Unknown outcomes cannot be promoted to success by projection.

Task 4 defines `ArtifactPolicy(classification, source_id: str|None, source_version: int|None, retain_until_ms, access_policy_id, key_id)`; `ContextRequest(source_id, version, required_freshness_seconds, allowed_classifications)`; `AuthorizedContext(value: ArtifactHandle, citation: ArtifactHandle)`. All metadata is checked server-side; client-supplied policy IDs grant nothing. `ArtifactKeyProvider.key(scope: Scope, key_id: str) -> bytes` returns a 32-byte AES-GCM key through a bounded independent provider; no key in DB, engine, logs or package. AES-GCM uses fresh 12-byte nonce and AAD binding format `lotuspond.execution-artifact.v1`, full scope, artifact ID/version/digest/classification/source/key identity. Ciphertext-only DB backups still require a separately retained qualified key provider.

Task 5 defines `ExecutionEntitlement.current(scope: Scope, now_ms: int) -> EntitlementGrant` where grant binds scope, installation/epoch, revision, expiry, allowed `execute` and explicit numerical limits. Default raises `entitlement_unavailable`; telemetry `plan_for()` is never consulted as an execution grant. `BudgetDemand(input_tokens, output_tokens, cost_microusd, active_ms)` uses nonnegative safe integers. Task 1 owns the shared pure `UsageObservation(input_tokens: int|None, output_tokens: int|None, cost_microusd: int|None, completed_at_ms: int)` in `activity/models.py`, with explicit unknowns and no authority. Backend and runtime import that same type. `ReservationView(id, attempt_id, state)` and `ScheduledAttempt(attempt_id)` are safe internal outputs. The root execution budget includes every child/retry/tool attempt. A separate record tracks uncertain liability after compute is confirmed stopped. `ChargeAuthority.bound(dependency: ArtifactRef, demand: BudgetDemand) -> BudgetDemand` must provide a current conservative maximum charge, never an optimistic estimate; a missing or unbounded charge refuses dispatch.

### Orchestration and runtime interfaces (Tasks 7–11)

**Engine contracts.** Task 7's `backend/lotuspond/execution/engine_contracts.py` is inert: it has no database session, worker or provider import. It defines:

- `INTERPRETER_V1 = "lotuspond.execution-interpreter.v1"`.
- `WorkerIdentity(worker_id: str, role: Literal['orchestrator','sweeper'], interpreter_ids: tuple[str, ...])`. It comes from operator configuration and grants no authority.
- `AdvanceOutcome(execution_id, transitions: int, state, runnable: bool, reason: str|None)`.
- `ProgressReport(claimed: int, transitions: int, deferred: int)`.
- `SweepReport(timers: int, expired_waits: int, deadlines: int, lapsed_leases: int, expired_deliveries: int)`.
- `EngineObservation(execution_id, interpreter_id, state, requested_state, runnable: bool, reason: str|None)`.
- `InterpreterCheck(compatible: bool, code: str)`.

None of these carries payloads, customer names or caller-chosen routing.

`ExecutionEngine` is a `Protocol` with:

- `interpreter_ids: frozenset[str]`;
- `progress(sessions, worker, *, limit: int) -> ProgressReport`;
- `sweep(sessions, worker, *, limit: int) -> SweepReport`;
- `describe(db, execution_id: str) -> EngineObservation`;
- `check_interpreter(interpreter_id: str) -> InterpreterCheck`.

`sessions` is a callable that opens a new `Session`. Each claimed execution and each swept item gets its own transaction. `PostgresExecutionEngine` is the only implementation. The conformance suite (Task 13) parametrizes over registered implementations, so a replacement engine must pass it. Backend services never import a worker module.

**Schema v15.** Task 7's `migrate_execution_v15(connection)` runs under the existing caller transaction and advisory lock, refuses anything but exact v14 and partial v15 objects, and writes version 15 last. It:

- adds nullable `executions.runnable_at_ms BIGINT`, with checks for a safe nonnegative integer and for `NULL` whenever the state is `succeeded`, `failed` or `cancelled`;
- adds partial indexes:
  - `execution_runnable_idx (interpreter_id, runnable_at_ms, id) WHERE runnable_at_ms IS NOT NULL`;
  - `execution_wait_due_idx (deadline_ms, id) WHERE state = 'open'` on waits;
  - `execution_lease_due_idx (expires_at_ms, id) WHERE status = 'active'` on leases;
  - `execution_delivery_due_idx (expires_at_ms, id) WHERE status = 'pending'` on activity deliveries;
- sets the marker for non-terminal executions that hold a pending `engine_start`, `engine_signal` or `control` outbox row, so v14 admissions made before the upgrade are not stranded.

It alters no other column, constraint or row.

**Runnable marker.** `runnable_at_ms` is not null exactly while the execution has orchestrator work due at or after that time. Only Task 7's `mark_runnable(db, execution, at_ms)` sets it, and its caller already holds the execution row lock. The callers are admission and signal acceptance (Task 7), control requests (Task 11), activity results, expired deliveries and reconciliation (Task 9; a lapsed lease leaves the execution for reconciliation instead), and the sweeper and self-continuation (Tasks 8 and 11). The orchestrator clears it when no further transition is possible without an external event.

**The row lock is the fence.** An orchestrator transaction:

1. reads due candidates without locks, ordered by `runnable_at_ms` and `id` and filtered by the worker's interpreter IDs and `runnable_at_ms <= database_now_ms`;
2. for the first candidate, takes its workspace row `FOR KEY SHARE`, then its execution row `FOR UPDATE SKIP LOCKED`, rechecking that it is still due; a locked or no-longer-due candidate is skipped;
3. consumes that execution's pending start, signal and control outbox rows;
4. applies at most 64 pure transitions;
5. writes rows and events, and commits.

**Amendment proposed in Task 7's review, for the owner's acknowledgement:** a candidate whose workspace row is locked is skipped like a locked execution, so one tenant's API transaction cannot stall claims elsewhere. The known cost is that a tenant with continuous admission, signal or reservation traffic can have its due executions skipped repeatedly; Task 13 tier 3 measures this alongside owner decision 6. Work that made no progress (a refused transition, a pending capacity step or an empty pass) is marked due again only after a back-off, never immediately; repeated refusals back off exponentially per worker up to 60 s, and Task 11's reconciliation fences executions that keep failing. No network or provider call happens inside it. The worker's database role sets `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout`, initially 15 s, so a stalled worker's transaction is ended and rolled back. Tokened leases exist only for runtime activity attempts.

**Orchestrator lock order.** A pure transition never acquires the installation cursor, environment capacity, runtime pool, connection or usage-reservation rows. Some transitions change capacity counters: retaining or releasing wait quota, and releasing pending or active counts at a terminal state. Those run through Task 5 services in a separate transaction that follows Task 5's documented order from the installation cursor, acting for the execution's initiating principal after current reauthorization, exactly as `Scheduler.schedule_batch` does. When such a transition is next, the pure transaction leaves the execution runnable with reason `capacity`. PostgreSQL deadlock tests race orchestrator transitions with admission, reservation, renewal, revocation and cancellation.

**Progression.** Task 8 provides:

- `load_progression(db, execution, region_path) -> ProgressionState`, which reads only the affected region occurrence's committed step, attempt, wait and inbox rows;
- pure `next_actions(plan: CompiledPlan, state: ProgressionState, now_ms: int) -> tuple[Action, ...]`;
- `advance_execution(db, worker, execution, *, bound=64) -> AdvanceOutcome`;
- `apply_capacity(db, worker, execution_id) -> AdvanceOutcome` (amendment proposed in Task 7's review, for the owner's acknowledgement). The engine calls it in its own transaction after a pure transaction returns `runnable` with reason `capacity`. It enters Task 5's order from the installation cursor before locking the execution and must be idempotent, because another orchestrator may run the same step concurrently. Its `transitions` count is the number of capacity changes made; only a nonzero count makes the execution due again at once.

`Action` is a closed union of `RecordPure`, `CreateAttempt`, `OpenWait`, `ConsumeSignal`, `CancelUndispatched`, `Complete` and `Fail`. **Conventions fixed by Task 8, for the owner's acknowledgement:**

- **Occurrence paths.** A path is `segment ("/" segment)*`. A segment is a step ID, optionally followed by `:` and a region selector (`then`, `else`, a branch ID, an item index or `call`), or by `#` and a record name (`select`, `items` or `input`, a composite's pinned record). `program.resolve_occurrence` resolves a path. It is also how Task 5's budget dependencies and Task 6's inbox find their node.
- **Records.** Pure steps, composites and region occurrences complete with one attempt whose input and result artifact hold the value. A failed attempt's result artifact is `{"failure": code, "failed_at_ms": ms}`. Task 9 records activity failures the same way, and the activity's retry decision reads that record.
- **Waits.** A wait's key is `w:` + SHA-256 of its path.
- **`pending`.** An execution stays `pending` while it holds its admission slot and no activity has been reserved yet, even while pure steps progress; Task 5's first reservation or wait retention moves it on, and `release_pending` returns the slot if it ends first. Task 11 projections must not present `pending` as "not started".
- **Capacity releases.** These are Task 5 services entered from the installation cursor: `release_wait`, keyed by `capacity.wait-release:<wait>`, and `release_pending` at a terminal state, keyed by `capacity.pending-release:<execution>`. Attempts cancelled before delivery have their reservation stopped in the capacity step.
- **Paths.** Composite records use `#` (`decision#select`, `iteration#items`, `subworkflow#input`), so no branch ID can collide with them. A path longer than the stored 1,024 characters fails its region with `occurrence_unresolved`.
- **Signal timeliness.** A signal is judged at acceptance: it is valid if it was accepted before the earlier of its expiry and the wait's deadline, however late a worker consumes it. Task 11's sweeper may mark such a wait `expired` first; progression still loads the pending signals of an expired wait, and the in-time signal satisfies it. Signals still pending when their wait closes are refused (`conflict`).
- **Capacity step order.** `apply_capacity` takes every Task 5 parent before the execution row, then applies releases, stops of undelivered cancelled attempts and the terminal state. It retains each wait last, in its own savepoint, so a quota, pause or authority refusal leaves only that wait unretained. Consequence for the owner: an unretained wait stays open and can still be satisfied, so the 10,000 retained-wait ceiling bounds retained quota, not the number of open waits. While an open wait is unretained the execution stays due for its capacity step, which backs off like any refusal (a step that changes nothing counts as one, up to 60 s). That back-off never throttles other work. A capacity pass keeps its own wake-up (a sibling's retry back-off, for example), and the earliest time wins. A pass stopped by the 64-transition bound continues at once and skips its capacity step, and the first pass that finishes finds pending capacity work from committed state (an unretained open wait, a closed wait not yet released, or a cancelled undelivered attempt not yet stopped). Retention is skipped without a provider call when the quota is visibly full, and not tried at all while the execution is paused or cancelling; resuming marks it due. Only fixed refusal codes are deferred; any other error aborts the capacity transaction. A terminal state waits for containment: no reserved, dispatched, claimed or uncertain attempt and no unstopped reservation. **Obligation for Tasks 9 and 11:** every caller of `confirm_stopped`, a result, a lapse or a reconciliation that can complete containment calls `mark_runnable` in the same transaction.
- **Provider calls under the row lock.** The engine passes the recovery epoch it fenced before claiming, so progression does not call the RecoveryAuthority while it holds the row. The only bounded provider call inside a transition is the artifact key provider, at most once per scope and key per transaction (5 s bound, below the 15 s role timeouts), because encrypted results commit with the transition. No network or customer-system call happens.
- **Interfaces as implemented.**
  - `resolve_occurrence`, `next_actions` and `derive` take the verified `Program`: the pinned package closure plus its `CompiledPlan`, which must equal the SDK's recompilation of that closure.
  - `load_progression`, `advance_execution` and `apply_capacity` are methods of the explicitly constructed `InterpreterV1`. `advance_execution(db, worker, execution, *, bound=64, epoch)` and `apply_capacity(db, worker, execution_id, *, epoch)` take the recovery epoch the engine fenced before claiming. `None` is only for direct callers such as tests: the method then fences under the locks its caller already holds.
  - `load_program` recompiles the pinned closure on a cache miss (64 entries per process) and compares the plan bytes. A future SDK compiler that changes plan bytes would refuse in-flight executions as `integrity` until a new interpreter version pins the old compiler; Task 11's interpreter registry owns that rule.
  - Files beyond Task 8's row, each necessary:
    - `execution/program.py`: the resolver and the verified loader.
    - `execution/artifacts.py`: actorless `_put_internal` and `_get_internal` for workspace-policy values only. Source-bound artifacts are refused, and current source ACLs still require an authenticated actor (SECURITY.md).
    - `execution/budgets.py`: the releases, the carried `retain_wait` deadline check, and `_dependency` resolving by exact occurrence.
    - `execution/engine.py` and `execution/engine_contracts.py` (Task 7): the epoch is passed in, and a capacity step that changes nothing backs off.
    - Tests: `test_budgets.py`, `test_scheduler.py`, `test_inbox.py`, and Task 7's `engine_fixtures.py` and `test_orchestrator.py`.
  - The pure function never reads a clock, randomness, environment, network or provider; it receives database time as `now_ms`. Applying the same actions twice creates no rows, because the unique occurrence-path and attempt-ordinal keys of v14 make repeats no-ops.

**Sweeper and controls.** Task 11's `sweep` processes due rows through the v15 indexes, each in its own transaction with `SKIP LOCKED` and database time. `request_control` persists the control request and its outbox row and marks the execution runnable under the execution lock. Task 9's `dispatch_activity` checks `requested_state == 'running'` under the same lock, so a committed cancellation or pause precedes any later dispatch. `RecoveryFence.current(db)` reads the current epoch from the independent RecoveryAuthority. Orchestration, sweeping and dispatch refuse while it is unavailable.

Task 9 runtime `activity_protocol.py` defines `PinnedActivityRecords(envelope: ActivityEnvelope, connection: ConnectionConfig|None, dependency: ArtifactRef, deadline_ms: int, input_schema: object, output_schema: object, request_sha256: str)`, `ActivityLease(attempt_id, generation, lease_revision, expires_at_ms, deadline_ms)`, `ActivityResult(attempt_id, lease_revision, result: ArtifactHandle|None, code, usage: UsageObservation)`, `ChildActivityRequest(request_id, parent_attempt_id, kind: Literal['connector_read','model'], declaration_name, arguments: ArtifactHandle)`, and `HistoricalObservation(observation_id, attempt_id, delivery_sha256, result: ArtifactHandle|None, code, usage: UsageObservation)`. Historical observations are evidence, not a lease/permission or a way to overwrite an existing result. `ActivityTrustAuthority.current(scope: Scope, now_ms: int) -> TrustSet` uses separately provisioned activity trust, never automatic publisher/action trust reuse. Runtime imports shared SDK types, never backend types.

Task 10 providers have `ReadProvider.invoke(records: PinnedActivityRecords, arguments: object, deadline_ms: int) -> ProviderResult` and `ModelProvider.generate(request: ModelRequest, deadline_ms: int) -> ModelReply`. `ModelRequest` contains pinned model/instructions, validated input/context handles resolved only in worker memory, declared read-tool schemas and remaining per-call limits; `ModelReply(output: object|None, tool_calls: tuple[ToolProposal,...], usage: UsageObservation)`; `ToolProposal(name, arguments: object)`; `ProviderResult(value: object, usage: UsageObservation)`. No model output supplies catalog entries, URLs, secret paths or executable code. Constructor-selected fixed providers have immutable descriptors/destination/price/max-charge records; missing or changed pins deny.

## Verification convention

Run from the implementation worktree root after frozen sync. `B = uv run --frozen --project backend pytest`, `S = uv run --frozen --project sdk/python pytest`, `R = uv run --frozen --project runtime/python pytest` are command prefixes, not additional scripts. Each task's Step 2 runs its stated test command before implementation and records the missing behavior/interface; Step 4 repeats it after implementation and requires PASS. A missing new module is an acceptable initial red, a malformed test or unrelated setup failure is not. Then run the changed distribution's Ruff check. Both independent reviews inspect code and evidence, not merely the implementer's summary.

PostgreSQL tests follow existing guarded `LOTUSPOND_PLATFORM_TEST_DATABASE_URL`, independently verified loopback/database `lotuspond_platform_test`, unique owned schema, separate connections and deterministic barriers. No ambient external database or shared reset. Qualification has zero database-related skips. Tier 2 and tier 3 use separately owned disposable PostgreSQL clusters on private ports that refuse 5432, 55439 and the development database. Tests advance time only through the engine clock (database time plus a harness offset), never by editing deadline, lease or expiry columns. Every process/container/schema/volume is labeled, individually owned and cleaned with a positive control and observed receipt; retain dumps/reports and failures. Test clocks do not replace DB time in actual race tests.

## Task 1: Add the frozen activity contract

**Files:** Create ownership-table files; `sdk/python/tests/activity/{test_wire.py,test_signing.py,test_schema.py}`, `sdk/python/tests/activity/fixtures/{read.json,read.canonical.json,read.signature.json}`. Modify `sdk/python/pyproject.toml`, `sdk/python/uv.lock`, `sdk/python/README.md` for SDK 0.3.0; retain old namespace/CLI behavior. Additive schema is separate from existing executable schema output.

**Interfaces:** `validate_activity(raw: object) -> ActivityEnvelope`, `canonical_activity_json(raw: object) -> bytes`, `activity_digest(raw: object) -> str`, `decode_activity_delivery(content: bytes) -> tuple[ActivityEnvelope, ActivitySignature]`, `sign_activity(raw: object, key: Ed25519PrivateKey) -> ActivitySignature`, `verify_activity(raw: object, signature: object, trust: TrustSet) -> ActivityVerificationReport`, `activity_schema() -> dict`. `ActivityVerificationReport` is the fixed report defined above.

- [ ] **Step 1:** Add `test_literal_vector_and_distinct_frame`, `test_combined_limit_and_lifetime`, `test_mutated_model_and_scope_fields` and schema parity tests. Assertions: `len(canonical)==1375`; digest/signature equal the literal vectors; `report.authority_granted is False`; valid JSON padded with permitted whitespace to 65536 combined bytes accepted structurally, 65537 refused; 300000-ms lifetime accepted, 300001 refused; old command/package signatures and boolean timestamps refused. Exercise Unicode, duplicate keys, exact maximum IDs, model-operation combinations and unknown usage versus zero; valid signature with changed field fails verification.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert len(canonical) == 1375
assert activity_digest(read_fixture) == "06abd378a53efb058bf9b880d97f1952d23db32a50b8ffd15d8a658fc06e2203"
assert report.authority_granted is False
```

- [ ] **Step 2:** Run `S sdk/python/tests/activity -q`; record red before implementation.
- [ ] **Step 3:** Implement the interfaces using bounded projection/JCS and cryptography Ed25519. Freeze the three literal fixture files and independently verify the canonical hash with Node. Export only the new namespace; do not extend existing runtime-command validators.
- [ ] **Step 4:** Run `S sdk/python/tests/activity sdk/python/tests/executable -q`; PASS with unchanged old byte fixtures. Check schema output and inert imports, build/install SDK 0.3.0 in isolation on both Python versions.
- [ ] **Step 5:** Commit only Task 1 owned files as `feat: add separately signed read and model activity contract`; specification and quality reviews gate Task 2.

## Task 2: Preserve data through additive schema v14

**Files:** Ownership-table Task 2 files; `backend/tests/execution/{conftest.py,test_schema.py,test_migration.py}`, `backend/tests/execution/fixtures/schema-v13.sql` from the committed baseline. Modify `backend/{pyproject.toml,uv.lock}` and `runtime/python/{pyproject.toml,uv.lock}` to consume SDK 0.3.0; no Temporal dependency.

**Interfaces:** `migrate_execution_v14(connection: sqlalchemy.Connection) -> None` under the existing caller transaction/advisory lock; all ORM records/states and full-scope links specified above. Define constraints/indexes for queue scheduling, absolute deadlines, uniqueness, leases and retention; store bytes as `LargeBinary`, safe integer timestamps, no secrets or free-text provider errors in events.

- [ ] **Step 1:** Add `test_v13_upgrade_preserves_all_rows`, `test_fresh_upgrade_signature_equal`, `test_full_scope_fk_matrix`, `test_partial_v13_refused_atomically`, `test_attempt_and_event_uniqueness`. Assertions: `after_legacy_rows == before_legacy_rows`; `fresh_signature == migrated_signature`; cross-environment deployment/artifact/attempt/signal link raises integrity error; failed preflight leaves version 13 and all rows unchanged. Include corrupt preexisting execution tables, wrong indexes/constraints and caller rollback after the migration function returns.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert after_legacy_rows == before_legacy_rows
assert fresh_signature == migrated_signature
assert failed_upgrade_version == 13
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_schema.py backend/tests/execution/test_migration.py -q` against isolated PostgreSQL; record red.
- [ ] **Step 3:** Implement exact preflight from v13 and additive tables/constraints; write version 14 last. Use existing schema generation/parity machinery. Normalize dependency and connection-secret links rather than trusting JSON alone; use composite keys on every customer relationship.
- [ ] **Step 4:** Repeat Step 2 plus `B backend/tests/test_migrations.py backend/tests/fleet/test_migration.py -q`; PASS with zero required DB skips, populated v13 fixtures preserved and fresh/migrated schema parity.
- [ ] **Step 5:** Commit the exact schema/model/metadata/test files as `feat: persist scoped durable execution records with v14 migration`.

## Task 3: Admit exact v2 packages and deployment revisions

**Files:** Ownership-table Task 3 files; `backend/tests/execution/{test_publication.py,test_deployments.py,test_authorization.py,test_assurance.py}`. Use existing `packages.py`, SDK compiler/signing and fleet catalog as references; do not repurpose v1 endpoints.

**Interfaces:** `authorize_execution(db: Session, principal: Principal, scope: Scope, operation: str) -> ExecutionActor`; `publish_v2(db, actor: ExecutionActor, request: PublicationRequest, request_key: str) -> PublicationView`; `create_deployment(db, actor, request: DeploymentRequest, request_key: str) -> DeploymentView`; `set_deployment_status(db, actor, deployment_id: str, revision_id: str|None, status: Literal['enabled','disabled','revoked'], request_key: str) -> DeploymentView`; `check_release_binding(db, actor, publication_id: str, release_id: str|None) -> None`. Operations are fixed `publish|manage|execute|signal|human_form|read|control|reconcile`, not arbitrary permission strings. `ExecutionError(code: str)` in `errors.py` admits a closed fixed-code enum; never render constructor arguments or caught exception text. A non-null deployment ID publishes another revision into that existing scoped deployment; status with revision ID addresses that revision, while null addresses the deployment as a whole. Enabling a revision selects it for future admissions.

- [ ] **Step 1:** Add `test_published_bytes_and_closure_are_exact`, `test_old_revision_stays_pinned`, `test_effect_nodes_refused`, `test_key_creator_demotion_after_lock_wait`, `test_evaluation_id_is_not_release_authority`. Assertions: stored bytes/hash equal SDK canonical outputs; missing/transitive/wrong catalog pin denies; viewer or demoted key cannot execute; a passing evaluation for another package/revision cannot activate deployment; forms pass, review/intent/dispatch/verification fail with `unsupported_step`.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert stored_package_bytes == canonical_executable_json(package)
assert demoted_key_error.code == "forbidden"
assert effect_node_error.code == "unsupported_step"
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_publication.py backend/tests/execution/test_deployments.py backend/tests/execution/test_authorization.py backend/tests/execution/test_assurance.py -q`; record red.
- [ ] **Step 3:** Implement strict requests/DTOs and current authorization. Cap publication body to 1MiB and closure to SDK limits; retrieve dependencies only from same-scope admitted records/operator catalog. Independent publisher trust verifies exact bytes and allowed scope. Bind real assurance results to exact package/configuration evidence through `DeploymentAssuranceBinding`, not caller IDs or arbitrary candidate revision text; if existing assurance cannot establish the applicable identity, deny with `assurance_unavailable`. Evaluate real release policy/independent approvals; record identities, never synthesize a pass. Resolve and normalize all model/tool/context/connection/secret pins. Reject unsupported escalation/assignment policies instead of silently dropping them.
- [ ] **Step 4:** Repeat Step 2 and existing registry tests; PASS. Concurrent publication/activation cannot create mutable versions or bypass a changed role/policy. Existing v1 canonical bytes/endpoints remain unchanged.
- [ ] **Step 5:** Commit Task 3 files as `feat: admit scoped executable deployments with current release authority`.

## Task 4: Store encrypted artifacts and authorize context

**Files:** Ownership-table Task 4 files; `backend/tests/execution/{test_artifacts.py,test_context.py}`.

**Interfaces:** `ArtifactStore.put(db, actor: ExecutionActor, content: bytes, policy: ArtifactPolicy) -> ArtifactHandle`; `ArtifactStore.get(db, actor, handle: ArtifactHandle) -> bytes`; constructor requires `ArtifactKeyProvider` and current source-access provider. `ContextSource.resolve(db, actor, request: ContextRequest) -> AuthorizedContext`. Add `SourceAccess.current(actor, source_id: str, version: int, now_ms: int) -> bool`; missing/timeout raises `source_unavailable`, never returns allow. Bounded supplied context registration uses these same services, not a bypass.

- [ ] **Step 1:** Add `test_ciphertext_aad_and_digest_bind_scope`, `test_exact_json_limits`, `test_revoked_source_cannot_be_reread`, `test_missing_key_denies`. Assertions: raw sentinel absent from persisted ciphertext; swapped scope/version/digest/AAD fails; 1048577 bytes/depth 33/item 10001 denied; source revocation after admission makes both input and retained-result reads fail; missing key produces fixed error with no content. Test expiry/freshness boundary and changed classification/citation source.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert private_sentinel not in persisted_ciphertext
assert revoked_source_error.code == "forbidden"
assert oversized_value_bytes == 1048577
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_artifacts.py backend/tests/execution/test_context.py -q`; record red.
- [ ] **Step 3:** Implement exact-byte SHA256, strict JSON value checks, AES-GCM and immutable versioned metadata. Reauthorize every get under current source ACL/retention; include current source version/access in citation provenance. Bounded key-provider timeout has no cached allow fallback. Retention initially denies expired access and provides dry-run inventory; do not delete uncertain execution evidence or introduce an unapproved key-rotation/deletion system.
- [ ] **Step 4:** Repeat Step 2; PASS for concurrent revoke/read serialization and corrupt ciphertext. Assert errors/logs contain fixed codes only, and encryption keys never enter DTOs/history.
- [ ] **Step 5:** Commit Task 4 files as `feat: protect execution artifacts and current knowledge access`.

## Task 5: Enforce entitlement, shared usage and fair capacity

**Files:** Ownership-table Task 5 files; `backend/tests/execution/{test_entitlement.py,test_budgets.py,test_scheduler.py}`.

**Interfaces:** `reserve_attempt(db, actor: ExecutionActor, attempt_id: str, demand: BudgetDemand) -> ReservationView`; `settle_attempt(db, attempt_id: str, observation_id: str, usage: UsageObservation) -> ReservationView`; `schedule_batch(db, installation_id: str, limit: int=25) -> tuple[ScheduledAttempt,...]`; `reserve_admission(db, actor, deployment_revision_id: str) -> None`. Entitlement, tenant policy, price/max-charge and current recovery providers are required constructor dependencies; numerical limits intersect all scopes, not just two convenient sources.

- [ ] **Step 1:** Add `test_parallel_reservations_cannot_overspend`, `test_unknown_usage_retains_liability`, `test_nested_attempts_share_root_budget`, `test_fair_capacity_and_wait_release`, `test_expired_entitlement_after_lock_wait`. Assertions: total active `<=25`, per environment `<=5`; pending 1001 and retained wait 10001 refused; exhausted environment does not starve another; duplicate observation changes cost once; unknown cost remains reserved, never 0; missing price/entitlement prevents dispatch while read/export works.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert installation_active <= 25
assert max(environment_active.values()) <= 5
assert unknown_reservation.state == "uncertain"
assert settled_once == settled_after_duplicate
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_entitlement.py backend/tests/execution/test_budgets.py backend/tests/execution/test_scheduler.py -q`; record red with real concurrent PG connections.
- [ ] **Step 3:** Implement transactional reservations with documented lock order: installation capacity/cursor, workspace, E03 pool/instance/sessions/credentials in their existing order, environment capacity, deployment, execution, step, attempt, reservation, DB wall time after locks and persisted round-robin cursor. Every E04 mutator that touches both fleet and execution records follows this order; no later acquisition of a workspace/fleet parent while holding an execution row. Sort multiple same-kind IDs; PG deadlock tests race renewal, revocation, cancellation and reservation. FIFO key is admission sequence then ID. Batches <=25. Wait transition releases compute only after confirmed attempt stop, retains wait/storage quota and absolute deadline. Usage may exceed estimate: record actual liability and fence subsequent work, never clamp measured cost. Expired/unconfirmed attempts require reconciliation before capacity or money is released. Use fixed `capacity_unavailable`, no fabricated ETA.
- [ ] **Step 4:** Repeat Step 2; PASS under contention, retry/nested-tool/subworkflow accounting, lower operator limits and missing provider cases. Confirm active timeout consumption is additive across retries, not reset by lease renewal.
- [ ] **Step 5:** Commit Task 5 files as `feat: reserve execution budgets and schedule bounded fair capacity`.

## Task 6: Make admission and signals transactional

**Files:** Ownership-table Task 6 files; `backend/tests/execution/{test_admission.py,test_inbox.py,test_outbox.py}`.

**Interfaces:** `admit_execution(db, actor: ExecutionActor, request: AdmissionRequest, request_key: str) -> ExecutionView`; `accept_signal(db, actor, execution_id: str, request: SignalRequest) -> SignalReceipt`; `lease_outbox(db, owner_id: str, limit: int=25) -> tuple[OutboxItem,...]`; `ack_outbox(db, item_id: str, lease_revision: int, acknowledgement_digest: str) -> None`. `OutboxItem(id, lease_revision, kind, opaque_token, digest)` is defined in `outbox.py`, kind `start|signal|control`; worker resolves a token through current scoped records. Lease <=30s, batches 1..25, retries bounded by original execution deadline.

- [ ] **Step 1:** Add `test_admission_retry_after_commit_returns_one_execution`, `test_conflicting_key_refused_after_reauthorization`, `test_signal_commit_before_ack_and_wait_binding`, `test_lost_ack_releases_only_matching_outbox_lease`. Assertions: concurrent equal requests produce one execution/start row; altered input conflicts; transaction failure leaves no execution/reservation/outbox; early/wrong-wait/expired signal cannot satisfy a wait; duplicate equal signal one inbox row; wrong acknowledgement digest cannot mark delivered.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert first.id == repeated.id
assert execution_count == start_outbox_count == 1
assert rows_after_rollback == rows_before_request
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_admission.py backend/tests/execution/test_inbox.py backend/tests/execution/test_outbox.py -q`; record red.
- [ ] **Step 3:** Implement one admission transaction: current auth, exact typed input/artifact, pins, deadline, capacity and start outbox. Keys use existing opaque grammar length 1..128, scoped by operation and canonical digest. Equal replay reauthorizes. Signal payload is typed/encrypted, assignment policy and human-session versus external-key eligibility are explicit; persist before ack. Outbox carries references only, leased with `FOR UPDATE SKIP LOCKED`; stale owner cannot acknowledge a new lease.
- [ ] **Step 4:** Repeat Step 2; PASS including injected failures around each commit. Use a fake engine only for unit acknowledgement cases; Task 13 supplies the real lost-start-ack test. (Revision 2: that window no longer exists; Task 13 injects crashes before and after admission and transition commits instead.)
- [ ] **Step 5:** Commit Task 6 files as `feat: persist idempotent execution admission and signal delivery`.

## Task 7: Add the orchestration adapter, v15 claim marker and worker

**Files:** Ownership-table Task 7 files; `backend/tests/execution/{test_engine_contracts.py,test_orchestrator.py,test_migration_v15.py,test_execution_worker.py}`, `backend/tests/execution/fixtures/schema-v14.sql` from the committed baseline. Set backend metadata to 0.3.0. Add no dependency and no separate orchestrator distribution. Runtime, SDK and the activity contract stay unchanged.

**Interfaces:** Engine contracts, schema v15, marker and lock order as frozen above. `PostgresExecutionEngine(progression, sweeper, recovery, *, interpreter_ids)` implements `ExecutionEngine`. Task 7 also provides:

- `claim_runnable(db, worker: WorkerIdentity) -> Execution | None`;
- `consume_wakeups(db, execution: Execution) -> tuple[str, ...]`, which returns the consumed outbox kinds;
- `mark_runnable(db, execution: Execution, at_ms: int) -> None`;
- `migrate_execution_v15(connection) -> None`;
- `run_execution_worker(settings: ExecutionWorkerSettings) -> None` and `main(argv: list[str] | None = None) -> int`, with `--role orchestrator|sweeper`.

Settings carry the worker ID, role, supported interpreter IDs, polling bounds and supervised shutdown bounds from operator configuration. There is no plugin import or caller-selected interpreter. Task 7 tests use constructor-injected, test-only progression and sweeper fixtures, which production cannot select. Production wiring requires Tasks 8 and 11 and refuses to start without them.

- [ ] **Step 1:** Add `test_v14_upgrade_preserves_rows_and_marks_pending_work`, `test_partial_v14_refused_atomically`, `test_claim_skips_locked_and_unsupported_interpreters`, `test_wakeups_consumed_only_with_the_transition`, `test_pure_transition_never_locks_capacity_parents`, `test_stalled_worker_is_ended_by_role_timeouts`, `test_worker_refuses_unsafe_startup` and `test_shutdown_stops_owned_workers`. Assertions:
  - Migration: populated v14 rows are unchanged apart from the new column, and fresh and migrated schema signatures are equal. A non-terminal v14 execution with a pending start outbox row becomes runnable; a terminal one does not. A failed preflight leaves version 14.
  - Claiming: two workers racing one runnable execution never both claim it. A worker without `lotuspond.execution-interpreter.v1` never claims it. A rolled-back transition leaves its outbox rows `pending` and the marker set.
  - Lock order: during a pure transition, `pg_locks` shows no lock on the scheduler cursor, environment capacity, runtime pool, connection or reservation rows.
  - Stalls: a SIGSTOPped worker that holds an execution row is ended by `idle_in_transaction_session_timeout`, and a second worker advances the execution.
  - Startup and shutdown: the worker refuses SQLite, unknown interpreter IDs, a missing role and an unavailable RecoveryAuthority. Shutdown has observed cleanup.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert after_legacy_rows == before_legacy_rows
assert fresh_signature == migrated_signature
assert second_worker_claim is None
assert outbox_status_after_rollback == "pending"
assert locked_parents_during_pure_transition == set()
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_engine_contracts.py backend/tests/execution/test_orchestrator.py backend/tests/execution/test_migration_v15.py backend/tests/execution/test_execution_worker.py -q` against isolated PostgreSQL; record red.
- [ ] **Step 3:** Implement:
  - The v15 step from exact v14, as frozen above.
  - The claim and in-transaction consumption. Pending `engine_start`, `engine_signal` and `control` rows go from pending to delivered with `delivery_count + 1` and no lease columns, in the transition's own transaction. Never call `lease_outbox` or `ack_outbox`, and reword `outbox.py`'s docstring to name external consumers instead of an engine.
  - `mark_runnable` calls in `AdmissionService.admit_execution` and `InboxService.accept_signal`. These are the only changes to those services.
  - Role timeouts on worker connections, and polling with back-off from 200 ms to 2 s. Optional `LISTEN`/`NOTIFY` wake-ups on one fixed channel with an empty payload are an optimization, never the only path.
  - `execution-worker` in `scripts/python.mjs` and a root `worker:execution` script.

  Task 14 documents the orchestrator role's grants and Task 13 (ENG-4) verifies them.

- [ ] **Step 4:** Repeat Step 2, plus `B backend/tests/test_migrations.py backend/tests/fleet/test_migration.py backend/tests/execution/test_migration.py backend/tests/execution/test_admission.py backend/tests/execution/test_inbox.py backend/tests/execution/test_outbox.py -q`. Require PASS with zero required database skips, and Task 6 behavior unchanged apart from the marker.
- [ ] **Step 5:** Commit Task 7 files as `feat: add PostgreSQL orchestration claim and worker behind the engine adapter`.

## Task 8: Execute typed control flow as deterministic row transitions

**Files:** Ownership-table Task 8 files; `backend/tests/execution/{test_expressions.py,test_progression.py,test_progression_fixtures.py}`. Test fixtures under `backend/tests/execution/fixtures/control-flow/` cover each admitted SDK step kind. `backend/tests/execution/fixtures/progression/lotuspond.execution-interpreter.v1/` holds golden progression records for that interpreter version: canonical committed rows with the expected next actions.

**Interfaces:**

- `evaluate_mapping(mapping: object, values: ValueEnvironment, definitions: list[NamedSchema]) -> JsonValue`;
- `ValueEnvironment(input: JsonValue, steps: dict[str,JsonValue], item: JsonValue|Missing, index: int|None)`, with a private nonserializable `Missing` sentinel;
- `load_progression`, `next_actions` and `advance_execution` as frozen above. Task 7 calls `advance_execution` with the execution row already locked;
- `resolve_occurrence(plan, occurrence_path) -> StepRef`, the exact occurrence resolver that Task 6's `InboxService` lacks.

- [ ] **Step 1:** Add `test_missing_is_not_null_or_false`, `test_decimal_rounding_and_safe_integer_overflow`, `test_structured_regions_and_occurrence_identity`, `test_repeated_transition_is_noop`, `test_progression_is_deterministic_from_rows`, `test_wait_deadline_does_not_restart`, `test_signal_consumption_requires_exact_wait_binding`, `test_parallel_failure_stops_undispatched_siblings`, `test_region_scoped_derivation_bound` and `test_golden_progression_fixtures_rederive`. Assertions:
  - Expressions: `coalesce(null, fallback)` preserves null, while missing selects the fallback. `exists` checks presence. Half-even decimal `1.005` at scale 2 becomes `1.00`. Division by zero and overflow are fixed errors.
  - Control flow: a decision runs one branch, and parallel joins all selected branches. Iteration output order matches input order despite completion order.
  - Determinism: re-running a committed transition creates no row, and two workers given the same committed rows produce identical actions.
  - Signals: a signal for another wait or with another digest satisfies nothing, and neither does one accepted after the earlier of its expiry and the wait's deadline (see Signal timeliness above).
  - Parallel failure: the first failed branch cancels `reserved` sibling attempts and keeps completed and uncertain ones.
  - Derivation bound: at 1,000 iteration items and the largest expansion the SDK admits for them, the rows read per transition stay within the enclosing region occurrence, and the count is recorded. Each item region counts itself plus its steps, so at 1,000 items the 10,000-step cap admits at most 9,001 (eight body steps); the test proves that one more step is refused.
  - Fixtures: every golden fixture re-derives byte-identically.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert decimal_half_even_result == "1.00"
assert integer_inexact_division_error.code == "inexact_division"
assert iteration_result == input_order_expected
assert rows_created_by_repeated_transition == 0
assert actions_from_worker_a == actions_from_worker_b
assert resumed_wait.deadline_ms == original_deadline_ms
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_expressions.py backend/tests/execution/test_progression.py backend/tests/execution/test_progression_fixtures.py -q`; record red.
- [ ] **Step 3:** Implement closed expression operations using public SDK parsing and type validation, exact integer and decimal arithmetic, and bounded AST evaluation (64 nodes, depth 8). Honor all existing `CallMapping` operations and scale/rounding rules. Use no `eval`, float decimal conversion or implicit truth coercion. Integer division must be exact; reject a nonzero remainder, a zero divisor and safe-integer overflow. `coalesce` evaluates its fallback only for missing data. Boolean calls evaluate their typed arguments without adding undeclared presence guards. Progression follows the compiled plan's pointers and digests; compiled plans hold no executable expression bodies. The rules by step kind:
  - **Pure steps** (transforms, decision selection and joins) record their result as an encrypted artifact on a succeeded attempt of that occurrence. The attempt's input artifact is the canonical evaluated mapping input. No reservation or delivery is created.
  - **Connector reads and bounded agents** create the step instance and a `reserved` attempt with an encrypted input artifact. Task 9 schedules, reserves and dispatches it.
  - **Timers, external waits and human `mode=form` waits** create an `open` wait. Its absolute deadline is the earlier of the declared delay or expiry and the execution deadline. Wait quota is retained through `BudgetService.retain_wait` in an ordered capacity transaction, after fixing the carried shorter-deadline finding. External and form waits are satisfied only by consuming an inbox row whose wait, digest, typed payload and expiry match. Timers are satisfied only by the sweeper.
  - **Parallel all-join** cancels undispatched siblings on the first failure. **Bounded iteration** pins input order and content identity and honors declared concurrency. **Pinned subworkflows** inherit stricter limits and share the root budget.
  - **Unsupported policies and effect nodes** deny with fixed codes.

  Each transaction:
  - loads only the affected region occurrence;
  - applies at most `bound` transitions;
  - appends gap-free events: the sequence comes from `executions.next_event_sequence` under the row lock, the source is `orchestrator`, and events carry codes and artifact references only;
  - leaves the execution runnable when the bound is reached or a capacity transition is next.

  Replace `InboxService`'s ambiguous-node denial with `resolve_occurrence`, keeping a refusal for unresolved paths. Record the golden fixtures for `lotuspond.execution-interpreter.v1`.

- [ ] **Step 4:** Repeat Step 2, plus `B backend/tests/execution/test_inbox.py -q`. Require PASS for empty iterations, nested scopes, optional fields, finite numeric boundaries, duplicate signal delivery and a worker killed between transitions.
- [ ] **Step 5:** Commit Task 8 files as `feat: interpret pinned workflow regions as deterministic row transitions`.

## Task 9: Bind activity dispatch, leases and historical reconciliation

**Files:** Ownership-table Task 9 files; `backend/tests/execution/{test_dispatch.py,test_leases.py,test_reconciliation.py}`, `runtime/python/tests/test_activity_client.py`. Introduce runtime 0.2.0 metadata and lock; update the backend requirement, keeping E03 command behavior unchanged. Use the Task 1 shared usage type unchanged.

**Interfaces:** `ActivityRecordSource.load(db, attempt_id: str) -> PinnedActivityRecords`; `dispatch_activity(db, attempt_id: str, recipient: RuntimeIdentity) -> ActivityDelivery`; `claim_activity(db, identity: RuntimeIdentity, delivery_id: str, digest: str) -> ActivityLease`; `renew_activity(db, identity, lease: ActivityLease) -> ActivityLease`; `request_child_activity(db, identity, request: ChildActivityRequest) -> str` returns the persisted child-attempt ID; `record_activity_result(db, identity, result: ActivityResult) -> None`; `reconcile_activity(db, actor: ExecutionActor, observation: HistoricalObservation) -> None`; `lapse_lease(db, lease_id: str) -> None` and `expire_delivery(db, delivery_id: str) -> None` for the Task 11 sweeper. `dispatch_activity` runs in the runtime poll path after Task 5 `Scheduler.schedule_batch` and `BudgetService.reserve_attempt`, in Task 5's lock order, and refuses unless `requested_state == 'running'` under the execution lock. `record_activity_result`, `lapse_lease` and `expire_delivery` call Task 7 `mark_runnable` in their own transaction. `ActivityClient.poll() -> ActivityDelivery|None`, `.claim(delivery) -> ActivityLease`, `.renew(lease) -> ActivityLease`, `.request_child(request) -> str`, `.result(result) -> None` use existing verified direct control transport and runtime session credentials, never evaluation keys.

- [ ] **Step 1:** Add `test_each_persisted_pin_and_actual_mapping_is_required`, `test_claim_once_with_lost_ack`, `test_expiry_and_revoke_during_lock_wait`, `test_lease_renewal_cannot_extend_attempt_deadline`, `test_historical_evidence_requires_separate_authority`, `test_child_request_requires_declaration_and_actual_arguments`, `test_unclaimed_delivery_expiry_allows_only_a_new_ordinal`, `test_lapsed_lease_requires_reconciliation`, `test_result_marks_execution_runnable_atomically`. Assertions: deployment digest substituted for connection digest denies; valid signature/wrong instance or generation denies; equal claim returns the same live lease only to the same current identity, never a second invocation; expiry+1/revoked identity cannot renew; max lease 60000ms and <=remaining deadline; old result remains uncertain and cannot resurrect activity. An expired unclaimed delivery fails its attempt, with `timeout_before_dispatch` as the event code (attempts have no code column), and the attempt never gets a second delivery; a lapsed lease leaves lease and attempt `uncertain`, keeps the reservation liability and moves the execution to `reconciliation_required` without marking it runnable; a result and its runnable marker commit together or not at all.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert lease.expires_at_ms - server_now_ms <= 60000
assert lease.expires_at_ms <= lease.deadline_ms
assert lease_after_equal_claim == first_live_lease
assert altered_arguments_error.code == "forbidden"
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_dispatch.py backend/tests/execution/test_leases.py backend/tests/execution/test_reconciliation.py -q` and `R runtime/python/tests/test_activity_client.py -q`; record red.
- [ ] **Step 3:** Reload exact execution/deployment/connection/version/secret links, current fleet identity/session/pool/generation/recovery and budget/entitlement under locks. Sign captured bytes with independent activity trust. Claim is durable, not reclaimable on timeout; lease renew cadence <=20s, lease length <=60s. Every agent model/tool round uses a separately validated child request, exact declaration/arguments, shared-root reservation and newly signed delivery; parent lease is not permission to invoke another tool. Equal child request IDs deduplicate, changed content conflicts. Recheck database time after locks and provider lookups. Late evidence has a separate owner/admin reconciliation path with current recovery authority, immutable observation and conflict checks; it can settle known usage or record a result but cannot issue work or turn unverifiable data into success. Do not call E03 `record_status` for historical closure.
- [ ] **Step 4:** Repeat Step 2 and E03 command/auth tests; PASS under duplicate/reordered delivery, authority-provider timeout and current policy change. Verify plaintext credentials never enter signed envelope or stored admission.
- [ ] **Step 5:** Commit Task 9 files as `feat: authorize bounded activity claims and independent reconciliation`.

**Conventions fixed by Task 9, for the owner's acknowledgement:**

- **Lock order.** Every activity service follows a subsequence of Task 5's order: installation cursor (only when capacity counters change), workspace and E03 fleet rows, environment capacity, deployment and revision, execution, step, attempts, reservations, delivery, lease. Runtime routes authenticate first, so the workspace and fleet rows are already held when the service runs. The initiating principal's account rows then follow the fleet rows. This is safe because every transaction that locks a workspace's account rows first holds that workspace row `FOR UPDATE`. A service that needs the cursor after that takes it with `NOWAIT`: it never waits there, so it cannot close a deadlock cycle, and it is refused with the retryable `capacity_unavailable` while another transaction holds the cursor. Task 12's routes for child requests and results call `ActivityService.lock_cursor` before authentication, so in normal use the `NOWAIT` acquisition is already satisfied.
- **One delivery per attempt.** An expired, unclaimed delivery fails its attempt with `{"failure": "timeout_before_dispatch", ...}`, and only a new ordinal retries. Its event is `attempt_failed` with detail `timeout_before_dispatch`; Step 1's "event code" cannot be literal because v14's event-code check allows only the fixed set. Its reservation is released at zero (owner decision, 2026-10-26; Task 9 settled zero tokens and cost but kept its reserved active time): a runtime invokes nothing before a durable claim, so an unclaimed delivery never started.
- **Leases.** A lease's token digest binds the attempt, the delivery and its digest, the claiming session and the generation; it is fixed at claim. `lease_revision` is derived from stored times (`renewed_at_ms - claimed_at_ms + 1`), so no schema change is needed. It increases with every renewal. Renewal and results accept a presented revision no newer than the current one, from the same session. The revision orders a lease's renewals; protection against a stale or foreign holder comes from the session-bound token, the active status and the expiry. A lease is at most 60 s and never outlives the attempt's deadline (the earlier of the execution deadline, the delivery's issue time plus the step's timeout, and for a child its parent's deadline) or the attempt's reserved active time, counted from its reservation. Dispatches, claims and renewals past the reserved active time are refused with `budget_exceeded`, so concurrent attempts cannot exceed the active budget that reservation checked, and a delivery expires no later than it and is signed only with at least 1 s left to claim it. A reserved attempt whose reserved active time runs out before any dispatch is retired by `LeaseService.expire_reservation`: it never started, so it fails with `timeout_before_dispatch`, its compute stop is confirmed, its reservation is released at zero (since 2026-10-26) and the execution is marked runnable. **Obligation for Task 11:** the sweeper calls `lapse_lease`, `expire_delivery` and `expire_reservation` for due rows (a spent reservation is one whose start plus reserved `active_ms` has passed while its attempt is still `reserved` and undelivered). Two consequences for Task 11: the frozen `SweepReport` has no field for retired reservations, so it either counts them under an existing field or amends the contract for the owner's acknowledgement; and v15 has no due index for spent reservations, so Task 11's sweep query (and Task 13's load measurement) must show they are found efficiently. A child's renewal also requires its parent's live lease. Cancellation is reported by refusing renewal.
- **Lapse.** A lapsed lease leaves the lease, the attempt and the reservation `uncertain`, keeps the compute slot and the reserved liability, and moves the execution to `reconciliation_required`. It does **not** mark the execution runnable: Step 1's assertion wins over the interface sentence above, since only reconciliation can decide what happened.
- **Results.** A result is idempotent by attempt: an equal repeat changes nothing, a different one conflicts. A succeeded root result must be a value this lease holder stored for the attempt and must match the activity's output schema; otherwise the attempt fails with `invalid_result`. `invalid_output` maps to `invalid_result`. Usage is settled through Task 5's `settle_attempt`: unknown usage leaves the attempt `uncertain` and the execution in `reconciliation_required`, never measured zero. The result, compute-stop confirmation and runnable marker commit together.
- **Children.** An agent's model rounds and read tools are child attempts on the parent's step instance, with ordinals above 10 (root retries use 1..10). A tool's name is `<connector catalog name>-<operation>` (`activity_protocol.tool_name`), and an unnameable or ambiguous tool can never be requested. The model is named by its catalog name. A child's request digest binds the request ID, kind, name and argument handle, and it identifies the child's declaration again on every reload. Arguments must be a value the parent's lease holder stored, and connector-read arguments must match the operation's target and input schemas. A child shares the root budget through its own reservation but takes no compute slot, because the parent's slot covers it. This uses Task 5's `reserve_attempt(..., shared=True)`, which requires the parent's compute to be running and records a `capacity.shared:<attempt>` row. Stopping such a child returns no slot, and the pool count excludes it. Without `shared`, a child attempt keeps Task 5's own-slot behaviour. Otherwise agents holding every slot could deadlock waiting for their own tool calls. Children are delivered only to the session holding the parent's live lease, and a child cannot request children.
- **Values.** Runtime-stored values (results and child arguments) are canonical JSON of at most 64 KiB, the control transport's body bound, and at most 1,024 per execution. Each is registered to its attempt with an `activity.value:<artifact>` request row.
- **A parent and its children.** A root attempt's compute stops (by its result or by reconciliation) only when none of its children may still run: a claimed child, or an uncertain one not yet confirmed stopped, makes the root's result or reconciliation refuse with `conflict` until that child reports or is reconciled. In the same transaction its never-started children (reserved, or dispatched but unclaimed) are cancelled and their compute stop confirmed; since 2026-10-26 their reservations are released at zero. A shared child therefore never runs without its parent's slot.
- **Outcomes and authority.** Recording a result or storing a value needs the runtime's current identity, session and live lease, but not the initiator's current `execute` permission: it grants nothing, and refusing it would discard a known outcome. Dispatch, claims, renewals and child requests still require that permission, so revoking the initiator stops new work and renewal.
- **Polling.** A poll tries at most 10 candidates in order: pending deliveries of running executions, then children of attempts the session holds (at most 4 of each, so roots always get a turn), then roots by admission order. Each is tried in its own savepoint, and refused ones are skipped. One refused head (a revoked initiator, a disabled deployment, a passed deadline) never blocks the pool.
- **Reconciliation.** Only an owner or admin under current recovery authority, fenced before any lock, can reconcile, with immutable evidence that must name the persisted delivery's digest. It settles known usage, confirms the stop and fails the attempt with `uncertain`; it never adopts a claimed result as success. The `reconciled` event records the reconciling user. When no attempt remains uncertain, the execution returns to `running`, or to `paused` if a pause was requested, and is marked runnable, and progression decides what the failure means. Events record their origin (`orchestrator`, `activity`, `sweeper` or `reconciliation`) in their encrypted content.
- **Interfaces as implemented.**
  - Runtime-authenticated operations take the route's `RuntimePrincipal` (identity, credential and session), as E03's `CommandService` does: `claim_activity`, `renew_activity`, `request_child_activity`, `record_activity_result`, plus `poll_activity` and `store_activity_value`. `dispatch_activity` takes the recipient's `RuntimeIdentity`.
  - `poll_activity` returns a pending delivery or dispatches the next reserved attempt for this pool, in the order given under Polling above.
  - The services are classes:
    - `ActivityService` in `dispatch.py`;
    - `LeaseService` in `leases.py`, whose `lapse_lease`, `expire_delivery` and `expire_reservation` take an optional `epoch` the sweeper has fenced; without it they fence before any lock;
    - `ReconciliationService` in `reconciliation.py`.
  - `ActivityRecordSource.load` requires the persisted delivery and rebuilds its exact envelope from current pins. `ActivityRecordSource.pins` serves dispatch before a delivery exists.
  - `ActivityLease` has exactly the frozen fields. `ActivityClient.store(attempt_id, content)` and the route `/activities/<attempt>/values` are added.
  - Runtime 0.2.0 adds `activity_protocol.py`, `activity_client.py` and the six activity routes to the control transport's allowlist (all POST, all requiring the session). E03 command behaviour is unchanged.
  - Files beyond Task 9's row, each necessary:
    - `execution/budgets.py`: the opt-in `shared` child reservation.
    - `execution/progression.py`: the event writer becomes the shared `record_event`, so activity events join the one gap-free sequence.
    - `runtime/.../control_transport.py`: the route allowlist.
    - Version pins in `ci.yml` and the fleet acceptance harness and test.
    - New control-flow fixtures.
  - Not checked server-side: whether the claiming runtime has the connector installed. The execution catalog and E03's installed connector catalog are separate. The runtime verifies its installed connector before any call (Task 10).

## Task 10: Run bounded agents and read tools in the runtime

**Files:** Ownership-table Task 10 files; `runtime/python/tests/{test_activity_worker.py,test_agent.py,test_activity_providers.py,test_activity_context.py}`; test-only fixtures `runtime/python/tests/fixtures/activity_provider.py`. Modify existing `cli.py`, `supervisor.py`, `client.py` only for explicit `activity-worker` integration; keep E03 fixture invocation semantics.

**Interfaces:** Provider types above; `run_activity(delivery: ActivityDelivery, lease: ActivityLease, dependencies: ActivityDependencies) -> ActivityResult`; `run_agent(request: AgentRequest, dependencies: ActivityDependencies) -> ProviderResult`. `ActivityDependencies` requires current-authority client, immutable catalog, independent trust, local secret resolver, artifact/context client, providers and supervisor. `AgentRequest` binds agent/model/instructions pins, input/output schema, declared tools/context, root attempt and remaining limits. Constructors cannot accept arbitrary executable paths or dynamically load package code.

- [ ] **Step 1:** Add `test_proposed_tool_call_is_data_not_authority`, `test_read_adapter_cannot_write`, `test_nested_calls_share_budget`, `test_lost_lease_stops_child_and_retains_unknown_usage`, `test_private_provider_exception_is_sanitized`, `test_context_acl_change_blocks_new_read`, `test_restart_after_started_journal_never_reinvokes`. Assertions: undeclared tool/changed actual arguments denied before provider call; write descriptor never invoked; provider call count observed, not hardcoded; forged success/output-schema failure cannot complete step; fixed synthetic provider unavailable with production settings; lease loss yields uncertain state and confirmed child cleanup.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert provider_calls_after_restart == provider_calls_before_restart
assert undeclared_tool_error.code == "forbidden"
assert cleanup.confirmed is True
assert lost_response_usage.cost_microusd is None
```

- [ ] **Step 2:** Run `R runtime/python/tests/test_activity_worker.py runtime/python/tests/test_agent.py runtime/python/tests/test_activity_providers.py runtime/python/tests/test_activity_context.py -q`; record red.
- [ ] **Step 3:** Use supervised bounded child processes and existing destination/resolver rules for read/model calls; verify signed bytes, actual request hash, exact catalog effect class and pins. Resolve secrets only just before dispatch in the worker, never pass API/session tokens to children. Persist a private invocation journal before provider access (attempt/delivery digest/generation and claimed/started/result-recorded phases); follow existing `state.py` owner-only, atomic replacement, fsync and symlink-refusal rules. Restart with a started or ambiguous attempt reports uncertainty and never invokes that attempt again. A retry is a new server-admitted ordinal, only for declared `unavailable|rate_limited|timeout_before_dispatch`, within max_attempts/backoff/deadline and a fresh reservation; lost model response liability remains on the old attempt. All model/tool rounds use Task 9 child admission under the same root budget; output is schema-validated and stored encrypted before reporting. Renew leases outside the blocked provider call; after loss initiate cancel immediately, allow at most 1s TERM grace and 1s after KILL to confirm exit. Refuse subsequent work if cleanup is unconfirmed. Provide deterministic fixtures only under test settings; real-provider absence reports unavailable rather than success.
- [ ] **Step 4:** Repeat Step 2 and existing runtime suite; PASS with positive controls that prove fixture execution, cancellation, child cleanup, bounded response/time and no credential/prompt leakage. Process groups are lifecycle controls, not hostile-code isolation.
- [ ] **Step 5:** Commit Task 10 files as `feat: execute bounded agent and read activities with current authority`.

**Conventions fixed by Task 10, for the owner's acknowledgement:**

- **Provider children.**
  - Every provider call runs in a fresh child: `python -I -m lotuspond_runtime.activity_child <fd> <worker pid>`, with an empty environment and its own process group. On Linux the child dies with the worker (parent-death signal), and it refuses to start if the worker is already gone.
  - The request travels only over the child's private socket, in bounded frames (1 MiB, SDK JSON limits). That includes the pinned records, any resolved secrets, a model round's context and the call's deadline. The child is bounded by that deadline (at most one hour), not by a fixed cap.
  - The child rebuilds the frozen types and calls `ReadProvider.invoke(records, arguments, deadline_ms)` or `ModelProvider.generate(request, deadline_ms)` on the implementation registered under the descriptor's fixed identifier. No code is loaded by path. This distribution installs no real provider, so a call reports `unavailable` with nothing sent.
  - A provider failure carries a fixed code, plus the usage the provider reported, or a positive "not reached" marker; nothing else leaves the child.
  - Every call ends with the group gone. This holds on success, failure, cancellation (lease loss, deadline) and any exception, including an interrupt, and also when a group outlives its finished leader. The stop is SIGTERM, 1 s, SIGKILL, and exit confirmed within 1 s.
  - An unconfirmed cleanup fences the worker. The fence is persisted in the journal where it is detected, so a restarted worker stays fenced until an operator confirms no orphan remains and removes the marker. A fenced worker stops before polling.
  - Each provider's process group is journaled, with this boot's identity, before the child receives its request. On recovery, an ambiguous attempt whose group still exists in this boot fences the worker. The worker never kills a group after a restart, because the number may have been reused.
- **Connections, secrets and limits.**
  - A read runs only when all of these hold:
    - the pinned connection's connector pin names the catalog dependency (name and version);
    - its artifact digest equals the installed connector the provider descriptor serves;
    - the operation is allowed;
    - the canonical arguments fit `max_request_bytes`.
  - The call is bounded by the earlier of the attempt deadline, the lease and `timeout_ms`. A response larger than `max_response_bytes`, or the 64 KiB value bound, is `invalid_output`.
  - Every pinned secret of the connection, and every worker-local credential of the descriptor (a model's API key, for example), is resolved by the worker after `started` and immediately before the call. They are never logged, stored or journaled.
  - The secret source takes a `SecretPin`. In production, `MountedActivitySecrets` wraps E03's `MountedSecretResolver` with the operator's pin-to-binding mapping, as E03 commands do.
  - **Deferred, with reason (owner decision, 2026-10-23):** destination enforcement. The child receives the pinned destinations inside the records. Because no real provider is installed, no network path exists. The first real provider must connect only through E03's `DestinationTransport` under the operator's egress policy. Whichever change installs it owns that wiring and its tests. The owner has no real provider to supply ("I cant provide a provider as of now so proceed accordingly"). E04 therefore uses synthetic test providers only and never claims live-provider evidence. Nothing reaches a real network by default: the provider child selects nothing but the test-mode fixtures, which have no network effect.
- **Model rounds.**
  - Resolved context, instructions, input and the transcript stay in worker memory and reach only the model child.
  - A round's stored argument is `{"round", "request_sha256"}` and its stored result is `{"reply_sha256"}`, so source-bound context never becomes a workspace-policy value. Transcript growth no longer counts against the 64 KiB value bound; the 1 MiB frame bounds a round's request.
  - Each round receives the limits that remain after earlier rounds. A tool read's cost counts against the same cost limit.
  - A round or read whose usage is unknown, or exceeds what was left, ends the agent with `provider_failed`; the child itself reports its real usage.
  - Context references are read one at a time, with the lease kept live between reads; an agent declares at most 64.
- **Children.**
  - An agent takes each child's delivery through a poll targeted at its own parent (below), never an untargeted one.
  - Before the claim, the child's signed envelope must name exactly the requested kind, dependency, operation and stored argument handle. If it does not, the child is never claimed and the agent ends with `provider_failed`.
  - The child's lease passes `validate_lease`. A granted lease it refuses is reported like a root's, as `unavailable` with zero usage, so the root can still report.
  - A child claim whose answer is lost on every retry is left as lease loss: the claim may have committed, and a claimed child keeps the root from reporting.
  - A tool read runs through the same connection, secret and bound checks as a root read, using the child's own verified records.
  - A refused admission ends the agent with `provider_failed`. A child that is not dispatched ends it with `unavailable`; the root's outcome then cancels the child.
  - A failed child ends the agent with the child's code.
  - A child outcome that cannot be reported is treated as lease loss, because the root could not report either. Both lapse to reconciliation, and the journal keeps the child's outcome.
- **Time.** The worker's clock is the control plane's: confirmed server time plus elapsed monotonic time (`ServerClock`, anchored from an E03 session or heartbeat as `RuntimeSupervisor` is).
  - `now_ms` counts the whole roundtrip and never runs behind the server, so every validity check uses it. A lease is judged on the clock read after its response arrives.
  - `settled_ms` never runs ahead, so usage completion is stamped with it rather than the provider's time.
  - Both bounds include a drift allowance of 50 ms plus 1 ms per second since the anchor.
  - A wall clock that disagrees with monotonic time by more than 30 s, or an anchor older than 10 minutes, makes time unavailable.
  - Time that becomes unavailable after a provider may have run is treated as lease loss: nothing is reported, and in particular never zero usage.
  - `ActivityDependencies.clock` is required; local wall time is never a default.
- **Leases.**
  - The control transport carries one request at a time, so leases are renewed on the worker's own thread. Renewal happens inside the provider loop (every 50 ms) and before each control call; there is no renewal thread.
  - An explicit refusal ends a lease at once. A transient failure (`transport_failed`, `rate_limited`, `authority_unavailable`) is retried until 13 s before expiry. That leaves room for one more request blocked for the transport's 10 s bound, then 2 s to stop the provider.
  - Renewal also runs between the agent's control-plane reads (agent records, input, each context reference).
  - A child's lease is lost with its parent's.
  - Idempotent control calls (claim, store, child request, result) are retried on transient failures while the lease holds.
- **Results and usage.**
  - A reply with no tool proposals is final, and its output may be `null`.
  - Usage is:
    - zero, and known, when nothing was sent;
    - the provider's report when there is one, failures included, so the declared retry classes stay retryable;
    - unknown, never zero, otherwise.
  - An agent's root attempt reports zero usage of its own.
  - Once a lease is held, every ending except lease loss is reported:
    - a refusal before anything started (a lease out of bounds, for example) is reported as `unavailable` with zero usage;
    - a refused input digest is reported as `provider_failed`;
    - a failure to store after success is reported with the known usage.
  - Before any provider may have run, authority-type errors map to `unavailable` and all others to `provider_failed`. After one may have run (a read's, a child's or an agent's output could not be stored, or an agent's later control call failed), the code is `provider_failed`, so a retry never runs it again. `unsafe_storage` stops the worker without reporting around the journal.
  - When the lease is lost, the worker reports no outcome; the control plane's lapse makes the attempt uncertain.
  - `unavailable` for a changed pin or a write operation means "this runtime cannot serve it". Another runtime may, within the attempt's retry bounds.
  - **Narrowed beyond E04:** a pending delivery the runtime cannot verify used to head each of that session's polls until it expired (up to 5 minutes). The owner-approved decline addition below removes that for deliveries that are themselves bad. On purpose, it keeps the old behaviour (expiry within 5 minutes) for local gaps (stale trust, a stale identity, a wrong clock, outages) and once its circuit breaker has stopped declining; see the addition's safeguards and trade-off.
- **Journal.**
  - One owner-only (0600) file per attempt, in a private directory held by one process through an exclusive lock. Files are written by atomic replacement with file and directory fsync, and have crash-point hooks. A temporary file left by a crash is removed when the journal is reopened.
  - Records are opened without following links and without blocking, so a planted FIFO is refused.
  - Phases move only forward: `claimed`, `started`, `finished`, then `recorded` or `abandoned`. `started` is durable before any provider is reached.
  - `finished` keeps the outcome (identities, code and usage numbers) before it is reported. An unreported outcome stays there as reconciliation evidence, and the worker answers `unreported`.
  - A torn or unreadable record counts as started and may only be abandoned. After a restart every ambiguous attempt is abandoned and never invoked again: a worker runs this recovery once, by itself, before its first poll. The journal's lock allows one holder, so the runtime wiring keeps one worker per process.
  - A delivery seen past its claim is refused. A delivery journaled only as `claimed`, with the same digest and generation, is claimed again: an equal claim returns the same live lease after a lost acknowledgement.
- **Agents.**
  - Every proposal in a model reply is checked, and its provider selected, before any of them runs. An undeclared tool, any other argument shape or a mistyped argument raises `ProposalRefused` (`forbidden`), and only that error becomes `invalid_output`.
  - The step limit ends the agent with `invalid_output`.
  - Context is reread for every activity under current access. A refusal ends it with `provider_failed`; no context client means `unavailable`.
  - `AgentRecords` is validated: 1–64 steps, at most 64 uniquely named tools, and safe nonnegative limits.
- **Interfaces as implemented.**
  - `run_activity(delivery, lease, dependencies, *, verified=None)`: a caller that has just verified the delivery passes the envelope and records on, so each claim is verified once.
  - `run_agent(request, dependencies, keeper)`: the keeper renews the parent's lease and reports its loss.
  - `ActivityDependencies` takes:
    - the runtime identity, the journal and the server-anchored clock;
    - a record source (pinned records and agent declarations) and a value source;
    - the trust authority and the provider set;
    - optionally, the secret source and the context client.
  - There is no separate "immutable catalog" or "supervisor" object. `ProviderSet` holds the immutable descriptors (its test mode is fixed at construction, and a script is canonical JSON text), and `activity_invocation` is the supervisor, with its fence.
  - `ModelReply` keeps the frozen fields. `ModelRequest` gains JSON conversion for the child.
  - The `activity-worker` CLI command fails closed with `unavailable` until real providers and the control-plane routes exist.
  - Files beyond Task 10's row, each necessary:
    - `activity_child.py` (the fixed child), `activity_framing.py` (its frames) and `activity_fixture.py` (the test-only providers; see below);
    - `activity_client.py` and backend `execution/dispatch.py`: `poll(parent_attempt_id)` and `poll_activity(..., parent_attempt_id)`. With it, only that parent's children are candidates, only for the session holding the parent's live lease, under a separate 600-per-minute allowance; a backend test covers it;
    - `cli.py`, as the task allows;
    - tests: `activity_env.py`, `test_activity_state.py` and the `activity-worker` test in `test_cli.py`.
- **Synthetic providers.** The test-only read and model providers are packaged as `lotuspond_runtime/activity_fixture.py`, as E03 packaged `fixture_connector.py`, because the `-I` child cannot import from `tests`.
  - They are selectable only with test-mode descriptors, and their behaviour is a data script.
  - Their only file effect is a call counter: an absolute path whose file name is `provider-calls`, opened for append without following links.
  - `tests/fixtures/activity_provider.py` is a control plane that behaves like the real one: its own skewed clock, single-flight calls with latency, pending-first and targeted polls, refusal of a root result while a child is claimed, and of a completion in its future.
- **Obligations for Task 12.** The runtime routes must serve:
  - the pinned records and the agent declarations (instructions, model name, declared tools with their schemas, context references and limits);
  - fetches of the attempt's input;
  - context reads under current source access;
  - the targeted poll's `parent_attempt_id` body field;
  - the decline route and its records-route refusal codes (see the owner-approved addition beyond E04).

  The runtime integration must also re-anchor the activity clock on every E03 session or heartbeat response. Re-anchoring has to come from the E03 supervisor's own heartbeat loop (every 20 s or less, on its own transport), not from the activity worker's thread. The worker can sit inside one provider call or agent for longer than the clock's 10-minute anchor age, and lease responses carry no server time. `ServerClock.anchor` replaces one tuple, so calling it from that loop's thread is safe.

  Task 12 also owns the runtime-side clients for the record, value and context routes, because no runtime file in its row exists yet. It must add them, or name the files they need, as files beyond its row. Until then the runtime's record, value and context sources deny.

- **Platform.** The journal tests, like the existing runtime storage tests, run only on Linux, because `state.private_directory` refuses other platforms by design.

## Task 11: Sweep due work and preserve controls, versions and restore fences

**Files:** Ownership-table Task 11 files; `backend/tests/execution/{test_control.py,test_projections.py,test_recovery.py,test_sweeper.py,test_interpreters.py}`.

**Interfaces:**

- `request_control(db, actor: ExecutionActor, execution_id: str, request: ControlRequest) -> ExecutionView`;
- `apply_event(db, source_identity: str, event: object) -> ExecutionView`, for runtime-reported events;
- `reconcile_execution(db, actor, execution_id: str) -> ExecutionView`;
- `sweep(sessions, worker: WorkerIdentity, *, limit: int = 100) -> SweepReport`;
- `require_interpreter(interpreter_id: str) -> str`, which returns only a registered interpreter version;
- `RecoveryFence.current(db) -> int`, which raises `authority_unavailable` when the independent RecoveryAuthority cannot supply the current epoch.

`apply_event` validates the existing SDK event shape where applicable and reconstructs context from the database. Supplemental control events use a separate versioned closed model, not a change to SDK event bytes.

- [ ] **Step 1:** Add `test_cancel_serializes_before_new_dispatch`, `test_requested_state_is_not_observed_terminal`, `test_pause_resume_cannot_reset_deadline_or_budget`, `test_event_gap_and_digest_conflict_do_not_complete`, `test_sweeper_items_are_independent_and_idempotent`, `test_timer_and_wait_deadlines_survive_restart`, `test_unsupported_interpreter_is_never_admitted_or_claimed`, `test_restore_cannot_restore_epoch_or_grant` and `test_reconciliation_requires_admin_and_stopped_compute`. Assertions:
  - Controls: a cancellation committed before dispatch produces zero later provider calls. Pause and resume cannot reset a deadline or budget.
  - Events: a runtime-event gap leaves the projection pending, and a duplicate equal event is a no-op. A conflicting source sequence fences the execution.
  - Sweeper: each due item is swept in its own transaction, and sweeping twice changes nothing. A timer fires once after orchestrator and sweeper restarts.
  - Interpreters: an interpreter missing from the registry is refused at admission and never claimed.
  - Recovery: after a database restore, an old epoch denies orchestration and dispatch.
  - Reconciliation: an editor, or a request made before compute stop is confirmed, is refused.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert calls_after_cancel_commit == calls_at_cancel_commit
assert after_gap.state != "succeeded"
assert second_sweep_changes == 0
assert restored_old_epoch_error.code == "authority_unavailable"
assert resumed_wait.deadline_ms == original_deadline_ms
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_control.py backend/tests/execution/test_projections.py backend/tests/execution/test_recovery.py backend/tests/execution/test_sweeper.py backend/tests/execution/test_interpreters.py -q`; record red.
- [ ] **Step 3:** Implement:
  - **Controls.** Persist the control request and its outbox row, and mark the execution runnable, under the execution lock. The orchestrator applies the control in its next transaction, and dispatch (Task 9) checks the requested state within the same serialization boundary. Cancellation cancels `reserved` attempts and open waits only after the authority records commit. Claimed attempts receive cooperative cancellation through lease renewal, and the observed terminal state waits for containment or reconciliation. Pause stops new dispatch and wait creation while deadlines keep running. Resume requires current authority and remaining deadline and budget.
  - **Sweeper.** Work in `SKIP LOCKED` batches through the v15 due indexes, each item in its own transaction using database time:
    - due timers are satisfied;
    - expired external and form waits follow their declared expiry path or fail;
    - execution deadlines stop work at the next boundary, with cooperative cancellation of claimed attempts;
    - lapsed leases go to Task 9's `lapse_lease`;
    - expired unclaimed deliveries go to Task 9's `expire_delivery`.

    Capacity releases go through Task 5 services in Task 5's lock order. Publish queue metrics, keeping unknown counts distinct from zero.

  - **Events.** Orchestrator events are gap-free by construction. Runtime-reported events keep their event identity, digest and source sequence. At most 100 pending gaps are buffered per execution before the execution is fenced for reconciliation; completion is never inferred.
  - **Reconciliation.** `reconcile_execution` requires an owner or admin session, a current RecoveryAuthority and a `compute_stopped` observation for every uncertain attempt. It then lets progression either continue under the step's retry policy, with a new ordinal and a fresh reservation, or fail the step. It never turns unverifiable data into success.
  - **Interpreters.** Keep each registered version installed while any non-terminal execution pins it. Deployment admission and claims refuse unregistered versions. There is no history rollover.
  - **Recovery.** Orchestration, sweeping and dispatch call `RecoveryFence.current` and refuse while it is unavailable. Deliveries and sessions from older epochs are refused. After a restore, attempts `dispatched` or `claimed` under an older epoch become `uncertain` and reconcile. No restored row supplies epoch or revocation authority.
- [ ] **Step 4:** Repeat Step 2. Require PASS including gaps beyond the bound (with a fixed reconciliation reason), repeated controls, cancellation during a lock wait, lost grants during resume and a sweeper killed mid-batch. Task 13 repeats these with real process kills, an immediate PostgreSQL restart and a restore from an older backup.
- [ ] **Step 5:** Commit Task 11 files as `feat: sweep due execution work and preserve controls and recovery fences`.

**Conventions fixed by Task 11, for the owner's acknowledgement:**

- **Controls.** `request_control` is the only writer, and it runs under the execution row lock.
  - It records the request and a `control` outbox row, sets the requested state and marks the execution due. Dispatch, claims and renewals therefore see a committed pause or cancellation before any later dispatch.
  - The orchestrator applies it in its next transaction:
    - pause turns `running` or `waiting` into `paused`. An execution still `pending` stays `pending`, because it still holds its admission slot and the slot is returned only from that state;
    - resume returns to `waiting` when a retained open wait exists, otherwise to `running`;
    - cancel cancels reserved attempts and open waits at once. The terminal `cancelled` (reason `control`) waits until nothing the execution started may still run.
  - Cancellation is final. An equal request under the same ID changes nothing, and returns even once the authority it needed is gone. A different request under that ID conflicts. A control that changes nothing (a second pause) is recorded but adds no wake-up.
  - Owners and admins control any execution in scope, and editors only their own.
  - Resume also needs all of:
    - the initiator's current `execute` permission;
    - a deployment revision that may still run (`load_deployment`);
    - current entitlement and tenant grants;
    - the remaining deadline;
    - budget left. It is refused (`budget_exceeded`) when any limit is already spent by the execution's reservations; the next reservation still checks its own demand.
  - Deadlines, budgets and wait deadlines keep running while paused, and none is reset.
- **Pause stops work, as the spec's "cooperative cancellation and bounded containment" says.**
  - At the first orchestrator pass after a pause, root attempts that have not started are released: reserved ones, and dispatched ones that no runtime has claimed (no lease). Each ends `cancelled` with the _interrupted_ record, a pending delivery can no longer be claimed, and the capacity step confirms the stop and frees the compute slot. A long pause therefore neither holds slots nor lets the sweeper retire reservations.
  - A reservation whose attempt no runtime ever claimed, once the capacity step confirms its stop, moves to the schema's `released` state (`BudgetService.release_unstarted`), which counts as no liability. Nothing ran, so nothing is charged, and repeated pauses never spend the budget. This applies to every never-claimed cancelled attempt that the capacity step stops: a pause release, a cancellation, a deadline, a parallel failure. Anything claimed stays charged as observed (owner decision 1). **Owner-confirmed on 2026-10-24** ("OK, keep the refund"). **Owner-confirmed on 2026-10-26** ("yes, refund those too and proceed"), the owner extended the refund: a never-started attempt that ends any other way, by delivery expiry, reservation retirement or a decline, is released at zero too once its stop is confirmed. They remain counted failures. Task 9 had settled their tokens and cost at zero but kept their reserved active time as liability; see "Owner-approved change: release every never-claimed reservation at zero" below.
  - A claimed attempt's renewal is refused (`forbidden`), for a pause exactly as for a cancellation, and also when the initiator lost `execute`. Task 10's runtime now treats a `forbidden` renewal of a still-live lease as a request to stop: it cancels the provider child and reports `cancelled` with the usage it observed.
  - When a pause is requested, `request_control` records a pause marker (`activity.pause:` request row) for each root attempt then claimed, under the execution lock. A `cancelled` report from an attempt with that marker, short of a requested cancellation, is recorded as interrupted, even if a resume came first. The marker is never cleared. A marked attempt that renews after the resume, and later stops for another reason, is still recorded as interrupted. The other reason could be a revoked initiator or a stop of its own. The 10 root attempts bound that. Any other `cancelled` report (a revoked initiator, a runtime stopping on its own) is a failure the step's policy counts.
  - An interrupted attempt never counts toward its step's `max_attempts`. On resume, progression creates the step's next ordinal with a fresh reservation. A step still has at most 10 root attempts in all, interrupted ones included, because an agent's children take the ordinals above 10 on the same step instance. Past that the step fails `retries_exhausted`, which also bounds how often pauses can restart it. The bound counts releases of work that never started too: a step reserved and queued through 10 pauses fails, although nothing of it ran or was charged.
  - **Owner-confirmed on 2026-10-23** ("OK to all three, proceed"):
    1. a paused agent or read starts over on resume; what the stopped attempt already spent stays charged;
    2. a provider stopped mid-call has unknown usage. Its attempt becomes uncertain liability and the execution goes to `reconciliation_required`, so it needs one owner reconciliation before resume. The runtime's report already confirmed the compute stop, so the owner can reconcile at once, and the attempt stays interrupted rather than failing its step;
    3. signals are accepted while paused, and progression consumes them on resume. A signal accepted before its wait's deadline still counts, even if the sweeper expired the wait during the pause. A cancellation still refuses signals.
  - A child request or claim refused by a pause or cancellation ends the agent as `cancelled`, never `provider_failed`: the runtime renews at once to tell a stop from another refusal.
- **Failing transitions and the deadline.**
  - The deadline, a cancellation, a pause and a reconciliation fence need only the execution row. They are evaluated before the pinned program is loaded, in the pure and in the capacity transaction, so a program that cannot be loaded never holds them up.
  - A transition refused because the stored program or rows fail verification (`ProgramError`: `integrity`, `occurrence_unresolved`) would fail alike on every retry. The engine fences the execution instead (Task 7's "Task 11 fences executions that keep failing"): `reconciliation_required`, reason `uncertain`, event detail the code, marker cleared. A `pending` execution is left to its deadline, so that its admission slot is returned. A sweeper wait whose program cannot be loaded marks the execution due, so it is fenced the same way.
  - Other refusals (lock timeouts, provider outages) keep the engine's back-off of at most 60 s, bounded by the deadline.
  - `reconciliation_required` halts progression: nothing new is derived and nothing is loaded, and the sweeper leaves its due waits until it resumes. Cancellation and the deadline still end it.
  - Progression caps every wake-up it sets at the execution's deadline, so a step's retry back-off (up to 3600 s) cannot outlast it.
  - Reconciliation, of either kind (owner `reconcile_execution` or Task 9's evidence-based one), returns to `paused`, `waiting` (a retained open wait) or `running`.
- **Sweeper.** `ExecutionSweeper(leases, recovery).sweep(sessions, worker, limit)` handles each item in its own transaction, fenced by the current epoch, so a failed or interrupted item never affects another.
  - Each item's transaction waits at most 500 ms for a row lock (`SET LOCAL lock_timeout`). A blocked item is deferred to a later pass, so one tenant's long transaction cannot stall a pass.
  - Order of a pass:
    1. attempts in flight under an older epoch (restore);
    2. lapsed leases;
    3. expired unclaimed deliveries;
    4. spent reservations;
    5. due waits: a timer is satisfied, an external or form wait expires;
    6. overdue executions with no marker, or one more than the engine's longest back-off (60 s) away.
  - Execution rows are taken `SKIP LOCKED`, as the orchestrator's claim takes them. The engine's back-off is never cut short.
  - v15 has no state index on attempts or reservations. The spent-reservation scan is driven from capacity rows with active work, then that environment's non-terminal executions (`execution_queue_idx`), then each one's attempts and reservation by `(scope, execution_id)`. It is bounded by live executions and their attempts, never by retained history. Task 13's load runs must still measure it, against the Task 9 obligation.
  - Queue depths are logged as fixed-key JSON after each pass and kept on the sweeper (`depths`) for the worker's metrics, bounded at 10,000. A count that cannot be read is `null`, never zero. The keys are due executions, spent reservations, due waits, overdue executions, restored attempts, lapsing leases and expiring deliveries.
- **`SweepReport` amendment:** `retired_reservations` and `restored_attempts` are added, both defaulting to 0.
- **Recovery.**
  - `RecoveryFence(fleet).current(db)` asks the independent authority through the bounded fleet lookup and never reads rows. It carries the authority's `test_only`.
  - After a restore, pending deliveries and active leases signed under an older epoch make their attempt `uncertain`, its reservation uncertain liability, and the execution `reconciliation_required`. Both are found through the v15 pending-delivery and active-lease indexes. `expire_delivery` leaves such deliveries alone, so an old epoch's "unclaimed" never becomes "never started". A pending old-epoch delivery whose attempt is no longer in flight (an inconsistent restore) is closed (`conflict`), so it is never selected again.
  - **Residual, for Task 13's restore tests:** attempts and reservations carry no epoch. A restored row that still shows an attempt `reserved`, which was in fact dispatched after the backup, cannot be told apart. Such a row is treated as never started: since 2026-10-24 a cancellation releases it at zero, and since 2026-10-26 so does its retirement, although it may have run. **Open for the owner.**
- **Runtime-reported events.** `EventProjection.apply_event(db, source_identity, event)` accepts the SDK's admitted `RuntimeEvent`. It requires all of:
  - the current epoch and this installation;
  - a source that equals the authenticated identity;
  - a source holding the execution's live work: a pending delivery or an active lease, under the current epoch and the event's generation.

  Rules:
  - Each `(source, generation)` starts at sequence 1.
  - An event beyond the next expected sequence is buffered in one of at most 100 gap slots per execution.
  - Another event under a known ID, an event at a taken or stale sequence, or a gap beyond the bound fences the execution: `reconciliation_required`, reason `uncertain`, with event detail `event_conflict` or `event_gap`.
  - Action-review and outcome kinds are refused. No runtime event moves an execution.
  - Reconciliation abandons buffered events (`conflict`), and the source continues after the highest sequence seen. A late event behind the abandoned range is refused (`conflict`) and never fences again.
  - No E04 component produces runtime events, and Task 12's listed routes serve none. **Obligation for whichever task first serves one:** `apply_event` takes a `RuntimePrincipal` and rechecks the runtime's current credential and session under the locks, as Task 9's services do.

- **Reconciliation.** `ControlService.reconcile_execution(db, actor, execution_id)` needs:
  - an owner or admin;
  - the current epoch;
  - an execution in `reconciliation_required`;
  - for every uncertain attempt, a `compute_stopped` observation and a confirmed stop.

  It fails those attempts as `uncertain`, except an interrupted one, which stays interrupted. It keeps their unverifiable usage as uncertain liability. It returns the execution to the requested state and marks it due, and progression then retries or fails under the step's policy. In E04 a compute stop is confirmed by a runtime's report, by Task 9's historical reconciliation, or by a separately authorized observation.

- **Interpreters.**
  - `COMPILERS` maps each interpreter version to the SDK compiler revision whose plans it interprets (v1 → `lotuspond.workflow-compiler.v2.1`). `REGISTERED` holds the versions whose compiler this build's SDK compiles. The SDK carries one compiler revision, so a build whose compiler changed registers no v1: its workers refuse to start until no non-terminal execution pins v1, and new work needs a new version that pins the new compiler.
  - `require_interpreter` returns only registered versions, otherwise raising the new code `unsupported_interpreter`. Admission services and engines cannot be built for an unregistered version.
  - `load_program` refuses (`integrity`) a publication compiled by another revision than the one its version pins.
  - The worker refuses to start (`unsupported_interpreter`) while a non-terminal execution pins a version this build does not register, or a plan of a compiler its version does not pin (`unsupported_pins`).
- **Projections.** Read models over committed rows: `project_execution` (read permission) shows the observed and the requested state separately. Events are an append-only log, and not every observed change emits one: a first reservation moving `pending` to `running`, and a last wait closing from `waiting` to `running`, emit none. Task 13's ENG-5 replay check must allow for that or add those events.
- **Note for Task 12's wiring.** The fleet lookup (`FleetDependencies.current`) admits one call at a time and refuses the others. The race tests therefore give each concurrent caller its own (`own_fence`). An API process that shares one `FleetDependencies` across concurrent requests would refuse concurrent controls, events and results with `authority_unavailable` under load, so Task 12 must account for it.
- **Production wiring.** `production_engine` still refuses (`engine_unavailable`). The interpreter, sweeper and fence now exist, but no qualified production providers do: artifact keys, entitlement, the recovery authority, activity trust and signing. Task 14's operations procedure wires them.
- **Files beyond Task 11's row,** each necessary:
  - `execution/progression.py`: controls observed, pause release, interrupted attempts, the load-free deadline, cancel, pause and fence paths, the wake-up cap, `fence_execution`, the `cancelled` terminal state;
  - `execution/engine.py`: the fence for failing transitions, and the registry check;
  - `execution/program.py`: the pinned compiler check;
  - `execution/dispatch.py`: pause markers, an interrupted report recorded, and kept through reconciliation;
  - `execution/leases.py`: renewal refused while paused, change flags and the old-epoch guard;
  - `execution/inbox.py`: signals accepted while paused;
  - `execution/reconciliation.py`: the resumed state, and interrupted attempts kept;
  - `execution/admission.py`: the registry check;
  - `execution/budgets.py`: a never-claimed stopped reservation released, with no liability;
  - `execution/engine_contracts.py`: the `SweepReport` amendment;
  - `execution/errors.py`: the new code;
  - `execution_worker.py`: the pin check at startup;
  - `runtime/python/src/lotuspond_runtime/{activity_worker,agent}.py`: a refused renewal is a stop, not a loss;
  - tests:
    - `control_fixtures.py`;
    - `activity_fixtures.py` (a settable recovery epoch, limits within the package's);
    - `test_deployments.py` and `test_progression.py` (the same limit clamp);
    - five short-time fixtures in `fixtures/control-flow`;
    - the runtime fake's lost-versus-refused renewal and refused children, with the runtime tests that drive them.

## Task 12: Expose explicit execution APIs with current authorization

**Files:** Ownership-table Task 12 files; `backend/tests/execution/{test_routes.py,test_runtime_routes.py,test_boundary.py}`. Modify `schemas.py` to add `execute`, `execution_signal` API-key scopes; do not reinterpret `run`. Add scoped changelog entry in `app.py` only for implemented capability.

**Interfaces:** Prefix `M=/api/v1/projects/{project_id}/environments/{environment_id}`; workspace only from authenticated context. `POST/GET M/executable-packages`, `GET M/executable-packages/{id}` and `/content`; `POST/GET M/deployments`, `GET M/deployments/{id}`, `POST M/deployments/{id}/status`, `POST M/deployments/{id}/revisions/{revision_id}/status`; `POST/GET M/executions`, `GET M/executions/{id}`, `GET M/executions/{id}/events`, `GET M/executions/{id}/waits`, `POST M/executions/{id}/signals`, `/control`, `/reconciliation`; `POST M/context-sources` and `POST M/context-sources/{id}/versions`, `/revoke`; `GET M/artifacts/{id}/versions/{version}`. Runtime prefix `/api/v1/runtime/activities`: `POST /poll`, `/{delivery_id}/claim`, `/leases/renew`, `/children`, `/results`, and `/decline` (the owner-approved addition beyond E04; serve it here with its changelog item); separately scoped worker artifact/context reads and result uploads under `/api/v1/runtime/activity-artifacts`. Worker gets only artifacts linked to its current claimed attempt, never a generic artifact bearer capability.

- [ ] **Step 1:** Add `test_route_role_scope_matrix`, `test_runtime_credentials_cannot_enter_customer_routes`, `test_human_form_rejects_api_key`, `test_headers_body_and_pagination_boundaries`, `test_orchestrator_outage_keeps_evidence_available`. Assertions: owner/admin sessions manage/publish; editors execute enabled work and control their own executions, owner/admin control any scoped execution; viewers read only; keys require explicit scope and current creator role and cannot manage/human-form/reconcile; no implicit platform-admin access. Duplicated auth/session/workspace headers and cross-scope IDs refuse; stopped orchestrator workers do not block read/export/admin.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert evaluation_run_scope_response.status_code == 403
assert human_form_key_response.status_code == 403
assert evidence_during_orchestrator_outage.status_code == 200
assert execution_detail.headers["cache-control"] == "no-store"
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_routes.py backend/tests/execution/test_runtime_routes.py backend/tests/execution/test_boundary.py -q`; record red.
- [ ] **Step 3:** Wire services through explicit request/response DTOs, no ORM serialization. Preserve verification/MFA/session Origin/content-type protections. Mutation idempotency key is required except explicit signal/control identity protocols. Keyset pagination `after_id`, limit default 50/max100, no full-table counts. Use no-store and fixed HTTP errors: 400 invalid, 401 unauthenticated, 403 forbidden, 404 scoped missing, 409 conflict, 413 limit, 429 capacity, 503 unavailable dependency. Default body max1MiB; activity delivery max131072; actual decoded value/envelope limits still apply. Add audit with fixed identifiers/state only. Runtime output upload verifies live lease and output schema; late data uses the separate historical path.
- [ ] **Step 4:** Repeat Step 2 and existing identity/registry/fleet route tests; PASS with production-deny startup defaults. OpenAPI names distinguish evaluation runs, production executions and waits; no endpoint implies verified effects or unimplemented Studio screens.
- [ ] **Step 5:** Commit Task 12 files as `feat: expose scoped durable execution and runtime activity APIs`.

**Conventions fixed by Task 12, for the owner's acknowledgement:**

- **Opt-in and deny by default.**
  - Every execution route, customer and runtime, is part of the opt-in fleet API: with `fleet_enabled` off it answers 404, as E03's routes do.
  - `create_app(..., execution_services=ExecutionServices(...))` takes constructed services only in test mode, as `fleet_dependencies` does. Production builds none yet, so a route that needs a service answers 503 `authority_unavailable`.
  - Database-only reads still answer without any service or worker: packages and their exact content, deployments, execution detail, events and waits. A stopped orchestrator or sweeper therefore never blocks evidence. Withdrawing a deployment (`disabled`, `revoked`) also needs no provider.
  - Task 14 builds `ExecutionServices` from qualified providers.
- **Authorization.**
  - A route authenticates the session or `lp_` key (`security.authenticate`), then pre-checks the operation with `authorize_execution(..., locked=False)`: the same rules, read from current committed rows without locks, in the request's own session. A request therefore holds one pooled connection, and the route holds no lock that a service must take earlier.
  - Every service then reauthorizes in the request's transaction under its own lock order: the installation cursor and provider lookups come before account rows.
  - Reads (`read`) are always authorized without locks, by services too (`project_execution`, artifact and context reads). A read fences no write, so readers never queue behind a writer or each other on the workspace row, and the orchestrator and sweeper never skip a tenant because someone is reading. Writes keep their locks, so a revocation cannot commit between a write's check and the write.
  - Shared refusals keep closed codes: 401 `unauthenticated`, 429 `rate_limited`, any 5xx (an MFA or quota-store outage) `authority_unavailable`, anything else `forbidden`.
  - Each workspace has separate per-minute allowances for execution reads and execution writes (1,000 each), so polling never spends a write's allowance.
- **Roles.**
  - Package publication follows Task 3's tested rule: owners, admins and editors, and `write` keys.
  - The spec's "owner/admin publishes" concerns deployment revisions (`manage`): only owners and admins create, enable, disable or revoke deployments, register context or reconcile.
  - Editors start enabled work and control their own executions; owners and admins control any execution in scope; viewers read.
  - Platform administration grants nothing here.
- **Keys.**
  - `KeyInput` offers `execute` and `execution_signal`, with up to 7 scopes.
  - Keys need `read` to read, `write` to publish, `execute` to start or control, and `execution_signal` to signal.
  - `run` grants nothing here.
  - Keys never manage, submit human forms or reconcile, and a key carries its creator's current role.
- **Runtime paths.**
  - Task 9's fixed paths supersede the plan's `/{delivery_id}/claim` and `/leases/renew`. Task 10's client already calls them: `POST /api/v1/runtime/activities/{poll,claim,renew,children,results,decline}` and `/activities/{attempt_id}/values`.
  - `/decline` and its changelog item ship here.
  - Results, declines and child requests take the installation cursor before runtime authentication, in Task 5's order. A lock-free look-up comes first: an unknown or inactive credential or session is refused (401) before the cursor, so unauthenticated traffic never queues on it. The locked authentication after the cursor still decides.
  - Every runtime route authenticates before it validates the body.
- **Worker reads**, under `/api/v1/runtime/activity-artifacts` (`ActivityReads`, in `activity_reads.py`):
  - `/records` returns the pinned records of a delivery dispatched to this session, named by its exact digest: pending and unexpired, or leased under this session's live lease.
  - `/agent` returns the declarations of a root agent attempt this session holds: model, instructions, tools, the pinned knowledge sources and the limits.
  - `/{attempt}/input` returns exactly the held attempt's input.
  - `/{attempt}/context` returns one knowledge source the held agent step pinned, bound at deployment. It is resolved for the execution's initiator under current source access, with the declaration's freshness and classifications.

  Nothing else is served: there is no generic artifact capability.
  - Records and input grant nothing, so, like results, they skip the initiator's current `execute`; context reads require it.
  - A delivery this session will not be served gets `forbidden`, `not_found` or `conflict`, which the runtime may decline; an outage gets 503, which it does not.
  - A revoked context source withdraws the deployment that pins it (Task 3's check), so its read answers 503 `dependency_unavailable`, before any source access.

- **Errors.** Closed codes, `{"error": code, "message": code}`, always `no-store`:
  - 400: `invalid_request`, `unsupported_step`, `unsupported_policy`;
  - 401: `unauthenticated`;
  - 403: `forbidden`;
  - 404: `not_found`;
  - 409: `conflict`, `budget_exceeded`;
  - 413: `input_limit`;
  - 429: `capacity_unavailable`, `rate_limited`;
  - 503: every `*_unavailable` code, plus `artifact_corrupt` and `unsupported_interpreter`.

  Validation errors are 400 and never echo fields.

- **Boundary.** `ExecutionRequestBoundary` covers the customer execution paths; it is outermost, so its codes come first.
  - It refuses a duplicated `authorization`, `cookie`, `x-lotuspond-workspace`, `idempotency-key`, `content-type`, `content-length` or `content-encoding` header (400).
  - It refuses any runtime session header (401).
  - Headers are capped at 32 KiB. Mutations need uncompressed `application/json`, with strict decoding (no duplicate keys or nonfinite numbers). Bodies are capped at 1 MiB.
  - Runtime paths keep E03's `FleetRequestBoundary`: 64 KiB bodies, the transport's bound. A delivery's wire form is at most 131,072 bytes.
- **Mutations** require an `Idempotency-Key` (OpaqueId), except signals (`signal_id`), controls (`request_id`) and reconciliation, which is idempotent by state.
  - Context registration replays through `mutation_replay`, which gains `execution.context.register` and `execution.context.version`. It takes the account locks first, as `register` does, so two equal concurrent requests serialize and the second replays the first. A new source's ID is chosen after the replay check.
  - Each mutation writes an `AuditEvent` with fixed identifiers and states only: `executions.packages.published`, `.deployments.created`, `.deployments.status`, `executions.started`, `.signalled` (on the wait), `.controlled`, `.reconciled`, `.context.registered`, `.context.revoked`. It records the request's own identifier (`request_id`: the idempotency key, control request ID or signal ID). An equal retry, which the service answers by replaying the first answer, is not audited again: the service marks the replay on its session (`mark_replay`: `mutation_replay`, admission, control, the inbox, and revoking a revoked source), so the audit trail is never searched under the service's locks.
  - Reconciliation takes no key: it is idempotent by state, so a retry after a lost answer gets 409 `conflict` once the first succeeded, and is never applied twice.
- **Reads.**
  - Pages are keyset: `after_id` and `limit` 1..100, default 50, with no counts. Unknown or repeated parameters are refused, and a cursor from another collection is `not_found`.
  - Events page by `after_sequence` and show metadata only: sequence, family, code, time, attempt and application state. Their encrypted contents are not served.
  - Waits show the occurrence path, state and deadline; a page reads only its own waits' step instances.
  - Package content is the exact admitted bytes, digest-checked.
- **OpenAPI** names the operations `*_production_execution*` and `*_production_execution_wait*`, under tags for production executions, waits, deployments, executable packages, context sources and execution artifacts. Nothing is named for evaluation runs.
- **Runtime 0.2.0, unreleased.**
  - `ActivityClient` gains `records`, `agent`, `input` and `context`. `ControlPlaneSources` serves Task 10's `ActivityRecordSource`, `ValueSource` and `ContextClient` from them.
  - A malformed control-plane answer raises `transport_failed`, never a declinable code, and the input's digest is checked.
  - The runtime runs at most 64 model rounds, whatever an agent step declares (up to 1,000).
  - The control transport allows the four read routes, session-only. Response bounds: poll 131,072 bytes, reads 1 MiB, everything else 64 KiB.
  - `RuntimeSupervisor(..., activity_clock=ServerClock)` re-anchors the worker's clock from every E03 session and heartbeat response, on its own transport. It refuses a clock on another monotonic source.
- **Known limitation, for Tasks 13 and 14.** The API shares one `FleetDependencies`, whose recovery lookup admits one call at a time. A concurrent caller gets 503 `authority_unavailable`, which the runtime retries as transient. Customer requests that hit it should be retried too; it is not changed here. Task 14's production wiring must resolve it before the API serves real load. Separately, `ArtifactStore.get` still locks the artifact row it reads (Task 2), so concurrent reads of one artifact serialize; Task 13's load measurement should cover it.
- **Open for the owner.**
  - Package publication follows Task 3's rule (editors and `write` keys publish) rather than the step's "owner/admin sessions manage/publish". Publishing activates nothing; only owners and admins deploy.
  - A revoked knowledge source answers the runtime 503 (the withdrawn deployment), so the agent attempt ends `unavailable`, a retryable class, not `provider_failed`. Retries stay bounded: the withdrawn deployment dispatches nothing new.
- **Not served.** Runtime-reported events (`EventProjection.apply_event`) still have no route. Task 11's obligation stands: whichever task serves them takes a `RuntimePrincipal`.
- **Files beyond Task 12's row,** each necessary:
  - backend `execution/activity_reads.py` (the worker reads);
  - backend `execution/publication.py` (two replay operations, and the replay marker);
  - backend `execution/authorization.py` (lock-free reads and pre-checks, and `mark_replay`);
  - backend `execution/admission.py`, `control.py`, `inbox.py` and `context.py` (each marks an equal repeat, so it is audited once);
  - runtime `activity_client.py`, `control_transport.py` and `supervisor.py`;
  - tests: `api_fixtures.py`, `test_api_concurrency.py`, and runtime `test_activity_client.py` and `test_supervisor.py`.

## Task 13: Qualify the engine with a conformance suite and real failures

**Files:** Ownership-table Task 13 files:

- tier 1 conformance: `backend/tests/execution/conformance/{conftest.py,harness.py,test_restart.py,test_admission.py,test_activities.py,test_waits.py,test_regions.py,test_cancellation.py,test_versions.py,test_revocation.py,test_disconnection.py,test_isolation.py,test_privacy.py,test_invariants.py}`;
- tier 2: `backend/tests/execution/conformance/disruptive/{test_database_restart.py,test_restore.py}`;
- tier 3 and smoke: `scripts/checks/{execution-smoke.py,execution_process.py,execution-load.py}`;
- installed acceptance: `backend/tests/execution/test_installed_acceptance.py` and `deploy/checks/test_durable_execution_smoke.py`.

Create `docs/validation/2026-10-15-durable-execution.md` recording failures and observed limits.

**Interfaces:**

- **Black box.** An `engine` fixture parametrizes over registered `ExecutionEngine` implementations; `postgresql` is the only one. Cases drive only the public services, HTTP routes and runtime protocol. A small, labelled set of integrity cases tampers with stored rows to prove that reads fail closed.
- **Harness.** It starts as separate killable processes (SIGKILL, SIGSTOP and SIGCONT): the API on an ephemeral localhost port, orchestrator and sweeper workers, and runtimes. A TCP proxy between runtimes and the API can stall or sever connections. Runtimes receive only the API URL and an enrollment credential.
- **Clock.** Test time is database time plus a harness offset, read through the engine clock. No test edits deadline, lease or expiry columns.
- **Smoke and supervision.** `execution-smoke.py --output <private-directory>` produces a versioned `execution-report.json`, source and distribution manifests, the decoded-event privacy result and resource cleanup receipts. `execution_process.py` is the independent outer supervisor, with a bounded timeout and a census of the processes, containers, volumes and schemas it owns. `execution-load.py` runs tier 3 and writes its own report.
- **Required mode.** `LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS=1` turns missing database, artifact or cluster prerequisites into failures, not skips.

| Tier           | Runs                                                                                                            | Contents                                                   |
| -------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 1: conformance | `npm run test:database` and the existing CI PostgreSQL job                                                      | Everything except cluster restarts, restores and load      |
| 2: disruptive  | Opt-in `LOTUSPOND_DISRUPTIVE_EXECUTION_TESTS=1`; a separate CI job with its own disposable PostgreSQL container | Immediate PostgreSQL restart; restore from an older backup |
| 3: load        | Manual or scheduled on recorded hardware, with its own disposable cluster                                       | The planning-target workload below; gates E04 completion   |

Tier 2 and tier 3 clusters use private ports and refuse 5432, 55439 and the development database.

**Spike checks that apply to E04.** Numbers follow `results/pg-demo.txt` of the [spike](../../plans/2026-10-15-durable-engine-spike.md):

| Spike checks                                    | E04 conformance case                                                                                                                                                                                                                                                                                   | Tier |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---- |
| 1–2: kill and crash restart during a human wait | SIGKILL orchestrator, sweeper and runtime during a form wait; then `pg_ctl -m immediate` on the suite's own cluster. The wait, its deadline and its payload schema digest are intact                                                                                                                   | 1, 2 |
| 3–4: bound action intact; other digest refused  | Wait identity, deadline and schema are byte-identical. A signal naming another wait or digest is refused, and the wait stays open                                                                                                                                                                      | 1    |
| 5–7: new processes complete; one effect         | Fresh processes finish. Each attempt's provider call is observed exactly once. Event actors show distinct worker and runtime identities                                                                                                                                                                | 1    |
| 8–10: lease fencing                             | Orchestrator: SIGSTOP inside a transaction is ended by role timeouts and another worker advances (ENG-6). Runtime: a SIGSTOPped lease lapses, the attempt becomes uncertain and is never re-leased, and the late result is historical evidence only. After reconciliation a new ordinal completes once | 1    |
| 11–13: duplicate admission                      | 25 concurrent admissions with one key give one execution and 24 originals, in one row. A changed payload conflicts                                                                                                                                                                                     | 1    |
| 14: repeated schedule occurrence                | E04 has no schedule triggers. Replacement: an equal retry after a new deployment revision is activated returns the original execution, pinned to its revision                                                                                                                                          | 1    |
| 23–24: cancel during a review                   | Cancelling during a form wait cancels the execution and the wait; a later signal is refused                                                                                                                                                                                                            | 1    |
| 25: cancel during an in-flight read             | A `slow` read is cancelled: renewal reports the cancellation, a reported result is recorded and no next step runs                                                                                                                                                                                      | 1    |
| 26 (cancel before dispatch) and 27              | Cancelling before dispatch signs no delivery and makes zero provider calls; no cancelled execution reaches `succeeded`                                                                                                                                                                                 | 1    |
| 29–33: pinned upgrade and interpreter pin       | Revision 2 is activated while revision 1 waits: new admissions pin revision 2, and the waiting execution completes on revision 1. An orchestrator without the interpreter version never claims it, and a runtime without the capability never receives its deliveries                                  | 1    |
| 34: review expiry                               | A form or external wait past its deadline follows its expiry path through the sweeper, with zero provider calls afterwards                                                                                                                                                                             | 1    |
| 15–22, 26 (write half), 28, 35                  | Writes, read-back, permits and approval invalidation move to E05's suite, which reuses this harness                                                                                                                                                                                                    | —    |

**Executable-v2 regions the spike never ran:**

| ID    | Case                                                                                              | Required result                                                                                |
| ----- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| REG-1 | Parallel all-join with three branches; one fails after another succeeded                          | Undispatched siblings are cancelled; completed and uncertain results are retained; no join     |
| REG-2 | Iteration over 1,000 items with declared concurrency 5; orchestrator killed mid-iteration         | Output order equals input order; at most 5 of its attempts active at once; each item runs once |
| REG-3 | Subworkflow with stricter limits than its parent                                                  | The inherited limits apply, and the root budget covers both                                    |
| REG-4 | Timer across orchestrator and sweeper kills (tier 1) and an immediate PostgreSQL restart (tier 2) | It fires once, at its original deadline                                                        |
| REG-5 | External wait receiving a duplicate, a conflicting, a late and an out-of-order signal             | One satisfied wait; the conflict and the late signal are refused; no completion is inferred    |
| REG-6 | A package at the 10,000-step expansion ceiling                                                    | Limits hold; rows read per transition stay within the recorded region bound                    |

**Revocation, disconnection and restore:**

| ID        | Case                                                                                                    | Required result                                                                                                                                                                               |
| --------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| REV-1     | Runtime instance revoked while holding a lease                                                          | Renewal and result are refused; the attempt becomes uncertain; no new delivery reaches that instance                                                                                          |
| REV-2     | Instance revoked between delivery and claim                                                             | The claim is refused and the delivery expires; a new ordinal goes to another instance                                                                                                         |
| REV-3     | Form signal from a member removed after the wait opened                                                 | Refused; the wait stays open                                                                                                                                                                  |
| REV-4     | Workspace suspended                                                                                     | Admission is refused, nothing is dispatched and waits are preserved                                                                                                                           |
| REV-5     | New revision activated, versus a revision disabled or revoked                                           | After activation, pinned executions continue. After disable or revoke, they get no further activities                                                                                         |
| REV-6     | Entitlement or price authority expires mid-execution                                                    | No new dispatch; read, export and reconciliation still work                                                                                                                                   |
| DISC-1    | Runtime disconnected before claim                                                                       | The delivery expires and a new ordinal is created; after reconnection the old delivery cannot be claimed                                                                                      |
| DISC-2    | Runtime disconnected after claim, with a provider call in flight                                        | The lease lapses and the attempt becomes uncertain. On reconnection the journal reports stop and usage as historical evidence. One provider call in total                                     |
| DISC-3    | Runtime revoked while disconnected                                                                      | Its session is refused on reconnection; no further calls                                                                                                                                      |
| DISC-4    | All runtimes unreachable for several lease periods                                                      | Waiting executions are unchanged; recovery completes after reconnection                                                                                                                       |
| RESTORE-1 | Backup taken during a form wait; afterwards a signal, an activity and completion; then restore (tier 2) | Older-epoch deliveries and sessions are refused. Activities claimed after the backup reconcile as uncertain with journal evidence. A resubmitted signal with its original ID is accepted once |
| RESTORE-2 | Backup taken before a runtime instance was revoked (tier 2)                                             | The restored instance cannot return: its E03 generation and the independent epoch refuse it                                                                                                   |
| RESTORE-3 | RecoveryAuthority unavailable after a restore (tier 2)                                                  | Orchestration, sweeping and dispatch refuse; read and export work                                                                                                                             |

**Tenant isolation and engine invariants:**

| ID    | Case                                                                        | Required result                                                                                                                                                                                                                             |
| ----- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TEN-1 | Another workspace admits to, reads, signals, controls or reconciles records | `not_found`, indistinguishable from a missing record                                                                                                                                                                                        |
| TEN-2 | Rows referencing another scope, written directly                            | Rejected by composite foreign keys                                                                                                                                                                                                          |
| TEN-3 | Another pool's runtime claims, renews or reports using a known ID           | Refused without revealing existence                                                                                                                                                                                                         |
| TEN-4 | The same idempotency key in two scopes                                      | Two executions                                                                                                                                                                                                                              |
| TEN-5 | A reviewer in two workspaces                                                | Each form-wait list shows only its own scope                                                                                                                                                                                                |
| TEN-6 | The same package name and version with different content in two scopes      | Each execution pins its own digest                                                                                                                                                                                                          |
| TEN-7 | Projections, events and delivery payloads scanned for foreign identifiers   | None present                                                                                                                                                                                                                                |
| ENG-1 | Execution deadline during an activity and during a wait                     | Work stops at the boundary; uncertain work reconciles                                                                                                                                                                                       |
| ENG-2 | Retry bounds; non-conforming read or model output                           | Retries stop at declared bounds; bad output fails the step and never becomes data                                                                                                                                                           |
| ENG-3 | Delivery and lease bounds                                                   | Deliveries are single-use and expire within 5 minutes; leases last at most 60 s and end within the attempt deadline                                                                                                                         |
| ENG-4 | Event order and grants                                                      | Sequences are gap-free under concurrent transitions. The orchestrator role cannot update or delete events. Tested with a uniquely named role on the isolated cluster, created only with the owner's approval because roles are cluster-wide |
| ENG-5 | Projection replay                                                           | Replaying events reproduces each execution's projected state                                                                                                                                                                                |
| ENG-6 | Stalled orchestrator                                                        | Its role timeouts end it, and another worker advances the execution                                                                                                                                                                         |
| ENG-7 | Unsupported interpreter version                                             | Refused at admission and never claimed                                                                                                                                                                                                      |
| ENG-8 | Tampered stored package, plan or input (integrity case)                     | The digest mismatch is refused on load                                                                                                                                                                                                      |

**Load (tier 3).** The harness admits through the public API.

- **Workload.** 5 admissions per second for 60 minutes, then a 2× burst for 5 minutes. Admissions are spread over at least 8 environments, because the installation allows 25 active attempts and each environment 5. Each case has two activities with injected latency (mean 1 s, p95 3 s, log-normal). 20 % of cases enter a form wait that stays open.
- **Pass criteria.** Admission p95 below 1 s; p95 runnable-queue age (due executions plus schedulable attempts) below 30 s; no sustained backlog growth in the final 30 minutes; the burst backlog back in its pre-burst range within 10 minutes; at most one provider call per attempt.
- **Waiting cases.** Separately, admit 10,000 executions into waits, kill every worker and runtime, and restart them. Record time to resume, claim latency with the waiting set present, sweep duration and memory.
- **Recorded.** Hardware, PostgreSQL version and settings, latency distributions, claim and dispatch latency, lock-wait time on `execution_scheduler_cursors`, database CPU, WAL rate, table and index sizes, and dead-tuple ratios. These calibrate the thresholds below. A local result is evidence about this harness on this hardware, not hosted capacity or an SLA.

- [ ] **Step 1:** Add the cases above, and installed-artifact acceptance that asserts:
  - independently installed API, runtime and SDK origins;
  - actual TLS authentication;
  - the synthetic reference workflow's result and measured read and model call counts;
  - rollback injection before and after the admission, transition, claim, result, signal and projection commits.

  `test_decoded_events_and_errors_exclude_payloads` inserts unique private sentinels in prompts, documents, credentials, source IDs and provider errors. It decodes every event artifact and inspects projections, logs, errors, outbox and inbox rows and plaintext columns, and asserts that no sentinel appears outside its authorized encrypted artifact. Add a positive control before each denial assertion, so "nothing ran" cannot pass because a service was dead.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert required_integration_skips == 0
assert all(item["passed"] for item in observed_scenarios)
assert all(sentinel not in decoded_events_and_errors for sentinel in private_sentinels)
assert max_provider_calls_per_attempt == 1
assert cleanup_receipt["confirmed"] is True
```

- [ ] **Step 2:** Run tier 1 with the required flags: `B backend/tests/execution/conformance backend/tests/execution/test_installed_acceptance.py -q`. Run tier 2 against an owned cluster: `B backend/tests/execution/conformance/disruptive -q`. Record missing behavior as red.
- [ ] **Step 3:** Build the owned harness. Install the wheel and an sdist-rebuilt wheel in isolated environments. Start real API, orchestrator, sweeper and runtime processes. Measure queue, call, event, resource and cleanup counts from observed records, inspect failed phases and keep safe diagnostics. Keep independent effect and authority counters outside restored snapshots. Never use the spike code as product or harness code.
- [ ] **Step 4:** Repeat Step 2, plus `B deploy/checks/test_durable_execution_smoke.py -q`. Require every named scenario true, zero required skips, no sentinel and confirmed cleanup. Run tier 3 once on recorded hardware and record it. Record the exact source tree, dependency artifacts, durations, counters and limitations. Unresolved cleanup or missing evidence is a failure.
- [ ] **Step 5:** Commit Task 13 tests, harness and evidence documents as `test: qualify durable execution across failure, restore and load`; review failure injection and physical observations independently.

**Conventions fixed by Task 13, for the owner's acknowledgement:**

- **Sweeper scheduling (amendment).** No worker called Task 5's scheduler, so an admitted execution with an activity stayed `pending`: nothing reserved its attempt.
  - `ExecutionSweeper(..., scheduler=Scheduler(...))` now runs `schedule_batch` after each pass's items. It works in batches of at most 25, each in its own fenced transaction, until a batch reserves nothing or the pass's limit is reached.
  - A plain read without locks first checks that some attempt waits for a reservation, so an idle sweeper never takes the installation cursor.
  - Each batch waits for the cursor under the worker role's own lock timeout, as any request does. Under the shorter item timeout, a stream of admissions starved the scheduler in the quality review's analysis.
  - A refusal is logged and retried by a later pass.
  - `SweepReport` gains `scheduled_attempts`, defaulting to 0.
- **Runtime test mode.** `lotuspond-runtime activity-worker --activity-config <private file>` serves synthetic providers only in `mode: "test"`: a re-read trust file, a journal directory and up to 8 `fixture-read` or `fixture-model` descriptors.
  - Production and development modes still refuse with `authority_unavailable`.
  - The supervisor exposes `activity_session()`. Every activity request carries the supervisor's current credential (`ActivityClient(..., live=...)`), so an invocation in flight across a rotation renews and reports with the rotated token. A rotation keeps the session, so leases survive it.
  - An idle worker polls again after 0.2 s, backing off to E03's one-second ceiling.
  - A synthetic provider records each call against its attempt: the test-mode child is told the attempt ID. A synthetic read may sleep a log-normal time, for tier 3's injected latency.
- **Harness.**
  - A world is an owned schema plus a private directory of file-backed test authorities: recovery, entitlement and price, tenant policy, deployment trust and catalog, artifact keys and access, activity trust and signing. They live outside the database, so a restore never rewinds them.
  - The API (uvicorn over TLS), the workers and the installed runtimes run as separate process groups. Runtimes reach the API through a TCP proxy that can stall or sever.
  - A restarted API comes back on its earlier port.
  - A worker whose first fenced cycle fails refuses to start, by design. So each worker reports its first completed cycle, and a fixture waits for it before any disruption.
  - Tier 3 sets two test-only timing logs: every statement that locks the scheduler cursor, timed by its process, and every worker cycle's duration.
  - With `LOTUSPOND_CONFORMANCE_PRIVILEGE_LOG` set, each worker records every table it reads, writes or row-locks. Task 14's operator grants come from such a tier 1 run.
- **Clock.** "Database time plus a harness offset" is realised as short fixture durations measured in real database time. No test edits a deadline, lease or expiry column.
- **Setup outside E04, written as labelled rows:**
  - projects and environments;
  - operator provisioning of the cursor and capacity rows;
  - the pool policy and connection for `connector-fixture`, which E03's installed catalog does not know. The connection allows 30 s calls (`DataHandling.timeout_ms`; the default is 10 s).
- **Fault injection.** A test-only `faults.json` arms one commit kind (`admission`, `signal`, `claim`, `result` or `transition`), before or after commit, through global SQLAlchemy session hooks. An orchestrator commit counts as a transition only when it writes execution events, so an idle poll never takes the fault. Concurrent commits decide under a file lock.
  - Before: the commit raises.
  - After: the API replaces its answer with 503, or the orchestrator exits at once.
  - Projection rollback is injected in-process, because runtime-reported events have no route.
- **ENG-4 (owner decision, 2026-10-26).** The least-privilege orchestrator role is created only on tier 2's disposable cluster, and disappears with it.
- **Tier 2 is opt-in.** Without `LOTUSPOND_DISRUPTIVE_EXECUTION_TESTS=1` its cases skip, even in required mode. Once opted in, a missing Docker CLI fails.
- **Deviations.**
  - Spike 14: E04 has no schedule triggers, so an equal retry after a new revision is the replacement case.
  - Spike 8–10: no retry class covers uncertain work, so after reconciliation the step fails and gets no new ordinal. The replacement is a new execution, which runs once on a fresh runtime; the stopped runtime's journal fence keeps it from serving again.
  - Spike 5–7: events record their origin (orchestrator or activity) and the delivery's runtime instance, but not which orchestrator or sweeper made them.
- **Findings** are recorded, with their evidence, in `docs/validation/2026-10-15-durable-execution.md`. Two are red against this plan and open for the owner:
  - RESTORE-1: an activity claimed after the backup runs again after the restore.
  - Tier 3: the installation-wide cursor serializes the engine far below the planning target. **Owner decision, 2026-10-28: diagnose, then decide.** A calibration with three API processes still starved: no process was saturated, yet cursor waits grew and dispatch stopped (finding 11). The bound is the cursor, not the API process, so it stays open as a follow-up redesign of Task 5's serialization, outside E04.
- **Files beyond Task 13's row,** each necessary:
  - backend `execution/sweeper.py` and `execution/engine_contracts.py` (the scheduling amendment), and `tests/execution/test_sweeper.py`;
  - backend `execution/inbox.py`, with `tests/execution/test_inbox.py`: an equal replay of a signal that already took effect returns its receipt (validation finding 13);
  - runtime `cli.py`, `supervisor.py`, `activity_fixture.py`, `activity_client.py` (the live credential), and `activity_child.py` and `activity_worker.py` (the test-mode attempt ID for call counters), with `test_cli.py`, `test_supervisor.py`, `test_activity_client.py` and `test_activity_providers.py`;
  - conformance `world.py`, `processes.py`, `driver.py`, `journey.py`, `disruptive/conftest.py` and the package `__init__.py` files.

## Task 14: Complete reproducible packaging, documentation and final review

**Files:**

- Modify `.github/workflows/ci.yml`, root `package.json`, `deploy/Dockerfile`, `.dockerignore` if needed, `runtime/python/README.md`, `sdk/python/README.md`, `docs/{CAPABILITIES,VALIDATION,AGENT_PRODUCT_RATIONALE}.md` and `docs-site/docs/runtime-foundation.md`.
- Create `deploy/execution/execution-worker.env.example`, `docs/operations/durable-execution.md`, `docs/superpowers/progress/2026-10-15-durable-execution.md` and `scripts/checks/test_execution_packaging.py`.
- Update existing image metadata assertions for API 0.3.0, SDK 0.3.0 and runtime 0.2.0, with exact noneditable origins.

The orchestrator and sweeper run from the API image with the `execution-worker` command. There is no separate orchestrator image or engine server.

**Interfaces:** Root `check` keeps its members. Conformance tier 1 runs inside the existing isolated PostgreSQL job. On 22 October 2026 that job already needed about 29 minutes for 998 tests, so if tier 1 would exceed its budget, give tier 1 its own job instead of silently raising the timeout. Tier 2 runs in a dedicated job with its own disposable PostgreSQL container and exports the Task 13 report format. Tier 3 is a documented manual or scheduled job. No newly green job may conceal skips. Starting workers is an explicit operator action, never a side effect of importing the API.

- [ ] **Step 1:** Add `test_image_dependency_and_command_boundary`, `test_missing_authority_is_not_readiness_success`, `test_required_acceptance_cannot_skip` and `test_owned_cleanup_reports_are_required`. Assertions:
  - the API image imports without any engine client or server;
  - `execution-worker` refuses to start without an explicit role and interpreter configuration;
  - a configured but unqualified provider does not appear healthy or usable;
  - omitting any required scenario fails;
  - packaging cannot select fixture providers in production.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert "temporalio" not in api_installed_distributions
assert worker_without_role.returncode != 0
assert required_report_missing_error.code == "missing_required_evidence"
```

- [ ] **Step 2:** Run `B scripts/checks/test_execution_packaging.py -q`; record red. Keep test selection separate from test-environment availability.
- [ ] **Step 3:** Wire immutable builds and bounded CI jobs, choosing timeouts from measured Task 13 durations rather than deleting tests. `docs/operations/durable-execution.md` documents:
  - topology (API, orchestrator, sweeper and runtimes) and trust boundaries;
  - the orchestrator role's grants and timeouts: `SELECT` on definition tables; `SELECT`, `INSERT` and `UPDATE` on mutable execution tables; `SELECT` and `INSERT` only on events; `DELETE` on nothing;
  - autovacuum settings for frequently updated execution tables, and the thresholds below;
  - explicit operator provisioning of capacity and scheduler-cursor rows (missing setup keeps denying);
  - backups, and the one-store restore procedure with the RecoveryAuthority fence;
  - interpreter coexistence during upgrades;
  - runbooks for orchestrator outage, backlog, lapsed leases, uncertain usage and forward repair;
  - artifact custody and retention.

  Mark each production qualification still open: the actual customer workflow, real providers, signed commercial issuance, E05/E06 effects and outcomes, the agent-first E07 UX, and enterprise, topology, security, capacity and SLO evidence. All goals remain in the completion register; no UI or engine-console parity is implied.

- [ ] **Step 4:** Run:
  - `npm run check`;
  - the required isolated PostgreSQL suite with conformance tier 1, and tier 2;
  - SDK and runtime artifact suites on both Python versions;
  - API image checks, schema parity and strict docs.

  Run an independent whole-branch review and resolve its findings, including the carried `check_release_binding` finding. Then run exact final-head hosted CI once. Record actual failures and skips, artifact hashes and the CI head and base. Broaden or repeat only for changed code or a concrete evidence gap.

- [ ] **Step 5:** Commit Task 14 owned files as `build: package and document the PostgreSQL durable execution foundation`. Publish a draft implementation PR stacked on the approved plan branch, attach it, and give an evidence-backed handoff. Do not merge or claim the whole product production-ready.

**Operational thresholds.** Initial values, recalibrated with the tier 3 results. Act when a threshold is measured, not assumed:

| Signal                                                    | Initial threshold                                     | Action                                                                                                                 |
| --------------------------------------------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `execution_events` rows, or its insert-triggered vacuum   | Over 50 M rows, or vacuum over 1 hour                 | Review partitioning by execution creation month. Converting a populated table is a reviewed, data-preserving migration |
| Execution tables' share of database storage               | Over 30 % or 200 GB                                   | A retention policy must exist before growth continues. It never deletes the events of a retained execution             |
| `executions` rows                                         | Over 100 M                                            | Batch retention; partitioning a foreign-key target needs its own review                                                |
| Projection p95 at 50 events per execution                 | Over 100 ms                                           | Investigate plans; never index event payload columns                                                                   |
| Claim p95 at target load, or execution-table WAL          | Over 50 ms, or over 50 % of measured primary capacity | ADR-0011 revisit trigger                                                                                               |
| Lock wait on `execution_scheduler_cursors` at target load | p95 over 100 ms (proposed)                            | Review Task 5's installation-wide serialization                                                                        |

## Owner-approved addition beyond E04: declining an unverifiable delivery (2026-10-23)

This is not an E04 task. The owner reviewed Task 10's known limitation and decided: "fix outside E04". It lands as its own commit on this branch, with its own specification and quality review, tests and validation entry.

The commit delivers the decline behaviour behind the service boundary: the backend service, the runtime client and the worker's use of it. The HTTP surface, `POST /activities/decline`, comes in Task 12 with the other runtime activity routes. Until then no API serves it.

- **Service.** `LeaseService.decline_activity(db, principal, delivery_id, digest)`.
  - Only the runtime session the delivery was dispatched to may decline it, matching instance, pool and generation, and naming its exact stored digest. So an altered or forged envelope declines nothing.
  - Only while it is pending and unclaimed. A claimed attempt is still reported or reconciled, never declined.
  - The attempt fails exactly as if its delivery had expired now: the same transition and the same failure code, `timeout_before_dispatch`, so each step's declared retry classes apply unchanged; only the event detail says `declined`. That means compute stop confirmed, its reservation released at zero (since 2026-10-26) and the execution due again, so progression may retry it on a new ordinal within the step's bounds.
  - An equal repeat changes nothing; an `activity.decline:<delivery>` request row records it. There is no schema change: the delivery takes the existing `conflict` status, as on expiry.
- **Rules kept.**
  - Authorization: the runtime's current identity, credential, session and recovery epoch are required. As for results, the initiator's current `execute` permission is not, because declining grants nothing. A runtime that never claims achieves the same outcome, only later.
  - Tenancy: scoped records and the pool check.
  - Lock order: the Task 9 order, with the cursor taken `NOWAIT`; routes call `ActivityService.lock_cursor` first.
  - Leases and reconciliation: unchanged.
- **Runtime.**
  - `ActivityClient.decline(delivery)` posts `{delivery_id, delivery_sha256}` to `/activities/decline`, which is added to the control transport's allowlist.
  - The worker declines a delivery only when the refusal is about the delivery itself:
    - it does not parse;
    - its signature is invalid or made with a key its trust revokes;
    - it does not match its pinned records;
    - the record source refuses it as one it will not serve (`forbidden`, `not_found`, `conflict`).

    An agent declines such a child.

  - Local state declines nothing, because every later delivery would fail alike:
    - a trust or authority outage;
    - an expired session;
    - a signer its trust does not know (`untrusted_key`);
    - a recipient mismatch against its own identity;
    - a time-window failure on its own clock.
  - **Safeguards against a broken or hostile runtime.** Before this addition, an unclaimed delivery cost about one attempt per 5-minute expiry; these keep declining from failing a pool's work much faster than that.
    - Circuit breaker: the worker stops declining after 3 declines in a row with no verified delivery in between, and the refusal then stands until a delivery verifies. The count is kept in the invocation journal, so neither a new worker nor a restarted process resets it. It is counted before the decline is sent, so a crash never loses one; an unreadable count stops declining.
    - The control plane accepts at most 6 declines a minute from one runtime instance (`LeaseService.DECLINES_PER_MINUTE`); above that it answers `rate_limited`.
    - Since 2026-10-26 a decline charges nothing, so the budget no longer slows a declining runtime. These two safeguards and the step's `max_attempts` (at most 10 root attempts) bound it instead.
    - **Trade-off for the owner:** a runtime whose trust is stale after a rotation declines nothing; its delivery expires as before, at most 5 minutes. Nothing servable waits behind it, since every delivery to that runtime fails alike.
  - A refused decline leaves the original refusal standing.
- **Obligations for Task 12:**
  - Serve `POST /activities/decline` with the other runtime activity routes, and add the API changelog item in the same change. The route does not exist before then, so a changelog item now would describe behaviour the API does not have.
  - The records route answers a delivery it will not serve with `forbidden`, `not_found` or `conflict`, and an outage with `authority_unavailable`, so the runtime declines only the former.
- **Files:**
  - `execution/leases.py`: `_fail_unstarted` takes the failure code, event detail and observation source;
  - `runtime/.../activity_client.py`, `control_transport.py`, `activity_worker.py` and `agent.py`;
  - tests: backend `test_decline.py`; runtime `test_activity_worker.py`, `test_agent.py`, `test_activity_client.py` and the fake control plane.

## Owner-approved change: release every never-claimed reservation at zero (2026-10-26)

The owner reviewed Task 11's consequence note and decided: "yes, refund those too and proceed". **Owner-confirmed on 2026-10-26.** It lands as its own commit after Task 12, with its own specification and quality review, tests, mutations and validation entry.

- **Rule.** An attempt that no runtime ever claimed has its reservation released at zero once its compute stop is confirmed, whatever ended it: a cancellation by the capacity step (Task 11, already), a child closed when its parent ends (by the parent's result, its reconciliation, or a pause that stops it), a delivery expiry, a reservation retirement or a decline. Released is the schema's `released` state, which counts as no liability in any field, active time included. An expired, retired or declined attempt still fails with `timeout_before_dispatch` and counts toward its step's `max_attempts`; cancellations and closed children end `cancelled`, as before.
- **Kept.**
  - A claimed attempt may have run, so it stays charged as observed, whatever its result.
  - Unknown usage after a mid-call stop stays uncertain liability.
  - After a restore, "unclaimed" in a restored row proves nothing, so the restore fence's uncertain attempts keep their liability, even after reconciliation fails them: `release_unstarted` changes only a `reserved` reservation.
  - Nor is an attempt whose delivery was signed under an older recovery epoch released, even when a pause or its parent's reconciliation ends it before the fence reaches it: `release_unstarted` takes the caller's current epoch and leaves such a reservation charged.
- **Code.**
  - `LeaseService._fail_unstarted`, used for expiry, retirement and declines, confirms the stop and then calls `BudgetService.release_unstarted`, as the capacity step does for cancelled attempts. It no longer settles a zero usage observation first.
  - `dispatch.close_children` (a parent's result or reconciliation) releases each never-claimed child it cancels, after confirming its stop.
  - `release_unstarted(db, attempt_id, epoch)` releases a confirmed stop of a cancelled or failed attempt with no lease row; lease rows are never deleted. Anything else is a `conflict`. A reservation that is not `reserved`, or whose attempt has a delivery signed under another epoch than the caller's current one, is left as it is, and a repeat changes nothing.
- **Bounds without a charge.** A decline used to cost its reserved active time. Now a declining runtime is bounded by:
  - the step's `max_attempts` (at most 10 root attempts);
  - the control plane's 6 declines a minute per runtime instance;
  - the runtime's breaker after 3 declines in a row;
  - released reservations still count toward the execution's `max_steps` (`reserve_attempt`, and the resume check), so "charges nothing" means no tokens, cost or active time.

  Expiry and retirement are bounded by `max_attempts` alike.

- **Residual, open for the owner.**
  - An attempt restored as `reserved` with no delivery carries no epoch, so it is treated as never started, as Task 11's pause and cancellation already treated it, and its retirement releases it too. A restore from before its dispatch cannot show that it ran (the Task 11 restore residual).
  - An old-epoch child closed by its parent's reconciliation, or an old-epoch root released by a pause, before the fence reaches it, keeps its reserved liability but is recorded `cancelled`, not `uncertain`, so it is never reconciled. Its charge is the same reserved bound; only its status is wrong. This status bypass predates this change; Task 13's restore tests should cover it.
- **Superseded.** Task 9's zero usage settlement for these endings, and Task 11's note that they keep their reserved active time.
- **Files:** `execution/leases.py`, `execution/budgets.py`, `execution/dispatch.py`, `execution/progression.py` and `execution/reconciliation.py`; tests `test_leases.py`, `test_decline.py`, `test_activity_races.py`, `test_dispatch.py`, `test_reconciliation.py`, `test_control.py` and `test_recovery.py`.

## Author self-review and scope trace

Completed by the plan author before handoff and repeated for Revision 2; this is not a delegated implementation review. The following table maps each specification section to an implementation or explicit qualification boundary.

| Specification requirement                                 | Owning tasks / evidence                                                                                    |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Customer intent, agent-first UI, remaining product goals  | 12–14; API progress/waits, unchanged E05–E09 register                                                      |
| Engine choice, separate runtime/worker, dependency cost   | 7, 13–14; v15 claim marker, worker roles without new dependencies, conformance tiers and load test         |
| Current authority, deployment-to-connection linkage       | 3, 5, 9, 11–12; post-lock checks, independent recovery/provider denial                                     |
| V2 admission, signatures, exact pins, real assurance      | 1–3; compatibility vectors, closure/FKs, release identity checks                                           |
| Admission, inbox/outbox, duplicate/ordered events         | 2, 6–8, 11, 13; in-transaction outbox consumption, crash-before/after-commit and sequence tests            |
| Typed control flow and bounded read/model agents          | 8–10; all supported region kinds, tools as data, shared budgets                                            |
| Private artifacts/history and current knowledge access    | 4, 8, 10, 13; encrypted values, code-only events, decoded sentinels                                        |
| Entitlement, usage, wait quotas, fairness                 | 5–6, 8–10, 13; race/unknown-use/starvation tests and required denial                                       |
| Retry, pause/cancel, interpreter versioning               | 8–11, 13; original deadlines, interpreter pin and golden progression fixtures                              |
| Restore, schema preservation and forward repair           | 2, 7, 11, 13–14; one-store restore behind the independent recovery fence, populated v13 and v14 migrations |
| Full checks, installed artifacts/images and honest limits | 13–14; required zero-skip integration, final-head CI and validation record                                 |

Self-review corrections (original): the usage wire type has one SDK owner; nested model/tool calls require separate signed child admission; the invocation journal prevents restart from repeating an ambiguous claimed attempt. Revision 2 corrections:

- Engine contracts stay inert in the backend, and no worker module is imported by backend services.
- A pure orchestrator transition never acquires installation, capacity, pool or reservation locks while holding an execution row; capacity changes go through Task 5 services in their own ordered transaction.
- The outbox is consumed in the transaction that applies it.
- Lease lapse keeps the approved uncertain-then-reconcile rule instead of the spike's automatic read retry.
- Spike checks that need consequential writes move to E05's suite instead of being weakened.
- The spike's unproven vocabulary (parallel, iteration, subworkflow, timers) gets its own conformance cases.

Author checks: all five Review Focus cases have named tests; shared types have owners and identical consumers; activity vectors/lifetimes precede integration; plan bodies prescribe interfaces and assertions rather than implementing them; no effect or production entitlement is invented. One integrated plan is appropriate because admission, authority, row-locked progression and runtime leases must work together to produce the approved durable workflow. The first complete customer action-taking workflow still requires the retained downstream stages and customer qualification.

The actual first customer, systems, permitted autonomy, data placement and acceptance owner remain open inputs for later customer/provider/UI qualification. They do not block these synthetic local engineering tasks or authorize scope removal. Owner review of the original plan was completed on 15 October 2026. The owner approved Revision 2 on 22 October 2026. Implementation of Tasks 7–14 has not been requested yet; when it is, the previously selected subagent method applies.
