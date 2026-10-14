# Package SDK implementation progress — 14 October 2026

The owner approved the [implementation plan](../superpowers/plans/2026-10-14-package-sdk.md) and chose subagents with independent review after each task. Work proceeds in an isolated `codex/package-sdk` worktree from `c2d3e3685db79f37758095403b3bab4a94212e32`; the original checkout, draft readiness PR #3, archive and local test data are preserved.

The scope is the approved standalone SDK/diff/detached trust/declared compatibility subsystem. Executable package semantics, runtime authority, live compatibility and the broader production qualification register remain open. No package-index publication, production deployment or customer-system access is implied.

## Task evidence

Implementation has started with isolated dependency installation and baseline checks. Each completed task will record commits, meaningful red/green tests and independent specification/quality review. The controller coordinates whole-branch review and draft PR publication after the final task; implementers do not spawn additional agents or publish.

## Task 1 — standalone extraction

The historical example's exact canonical bytes were frozen before extraction from
baseline `753b0658730a5ac1f5ffd47afab89f30b0d7374e`. SHA-256 remains
`305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3`.
The new focused tests first failed on the missing `lotuspond_sdk` module. A separate
content-proof regression failed because SDK source was not included in the proof.

The extracted pure validator/decoder share types and behavior with backend
compatibility modules. Focused SDK, distribution and backend parity tests,
frozen sync, schema byte parity and deployment proof unit tests are recorded in
the Task 1 implementation report. Local SDK suites passed 40 tests and 68 subtests
on Python 3.13.5 and 3.14.5; focused backend checks passed 40 tests and 80 subtests
(with one existing Starlette deprecation warning), and deployment unit checks
passed 36 tests and 13 subtests. Ruff and schema parity checks passed.
Wheel and sdist-derived wheel tests run from
fresh environments outside the checkout and reject API dependencies.

The brief's sample Unicode test named `Reviewed action example`; the existing
example actually names `Synthetic reviewed action example`. Its replacement
needle was corrected to match the independent fixture without changing bytes.
An initial copied SDK schema test also needed its repository-root path adjusted.

Controller-owned independent review, full application/database tests, actual API
image build/content proof and airgap smoke remain pending here. CI includes the
3.13/3.14 SDK matrix; no hosted CI, publication, production deployment or customer
qualification is claimed by local tests.

### Task 1 review fix I1 — JSON item-budget regression

Independent review identified missing standalone item-limit coverage. Four added
cases freeze the 10,000-item limit: flat and nested programmatic JSON pass at
exactly 10,000 items and reject 10,001 with `input_too_complex`; package-shaped
raw input and a mutated model reject excessive operations with that same code.
All cases stay shallow and below the byte limit.

The four cases passed with the existing guard. Temporarily disabling all four
item-budget rejection sites made all four cases fail: programmatic overflows
stopped raising, while package/model inputs returned the schema `too_long` error
instead. Exact contract source bytes were restored and checked against fix base
`1d317ad8fc9260f56daf9749968b3e4941b844a3`. Focused contract/input verification then
passed 38 tests and 68 subtests; SDK Ruff and whitespace checks passed. No v1
semantics or committed schema changed; broader integration remains controller-owned.

## Task 1 independent review and integration

Task 1 review confirmed identical contract/schema/digest behavior and required one missing item-budget regression. Fix `a60a8d5` adds four boundary/overflow cases; each fails when the preserved guard is temporarily disabled, and all pass after restoring identical source bytes. Scoped re-review passed specification and code quality with no remaining findings.

Controller verification on implementation `1d317ad` passed full checks (105 Node, 298 backend +89 subtests with 39 DB-only skips, 40 SDK +68 subtests), separately identity-verified loopback PostgreSQL (337 +89, no skips), and strict documentation build. The test-only follow-up passed 38 contract/input tests +68 subtests and Ruff; no runtime change required repeating unchanged integration tests.

The actual API image and immutable offline bundle passed installation and worker/Redis failure-recovery in 210.4 seconds on internal-only networking, zero published ports, synthetic data only. SDK import/type identity and historical digest passed inside the image without a checkout. Source-content proof matched backend, installed SDK and static UI. Five drill containers and three anonymous volumes were removed with confirmed cleanup.

The cloud's default Buildx config directory was read-only; a workspace-local Buildx directory resolved it. The unmodified Dockerfile initially failed the cloud proxy certificate check; a local derived Dockerfile supplied CA certificates through build-only secret mounts with TLS verification enabled. Product sources and image trust stores were unchanged. Final hosted CI will independently exercise the ordinary Dockerfile. Logs and artifact reports are local test evidence, not deployment or public distribution.

## Task 2 — deterministic private package differences

The SDK now exposes `diff_packages`, `PackageDiff` and `PackageChange`. Both inputs
use the preserved contract validation and digests. Declaration identity and
relative common-element order are compared independently: reversing nodes while
editing instructions reports both changes with the actual normalized pointers.
Default serialization contains identifiers, categories, operations, paths and
digests, with no value payloads. Explicit boolean `include_values=True` enables
potentially sensitive local detail. Non-boolean flags, including `"false"`, fail
closed with `TypeError`. Strict report models revalidate mutable instances; JSON
is ASCII-safe and capped at 8 MiB with fixed `report_too_large` errors.

The initial focused run failed on the absent diff exports. Independent literal
expectations cover every category, all declaration/edge/operation sequence types,
add/remove and reorder coexistence, kind replacements, normalized defaults,
privacy, unchanged source inputs, invalid/mutated inputs and the exact normalized
1,048,576-byte boundary. One operation-order expectation was corrected to follow
the approved sort tuple, where an absent before path sorts as an empty string.
Separate red/green checks found and fixed Pydantic's acceptance of integer `0` as
`Literal[False]` and nonfinite detail numbers that would otherwise serialize as
null. Redacted and detailed oversized reports are rejected without dropping records.

Local `npm run check:sdk` passed frozen sync, Ruff, **83 tests and 68 subtests** on
Python 3.14.5, including installed wheel/sdist checks. The full SDK suite also
passed **83 tests and 68 subtests** in isolated Python 3.13.13. The 39 diff cases
have no skips. Strict MkDocs build passed with output under `/tmp`.
Independent Task 2 review, whole-application checks and hosted CI remain
controller-owned; no database access, publication or runtime authority was added.

Task 2 independent specification review passed; quality approved with zero critical/important findings. One documentation nit remains for the final documentation pass: identify the reviewed-action fixture before an example indexes its specific topology. No runtime findings remain open for this task.
