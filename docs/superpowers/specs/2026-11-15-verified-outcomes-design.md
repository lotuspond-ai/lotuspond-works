# Verified outcomes and traceability (E06, first part)

Date: 15 November 2026 (Asia/Kolkata). Status: **design, decided by Claude under the owner's delegation ("proceed to build in the order", item 8); this change builds it.** It serves the Control step of [GOAL.md](../../GOAL.md) ("Who allowed this, and did it happen? … Each outcome is verified and linked to the version, the action and the decision") and E06 of the [delivery plan](../../plans/2026-10-13-platform-delivery.md) ("Input → deployed version → action/approval → target read-back → outcome traceability").

## E06 in two parts

E06 in the [completion design](2026-10-14-product-completion-design.md) has two parts:

- Traceability, verified outcome rules with explicit uncertainty, and reporting and exports. This change builds it. It needs no new storage, because E05 already records every link.
- Cases that group executions under a business key, and exception ownership. That is the next change, and it needs schema v17.

## What exists

E05 stores, per action:
- the intent, with its canonical binding: connector, operation, policy, target and input digests, and the requester;
- the review task and its decision: allow or deny, by whom, when, and the decision digest;
- the command and its status;
- the runtime's receipts;
- the read-back observations, each with its rule;
- the outcome record: `verified`, `not_verified` or `unknown`, with rule, version, evidence digest and time.

Each execution pins its deployment revision. The revision links to the publication (package name, version and digest), the configuration digest and, when one admitted it, the release. Nothing joins these into one answer.

## Decisions

1. **A trace per execution** (`GET …/executions/{id}/trace`). It is read without locks and built from committed rows only. In order:
   - the execution: states, input and result digests, the initiator;
   - the version: deployment, revision, package name, version and digest, plan and configuration digests, and the release and candidate run when one bound it;
   - each action, in intent order: its step, connector and operation, the intent, target, input and policy digests, its review and decision (who, when, allow or deny, decision digest), its dispatch (command digest and status), its receipts, its read-backs and its outcome record.

   **Digests and identifiers only, never values.** A proposal's values stay behind the audited proposal route. A person who may read the execution may read its trace (`read`), as with every execution projection. The trace works without action services: an interpreter v1 execution has no actions.
2. **The business outcome**, one rule over the execution's state and its actions. In order:
   - `in_progress`: the execution has not finished.
   - `unknown`: an effect's occurrence or outcome is uncertain. This covers an effect that is `dispatching` or `unknown`, a write that was made but whose outcome is still `unknown`, and an execution held in `reconciliation_required`.
   - `not_verified`: a write was made and its verification failed.
   - `verified`: the execution succeeded, and every write it made was verified.
   - `completed`: it succeeded and made no external write. This is E04's meaning: a valid result, not a verified effect.
   - `partial`: it failed or was cancelled after at least one verified write.
   - `no_effect`: it failed or was cancelled, and nothing was written.

   `unknown`, `not_verified` and `partial` are **exceptions**, and so is a failed execution with no effect: each needs a person. The rule never turns uncertainty into success. A missing read-back or outcome record is `unknown`, never `verified`.
3. **Export.** The trace carries `trace_sha256`, the SHA-256 of its canonical JSON without that field, as release evidence does. `GET …/trace/export` returns the same document as a download and is audited (`executions.trace_exported`), with identifiers only.
4. **Reporting** (`GET …/outcomes?since_ms=&deployment_id=`). It counts executions admitted since a time, optionally for one deployment, by business outcome, and gives the number of exceptions. It covers at most the 10,000 most recent executions in the window, and says when it was cut off (`truncated`).
5. **Console.**
   - Control Room's execution detail shows the business outcome and the trace: version, each action's review and outcome, and a download.
   - Control Room shows the last 7 days' outcome counts.
   - The trace replaces the panel's separate actions request, which answered 503 without action services.

## Out of scope (next change)

Cases and business keys, exception ownership and resolution notes (schema v17), partial completion across executions of one case, and notifications.
