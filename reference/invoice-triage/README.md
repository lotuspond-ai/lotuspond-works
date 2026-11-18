# Invoice exception triage (synthetic reference workflow)

The first reference workflow, specified in
[the design](../../docs/superpowers/specs/2026-11-11-reference-workflow-design.md). Everything here
is synthetic: no customer system, supplier, person or amount is real, and nothing here is a
provider-qualified or production package.

| Path                      | What it is                                                                                                                                                                                             |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `generate.py`             | The source of truth. Authors the catalog documents and both packages and compiles them with the SDK.                                                                                                   |
| `package.json`            | The full workflow: reads, tolerance decision, tool-using investigation, payment hold and credit-note actions behind review and readback, clerk form.                                                   |
| `prefix.json`             | The same workflow with both action branches replaced by the clerk's form. E04 refuses action steps until E05, so this is what runs today (test mode only).                                             |
| `catalog/`                | The `ap-standin` connector, the investigator model and the three policies. `ap-standin.routes.json` is the connector's installed artifact: its canonical SHA-256 is the connector's `artifact_sha256`. |
| `data/ap.json`            | The stand-in's records: suppliers' contracts, purchase orders (with quantity already invoiced), goods receipts, invoices and credit notes.                                                             |
| `scenarios.json`          | Six cases with expected results and, for test mode only, scripted model rounds and clerk answers.                                                                                                      |
| `standin/model_double.py` | A local HTTPS double of the Anthropic Messages API, answering from a scenario's scripted rounds in the documented response shape. Not a recording of a live model.                                     |
| `standin/server.py`       | The stand-in AP system over HTTPS JSON with bearer-token auth, idempotent writes and readback, and an optional timeout after committing a write.                                                       |

Regenerate after changing `generate.py`, from the repository root:

```bash
uv run --frozen --project backend python reference/invoice-triage/generate.py
```

Tests: `backend/tests/test_reference_invoice_triage.py` (documents, pins, rules, the stand-in and the
`https-json` connector reading it; runs anywhere) and
`backend/tests/execution/conformance/test_reference_triage.py` (the prefix on real API, worker and
runtime processes, with the runtime's `https-json` and `anthropic-messages` providers against the
stand-in and the model double; Linux only). `test_reference_triage_full.py` runs the full package
the same way on an operator directory with release decisions required: proven on an evaluation
deployment, released, then every scenario on the released version, each hold and credit-note
request allowed, read back and verified (a lost answer settled by read-back, never resent). Its
opt-in console case (`LOTUSPOND_CONSOLE_JOURNEY=1` after `npm run build`) has two admins decide a
hold, an independent credit-note request and the clerk's form in the built console.
