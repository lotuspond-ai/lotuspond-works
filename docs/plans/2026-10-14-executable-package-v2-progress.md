# Executable package v2 implementation progress

The owner approved the specification and ten-task plan on 14 October 2026 and selected subagent implementation with independent review after every task. Implementation starts from approved design commit `468550703a45c702995391c5d93ac201283d4a50` in `codex/executable-v2`, preserving the original checkout and the completed SDK/design branches.

The new worktree passed the inherited SDK contract/input baseline: 38 tests and 68 subtests on Python 3.14.5. The prior SDK has its own complete review, application/database, installed-artifact, offline-recovery and hosted-CI evidence. These baseline results do not establish any v2 implementation or runtime qualification.

All ten tasks are complete after independent task reviews, whole-branch corrections, local application/database/artifact verification and draft publication. All twelve hosted jobs passed on published head `5b43e64acbd9e818e979d1cd034479ef9a48d66d`; the PR records checks for subsequent evidence-only commits. The complete approved product requirement register remains open; this subsystem delivers executable definitions/compiler and runtime-facing contracts, with later engine/runtime/Studio/commercial/operational goals retained.

## Task 1 initial implementation evidence

Bounded executable JSON/JCS, fixed diagnostics, exact known-model projection,
strict fixed-field guards and ASCII report helpers are implemented in the new
namespace. SDK metadata is 0.2.0; both locks add only exactly pinned
`rfc8785==0.1.4` and the SDK metadata/dependency changes. The reviewed Apache-2.0
sdist hash matches
`e545841329fe0eee4f6a3b44e7034343100c12b4ec566dc06ca9735681deb4da`;
installed canonicalizer source also matches that reviewed source.

The specified focused command first failed with two missing-namespace collection
errors, then passed 105 tests. Self-review reproduced and fixed unsafe integer
field admission (including optional integers) and constructor-bypassed limits;
normalized defaults, report wrappers/newlines and ASCII expansion have bounds
coverage. An executable test-package marker resolves the same-name inherited
`test_input.py` collection collision. The controller authorized the existing
installed distribution assertion to track SDK 0.2.0.

Final required scoped checks pass on Python 3.14.5 and separate Python 3.13.13:
181 tests and 68 subtests on each, with no skips. This includes focused v2 tests,
inherited contract/input/signing and installed wheel/rebuilt-sdist checks.
A separate one-off installed probe imports the new namespace, verifies the exact
JCS dependency and tests decoder/JCS numeric/order behavior from both artifact
origins on both interpreters. The independent Node oracle passes all 26 RFC
numeric vectors plus UTF-16/numeric-key/Unicode checks. Frozen SDK Ruff passes.
Historical v1 schema parity, 1,166 bytes and digest
`305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3` remain pinned.

This is local Task 1 evidence; no full application/database/offline matrix,
provider execution, authority or production qualification is claimed. Later
v2 authoring/compiler/wire/CLI APIs remain separate tasks. The controller owns
review acceptance and task/plan completion state.

### Task 1 review correction I1 implementation evidence

Independent review reproduced a full-cap canonical round trip refused by the
projection's intermediate Python numeric spelling. The correction separates
actual raw-byte admission, structural projection and actual normalized/output
measurement. Projection retains all type/Unicode/cycle/depth/item/raw-integer
checks and a structural byte lower bound; it does not choose numeric domains.
`validate_model` measures complete JCS after field validation/default insertion.
`report_bytes` shares model revalidation but measures its own complete ASCII
output, including newline, without imposing a different numeric representation.
No approved cap, dependency, v1 boundary or root SDK API changed.

The initial focused correction tests observed 6 failures and 105 passes. Separate
red checks reproduced the mixed raw-number interaction, normalized/default byte
expansion and full-cap ASCII report interaction before their corrections.
Final focused tests pass on Python 3.14.5 and separate 3.13.13: 115 tests on each,
no skips. Fresh wheel and independently rebuilt sdist-wheel direct v2 probes
pass on both interpreters. Frozen SDK Ruff and diff whitespace checks pass.
The correction and exact commands/logs are recorded in the implementation
report; independent review and completion state remain controller-owned.

## Task 1 review closure

Task 1 is complete through `897484cc2bbfaf2add68552731c3926430d6895f`. Independent review found and reproduced one Important exact-byte-boundary defect; correction and scoped re-review passed with no remaining findings. The final 115 focused tests pass on both Python 3.13.13 and 3.14.5. Corrected wheel and independently rebuilt-sdist probes pass on both interpreters, and reviewer inspection confirms every retained executable module matches reviewed source. Earlier unchanged v1 regression evidence remains recorded above.

Projection now provides only a structural byte lower bound. Actual raw JSON, normalized/default JCS and final ASCII reports each enforce their own complete byte limits. Later model validators must select declared numeric domains before complete normalized measurement. Required full-cap, mixed numeric, default-expansion and actual overflow regressions are present; no package limit was raised.

Ruling: update the inherited installed-distribution version assertion with Task 1's approved SDK 0.2.0 bump, retaining broader installed v2 CLI journeys for Task 10 — otherwise the required existing artifact gate would knowingly fail — cost if wrong: moving one test-line change between task commits, with no runtime or compatibility change.

Tasks 2–10 and final application/database/artifact/hosted gates remain pending. This does not qualify an execution runtime or the complete product for production.

## Task 2 initial implementation evidence

Closed data/export schemas, bounded literal validation, lexical mapping inference,
optional-presence checks and compiler-only handle provenance are implemented in
`executable/types.py` and `expressions.py`. The fixed operation vocabulary is
static: no interpreter, constant folding or runtime authority was added. Public
data cannot declare or construct handles. Numeric literals select their declared
domain before normalized JCS measurement, including integral spellings of large
finite numbers and the largest finite IEEE value.

The prescribed initial red command produced two missing-module collection errors.
Subsequent red/green regressions corrected alias origin changes, first-element
array summaries, positional/multiplicity loss, optional cross-field selection
correlation, definition resolution in step outputs, empty handle arrays and
nested iteration ownership. Literal 1.0, negative zero, 1e20, its neighboring
representable float and the largest finite float round-trip through decoding,
typed normalization and JCS with stable bytes/digests. Exact expression 64/65-node
and 8/9-depth boundaries are tested separately. The public import also passes
with UserWarning treated as an error; only known declaration-time schema-shadow
warnings are suppressed in tightly scoped contexts.

Final focused Task 2 tests: 90 passed. Task 1 plus Task 2: 205 passed on each of
Python 3.14.5 and separate Python 3.13.13, with no skips or warnings. Frozen SDK
Ruff and whitespace checks pass. The first broader SDK run had 505 passed,
38 fixture errors and 68 subtests: all errors shared the Hatchling fetch blocked
by sandbox networking. An authorized network-enabled repeat of the affected
wheel/rebuilt-sdist CLI and distribution tests passed all 41 tests; no product
or fixture change was required. The failed logs are retained with the task report.

Controller-approved provenance representation extension: concrete constructor
arrays retain immutable `elements`; unknown selections retain ordered,
multiplicity-bearing `OriginChoice` options, including absent optional slots.
`select_array_item` and `iteration_item_flow` preserve alias identity and only
bind occurrence slots owned by `FlowArrayType.iteration_id`. Empty handle arrays
have no item origin. The detailed helper contract and caller obligations are in
the guide and Task 2 report; the controller owns the ruling and review gate.

Later package validators must call `parse_mapping(raw, definitions)` to normalize
literal numbers before Task 1's final JCS measurement. Later compilers must supply
lexical scopes, apply direct exists guards only on valid true edges, originate
handles only from validated producing steps, preserve complete selection
correlation and compare exact intent origins. Wire authentication, runtime
binding checks and the full application/database/offline/hosted gates remain
later work. Root v1 modules and runtime behavior are unchanged.

The final network-enabled SDK 3.14 rerun passes 544 tests and 68 subtests, with
no skips or warnings, closing the earlier shared artifact-fixture errors.

### Task 2 review correction round 1 (scoped re-review pending)

Independent review found three Important nested-flow defects: array merging lost
producer ownership, repeated aliases rewrote already-bound choices, and empty
nested arrays became missing handle items. Seven initial regression cases
reproduced those failures. The correction retains complete ordered nested-array
alternatives with each owner/cardinality, distributes selection before merging,
and distinguishes impossible zero-cardinality branches from absent fields.
Already-bound choice identities remain unchanged through unrelated aliases;
only explicitly owned source/selector slots are specialized.

A composed differing-owner regression additionally proved that replacing an
owned slot with the same index could erase its owner. The controller approved
explicit immutable occurrence/selection binding records as part of static origin
equality. They retain ordered owner/path pairs, reject repeated/conflicting
owners, and survive aliases, choices and revalidation. The guide and appended
Task 2 report record the exact metadata and later serialization obligations.
This refines compiler metadata only; no wire/runtime authority or v1 code changed.

Final retained logs show 110 focused tests and 225 Task 1+2 tests on each Python
3.14.5 and separate 3.13.13, all passing with no skips or warnings. Frozen SDK
Ruff and whitespace checks pass with retained output. The correction includes
nested intent/decision correlation, owned selectors, empty/nonempty object items,
metadata mutation and cyclic-alternative refusals. No full SDK/application/DB
matrix was repeated; the controller requested this scoped correction gate.

## Task 2 review closure

Task 2 is complete through `03c7b76248f87cbe37de969b4885cf3593ebd55c`. Independent review found three Important nested-flow defects: lost array ownership, rewritten bound-choice aliases, and empty-array cardinality treated as missing items. One correction round and a fresh scoped re-review passed: all three addressed, no open or new findings. The final 110 focused tests and 225 Task 1+2 tests on each Python 3.13/3.14 pass without skips or warnings; final logs, Ruff and whitespace evidence were inspected. Earlier full SDK/artifact results remain evidence for their recorded source, not a new full-matrix claim at this fix head.

Nested arrays retain ordered alternatives, owner metadata and cardinality. Owned selections record explicit immutable binding pairs; aliases retain their original identity. Compiler integration must preserve the complete choice, bindings and alternatives in exact comparison and deterministic projection. Package validation must resolve and normalize every mapping before final complete JCS measurement. These obligations are carried into Tasks 3 and 4.

Ruling: extend FlowArrayType with immutable per-element provenance and safe item-selection/iteration metadata because a representative item cannot retain heterogeneous handle origins — cost if wrong: refine these compiler metadata/helper interfaces before v2 stabilization, without changing wire authority.

Ruling: retain selected ownership in ordered immutable source/selector binding records while preserving occurrence-path behavior because substituting different owner slots with the same numeric index otherwise collapses identity — cost if wrong: refine the not-yet-stable compiler metadata and deterministic projection before v2 stabilization; no runtime authority or wire compatibility is created.

Tasks 3–10, full application/database/offline/artifact/hosted verification and the broader product requirement register remain open. This layer does not execute workflows or establish production qualification.

## Task 3 — structured packages and exact catalogs (independent review pending)

Implemented `ExecutablePackage`, `Body`, the 13-family closed `Step` union,
`ArtifactRef`, `CatalogDocument`, and `CatalogBundle`, plus public package
validation/canonicalization/digest and catalog validation/document digest APIs.
All required declaration families, exact pins, local references, nested step ID
uniqueness, control/retry/resource bounds and deterministic readback rule shapes
are checked. The seven synthetic source fixtures include actual normalized
catalog pins and a separately supplied workflow pin; external binary/content
availability is not checked. Guides document the exact structures and authority
boundary. V1 code and historical content remain unchanged.

The initial prescribed focused command failed with the two expected missing-API
collection errors. The first implementation run had 49 passes and one test
asserting error prose; that test was corrected to follow the required fixed-code
contract. Additional development runs corrected test setup (exact Version values
and the number of default-bearing fields needed to cross the item cap). A later
red run specifically proved schema-count refusals occurred in recursive shape
validation; preflight checks now refuse those counts before that phase.
All failed output is retained with the Task 3 report, including initial Ruff
formatting findings and the successful formatting correction.

Controller ruling: keep shape checks in Pydantic, then perform literal/schema
normalization and local domain checks outside its validators before the unchanged
Task 1 helper performs final normalized/default projection and complete JCS
measurement. This retains specific fixed domain codes without inspecting exception
cause chains. Exact 1 MiB canonical reread, one-byte overflow after defaults,
complete normalized item overflow, catalog aggregate budgets, named number
literals and mutation/construction boundaries are covered.

Python 3.14.5 final focused command:
`uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_package.py sdk/python/tests/executable/test_catalog.py -q`
— 114 passed. Preceding Task 1/2 plus v1 contract/input regression files —
263 passed and 68 subtests. One authorized network-enabled standalone SDK run,
`uv run --frozen --project sdk/python pytest sdk/python/tests -q`, passed
678 tests and 68 subtests in 45.94 seconds, including wheel and rebuilt-sdist
journeys. No skips or warnings. Frozen SDK Ruff and whitespace checks pass.
The full standalone run includes the final preflight changes; no additional
Python 3.13/application/database/offline matrix was run in this task.

Task 4 must verify supplied dependency closure/pins, lexical/type availability,
read/write operation effects, rule/readback schema compatibility, exact handle
and intent/target provenance, expansion limits and every-path verification.
Constructors and structural admission do not establish those compiler properties.
The runtime must independently authenticate authority and enforce budgets.
Independent Task 3 review and later whole-branch/product gates remain pending.

### Task 3 controller correction — identical catalog documents

Before dispatching independent review, the controller identified that Task 4's
closure contract needs identical supplied definitions to pass catalog admission.
The initial Task 3 source rejected those repetitions. Catalog validation now
retains every identical normalized document in input order, counting every
occurrence toward its family and complete raw/normalized bundle limits. Only
differing normalized content for one identity/version is `dependency_conflict`.
Task 4 owns deduplication of admitted definitions in the resolved closure.

Three new initial regression failures and a separate byte-boundary failure
reproduced the over-strict admission. The corrected focused package/catalog
command passes 119 tests, including omitted/explicit default equivalence,
64/65 identical-document family bounds, conflict refusal, complete normalized
item overflow and an identical-document bundle of 1,048,575 bytes that rereads
successfully but refuses after two added bytes despite the raw source fitting.
Raw duplicate bundles still count all bytes. Frozen SDK Ruff and whitespace
checks pass. Red/green outputs are retained in Task 3 evidence. No full SDK or
other broad suite was repeated for this narrow controller-approved correction;
the earlier 678-test standalone result describes the initial source commit.
Independent review will cover the complete Task 3 range including this fix.

## Task 3 review closure

Task 3 is complete through `1b4d871a82f949051a260a256ac1b7a60ddda91d`. Independent review of the full package/catalog range passed specification and quality with no findings. All 13 step families, seven catalog families, synthetic fixtures, complete normalized limits, local references and v1 boundaries were inspected.

The final corrected source passes 119 focused package/catalog tests, Ruff and whitespace checks. The initial task source `98e1bbe` passed 263 preceding regression tests plus 68 subtests and the full standalone SDK suite of 678 tests plus 68 subtests. These remain evidence for that source; the narrow catalog correction has fresh covering tests. No Python 3.13/application/database/offline/hosted result is claimed for this task. Full integration and final artifact gates remain Task 10.

Ruling: run coded domain/reference checks at the Task 3 public boundary outside Pydantic validators, then perform final complete normalized/default JCS enforcement — this preserves fixed domain codes without recovering them from exception internals — cost if wrong: an extra bounded shape-validation pass or boundary refactor, without changing v1, shared helpers or authority.

Ruling: retain every validated identical normalized catalog document occurrence and count each toward family/raw/normalized bundle limits; reject differing content under one identity/version — the compiler must be able to deduplicate identical supplied definitions after admission — cost if wrong: refine the not-yet-stable bundle/caller contract; no limit is relaxed and no runtime authority changes.

Task 4 receives the actual package/catalog APIs, normalized definition hashing, duplicate-retention contract, fresh fixture helpers and the complete reviewed Task 2 provenance contract. Compiler closure, lexical/type analysis, work expansion and per-effect verification coverage are still unimplemented requirements. Actual artifact custody, provider support and runtime authority remain later qualification boundaries.

## Task 4 — pinned compiler implementation (independent review pending)

Implemented complete local closure admission and deterministic typed compilation
in `dependencies.py`, `compiler.py` and `plan.py`. All supplied occurrences count
before deduplication; every declared and catalog-internal pin is required, and
unrelated supplied definitions refuse. Closure depth/count, lexical availability,
full handle/array provenance, target identity, read-only tools, inherited envelopes,
conservative attempts and per-action normal-path verification are checked without
I/O or execution. Plans carry separate stable authored region IDs and movable
source pointers; callee templates retain distinct call-site edges.

Controller rulings applied: agent steps require explicit typed `input`; catalog
reconciliation rules use write input/read operation output contexts, and named
evaluation rules use their declared input context. Target equality preserves
aliases and correlated choices/occurrences using private bounded symbolic DAGs;
no evaluator or authority was introduced. Complete-array receipt verification
retains producer cardinality through branch-specific array alternatives, while
subset/empty unrelated-loop paths cannot erase pending obligations.

The initial exact focused command failed 21 tests because the resolver/compiler
interfaces were absent. Final focused command:

```sh
uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_dependencies.py sdk/python/tests/executable/test_compiler.py -q
```

Result: **67 passed** on Python 3.14.5. The preceding input/canonical/types/
expressions/package/catalog files passed **344 tests**. `uv run --frozen --project
sdk/python ruff check sdk/python` passed. Hand-authored minimal/timer plans are
checked against independent standard-library sorted JSON identities and committed
JCS fixtures; SHA-256 values are recorded in `plan-hashes.txt`. Named synthetic
examples include 10,000/10,001 attempts, a forbidden private branch reference and
an explicit agent input.

The standalone SDK suite ran once with authorized artifact-dependency network
access and TLS intact: **746 passed, 68 subtests passed**. This broader run
preceded the final complete-array cardinality correction, stable region metadata
and empty data-array constructor correction. Final scoped checks cover those
changes; the earlier full-suite snapshot is not relabeled as final-head evidence.
No application/database/offline matrix was run; Task 10 owns integration gates.
All red outputs, intermediate mistakes, final scoped results and full-suite
source hashes are retained in the local Task 4 evidence/report directory. The
independent Task 4 review remains pending; provider behavior, artifact contents,
spend enforcement and production runtime qualification remain unchecked.


### Task 4 correction round 1 — scoped re-review pending

The first independent Task 4 review requested three Important corrections:
decision selectors omitted producing iteration occurrences; shared-choice
projection/item traversal could expand exponentially; and the required complete
structured plan oracles were absent. The retained initial correction regression
run reproduced **11 failures** (eight occurrence assertions/refusals and three
five-second bounded-process timeouts). Missing structured oracles separately
produced **5 failures**.

Selectors now retain full occurrence paths and ordered bindings in public flow
and private identity. Coverage rebases only the exact receipt-bound producer
selector. Same-item positives and cross-index refusals include nested loops and
branches. Projection/item selection preserve DAG sharing, with a common bounded
work budget for traversal, binding and fingerprints. Context separation, retained
sharing and controlled budget exhaustion have focused assertions.

Five complete hand-authored plans cover decisions, parallel joins, ordered
iteration, pinned subworkflows and a receipt-bearing decision. Every step,
region, edge, source/mapping/type identity, pin, choice and obligation is compared;
canonical bytes and literal hashes are independently calculated without the SDK
in `structured_plan_oracles.py` and frozen in committed JCS artifacts. One initial
receipt-oracle mismatch exposed an omitted explicit null credential default in
the hand-authored source; that oracle error was corrected and retained in evidence.

Correction covering checks: **85 passed** in dependency/compiler files and
**344 passed** in earlier input/canonical/type/expression/package/catalog files.
Ruff and whitespace checks pass, and all seven committed plan hashes verify.
These are scoped Python 3.14 results. The earlier standalone SDK/artifact snapshot
is unchanged; no broad artifact suite was repeated. The local Task 4 report holds
exact commands, retained failures and final source hashes. This correction remains
subject to independent review; runtime authority and provider/artifact behavior
remain unchecked.


## Task 4 review closure

Task 4 is complete through `9bf785d9fb5b790dbffd79162824796618f39693`. The initial independent review identified three Important defects: cross-iteration selector identity allowed a mismatched target, shared-choice projection could expand exponentially, and complete structured literal plans were missing. Correction round 1 and scoped independent re-review passed specification and quality, with all three addressed and no open or new findings.

The corrected source passes 85 dependency/compiler tests and 344 earlier executable contract tests, plus Ruff and whitespace checks. All seven independent literal plan artifacts match their fixed hashes, including decision, parallel, ordered iteration, pinned subworkflow and receipt-bearing decision coverage. The reviewer also checked all 14 changed source/artifact hashes against the committed correction. The earlier full SDK result of 746 tests and 68 subtests remains scoped to its recorded earlier source. Final dual-Python, complete application, verified localhost database, installed/offline artifacts, whole-branch review and exact-head hosted CI remain Task 10 gates.

Decision selectors now retain complete occurrence paths and ordered owner bindings; consumers must preserve both rather than comparing only authored paths or decision IDs. Projection and iteration-item distribution preserve shared structures under a bounded per-operation work budget. Plans retain stable authored region IDs separately from source pointers. The approved plan's agent row now explicitly includes the required typed input mapping correction.

Ruling: reject unreachable supplied catalog definitions as well as workflow definitions after full admission, with all declared dependencies and catalog-internal pins reachable even when unused by steps — unrelated inputs should not alter plan identity — cost if wrong: callers must select a precise catalog closure or the not-yet-stable input policy must be refined.

Ruling: require explicit AgentStep input mapping and check the selected model/agent input schema — typed agents need an explicit way to consume mapped data — cost if wrong: draft v2 authors/fixtures must add the field or refine the not-yet-stable contract; v1 is unchanged.

Ruling: give catalog reconciliation mappings the write operation input context and read operation output context, and evaluation rules their declared input schema — catalog rules have no workflow/iteration scope — cost if wrong: catalog mappings or the not-yet-stable context contract need refinement.

Ruling: preserve static verification-target identity with private symbolic provenance across aliases, choices and occurrences, retaining exact connector and reconciliation operation checks — mapping digest equality or runtime-only deferral cannot prove the approved static binding — cost if wrong: refine proof rules or conservatively rejected equivalent expressions before v2 stabilization; no authority boundary is weakened.

Task 5 receives the bounded model/JCS helpers and reviewed compiler metadata contract. Real persistence, authentication, provider read-back, current authority and production qualification remain separate requirements in the approved master register.

## Task 5 implementation — independent review pending

Implemented bounded action bindings/intents, human decisions, complete command
records, strict event variants, receipts/outcomes and explicit supplied binding
contexts. Public validators revalidate known typed objects, recompute embedded
intent/payload hashes, enforce normalized wire bounds and expose complete
canonical/digest helpers. Ordered consistency reports keep signature checking
unchecked and authority false. Every event payload/reference combination is
closed and documented for the later transition, signing and schema tasks.

The initial exact focused command failed with the expected two missing-module
collection errors. A first implementation passed 94 tests; subsequent red tests
caught malformed-object error mapping, missing payload helpers and command
decision-digest refusal precedence. Final focused result: **116 passed**.
Named preceding compiler/dependency/type/expression/package/catalog/input/JCS
checks: **429 passed**. Ruff and whitespace checks pass. The intent byte oracle
is independently calculated from ASCII/exact-integer synthetic data with stdlib
JSON/hashlib, frozen at 1,064 bytes and SHA-256
`e3860ea56b81199fb42870a5ee4e17bc0d4c90e54026fe2d36e21debd5cc7c7c`.
Tests include every binding-field hash contribution, changed context/recipient/
epoch/generation, independent human review, exact expiry equality, mutated and
constructed models, nested record checks, and the exact 65,536-byte wire boundary.

The full standalone SDK run first reached **846 passed, 38 setup errors and 68
subtests passed**: the common installed-artifact fixture could not fetch hatchling
under restricted network access. A retained direct build probe confirmed the
sandbox network denial. Retrying the same full command with already-authorized
network access, with TLS verification retained, passed **884 tests and 68
subtests**, including wheel and rebuilt-sdist-wheel journeys. No skips occurred.
These results use Python 3.14.5; the final dual-Python/application/database/offline
and hosted gates remain Task 10 work.

The local Task 5 report and wire consumer handoff retain exact commands, all
failed and successful logs, exact public contracts and downstream obligations.
Independent review is pending. No runtime, authority, transport, database,
provider execution, public release or deployment is added or claimed.


## Task 5 review closure

Task 5 is complete through `ed262802d504f46fc49ed17b933479ded8d0d5bf`. Independent review of the entire action/wire change passed specification and quality with no findings. Review covered exact context and approval binding, half-open lifetimes, ordered refusals, complete normalized wire limits, mutation revalidation, all 17 closed event variants and their references, nested digests, declared evidence, public helpers and the independent 1,064-byte intent-binding oracle.

Final Python 3.14 evidence is 116 focused tests, 429 preceding tests, and the full standalone SDK suite of 884 tests plus 68 subtests, with no skips or warnings. Ruff and whitespace checks pass. The reviewer verified all eight source/test/oracle hashes against the inspected commit. The initial full run's 38 installed-artifact setup errors were traced to sandbox denial of the Hatchling download; the authorized network rerun passed with TLS verification unchanged. Failed and successful outputs remain retained. This resolves the setup failure, without claiming that test stdout independently certifies execution permissions or TLS settings.

Task 6 receives complete record admission and full-event digest helpers, the exact event/reference matrix, binding refusal order and explicit context requirements. Full-event replay identity must include headers, sequence and time; an intent's binding digest differs from its full wire-record digest. Signing must cover the full admitted command. Constructor validation alone is not complete admission, and exported schema must preserve event kind/payload/reference correlations.

Actual authentication, durable state, provider read-back, authority and production qualification remain unimplemented consumers. Final Python 3.13, complete application/database, actual offline-image recovery, whole-branch review and exact-head hosted CI gates remain Task 10. No earlier task finding is parked or open.

## Task 6 implementation evidence

Added bounded, deterministic `ContractState`, `EventContext`, `KnownEvent`,
`CompletionSummary` and `TransitionReport` contracts, full-event replay digest,
public admission helpers and ASCII report serialization. Scope/source/epoch
fences precede known replay, then exact sequence and current-state guards.
Only applied events advance the supplied sequence. Refusals preserve state;
cancellation/failure and uncertain writes preserve known effects. Completion
requires the full supplied verification summary and cannot promote acceptance
into verified success.

Context includes independent admission hashes, explicit active step/attempt,
exact opaque target-operation and reconciliation/verification rule bindings.
Ordinary activity retries need the actual failure code/policy and no action
intent; possible writes require qualified absence under the same intent.
Qualified absence is a separate structural recovery basis without an invented
failure code. It may resolve acknowledgement, but never clear observed/verified
facts or contradictory positive evidence. All results remain synthetic and grant
no authority; the future store must retain receipts and evidence independently.

Initial exact focused red command produced 54 missing-interface failures before
implementation. Later red cases covered unsafe retry/state reset, missing ordinary
activity retries, controller-qualified absence refinements and Task 5 binding
precedence; all failed runs and corrections remain in the task evidence/report.
Final Python 3.14.5 focused run: **107 passed**. Required transition/action/wire/
compiler coverage: **292 passed**. Full standalone SDK: **991 passed and 68
subtests passed**, including installed artifact journeys, no skips or warnings.
The full run used already-authorized network access for Hatchling from the outset,
with TLS settings unchanged. Ruff and whitespace checks pass. Exact tested source
hashes, commands, intermediate failures and final outputs are retained under the
local Task 6 SDD evidence directory.

The tracked executable guide records exact model fields, required/null context
facts per event, admission and replay order, zero-based attempts, retry/completion
invariants and future consumer duties. Independent task review is pending. Final
Python 3.13/application/database/offline/whole-branch/hosted gates remain Task 10;
no runtime persistence, authority, provider execution or production qualification
is implemented or claimed.

## Task 6 correction round 1 evidence

Independent review of `f6142cd9c78fa16c283d1e9f62b72aa46bc9490a` found two
Important defects: positive receipt evidence could be ignored by no-dispatch
retry/redispatch/zero-effect completion paths, and state/report admission allowed
completed accepted/observed actions with unknown outcomes. Both were reproduced
before correction: **48 failures, 8 passes** in the new review-focused cases.

Fresh progression now checks any supplied relevant receipt against the expected
full intent/context and exact action/attempt/target operation. Positive observed
effect contradicting not_dispatched refuses without state changes, sequence
advancement or retry. The guard follows scope/replay/sequence and stop semantics;
historical duplicates, ordinary no-intent activity retries and qualified absence
of an acknowledged request remain covered. Completed accepted/observed states
now require verified outcomes at public state/report admission and serialization,
including raw, mutated and constructed input. Zero-effect and verified aggregate
completion remain valid.

Correction verification on Python 3.14.5: **56 review-focused cases passed**;
required transition/action/wire/compiler suites: **348 passed**; Ruff and
whitespace pass. No full SDK artifact repeat was requested or run. The previous
991-test full SDK result belongs to the pre-correction source; it is not an
exact-head full-suite claim for this correction. Failed and passing correction
logs and exact tested source hashes are retained in the Task 6 evidence directory,
and the implementer report documents the updated receipt/state obligations.
Scoped independent re-review remains pending.

## Task 6 review closure

Task 6 is complete through `6877cc54514b2b79b375ec92b670f5de8e815539`. Initial independent review found two Important defects; scoped independent re-review confirms both addressed, specification PASS and quality APPROVE, with no open or new findings. The common fresh-progression guard now checks the exact relevant receipt binding before retry/dispatch/completion paths, and completed accepted/observed actions require verified outcomes through every public state/report admission path. Fence, historical replay, sequence, stop and legitimate acknowledgement/qualified-absence behavior remain preserved.

The reviewer checked all corrected source hashes and retained 48-failure red, 56-case green and 348-test covering evidence. Ruff and whitespace are clean. No full SDK suite was repeated for the correction; the earlier 991-test/68-subtest result applies to its earlier commit. No defect was waived or deferred. Corrected-head dual-Python/artifact/application/database/offline/whole-branch/hosted gates remain Task 10, and actual authentication, persistence and provider qualification remain later product work.

Controller rulings for the reviewed transition contract:

Ruling: Extend EventContext with bounded optional singleton BindingContext/intent/decision/receipt/wait/retry/completion facts, expected step/action/attempt, exact active_step boolean, expected reconciliation/verification rule identities and explicit opaque target_operation_ref — transition legality and receipt/evidence correlation require these supplied facts; neither persistent history nor parsing an opaque reference can substitute — cost if wrong: refine the not-yet-stable context model and caller fixtures before v2 stabilization; no authentication or execution authority is introduced.

Ruling: Require explicit expected package/configuration/plan hashes for admission in EventContext fields or a bounded expected-admission singleton, without fabricating action identity to reuse BindingContext — a new execution must compare its admission payload to independent supplied expectations rather than accept the incoming hashes as their own reference — cost if wrong: refine draft context shape and caller fixtures; this remains synthetic consistency and introduces no authority.

Ruling: Interpret runtime attempt as a zero-based index, allowing dispatch only while attempt < max_attempts and a further retry only while attempt+1 < max_attempts — existing wire fixtures begin at zero and max_attempts counts total attempts — cost if wrong: refine draft transition/context documentation and callers before stabilization; no extra attempt or authority is granted.

Ruling: Allow exact qualified-absence reconciliation to make an accepted acknowledgement retry-eligible, while preserving observed/verified effects and contradictory positive evidence — accepted records transport acknowledgement, not a known target effect, and the approved retry contract permits qualified absence under the same intent — cost if wrong: refine the pre-stable transition policy and callers; future authority must still authenticate absence and issue fresh permission before any retry.

Ruling: Treat qualified absence as its own structural retry basis, without requiring the timeout_before_dispatch retry code; ordinary failures and no-dispatch timeouts still require their actual allowed retry code — a qualified-absence payload contains no failure code and the approved contract separately permits that recovery condition — cost if wrong: refine the pre-stable retry-policy interpretation and callers; bounded attempts, current binding/lifetime/epoch, positive-effect preservation and fresh authority remain mandatory.

## Task 7 implementation evidence

Added private deterministic authored-source `ExecutableChange`, `ExecutableDiff`,
`diff_executable_packages`, `validate_executable_diff` and `executable_diff_json`.
Sources use full structural v2 admission and normalized canonical hashes without
catalog resolution or compilation. Global step IDs, stable owner-scoped regions
and exact declaration aliases preserve movement plus edits, child independence
and significant common-item order. Public schemas, result exports, every
declaration/pin, controls, model/tool/input declarations, approval/compensation/
verification and budgets participate. Default details are normalized `values:null`;
exact boolean opt-in permits private local detail. Source paths never contain
authored data-member names/pointer values. Report admission and ASCII/newline
serialization revalidate privacy, exact flags, identity/path/operation consistency,
order and complete normalized/actual output limits without truncation.

Initial exact focused red command failed at collection because the new
`ExecutableChange` interface was absent. First focused green: **21 passed**.
Self-review added independently literal cases for nested parent movement, scoped
branch identities, all declarations/public schemas and compensation verification;
it exposed one category defect (**1 failed, 27 passed**) before correction.
Final focused green: **28 passed**. Ruff initially reported one lambda-style
violation, corrected without changing behavior; final Ruff and whitespace pass.

Python 3.14.5 task-named suites grouped by fixture scope: **161 v2 tests passed**,
**66 v1 tests and 68 subtests passed**. An earlier mixed explicit command that
switched from v2 to v1 and back produced **137 passes, 90 setup errors and 68
subtest passes** (`case` fixture unavailable for later v2 package tests). Its
exact command and all outcomes are retained; no assertion or fixture was weakened.
This is an observed fixture-resolution issue, not a proven pre-existing defect.
Normal full SDK discovery passed **1,075 tests and 68 subtests**, no skips or
warnings, including standalone wheel/sdist journeys. The full run used authorized
network access initially, with TLS intact. Complete red/intermediate/green output
and tested source hashes are retained in Task 7 SDD evidence and its report.

The controller approved the public report spelling and final admission/ASCII
API, explicit no-behavior/authority flags, top-level local detail and scoped
identities. The normalized null detail marker follows explicit-default/privacy
rules and adds no private payload. Task 10 must revalidate the inner report and
count its complete CLI wrapper/newline with the shared report boundary. Fresh
independent Task 7 review is pending. Python 3.13/application/database/offline/
whole-branch/hosted gates remain Task 10; no production qualification is claimed.

## Task 7 correction round 1 evidence

Independent review of `96617e65bbc65c98794e8283e3850bf30ea3af3d` found one
Important defect: report admission rejected a legitimate moved step when its
numeric before/after source pointers were equal. Replacing a parallel branch ID
or control-owner ID changes the containing region identity while preserving the
stable child ID and its position. Movement follows region identity; distinct
numeric pointers are not required. Removed that admission prohibition while
retaining category, step identity, non-null paths, permitted pointer tokens,
privacy, order and complete output bounds. The guide now states the distinction.

Literal branch/owner replacement expectations cover both private and opt-in
serialization, removed/added regions and owners, the retained child's `moved`
operation and exact equal source paths. Correction red: **4 failures, 5 passes,
28 deselected**. Same targeted green: **9 passes, 28 deselected**. Grouped named
v2 diff/package/canonical suites: **170 passed**; separate v1 diff/contract:
**66 passed and 68 subtests passed**. Ruff and whitespace pass on Python 3.14.5.
Exact commands, failed/passing output and corrected source hashes are retained
in Task 7 evidence/report. No full SDK artifact repeat was requested or run;
the earlier 1,075-test/68-subtest result belongs to the pre-correction source.

The reviewer independently reproduced the mixed whole-file fixture-order errors
on immutable pre-Task-7 BASE `3e9f2cae2c76ba5a0a6d2613f078cdad4d858f2f`.
That concern is now established as a pre-existing harness limitation; the
original failed evidence remains and no harness rewrite or weakened assertions
entered this correction. Scoped independent re-review is pending; no finding
was waived or deferred. Final dual-Python/application/database/offline/whole-
branch/hosted gates remain Task 10.

## Task 7 review closure

Task 7 is complete through `4e1e98cf8cc3b4758d610024f66bff87c96410f7`. Independent review found one Important defect: a branch or owner ID replacement can move a retained child to a different logical region while its positional pointer stays the same. The correction removes the extra pointer-inequality restriction; traversal continues to determine movement from stable region identity. Fresh scoped review passed specification and quality with one addressed, zero open and zero new findings.

The corrected source is covered by nine focused review cases (four reproduced failures before correction), 170 grouped v2 tests and 66 v1 tests plus 68 subtests, with Ruff/whitespace clean. The reviewer verified all three source hashes and literal private/detail branch/owner expectations. The earlier full SDK result of 1,075 tests plus 68 subtests remains tied to its pre-correction commit. No broad artifact rerun was represented as corrected-head evidence.

Independent narrow probes reproduced the mixed explicit v2/root/v2 pytest fixture-order failure on immutable pre-task BASE with unchanged fixture/config/test files. It is a pre-existing harness limitation; normal full discovery and separately grouped required suites pass. Failed outputs and exact probe commands are retained. No task defect was waived or deferred. Final CLI/artifact/dual-Python/application/database/offline/whole-branch/hosted gates remain Task 10.

Controller rulings for the reviewed report contract:

Ruling: Expose validate_executable_diff and executable_diff_json with explicit before/after hashes, exact include_values, fixed behavioral_compatibility=not_assessed and false authority; identify authored steps, stable scoped regions and declaration aliases separately, using source pointers that never interpolate private data-member names — Task 10 needs a revalidating report boundary and a structural diff must not imply behavior or leak authored payloads — cost if wrong: refine pre-stable report field/identity spelling and caller fixtures; no v1 byte or behavior change.

Ruling: Represent unavailable private diff detail as values=null in normalized default reports, and require a non-null before/after detail object only for explicit include_values=true — the approved specification excludes free-text/literal values while global normalization includes optional defaults; a null marker discloses no payload and keeps one complete serialization boundary — cost if wrong: change pre-stable report field presence and literal output fixtures, with no private-data or v1 behavior change.

## Task 8 implementation and review closure

Task 8 is complete through `59d9f4e55c1637f5d0d796a50858da0e210f1194`. Added separate v2 package and runtime-command Ed25519 envelope/framing/signing/verification APIs, complete public admission and bounded ASCII serializers. Every required envelope header and the exact captured complete canonical content are signed. Command APIs first require command-specific admission. Explicit allowed/revoked TrustSet validation, key encoding guards and refusal order are reused; v1 code, frames, fixtures and file helpers are unchanged.

Independent crypto-capable review passed specification and quality with zero Critical, Important or Minor findings. It checked both exact framing contexts, complete header/command coverage, trust/model mutation boundaries, error/output limits, independent oracle provenance and the separate Task 5 binding check. No task defect was waived or deferred; signatures remain independent of actual permission or runtime authority.

The exact initial focused command failed on the missing CommandSignatureEnvelope API, then passed 103 cases; expanded final signing coverage passes 146 tests on each Python 3.14.5 and 3.13.13. Grouped signing/canonical/input/action/wire regressions pass 377 tests on each, and unchanged root v1 signing/trust passes 103 on each. Full normal-discovery SDK suites pass 1,230 tests plus 68 subtests on each, including current wheel and independently rebuilt-sdist journeys, with no skips. Ruff passes on both interpreters. Builds used already-authorized dependency network access and retained TLS verification.

One trailing space in a new test was caught after staging and removed after the full matrix. Production/export/oracle hashes are identical before and after that correction; final focused suites and Ruff were repeated on both interpreters, and whitespace passes. The full matrix remains tied to the pre-whitespace test file rather than being relabelled as a new run. The reviewer independently matched all six final source/test/oracle hashes and inspected both manifests and retained outputs.

Independent literal frames/signatures use public RFC 8032 TEST 1 material and RFC 8410 wrappers, Node v24.21.0 and OpenSSL 3.5.7, with no SDK/cryptography/Python-JCS imports in their generator. Both signing directions are checked live. Package canonical content is 937 bytes and its frame 1,343 bytes; command content is 486 bytes and its frame 865 bytes. Their complete bytes, signatures and hashes are retained as attributed fixtures. An initial generator path error and its wrapper's misleading zero exit are retained and explicitly identified as failure; corrected generation succeeded before implementation.

Later installed v2 CLI journeys, full application/database/offline-image qualification, whole-branch review and exact-head hosted CI remain Task 10. Actual authenticated runtime authority and provider qualification remain later product work.

Ruling: Signing uses one public canonical-entry call per operation and frames its returned immutable bytes, while retaining the reviewed validators' internal JCS bound measurements; expose explicit context-specific envelope and verification-report admission/ASCII helpers — the byte-consistency requirement forbids independently rereading or reframing different content, not the existing bounded semantic validation passes — cost if wrong: refine pre-stable helper naming or optimize repeated internal validation, without changing signed bytes, v1 framing or trust semantics.

## Task 9 implementation evidence

Added executable-only strict capability profiles, explicit requirements and
bounded synthetic compatibility reports, with public admission and normalized
JCS/ASCII serialization APIs. Profiles compare exact artifact versions/digests,
connector operations, model configurations, selected platform capabilities and
every package budget through the entire supplied closure. Unused declarations,
catalog-internal pins, source schemas/functions/steps, originating handles,
implicit expression results, iteration indices and control output values all
remain requirements. A multi-origin compiled dispatch with operation=null cannot
imply unconstrained support. Root v1 APIs and canonical fixtures are unchanged.

A shared private dependency inventory admits all occurrences and checks known
pins/graph errors before allowing an unresolved report. Public resolution remains
complete-only. Missing well-formed definitions yield structurally valid but
unresolved/incompatible reports; type compilation waits for complete closure.
Neither a missing source nor truncation creates success. Every returned report
retains synthetic assurance, false authority and explicit unchecked signature,
artifact/provider availability, policy, credential and live-support statuses.

Initial missing-API red: **32 failures**, expanded to **60 failures**. The first
implementation run had **72 passes, 2 fixture failures**; the fixtures were
corrected to respect parent budget containment and actually author a transform.
Self-review independently exposed a discarded parallel-object requirement
(**1 red → 1 green**) and implicit comparison/logical/exists booleans and
iteration-index integers (**20 red → 20 green**). The fixed expression/control
vocabulary was reviewed together without introducing an interpreter.

Final Python **3.14.5** and separate-environment **3.13.13** checks each pass:
**164 focused compatibility tests**, **364 grouped v2 compatibility/dependency/
compiler/canonical/input tests**, **113 separate root v1 tests plus 68 subtests**,
and **1,394 full normal-discovery SDK tests plus 68 subtests**. No skips. Ruff and
whitespace checks pass. Full discovery includes the current installed wheel and
independently rebuilt-sdist journeys; new v2 CLI installed journeys remain later.

Initial full runs had **1,336 passes +68 subtests and 38 artifact setup errors**
on each interpreter: sandbox networking prevented uv from fetching hatchling
(`Operation not permitted` opening the proxy connection). The already-authorized
network-enabled reruns passed with TLS, proxy and CA settings intact. No test or
harness was weakened. Their **1,374-pass** source snapshots precede the final
implicit guard/index correction and are not relabelled as final-head results.
Final corrected matrix passes are **1,394** each. Failed/intermediate/final logs,
exact commands and both source manifests are retained in the Task 9 SDD evidence
directory. Final source hashes were checked after all runs with no changes.

The guide and synthetic profile/requirements examples document exact records,
zero-capacity semantics, explicit platform requirements, known-edge incomplete
inventory rules, separate identities and complete output bounds. Controller
rulings are preserved in `task-9-controller-decisions.md`. Self-review is complete;
independent Task 9 review is pending. No task defect is knowingly deferred or
waived. Full application/database/offline-image, installed v2 CLI, whole-branch
and exact-head hosted gates remain Task 10; real runtime/provider qualification
is not asserted.


## Task 9 review and correction closure

Task 9 is complete through `814b6b2b657659796728acf08b991e11152ba7cc`. Independent review of `e3f0a945ca6be117dda6389d784688069dd486f7` found one Important omission: a workflow with an automatic main action and independently human-approved compensation could be declared compatible without human_decision support. The explicit main-path review/output scan did not account for compensation's implicit review. A retained independent reproduction compiled successfully with two outcome obligations and demonstrated the false compatibility result.

The correction walks admitted CompensationSpec records and requires their own action_intent, action_receipt and verification_evidence handles plus human_decision when approval is human. Root and called-subworkflow tests isolate the compensation requirement from explicit main-path review, with positive full-support and non-human controls. No compiler, dependency resolver, v1 API or authority semantics changed. The guide documents the conditional requirement.

Correction red was eight failures and six passes; the exact targeted green has 14 passes. Named compatibility/dependency/compiler suites pass 263 tests on each Python3.14.5 and3.13.13, without skips; the original reviewer probe now refuses missing support. Ruff and whitespace pass. All three corrected source/test/guide hashes match the retained tested manifest. The earlier 1,394-test/68-subtest full SDK results remain tied to the pre-correction source; a broad artifact repeat was not substituted for the focused correction checks. Final integrated matrix remains Task10.

Fresh independent scoped re-review passed specification and quality with one addressed, zero open and zero new findings; no Critical, Important or Minor findings remain. No finding was waived or deferred. Actual availability, policy, credentials and live runtime qualification remain explicitly unchecked and are separate product gates.

Controller rulings carried through the reviewed implementation:

Ruling: Give v2 profiles explicit artifacts, connector-operation and model-configuration records, enforce immutable kind/name/version pin consistency and exact digest comparison, and make every supported PackageLimits field required with zero meaning no capacity — the plan names these support dimensions without their record shapes, and partial support must remain representable without silent defaults — cost if wrong: refine pre-stable profile schema, hashes and fixtures before stabilization; v1 profiles remain unchanged.

Ruling: Requirements name only the current literal lotuspond.runtime-contract.v1, which this v2 profile format describes, and the explicitly selected OS/architecture must appear in every applicable package platform whitelist and the profile, with empty lists meaning no support — there is no supported alternate runtime contract or inferred environment, and a dependency cannot silently broaden the selected deployment — cost if wrong: revise pre-stable runtime/platform declaration semantics and caller fixtures; no real runtime support or permission is asserted.

Ruling: V2 value_kinds includes the nine existing DataSchema kinds and four existing opaque HandleKind literals, with originating steps requiring their handle support even when that output is discarded — complete typed-feature accounting must not disappear merely because an authored result is unused — cost if wrong: refine the pre-stable feature vocabulary and profile fixtures; handles remain compiler-originated and untrusted on the wire.

Ruling: Missing-dependency compatibility results require full admission and a bounded audit of all available supplied pins/graph errors before returning unresolved; share dependency inventory/traversal rather than copy a second resolver, and defer lexical/type compilation until closure is complete — a missing first pin must not hide detectable bad digests, conflicts, cycles or extra supplied definitions, while an unresolved report promises only structural validity — cost if wrong: refine private dependency factoring and partial-closure diagnostics, with complete-input resolver/compiler behavior and public authority boundaries preserved.


## Task 10 implementation, independent review and local integrated evidence

The complete CLI/schema/artifact integration was committed at
`29052f5e9187d5ce9f78c0e97391116c51c9bb70`. A fresh independent review of the entire
Task 10 range from `e7c3899a46de5fa734a9c5a5262a3abb116565da` passed specification
and quality with zero Critical, Important or Minor findings. All 31 changed-file
hashes match the final manifest; both final artifact manifests match all 36 SDK
source files. No task finding was waived.

Only an exact leading `v2` selects the nine new local CLI forms. The root v1
commands, canonical bytes/digest and registry admission remain unchanged. Three
committed structural schemas cover packages, compiled plans and the seven tagged
runtime records, including all seventeen event/payload/reference combinations.
Independent JSON Schema behavioral cases supplement snapshot parity. Schemas do
not establish semantic validity, hashes, trusted compilation or authority.

Initial evidence retains missing-validator/schema collection failures, CLI
2-failed/13-passed red and installed 2-failed/2-passed red; legacy baseline was
39 passes. Final focused CLI/schema checks pass 23 tests. After inspection found
and fixed a missing required root runtime discriminator, full SDK suites passed
1,433 tests plus 68 subtests on Python 3.14.5 and 3.13.13. This was an inspection
finding, not an invented observed-red claim.

A later fixture-only correction made the artifact origins independent: explicit
checkout `uv build --wheel`, separate `uv build --sdist`, then wheel build from
that extracted archive. All consumers of the corrected fixture passed 43 tests
on each interpreter. Earlier default-uv-build wheels originated from an sdist;
those earlier full matrices are not relabelled as corrected-fixture runs. Final
checkout/rebuilt wheels on both interpreters have SHA-256
`f194589f26b9293b005634936b4099bb48f7eb73ceaa72f74e109b2e01c68be8`; the source
archive is `b8de3e7b8e6953cd30642a1f4bb23078b5322e299ea95e56a36ab545d1e312e4`.

Controller integration then passed on the same clean product commit:

- `npm run check`: 105 Node tests, 300 backend tests plus 97 subtests with 39
  PostgreSQL-only skips, and 1,433 SDK tests plus 68 subtests; TypeScript, Ruff,
  legacy/new schema parity and Vite build passed. This final Python 3.14 SDK run
  includes the corrected installed fixture. The cloud host used serial Node and
  a child-process reaper, without changing hosted CI.
- The identity-verified owned localhost PostgreSQL run passed 339 tests plus 97
  subtests with no skips. An inherited Starlette deprecation warning remains.
- Deployment checks passed 37 tests plus 13 subtests; database schema parity and
  whitespace passed. Strict MkDocs evidence remains tied to the unchanged site
  sources tested in Task 10; no unrelated broad repeat was substituted.
- The actual noneditable API image is
  `sha256:7624d3080f9a56044d1fcc766f43056fd33c179ffd227debb53dcc7e9ae30abf`.
  The unchanged collector source and immutable image ID were rechecked before
  reuse. A build-only CA secret preserved TLS on this cloud host; ordinary hosted
  builds remain an independent gate.
- The retained offline archive is 214,667,187 bytes, SHA-256
  `afa840593a810b44d9759c335a42a3c50c880fc2f5f233fc08369e71fe623112`.
  Its 218.4-second drill passed installed v1 identity/digest and literal v2
  compilation, schema 12, operator MFA/replay/revocation, worker backlog/staleness
  and restart drain, and Redis failure/recovery. It used an internal-only network,
  zero published ports and synthetic data; cleanup of five containers and three
  anonymous volumes was confirmed. The image matches all 42 backend runtime,
  36 nested SDK and four static UI files. Content equality is not signed Git
  provenance. The inherited proof tool's literal “Repository has no HEAD” note
  is inaccurate for this checkout and is flagged for whole-branch review; the
  source commit is independently recorded above.

Local evidence is retained at `/tmp/lotuspond-v2-final.5WdOFT` and in this plan's
SDD workspace. Whole-branch review, its corrections and exact-head hosted checks
remain pending. Real runtime/provider support, Studio, commercial licensing and
customer/operational production qualification remain open product requirements.
The customer-need rationale in `docs/AGENT_PRODUCT_RATIONALE.md` preserves every
approved goal and treats Automation Anywhere as a reference only.


## Whole-branch corrections and final source validation

Whole-branch review of `4685507..2e1c2c0` found four Important compiler boundary
issues and two Minor documentation/proof issues. One coordinated correction at
`2b4b3ee908daf164dc3ad19759a23bfd3b20bf36` fixes nonarray iteration diagnostics,
forwards owning named schemas into decision validation, admits complete ordinary
structured wait correlations, and rejects review/decision use with automatic
intents. It also corrects stale delivery descriptions and the inaccurate Git
provenance note. No wire implementation, v1 contract, schema or independent plan
oracle was changed. Fresh scoped review passed all six corrections with zero
open or new findings; none were waived or parked.

Correction evidence preserves genuine baseline-source failures (20 failed,
79 passed) and the matching final 99 passes. Broader covering tests pass 709 on
each interpreter. Named legacy/installed tests pass 152 plus 68 subtests on each;
initial restricted-network artifact setup errors are retained separately. Five
content-proof tests, Ruff, whitespace and strict documentation build pass.

The controller independently verified the corrected source:

- Full `npm run check`: 105 Node tests; 300 backend tests plus 97 subtests
  (39 PostgreSQL-only skips); 1,472 SDK tests plus 68 subtests on Python 3.14.5;
  TypeScript, Ruff, both schema checks and production build pass.
- Complete Python 3.13.13 SDK discovery: 1,472 tests plus 68 subtests, no skips.
  Both full suites use actual checkout wheels and independently rebuilt-sdist
  wheels. Retained artifact manifests match all 36 current SDK source files.
- Deployment tests: 37 plus 13 subtests. Database/backend/v1/schema/lock sources
  remain identical to the previously verified 339-test/97-subtest localhost
  PostgreSQL gate; that evidence remains attributed to `29052f5e`, not rerun or
  mislabelled as a new database check.
- Final wheel SHA-256 is
  `ed5aeaa0fd4cc417eaa4a9ba69627e5dccb7c9e000e4716397a4d2ea5e216118`;
  sdist SHA-256 is
  `de474ab6850d43cd2ef198d12467eb1cbe459976280b01e01c3e16c86c0d03d6`.
  Checkout and rebuilt wheels match. The full 3.13 run's pytest temporary
  manifest was cleaned before retention; separately retained final-source 3.13
  installed-test manifests establish the same artifact hashes. Actual 3.14
  artifacts and manifests are retained, without rerunning a passing suite just
  to recreate temporary files.
- Fresh actual API image:
  `sha256:78c016f4a2eb02c84035d6c062dd57573bac734508d6cfbb382efeb1008c9b0f`.
  Fresh archive: 214,655,196 bytes, SHA-256
  `241e79c44b595bfb65cfa78968e01bd3c18dd1ebd16fb1ee36f2861b7a543561`.
  The 200.7-second offline drill passes installed v1/v2 compilation, schema 12,
  MFA/replay/revocation and worker/Redis failure-recovery. Internal network,
  zero published ports, synthetic data and cleanup of five containers/three
  volumes are confirmed. Backend (42), SDK (36) and static UI (4) file content
  matches are exact. The corrected proof accurately states content identity
  only; Git provenance has not been established.

Final artifacts are retained at `/tmp/lotuspond-v2-final.zwU1UW`; older
`/tmp/lotuspond-v2-final.5WdOFT` evidence remains unchanged. Cloud-only serial
Node/subreaper and build-only CA handling remain distinct from ordinary hosted
CI. All product/runtime/customer qualification boundaries above remain open.


## Draft publication and hosted completion

[Draft PR #6](https://github.com/lotuspond-ai/lotuspond/pull/6) targets the reviewed
`codex/executable-v2-design` base `468550703a45c702995391c5d93ac201283d4a50`.
The normally pushed publication head is `5b43e64acbd9e818e979d1cd034479ef9a48d66d`;
its only changes after corrected product source `2b4b3ee` are plan/evidence
Markdown. The fetched GitHub pull-request head matches exactly. The complete
106-file diff is retained and checked from Git; the GitHub API omits some large
patches, so its preview was not treated as the complete review surface.

All twelve jobs in [validation run 38573333807](https://github.com/lotuspond-ai/lotuspond/actions/runs/38573333807)
passed for that head: validate, SDK 3.13, SDK 3.14, collector, evidence, protocols,
voice, docs, execution image, offline installation, restore and monitoring rules.
These are ordinary hosted checks, distinct from the recorded cloud-workspace
adaptations. Subsequent documentation-only completion commits must retain green
checks at their own published head; current results belong in the PR rather than
being inferred from this historical run.

The plan and task reviews are complete with no waived or parked findings. The
worktree and all sibling branches/worktrees/data remain preserved. Full review
reports, failure/success logs, all 26 chronological SDK/v2 implementation rulings
with their costs, artifact identities and publication receipts are retained at
`/tmp/lotuspond-v2-final.zwU1UW`. This completes the approved executable-v2
subsystem, not the complete product: durable execution, authenticated action
services, first-customer workflow acceptance, Studio/operations UX, licensing,
enterprise breadth and production qualification remain in the master scope.

## SDK dependency metadata repair

Subsequent E03 integration inspection found that the backend declared
`lotuspond-sdk==0.1.0` although its source override and frozen lock installed the
reviewed SDK 0.2.0. Frozen sync succeeded, but `uv pip check` correctly rejected
the installed metadata. Commit `acd1b0930a4608d66ae72ccbbfa78167419ce41f`
aligns the requirement to 0.2.0 and adds a backend dependency-metadata CI gate
immediately after frozen sync. Lock regeneration records the SDK's existing
dev-only jsonschema requirement; all 77 locked package identities, versions,
sources and artifacts remain unchanged. No product code or SDK payload changed.

The actual old backend wheel plus SDK 0.2.0 fails offline resolver dry-run;
the repaired pair resolves. Before/after backend wheels differ only in
distribution metadata and RECORD. Frozen sync, lock verification and installed
dependency checking pass. The full application gate passes with the existing
cloud serial Node/subreaper harness: 105 Node tests, 300 backend tests plus 97
subtests (39 database-dependent skips), 1472 SDK tests plus 68 subtests, lint,
schema parity, TypeScript and web build. This metadata-only repair changes no
database behavior; it does not claim a new database integration run.

Independent review of the exact three-file repair reports zero Critical,
Important or Minor findings. The reviewer freshly reproduced both wheel
resolver outcomes and confirmed the installed dependency check, unchanged
package payloads and complete lock equality except for that dev metadata entry.
Reports, wheel metadata, failures and successful logs are retained at
`/tmp/lotuspond-sdk-alignment.pmir2rcb`.

A fresh API image built from an exact archive of `acd1b0930a4608d66ae72ccbbfa78167419ce41f`:
`sha256:8ea1a7a5bc1f1a9e3e9af365cae4c4fbc113d489b0ba731be8fc2909637b01cf`.
It verifies non-editable backend/SDK 0.2.0 metadata, all 43 installed package
requirements, and the existing installed v1 validation/digest and literal-v2
compilation checks. These checks ran as UID 10001 with no network, a read-only
root and bounded temporary scratch; their containers were removed. Build-only
CA mounts and a temporary Buildx cache are environment adaptations. Initial
cache/scratch/stdin probe failures were diagnosed and retained separately.
The prior offline installation drill remains evidence for its earlier source
and image; it was not rerun or relabeled as proof of this new image. Current
hosted CI results are recorded on PR #6 for its exact published head.
