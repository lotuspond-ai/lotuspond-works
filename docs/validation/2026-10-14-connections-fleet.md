# Connections and runtime identity qualification

Approved E03 plan/spec date: 2026-10-14. Execution continued on 2026-10-15 UTC.
This is local foundation evidence; hosted exact-head CI, publication, deployment
and production/customer qualification are separate gates.

## Attribution and tested boundaries

The harness builds SDK 0.2.0, runtime 0.1.0 and API 0.2.0 wheels outside checkout,
plus an independent runtime wheel rebuilt from its sdist. Isolated `python -I`
consumers reject editable imports. Unique retained evidence includes source
commit/dirty-state and file hashes, exact artifact digests, installed-origin
records, actual outputs, schema namespace identity/owner/comment cleanup and
owned process receipts. Only fixed safe reports/manifests/distributions are
uploaded; private URLs/tokens/keys/state/requests stay private.

The ordinary CLI has empty connector observations. Actual TLS/API/PostgreSQL
qualifies fresh acquire→poll→first heartbeat, revocation and forced valid-but-
overdue startup C/P recovery across committed activation-response loss, restart
and a natural old-lease wait. Product clocks/TTLs remain unchanged. This does not
claim natural ten-minute wall-clock renewal; Task6 tests scheduling separately.
A distinct explicitly enrolled installed-library fixture has the exact synthetic
connector ceiling and independent record/trust/dispatch inputs. It verifies a
signed no-effect command/claim and a fixed child resolving only its mounted
synthetic secret over approved real TLS. Production settings refuse injected
hooks. Restored machine/admission records remain fenced by authority outside the
schema; a fresh epoch without current business grants still denies. Correction
round1 keeps the installed consumer and its TLS sink alive across those phases:
a positive fixture send precedes restore, then real guarded invocation attempts
measure receiver and Linux secret-file-open deltas under restored and fresh-epoch
proof. The reported new-effect value is derived from those live counts. This is
not a complete customer backup/restore drill.

## Failures and corrections

The initial red was the absent installed integration harness. Attempt1 rejected
a reserved synthetic `example.test` address; EmailStr independently reproduced
that fixture error, corrected to `example.com`, with cleanup confirmed. Attempt2
then exposed duplicate no-store response headers from fleet one-time routes and
API middleware. Strict runtime header refusal correctly blocked real enrollment.
A focused real-route test failed with two no-store values; raw ASGI mixed-case
cache-control duplication also failed before correction. Middleware now replaces
all API cache-control entries with exactly one no-store field while preserving
other fields (including repeated Set-Cookie) and non-API behavior. Transport
refusal remains intact; API artifacts were rebuilt. Attempt3 passed both installed
variants (6 acceptance/regression cases, zero skips) in 300.61 seconds.

A changed-only-runtime regression also failed before the API image source/content
proof was extended to include the new runtime distribution. The Node evaluation
image/offline contents are preserved. API build CA mounts are optional and
portable; no session CA is copied into image layers or TLS verification disabled.

## Final local gates (2026-10-15 UTC)

Tested source is clean commit
`c74f864074a577110c31d57cb7d465aa2417f698`; later CI full-app-import wiring and
this evidence update change no shipped API/SDK/runtime/package bytes. One final
PostgreSQL-enabled `npm run check` under the documented normal-host reaper passed,
including installed smoke, backend and both Python3.14 package suites. Counts
overlap with focused runs and are not added together.

| Gate | Actual outcome |
| --- | --- |
| Root Node suite | 105 passed, zero skips |
| Complete backend / verified PostgreSQL | 710 passed +97 subtests, zero skips, 625.45s; one existing Starlette/AnyIO deprecation warning |
| SDK Python3.14.5 | 1472 passed +68 subtests, zero skips, 94.16s |
| Runtime Python3.14.5 | 623 passed, zero skips, 29.18s |
| SDK Python3.13.13, separate environment | 1472 passed +68 subtests, zero skips, 93.32s |
| Runtime Python3.13.13, separate environment | 623 passed, zero skips, 30.83s |
| Initial API/CLI/library smoke | Direct wheel and independently rebuilt sdist-wheel passed lifecycle/API assertions with zero PostgreSQL skips; its initial literal zero-effect metric was rejected in independent review and is superseded by correction round1 live observation |
| Schema export, lint, v1/v2 contract/schema checks, frontend build | Passed |
| Deployment checks | 38 passed +13 subtests |
| Strict MkDocs1.6.1 build | Passed with no strict warnings |
| Backend dependency check | 75 compatible packages |
| SDK/runtime artifact dependency checks | All eight distinct direct/sdist environments across Python3.13/3.14 compatible |
| Exact API image | Built; offline full FastAPI app/runtime-route import, noneditable metadata, dependency consistency and exact source-tree hashes passed |

Local tools: uv0.11.16, Node24.21.0, npm11.9.0, PostgreSQL17.11. The existing
independently verified localhost test server was retained. API image Python3.14.7
contains44 compatible packages, SDK0.2.0 and runtime0.1.0; image identity is
`sha256:450978edb99c90bc905af6ea4796edc9e431eb73d26e60b219edd589f3058830`.
All ten exact diagnostic/probe container IDs were confirmed absent; image retained.
The external uv probe initially tried the nonroot image user's unavailable cache;
`--no-cache` qualified it without changing image permissions. A full-app probe's
first route inspection assumed flattened FastAPI routes; public URL lookup
qualified the current lazy router representation. Both failed helper receipts
remain alongside successful outputs; neither required a product change.

Actual final installed-smoke SHA256 identities:

| Distribution | SHA256 |
| --- | --- |
| SDK0.2.0 wheel | `ed5aeaa0fd4cc417eaa4a9ba69627e5dccb7c9e000e4716397a4d2ea5e216118` |
| Runtime0.1.0 direct wheel | `198431053d72d4e77540936c6dc9b45a30e608165901884dc255a50df737dd0e` |
| Runtime0.1.0 sdist | `1571725270b9b9d44cbab4caf004bd2d378c3e5cf113ce048ddb473a8572fae5` |
| Runtime0.1.0 independently rebuilt wheel | `198431053d72d4e77540936c6dc9b45a30e608165901884dc255a50df737dd0e` |
| API0.2.0 wheel | `09fa657f1d467ac5ae2d680053ad254bc55f70feb0591bbd604d411fc2891792` |

Equal wheel digests retain their distinct independent build origins. Frozen-source
and per-artifact manifests, installed-origin/namespace/process receipts and actual
safe tested outputs are retained in the controller's Task9 report/evidence set.
The source suite's natural lease wait is real; forced startup recovery does not
claim natural ten-minute renewal. Root owns fresh task/whole-branch reviews and
exact-head hosted CI/publication. No merge, deployment or public package release
was performed by this qualification task.

## Remaining customer and production work

E03 provides identity/connection/integration contracts, not production business
execution. E04/E05 retain execution/actions/approvals/reconciliation; E08 licensing
and distributed entitlements; E09 production/customer qualification. Studio/UI,
enterprise and cross-industry services remain active. The agent-first rationale
remains in `docs/AGENT_PRODUCT_RATIONALE.md`; Automation Anywhere is a reference.

Production recovery/rollback witness, independent current grants, secret/signing
custody, deployment-record/dispatch/trust providers, customer network allowlists,
host isolation, capacity/load, production migration/restore and uncertain-effect
reconciliation remain separately qualified. Customer workflow/autonomy, provider,
custodian and acceptance inputs have not been supplied. Reconstructed v2–v11
fixtures are not actual historical deployment snapshots; unknown/customized
schemas need reviewed forward repair. No destructive downgrade, automatic witness,
exactly-once external effect or memory zeroization is claimed. A revoked oldest
admission may block later polls until expiry.


## Correction round1 qualification

Independent review found that the initial worker/sink exited before restore and
its reported zero was literal. The revised installed worker retains the live
receiver across three actual guarded effect attempts: fenced restored proof,
old proof after epoch advance, and fresh-epoch identity without renewed business
grants. Backend Dispatch and worker authority read one private independent grant
source outside restored rows. API proof is checked first. A real verified TLS
handshake confirms receiver liveness during every denial. Linux IN_OPEN watches
measure actual secret-version-file opens without reading or exposing values.
The positive send observes one request and at least one secret open; all denied
phases must preserve both counts, and the report derives its delta by subtraction.

The outer deadline now terminates the owned smoke controller cooperatively and
waits boundedly. The controller forwards cancellation to its active artifact
harness or build/install process group, waits for cleanup and retains process,
output and schema receipts. Escalation targets only exact owned groups; missing
resource confirmation produces an explicit failed outcome. A real PostgreSQL/API
interruption case independently checks process-group absence, closed API port and
owned namespace absence. A fixed build-descendant timeout probe checks the same
supervision mechanism. These changes are test-only; shipped API/SDK/runtime and
its previously qualified image bytes remain unchanged. Correction receipts and
actual focused final results are recorded in the retained Task9 report.


Correction round1 was tested from clean
`07f28ce9f108c6898e741c630dd65bbad84ca6f5`: the full affected acceptance file
passed12 tests, zero skips,326.80s, with the existing Starlette/AnyIO warning.
Both full ordinary CLI journeys and both isolated restore consumers passed direct
wheel and rebuilt-sdist-wheel paths. Each live receiver observed1 positive request
and1 secret-version open; all three denial phases kept both counts at1, deriving
1−1=0 new effects. The active API outer deadline produced a cancelled outcome with
independently confirmed cleanup. Five exact namespaces/harness groups, three outer
controller groups and the inert build-probe group were independently absent.

The final result update changes documentation only; all tested correction code
matches07f28ce. Package wheel/sdist digests and shipped API/SDK/runtime/image bytes
match the c74 foundation evidence. Failed correction attempts remain recorded,
including a missing test import whose exact controller was stopped and whose
namespace/harness were cleaned. That failed attempt's terminated controller remains
a non-executing PID1-owned zombie; its PID absence is not claimed. The successful
correction interruption cases have no remaining process groups or schemas. Fresh
independent review and hosted exact-head CI remain separate root-owned gates.

## Correction round2 qualification

Fresh review found that leader exit and capture-pipe EOF could bypass escalation
while a TERM-resistant child remained in the owned group. The test supervisor now
uses one grace deadline for leader collection and independent group disappearance,
then sends KILL to a surviving exact group and bounds forced confirmation. Missing
absence or resource proof still records cleanup as unconfirmed.

Clean source `f409f9a78d08a7878ec225db4d2ea5cfbe837b64` passed the scoped
acceptance run:14 passed, zero skips, one deliberately deselected full CLI journey,
28.99s, with the existing Starlette/AnyIO warning. Three isolated subreaper probes
cover timeout, cancellation and successful leader exit with a TERM-resistant
descendant on closed capture pipes; each escalated, reaped its exact child and
confirmed group absence. Both installed restore artifacts retained receiver and
secret-open counts1→1 across API/API/dispatch denials. The active API/PostgreSQL
cancellation remained cooperative and confirmed its resource cleanup. Independent
checks confirmed three owned namespaces and37 recorded process groups absent.
The unchanged full CLI timing evidence remains attributed to07f28ce; shipped
code, image and distribution hashes remain unchanged. Original failed probes and
the previously disclosed PID1-owned zombie remain recorded separately.
