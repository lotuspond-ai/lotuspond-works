# First reference workflow: invoice exception triage

Date: 11 November 2026 (Asia/Kolkata). Status: **specification, revision 2, decided under the owner's delegation ("you only take the decisions"); not implemented.** It serves the Build row of [GOAL.md](../../GOAL.md) ("a first reference workflow built end to end") and the E10a row of the [completion design](2026-10-14-product-completion-design.md). The owner chose to start it before E05 ([actions and human tasks](2026-10-26-actions-human-tasks-design.md)) so that the workflow shapes E05. Nothing here is a production-readiness or live-provider claim.

**Revision 2 (owner review, 11 November).** Revision 1 gave the agent no tools and left the action to a later epic, so milestones 1–5 would have shown only what any workflow engine has. On review the owner asked that the workflow carry the product's core promise: the agent does real, bounded investigation; the consequential action it proposes waits for a person's approval of exactly that action; and the outcome is verified. E05's action chain is therefore part of this workflow's definition of done, and the console pages come after it.

## Why this workflow

GOAL.md names invoice matching as the first kind of process a company cannot yet trust an agent with. A three-way-match exception (invoice, purchase order, goods receipt) exercises every promise in GOAL.md:

| Promise                                      | Where it shows                                                                                                                                       |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| The agent does work a rule cannot            | It investigates the exception with read tools (contract prices, earlier invoices, credit notes) and proposes a resolution with the evidence it used. |
| Every version is proven before release       | Scenarios with expected proposals and outcomes gate the release, and deployment admission requires that release decision.                            |
| Each consequential action waits for a person | A payment hold or a credit-note request is sent only after a person approves that exact action, with those exact inputs, before it expires.          |
| Every outcome is recorded                    | The write is read back from the AP system and the verified outcome is linked to the version, the proposal, the approval and the dispatch.            |

It is also common (every accounts-payable team runs it) and mostly deterministic: the match itself is arithmetic, so the model is used only for the investigation.

No buyer has selected a workflow yet. The completion design allows an "equivalent buyer-selected workflow"; this one is replaced or joined by the first buyer's process when there is one.

## The workflow

Input: `{ "invoice_id": string }`. Output: a triage record.

| #   | Step            | Kind                                | What it does                                                                                                                                                                                                                                                                                                                                                                                                                   |
| --- | --------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `invoice`       | `connector_read`                    | `ap.get_invoice(invoice_id)`: supplier, PO number, currency and lines (SKU, quantity, unit price as decimal strings).                                                                                                                                                                                                                                                                                                          |
| 2   | `order`         | `connector_read`                    | `ap.get_purchase_order(po_number)` from step 1.                                                                                                                                                                                                                                                                                                                                                                                |
| 3   | `receipt`       | `connector_read`                    | `ap.get_goods_receipt(po_number)`; an empty list when nothing has been received.                                                                                                                                                                                                                                                                                                                                               |
| 4   | `variance`      | `transform`                         | Invoice total minus PO total, and invoiced quantity minus received quantity, from the documents' totals. Decimal arithmetic, no model. The expression language has no aggregation over lists, so line-level differences are left to the agent.                                                                                                                                                                                 |
| 5   | `matched`       | `decision`                          | Within tolerance → result `matched`; no model, no person. Tolerance: the price difference is within 0.5 % of the PO total, checked as `-5 × PO total ≤ 1000 × Δ ≤ 5 × PO total` because there is no absolute value, and nothing is invoiced beyond what was received, counting the quantity already invoiced against the PO, so a duplicate invoice is an exception.                                                           |
| 6   | `investigate`   | `agent` with read tools             | Else only. See below. Returns a proposal: class, resolution, amount and reasons.                                                                                                                                                                                                                                                                                                                                               |
| 7   | `route`         | `decision`                          | On the proposal's resolution: `hold`, `request_credit_note` or `no_action`.                                                                                                                                                                                                                                                                                                                                                    |
| 8   | `hold` branch   | intent → review → dispatch → verify | `ap.set_payment_hold(invoice_id, amount, reason)`, approved by one workspace admin, read back with `ap.get_payment_hold(invoice_id)`.                                                                                                                                                                                                                                                                                          |
| 9   | `credit` branch | intent → review → dispatch → verify | `ap.request_credit_note(supplier_id, invoice_id, amount, reason)`. It reaches the supplier, so the review needs a second, independent admin. Read back with `ap.get_credit_note_request(invoice_id)`.                                                                                                                                                                                                                          |
| 10  | `clerk`         | `human_task`, `mode=form`           | When the proposal is `no_action` (or an unrecognised resolution): a person records the decision (`accept_variance` or `manual_follow_up`) with a comment. Nothing is written to the AP system.                                                                                                                                                                                                                                 |
| 11  | result          | —                                   | Status (`matched`, `held`, `credit_requested` or `clerk_decided` with the clerk's `accept_variance` or `manual_follow_up`), the proposal and the verified readback where there is one. A denied or expired review ends the execution as `failed` with `approval_denied`, `review_expired` or `approval_expired` and sends nothing, because the v2 contract cannot branch on a decision (E05 specification, owner decision 17). |

### The agent's investigation (step 6)

The agent is given the three documents and the computed variance, and three read tools on the same connection:

- `ap.get_supplier_contract(supplier_id)`: contracted unit prices and agreed price-change terms;
- `ap.list_supplier_invoices(supplier_id, po_number)`: earlier invoices against the PO, for duplicates and split billing;
- `ap.list_credit_notes(supplier_id)`: credit notes already issued or requested.

It returns structured output: a class (`price`, `quantity`, `missing_receipt`, `duplicate` or `other`), a resolution, a decimal amount and up to five reasons. Each reason cites the tool call and the field it relied on. Limits: at most 6 model rounds and 8 tool calls, with hard token and cost caps from the package.

**What the agent cannot do.** It never sees credentials. It cannot call a write operation, because its tools are read-only by declaration and the runtime's read providers have no write path. It does not decide whether a person is needed (step 5 does) and it does not authorize anything. Its proposal becomes an action intent, and a person approves that exact intent. An approval of a changed or expired intent authorizes nothing (E05's binding rule). A malformed proposal fails the step; it is never coerced.

**Why two policies.** A payment hold is internal and reversible, so one admin's approval is enough. A credit-note request goes to the supplier, so it needs independent review by an admin other than the one who would otherwise act. This is also the first concrete case that pushes E05 past E04's single `workspace_admins` assignment.

## The stand-in AP system

A small HTTPS JSON service, `reference/invoice-triage/standin/`, serves six read operations, two writes and their two readbacks. Writes take an idempotency key, are kept in memory, and can be read back; a reused key with different content is refused. It is synthetic: fixed suppliers, contracts, orders, receipts, invoices and credit notes covering a clean match, each exception class, a duplicate and a contract price change that explains a variance. It can also be told to time out once after committing a write, so reconciliation is tested. It runs locally and in the conformance harness, and it is never presented as a customer system or an ERP integration.

## What "end to end" requires, and what is missing today

Read on 11 November 2026 at `aba9819`. The engine, SDK and runtime can run steps 1–7 and 10 **only in test mode with synthetic providers**. Steps 8 and 9 are refused at publication (`unsupported_step`) until E05. Outside test mode four seams deny by design:

| Seam                                                | Today                                                                                       | Needed                                                                                                                       |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Publisher trust (`ExecutionDependencies.authority`) | `None` → `trust_unavailable`; only test authorities exist                                   | A workspace-held Ed25519 publisher key set and catalog allow-list, read by a real authority provider                         |
| Release binding (`ExecutionDependencies.assurance`) | `None` → `assurance_unavailable`                                                            | A provider that binds a package digest and configuration to an existing `ReleaseDecision`                                    |
| Execution entitlement                               | `UnavailableExecutionEntitlement`                                                           | A workspace grant with limits; no commercial terms (E08 is out of scope)                                                     |
| Runtime providers                                   | Only `activity_fixture` in test mode; the activity worker starts only from a fixture config | An `https-json` connector and an Anthropic model provider, selected by descriptor from an operator config, outside test mode |

**Found while building milestone 1: the agent's timeout caps the whole execution.** E04 intersects every declared limit conservatively. An execution's active-time budget is the smallest `timeout_seconds` in its closure, here the agent's 240 s, and every attempt (each read, the agent root, each model round and tool call) is charged at least its reserved time. Reads outside the agent therefore spend the agent's allowance. With an investigation of 6 model rounds and 8 tool calls, reservations must stay under about 13 s per attempt. Milestone 2 measures real reservations against this. If they do not fit, a follow-up decides whether the agent's timeout should bound only its own subtree. This is an engine decision for the owner, not a change made here.

Proving the package version itself (scenarios that run the workflow, not an agent behind HTTP) is also open; it is the Prove row's own gap.

## Decisions

1. **Workflow:** invoice exception triage as above. The reference workflow is done only when an approved hold or credit-note request is dispatched and its readback verified, outside test mode.
2. **Connector:** one generic `https-json` connector shipped in the runtime. Read operations are fixed GETs declared in its catalog document, and responses are validated against the declared output schema. Write operations (milestone 4) are fixed POSTs with an idempotency key and a declared readback operation. It reuses `DestinationTransport`, the egress policy and `MountedSecretResolver`. Agent tools are its read operations only.
3. **Model provider:** the Anthropic Messages API with tool use, through the same destination transport, key from a mounted secret, model pinned in the catalog document (default `claude-sonnet-5-5`; the investigation needs tool use and reasoning over several documents, which outweighs the cost saving of a smaller model at these volumes; the pin names the exact ID). Usage comes from the API's token counts; cost is computed from a pinned price table in the catalog document, and unknown stays unknown.
4. **Trust, binding, entitlement:** real providers backed by additive tables, each behind a reviewed, data-preserving migration. They deny when their rows are absent, so existing installs keep refusing.
5. **E05 scope for this workflow:** action intent, review with one-admin and independent-admin policies, dispatch with idempotency, readback verification, and reconciliation of a timed-out write. The rest of the E05 plan (claim, escalation, compensation) follows the approved E05 specification and is not narrowed by this document.
6. **Prove:** a package evaluation runs each scenario's input through the whole workflow against the stand-in with the pinned model and a scripted approver. Deterministic fields (match result, routing, dispatched inputs, verified readback) must match exactly. The proposal's class and resolution are scored fields with a release threshold, and every reason must cite a tool call the agent actually made.
7. **Evidence:** every milestone records local evidence separately in `docs/VALIDATION.md`. Live model calls count as live-provider evidence only when actually made, recorded with the model ID and date.

## Milestones

Each is its own PR, in this order. The workflow is done after milestone 6.

1. **Package and stand-in (synthetic).** The full package (all eleven steps), its catalog documents, scenarios and the stand-in, authored and compiled with the SDK (the harness signs the prefix when it publishes it). The engine refuses steps 8 and 9 until E05, so the conformance harness runs a labelled **prefix package** (steps 1–7, with both action branches replaced by step 10) on scripted synthetic providers. It covers six scenarios: a clean match, a price rise the contract justifies, an overcharge, a short receipt, a missing receipt and a duplicate, three of them with tool-using investigations. No product code changes; the test-only changes are per-operation scripts in the runtime's synthetic read fixture and a connector option in the conformance driver. The clerk's 3-day timeout is not exercised.
2. **Runtime providers.** The `https-json` connector's read side and the Anthropic model provider with tool use; an operator provider config for a non-test activity worker. Tested against the stand-in and a recorded-response model double, plus one opt-in live call.
3. **Server seams.** Publisher trust, release binding and entitlement providers with their migrations, so the server can publish, admit and deploy the prefix package without test mode.
4. **E05 for this workflow.** Decision 5, and the connector's write side. The full package now publishes and runs: hold and credit-note requests dispatched after approval, read back, with a timed-out write reconciled before any retry.
5. **Prove the package.** Package evaluations of the full workflow and their release decision, so deployment admission requires a pass.
6. **See it run.** A review inbox for the exact action, and Control Room read pages showing each execution as proposal → approval → dispatch → verified outcome. These are the E07 increments this workflow needs, nothing more.

E06 (cases and business outcome reporting) builds on the verified outcomes this produces and is not part of this workflow.

## Out of scope

A real ERP, more than one workflow, person-chosen alternative actions after a denial (a denied execution ends as `failed`; a new execution is started if needed), Studio authoring of this package (it is authored with the SDK), notification delivery, hosted installation and anything in GOAL.md's "Not part of the product".
