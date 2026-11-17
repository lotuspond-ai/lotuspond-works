# Package SDK implementation progress — 14 October 2026

The owner approved the [implementation plan](../superpowers/plans/2026-10-14-package-sdk.md) and chose subagents with independent review after each task. Work proceeds in an isolated `codex/package-sdk` worktree from `c2d3e3685db79f37758095403b3bab4a94212e32`; the original checkout, draft readiness PR #3, archive and local test data are preserved.

The scope is the approved standalone SDK/diff/detached trust/declared compatibility subsystem. Executable package semantics, runtime authority, live compatibility and the broader production qualification register remain open. No package-index publication, production deployment or customer-system access is implied.

## Task evidence

All five implementation tasks have completed independent specification and quality review. The evidence below records their red/green tests, corrective reviews and final local integration. The reviewed SDK is published in draft PR #4 with exact-head hosted CI recorded below; implementers do not spawn additional agents or publish.

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

## Task 3 — detached signatures and independent trust

The SDK exposes `sign_package`, `verify_package`, `SignatureEnvelope`,
`VerificationReport`, `TrustedKey`, `TrustSet`, `generate_keypair`,
`load_signing_key` and `write_new`. Detached Ed25519 signatures bind the fixed
package context, strict metadata, sorted ASCII header and exact canonical UTF-8
content through uint64 big-endian length framing. Trust is externally provisioned,
capped at 64 records and rejects extras, duplicate IDs/public keys, mismatched
fingerprints and noninteger format versions. Unknown and revoked keys cannot
verify. Reports preserve `contract_valid=True`, `compatibility_status=not_checked`
and exact `authority_granted=False`; verification grants no execution authority.
Mutable typed records and serialization are revalidated.

The initial focused tests failed on three missing interfaces. The package fixture
was independently signed with OpenSSL 3.5.7 using the public RFC 8032 vector-1
test key: 40 context/NUL bytes, 361 header bytes, 1,166 canonical bytes and 16
length bytes produce a 1,583-byte frame. RFC known-message bytes, committed literal
signature expectations and mandatory OpenSSL checks in both directions pass.
All key material is synthetic. Provenance and source links are in the fixture
README; missing OpenSSL fails rather than skipping Linux CI cross-verification.

Local cryptography 50.0.2 accepted an identity-key forgery. The controller approved
a bounded trust-encoding guard using all seven libsodium 1.0.18 published
small-order y encodings under both sign bits and the canonical y < 2^255-19 bound.
The 14 blacklist variants, 38 noncanonical variants and identity-forgery case
were red before the guard, then green. No curve arithmetic or new crypto
dependency was introduced; this is not a full subgroup validation or an assertion
of equivalence to Rust `verify_strict`.

The controller also superseded the original pathname-cleanup instruction:
publication failures now preserve created outputs and concurrent replacements,
raising fixed `output_incomplete` for standalone writes or `keypair_incomplete`
for key generation. No failure path unlinks files. Nine revised file cases failed
against cleanup behavior before this correction. New outputs are exclusive,
complete-write/fsynced on success; private PEM is owner-only 0600, current-user
owned, no-follow and bounded at 4096 bytes. FIFO/symlink/replacement, wrong-owner,
permissions, malformed/encrypted/wrong-algorithm, short/zero/partial writes,
second-file collisions and fsync/close failures have explicit coverage. Separate
red/green checks corrected unsanitized close errors and nonboolean private flags.
Two-file publication is not crash-atomic; retained partial outputs require
operator inspection in an operator-controlled directory.

Final focused verification passed **125 tests**, with no skips. Complete SDK
verification passed **208 tests and 68 subtests** on Python 3.14.5 and isolated
Python 3.13.13, including wheel/sdist installation and independent OpenSSL checks.
Frozen dependency sync, Ruff, whitespace checks and strict documentation build
passed. The first documentation-tool attempt hit a read-only global tool directory;
`uv run --no-project --with mkdocs==1.6.1` built successfully to `/tmp`. The isolated
Python 3.13 install used uv's normal full-copy fallback across filesystems.

The Task 2 README topology nit is resolved by loading the reviewed-action example
explicitly before indexing its agent node. Independent Task 3 specification/crypto
and quality review, application/database/image integration and hosted CI remain
controller-owned. No push, merge, package-index publication, production/customer
access or broader integration check was performed by this implementer.

### Task 3 review fix — formatted serialization cap

Independent review found one Minor spec gap: caller indentation could expand a
compact-valid record beyond 8 MiB because `_StrictRecord.model_dump_json` checked
only the complete compact record. The fix retains complete-record revalidation,
ASCII safety and the full-record precheck, then also checks the actual rendered
JSON before returning it. Oversized formatted output raises `report_too_large`.

Five regressions failed before the fix: the reviewer's actual 8,389,129-byte
indented envelope, plus formatted output limits for envelope, verification report,
trusted key and trust set. The four smaller boundary cases also verify exact-limit
acceptance and that field selection cannot bypass the full-record precheck.
All five then passed. Scoped signing/trust verification passed **103 tests**,
zero skips, on Python 3.14.5 and isolated Python 3.13.13; SDK Ruff and whitespace
checks passed. No unrelated application, database or artifact checks were repeated.
Independent re-review remains controller-owned.

### Task 3 independent review closure

Independent review of `653dab2..a6cfa7c` passed the signature framing, strict
external trust, degenerate-key guard provenance, RFC/OpenSSL evidence and safe
file requirements. It found one minor specification gap: caller-controlled JSON
indentation could exceed the 8 MiB rendered-output cap. Fix `0c71e0b` checks actual
rendered output as well as the complete-record precheck. Five regressions failed
before the repair and passed after; 103 signing/trust tests passed on both
Python 3.13 and 3.14. Fresh scoped review passed specification and quality with no
remaining findings. The earlier README fixture nit is also confirmed resolved.

Two execution safety decisions are incorporated into the plan and documentation:
retain incomplete/replaced output on write failure instead of unsafe pathname
cleanup (operator inspection may be needed), and reject known degenerate or
noncanonical provisioned trust keys using established encoding guards (those
keys must be replaced). Neither changes historical package bytes or grants
execution authority. Final whole-branch artifact/application/CI checks remain
the shipping gate.

### Task 4 — strict synthetic capability comparison

Implemented `check_declared_compatibility` with required synthetic profile and
explicit platform requirements, strict typed records and safe bounded reports.
Comparison checks current schema/canonicalizer declarations, every node kind,
connector exact version and every declared operation, model exact revision, and
explicit OS/architecture/capabilities. Unused connector/model/operation declarations
are checked. Unknown metadata, missing/extra fields, duplicates, conflicting
platform declarations, wildcard-like inputs and mutated typed records are rejected.
Controller clarified natural keys as (connector, version), (profile, revision) and
(os, architecture); explicitly listed multiple versions/revisions are allowed,
without operation sharing between versions or merging duplicate platform pairs.

The first focused run failed collection for the intentionally absent public
`CapabilityProfile` interface. After implementation, all 82 initial behavioral
cases passed. Clarification and self-review coverage added four cases for explicit
multiple versions/revisions, constructed/nested typed mutations, hostile JSON
shapes and strict report consistency; **86 focused tests pass**. Assessment digests
bind normalized profile/requirements, while golden v1 bytes and package digest stay
unchanged. More than 20 sorted refusal reasons preserve incompatibility and report
truncation. No supplied instructions, identifiers, revisions or credentials appear
in report reasons; signature status remains `not_checked`, assurance is always
`synthetic-declaration`, and authority is always false. Reused report serialization
checks both the complete record and actual formatted JSON against 8 MiB.

Complete `npm run check:sdk` passed frozen sync, Ruff and **299 tests plus 68
subtests**, zero skips, on Python 3.14.5. An isolated frozen Python 3.13.13
environment passed the same suite, including existing wheel/sdist installations
and OpenSSL checks; uv used the ordinary full-copy fallback across filesystems.
Strict MkDocs build to `/tmp/lotuspond-task4-docs` and whitespace checks passed.
Synthetic example files, SDK README, repository README and SDK guide document
explicit inputs, exact matching, bounded sanitized reasons and assurance limits.
No application/database/artifact checks, pushes, deployment or external package
publication were performed. Independent task review and final integration remain
controller-owned.

### Task 4 independent review closure

Review of `5c53335..0ec070d` passed specification and quality with no findings.
All declared connectors/operations/models are checked, exact platform requirements
are explicit, and truncated refusal reports cannot become compatible. Capability
natural keys include exact versions/revisions, allowing explicitly listed support
for several versions while refusing duplicate pairs and conflicting platform
entries. This clarification preserves exact matching; restricting future profiles
to one version per name would require a separate policy change. The SDK remains
synthetic and non-authoritative. Final CLI, capability/changelog integration,
application/database/artifact checks and hosted CI are still pending.

### Task 5 — complete bounded CLI and installed journeys

The SDK and legacy CLI now share validate, digest, canonicalize, diff, keygen,
sign, verify and compatibility commands. Historical validation JSON and v1 bytes
are unchanged. Auxiliary reads use explicit role byte limits, the shared JSON
checks and strict models; stdout is ASCII-safe, capped at 8 MiB including its
newline, and errors retain at most 20 value-free entries. Argument/input/format
failures exit 2; well-formed trust refusals and synthetic incompatibility exit 3.
All canonical/signature/key files use explicit exclusive outputs, private files
stay owner-only, and incomplete publication remains for operator inspection.

Initial installed tests against the Task 1–4 base failed for unsupported commands:
8 failures and 14 fixture setup errors. A clean focused red run recorded 4 failures
for missing digest/canonicalize dispatch, help returning from main, and CLI report
bounds. Existing legacy validation passed 12 tests and 12 subtests before dispatch
changes. Initial green passed 24 CLI tests across both artifact origins. Expanded
coverage includes every package input bound, auxiliary malformed/oversized/FIFO
inputs, wrong-algorithm/unsafe/symlinked keys, duplicate/ambiguous trust, revoked
and cryptographically invalid signatures, output preservation, partial keypair
publication and sanitized errors. A unit fsync failure checks retained standalone
output; the installed normal/refusal journeys use real subprocesses and files.

Frozen Python 3.14.5 and isolated Python 3.13.13 each passed the full standalone
suite: **339 tests plus 68 subtests**, zero skips, including RFC 8032 and independent
OpenSSL cross-verification. Each run independently built and installed a direct
wheel and an sdist-derived wheel into separate environments outside the checkout.
The installed import probe checks package location, py.typed and absence of API,
DB and HTTP-client dependencies. Each origin runs local validation/digest/
canonicalization/diff, synthetic key generation/signing, external trust provision,
verification/mutation/revocation and full/partial capability declarations.

Focused backend legacy CLI/import/contract parity passed **35 tests plus 88
subtests**, including identical canonical bytes and signature envelopes, allowed
verification and revoked-key refusal through both module entry points. The only
warning is the inherited Starlette BlockingPortal deprecation. Frozen Ruff,
package-schema parity, strict MkDocs and git whitespace checks passed. Final
installed CLI/distribution subset reruns after the README metadata wrap passed
**41 tests on each Python version**, zero skips. uv used its
ordinary cross-filesystem full-copy fallback for the Python 3.13 environment.

The SDK README/guide, root README, capability register, changelog, current
AGENTS/CLAUDE ownership guidance and a dated ADR-0009 addendum now describe the
local tooling and preserve E02/runtime/production gates. The Task 2 reviewed-action
README fixture nit was already resolved at the task base and remains correct.
The existing CI matrix runs the complete installed journeys; no hosted result is
claimed here. Exact commands/logs and the local task commit are recorded in
`.superpowers/sdd/2026-10-14-package-sdk/task-5-report.md`.

Per the final execution split, final full npm/application checks, independently
verified loopback DB, deployment checks, actual API image, offline installation/
failure-recovery and content proof, all independent reviews, publication and
exact-head hosted CI remain controller-owned gates. No DB, production/customer
access, push, merge, deployment or public package publication was performed by
the Task 5 implementer.


### Task 5 independent review and final local integration

Task5 source `d94aa62f6d006223e861ce3a181d53eb83bd2344` passed independent
specification and quality review of `bf92875..d94aa62` with zero findings.
All five task reviews are complete; the separate whole-branch review also passed as recorded below.

Final `npm run check` passed 105 Node tests, 299 backend tests plus 97 subtests
(39 database-dependent skips), 339 SDK tests plus 68 subtests, lint/schema parity
and the production UI build. This cloud used the saved serial Node wrapper and
subreaper. Separately identity-verified loopback PostgreSQL passed **338 tests
plus 97 subtests, no skips**. The only warning is the inherited Starlette
BlockingPortal deprecation. Deployment checks passed 36 tests plus 13 subtests.

The actual API image from `d94aa62` is
`sha256:f2c9b56731b923da9f95b3a54794e0c63b6ba7ca3e90d6f1e8be08ae1be2ddb2`.
The local build used the previously documented build-only cloud CA secret with
TLS verification enabled; the ordinary Dockerfile remains a hosted CI gate.
The unchanged collector image was reused after verifying no collector delta.
The immutable offline bundle SHA-256 is
`a03c29c52d73dafe8c0db799c4bae19d93376e385b737e51d8e0e89a3a116cc1`.

Offline installation and failure-recovery passed in **202.2 seconds**: schema12,
SDK import/type identity and the historical digest, MFA/enrollment/revocation,
worker crash/backlog recovery and Redis outage/recovery. Networking was internal
with zero published ports and synthetic data only. Cleanup confirmed five
containers and three anonymous volumes removed. Content proof matched all three
embedded source trees: backend, SDK and static UI. The inherited proof helper
provides content hashes with null Git provenance and a stale no-HEAD note; source
SHA is separately recorded by the controller, without claiming signed Git provenance.

Local logs are `/tmp/lotuspond-sdk-final-{full-check,database,deploy-tests}.log`;
image/bundle/recovery/proof reports are in `/tmp/lotuspond-sdk-final.ayKchm`.
These are local reproducible checks, not hosted CI, customer acceptance or a
production deployment. No product source changed during final checks.


### Independent whole-branch review

The strongest available review model independently reviewed the complete immutable
`c2d3e3685db79f37758095403b3bab4a94212e32..d94aa62f6d006223e861ce3a181d53eb83bd2344`
range. Specification **PASS**, code quality **APPROVE**, zero Critical, Important
or Minor findings. Exact comparisons confirmed the contract, decoder (except its
relative import) and committed schema are unchanged. One narrow fresh probe
injected unknown Pydantic extra fields into envelope, trust set and nested key
instances; all were rejected with the correct fixed errors. The reviewer inspected
actual final recovery/content-proof reports. No corrective source change is needed.
There are no open, parked or deferred SDK findings. Publication and exact-head
hosted CI results are recorded separately below.

### Controller decisions and cost if wrong

1. The controller owns Task5 reviews and draft publication so implementers do not
   spawn duplicate reviewers. Cost if wrong: review scheduling/PR organization
   rework; no production effect.
2. The Unicode regression uses the historical fixture name, `Synthetic reviewed
   action example`, rather than the plan's shortened example. Cost if wrong:
   correcting a fixture assertion; canonical bytes remain unchanged.
3. A failed output/keypair publication retains created partial files because an
   inode comparison followed by pathname unlink has a race. Cost if wrong:
   operators must inspect/remove their partial outputs before retrying. Existing
   files remain refused and preserved.
4. Trusted public keys reject known small-order/noncanonical Ed25519 encodings
   using attributed established constants, after an identity-point forgery was
   reproduced against the crypto library. Cost if wrong: an affected provisioned
   key must be replaced; ordinary generated/RFC/OpenSSL keys remain compatible.
   This is not full subgroup validation or Rust `verify_strict` equivalence.
5. Capability identity includes connector version, model revision and the exact
   OS/architecture pair, permitting explicit multiple versions without merging
   duplicates. Cost if wrong: future policy may restrict one version per name;
   current matching still requires exact declarations and grants no authority.


## Draft publication and exact-head hosted verification

[Draft PR #4](https://github.com/lotuspond-ai/lotuspond-works/pull/4) targets the preserved
readiness branch `codex/production-readiness-20261014` at
`c2d3e3685db79f37758095403b3bab4a94212e32`. Its initial published head
`219e54b72fbb0a556a8c3e5051f91b4e4b6026d6` passed **all twelve GitHub jobs** in
[run 38523799089](https://github.com/lotuspond-ai/lotuspond-works/actions/runs/38523799089):
validation/application/database/supervised runner, SDK3.13, SDK3.14, collector,
monitoring rules, voice, evidence, protocols, execution, restore, offline install
and docs. The ordinary Dockerfile and actual hosted offline recovery passed,
without the local cloud CA or serial Node adaptations.

The remote branch/head/base and all 58 changed paths matched the local reviewed
branch. Runtime/tests/dependencies remain exactly at reviewed `d94aa62`; later
commits only record planning/review evidence. This completion record is likewise
documentation only. The PR records the current head's own CI result; an earlier
pass is never substituted for it. The branch and worktree remain for review.
No merge, package-index release or production deployment was performed.

The five-task SDK plan is complete. Local artifact and detailed review reports
are preserved at `/tmp/lotuspond-sdk-final.ayKchm`, with independent reports in
its `review-evidence` subdirectory. Only this completed plan's ignored working
scratch is eligible for cleanup; source worktrees, branches, original data and
recovery material remain intact.

The next executable-v2 specification is separately proposed in
[draft PR #5](https://github.com/lotuspond-ai/lotuspond-works/pull/5). Written-spec review
is pending under the selected Superpowers workflow; no v2 implementation or
product dependency installation is part of this SDK change. All broader product
and production-qualification requirements remain open until their evidence exists.
