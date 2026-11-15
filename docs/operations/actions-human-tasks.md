# Reviewed actions and human tasks: operator guide (E05)

E05 lets a workflow write to a business system only through a reviewed, durable action. A write is first recorded as an immutable **intent**: its exact connector, operation, target and input, bound by digest. A person **reviews** it when the package's policy asks for one. It is **dispatched** as a signed command to one runtime, which writes **once** under a lease. Then it is **read back** and **verified** before the execution may succeed.

This guide extends [the durable execution guide](durable-execution.md): its topology, grants, capacity, restore and runbooks all still apply. Read that one first.

> **Not production-qualified.** Every connector, provider and target that has run this is synthetic, in local Linux containers. Production gains a command key and an installed catalog only when its operator provisions them ([operator authority](operator-authority.md#reviewed-actions)); until then the action and task routes answer 503 and only interpreter v1 runs. No real system of record has been written through yet (see [Still open](#still-open)).

## What turns it on

Action dispatch exists only when composition is given a **command key provider**: an object that signs commands (`sign`) and answers their current verification trust (`current`). Without one:

- the API's action and task routes refuse with 503 `authority_unavailable`;
- the engine serves interpreter v1 only. A worker configured to serve v2 (`LOTUSPOND_EXECUTION_WORKER_INTERPRETER_IDS` naming `lotuspond.execution-interpreter.v2`) refuses to start (`unsupported_interpreter`), so no worker progresses an execution that pins v2 (any package with an effect or a review step);
- the installed catalog names no connector, so no deployment can allow a write;
- `/api/v1/ready` reports the database and limiter only. It never reports dispatch as ready, so a missing key is never readiness success.

In production the operator directory holds both: `authority command-key` provisions the key (`command-signing.key`, `command-trust.json`) and `authority install-connector` / `secret-binding` the catalog (`catalog.json`); see [operator authority](operator-authority.md#reviewed-actions). The key is reread on every signature; the catalog is read when the API and workers start. A test command key or a catalog naming the synthetic target refuses composition outside test mode.

## Command key custody and rotation

The command key is separate from package publisher keys and from the activity signer.

- **Custody.** Keep the signing key with the operator, never in the database. Give runtimes only the verification trust, through a channel independent of the control plane (E03's command trust).
- **Trust snapshots** are valid for at most 60 seconds. A runtime refuses a snapshot that claims longer, and refuses any command whose signing key is not in the current snapshot.
- **Rotation.** Publish the new key in trust first, start signing with it, then remove the old key from trust. E03's runbook limits the overlap to five minutes; that limit is operator policy, and nothing in the product enforces it. A command signed by a key not in current trust is refused when the runtime verifies it, before any claim.
- **Compromise.** Remove the key from current trust at once. Commands already delivered but not claimed are then refused. A write already claimed has started or may start; treat it as uncertain and follow [Unknown read-backs](#unknown-read-backs).
- **Command lifetime.** A command expires at most five minutes after issue, and never after its intent's expiry. E03 refuses a claim, a receipt or a stop of an expired command. A command that expired unclaimed provably ran nowhere: progression rechecks the step and either fails it with the cause (`approval_revoked`, `approval_expired`) or, when the dispatch step's retry policy lists `timeout_before_dispatch`, sends one more command under the same intent.

## Action leases

- A runtime claims a command under a 60-second lease and renews it every 20 seconds from its own thread, outside the provider call.
- A write that has started is never cancelled by its lease. If renewal is refused or lost, the write is allowed to finish; its outcome is reported only while the lease holds. Otherwise the runtime's journal keeps it, and the control plane treats the lapse as uncertain.
- The runtime journals `claimed`, then `started` (durably, before any connector code runs), then the report it is about to send. A restarted runtime never invokes a journaled write again: it reports `started` as `stopped_unknown`, `claimed`-only as `not_started`, and a finished write with its journaled report.
- **A lapsed write keeps its deployment's compute slot** until its stop is confirmed: by the runtime's report, or by an owner's `compute_stopped` resolution. A runtime that was killed or cut off reports only after its previous session lease ends; a revoked one never does. Watch per-deployment lapsed writes: a deployment with one active slot stops making progress while one is held.

## The synthetic target is test-only

The conformance suite and installed acceptance write to a synthetic HTTPS target with the runtime's `synthetic-target-write` and `synthetic-target-read` providers. These exist only to qualify the protocol.

- A production-mode runtime refuses them at configuration, and its provider child answers `unavailable` without reaching anything.
- A production-mode runtime refuses to report any connector in its observation ceiling, so the control plane delivers it no action command.
- No real write provider ships. The `https-json` connector's write side is not built.

## Human tasks and reviews

A review is a human task. It is listed only once its wait is retained.

- **Who.** People only: an API key, a runtime, a viewer or a platform administrator never claims, decides or submits. Eligibility is the person's current role in the task's assignment group, plus its escalation group once escalated, rechecked under lock at every action. With independent approval, the requester cannot decide.
- **Claim.** One holder at a time, for 15 minutes or until the task's deadline, whichever comes first. The holder may release it, and so may an owner or admin; once it is released or lapsed, another eligible person may claim. Reading the proposal needs no claim.
- **Decision.** `allow` or `deny`, naming the intent's digest. A decision for any other digest is refused (409 `conflict`): changed parameters can never be approved by an older review.
- **Escalation** happens at half the task's timeout: the escalation group becomes eligible and any current claim ends. **No notification is sent**, by mail or otherwise. People find tasks by listing them (`GET .../human-tasks`). Arrange your own polling or alerting on that list.
- **Expiry.** At the task's deadline an open task expires, its intent becomes `review_expired`, and the step fails `review_expired`. An approval also has its own expiry (`expiry_seconds` in the policy): a write not dispatched by then fails `approval_expired`.
- **Revocation.** If an approver loses their eligible role before dispatch, the write is not sent: it fails `approval_revoked`. A write already claimed keeps going and records what happened (its receipt is accepted even if the initiator has since lost `execute`).

## Restore

Restore follows the [durable execution procedure](durable-execution.md#backups-and-restore): fence, restore, advance the epoch, start, hold, reconcile, release. E05 adds these steps:

1. **Held intents.** An intent of a held execution is held with it. Nothing is dispatched or read back for it before its owner's release.
2. **Journal review.** Before restarting a runtime, read its journal directory. Note each action attempt with a `started` or finished phase: those writes may have reached the target after the backup.
3. **A write claimed under the older epoch** blocks its execution's release (409 `conflict`) until its compute stop is confirmed. Once the runtime that held it is stopped, confirm it with a resolution of kind `compute_stopped` (owner or admin), then release.
4. **Release.** `POST /executions/{id}/reconciliation`. Every held intent is then **read back**, never resent:
   - effect present after a write the restored rows dispatched: `observed`, then verification;
   - an intent the restored rows never dispatched fails `recovery_epoch_changed`, even when its effect reads back present, because those rows lack the decision and dispatch that wrote it. Its write is never repeated (see [Still open](#still-open));
   - an intent still under review at the backup is `superseded` and fails `recovery_epoch_changed`. The workflow must be started again for a new review.
5. **A restored grant cannot authorize.** A runtime revoked after the backup is refused under the new epoch even though the restored rows show it active.

## Runbooks

- **Held intents** (execution `reconciliation_required` after a restore): do the journal review, confirm any older-epoch write's stop, then release. Never delete an intent or its rows to get past a hold.
- **Unknown read-backs.** A write whose result is uncertain (lost response, lapsed lease) is read back with the connector's reconciliation read, up to five times. If the effect is present the step succeeds. If absence is qualified (the operation declares `target_key` idempotency, or the write's stop is confirmed), the write is closed `not_applied`; it is resent once, under the same intent, decision and idempotency key, only when the operation declares `target_key` and the dispatch step's retry policy has attempts left. Otherwise the step fails `not_applied`. Otherwise the execution becomes `reconciliation_required` with reason `action_uncertain`, and an owner resolves it.
- **Owner resolutions** (`POST /executions/{id}/actions/{action}/resolution`, owner or admin, by session, with a `resolution_id`):
  - `readback`: run one more read-back;
  - `compute_stopped`: confirm the write's compute has stopped (the runtime is stopped and its journal reviewed), then read back. Allowed while the execution runs, to free a lapsed write's slot;
  - `effect_present`: the effect is present, citing evidence already recorded for this intent (a read-back result or a historical receipt); the step succeeds and verification follows;
  - `close_unknown`: give up on knowing; the step fails `outcome_unknown`.

  A resolution never resends, and never turns missing evidence into an observed effect.
- **Expired reviews.** Nothing retries an expired review. Start the workflow again; the new intent needs a new review.
- **Failed verification.** An effect that is present but whose verification read fails leaves the step `outcome_not_verified`, with the effect still recorded as observed. Investigate the target; do not resend.

## Upgrade and rollback

- **Upgrade** v15 to v16 only, with `npm run db:migrate`: one transaction, strict preflight, the version written last. A database at v14 first takes E04's v15 step. Every v15 row is kept; no v15 execution has an effect or review step, so nothing is backfilled.
- **No downgrade.** There is no destructive downgrade, and an API older than v16 is not supported against a v16 database. To stop E05, remove the command key provider (the action routes answer 503 and only interpreter v1 runs) or disable the deployments that pin v2.
- **Recovery is forward repair**, preserving every uncertain record. Never delete a held or unknown action to make a rollback possible.

## Still open

Each must be qualified before production use:

- a real system of record: the `https-json` write side and the operator command key and catalog are built and tested locally, against stand-ins, but nothing has written to a customer's system; command key rotation without a restart;
- notification delivery for tasks and escalations;
- compensation (plan decision 14): reversing an applied write stays refused;
- notifications and deadlines for cases (E06 is built: traces, business outcomes and cases with exception ownership, schema v17);
- the rest of E07's operations UI (the console's Human tasks inbox and Control Room exist since 2026-11-15; workers, queues and deployment changes do not);
- E08 signed entitlements;
- E09 operations: hosted CI, capacity at the planning target (tier 3 failed its throughput criteria on a 4-vCPU VM), HA and service levels;
- the owner's decisions on ACT-12 (a reviewer without source access), RESTORE-2 (a restored, never-dispatched intent that reads back present), and whether a lapsed write should keep its slot until its stop is confirmed. See the [qualification record](../validation/2026-11-13-actions-human-tasks.md).
