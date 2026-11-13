# Late purchase order recovery (synthetic reference workflow)

The second reference workflow, specified in
[the design](../../docs/superpowers/specs/2026-11-14-late-purchase-order-design.md). Everything here
is synthetic: no customer system, supplier, carrier, customer or person is real, and nothing here is a
provider-qualified or production package.

| Path                     | What it is                                                                                                                                         |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `generate.py`            | The source of truth. Authors the catalog documents and the package and compiles them with the SDK.                                               |
| `package.json`           | The workflow: ERP and carrier reads, the on-time decision, a tool-using investigation, the ERP date change and the customer update behind review. |
| `catalog/`               | Three connectors (`erp-standin`, `carrier-standin`, `inbox-standin`) with their routes documents, the investigator model and the two policies.    |
| `data/late-po.json`      | The stand-in's records: orders with their customer sales orders, shipments, tracking events, supplier messages and stock cover.                   |
| `scenarios.json`         | Six cases with expected results, scripted model rounds (test mode only) and the reviewers' decisions.                                              |
| `standin/server.py`      | The three stand-in systems in one HTTPS process: one route prefix and one bearer token each, idempotent writes and readback.                      |

The model double is the first workflow's (`reference/invoice-triage/standin/model_double.py`).

Regenerate after changing `generate.py`, from the repository root:

```bash
uv run --frozen --project backend python reference/late-po/generate.py
```

Tests: `backend/tests/test_reference_late_po.py` (documents, rules, deployment admission, the
stand-in and the `https-json` connector reading each system with its own secret; runs anywhere) and
`backend/tests/execution/conformance/test_reference_late_po.py` (the package on real API, worker
and runtime processes, three connections with three secrets, the runtime's `https-json` and
`anthropic-messages` providers against the stand-in and the model double; Linux only). A late order
stops at the planner's review of the exact date change, which the test denies: the `https-json`
connector has no write side yet, so no write is dispatched (milestone 3).
