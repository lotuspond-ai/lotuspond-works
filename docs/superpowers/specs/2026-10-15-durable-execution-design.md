# Durable agent execution foundation (E04)

Date: 15 October 2026 (UTC). Status: proposed written specification for owner
review; implementation and engine adoption are not yet approved. This specification
continues the approved product-completion sequence after E03. It is not a
production-readiness claim or a replacement for the remaining requirements.

Authorities: [approved completion design](2026-10-14-product-completion-design.md),
[executable-v2 specification](2026-10-14-executable-package-v2-design.md),
[connections/runtime identity specification](2026-10-14-connections-fleet-design.md),
[agent needs and UI boundaries](../../AGENT_PRODUCT_RATIONALE.md), and
[completed engine probe](../../plans/2026-10-14-durable-engine-probe.md).
The complete requirement register remains in the completion design.

## Customer intent and stage boundary

A customer needs an agent request to survive interruption, wait for information,
stay within its permissions and budget, and explain its progress. E04 turns the
existing executable package representation into durable, version-pinned work.
Its customer-visible contract is an execution with understandable progress,
waiting reasons, bounded cost and explicit failure/cancellation. Engine queues
remain internal; no new engine console or mandatory queue screen is prescribed.
Studio will consume these contracts through agent goals, instructions, models,
tools, authorized context, evaluation/debugging and oversight. Operations will
show executions, outcomes and actionable waits. Those visible experiences retain
their own E07 design and acceptance gates; this stage supplies their reliable
state and controls rather than prescribing screens from a reference product.

The first complete action-taking journey remains E04 plus E05 actions/human
approval/reconciliation, E06 cases/verified outcomes, early E08 entitlement
enforcement, agent-first Studio/operations and independent customer qualification.
E04 does not label a read result, successful model response or engine completion
as a verified business effect. V1 registry behavior, evaluation Run/EvaluationJob,
SDK v2 bytes and E03 machine/action-command contracts remain preserved.

Order/document intake is still a synthetic reference, not a chosen customer or
industry. The design can be exercised with synthetic providers while the owner
clarifies the actual workflow, systems, autonomy, data placement and acceptance
owner. Those choices determine external qualification and UI priorities; they
do not authorize removing the other approved features.

## Engine choice and operating cost

Recommend Temporal server 1.32.1 and Python SDK 1.34.0, the versions exercised in
the approved probe, behind a narrow adapter for start, signal, cancel, describe
and replay/version compatibility. The completed local probe supports persistent
waits, restart, duplicate workflow IDs, pinned replay and recovery fencing. It
does not establish production HA, remote write semantics, workload capacity or
security configuration. The implementation plan must pin the SDK and server
artifacts, record license/transitive inventories and use a separately owned
local PostgreSQL-backed server for integration qualification.

DBOS remains a credible alternative with fewer separate service components;
it received source/documentation inspection, not equivalent failure testing.
Building an engine from the evaluation worker would transfer responsibility for
timers, history, replay, signals and version evolution to Lotuspond. Temporal is
recommended because its exercised behavior fits the required waits and recovery,
with explicit cost: another stateful service, history/data controls, replay-aware
upgrades, operational staffing and separate release qualification.

The public package and API never accept engine workflow code, queue names or
arbitrary workflow types. A fixed Lotuspond interpreter executes only the
compiled, validated representation. Temporal's determinism sandbox is not a
security boundary; untrusted code is not loaded into it. Namespace and task
queue selection are operator-owned routing, never tenant authorization.

## Authority and component ownership

The API/PostgreSQL authority owns packages, deployment pins, execution identities,
authorized data, current permissions, inbox/outbox, usage reservations and
customer projections. The engine owns durable progression, timers and waits.
An orchestrator service translates committed product work through the narrow
engine adapter. A separately managed runtime owns bounded model/read activities,
actual destination checks and late credential resolution. Customer credentials
never enter the API, engine history, package, model context or event payload.

No authority is inferred from an engine task or previously successful activity.
Every new activity checks the current tenant/environment, deployment, pool,
worker/session/generation, recovery epoch, permissions, policy and applicable
entitlement. The authoritative record source supplies complete deployment-to-
connection and secret linkage; a deployment digest is not a connection digest.
Current revocation wins over historical pinning. Missing providers deny activity.

Keep the engine library outside the pure SDK and existing evaluation runner.
The API can operate for evidence/administration while the engine is unavailable;
admitted work remains visibly pending, subject to explicit backlog limits. The
interpreter and activity dispatcher run in separate supervised services, not
inside API request handlers.

## Additive publication and deployment admission

Add an explicit v2 publication path and separate versioned admission records.
Keep v1 endpoints, canonical bytes, IDs and declaration-only meaning unchanged;
there is no implicit conversion or execution of v1 content. The server performs
complete bounded SDK validation and compilation using the persisted, exact
dependency closure and an operator-owned catalog. Unknown capabilities,
unresolved/mismatched pins and unsupported step kinds are refused at admission.

Store immutable canonical package, plan and dependency/configuration bytes with
their digests and validator/compiler identities. An independently provisioned
publisher trust policy verifies signatures and allowed publisher scope; package
signatures do not authorize deployment. A customer owner/admin explicitly
publishes a deployment revision into a checked environment/pool after applicable
evaluation/release requirements. The API reuses real assurance records without
treating an arbitrary caller-provided evaluation ID as a passing release.

Deployment configuration maps every declared model/tool/context dependency to
exact admitted versioned records and allowed connections. It carries no secret
bytes. Configuration changes create new immutable revisions. Current deployment
enablement/revocation is separate from those historical records. Existing work
stays pinned when a new revision is selected for future admissions.

Owner/admin customer sessions manage deployment publication and activation;
editors may submit work only to explicitly enabled deployments, and viewers
cannot mutate execution. Introduce explicit `execute` and `execution_signal`
API-key scopes rather than interpreting the existing evaluation `run` scope as
business authority. Keys remain bound to their creator's current membership and
role, and cannot activate deployments or perform human decisions. Runtime tokens
authenticate only the separately scoped worker activity/event routes. Platform
administrator status never supplies implicit customer-data authority. New
inventories and details use explicit DTOs, scoped pagination and no-store;
mutations retain the existing origin/content-type/verification/MFA protections.

## Durable identity, transactions and replay

Separate Execution, StepInstance, Attempt and Wait records; E06 later associates
them with a business Case rather than reusing evaluation Run. All customer rows
carry complete scope and composite foreign keys. Pure SDK events validate
structure, but authenticated services reconstruct their context from persisted
records before applying transitions. Caller-supplied context grants nothing.

Execution admission takes a bounded idempotency key and validated typed input.
Under one transaction, authorize, pin deployment/package/configuration/plan/
interpreter version and input identity, reserve applicable admission capacity,
insert the execution and append an engine-start outbox entry. Equal key and
digest returns the same execution after current authorization; changed content
conflicts. An HTTP retry cannot create another business execution.

Outbox workers lease bounded batches using database time. Publishing to Temporal
can occur more than once; a stable opaque engine workflow ID and exact stored
digest resolve the lost-response window. Never mark an outbox entry delivered
before the engine acknowledges its matching identity. Engine queries and API
projections are observations, not permission to perform an activity.

Human/external signals use a separately authenticated, durable inbox with unique
signal identity, exact digest, wait correlation and expiry. Persist before
acknowledging; duplicate equal content is idempotent and conflicting content is
refused. Persisted source sequence and event ID/digest checks reject conflicts
and retain gaps for reconciliation. A gap, late message or duplicate cannot
complete an execution or revive cancelled authority.

## Interpreter semantics and supported activities

Execute the existing compiled structured regions without a second language:
typed transforms, one selected decision branch, parallel all-join, bounded
iteration with stable input order, pinned subworkflow calls, timers, authenticated
external waits, typed human-information waits, connector reads and bounded agents.
Use exactly the SDK's integer/decimal/schema/mapping semantics and bounds. No
eval, imports, shell, filesystem access, ambient clock/randomness or network
exists inside package expressions. Reject unsupported plans before starting.

Read activities and model activities need a distinct, versioned signed activity
envelope because the existing RuntimeCommand is action-intent-specific. Introduce
an additive SDK namespace/frame, never change or reinterpret existing command
bytes. The envelope binds installation/epoch/full scope, execution/step/attempt,
package/plan/configuration/input digests, exact activity kind/dependency pin,
recipient/generation, unique delivery/replay ID, issue and expiry. Use the existing
reviewed Ed25519 primitives with a distinct framing context and independent trust.
The plan must freeze its exact schema, canonical vectors and lifetime bounds
before backend/runtime integration. No arbitrary endpoint, executable or secret
path comes from a model, signal or activity envelope.

Use at most 65,536 bytes for the combined activity envelope/signature, exact
bounded decoding and a five-minute maximum dispatch-command lifetime, matching
the existing authority cadence without changing the action command format.
Command expiry governs the first claim. Running activity attempts also require
a separate server-owned execution lease of at most 60 seconds, renewed only
under current identity/permission, the same attempt and its remaining budget and
deadline. Losing that lease initiates bounded cancellation; a prior claim does
not grant continuing access. Expiry never extends the declared activity deadline.
Late results/unknown usage remain reconciliation evidence, not permission to
resume or repeat work. A separately authorized reconciliation path handles
historical attempts; do not reuse E03 record_status as if it could authorize
closure after command expiry, revoked identity or restoration.

Activity dispatch reloads authoritative records and current E03 identity and
policy, then durably claims once. Runtime verifies the exact signed bytes and
actual request mapping, catalog effect class and destination/secret pins before
execution. A read-named adapter cannot dispatch a write. Model credentials use
the same late worker-local custody boundary as connector credentials. Fixed
synthetic model/read adapters are test-only and production-unselectable; an
unconfigured real provider never returns a successful evaluation or activity.

Bounded agents use pinned instructions/model configuration, typed structured
output, declared read-only tools, authorized context and explicit step/time/
token/cost limits. Model output is data, including proposed tool calls. Validate
each requested tool and actual arguments independently. The agent may propose
an action as typed data; E04 never turns that proposal into effect permission.
Intent, action-review, action-dispatch and outcome-verification nodes remain
explicitly unsupported at E04 deployment admission until E05/E06 supply their
reviewed complete implementation. This retains those goals without a fake path.

## Durable data, history and knowledge boundaries

Keep bounded inputs/results in tenant-authorized artifact records with immutable
content identity, classification, source version, encryption/access policy and
retention metadata. Engine history carries opaque references, digests, fixed
codes and bounded routing metadata rather than prompts, documents or secrets.
Result lookup requires current scope/source access; a content digest is not an
access token. Avoid private values in search attributes, workflow IDs and logs.

The initial artifact interface limits each admitted JSON value to 1 MiB and the
existing SDK depth/item limits. It verifies exact bytes on writes and reads and
binds encryption metadata to tenant/artifact/version identity. Use established
authenticated encryption through a separately configured key provider; missing
keys deny storage/use, and no key is kept in the same artifact row. Database-backed
bounded ciphertext is sufficient for this stage; object-store and large-document
support remain explicit later providers, not implicit unbounded paths.

The implementation plan must test decoded exported engine histories and errors
with sentinel content. Reference minimization does not eliminate sensitive
metadata or replace encryption, access controls, deletion/retention policy and
key custody. A production history backend remains unqualified until those
controls are configured and tested for the chosen topology.

Authorized knowledge references retain source/version, ACL/freshness and
citations. E04 accepts bounded supplied context and fixed synthetic source
fixtures; broad ingestion, connector catalogs, memory and deletion propagation
remain their approved workstream. Deleted/revoked sources cannot be reread just
because an execution was pinned earlier. Replay may use a retained authorized
result only under the documented retention/access contract; new reads reauthorize.

## Budgets, waits and capacity

Enforce the stricter intersection of package, agent, deployment, pool, tenant and
entitlement limits. Reserve capacity and conservative token/cost allowance before
an attempt, then settle by unique execution/step/attempt identity. Every retry,
nested agent/tool call and subworkflow consumes the shared budget. Unknown usage
remains explicitly reserved/uncertain until reconciliation, never measured zero.
Missing price or maximum-charge information prevents cost-bounded dispatch.

Waits release active compute slots while retaining their separate wait/storage
quota and elapsed-business deadline. Persist wait identity and absolute deadline;
restart does not reset it. Use bounded queue batches, per-environment quotas and
fair scheduling so one tenant cannot occupy all available activity capacity.
Show queue age and the real waiting reason, not fabricated progress percentages.
Admission refuses a full bounded backlog rather than dropping work silently.

Initial engineering ceilings are 25 active activities per installation, 5 per
environment, 1,000 pending executions and 10,000 retained waits per environment,
with stricter operator, pool or entitlement values prevailing. They are
configurable downward; raising the qualified ceilings requires explicit capacity
qualification. Per-environment FIFO plus round-robin selection among eligible
environments provides bounded-batch fairness; no unrestricted priority value is
accepted from a caller. Reserve capacity transactionally and release stale
reservations only through a reconciled attempt state. Admission/backlog refusal
uses a fixed retryable capacity reason without inventing a completion time.
Roadmap workload targets remain test hypotheses, not SLO claims. Applicable E08
entitlement checks are mandatory dependencies; their
absence denies new licensed work. E08 signed entitlement/issuance and commercial
terms get their own specification before production activation. Evidence/export,
administration and bounded recovery remain available when new work is refused.

## Retry, pause, cancellation and version changes

Read retries use declared fixed error classes, attempt/deadline/backoff bounds
and shared budgets. Model generation retried after response loss can incur
additional real cost; retain attempt identity and unknown usage. No E03 claimed
action is made reclaimable by the engine or an activity timeout.

Pause and cancellation are durable requests. At the authority transaction
boundary they prevent new dispatch; already dispatched read/model work receives
cooperative cancellation and bounded containment. Report requested versus
observed terminal state. Neither status promises to undo an external effect.
Resume requires current authority and remaining deadlines/budgets. Parallel
failure stops undispatched siblings and retains already received results/errors.

Pin the interpreter implementation independently of package/configuration hashes.
Route existing histories to their compatible implementation; deployment of new
code cannot silently reroute them. Retain old supported code until histories
finish or an explicitly reviewed migration exists. Replay recorded histories
against compatible/incompatible implementations and refuse unsupported versions.
Engine history rollover, if needed, preserves the same execution/step/wait and
deduplication identities and budgets; it cannot create a fresh allowance.

## Recovery and migration

Use the existing independent RecoveryAuthority contract. Restored application
and engine rows cannot establish current epoch, grants, revocations or identity.
Fence before controlled restore; revalidate current identity/permissions and
reconcile state before any resumed activity. No always-allow provider is added
to make production startup appear successful. Real custody/rollback detection
remains a separate qualified integration, not a local database flag.

Add the next reviewed schema revision from exact v13, with strict preflight,
full-scope constraints, caller-transaction migration and version written last.
Preserve every legacy/evaluation/registry/fleet record. No destructive downgrade
or empty-database initializer as upgrade. Engine persistence is separately owned
and versioned; app migration does not silently initialize or reset it. Forward
repair and operator recovery procedures must preserve uncertain records.

## Verification and completion criteria

Require meaningful red/green tests and independent review per implementation
task. Exercise actual PostgreSQL races/rollback, separately installed artifacts,
real API/TLS/runtime boundaries and a disposable persistent Temporal server.
Test complete typed control flow, cross-scope pins, malformed signed activity,
revocation/expiry during lock waits, changed policy, forged tool output, budgets
including retries, authorized context and no-secret/history sentinel controls.

Inject failures before/after app commit, engine start acknowledgement, activity
claim/result, signal persistence and projection. Demonstrate duplicate admission,
signal conflicts/gaps, worker/server/database restarts during waits, pause/cancel
around dispatch, old/new implementation coexistence and incompatible replay.
Restore app and engine snapshots with independently current revocation/epoch
records; stale workers and lost grants must deny. Observe physical fixture calls
and resource cleanup with live positive controls, not hardcoded report counters.

Run required full repository checks, both supported Python versions, installed
artifact/image checks, schema parity, strict docs and exact-head hosted CI.
Record source/engine/dependency/artifact identities, failed attempts and limits.
Local deterministic/model fixtures do not qualify real providers, workload,
customer outcomes, HA, hostile-tenant isolation, RPO/RTO or service availability.

Completion delivers durable bounded agent-execution infrastructure and APIs,
not the full customer product. E05/E06 then add exact authorized effects and
verified outcomes; E07 adds the agent-first builder and operations experience;
E08/E09 and all knowledge/triggers/enterprise/channel/industry/topology goals
remain in the unchanged completion register. The first complete customer
workflow is accepted only after those applicable gates are actually satisfied.

## Review handoff

This is a proposed architectural specification. Owner approval of the written
specification permits detailed implementation planning; plan review and the
already-selected subagent execution method precede product implementation.
No additional merge, deployment, production database access, provider/customer
connection, public distribution or external commitment is requested by it.

Author review checks: the proposed activity contract is additive and does not
reinterpret existing action signatures; execution authorization is distinct from
evaluation scopes; deployment refuses unimplemented effect nodes; active capacity
and waiting quota are separate; history/reference privacy and current source ACLs
are explicit; engine selection and local integration are not production adoption
claims. The detailed plan must turn these boundaries into concrete interfaces,
schema/compatibility fixtures and failure tests before implementation approval.
