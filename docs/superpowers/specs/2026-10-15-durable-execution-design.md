# Durable agent execution foundation (E04)

Date: 15 October 2026 (Asia/Calcutta); revised 22 October 2026. Status: written
specification approved by the owner; the detailed plan and implementation were also
approved on 15 October 2026, and Tasks 1–6 are implemented. **Revision 2**, dated
22 October 2026, applies [ADR-0011](../../architecture/0011-postgresql-durable-execution.md):
the owner confirmed a Lotuspond-owned PostgreSQL state machine instead of Temporal.
The revised sections await the owner's approval together with the revised plan, and
Tasks 7–14 do not start before that approval. The sections are engine choice, component
ownership, outbox and signal delivery, interpreter progression, history, versioning,
recovery and verification. Engine selection authorizes the specified local integration,
not production adoption. This specification continues the approved product-completion
sequence after E03. It is not a production-readiness claim or a replacement for the
remaining requirements.

Revision 2 changes only what depends on the engine. Customer intent, publication and
deployment admission, authority, artifacts, budgets, waits and capacity keep their
approved text, and the schema-v14 records from Tasks 1–6 become the engine's state.
The [revised plan](../plans/2026-10-15-durable-execution.md#temporal-assumptions-in-completed-tasks-16)
lists every element of Tasks 1–6 that assumed Temporal and how each is reused.

Authorities: [approved completion design](2026-10-14-product-completion-design.md),
[executable-v2 specification](2026-10-14-executable-package-v2-design.md),
[connections/runtime identity specification](2026-10-14-connections-fleet-design.md),
[agent needs and UI boundaries](../../AGENT_PRODUCT_RATIONALE.md),
[ADR-0011](../../architecture/0011-postgresql-durable-execution.md) with its
[durable-engine spike](../../plans/2026-10-15-durable-engine-spike.md), and the
[completed engine probe](../../plans/2026-10-14-durable-engine-probe.md), whose engine
recommendation ADR-0011 supersedes.
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

Use a Lotuspond-owned state machine in product PostgreSQL
([ADR-0011](../../architecture/0011-postgresql-durable-execution.md)) behind a narrow
orchestration adapter. Orchestrator workers in the control plane advance executions in
short transactions that hold the execution row lock. A sweeper handles due timers, wait
and execution deadlines, lapsed activity leases and unclaimed deliveries. Runtimes
perform every read and model activity over the signed, leased activity protocol. No new
production dependency, engine server or engine-owned schema is introduced. The
schema-v14 records are the engine's state, and one additive v15 migration adds its claim
marker and sweep indexes.

The local spike showed restart during waits, duplicate admission, fencing,
cancellation and pinned upgrades on both PostgreSQL and Temporal; the probe showed
Temporal's restore and replay behavior. Neither exercised executable-v2's parallel,
iteration, subworkflow or timer regions, workers on separate hosts, failover or load.
Choosing PostgreSQL makes these engine responsibilities Lotuspond's: claim and fencing,
timers, retry backoff, interpreter versioning, event history and its growth, vacuum
and polling load. The conformance suite and load test of the implementation plan
establish their correctness, not the spike.

Temporal server 1.32.1 with Python SDK 1.34.0, the previous recommendation, remains
the named fallback behind the same adapter. DBOS was reviewed from documentation and
not chosen. Any replacement must pass the same conformance suite.

The public package and API never accept engine workflow code, queue names or
arbitrary workflow types. A fixed Lotuspond interpreter executes only the
compiled, validated representation. Orchestrator processes never load customer code.
Worker roles and supported interpreter versions are operator-owned routing, never
tenant authorization.

## Authority and component ownership

The API/PostgreSQL authority owns packages, deployment pins, execution identities,
authorized data, current permissions, inbox/outbox, usage reservations and
customer projections. Orchestrator workers own durable progression, timers and waits
as rows in the same database, behind the narrow engine adapter. They hold no
authority beyond what the persisted records grant. A separately managed runtime owns bounded model/read activities,
actual destination checks and late credential resolution. Customer credentials
never enter the API, execution history, package, model context or event payload.

No authority is inferred from an engine task or previously successful activity.
Every new activity checks the current tenant/environment, deployment, pool,
worker/session/generation, recovery epoch, permissions, policy and applicable
entitlement. The authoritative record source supplies complete deployment-to-
connection and secret linkage; a deployment digest is not a connection digest.
Current revocation wins over historical pinning. Missing providers deny activity.

Keep orchestration code outside the pure SDK and the existing evaluation runner.
The API can operate for evidence/administration while orchestrator workers are stopped;
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
insert the execution, append a start outbox entry and mark the execution runnable. Equal key and
digest returns the same execution after current authorization; changed content
conflicts. An HTTP retry cannot create another business execution.

The outbox is the durable record of each start, signal and control request. Because
the engine shares the database, the outbox's delivery hop collapses. The orchestrator
consumes an execution's pending entries in the same transaction that applies them,
so a crash leaves both unapplied and pending, and no acknowledgement can be lost
between stores. Task 6's bounded two-transaction lease and matching-digest
acknowledgement remain the protocol for external consumers and for a replacement
engine; the PostgreSQL engine does not use them. Engine observations and API
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

Progression is a deterministic function of the pinned compiled plan, the committed
step, attempt, wait and signal rows of the affected region occurrence, and
database time passed in explicitly. Workers keep no execution state between
transactions. One transaction applies the pure transitions available for one execution,
up to a fixed bound. It creates attempts or waits, records encrypted results and events,
and commits. If the bound is reached, the execution stays runnable. Unique
occurrence and attempt-ordinal keys make a repeated transition a no-op.

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
retention metadata. Execution events and orchestration rows carry opaque references,
digests, fixed codes and bounded routing metadata rather than prompts, documents or
secrets. Result lookup requires current scope/source access; a content digest is not
an access token. Avoid private values in identifiers, index keys and logs.

The initial artifact interface limits each admitted JSON value to 1 MiB and the
existing SDK depth/item limits. It verifies exact bytes on writes and reads and
binds encryption metadata to tenant/artifact/version identity. Use established
authenticated encryption through a separately configured key provider; missing
keys deny storage/use, and no key is kept in the same artifact row. Database-backed
bounded ciphertext is sufficient for this stage; object-store and large-document
support remain explicit later providers, not implicit unbounded paths.

The implementation plan must test decoded event artifacts, projections, logs and
errors with sentinel content. Reference minimization does not eliminate sensitive
metadata or replace encryption, access controls, deletion/retention policy and
key custody. Production retention of execution history remains unqualified until
those controls are configured and tested for the chosen topology.

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

Pin the interpreter version independently of package/configuration hashes. A
worker claims only executions whose interpreter version it implements, so deploying
new code cannot silently reroute existing executions. Retain old supported
interpreter versions until their executions finish or an explicitly reviewed
migration exists. Golden progression fixtures recorded under each supported version
must re-derive identically; an incompatible change needs a new version, and
unsupported versions are refused at admission and claim. Execution history is
ordinary rows bounded by the contract's expansion limits. There is no history
rollover, and nothing resets identities, deadlines or budgets.

## Recovery and migration

Use the existing independent RecoveryAuthority contract. Restored rows cannot
establish current epoch, grants, revocations or identity.
Fence before controlled restore; revalidate current identity/permissions and
reconcile state before any resumed activity. No always-allow provider is added
to make production startup appear successful. Real custody/rollback detection
remains a separate qualified integration, not a local database flag.

Add the next reviewed schema revision from exact v13, with strict preflight,
full-scope constraints, caller-transaction migration and version written last
(Task 2, implemented). The engine adds one further additive revision from exact v14:
the orchestration claim marker and sweep indexes. Preserve every
legacy/evaluation/registry/fleet/execution record. No destructive downgrade or
empty-database initializer as upgrade. Engine state is product schema. It evolves
only through reviewed additive migrations in `initialize.py`, and nothing
initializes or resets it when a worker starts. Forward repair and operator recovery
procedures must preserve uncertain records.

A restore returns execution and business records together from one backup. Before
work resumes, orchestration, sweeping and dispatch refuse to proceed until the
independent RecoveryAuthority reports the current epoch. Attempts dispatched or
claimed at backup time become uncertain and reconcile. Waits keep their absolute
deadlines. Admissions and signals accepted after the backup are absent and must be
resubmitted under their original idempotency identities.

## Verification and completion criteria

Require meaningful red/green tests and independent review per implementation
task. Exercise actual PostgreSQL races/rollback, separately installed artifacts,
real API/TLS/runtime boundaries and disposable PostgreSQL clusters owned by the
test tiers.
Test complete typed control flow, cross-scope pins, malformed signed activity,
revocation/expiry during lock waits, changed policy, forged tool output, budgets
including retries, authorized context and no-secret/history sentinel controls.

Inject failures before/after admission commit, orchestrator transition commit,
activity claim/result, signal persistence and projection. Demonstrate duplicate
admission, signal conflicts/gaps, worker and database restarts (including immediate
PostgreSQL restart) during waits, pause/cancel around dispatch, old/new interpreter
coexistence and refusal of unsupported interpreter versions. Restore the database
from an older backup with independently current revocation/epoch records; stale
workers and lost grants must deny. Port the spike checks that apply to E04 and add
cases for parallel, iteration, subworkflow and timer regions, revocation,
disconnected runtimes and tenant isolation, plus a load test at the planning targets. Observe physical fixture calls
and resource cleanup with live positive controls, not hardcoded report counters.

Run required full repository checks, both supported Python versions, installed
artifact/image checks, schema parity, strict docs and exact-head hosted CI.
Record source/interpreter/dependency/artifact identities, failed attempts and limits.
Local deterministic/model fixtures do not qualify real providers, workload,
customer outcomes, HA, hostile-tenant isolation, RPO/RTO or service availability.

Completion delivers durable bounded agent-execution infrastructure and APIs,
not the full customer product. E05/E06 then add exact authorized effects and
verified outcomes; E07 adds the agent-first builder and operations experience;
E08/E09 and all knowledge/triggers/enterprise/channel/industry/topology goals
remain in the unchanged completion register. The first complete customer
workflow is accepted only after those applicable gates are actually satisfied.

## Review handoff

The owner approved this written specification on 15 October 2026. That approval
permitted detailed implementation planning. The owner subsequently approved the
fourteen-task plan and implementation with the already-selected subagent method.
No additional merge, deployment, production database access, provider/customer
connection, public distribution or external commitment is requested by it.

On 22 October 2026 the owner confirmed ADR-0011's PostgreSQL engine. Revision 2 of
this specification and the revised Tasks 7–14 are submitted for the owner's
approval. Until it is given, the Temporal sections they replace are withdrawn and
nothing from either version of Tasks 7–14 is implemented.

Author review checks: the proposed activity contract is additive and does not
reinterpret existing action signatures; execution authorization is distinct from
evaluation scopes; deployment refuses unimplemented effect nodes; active capacity
and waiting quota are separate; history/reference privacy and current source ACLs
are explicit; engine selection and local integration are not production adoption
claims. The detailed plan must turn these boundaries into concrete interfaces,
schema/compatibility fixtures and failure tests before implementation approval.
