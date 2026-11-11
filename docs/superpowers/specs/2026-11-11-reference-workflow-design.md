# First reference workflow: invoice exception triage

Date: 11 November 2026 (Asia/Kolkata). Status: **specification, decided under the owner's delegation ("you only take the decisions"); not implemented.** It serves the Build row of [GOAL.md](../../GOAL.md) ("a first reference workflow built end to end") and the E10a row of the [completion design](2026-10-14-product-completion-design.md). The owner chose to build it before E05 ([actions and human tasks](2026-10-26-actions-human-tasks-design.md)) so that the workflow shapes E05, not the other way round. Nothing here is a production-readiness or live-provider claim.

## Why this workflow

GOAL.md names invoice matching as the first kind of process a company cannot yet trust an agent with. A three-way match (invoice, purchase order, goods receipt) is:

- **common:** every accounts-payable team runs it, so a buyer recognises it without explanation;
- **mostly deterministic:** the match itself is arithmetic, which the engine's decimal transforms and decisions already do, so the model is used only where judgment helps;
- **person-gated:** an exception goes to a clerk, which is exactly the Control step;
- **consequential later:** placing a payment hold in the ERP is the first real action E05 will add, with readback verification.

No buyer has selected a workflow yet. The completion design allows an "equivalent buyer-selected workflow"; this one is replaced or joined by the first buyer's process when there is one.

## The workflow

Input: `{ "invoice_id": string }`. Output: a triage record.

| #   | Step       | Kind                      | What it does                                                                                                                                                                                                                                                                     |
| --- | ---------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `invoice`  | `connector_read`          | `ap.get_invoice(invoice_id)` returns the supplier, PO number, currency and lines (SKU, quantity, unit price as decimal strings).                                                                                                                                                 |
| 2   | `order`    | `connector_read`          | `ap.get_purchase_order(po_number)` from step 1.                                                                                                                                                                                                                                  |
| 3   | `receipt`  | `connector_read`          | `ap.get_goods_receipt(po_number)`; an empty list when nothing has been received.                                                                                                                                                                                                 |
| 4   | `variance` | `iteration` + `transform` | Per line: invoiced minus ordered price, invoiced minus received quantity; then invoice total minus PO total. Decimal `subtract`/`multiply` only, no model.                                                                                                                       |
| 5   | `matched`  | `decision`                | Within tolerance → result `matched`, no person involved. The expression language has no division or absolute value, so 0.5 % is checked as `-5 × PO total ≤ 1000 × Δ ≤ 5 × PO total`, together with no line over its received quantity.                                          |
| 6   | `explain`  | `agent`                   | Else only. Given the three documents and the variance, classify the exception (`price`, `quantity`, `missing_receipt`, `duplicate`, `other`) and draft a two-sentence note for the clerk. Structured output; no tools; hard token and cost limits. Advisory: it decides nothing. |
| 7   | `clerk`    | `human_task`, `mode=form` | A workspace admin confirms or corrects the class and picks `hold`, `request_credit_note` or `accept_variance`, with a comment. Times out after 3 days.                                                                                                                           |
| 8   | result     | —                         | `{ status: matched                                                                                                                                                                                                                                                               | resolved | timed_out, class, resolution, note, decided_by }`. |

The model never sees credentials, never chooses a connector call and never sets the outcome: step 5 decides whether a person is needed, and step 7 decides what happens.

**What E05 adds later.** When `resolution = hold`, an `action_intent` → `human_task mode=review` → `action_dispatch` → `outcome_verification` chain places the hold through `ap.set_payment_hold` and reads it back. Until E05 ships, the result records the clerk's decision and nothing is written to the AP system. This is the concrete case E05's design is checked against.

## The stand-in AP system

A small HTTPS JSON service, `reference/ap-standin/`, serves the three read operations from fixture files and, for E05, one write. It is synthetic: fixed suppliers, orders and invoices covering each exception class and a clean match. It runs locally and in the conformance harness. It is never presented as a customer system or an ERP integration.

## What "end to end" requires, and what is missing today

Read on 11 November 2026 at `aba9819`. The engine, SDK and runtime can run this shape **only in test mode with synthetic providers**. Outside test mode four seams deny by design:

| Seam                                                | Today                                                                                       | Needed                                                                                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Publisher trust (`ExecutionDependencies.authority`) | `None` → `trust_unavailable`; only test authorities exist                                   | A workspace-held Ed25519 publisher key set and catalog allow-list, read by a real authority provider                              |
| Release binding (`ExecutionDependencies.assurance`) | `None` → `assurance_unavailable`                                                            | A provider that binds a package digest and configuration to an existing `ReleaseDecision`                                         |
| Execution entitlement                               | `UnavailableExecutionEntitlement`                                                           | A workspace grant with limits; no commercial terms (E08 is out of scope)                                                          |
| Runtime providers                                   | Only `activity_fixture` in test mode; the activity worker starts only from a fixture config | An `https-json` read connector and an Anthropic model provider, selected by descriptor from an operator config, outside test mode |

Proving the package version itself (scenarios that run the workflow, not an agent behind HTTP) is also open; it is the Prove row's own gap.

## Decisions

1. **Workflow:** invoice exception triage as above. Read-only plus one form task until E05.
2. **Read connector:** one generic `https-json` connector shipped in the runtime. Operations are fixed GETs declared in its catalog document; responses are validated against the declared output schema. It reuses `DestinationTransport`, the egress policy and `MountedSecretResolver`. It has no write operation until E05.
3. **Model provider:** the Anthropic Messages API through the same destination transport, key from a mounted secret, model pinned in the catalog document (default `claude-haiku-5-5` for cost; the pin names the exact ID). Usage is reported from the API's token counts; cost is computed from a pinned price table in the catalog document, and unknown stays unknown.
4. **Trust, binding, entitlement:** real providers backed by additive tables, each behind a reviewed, data-preserving migration. They deny when their rows are absent, so existing installs keep refusing.
5. **Prove:** a package evaluation runs each scenario's input through the workflow against the stand-in with a pinned model, and compares the result to the expected triage record. The model's classification is compared as a scored field; the deterministic fields must match exactly.
6. **Evidence:** every milestone records local evidence separately in `docs/VALIDATION.md`. Live model calls are recorded as live-provider evidence only when actually made, with the model ID and date.

## Milestones

Each is its own PR, in this order.

1. **Package and stand-in (synthetic).** The package, catalog documents, scenarios and the stand-in, authored with the SDK; validate, compile, sign; run through the existing conformance harness with scripted synthetic providers covering a clean match, each exception class, a clerk decision and a timeout. No product code changes.
2. **Runtime providers.** `https-json` read connector and Anthropic model provider; an operator provider config for a non-test activity worker. Tested against the stand-in and a recorded-response model double; one opt-in live call.
3. **Server seams.** Publisher trust, release binding and entitlement providers with their migrations; the server can publish, admit and deploy the package without test mode.
4. **Prove the package.** Package evaluations and their release decision, so deployment admission requires a pass.
5. **See it run.** Control Room read pages for the deployment and its executions, and the form task in a Human tasks page (the E07 increments this workflow needs, nothing more).
6. **E05** then adds the payment hold.

## Out of scope

A real ERP, more than one workflow, Studio authoring of this package (it is authored with the SDK), notification delivery, hosted installation and anything in GOAL.md's "Not part of the product".
