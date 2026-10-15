# Durable Agent Execution (E04) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute admitted agent workflows durably, with current authority, bounded usage, private data handling and truthful progress through restart and recovery.

**Architecture:** PostgreSQL owns admission, artifacts, authority, usage and customer projections; a fixed Temporal interpreter schedules opaque work references. Trusted progression activities interpret pinned sources outside engine history, while separately supervised runtime workers perform bounded read/model activities. Existing evaluation, v1 publication and action-command paths retain their contracts.

**Tech Stack:** Python 3.13/3.14, SQLAlchemy/PostgreSQL, FastAPI, existing Pydantic/rfc8785/cryptography contracts, Temporal server 1.32.1 and Python SDK 1.34.0 in a separate orchestrator distribution.

**Spec:** [Approved E04 specification](../specs/2026-10-15-durable-execution-design.md). Read it with this plan. Source baseline: E03 `462ba12d8c9d4933ab0359f8699b6f27f68c3381`. Prepared 15 October 2026 (Asia/Calcutta); implementation awaits owner plan review. The owner's selected subagent execution with independent specification and quality review after each task remains in force.

## Global Constraints

- “Keep v1 endpoints, canonical bytes, IDs and declaration-only meaning unchanged; there is no implicit conversion or execution of v1 content.”
- “Keep the engine library outside the pure SDK and existing evaluation runner.”
- “Current revocation wins over historical pinning. Missing providers deny activity.”
- “Use at most 65,536 bytes for the combined activity envelope/signature” and a “five-minute maximum dispatch-command lifetime”; running leases are “at most 60 seconds.”
- “The initial artifact interface limits each admitted JSON value to 1 MiB and the existing SDK depth/item limits.” Use 1,048,576 bytes, depth 32, 10,000 items.
- “Initial engineering ceilings are 25 active activities per installation, 5 per environment, 1,000 pending executions and 10,000 retained waits per environment.” Only downward configuration is allowed without new qualification.
- “Per-environment FIFO plus round-robin selection among eligible environments” applies; callers cannot select priority, workflow type or task queue.
- “Unknown usage remains explicitly reserved/uncertain until reconciliation, never measured zero.” Existing telemetry subscription levels do not grant execution entitlement.
- “Intent, action-review, action-dispatch and outcome-verification nodes remain explicitly unsupported at E04 deployment admission until E05/E06 supply their reviewed complete implementation.” Human `mode=form` is supported; `mode=review` is refused.
- “No always-allow provider is added to make production startup appear successful.” Fixed synthetic catalogs, model/read adapters, authority and key providers are constructor-injected test fixtures and production-unselectable.
- “No destructive downgrade or empty-database initializer as upgrade.” Advance exact schema v13 to v14, preserve all existing data and keep engine persistence separate.
- “Local deterministic/model fixtures do not qualify real providers, workload, customer outcomes, HA, hostile-tenant isolation, RPO/RTO or service availability.” No merge, customer connection, production database, public distribution or deployment is authorized by this plan.

## Review Focus

1. A process dies after remote acceptance but before acknowledgement: the same identity reconciles without a second execution or fictitious zero usage (Tasks 6, 9, 13).
2. Revocation or expiry occurs while a request waits for a database lock: authority and database wall time are checked after acquiring locks (Tasks 3, 5, 9, 11).
3. A validly signed activity has altered scope, dependency, recipient or actual tool arguments: persisted binding and current authorization still refuse it (Tasks 1, 9, 10).
4. A source is revoked after admission, or a provider includes private payload in an exception: lookup denies and decoded history/errors remain free of sentinels (Tasks 4, 7, 10, 13).
5. A busy environment, nested tool calls or restored timers exhaust resources: shared budgets, fair scheduling and original deadlines survive retries, waits and restore (Tasks 5, 8, 11, 13).

---

## File ownership and task sequence

These tasks implement one integrated execution boundary. Independent future product subsystems retain their own approved specifications; they are not folded into this plan.

| Task | Exact ownership (brace lists expand to individual files)                                                                                                                                                                  | Responsibility                                                            |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 1    | `sdk/python/src/lotuspond_sdk/activity/{__init__,models,canonical,signing,schema}.py`; `contracts/runtime-activity.v1.schema.json`                                                                                        | Additive pure activity wire/signature contract                            |
| 2    | `backend/lotuspond/execution/{__init__,migration}.py`; existing `models.py`, `initialize.py`, `schema_version.py`, `backend/schema.sql`                                                                                   | Durable records and strict v14 migration                                  |
| 3    | `backend/lotuspond/execution/{contracts,errors,authorization,publication,deployments,assurance}.py`                                                                                                                       | V2 publication, pins, release binding and authority                       |
| 4    | `backend/lotuspond/execution/{artifacts,context}.py`                                                                                                                                                                      | Encrypted values and current source access                                |
| 5    | `backend/lotuspond/execution/{entitlement,budgets,scheduler}.py`                                                                                                                                                          | Capacity, shared reservations and fair scheduling                         |
| 6    | `backend/lotuspond/execution/{admission,inbox,outbox}.py`                                                                                                                                                                 | Transactional admission and durable delivery                              |
| 7    | `backend/lotuspond/execution/engine_contracts.py`; `orchestrator/python/{pyproject.toml,uv.lock,README.md}`; `orchestrator/python/src/lotuspond_orchestrator/{__init__,engine,temporal_engine,worker,cli}.py`, `py.typed` | Inert shared engine DTOs and separate engine adapter/service distribution |
| 8    | `backend/lotuspond/execution/{expressions,progression}.py`; `orchestrator/python/src/lotuspond_orchestrator/{workflow,activities}.py`                                                                                     | Typed progression and fixed durable interpreter                           |
| 9    | `backend/lotuspond/execution/{activity_records,dispatch,leases,reconciliation}.py`; `runtime/python/src/lotuspond_runtime/{activity_protocol,activity_client}.py`                                                         | Signed dispatch, continuing authority and historical evidence             |
| 10   | `runtime/python/src/lotuspond_runtime/{activity_worker,activity_invocation,activity_state,agent,providers,context}.py`                                                                                                    | Bounded read/model runtime, invocation journal and tool validation        |
| 11   | `backend/lotuspond/execution/{control,projections,recovery}.py`; `orchestrator/python/src/lotuspond_orchestrator/versions.py`                                                                                             | Pause/cancel/resume, projections, replay and restore fences               |
| 12   | `backend/lotuspond/execution/{routes,runtime_routes,boundary}.py`; existing `app.py`, `schemas.py`                                                                                                                        | Authenticated customer/runtime API wiring                                 |
| 13   | `scripts/checks/{execution-smoke.py,execution_process.py}`; `backend/tests/execution/test_installed_acceptance.py`; `orchestrator/python/tests/integration/`                                                              | Real persistent engine, API/TLS and failure qualification                 |
| 14   | CI, packaging, operations and capability documentation listed in Task 14                                                                                                                                                  | Reproducible delivery and complete evidence                               |

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

Persist exact canonical package/plan/config/dependency bytes, hashes and validator/compiler identifiers. Dependencies reference existing admitted exact v2 publications or admitted catalog records; no request contains a URL to resolve. Store normalized pin/source/connection/secret links with full-scope FKs and verify agreement with canonical bytes on read. Execution pins deployment revision, interpreter ID, opaque engine identity, input artifact, initiating user/key, absolute deadline and admission digest. Step instances key on execution plus canonical occurrence path (region/branch/iteration/subworkflow); attempts have increasing ordinal and immutable input identity. No attempt becomes reclaimable merely because its lease expires. Child attempts additionally have a full-scope parent-attempt FK and unique parent/request ID+digest. A deployment may gain a new immutable revision without invalidating already admitted revisions; global disable/revoke and explicit per-revision disable/revoke override old pins. Activation selects the revision for future work, not a silent migration of existing executions.

Execution states: `pending`, `running`, `waiting`, `paused`, `cancelled`, `succeeded`, `failed`, `reconciliation_required`; requested state is separately `running|paused|cancelled`. Attempt states: `reserved`, `dispatched`, `claimed`, `succeeded`, `failed`, `uncertain`, `cancelled`. Wait states: `open`, `satisfied`, `expired`, `cancelled`. Outbox/inbox use `pending`, `leased`, `delivered`, `conflict`; retries do not delete evidence. `succeeded` is a workflow result, never a verified business outcome. Unknown outcomes cannot be promoted to success by projection.

Task 4 defines `ArtifactPolicy(classification, source_id: str|None, source_version: int|None, retain_until_ms, access_policy_id, key_id)`; `ContextRequest(source_id, version, required_freshness_seconds, allowed_classifications)`; `AuthorizedContext(value: ArtifactHandle, citation: ArtifactHandle)`. All metadata is checked server-side; client-supplied policy IDs grant nothing. `ArtifactKeyProvider.key(scope: Scope, key_id: str) -> bytes` returns a 32-byte AES-GCM key through a bounded independent provider; no key in DB, engine, logs or package. AES-GCM uses fresh 12-byte nonce and AAD binding format `lotuspond.execution-artifact.v1`, full scope, artifact ID/version/digest/classification/source/key identity. Ciphertext-only DB backups still require a separately retained qualified key provider.

Task 5 defines `ExecutionEntitlement.current(scope: Scope, now_ms: int) -> EntitlementGrant` where grant binds scope, installation/epoch, revision, expiry, allowed `execute` and explicit numerical limits. Default raises `entitlement_unavailable`; telemetry `plan_for()` is never consulted as an execution grant. `BudgetDemand(input_tokens, output_tokens, cost_microusd, active_ms)` uses nonnegative safe integers. Task 1 owns the shared pure `UsageObservation(input_tokens: int|None, output_tokens: int|None, cost_microusd: int|None, completed_at_ms: int)` in `activity/models.py`, with explicit unknowns and no authority. Backend and runtime import that same type. `ReservationView(id, attempt_id, state)` and `ScheduledAttempt(attempt_id)` are safe internal outputs. The root execution budget includes every child/retry/tool attempt. A separate record tracks uncertain liability after compute is confirmed stopped. `ChargeAuthority.bound(dependency: ArtifactRef, demand: BudgetDemand) -> BudgetDemand` must provide a current conservative maximum charge, never an optimistic estimate; a missing or unbounded charge refuses dispatch.

### Engine and runtime interfaces (Tasks 7–11)

Task 7 `backend/lotuspond/execution/engine_contracts.py` defines `EngineStart(workflow_id, execution_token, admission_sha256, interpreter_id, deadline_ms)`, `EngineSignal(workflow_id, inbox_token, digest)`, `EngineObservation(workflow_id, admission_sha256, interpreter_id, state)`, `ReplayReport(compatible: bool, code: str)` and `Wakeup(kind: Literal['advance','wait','terminal'], token: str, deadline_ms: int|None)`. This module is inert and has no orchestrator/Temporal import; backend never imports the orchestrator distribution. Tokens are random opaque IDs from persisted records, never tenant/customer names or raw payload. `ExecutionEngine` in orchestrator `engine.py` has async methods: `start(request: EngineStart) -> EngineObservation`, `signal(request: EngineSignal) -> None`, `cancel(workflow_id: str) -> None`, `describe(workflow_id: str) -> EngineObservation`, `replay(history: bytes, interpreter_id: str) -> ReplayReport`. Server address/namespace/queue are operator configuration with verified transport; caller data cannot choose them. `start` refuses same workflow ID with a different admission digest/interpreter.

The workflow receives only `EngineStart`, calls versioned trusted progression activities using tokens, and gets `Wakeup`/opaque references. It **never loads package source, customer input, instructions, context or model output**. Pure expression evaluation and authoritative data lookup run in trusted progression activities outside Temporal history and commit results before returning a token. Branch selection uses only a bounded discriminator and opaque continuation token; fixed engine commands implement timers and parallel waits. Activity retries reload committed state, not re-execute uncommitted model calls. Disable raw SDK exception propagation and payload-bearing logs; safe fixed errors only. A static plan skeleton is not enough to authorize progression.

Task 9 runtime `activity_protocol.py` defines `PinnedActivityRecords(envelope: ActivityEnvelope, connection: ConnectionConfig|None, dependency: ArtifactRef, deadline_ms: int, input_schema: object, output_schema: object, request_sha256: str)`, `ActivityLease(attempt_id, generation, lease_revision, expires_at_ms, deadline_ms)`, `ActivityResult(attempt_id, lease_revision, result: ArtifactHandle|None, code, usage: UsageObservation)`, `ChildActivityRequest(request_id, parent_attempt_id, kind: Literal['connector_read','model'], declaration_name, arguments: ArtifactHandle)`, and `HistoricalObservation(observation_id, attempt_id, delivery_sha256, result: ArtifactHandle|None, code, usage: UsageObservation)`. Historical observations are evidence, not a lease/permission or a way to overwrite an existing result. `ActivityTrustAuthority.current(scope: Scope, now_ms: int) -> TrustSet` uses separately provisioned activity trust, never automatic publisher/action trust reuse. Runtime imports shared SDK types, never backend types.

Task 10 providers have `ReadProvider.invoke(records: PinnedActivityRecords, arguments: object, deadline_ms: int) -> ProviderResult` and `ModelProvider.generate(request: ModelRequest, deadline_ms: int) -> ModelReply`. `ModelRequest` contains pinned model/instructions, validated input/context handles resolved only in worker memory, declared read-tool schemas and remaining per-call limits; `ModelReply(output: object|None, tool_calls: tuple[ToolProposal,...], usage: UsageObservation)`; `ToolProposal(name, arguments: object)`; `ProviderResult(value: object, usage: UsageObservation)`. No model output supplies catalog entries, URLs, secret paths or executable code. Constructor-selected fixed providers have immutable descriptors/destination/price/max-charge records; missing or changed pins deny.

## Verification convention

Run from the implementation worktree root after frozen sync. `B = uv run --frozen --project backend pytest`, `S = uv run --frozen --project sdk/python pytest`, `R = uv run --frozen --project runtime/python pytest`, `O = uv run --frozen --project orchestrator/python pytest` are command prefixes, not additional scripts. Each task's Step 2 runs its stated test command before implementation and records the missing behavior/interface; Step 4 repeats it after implementation and requires PASS. A missing new module is an acceptable initial red, a malformed test or unrelated setup failure is not. Then run the changed distribution's Ruff check. Both independent reviews inspect code and evidence, not merely the implementer's summary.

PostgreSQL tests follow existing guarded `LOTUSPOND_PLATFORM_TEST_DATABASE_URL`, independently verified loopback/database `lotuspond_platform_test`, unique owned schema, separate connections and deterministic barriers. No ambient external database or shared reset. Qualification has zero DB/engine-related skips. Real persistent Temporal is a separately owned local server/database; no time-skipping server as qualification. Every process/container/schema/volume is labeled, individually owned and cleaned with a positive control and observed receipt; retain dumps/reports and failures. Test clocks do not replace DB time in actual race tests.

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
- [ ] **Step 4:** Repeat Step 2; PASS including injected failures around each commit. Use a fake engine only for unit acknowledgement cases; Task 13 supplies the real lost-start-ack test.
- [ ] **Step 5:** Commit Task 6 files as `feat: persist idempotent execution admission and signal delivery`.

## Task 7: Isolate the pinned Temporal adapter and services

**Files:** Ownership-table Task 7 files; `orchestrator/python/tests/{test_engine.py,test_distribution.py,test_history_boundary.py}`; `deploy/execution/engine-lock.json`. Modify backend metadata to version 0.3.0 and create orchestrator 0.1.0 depending on API 0.3.0 and exact `temporalio==1.34.0`; local uv source points at `../../backend`. Backend/runtime/SDK never depend on orchestrator or Temporal. Record actual resolved wheels/hashes/licenses in the lock/inventory.

**Interfaces:** Engine types and `ExecutionEngine` above; `TemporalEngine` implements them. `run_orchestrator(settings: OrchestratorSettings) -> None`, `main(argv: list[str]|None=None) -> int`; settings contain operator-owned TLS/namespace/queue/version configuration and supervised shutdown bounds. CLI subcommands `outbox`, `workflow-worker`, `progression-worker`, `replay`; no arbitrary class/plugin import. Internal services directly call backend services using a DB role/config separate from API customers; engine tasks themselves grant no authority.

- [ ] **Step 1:** Add `test_same_workflow_identity_requires_digest_match`, `test_public_payload_cannot_select_queue`, `test_sdk_and_api_import_without_temporal`, `test_history_payload_contains_only_tokens`, `test_shutdown_stops_owned_workers`. Assertions: conflicting duplicate start refuses; missing TLS outside explicit local fixture denies; customer strings absent from serialized `EngineStart/Wakeup`; installed SDK/API imports work without Temporal; worker shutdown has observed cleanup.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert matching_observation.admission_sha256 == start.admission_sha256
assert private_sentinel not in serialized_engine_payload
assert "temporalio" not in api_installed_distributions
```

- [ ] **Step 2:** Run `O orchestrator/python/tests/test_engine.py orchestrator/python/tests/test_history_boundary.py -q` after bootstrapping project metadata; record behavioral red.
- [ ] **Step 3:** Implement narrow adapter, retry-safe matching start/describe and signal acknowledgements. Pin server archive SHA256 `4260448d650c1ba7ee7683ff0c93d5c6a1709260e3a7ef543bfa34e06cff6051` and local PG image `postgres@sha256:557fea37a744d5f4c8faab304b0a90858b53ab119735a88c131fd19dab802f36` in engine-lock metadata. The lock records origin/version/platform/license as well as digest; additional platform artifacts require their own verified identity. No automatic download/install at API startup. Reuse engine probe lessons, not its simulator as product code.
- [ ] **Step 4:** Repeat Step 2 plus distribution tests on 3.13/3.14; PASS. Install wheel and sdist-rebuilt wheel outside checkout with noneditable SDK/runtime/API dependencies; verify origins, versions, `py.typed`, licenses and no SDK Temporal dependency. Actual server qualification is Task 13.
- [ ] **Step 5:** Commit Task 7 files as `feat: isolate durable execution engine behind pinned adapter`.

## Task 8: Execute typed control flow without exposing values to history

**Files:** Ownership-table Task 8 files; `backend/tests/execution/{test_expressions.py,test_progression.py}`, `orchestrator/python/tests/{test_workflow.py,test_replay.py}`. Test fixtures under `backend/tests/execution/fixtures/control-flow/` cover each admitted SDK step kind.

**Interfaces:** `evaluate_mapping(mapping: object, values: ValueEnvironment, definitions: list[NamedSchema]) -> JsonValue`; `ValueEnvironment(input: JsonValue, steps: dict[str,JsonValue], item: JsonValue|Missing, index: int|None)` with a private nonserializable `Missing` sentinel. `advance_execution(db, execution_token: str, interpreter_id: str) -> Wakeup`; `complete_wait(db, wait_token: str) -> Wakeup`. The workflow class `LotuspondExecutionV1.run(start: EngineStart) -> str` returns only terminal fixed code; versioned progression activity names include interpreter ID `lotuspond.execution-interpreter.v1`.

- [ ] **Step 1:** Add `test_missing_is_not_null_or_false`, `test_decimal_rounding_and_safe_integer_overflow`, `test_structured_regions_and_occurrence_identity`, `test_retry_after_progression_commit_reuses_result`, `test_wait_deadline_does_not_restart`. Assertions: `coalesce(null, fallback)` preserves null while missing selects fallback; `exists` checks presence; half-even decimal `1.005` at scale 2 becomes `1.00`; division by zero/overflow are fixed errors; a decision runs one branch; parallel joins all selected branches; iteration output order matches input despite completion order; duplicate progression does not create another step/attempt.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert decimal_half_even_result == "1.00"
assert integer_inexact_division_error.code == "inexact_division"
assert iteration_result == input_order_expected
assert resumed_wait.deadline_ms == original_deadline_ms
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_expressions.py backend/tests/execution/test_progression.py -q` and `O orchestrator/python/tests/test_workflow.py -q`; record red.
- [ ] **Step 3:** Implement closed expression operations using public SDK parsing/type validation, exact integer/decimal arithmetic and bounded AST evaluation (64 nodes/depth 8). Honor all existing `CallMapping` operations and scale/rounding rules; no `eval`, float decimal conversion or implicit truth coercion. Integer division must be exact; reject nonzero remainder, zero divisor and safe-integer overflow. `coalesce` evaluates its fallback only for missing data; boolean calls evaluate their typed arguments without introducing undeclared presence guards. Execute sources pinned by compiler pointers and hashes; compiled plans contain pointers/digests, not executable expression bodies. Persist transforms/branch selection and encrypted results transactionally before returning tokens. Parallel/iteration/subworkflow share root limits. Timer and external/form waits persist identity/deadline; unsupported policies and effect nodes deny. Workflow uses deterministic engine APIs for timers/waits and never decrypts values.
- [ ] **Step 4:** Repeat Step 2 plus `O orchestrator/python/tests/test_replay.py -q`; PASS for empty iterations, nested scopes, optional fields, finite numeric boundaries and duplicate activity delivery. Use deterministic unit replay here; Task 13 exports actual server histories.
- [ ] **Step 5:** Commit Task 8 files as `feat: interpret pinned workflow regions with private durable progression`.

## Task 9: Bind activity dispatch, leases and historical reconciliation

**Files:** Ownership-table Task 9 files; `backend/tests/execution/{test_dispatch.py,test_leases.py,test_reconciliation.py}`, `runtime/python/tests/test_activity_client.py`. Introduce runtime 0.2.0 metadata and lock; update backend requirement and orchestrator lock, keeping E03 command behavior unchanged. Use the Task 1 shared usage type unchanged.

**Interfaces:** `ActivityRecordSource.load(db, attempt_id: str) -> PinnedActivityRecords`; `dispatch_activity(db, attempt_id: str, recipient: RuntimeIdentity) -> ActivityDelivery`; `claim_activity(db, identity: RuntimeIdentity, delivery_id: str, digest: str) -> ActivityLease`; `renew_activity(db, identity, lease: ActivityLease) -> ActivityLease`; `request_child_activity(db, identity, request: ChildActivityRequest) -> str` returns the persisted child-attempt ID; `record_activity_result(db, identity, result: ActivityResult) -> None`; `reconcile_activity(db, actor: ExecutionActor, observation: HistoricalObservation) -> None`. `ActivityClient.poll() -> ActivityDelivery|None`, `.claim(delivery) -> ActivityLease`, `.renew(lease) -> ActivityLease`, `.request_child(request) -> str`, `.result(result) -> None` use existing verified direct control transport and runtime session credentials, never evaluation keys.

- [ ] **Step 1:** Add `test_each_persisted_pin_and_actual_mapping_is_required`, `test_claim_once_with_lost_ack`, `test_expiry_and_revoke_during_lock_wait`, `test_lease_renewal_cannot_extend_attempt_deadline`, `test_historical_evidence_requires_separate_authority`, `test_child_request_requires_declaration_and_actual_arguments`. Assertions: deployment digest substituted for connection digest denies; valid signature/wrong instance or generation denies; equal claim returns the same live lease only to the same current identity, never a second invocation; expiry+1/revoked identity cannot renew; max lease 60000ms and <=remaining deadline; old result remains uncertain and cannot resurrect activity.

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

## Task 11: Preserve controls and authority through replay and restore

**Files:** Ownership-table Task 11 files; `backend/tests/execution/{test_control.py,test_projections.py,test_recovery.py}`, `orchestrator/python/tests/test_versions.py`.

**Interfaces:** `request_control(db, actor: ExecutionActor, execution_id: str, request: ControlRequest) -> ExecutionView`; `apply_event(db, source_identity: str, event: object) -> ExecutionView`; `reconcile_execution(db, actor, execution_id: str) -> ExecutionView`; `require_interpreter(interpreter_id: str) -> str` returns only a registered versioned workflow/activity route. `apply_event` validates the existing SDK event shape where applicable and reconstructs context from DB; supplemental control events use a separate versioned closed model, not a change to SDK event bytes.

- [ ] **Step 1:** Add `test_cancel_serializes_before_new_dispatch`, `test_requested_state_is_not_observed_terminal`, `test_event_gap_and_digest_conflict_do_not_complete`, `test_restore_cannot_restore_epoch_or_grant`, `test_old_history_requires_old_interpreter`. Assertions: cancellation committed before dispatch produces zero later provider calls; pause/resume cannot reset deadline/budget; gap leaves projection pending and duplicate equal event is no-op; conflicting source sequence fences execution; old epoch denies even after both DBs restored; incompatible replay refuses, compatible old route continues.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert calls_after_cancel_commit == calls_at_cancel_commit
assert after_gap.state != "succeeded"
assert restored_old_epoch_error.code == "authority_unavailable"
assert after_rollover_budget == before_rollover_budget
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_control.py backend/tests/execution/test_projections.py backend/tests/execution/test_recovery.py -q` and `O orchestrator/python/tests/test_versions.py -q`; record red.
- [ ] **Step 3:** Persist control plus outbox under execution lock; new dispatch checks requested state in the same serialization boundary. Cancel outstanding timers/waits only after authority records commit; observed terminal waits for containment/reconciliation. Parallel failure stops undispatched siblings, retains completed/uncertain work. Persist event identity+digest+source sequence; buffer at most 100 pending gaps per execution and then fence for reconciliation, never infer completion. Keep compatible interpreter code installed until histories finish; use versioned workflow/activity routing. At 1000 recorded progression completions, continue-as-new after draining the workflow's handlers, carrying the opaque execution token and original identities/limits; pending timer deadlines and inbox messages remain authoritative in DB and are reattached without reset. Test rollover with an active wait, duplicate signal and unchanged budget. Independent RecoveryAuthority fences restore and every resumed activity; no application snapshot provides current epoch or revocation authority.
- [ ] **Step 4:** Repeat Step 2; PASS including gaps beyond the bound (fixed reconciliation reason), repeated controls, cancellation during lock wait and lost grants during resume. Task 13 repeats with actual engine/server/DB restart and snapshot restoration.
- [ ] **Step 5:** Commit Task 11 files as `feat: preserve execution controls and recovery fences across replay`.

## Task 12: Expose explicit execution APIs with current authorization

**Files:** Ownership-table Task 12 files; `backend/tests/execution/{test_routes.py,test_runtime_routes.py,test_boundary.py}`. Modify `schemas.py` to add `execute`, `execution_signal` API-key scopes; do not reinterpret `run`. Add scoped changelog entry in `app.py` only for implemented capability.

**Interfaces:** Prefix `M=/api/v1/projects/{project_id}/environments/{environment_id}`; workspace only from authenticated context. `POST/GET M/executable-packages`, `GET M/executable-packages/{id}` and `/content`; `POST/GET M/deployments`, `GET M/deployments/{id}`, `POST M/deployments/{id}/status`, `POST M/deployments/{id}/revisions/{revision_id}/status`; `POST/GET M/executions`, `GET M/executions/{id}`, `GET M/executions/{id}/events`, `GET M/executions/{id}/waits`, `POST M/executions/{id}/signals`, `/control`, `/reconciliation`; `POST M/context-sources` and `POST M/context-sources/{id}/versions`, `/revoke`; `GET M/artifacts/{id}/versions/{version}`. Runtime prefix `/api/v1/runtime/activities`: `POST /poll`, `/{delivery_id}/claim`, `/leases/renew`, `/children`, `/results`; separately scoped worker artifact/context reads and result uploads under `/api/v1/runtime/activity-artifacts`. Worker gets only artifacts linked to its current claimed attempt, never a generic artifact bearer capability.

- [ ] **Step 1:** Add `test_route_role_scope_matrix`, `test_runtime_credentials_cannot_enter_customer_routes`, `test_human_form_rejects_api_key`, `test_headers_body_and_pagination_boundaries`, `test_engine_outage_keeps_evidence_available`. Assertions: owner/admin sessions manage/publish; editors execute enabled work and control their own executions, owner/admin control any scoped execution; viewers read only; keys require explicit scope and current creator role and cannot manage/human-form/reconcile; no implicit platform-admin access. Duplicated auth/session/workspace headers and cross-scope IDs refuse; absent engine does not block read/export/admin.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert evaluation_run_scope_response.status_code == 403
assert human_form_key_response.status_code == 403
assert evidence_during_engine_outage.status_code == 200
assert execution_detail.headers["cache-control"] == "no-store"
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_routes.py backend/tests/execution/test_runtime_routes.py backend/tests/execution/test_boundary.py -q`; record red.
- [ ] **Step 3:** Wire services through explicit request/response DTOs, no ORM serialization. Preserve verification/MFA/session Origin/content-type protections. Mutation idempotency key is required except explicit signal/control identity protocols. Keyset pagination `after_id`, limit default 50/max100, no full-table counts. Use no-store and fixed HTTP errors: 400 invalid, 401 unauthenticated, 403 forbidden, 404 scoped missing, 409 conflict, 413 limit, 429 capacity, 503 unavailable dependency. Default body max1MiB; activity delivery max131072; actual decoded value/envelope limits still apply. Add audit with fixed identifiers/state only. Runtime output upload verifies live lease and output schema; late data uses the separate historical path.
- [ ] **Step 4:** Repeat Step 2 and existing identity/registry/fleet route tests; PASS with production-deny startup defaults. OpenAPI names distinguish evaluation runs, production executions and waits; no endpoint implies verified effects or unimplemented Studio screens.
- [ ] **Step 5:** Commit Task 12 files as `feat: expose scoped durable execution and runtime activity APIs`.

## Task 13: Qualify actual restart, privacy and recovery behavior

**Files:** Ownership-table Task 13 files; integration tests `orchestrator/python/tests/integration/{conftest.py,test_restart.py,test_delivery.py,test_privacy.py,test_restore.py,test_versions.py}`; `deploy/checks/test_durable_execution_smoke.py`. Create `docs/validation/2026-10-15-durable-execution.md` recording failures and observed limits.

**Interfaces:** `execution-smoke.py --output <private-directory>` produces versioned `execution-report.json`, source/distribution/engine manifests, decoded-history privacy result and resource cleanup receipts. `execution_process.py` is the independent outer supervisor, with bounded timeout and owned descendant/container/volume/schema census. Required environment flag `LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS=1` turns missing server/DB/artifact prerequisites into failure, not skip.

- [ ] **Step 1:** Add real acceptance tests that assert independently installed API/runtime/SDK/orchestrator origins; actual TLS authentication; synthetic reference workflow result and measured read/model call counts; rollback injection at admission/start-ack/claim/result/signal/projection boundaries; duplicate identities/conflicts; server/worker/DB restart while waiting; replay with old/new workers. `test_decoded_histories_and_errors_exclude_payloads` inserts unique private sentinels in prompts, documents, credentials, source IDs and provider errors, exports/decode all histories and failure payloads, and asserts sentinel absence. `test_restore_with_independent_revocation` restores app and engine snapshots while keeping current authority outside both restores; assert stale workers make zero observed new fixture calls and unknown usage stays reserved.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert required_integration_skips == 0
assert all(item["passed"] for item in observed_scenarios)
assert all(sentinel not in decoded_history_and_errors for sentinel in private_sentinels)
assert cleanup_receipt["confirmed"] is True
```

- [ ] **Step 2:** Run `B backend/tests/execution/test_installed_acceptance.py -q` and `O orchestrator/python/tests/integration -q` with required flags and owned local services; record any missing behavior as red. No test-only time-skipping engine as the passing result.
- [ ] **Step 3:** Build the owned harness around actual server1.32.1/SDK1.34.0 and persistent separate PG. Install wheel and sdist-rebuilt wheel in isolated environments; start real API, workflow/progression workers and runtime as separate processes. Measure queue/call/history/resource/cleanup counts from observed records, inspect failed phases, and retain safe diagnostics. Add positive controls before each denial assertion so “nothing ran” cannot pass because the service was dead. Keep independent authority/effect counters outside restored snapshots; never replace the product with the old probe simulator.
- [ ] **Step 4:** Repeat Step 2 plus `B deploy/checks/test_durable_execution_smoke.py -q`; require all named scenarios true, zero required integration skips, no sentinel and confirmed cleanup. Record exact source tree, dependency/server artifacts, durations, actual counters and limitations; unresolved cleanup or missing evidence is a failure.
- [ ] **Step 5:** Commit Task 13 tests/harness/evidence docs as `test: qualify durable execution across failure and recovery boundaries`; review failure injection and physical observations independently.

## Task 14: Complete reproducible packaging, documentation and final review

**Files:** Modify `.github/workflows/ci.yml`, root `package.json`, `deploy/Dockerfile`, `.dockerignore` if needed, `runtime/python/README.md`, `sdk/python/README.md`, `docs/{CAPABILITIES,VALIDATION,AGENT_PRODUCT_RATIONALE}.md`, `docs-site/docs/runtime-foundation.md`; create `deploy/execution/{orchestrator.Dockerfile,orchestrator.env.example}`, `docs/operations/durable-execution.md`, `docs/superpowers/progress/2026-10-15-durable-execution.md`, and `scripts/checks/test_execution_packaging.py`. Update existing image metadata assertions for API0.3.0/SDK0.3.0/runtime0.2.0 and exact noneditable origins; orchestrator0.1.0 alone carries Temporal.

**Interfaces:** Root `check:orchestrator` performs frozen sync, Ruff and non-integration pytest; append to `check`. Dedicated CI Python3.13/3.14 artifact checks and required owned persistent-engine acceptance job export the same report format as Task 13. No newly green job may conceal skips. Engine service startup is explicit operator action, never API import side effect.

- [ ] **Step 1:** Add `test_image_dependency_and_command_boundary`, `test_missing_authority_is_not_readiness_success`, `test_required_acceptance_cannot_skip`, `test_owned_cleanup_reports_are_required`. Assertions: API image imports without Temporal; orchestrator contains exact pinned SDK; configured-but-unqualified provider does not appear healthy/usable; omission of any required scenario fails; packaging cannot choose fixture providers in production.

Core assertions for the named Step 1 tests (variables come from that test’s fixture/observations):

```python
assert api_temporal_dependency is False
assert orchestrator_temporal_version == "1.34.0"
assert required_report_missing_error.code == "missing_required_evidence"
```

- [ ] **Step 2:** Run `B scripts/checks/test_execution_packaging.py -q`; record red. Keep test selection separate from test environment availability.
- [ ] **Step 3:** Wire immutable artifact builds/images and bounded CI jobs; choose timeout from measured Task 13 durations, not silent test deletion. Document topology, DB roles/TLS, artifact/history custody and retention, backups/independent recovery fence, upgrade/replay coexistence, outage/backlog/unknown-usage runbooks and forward repair. Mark each production qualification still open: actual customer workflow, real providers, signed commercial issuance, E05/E06 effects/outcomes, agent-first E07 UX, enterprise/topology/security/capacity/SLO evidence. All goals remain in the completion register; no UI or engine console parity is implied.
- [ ] **Step 4:** Run `npm run check`, required isolated PostgreSQL suite, both Python-version SDK/runtime/orchestrator artifact suites, persistent-engine acceptance, API/orchestrator image checks, schema parity and strict docs. Run independent whole-branch review and resolve findings, then exact final-head hosted CI once. Record actual failures/skips, artifact hashes and CI head/base; broaden/repeat only for changed code or a concrete evidence gap. E03 already-green source checks need no repetition during planning, but the final E04 integrated change requires this full pass.
- [ ] **Step 5:** Commit Task 14 owned files as `build: package and document qualified durable execution foundation`; publish a draft implementation PR stacked on this approved plan branch, attach it, and give an evidence-backed handoff. Do not merge or claim the whole product production-ready.

## Author self-review and scope trace

Completed by the plan author before handoff; this is not a delegated implementation review. The following table maps each specification section to an implementation or explicit qualification boundary.

| Specification requirement                                 | Owning tasks / evidence                                                    |
| --------------------------------------------------------- | -------------------------------------------------------------------------- |
| Customer intent, agent-first UI, remaining product goals  | 12–14; API progress/waits, unchanged E05–E09 register                      |
| Engine choice, separate runtime/worker, dependency cost   | 7, 13–14; exact artifacts, installed isolation, persistent real server     |
| Current authority, deployment-to-connection linkage       | 3, 5, 9, 11–12; post-lock checks, independent recovery/provider denial     |
| V2 admission, signatures, exact pins, real assurance      | 1–3; compatibility vectors, closure/FKs, release identity checks           |
| Admission, inbox/outbox, duplicate/ordered events         | 2, 6–7, 11, 13; lost acknowledgement and sequence tests                    |
| Typed control flow and bounded read/model agents          | 8–10; all supported region kinds, tools as data, shared budgets            |
| Private artifacts/history and current knowledge access    | 4, 7–8, 10, 13; encrypted values, opaque engine tokens, decoded sentinels  |
| Entitlement, usage, wait quotas, fairness                 | 5–6, 8–10, 13; race/unknown-use/starvation tests and required denial       |
| Retry, pause/cancel, version routing/rollover             | 8–11, 13; original deadlines, compatible/incompatible replay               |
| Restore, schema preservation and forward repair           | 2, 11, 13–14; independent authority and populated v13 migration            |
| Full checks, installed artifacts/images and honest limits | 13–14; required zero-skip integration, final-head CI and validation record |

Self-review corrections: shared engine DTOs live in the backend without a reverse orchestrator dependency; the usage wire type has one SDK owner; nested model/tool calls require separate signed child admission; the invocation journal prevents restart from repeating an ambiguous claimed attempt.

Author checks: all five Review Focus cases have named tests; shared types have owners and identical consumers; activity vectors/lifetimes precede integration; plan bodies prescribe interfaces and assertions rather than implementing them; no effect or production entitlement is invented. One integrated plan is appropriate because admission, authority, engine progression and runtime leases must work together to produce the approved durable workflow. The first complete customer action-taking workflow still requires the retained downstream stages and customer qualification.

The actual first customer, systems, permitted autonomy, data placement and acceptance owner remain open inputs for later customer/provider/UI qualification. They do not block these synthetic local engineering tasks or authorize scope removal. Owner plan review is the next gate; the previously selected subagent method is preserved.
