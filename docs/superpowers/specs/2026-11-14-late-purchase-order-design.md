# Second reference workflow: late purchase order recovery

Date: 14 November 2026 (Asia/Kolkata). Status: **specification, decided under the owner's delegation; not implemented.** On 2026-11-14 the owner asked for a second supply chain example to be built before a customer brings a process ([ADR-0017](../../architecture/0017-position-works-for-supply-chain-operations.md)). Claude chose the process and wrote this specification. Nothing here is a production-readiness or live-provider claim.

## Why this workflow

The first reference workflow, [invoice exception triage](2026-11-11-reference-workflow-design.md), is an accounts-payable process on one system. On its own it makes LotusPond Works read as an invoice tool, which the owner ruled out. The second example comes from the operations side of supply chain, and it is chosen to show what the first does not:

| What it shows                                    | Where                                                                                                                                             |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Work that spans several systems                  | Three connections, each with its own credential: the ERP, the carrier's tracking service and the shared customer inbox.                           |
| The agent does work a rule cannot                | It reads the supplier's messages and the carrier's tracking events, and proposes a realistic new date with the evidence it used.                  |
| Two consequential actions, each approved exactly | Changing the promised date in the ERP, then telling the customer. The second reaches a customer, so it needs a second, independent person.        |
| Every outcome is recorded                        | Both writes are read back: the ERP's promised date, and the message in the inbox's sent items.                                                    |
| The product's recent engine work holds           | Two dispatches in one region exercise the restore read-backs fixed on 2026-11-14 (PR #35), and three connections exercise per-connection secrets. |

It is a process every supply chain team runs by hand: a purchase order passes its promised date, and someone chases the supplier, checks the carrier, decides a new date, updates the ERP and tells the customer. It is also the "Late purchase orders" illustration on the company landing sample, so the public example and the built one match.

## The workflow

Input: `{ "po_number": string }`. Starting it when a promised date passes is a trigger outside this package (out of scope); a test or an operator starts it. Output: a recovery record.

| #   | Step          | Kind                                | What it does                                                                                                                                                                                                                                     |
| --- | ------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `order`       | `connector_read`                    | `erp.get_purchase_order(po_number)`: supplier, lines, the promised date and the customer sales order it serves. Dates are integer days since 1970-01-01, because expressions compare numbers.                                                    |
| 2   | `shipment`    | `connector_read`                    | `carrier.get_shipment(po_number)`: status and estimated arrival day; empty when nothing has shipped.                                                                                                                                             |
| 3   | `on_time`     | `decision`                          | The carrier's estimate is on or before the promised day → result `on_time`; no model, no person.                                                                                                                                                 |
| 4   | `investigate` | `agent` with read tools             | Else only. See below. Returns a proposal: a new promised day, whether the customer must be told, and the reasons.                                                                                                                                |
| 5   | `reschedule`  | intent → review → dispatch → verify | `erp.set_promised_date(po_number, day, reason)`, approved by one planner; read back with `erp.get_purchase_order` and verified by its promised day.                                                                                              |
| 6   | `tell`        | `decision`                          | On the proposal's `notify_customer`.                                                                                                                                                                                                             |
| 7   | `notify`      | intent → review → dispatch → verify | `inbox.send_customer_update(sales_order, day, message)`, a fixed template with the new day and the agent's one-line reason. It reaches a customer, so an independent second planner approves it. Read back with `inbox.get_sent_message(key)`.   |
| 8   | result        | —                                   | Status (`on_time`, `rescheduled` or `rescheduled_and_notified`), the proposal and the verified readbacks. A denied or expired review ends the execution `failed` and sends nothing further (as in the first workflow, owner decision 17 of E05). |

### The agent's investigation (step 4)

The agent gets the order, the shipment and three read tools:

- `supplier.list_messages(po_number)` on the inbox connection: the supplier's replies about this order;
- `carrier.list_tracking_events(po_number)`: the carrier's events, including delays and exceptions;
- `erp.get_stock_cover(sku)`: days of stock left for each line, so it can say whether the delay stops a customer order.

It returns structured output: a new promised day, `notify_customer` (true when the new day passes the customer's requested day in the sales order), and up to five reasons, each citing the tool call and field it used. Limits as in the first workflow: at most 6 model rounds and 8 tool calls, with hard token and cost caps. The model is Claude Haiku 5.5, the first workflow's choice (its decision 3), so both examples are scored the same way.

The agent cannot write, never sees credentials, and does not decide whether a person is needed. Its proposal becomes two intents at most, and a person approves each exact intent.

### Policies

- **Planners** (one approval) for the ERP date change: it is internal and can be changed back.
- **Customer messages** (independent review: a second planner, not the first) for the customer update.

## The stand-in systems

`reference/late-po/standin/` serves three synthetic systems from one local HTTPS process, each on its own route prefix and bearer token, so the package needs three connections: the ERP (orders, sales orders, stock cover, the promised-date write and its readback), the carrier (shipments and tracking events) and the inbox (supplier messages, the customer-update write and its sent-items readback). Writes take an idempotency key and can be told to time out once after committing, as in the first stand-in. Scenarios: on time, late with a supplier's confirmed new date, late with only carrier evidence, late past the customer's requested day (notify), a duplicate start for the same order, and a carrier with no shipment yet. It is never presented as a customer system.

## Milestones

Each is its own PR. Milestones 1 to 3 reuse what the first workflow built (the `https-json` connector and the Anthropic provider, the server seams, E05); 4 and 5 are shared with the first workflow's milestones 5 and 6, built once for both.

1. **Package and stand-in.** The package, its three connectors' catalog documents, the two policies, scenarios and the stand-in, authored with the SDK; a conformance case on synthetic providers covering the six scenarios.
2. **Real providers.** The same case on `https-json` against the stand-in and the recorded-response model double, three connections with three secrets.
3. **Both actions end to end.** Both reviewed writes dispatched, read back and verified, including a timed-out write reconciled before any retry, and the independent review of the customer message.
4. **Prove both packages.** Package evaluations and the release binding (the first workflow's milestone 5), run on both.
5. **See them run.** The review inbox and Control Room read pages (the first workflow's milestone 6), shown on both.

## Milestone 1 as built (14 November)

- `reference/late-po/` holds the generator, the package, three connectors with their routes documents, the model, two policies, the data, six scenarios and the three-system stand-in. `backend/tests/test_reference_late_po.py` (24 tests) runs anywhere.
- The conformance driver now binds several connectors to one package, each through its own connection and secret (`Operator(connections=...)`), and an action world can install several connectors (`actions_mode`). `test_reference_late_po.py` in the conformance suite runs all six scenarios on real processes in an E05 action world (test mode, because production composition still wires interpreter v1 only): on-time orders succeed with two reads and no model; late orders read across the three systems and stop at the planner's review of the exact date change, which the test denies (`approval_denied`, nothing written). It passed in the local Linux container.
- **Finding for milestone 3 (resolved below).** E05 requires a write's read-back to accept the write's exact target and input (owner decision 18), so the read-backs here take the change as input. The `https-json` connector then refused any read with an input and had no write side; the invoice workflow's full package had read-backs without input.

## Milestones 2 and 3 as built (15 November)

- **The `https-json` write side** (runtime): a routes document names POST routes for writes, and a read-back route declares `"input": "ignored"`, so it accepts the write's input and reads by target. `HttpsJsonWriteProvider` posts the exact input as canonical JSON with the bearer token and the action's `Idempotency-Key`; a sent request without a usable 2xx answer is `unknown`, and E05 reads it back first. A test-mode runtime's operator configuration may add these write providers (`actions`); production refuses them, since production command trust is not part of that configuration (production action wiring is later work).
- **Both reference packages deploy with their writes:** the invoice package's read-backs now take their writes' input, as the late PO package's do, and both connectors name their POST routes.
- **Milestone 2:** the conformance case already runs on `https-json` and `anthropic-messages` with three connections and three secrets. The opt-in live model call is not made: there is no API key on the build machine.
- **Milestone 3:** a second conformance case approves each late order. The date change goes to the ERP and, after a second, independent planner's approval, the customer update to the inbox, both through `https-json`; each is read back and verified, and the execution succeeds. One date change times out after committing: it is settled by read-back and never resent. The action world now installs each connection's secret binding, which E03's command admission checks; the synthetic target had no secret, so this had never been exercised.

## Out of scope

A real ERP, carrier or mail system; the date trigger; free-text customer messages (the template is fixed); choosing another supplier or splitting an order (the agent may mention it as a reason, nothing acts on it); Studio authoring of this package; anything in GOAL.md's "Not part of the product".
