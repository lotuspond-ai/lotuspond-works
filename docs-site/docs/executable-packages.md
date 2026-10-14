# Executable package v2 foundations

The standalone Python SDK has a separate `lotuspond_sdk.executable` namespace
for executable definitions. This increment provides bounded JSON decoding,
reviewed RFC 8785 canonicalization, strict validation/report helpers, closed data schemas, static mapping inference,
fixed value-free errors, structured package/control authoring and exact local
catalog documents. Compilation, runtime-facing records and explicit v2 CLI
commands are subsequent implementation tasks. No workflow is
executed and no local artifact grants authority.

The schema identifier is `lotuspond.workflow-package.v2`; the canonicalizer is
`lotuspond.workflow-package.jcs.v2`. V1 retains its existing APIs, registry
format and historical canonical bytes.

Package raw input and normalized content are each at most 1,048,576 bytes, with
32 container levels and 10,000 items, including object keys. The wire boundary
is 65,536 bytes. Reports use 8,388,608 bytes, depth 32 and 262,144 items, counting
complete ASCII output and its final newline. Package admission always uses its
own smaller limits. Supplied raw bytes, structural projection and complete
normalized output are separate checks. Projection counts a structural byte
lower bound without selecting numeric domains or spellings; model validation
then checks actual JCS bytes after domain selection and default insertion.
Reports measure their actual complete ASCII representation.

JCS includes normalized defaults, preserves array order and sorts object names
by UTF-16 units, including names such as `10` before `2`. Strings retain their
Unicode code points without normalization. Duplicate keys, invalid Unicode,
nonfinite numbers, cycles, coercions and unknown model fields are refused.
Known models are projected and revalidated after mutation or bypassed
construction without invoking their serializers.

Integer fields reject booleans, floats and values outside
±9,007,199,254,740,991. Declared numbers use the separate finite IEEE-754 domain:
JCS writes `1.0` as `1`, negative zero as `0`, and some large floats as integer
spellings. The raw decoder permits bounded integers until the declared field
chooses the numeric domain, so rereading that output remains possible. Exact
quantities use fixed-scale decimal strings.

Canonicalization calls exactly pinned `rfc8785==0.1.4` under Apache-2.0, reviewed
with source SHA-256
`e545841329fe0eee4f6a3b44e7034343100c12b4ec566dc06ca9735681deb4da`.
Attributed RFC number vectors and a separate Node oracle verify the numeric
spellings and Unicode ordering; Node is a test tool rather than an SDK runtime
dependency.

`ExecutableError` carries a closed code/message, at most 20 sanitized issues
and an explicit truncation flag. Pointers retain only fixed field names and
numeric offsets; supplied names and values never enter ordinary diagnostics.
Truncation preserves failure. Safe file/decode failures retain the existing
SDK operational error codes.

## Typed schemas and mappings

`validate_value(raw, schema, definitions)` validates public data and returns JSON;
only a declared `number` domain normalizes integer/float spellings to finite
IEEE-754 floats. `parse_schema` checks closed shape; `resolve_schema` additionally
checks and resolves the entire local definition set. Definitions are ordered
`{"name": "...", "schema": {...}}` records, at most 64, with acyclic references
at most eight levels deep. Expansion is bounded before constructing its result.

Data schemas discriminate on `kind`: `null`, `boolean`, `integer`, `number`,
`string`, `decimal`, `array`, `object`, and local `ref`. Integer/number optional
`min` and `max` normalize to null. Strings require `max_length` in 1..65,536;
arrays require `items` and `max_items` in 0..1,000. Objects require an ordered
`fields` list (at most 64), each with unique `name`, `schema`, and exact boolean
`required`. Unknown fields are refused. Optional means possibly absent; a
present null only satisfies a null schema. `decimal` requires `precision`
1..38 and `scale` 0..18, with scale no greater than precision. Values have exactly
that many fractional digits, no exponent, leading plus, redundant leading zero
or negative zero. Scale zero has no decimal point. `ref` names one local schema.

`infer_mapping(raw, TypeEnvironment(...))` returns compiler flow metadata and
never evaluates expressions. These are the closed mapping shapes:

```json
{"kind":"literal","schema":{"kind":"integer"},"value":1}
{"kind":"ref","source":{"kind":"package_input"},"pointer":"/customer/name"}
{"kind":"object","fields":[{"name":"count","value":{"kind":"literal","schema":{"kind":"integer"},"value":1}}]}
{"kind":"array","items":[],"item_schema":{"kind":"data","schema":{"kind":"integer"}}}
{"kind":"call","fn":"add","args":[{"kind":"literal","schema":{"kind":"integer"},"value":1},{"kind":"literal","schema":{"kind":"integer"},"value":2}],"scale":null,"rounding":null}
```

A reference source is `package_input`, `step` with an `id`, `iteration_item`, or
`iteration_index`. The caller supplies only lexically available step outputs;
future steps and private branch outputs are absent from that environment.
Pointers use RFC 6901 `~0`/`~1` escaping. Array indices use canonical nonnegative
decimal spelling and must fit the declared bound. A bounded array element can
still be absent. Expressions have at most 64 nodes and eight levels, counting
the root. Schema nodes and literal data do not count as expression nodes, but
remain subject to JSON budgets.

Calls have fixed signatures: `concat` takes 2..63 strings; `add`, `subtract`,
`multiply`, and `divide` take two integers or two decimals of matching scale;
`eq`, `ne`, `lt`, `le`, `gt`, and `ge` take two matching comparable scalar domains;
`and` and `or` take two booleans; `not` takes one. Null and boolean support equality
only. Arithmetic never promotes integer references to number. Decimal arithmetic
requires result `scale` and `rounding` (`reject`, `half_even`, `floor`, `ceiling`),
and has precision 38. Integer arithmetic has the safe-integer result domain;
concatenation adds declared length bounds. Division by zero, integer overflow,
inexact integer division and decimal nonrepresentability are runtime errors;
this layer does not execute or constant-fold even literal arithmetic.

`exists` accepts one direct reference and tests absence, not null/truthiness.
`presence_guard(condition)` returns reference keys only for a direct
`exists(ref)` condition. The later compiler applies those keys only on its true
edge, preserving lexical scope and removing them at inappropriate joins.
`coalesce` takes two compatible data values: its present fallback handles absence
and does not replace present null. Required consumers refuse a possibly missing
value with `unavailable_value`. A parent presence guard never proves that an
optional child exists. `assert_assignable` checks availability, exact domains,
closed fields and containment of declared bounds.

## Internal handle provenance

`ExportSchema` has `data` (with `schema`), `handle` (with `handle_kind`), `object`
(with named `schema`/`required` export fields), or `array` (with `items` export
schema and `max_items`). A declaration constrains an existing flow; it does not
cast data into handles. Public package/model/tool/connector schemas and literals
use `DataSchema`, which has no handle kind. Calls cannot produce handles.

Compiler-only `SourceSymbol(operation, step_id, occurrence_path=(), occurrence_bindings=())` identifies
an operation occurrence. Allowed operation/handle pairs are `action_intent` /
`action_intent`, `human_review` / `human_decision`, `action_dispatch` /
`action_receipt`, and `verification` / `verification_evidence`. Non-intent handles
also carry `intent_source`. Those symbols are static metadata, never authenticated
runtime handles. The later compiler must create them only for the corresponding
validated operation and compare the exact intent origins for review/dispatch;
the later runtime must independently reload authenticated tenant-bound records
and compare exact binding digests.

`FlowObjectType` preserves fields and origins. `FlowArrayType` adds immutable
`elements` for constructor arrays and an explicit `item_schema`; its `items`
summary never stands in for one chosen concrete origin. A selection between
nested arrays retains ordered `alternatives`, their `selection_path` and
`selection_bindings`, rather than flattening their owners or lengths. Identical
alternatives preserve the existing array and its owner. Different alternatives
retain each complete array, including its `iteration_id`, nested alternatives,
concrete elements and maximum length. `max_items` is their maximum, not the
export declaration's potentially larger allowance. An empty array has
`items=None`, `max_items=0` and its declared `item_schema`, creating no origin.
The pure selection helpers are:

```python
select_array_item(array_flow, index: int) -> FlowType
iteration_item_flow(array_flow, *, occurrence_path: tuple[str, ...]) -> FlowType
```

Indexed selection remains possibly absent until guarded. Iteration selection
is present because it describes a current element, and requires a nonempty,
stable path identifying that selection. Both refuse selection from an empty
array. Concrete array/object aliases preserve the exact original handle symbol.
Unknown selection among different handles produces `OriginChoice(options,
selection_path, selection_bindings=())`, preserving order, multiplicity,
nested choices and positional slots (`None`). An absent optional array occupies
a `None` alternative; a present empty array remains an explicit array of length
zero. Selection from an empty alternative is impossible, not a missing element
inside a nonempty array. Its positional origin slot remains `None` for
correlation, while a current inner iteration item is present. Required nested
fields retain their own availability checks. The compiler must traverse those choices, use the
same selector path for correlated fields, distinguish different selection paths
or reordered slots, and never choose the first option or treat an unbound
`items` summary as a concrete handle.

For repeated operation outputs, `FlowArrayType.iteration_id` identifies the
producing iteration. Only source occurrence slots spelled `$item:<iteration_id>`
are bound when that array is selected. The compiler creates such slots only for
operations actually authored inside that iteration. Aliases of outside handles
retain their origin, including an outer iteration's still-symbolic slot when an
inner result is selected. Already-bound outside choices retain their selector
identity; binding recurses into their options only to specialize slots belonging
to the current producer. An explicitly owned selector slot can likewise be bound.

`SourceSymbol.occurrence_bindings` and both `OriginChoice.selection_bindings` and
`FlowArrayType.selection_bindings` are immutable ordered
`tuple[tuple[str, tuple[str, ...]], ...]` records of owner and binding path. They
retain ownership after `$item:<owner>` is replaced by an index/selection path,
and participate in exact equality. At most 32 unique Identifier owners are
allowed. Binding paths are nonempty with at most 32 strings of 1..512 characters.
Duplicate owners, including identical or conflicting repeats, are refused; a
bound owner cannot also have an unresolved slot in that same path. Aliases retain
all bindings unchanged. Later plan serialization must preserve binding order,
owner/path pairs, alternatives, empty arrays and all choice slots deterministically;
none may be flattened, deduplicated or omitted from provenance comparison.

`FlowArrayType.alternatives` is either null or an immutable tuple of 1..1,000
arrays/absent slots, with at least one array. This representation requires an
explicit item schema, excludes simultaneous concrete `elements` or a shared
`iteration_id`, and is recursively revalidated under the flow bounds. Its
selection metadata belongs to that alternative selection; each alternative
retains its own producer owner. The selection helpers distribute through those
alternatives before merging results. Nested choices remain conservative metadata;
the compiler must carry their complete structure through region exports and joins.
Data bounds are checked for every concrete array element and retained in its
summary. All model inputs are reprojected/revalidated after mutation or bypassed
construction. Refusals use fixed `ExecutableError` codes and reveal no supplied
values. No function here executes a workflow or grants authority.

For integration, `parse_mapping(raw, definitions)` must run before the enclosing
model's final normalized JCS measurement: validating a `Mapping` annotation alone
checks its shape but does not choose the numeric domain of a literal's raw
`value`. The helper resolves declared literal schemas and normalizes numbers
before measuring complete mapping bytes. Likewise, resolve an expected export
with `parse_export(raw, definitions)` before passing it to `assert_assignable`.
The later package validator must apply these steps with its own definition set
and retain its complete package byte/item/depth limits.

## Structured packages and exact catalogs

```python
from lotuspond_sdk.executable import (
    canonical_executable_json,
    decode_executable_bytes,
    executable_digest,
    validate_catalog,
    validate_executable_package,
)

package = validate_executable_package(decode_executable_bytes(source_bytes))
normalized_bytes = canonical_executable_json(package)
content_digest = executable_digest(package)
catalog = validate_catalog({"documents": []})
```

These are local structural checks. They do not compile typed control flow,
resolve a dependency closure, verify provider support or external binaries,
run a workflow, or grant authority. A model constructor or JSON schema alone
is not the complete admission boundary. Use the public validation functions
even for already constructed or previously validated models.

`ExecutablePackage` requires `schema_version`, `package_id`, `version`, `name`,
`input_schema`, `output_schema`, `schemas`, `body`, `models`, `connectors`,
`agents`, `subworkflows`, `evaluations`, `policies`, `knowledge`, `artifacts`,
`secret_refs`, `environment`, `limits`, and `assurance`. Every declaration family
is required, including empty lists, unique by `id`, and bounded to 64 entries.
The input/output boundaries use public `DataSchema`; internal body exports use
`ExportSchema`. `name` is at most 160 characters. Each local schema definition
set is independently bounded, acyclic, and checked even when unused.

A declaration has `id` and `ref`. An `ArtifactRef` has exact `kind`, `name`,
`version`, and lowercase 64-hex `sha256`. Kinds are `workflow`, `connector`,
`model`, `agent`, `evaluation`, `policy`, `knowledge`, and `artifact` and must
match the declaration family. Names use the existing Identifier contract;
versions use the existing exact Version contract, never ranges or wildcards.
Repeated identity/version pins with different digests are refused.
Connector declarations additionally have `credential_ref` (default null), which
must name `secret_refs`. Knowledge declarations additionally require
`required_freshness_seconds` (1..31,536,000), unique `allowed_classifications`
(at most 64), and `citation_schema`.

Keep credentials and customer documents outside packages. Reference slots accept
bounded identifiers or pinned references, never credential objects or URL
substitutions. Free text is untrusted author content; these checks do not claim
to discover secrets hidden in arbitrary text.

`environment` requires literal `runtime_contract="lotuspond.runtime-contract.v1"`,
`platforms` as unique `{os, architecture}` pairs, and unique
`required_capabilities`, `destination_classes`, and `operation_classes` lists.
Every list has at most 64 entries. These names use Identifier; this increment
does not invent a supported OS/capability vocabulary. `assurance` requires
unique `evaluation_refs`, `outcome_rule_refs`, and `required_classification`.
The reference lists name package evaluations and locally authored verification
rule IDs respectively. These are declarations, never observed qualification.

`limits` requires `max_steps` (1..10,000), `active_seconds` (1..86,400),
`elapsed_seconds` (1..31,536,000), `retained_waits` (0..10,000),
`max_input_tokens` and `max_output_tokens` (0..10,000,000), and
`max_cost_microusd` (0..1,000,000,000,000). `AgentLimits` has `max_steps`
(1..1,000), `timeout_seconds` (1..3,600), and the same token/cost fields.
`RetryPolicy` requires `max_attempts` (1..10), `timeout_seconds` (1..86,400),
`backoff_seconds` (0..3,600), and unique `retry_codes` drawn only from
`unavailable`, `rate_limited`, and `timeout_before_dispatch`. A possibly
completed write does not qualify for blind retry. Waiting, compute, elapsed
time, retention, spend and availability remain distinct later runtime duties.

### Bodies and steps

`Body` has ordered `steps`, `result: Mapping`, and `result_schema: ExportSchema`.
There are at most 128 authored steps across all bodies, with package-wide unique
step IDs and at most eight nested decision/parallel/iteration controls. No edges,
back edges, implicit start/end nodes, custom code, or unsupported join modes
are accepted. Array order affects the digest. Every step has `id` and `kind`:

| Kind | Additional fields |
| --- | --- |
| `transform` | `mapping`, `output_schema` (data) |
| `decision` | `condition`, `then_body`, `else_body`; branch export declarations must match after local schema resolution |
| `parallel` | `branches` (1..64 `{id, body}` with unique IDs), `join="all"` |
| `iteration` | `input`, `max_items` (1..1,000), `concurrency` (1..64 and no greater than `max_items`), `body` |
| `connector_read` | `connector_ref`, `operation`, `target`, `input`, `retry` |
| `agent` | Required `input` mapping, exactly one `model_ref`/`agent_ref`, `instructions` (1..16,384), data `output_schema`, `read_tools` (default empty, at most 32), `knowledge_refs` (default empty, at most 64), `limits`, `retry` |
| `action_intent` | `connector_ref`, `operation`, `target`, `input`, `policy_ref`, `approval` (`none`/`human`), exact boolean `independent_review`, `expiry_seconds` (1..2,592,000) |
| `human_task` | `assignment_policy_ref`, `timeout_seconds` (1..31,536,000), optional `escalation_policy_ref`; `mode="form"` requires only data `form_schema`, `mode="review"` requires only `intent: RefMapping` |
| `action_dispatch` | `intent: RefMapping`, optional `decision: RefMapping`, `retry`, optional `compensation` |
| `timer` | `delay_seconds` (1..31,536,000) |
| `external_wait` | `signal_schema`, `correlation`, `expiry_seconds` (1..31,536,000), `assignment_policy_ref`, optional `escalation_policy_ref` |
| `subworkflow` | `workflow_ref`, `input` |
| `outcome_verification` | `receipt: RefMapping` and all `VerificationSpec` fields |

`ReadTool` is `{connector_ref, operation, retry}`, with package aliases in a
package and exact `ArtifactRef` connector pins inside model/agent catalogs.
`VerificationSpec` requires `connector_ref`, `read_operation`, `target`, `input`,
`rule_id`, `version` (ReferenceRevision), and nonempty `rules`.
`CompensationSpec` requires the action-intent payload, `retry`, and its own
`verification: VerificationSpec`; it is a separately bound write declaration,
not an automatic inverse. Optional kind-specific fields normalize to null.

A `Rule` uses `op` as its discriminator. `eq`/`ne`/`lt`/`le`/`gt`/`ge` require
`readback_pointer` and typed `expected: Mapping`; `present` requires only a
`readback_pointer`; `and`/`or` require `rules` with 2..63 children. Pointers use
RFC 6901 escaping and are at most 512 characters. Each rule tree and each
unnamed rule list has at most 64 total nodes and depth eight. Leaf rules always
inspect readback data: literal-only and model-assertion acceptance forms are
unsupported. Rules declare deterministic checks and are not evaluated here.

### Catalog documents

`CatalogBundle` is `{documents: [...]}`, bounded to 64 documents per non-workflow
kind and to the complete 1 MiB/depth-32/10,000-item budget. Documents with the
same identity/version and identical normalized content are retained in order;
every occurrence counts toward the family and complete raw/normalized bundle
limits. Differing normalized content for one identity/version is
`dependency_conflict`. The compiler deduplicates admitted definitions in its
resolved closure. Workflows are separately supplied complete v2 packages.
`CatalogDocument` requires exact integer `format_version=1`, `kind`, `name`,
`version`, local `schemas`, and one `definition` matching its outer `kind`.
There is no redundant inner kind field.

| Definition kind | Required fields |
| --- | --- |
| `connector` | `artifact_sha256`, `operations` (1..64 unique operation IDs) |
| `model` | `profile`, `configuration_revision`, `configuration_sha256`, `input_schema`, `output_schema`, `limits`; optional `read_tools` defaults empty |
| `agent` | Pinned `model_ref`, `instructions`, `input_schema`, `output_schema`, `limits`; optional `read_tools` defaults empty |
| `evaluation` | `input_schema`, `rules` (1..64 unique `{rule_id, version, rule}` records) |
| `policy` | `approval`, `independent_review`, `assignment`, `allowed_operation_classes`, `allowed_capability_classes`; optional `escalation` defaults null |
| `knowledge` | `source_revision`, `content_sha256`, `freshness_seconds` (1..31,536,000), `classification`, `citation_schema` |
| `artifact` | `media_type` (1..160), `content_sha256`, `size_bytes` (0..safe-integer maximum), `classification` |

A connector operation has `id`, `target_schema`, `input_schema`, `output_schema`,
`effect` (`read`/`write`), `idempotency` (`none`/`target_key`), and `reconciliation`.
A global target uses `{"kind":"null"}`. Reconciliation has `mode="none"` with
null `read_operation`, `absence_rules`, and `effect_rules`, or `mode="read_back"`
with all three required. The read operation must name a local operation whose
effect is `read`; absence/effect rule lists are nonempty. Write compilation
requires read-back; structural catalog admission alone does not do so.

`canonical_catalog_document(raw) -> bytes` validates and canonicalizes the
complete normalized document including `format_version`, identity, definitions,
schemas and defaults. `catalog_document_digest(raw) -> str` hashes those bytes.
That digest supplies `ArtifactRef.sha256`. It excludes the separate reference
wrapper and is distinct from the claimed connector binary/content checksums.
No artifact is downloaded or checked for availability by these functions.

### Admission sequence and compiler obligations

The public boundaries first project only fixed known model classes into bounded
JSON, then preflight step/control/schema/rule limits before recursive shape
validation. They run strict shape validation, normalize every mapping using
`parse_mapping(mapping, local_definitions)`, resolve every data/export schema,
and check local IDs/references/conflicts outside Pydantic. This preserves fixed
domain codes such as `duplicate_identifier`, `unknown_reference`, and
`dependency_conflict`. Malformed field/union shapes use `invalid_package` or
`invalid_catalog`. Finally the shared Task 1 validator revalidates the normalized
models, projects every default, and measures complete JCS bytes. No serializer
hooks or untrusted exception text are used. Catalog definitions use their own
schema scope, never the caller package's definitions.

The compiler verifies the complete supplied dependency closure and
all pins, enforces lexical ordering/branch availability and every mapping type,
checks operation effects and read-only tools, validates rules against readback
schemas, maintains exact handle provenance, and binds human decisions, dispatch,
compensation and verification to the same immutable intent/target. It
also enforces conservative retries/iteration/call expansion and verification
coverage on every effect-producing normal completion path. A structural
reference to an existing but unavailable step is not evidence of lexical
availability. Declared agent/review/write capabilities confer no authority;
artifact availability remains `not_checked` until later admission duties.

## Pinned local compilation

`resolve_dependencies(raw, dependencies, catalog)` and
`compile_package(raw, *, dependencies, catalog)` require explicit local inputs,
including `[]` and `{"documents": []}` for empty sets. They perform complete
package/catalog admission again, including constructed or mutated known models.
The resolver admits every occurrence before deduplication. At most 63 raw
additional package sources are accepted, each under the package byte limit;
including root this bounds package-source input to 64 MiB. Root counts as depth
one and as one of the maximum 64 packages; maximum dependency depth is eight.
Every declared pin is required even if no step uses it. Catalog-internal agent
and tool pins are required recursively. Identical content shares one definition;
conflicts, missing/mismatched pins, cycles and unrelated supplied packages or
catalog entries refuse. Select the needed catalog closure before calling.

`DependencyClosure` contains the validated `root`, read-only `packages` and
`package_digests` maps keyed by `(package_id, version)`, and `catalog` and
`catalog_digests` maps keyed by `(kind, name, version)`. Values are fresh validated
model snapshots; these maps are not authenticated runtime records.

```python
from lotuspond_sdk.executable import (
    compile_package, canonical_plan_json, compiled_plan_digest,
)

plan = compile_package(raw_package, dependencies=[], catalog={"documents": []})
plan_bytes = canonical_plan_json(plan)
plan_sha256 = compiled_plan_digest(plan)
```

`CompiledPlan` has fixed schema/compiler revisions, root `package_sha256`,
`dependency_digests`, `catalog_digests`, `steps`, `regions`, `edges`, `requirements`,
root `limits`, `maximum_step_attempts` and `outcome_obligations`. Its fixed flags
are `artifact_availability="not_checked"` and `authority_granted=false`.
Canonical plan JCS is limited to 8 MiB, depth 32 and 262,144 JSON items. Plan
serialization revalidates known records, strict flags, complete flow provenance
and output-type digests. A supplied plan still cannot prove that source compilation
occurred; a runtime must reload pinned definitions and validate/recompile them.

Each plan step records package digest, authored step ID, region/source pointers,
kind, normalized source digest, input-mapping digest, complete output flow and
its digest, definition pins, operation where singular, and conservative attempts.
Instructions, literal values and resolved payloads are not copied. A dispatch
with multiple possible operations retains its exact intent-origin choice tree;
consumers must follow those originating intent steps, rather than interpret a
null singular operation as an unrestricted operation. Regions record ordered
step IDs, stable `region_id`, result-mapping digest, full export flow and attempts.
Region IDs are `body`, `decision:<step_id>:then`/`else`,
`parallel:<step_id>:<branch_id>`, or `iteration:<step_id>:body`; moving the owning
step changes source pointers but preserves this authored region identity. Edges distinguish
sequence, then/else, parallel branch, iteration and callee-template links.
Packages/catalog identities are sorted; authored step/branch order and every
origin option, binding, array alternative and empty position remain ordered.

Each sequence body costs one plus its steps. A regular step costs one, activity
retries cost `max_attempts`, and an agent costs
`AgentLimits.max_steps * retry.max_attempts`; agent limits already include its
model/tool attempts. A decision costs one plus its maximum branch body; parallel
costs one plus every branch body; iteration costs one plus its declared
`max_items * body`; a call costs one plus the callee body. Compensation adds one
intent, a review when required, its bounded dispatch attempts, and one verification.
Counts exceeding package `max_steps` or 10,000 refuse. Child environment classes,
platforms, credentials and all limits cannot expand the parent's envelope.
These checks do not reserve funds or enforce dynamic time/token/spend usage.

An agent step now requires an explicit `input: Mapping`, checked against the
selected model or reusable agent input schema. This draft-v2 correction avoids
implicit access to package input. Read tools must be read-only exact operations
allowed by the pinned definition; reusable agent/model input/output and limits
must agree. Catalog reconciliation rules resolve `expected` mappings with the
write operation's input schema as `package_input`, and `readback_pointer` with
the referenced read operation's output schema. Each named evaluation rule uses
its evaluation input schema for both contexts. Neither catalog context contains
workflow steps or iteration values. Authored verification rules use the workflow
lexical mapping scope. Presence checks refine comparisons in conjunctions, never
across alternatives.

Verification must cover every dispatched receipt on every normal structured
path, including receipts exported through decisions, parallel joins and repeated
operations. An empty or possibly empty unrelated loop does not prove verification
of an earlier action. Private compiler value identities preserve direct aliases,
object/array projections, branch selections, iteration bindings and exact
expression descriptors. Verification checks the same target identity, exact
connector pin, and that intent's declared reconciliation read operation. Its
read-back input is separately typed and may differ from write input. Equivalent
expressions that require algebraic reasoning or runtime values are not assumed
equal; there is no evaluator or constant folding. Shared descriptors are handled
as bounded DAGs, with context-specific transforms and deterministic fingerprints.
Pointer projection and iteration-item selection memoize each shared node for the
complete selected token/slot context. Each operation shares a 262,144-unit work
budget across traversal, owner binding and fingerprinting; exceeding it refuses
with `expansion_exceeded`. Fingerprint cache entries retain their nodes, so object
ID reuse cannot confuse identities. Python object IDs never enter a plan or digest.

Source occurrences begin with the package digest. Each iteration owner is
`"l" + sha256(JCS([package_digest, source_pointer]))[:63]`; owned slots are
`$item:<owner>`. Complete owner bindings remain in flow metadata and digests.
Decision selection paths are ordered
`[package_digest, decision_source_pointer, ...enclosing_iteration_slots]`.
`OriginChoice.selection_bindings` records each specialization as an ordered
`{owner, path}` pair, even after a slot becomes a numeric index or another current
iteration slot. A private symbolic selector carries both that complete path and
all ordered bindings. This distinguishes `produce[0]` from the current consuming
item while retaining direct aliases of that item.

Decision merges install the same selector in public flow and private target
identity. Reference inference and `iteration_item_flow` specialize public owners
using the safe selection helpers; private owner binding mirrors their path and
binding semantics. Target restriction compares the complete selector, including
bindings, for every possible originating intent. Coverage may rebase a selected
decision to its producer guard only after its complete selector equals that
producer template specialized by the selected receipt's ordered bindings. It
cannot rebase a different index or unrelated selection. Existing complete-array
coverage still requires all producer occurrences; a subset or unrelated empty
loop cannot discharge them.

Plan projection and output-type digests retain every selection path, ordered
binding, option and array alternative. Diff, compatibility and runtime consumers
must not flatten them to the authored decision ID, pointer or representative
item. Callees are emitted once as package-scoped templates; distinct authored call edges
identify each invocation, and public callee outputs are data-only. Runtime call
frames must retain the call-site identity when instantiating those templates.
All immutable intent/review/receipt symbols are compiler metadata, not authority.
External artifacts, provider behavior, actual effects and dynamic enforcement
remain unchecked.
