# Executable package v2 implementation progress

The owner approved the specification and ten-task plan on 14 October 2026 and selected subagent implementation with independent review after every task. Implementation starts from approved design commit `468550703a45c702995391c5d93ac201283d4a50` in `codex/executable-v2`, preserving the original checkout and the completed SDK/design branches.

The new worktree passed the inherited SDK contract/input baseline: 38 tests and 68 subtests on Python 3.14.5. The prior SDK has its own complete review, application/database, installed-artifact, offline-recovery and hosted-CI evidence. These baseline results do not establish any v2 implementation or runtime qualification.

Tasks 1–3 are complete after independent review and correction; Tasks 4–10 and final integration/publication gates remain pending. The complete approved product requirement register remains open; this subsystem delivers executable definitions/compiler and runtime-facing contracts, with later engine/runtime/Studio/commercial/operational goals retained.

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
