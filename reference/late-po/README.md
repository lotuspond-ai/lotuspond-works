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
`anthropic-messages` providers against the stand-in and the model double; Linux only). The first case denies
each late order's review, so nothing is written; the second approves them: both writes go out
through the runtime's `https-json` write provider, are read back and verified, and a write that
times out after committing is settled by read-back, never resent.

Milestone 5 adds two cases on an operator directory provisioned only with
`python -m lotuspond.authority`, with release decisions required as an operator's are by
default. The first proves the package on an evaluation deployment, releases it and runs
the late orders on the released version. The second, opt-in
(`LOTUSPOND_CONSOLE_JOURNEY=1` after `npm run build`), has two people decide one late
order's reviews in the built console (`console_journey.mjs`), then checks Control Room
and the server agree. The runtime stays in test mode in both, because the stand-ins
are on loopback, which a production runtime's egress policy refuses.
