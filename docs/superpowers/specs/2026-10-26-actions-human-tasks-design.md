# Actions and human tasks (E05)

Date: 26 October 2026 (Asia/Kolkata). Status: **written specification approved by the owner on 29 October 2026, with decisions 1 and 13 restated; not implemented.** It continues the approved product-completion sequence after [E04](2026-10-15-durable-execution-design.md) (Revision 2) and is reviewed together with its [implementation plan](../plans/2026-10-26-actions-human-tasks.md), which records the owner's decisions. Nothing described here exists in the product: E04 publication and deployment admission still refuse every node this document covers, and the console's Human tasks page remains Planned. This is not a production-readiness claim. Approval would authorize only the local, synthetic implementation the plan describes.

Authorities: the [approved completion design](2026-10-14-product-completion-design.md) (rows "E05 actions and human tasks" and "Recovery authority"), the [delivery plan](../../plans/2026-10-13-platform-delivery.md#dependency-ordered-epics) (the E05 gate), the [executable-v2 specification](2026-10-14-executable-package-v2-design.md), the [connections and runtime identity specification](2026-10-14-connections-fleet-design.md), the [E04 specification](2026-10-15-durable-execution-design.md) and [plan](../plans/2026-10-15-durable-execution.md) (Revision 2), [ADR-0011](../../architecture/0011-postgresql-durable-execution.md), the [durable-engine spike](../../plans/2026-10-15-durable-engine-spike.md) and [agent needs and UI boundaries](../../AGENT_PRODUCT_RATIONALE.md). The complete requirement register remains in the completion design.

## Customer intent and stage boundary

A customer lets an agent propose a consequential change, such as submitting an order or updating a record, only if four things hold. The change is exactly what a permitted person reviewed. It is still permitted when it is sent. It happens at most once. And it is checked afterwards. When a send times out, the customer needs to know whether the change happened before anything is resent.

E05 supplies those guarantees for executable-v2's effect steps on top of E04's durable execution:

- action intent;
- human review, that is `human_task` with `mode=review`;
- action dispatch;
- the outcome verification that the compiler requires for every dispatch.

It also turns E04's admin-only form waits into assignable human tasks with claim, expiry and escalation.

The delivery-plan gate is "Parameter changes/expiry invalidate approval; denied action cannot execute; uncertain effects reconcile". The completion design's E05 row adds durable intent before effects, exact binding, independent reviewer assignment, claim, expiry and escalation, fresh authority before dispatch, idempotency, receipts, ambiguous-effect reconciliation and explicitly authorized compensation. Its recovery-authority row requires three things: disconnection blocks new consequential effects; revoked or stale workers are fenced; and after a restore, writes resume only after a new recovery epoch, downstream reconciliation and current identity and grant validation.

Outside E05:

- Case records, business outcome reporting, exception ownership and traceability export belong to E06.
- A task inbox, a review screen and Studio authoring of effect steps belong to E07. E05 supplies their state and APIs.
- Signed entitlements and commercial terms belong to E08. E05 consumes E04's entitlement dependency, which denies by default.
- Real connectors, providers, customer systems, notification delivery and production qualification belong to E09, E10a and customer acceptance.

Compensation is in the completion row, but the v2 contract does not define when it runs. The plan's owner decision 14 recommends refusing packages that declare it until a follow-up specification settles that.

No real model or tool provider exists. Every effect in E05 is against a synthetic target, and E05 claims no live-provider evidence.

## Starting point

As read on 29 October 2026:

- **E04 on `main`.** Tasks 1–14, merged as PR #17 (`533b3d6`): schema v15, the orchestrator and sweeper, interpreter v1, read and model activities, controls, the recovery fence, the customer and runtime APIs, the conformance harness and packaging.
- **The finding 11 redesign, approved and not merged.** It is on `claude/e04-cursor-redesign`, with its proposal in `docs/superpowers/plans/2026-10-29-installation-capacity-lock.md`. The installation cursor moves from the head of E04's lock order to its end. It is taken only by a transaction that takes a compute slot, after every lookup and lock. Provider lookups wait for their slot within their 5 s bound instead of refusing at once.

E05 builds on conventions that E04 on `main` implements:

- Publication and deployment refuse `action_intent`, `action_dispatch`, `outcome_verification`, `human_task` with `mode=review` (`unsupported_step`) and every escalation policy (`unsupported_policy`). The only accepted policy is `workspace_admins` assignment with `approval: human` and independent review.
- Owners and admins signal form waits through `POST …/executions/{id}/signals`.
- An attempt's failure is an encrypted `{"failure": code, "failed_at_ms": ms}` record. Root attempts use ordinals 1–10; an agent's children use ordinals above 10.
- A lapsed read or model lease makes its attempt uncertain and the execution `reconciliation_required` until an owner or admin reconciles it.
- A terminal state waits for containment: no attempt may still be running and no reservation may be unstopped.
- After a restore, every execution admitted under an older epoch that has not ended is held (owner decision, 2026-10-28). None of its attempts reaches a runtime until an owner releases it with `POST …/executions/{id}/reconciliation`. A release is refused while an attempt delivered under the older epoch has no confirmed stop.
- Runtime-reported events have no route. `apply_event` refuses action-review and outcome kinds.

E03 supplies what actions were designed around, none of which has a caller yet:

- signed `RuntimeCommand` admission with durable replay identity (`RuntimeCommandAdmission`);
- the `PinnedRecordSource` and `BusinessDispatchAuthority` hooks, which deny by default;
- the mounted secret resolver, `DestinationTransport` and the independent `RecoveryAuthority`.

The SDK supplies `ActionBinding`, `ActionIntent`, `HumanDecision`, `RuntimeCommand`, `ActionReceipt`, `OutcomeRecord`, the reconciliation payload and pure binding and transition checks, none of which grants authority. The compiler already requires two things: every write operation declares `read_back` reconciliation, and every dispatch on every path is covered by outcome verification (`verification_missing`).

E05's implementation starts from `main` after the finding 11 redesign merges, because the redesign changes the lock order in the files E05 extends (owner decision 1, restated 29 October 2026).

## Approach and alternatives

Recommended: implement actions as further row-locked transitions of the E04 engine, and deliver writes through E03's signed command path. Intents, tasks, decisions, commands, receipts, read-back observations and outcomes are rows in product PostgreSQL. They commit in the same transactions as the execution state that depends on them. This is ADR-0011's first reason for PostgreSQL: approval binding and action intent commit with the business records, and one restore covers them together.

Alternatives considered:

- **A separate action service with its own queue.** It would duplicate E04's claim, sweep and fencing, and split state that the restore fence must treat as one. Rejected.
- **Writes as a new kind in E04's activity envelope (`lotuspond.runtime-activity.v1`).** It would reinterpret an envelope that exists for reads and models, and bypass the command contract that the v2 specification fixed for consequential effects (`lotuspond-runtime-command-v1`). Rejected.
- **Receipts as generic runtime events through `apply_event`.** E04 refuses action kinds there and serves no route. Receipts need live-lease, journal and evidence checks that a generic event path lacks. Rejected in favour of dedicated runtime action routes.
- **Temporal.** Not reopened. E05 meets none of ADR-0011's revisit triggers.

## Authority and component ownership

The API and PostgreSQL authority own intents, tasks, decisions, commands, receipts, observations and outcomes, alongside E04's records. Orchestrator workers create intents, open review tasks, consume decisions and schedule dispatch, read-back and verification attempts as pure transitions under the execution row lock. They sign nothing and call no provider. The API process signs commands at runtime poll time, as E04 signs activity deliveries. Runtimes perform every write and every read-back over authenticated runtime routes, with their E03 identity. Customer credentials stay in the runtime, as in E03 and E04.

No authority is inferred from an earlier step. Each of these checks current authority again:

- **Intent creation** checks the pinned records and the current epoch.
- **A decision** checks the reviewer's current role, independence, claim and access to the proposal.
- **Dispatch** checks the current intent and decision, the initiator's `execute` permission, the deployment revision, the connection and pool policy, entitlement, price authority, the recovery epoch and the requested state.
- **Each command claim, renewal and receipt** checks the runtime's current identity, session, generation and epoch.

Current revocation wins over historical pinning, and a missing provider denies.

Model output is data. An agent can produce values that an `action_intent` step maps into its target and input, but only the compiled plan decides that an intent exists. No model, tool, document, signal or runtime chooses a connector, operation, connection, policy, reviewer or idempotency key.

## Admitting effect packages

E05 removes E04's refusal of effect steps under these rules, applied at publication, deployment and admission:

- **Interpreter.** Plans that use any E05 feature pin a new interpreter, `lotuspond.execution-interpreter.v2`. E05 features are effect steps, review tasks, and form tasks with E05 assignment or escalation. All other plans keep v1, whose golden fixtures must still re-derive unchanged. Workers claim only the versions they implement, so a rolling upgrade never moves an execution between semantics (owner decision 15).
- **Policies.** Assignment is a closed, operator-owned vocabulary: `workspace_owners`, `workspace_admins` and `workspace_editors` (owner decision 4). A review policy's `escalation` is `null` or `after_half_timeout`. It must agree with the step's `escalation_policy_ref`: both present or both absent (owner decision 5). Any other value is `unsupported_policy`. Policies with `approval: none` are admitted as declared (owner decision 3), and deployment detail lists the operations that would dispatch without review.
- **Connections.** Every write's connector declaration must be bound, in the deployment configuration, to a connection version whose connector artifact digest equals the catalog's `artifact_sha256`, and which allows the operation and its reconciliation read. A mismatch is `dependency_unavailable`.
- **Reconciliation input.** The reconciliation read is called with the intent's exact target and input. Deployment refuses a write whose read operation's target and input schemas do not accept those values' schemas (`unsupported_operation`, owner decision 18).
- **Compensation.** A dispatch step that declares `compensation` is refused with `unsupported_step` (owner decision 14).

Publication by editors and `write` keys, and deployment by owners and admins, keep E04's rules. A signed package or an admitted deployment grants nothing by itself: every intent, decision and dispatch is checked again.

## Action intents

An `action_intent` step creates one intent per occurrence, in the orchestrator transaction that evaluates its mappings. Its target and input values are stored as encrypted workspace-policy artifacts. Its `ActionBinding` is derived only from persisted records, as follows:

| Binding field                                            | Source                                                                                     |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `installation_id`, `recovery_epoch`                      | The installation, and the epoch the engine fenced before claiming the execution            |
| `workspace_id`, `project_id`, `environment_id`           | The execution row                                                                          |
| `execution_id`, `step_id`, `action_id`                   | The execution; the authored step ID; a new UUID unique per execution and occurrence        |
| `package_sha256`, `plan_sha256`, `configuration_sha256`  | The pinned publication and deployment revision                                             |
| `connector_name`, `connector_version`, `operation`       | The package's connector declaration and the step                                           |
| `connector_artifact_sha256`                              | The catalog connector's `artifact_sha256`, which must equal the bound connection version's |
| `target_sha256`, `input_sha256`                          | SHA-256 of the canonical JSON of the evaluated values, which equals their artifact digests |
| `policy_sha256`, `approval_mode`, `independent_approval` | The pinned policy document digest; the step's `approval` and `independent_review`          |
| `requester_id`                                           | The execution's initiating user, also when an API key started it                           |
| `issued_at_ms`, `expires_at_ms`                          | Database time; the earlier of issue plus `expiry_seconds` and the execution deadline       |

The intent's digest is the SDK's `intent_digest` over that binding. The intent row stores the exact canonical bytes and is immutable. Changing any bound field means a different intent and a different digest, so no decision, command or receipt for one intent can serve another. Every later read recomputes the digest from the stored bytes and artifacts, and a mismatch is an integrity failure that denies.

An intent records three separate states:

- **Authorization:** `proposed`, `awaiting_review`, `approved`, `denied`, `review_expired`, `expired`, `withdrawn` or `superseded`.
- **Effect:** `not_dispatched`, `dispatching`, `accepted`, `observed`, `unknown` or `absent`. This mirrors the SDK's action state, plus a recorded absence.
- **Outcome:** `unknown`, `verified` or `not_verified`.

A model assertion, a transport acknowledgement or missing evidence never sets `observed` or `verified`.

## Human tasks

A `human_task` step opens a wait, as in E04, and a human-task record that holds assignment, claim, escalation and expiry. Review tasks reference exactly one intent. Form tasks, in executions pinned to interpreter v2, use the same records (owner decision 8). Executions pinned to v1 keep E04's form signals unchanged.

The task's rules:

- **Eligibility.** Eligibility comes from the reviewer's current membership role, read under lock when they act. The policy's assignment group decides, plus the escalation group once the task has escalated. Viewers, API keys, runtimes and platform administrators never claim, decide or submit.
- **Independence.** With `independent_review`, the reviewer must not be the requester. The decision's actor is always a human session; the SDK refuses any other actor kind.
- **Claim.** A reviewer claims a task before deciding (owner decision 6). A claim lasts 15 minutes, never beyond the task deadline. The holder may renew or release it, and an owner or admin may release any claim. Two concurrent claims produce one holder. A decision or submission requires the actor's live claim.
- **What the reviewer sees.** The proposal shows the exact bound target and input, the connector, operation and policy identities, the requester, the expiry and the digest to quote. Values are read through the artifact store under the reviewer's current source access. A reviewer who cannot read them cannot decide.
- **Decision.** The reviewer quotes the intent digest and chooses `allow` or `deny`. The server builds an SDK `HumanDecision` whose expiry is the intent's, checks it with the SDK's `check_decision` against an authoritative binding context, and persists its exact bytes. A digest other than the stored one is refused. An equal retry under the same decision ID returns the original. A different decision, or a second decision on a decided task, conflicts. Decisions are accepted while the execution is paused and refused once it is cancelled, as E04's signals are.
- **Expiry.** The task deadline is the earliest of the step's timeout, the intent's expiry and the execution deadline. At the deadline the sweeper expires the task.
- **Escalation.** At half the task's timeout, an `after_half_timeout` task becomes escalated: its escalation group becomes eligible and any unexpired claim is released. Nothing is delivered to anyone, because E05 has no notification channel. The inbox shows the task as escalated, and delivery stays open.

The v2 contract cannot branch on a decision. A denial, an expired review or an expired intent therefore ends the execution as `failed`, with the fixed codes `approval_denied`, `review_expired` or `approval_expired`, and without any command. A future contract version can add decision branching (owner decision 17).

## Fresh authority and dispatch

An `action_dispatch` step creates an attempt with a reservation, as a read does. Dispatch happens when an eligible runtime polls the action route. Under one transaction, in E04's lock order, the API:

1. reloads the intent and decision and recomputes their digests from stored bytes;
2. checks current authority:
   - the initiator's current `execute` permission;
   - a deployment revision that may still run;
   - the connection and pool policy (E03's checks);
   - entitlement and price authority;
   - the requested state `running`;
   - the current recovery epoch, which must equal the intent's;
   - unexpired intent and decision;
   - the reviewer still holding an eligible role (owner decision 7);
3. builds a `RuntimeCommand` addressed to the polling runtime's instance and generation, with a fresh command ID and replay ID and an expiry of at most 5 minutes;
4. signs it under `lotuspond-runtime-command-v1` with the operator's command key;
5. admits it through E03's `CommandService.admit` in the same transaction.

E05 implements E03's hooks:

- **`ExecutionPinnedRecordSource`** rebuilds `PinnedCommandRecords` from the intent, decision, connection version, secret bindings and the exact target and input bytes, under locks.
- **`ExecutionDispatchAuthority`** runs the SDK's `check_command_binding` with that binding context, and the bounded entitlement and price lookups.

E03 calls both on admission, poll and claim, so a revocation that commits between dispatch and claim still refuses.

Approval alone is never enough. A changed target, input, connection, policy, configuration, package, epoch or initiator authority refuses before any command exists. A decision for another intent never matches.

**Action leases.** A runtime claims an action command through E05's runtime route. In the same transaction as E03's claim, that route creates an action lease of at most 60 seconds, bound to the attempt, the command digest, the session and the generation (owner decision 9). The lease is renewed at most every 20 seconds and never outlives the attempt deadline. E03's generic command claim route refuses commands that belong to an execution action, so no action is claimed without a lease. A claim is durable and never reclaimable. An action lease differs from E04's read leases in two ways. A pause or cancellation does not stop an in-flight write. And a lapse leads to read-back, not straight to an owner.

**The runtime boundary.** The runtime takes these steps:

1. It verifies the delivery with E03's `validate_delivery` against records it fetches for that delivery.
2. It claims, and journals `started` durably before it reaches the connector.
3. It resolves secrets just before the call.
4. It writes through the connector's provider, with the intent's `action_id` as the target idempotency key when the operation declares `idempotency: target_key`.
5. It uploads evidence values and reports an `ActionReceipt` (`accepted`, `observed_effect` or `unknown`) under its live lease, or a stop report when nothing started.

After a restart, an attempt journaled as `started` is never invoked again. The runtime reports what its journal holds, as historical evidence.

**The synthetic target.** E05 installs no real connector. The synthetic target connector and its test-only providers reach a separate synthetic target process over loopback HTTPS. They go only through E03's `DestinationTransport`, under a test egress policy, so the wiring that the first real provider must use is exercised (owner decision 16). The target keeps its own owner-only store and write counter outside every database snapshot. It injects faults: a lost request, a lost response, delay, and a crash after applying a write. Production settings cannot select it.

## Receipts, uncertainty and reconciliation

A receipt's status is the runtime's observation; it does not prove an outcome:

- `accepted` sets the effect state `accepted`;
- `observed_effect` sets `observed`;
- `unknown` sets `unknown`.

A write becomes uncertain, with effect state `unknown`, when any of these happens:

- the runtime reports `unknown`;
- its lease lapses;
- the attempt deadline passes after a claim;
- a stop report arrives without a receipt for an attempt that may have started;
- an epoch change finds the attempt in flight.

An uncertain write never schedules a blind replacement. Instead, progression schedules a read-back. The read-back is an E04 connector-read attempt of the write's reconciliation read operation, called with the intent's exact target and input, on the occurrence `<dispatch path>#reconcile`. The control plane then applies the catalog's `effect_rules` and `absence_rules` to the result:

- **Effect present.** The effect state becomes `observed` from read-back evidence, and progression continues to verification. Nothing is resent.
- **Absence, qualified.** The absence rules hold, and either the operation declares `idempotency: target_key` or the runtime confirmed the invocation never started or stopped. A qualified absence permits a resend only when the operation declares `target_key` and the dispatch step's retry policy has attempts left (owner decision 10). The resend uses the same intent, the same decision while unexpired, the same idempotency key and a new command, all under fresh authority.
- **Absence, unqualified.** The absence is recorded, and an owner or admin resolves it.
- **Unknown.** Read-backs repeat under the read's own retry policy, at most 5 in all, within the attempt deadline. After that, the execution becomes `reconciliation_required` (owner decision 11).

An owner or admin resolves an action under current recovery authority, and each resolution is recorded immutably. The choices are:

- request another bounded read-back;
- record a confirmed compute stop, which can make a later absence qualified;
- mark the effect present, citing a read-back result or historical receipt already recorded for that action;
- close the action unknown without resending, which fails its step with `outcome_unknown`.

A resolution never turns missing evidence into an observed effect, and never resends. Editors cannot resolve. A late receipt after a lease lapse, or a journal report after a restart, is historical evidence: it can settle known usage and support a resolution, but it never extends a lease or issues work.

A lapsed write keeps its compute slot and reserved liability until its stop is confirmed, as in E04. Read-backs for that write reserve their budget against the uncertain attempt's retained slot (E04's shared reservation, extended to uncertain parents). They therefore never wait for new capacity and cannot deadlock behind the writes they settle. An execution reaches its terminal state only after containment.

## Outcome verification and the E06 boundary

The compiler refuses any plan in which a dispatch on some path lacks outcome verification. E05 therefore executes `outcome_verification` steps (owner decision 2). Each step reads back through its declared read operation as an E04 connector-read attempt. It evaluates the step's deterministic rules (`eq`, `ne`, `lt`, `le`, `gt`, `ge`, `present`, `and`, `or`) over the result, using E04's expression semantics for expected values. It then records an SDK `OutcomeRecord` linking the exact intent, receipt, rule ID and version, and evidence. `verified` requires evidence. A read that fails after its retries leaves the outcome `unknown`.

An execution whose plan dispatched effects reaches `succeeded` only when every dispatched action's outcome is `verified`. A `not_verified` or `unknown` outcome fails the execution with `outcome_not_verified` or `outcome_unknown`, and the effect remains visible with its evidence. A purely computational execution keeps E04's meaning: `succeeded` reports a valid result and no verified customer-side effect.

E06 keeps business cases, partial business completion, exception ownership, business reporting and permission-correct traceability export. E05's outcome records are evidence those features will consume. E05 does not claim them.

## Controls, revocation and disconnection

- **Pause** stops new intents, tasks and dispatches at the authority transaction. In-flight writes finish and are recorded. Reviews stay open and their deadlines keep running.
- **Cancellation** withdraws undecided intents and cancels open tasks and undispatched commands. A write already claimed finishes, and is recorded and reported as a completed effect, as in spike check 26. A later decision is refused. Read-backs of uncertain writes continue after cancellation, because they contain an existing effect. Declared verification steps do not run (owner decision 12). The execution becomes `cancelled` after containment.
- **Revocation.** Revoking a connection, a pool policy entry, a runtime, the initiator's `execute`, a reviewer's role (owner decision 7), the deployment revision or entitlement prevents the next dispatch, claim, renewal or receipt. A refused renewal leaves an in-flight write to finish. Its lease then lapses into read-back.
- **Disconnection.** A disconnected runtime polls nothing, so no new command reaches it. A command already admitted to it expires unclaimed within 5 minutes, and its attempt fails `timeout_before_dispatch` with no write. A claimed write whose runtime disconnects lapses into read-back. On reconnection, the runtime's late receipt is historical evidence only. A revoked runtime is refused on reconnection.

## Recovery authority and restore

Every binding contains the recovery epoch, so an intent or decision made under an older epoch can never dispatch: the SDK reports `stale_epoch`. When the epoch changes after a restore, E04's hold applies unchanged, and E05 extends it (owner decision 13, restated 29 October 2026):

1. **Hold.** With each held execution, the control plane holds every intent that is not settled. That covers intents awaiting review, approved, dispatching or uncertain, because the restored rows cannot show what happened after the backup. Nothing is read back while the execution is held, because none of its attempts may reach a runtime before the owner's release.
2. **Release.** The owner reviews the runtime journals, as E04's procedure requires, and releases the execution. A write claimed under the older epoch blocks the release until its stop is confirmed, by the runtime's journal report or an owner's `compute_stopped` resolution. At the release, each held intent's target is read back under the new epoch, with current identity and grants. The write is never delivered again.
3. **Settle.** Each held intent is settled from its read-back result:
   - effect present: the intent is marked observed from evidence and verification continues, with no resend;
   - qualified absence: the intent is `superseded` and its step fails with `recovery_epoch_changed`, with no automatic re-proposal;
   - unknown: read-backs repeat within decision 11's bound, then the execution becomes `reconciliation_required`.

Commands, sessions and leases from older epochs are refused, as in E03 and E04. A restored grant, decision or revocation state never authorizes a write. Admissions, decisions and signals accepted after the backup are absent and must be resubmitted under their original identities. Read, export and resolution remain available while the RecoveryAuthority is unavailable; dispatch, claims and read-backs do not.

## Data, privacy, events and projections

Target and input values, receipts' response values and read-back results are encrypted artifacts with full-scope keys. Intent, decision, command, receipt, observation and outcome rows store canonical bytes, digests, fixed codes and artifact references. Command bytes carry only digests, because E03's command contract carries no payload.

Events use the SDK kinds that v14 already allows: `intent_created`, `decision_recorded`, `action_dispatched`, `action_receipt`, `dispatch_timeout`, `reconciliation_observed` and `outcome_recorded`. They are added to the execution's single gap-free sequence. Fixed task codes extend the execution-control family. Decoded events, projections, errors, logs, inbox, outbox and command rows must contain no target, input or provider sentinel.

Projections show, per action:

- the intent digest;
- the authorization, effect and outcome states;
- the task state, whether it escalated, and who holds the claim (the caller, or another reviewer);
- the receipt status;
- the evidence references.

No projection infers success from a receipt or presents `unknown` as a value.

## Capacity and budgets

A review or form task is a retained wait: it uses the environment's wait quota, not a compute slot. A dispatch, a read-back and a verification read are attempts with reservations under E04's limits. They take compute slots, except read-backs of an uncertain write, as described above. Connector writes reserve their active time and the conservative maximum charge from the price authority. A missing price or maximum charge refuses dispatch.

Unknown usage stays uncertain liability and is never measured as zero. E04's ceilings are unchanged: 25 active attempts per installation and 5 per environment, 1,000 pending executions and 10,000 retained waits per environment. Escalation and claim expiry are sweeper work; they use no compute.

## API surface

All routes are additive under E04's prefixes, behind the same opt-in fleet API, boundary and closed errors. Mutations need an idempotency key unless they carry their own identity.

- **Human tasks.**
  - `GET` list and detail of the human tasks the caller may see, as metadata only.
  - `GET` a review task's proposal, the decoded bound values, under current source access.
  - `POST` claim, release, decision (review) and submission (form, v2 only). These are human sessions only, with E04's origin, content-type, verification and MFA protections.
- **Actions.**
  - `GET` an execution's actions, and one action's detail and evidence.
  - `POST` an owner or admin resolution.
- **Runtime action routes**, for the session the command was dispatched to: poll, records, claim, renew, values, receipt and stop. E03's generic command routes refuse execution actions.

Keys never claim, decide, submit or resolve.

## Migration and compatibility

E05 adds schema v16 from exact v15, through `initialize.py`, in the caller's transaction, with strict preflight and the version written last. It adds tables for intents, human tasks, decisions, action commands with their leases, receipts, observations and outcomes, all with full-scope composite foreign keys. It widens two closed value sets, the execution `reason` codes and the execution-control event codes, by replacing their check constraints with supersets. It also adds due indexes for task deadlines, escalations, claim expiries, intent expiries and action leases.

Existing rows are unchanged. No v15 execution can contain an effect step, because E04 refused them, so nothing is backfilled. There is no destructive downgrade, and the empty-database initializer is never an upgrade. Turning the fleet API off stops admission without deleting records. Compatibility with an API older than v16 is bounded to forward repair, which preserves uncertain records.

V1 registry behaviour, SDK bytes, E03 command bytes and E04 activity bytes are unchanged. E05 adds no SDK version and no new dependency. It bumps the backend and runtime metadata.

## Verification and completion criteria

The plan's conformance suite reuses E04 Task 13's harness: killable API, orchestrator, sweeper and runtime processes, a TCP proxy, database-time clocks, owned disposable clusters and required mode. It covers:

- the spike checks that need consequential writes: rows 15–22, the write half of 26, 28 and 35;
- rows 1–7 and 34 again, with `mode=review` and a real synthetic write, because E04 ran them with form waits and reads;
- review, claim, escalation and expiry cases;
- revocation and disconnection cases;
- restore cases at tier 2;
- tenant isolation;
- privacy sentinels;
- an action share of the tier 3 load workload.

The synthetic target's write counter is the effect oracle. It lives outside every restored snapshot.

Completion means locally verified action, review and reconciliation behaviour against a synthetic target, with installed artifacts, exact-head checks and honest limits recorded. It does not qualify real connectors, real providers, customer workflows, notification delivery, UI, HA, RPO and RTO, or service availability. Zero duplicate writes in a finite test set is an observation, not proof.

## Review handoff

This specification and its plan were reviewed together. On 29 October 2026 the owner accepted all 18 of the plan's recommended options, restating decisions 1 and 13. The approval permits implementation from `main` after the finding 11 redesign merges, with the owner's selected subagent-driven method and independent review after each task. Two E04 findings that are still open must be decided along the way: finding 2 before Task 5, and finding 21 for uncertain E04 reads. Nothing in either document is implemented, and no merge, deployment, production database access, provider or customer connection, or public distribution is requested.

Author review checks:

- no E03, E04 or SDK bytes are reinterpreted;
- every authority check is repeated at the point of effect;
- the review binding is the SDK's;
- denial, expiry and uncertainty never produce a write or a success;
- the E06 boundary is explicit;
- synthetic providers only;
- planned features are not described as implemented.
