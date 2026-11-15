# Cases and exception ownership (E06, second part)

Date: 15 November 2026 (Asia/Kolkata). Status: **design, decided by Claude under the owner's delegation ("proceed to build in the order", item 8); this change builds it.** It follows [verified outcomes](2026-11-15-verified-outcomes-design.md) and completes E06's scope in the [completion design](2026-10-14-product-completion-design.md) for this product stage: "separate case/execution/attempt state … exception ownership, business reporting".

## The problem

A business item is handled by one or more executions: an invoice, or a late purchase order. A run can fail, be retried, or need a person after an uncertain write. Verified outcomes answer "did this execution do it?". They don't answer "is this purchase order done, and who is dealing with it if not?".

## Decisions

1. **A case is a business item in one environment, named by its business key** (for example `PO-1042`).
   - **The key is customer data, so it is stored as the platform stores customer values:** encrypted as an execution artifact, under the environment's artifact policy.
   - Only its SHA-256 (`key_sha256`) is kept in plain text, for lookup and uniqueness. Creating a case with a key that already exists returns that case.
   - Keys are 1–200 characters of text.
2. **Executions join a case explicitly.** `POST …/cases/{id}/executions {execution_id}` links one. An execution belongs to at most one case, and linking it again to the same case is a no-op. The admission contract (E04) is unchanged, so a caller that starts an execution for a business item links it next.
3. **The case outcome comes from its executions:** the business outcome of its most recently admitted execution, by the rule in the previous design. A case without executions is `empty`.
4. **Exceptions and ownership.**
   - **When a case needs a person.** It has an open exception when any linked execution's outcome is an exception and that execution was admitted after the case's last resolution.
   - **Ownership.** A person (session only, with the `review` permission) takes ownership of a case and may release it; an owner or admin may release anyone's.
   - **Resolution.** Only the owner resolves it, with one of a closed set of kinds:
     - `corrected_by_hand`: a person fixed the business system directly;
     - `handled_by_retry`: a later execution did it;
     - `no_action_needed`;
     - `handled_elsewhere`.

     There is no free text, so no customer value enters the record. The resolution covers every execution admitted up to that moment (`resolved_through_admitted_at_ms`), and a later exception reopens the case.
   - **Changes are fenced.** Each ownership change and resolution names a request ID. An equal retry is replayed, and each change is audited with identifiers only.
   - **A resolution changes no execution and sends nothing.** It records who decided that the business item is dealt with.
5. **Schema v17** adds two tables and changes nothing else:
   - `execution_cases`: the key's digest and its encrypted artifact, the creator, the owner and the last resolution;
   - `execution_case_links`: a case, an execution, who linked it and when.

   The migration follows v16's pattern. It verifies exact v16 (every table, check, index and the ledger) before any DDL, refuses anything else without changing it, and is tested against a committed v16 fixture.
6. **API:**
   - `POST /cases {key}` (`execute`);
   - `GET /cases` (keyset pages; the console puts cases that need a person first) and `GET /cases/{id}` with its executions (`read`);
   - `POST /cases/{id}/executions` (`execute`);
   - `POST /cases/{id}/owner {request_id, action: take | release}` and `POST /cases/{id}/resolution {request_id, kind}` (a person's session, `review`).

   Reading a case's key needs the artifact store. Without execution services the routes answer 503, as other value-bearing routes do.
7. **Console.** An Operate page, *Cases*, lists one environment's cases: key, outcome, whether one needs a person, and the owner. Open cases that need a person come first. A case opens to its executions with their outcomes, and to take, release or resolve.

## Out of scope

Automatic case creation from a package's declared key, notifications to owners, deadlines on exceptions, and reports by owner.
