# Invoice exception triage (synthetic reference workflow)

The first reference workflow, specified in
[the design](../../docs/superpowers/specs/2026-11-11-reference-workflow-design.md). Everything here
is synthetic: no customer system, supplier, person or amount is real, and nothing here is a
provider-qualified or production package.

| Path                | What it is                                                                                                                                                       |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `generate.py`       | The source of truth. Authors the catalog documents and both packages and compiles them with the SDK.                                                             |
| `package.json`      | The full workflow: reads, tolerance decision, tool-using investigation, payment hold and credit-note actions behind review and readback, clerk form.             |
| `prefix.json`       | The same workflow with both action branches replaced by the clerk's form. E04 refuses action steps until E05, so this is what runs today (test mode only).       |
| `catalog/`          | The `ap-standin` connector, the investigator model and the three policies. The connector's artifact digest is a declared placeholder until milestone 2 ships it. |
| `data/ap.json`      | The stand-in's records: suppliers' contracts, purchase orders (with quantity already invoiced), goods receipts, invoices and credit notes.                       |
| `scenarios.json`    | Six cases with expected results and, for test mode only, scripted model rounds and clerk answers.                                                                |
| `standin/server.py` | The stand-in AP system over HTTPS JSON with bearer-token auth, idempotent writes and readback, and an optional timeout after committing a write.                 |

Regenerate after changing `generate.py`, from the repository root:

```bash
uv run --frozen --project backend python reference/invoice-triage/generate.py
```

Tests: `backend/tests/test_reference_invoice_triage.py` (documents, rules and the stand-in; runs
anywhere) and `backend/tests/execution/conformance/test_reference_triage.py` (the prefix on real
API, worker and runtime processes with synthetic providers; Linux only).
