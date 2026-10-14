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

## Action bindings and runtime wire declarations

These local contracts compare supplied declarations. They do not authenticate an
actor, resolve evidence, verify a signature, dispatch a request or confer authority.
A later runtime must reload authenticated tenant-bound records and supply current
installation, generation, recovery epoch, recipient and time independently.

```python
from lotuspond_sdk.executable import (
    build_action_intent, check_decision, check_command_binding,
    canonical_wire_json, event_payload_digest, validate_runtime_event,
)

intent = build_action_intent(binding)
decision_result = check_decision(intent, decision, context)
command_result = check_command_binding(command, intent, decision, context)
# Even a valid result has authority_granted=False and signature_status="not_checked".
event["payload_sha256"] = event_payload_digest(event["event_kind"], event["payload"])
checked_event = validate_runtime_event(event)
record_bytes = canonical_wire_json(checked_event)
```

Every complete record has a fixed `record_type` and exact integer
`format_version=1`. Specialized validators normalize omitted fixed metadata;
`validate_wire` requires a `record_type` to select a record. Optional fields below
normalize to null; every other field is required. Payloads and evidence references
are fragments within records, with no separate version/tag. Contexts and reports
are local inputs/results, not wire records. No raw target, input, secret, evidence
body or evidence URL is included.

Runtime opaque IDs are 1–128 ASCII characters matching
`[A-Za-z0-9][A-Za-z0-9._:-]*`. Authored `step_id`, connector name, operation and
`rule_id` use the existing 1–64 character `Identifier`; connector version uses
`Version`; rule version and target operation reference use `ReferenceRevision`.
All SHA-256 fields are 64 lowercase hexadecimal characters. Timestamps, epochs,
generations, sequences and attempts are exact nonnegative safe integers; bool and
float lookalikes refuse. Timestamps are UTC epoch milliseconds. Record projection
and complete normalized JCS each enforce 65,536 bytes, depth 32 and 10,000 items.
Evidence lists have at most 64 entries. Unicode code points are preserved.

| Record | Fields in addition to fixed metadata |
| --- | --- |
| `ActionBinding` / `action_binding` | `installation_id`, `recovery_epoch`, `workspace_id`, `project_id`, `environment_id`, `execution_id`, `step_id`, `action_id`, `package_sha256`, `configuration_sha256`, `plan_sha256`, `connector_name`, `connector_version`, `connector_artifact_sha256`, `operation`, `target_sha256`, `input_sha256`, `policy_sha256`, `approval_mode` (`none`/`human`), `independent_approval` (exact bool), `requester_id`, `issued_at_ms`, `expires_at_ms` |
| `ActionIntent` / `action_intent` | `binding` (`ActionBinding`), `sha256` |
| `HumanDecision` / `human_decision` | `decision_id`, `intent_sha256`, `decision` (`allow`/`deny`), `actor_id`, `actor_kind` (`human`/`service`/`worker`/`model`), `policy_sha256`, `issued_at_ms`, `expires_at_ms` |
| `RuntimeCommand` / `runtime_command` | `command_id`, `replay_id`, `intent_sha256`, optional `decision_sha256`, `installation_id`, `recovery_epoch`, `workspace_id`, `project_id`, `environment_id`, `execution_id`, `action_id`, `worker_id`, `fencing_generation`, `issued_at_ms`, `expires_at_ms` |
| `ActionReceipt` / `action_receipt` | `receipt_id`, `action_id`, `intent_sha256`, `attempt`, `target_operation_ref`, `status` (`accepted`/`observed_effect`/`unknown`), `evidence_refs` |
| `OutcomeRecord` / `outcome_record` | `outcome_id`, `action_id`, `intent_sha256`, `receipt_id`, `state` (`verified`/`not_verified`/`unknown`), `rule_id`, `rule_version`, `evidence_refs`, `observed_at_ms` |
| `RuntimeEvent` / `runtime_event` | `event_id`, `installation_id`, `recovery_epoch`, `workspace_id`, `project_id`, `environment_id`, `execution_id`, optional `step_id`, `action_id`, `attempt`, then required `source_id`, `source_generation`, `source_sequence`, `event_kind`, `observed_at_ms`, `payload`, `payload_sha256` |

`EvidenceRef` requires `artifact_id`, `sha256`, `media_type` (1–128 code points),
`classification` (`public`/`internal`/`confidential`/`restricted`) and `source_kind`
(`target_readback`/`signed_receipt`/`deterministic_check`). `verified` outcomes
require nonempty evidence; every outcome state requires rule identity/version.
The SDK checks declarations only: even `signed_receipt` provenance is unverified,
and receipt acceptance or a claimed observed effect never implies verification.

### Binding checks and canonical identities

`BindingContext` requires all of `installation_id`, `recovery_epoch`,
`workspace_id`, `project_id`, `environment_id`, `execution_id`, `step_id`,
`action_id`, `package_sha256`, `configuration_sha256`, `plan_sha256`,
`policy_sha256`, `worker_id`, `fencing_generation`, `requester_id`, `now_ms`.
No environment or wall clock is read. `check_decision` uses the applicable
intent/decision fields; worker/generation checks apply to commands.

Intent and decision lifetimes must be positive and at most 2,592,000,000 ms
(30 days); commands at most 300,000 ms. At comparison time each applicable
interval must satisfy `issued_at_ms <= now_ms < expires_at_ms`. Decisions must
start no earlier than the intent and expire no later. `none` approval requires
no decision and a null command decision hash; `human` requires the exact allowed
human decision. Independent review excludes the requester. When approval is
`none`, the independence declaration remains hashed but has no reviewer to check.
Logical action identity has no attempt number; safe retry delivery IDs and
attempts do not alter the binding digest.

All public helpers reproject exact known model classes and revalidate, including
mutated or `model_construct` instances. Constructors and `model_validate` alone
perform shape checks and are **not complete admission**. Subclasses and arbitrary
serializers are not trusted. Malformed shape, lifetime, digest or cross-record
references raise `ExecutableError("invalid_wire")`; malformed context raises
`invalid_context`. Explicit traversal/Unicode/numeric bounds retain their fixed
errors, including `input_too_large`, `input_too_deep`, `input_too_complex`,
`cyclic_input`, `invalid_unicode`, `nonfinite_number` and `safe_integer` when
raised by input projection. Errors do not echo supplied strings or identifiers.

All inputs are admitted before comparison. Well-formed inconsistencies return a
`BindingReport`, in this order: scope and hashes (`context_mismatch`), epoch
(`stale_epoch`), command generation (`stale_generation`), command recipient
(`wrong_recipient`), time (`not_yet_valid`/`expired`), then intent/decision identity
(`intent_mismatch`) and approval/actor consistency (`approval_required`,
`approval_unexpected`, `approval_denied`, `invalid_approver`,
`independence_required`). Time checks visit intent, then command if present,
then decision. An unexpected decision in `none` mode refuses; a missing decision
in `human` mode refuses. A supplied human-mode decision's full digest is compared
before denial/actor checks. A structurally matching result is `binding_valid`.

`BindingReport` requires `valid` (exact bool) and `code` from that fixed vocabulary;
`valid` must equal `code == "binding_valid"`. Its fixed defaults are
`signature_status="not_checked"`, `assurance="synthetic-contract-evaluation"`
and `authority_granted=false`. `binding_report_json` validates and emits bounded
ASCII JSON plus newline. It contains no source records.

| Public helper | Complete validation and digest coverage |
| --- | --- |
| `validate_action_binding`, `validate_action_intent`, `validate_human_decision`, `validate_runtime_command`, `validate_runtime_event`, `validate_action_receipt`, `validate_outcome_record` | Admit their named record with wire bounds and domain checks; every contained intent binding hash and event payload hash is recomputed. |
| `validate_wire(raw)` | Selects one of the seven complete records by tag and performs the same admission. |
| `build_action_intent(binding)` | Admits an `ActionBinding`, hashes its complete normalized JCS including fixed metadata, and returns an admitted `ActionIntent`. |
| `canonical_binding_json(binding)` | Complete normalized `ActionBinding` JCS bytes, without a newline. |
| `intent_digest(intent)` | Takes an **ActionIntent**, rechecks its embedded binding, returns the binding hash. It does not hash the intent wrapper. |
| `canonical_decision_json(decision)`, `decision_digest(decision)` | Complete normalized `HumanDecision` JCS and its SHA-256, including actor, policy, times and fixed metadata. |
| `canonical_wire_json(record)`, `wire_digest(record)` | Complete admitted record JCS and its SHA-256, including every record/delivery field. An intent's full wire hash differs from its binding hash. Task 8 command signatures must cover the complete command bytes. |
| `canonical_event_payload_json(event_kind, payload)`, `event_payload_digest(event_kind, payload)` | Admit the exact payload variant and every nested record/hash; encode/hash the normalized payload, including nested defaults. Outer event identity references are checked only by complete event admission. |
| `validate_binding_context`, `validate_binding_report`, `binding_report_json` | Revalidate the explicit local context/report, including fixed report flags. |
| `check_decision(intent, decision, context)`, `check_command_binding(command, intent, decision_or_none, context)` | Admit every argument, then apply the ordered consistency checks. They do not verify signatures or authenticate supplied context. |

A standalone decision/command/receipt/outcome has an intent hash but no embedded
binding, so its admission checks shape/lifetime/evidence declarations only.
Use the binding checks with the corresponding complete intent; later transition
logic must reload and correlate receipts, decisions and outcomes. Never treat
standalone syntactic admission as proof that an intent exists or was approved.

### Strict runtime event payloads

`event_kind` selects the payload model; payloads have no redundant discriminator
and reject every extra field. `S` means `step_id` and `attempt` required, with
`action_id=null`. `A` means all three required. `E` means all three null.
Omitted null references normalize to explicit null. All fields listed in each
payload row are required.

| Event kind | Payload fields | References |
| --- | --- | --- |
| `admission` | `package_sha256`, `configuration_sha256`, `plan_sha256`, `recovery_epoch` | E |
| `step_started` | `input_sha256` | S |
| `step_result` | `output_sha256` | S |
| `step_failed` | `code` | S |
| `wait_started` | `wait_id`, `kind` (`timer`/`external`/`human`), `correlation_sha256`, `not_before_ms`, `expires_at_ms` | S |
| `signal_received` | `wait_id`, `correlation_sha256`, `signal_sha256` | S |
| `timer_elapsed` | `wait_id` | S |
| `intent_created` | `intent` (`ActionIntent`) | A |
| `decision_recorded` | `decision` (`HumanDecision`) | A |
| `action_dispatched` | `command` (`RuntimeCommand`) | A |
| `action_receipt` | `receipt` (`ActionReceipt`) | A |
| `dispatch_timeout` | `intent_sha256`, `may_have_dispatched` (exact bool) | A |
| `reconciliation_observed` | `intent_sha256`, `observation` (`effect_present`/`effect_absent`/`unknown`), `rule_id`, `rule_version`, `evidence_refs`, `qualified_absence` (exact bool) | A |
| `outcome_recorded` | `outcome` (`OutcomeRecord`) | A |
| `execution_completed` | `output_sha256` | E |
| `cancellation_requested` | `reason_code` | E |
| `execution_failed` | `code` | E |

Failure `code` is `unavailable`, `rate_limited`, `timeout_before_dispatch`,
`invalid_output`, `cancelled` or `unknown`. Cancellation `reason_code` is
`owner_requested`, `policy_revoked` or `recovery_fence`. Wait intervals require
`not_before_ms < expires_at_ms`. `qualified_absence=true` requires
`observation="effect_absent"` and nonempty evidence; this is still an unauthenticated
declaration. Every reconciliation result requires rule identity/version.

Complete event admission requires admission payload/outer epoch equality. Nested
intent bindings and dispatch commands must match every shared outer scope,
epoch and action field; intent step IDs also match. Receipt action/attempt and
outcome action IDs must match. A decision has no scope fields to compare: its
intent/actor/policy consistency requires the corresponding intent and context.
Each payload hash covers exactly the normalized payload. Event observation time
is a declaration and does not substitute for `BindingContext.now_ms`.

Transition consumers must separately enforce source sequences, replay/conflict,
current state, cancellation, cross-event intent/receipt/rule correlation and
reconciliation before any unknown dispatch is retried. Signature consumers must
use full canonical command bytes with the runtime-command context. Schema
consumers must retain the outer-kind/payload/reference correlation and domain
checks above; a raw Pydantic union schema alone does not encode all these rules.

## Private authored-source differences

`diff_executable_packages(before, after, include_values=False)` revalidates both
inputs through structural v2 admission and compares their normalized authored
source. It requires no dependency catalog, compiler, provider or filesystem
access. Source hashes cover the complete canonical packages. The report grants
no authority, does not assess behavioral compatibility and cannot establish
publisher trust or runtime admission. V1 differences retain their separate API.

`ExecutableDiff` contains `before_sha256`, `after_sha256`, ordered `changes`, exact
boolean `include_values`, fixed `authority_granted=false` and fixed
`behavioral_compatibility="not_assessed"`. Each `ExecutableChange` contains
`category`, `operation`, `identity`, `before_path`, `after_path` and optional
`values` with `before`/`after` local detail. Ordinary reports contain no instruction,
literal, label, schema-member name, payload or authored JSON-pointer values.
They emit the explicit normalized marker `values:null`; this supplies no payload.
Only an actual `True` enables private local details; integer/string lookalikes
are refused. Stable authored identifiers are deliberately visible.

Categories are `metadata`, `data_mapping`, `control_flow`, `model_tools`,
`connector_action`, `approval_policy`, `outcome` and `limits`; operations are
`added`, `removed`, `changed`, `moved` and `reordered`. Results sort by category,
identity, before path, after path and operation, treating absent paths as empty
strings. Source object-member order has no effect; significant arrays do.
Mapping/schema/rule/tool arrays may be reported as one complete changed field
subtree, keeping untrusted member names out of report paths.

Steps match globally unique authored IDs as `step:<id>`. Regions match `body`,
`decision:<owner_id>:then`, `decision:<owner_id>:else`,
`parallel:<owner_id>:<branch_id>` and `iteration:<owner_id>:body`. Branch IDs are
scoped to their parallel owner. Moving a parent preserves its descendants'
region identities. Moving and editing a step yields both changes. Movement
follows containing-region identity: replacing a branch or owning control
ID can produce a child `moved` change with equal before/after numeric source paths.
Kind replacement yields one `control_flow` step change, with child region/step
additions and removals reported independently. Parent content excludes child
bodies because their steps, results and export schemas are compared separately.
Branch order and relative order among common steps remaining in the same region
are independent changes; additions, removals and moves cause no reorder noise.

Declarations match exact family-local aliases as `<family>:<id>`, schemas as
`schemas:<name>` and secret references as `secret_refs:<reference>`. Aliases
remain distinct even when they pin the same definition. Every declaration
family, exact artifact pin, public input/output schema, package/region result,
assurance requirement, environment declaration and budget participates. A family
name alone identifies relative declaration order; `package` identifies package
fields. Paths use fixed SDK field tokens and numeric positions in each normalized
source; they never interpolate private data-member names or authored pointers.

`validate_executable_diff(raw)` is the final report admission boundary and
`executable_diff_json(raw)` emits complete compact ASCII JSON plus newline. Both
revalidate constructed/mutated reports without calling arbitrary serializers,
enforce exact flags, operation/path consistency, permitted identities/path
tokens, deterministic order and detail privacy, and admit the complete normalized
report under the 8,388,608-byte limit. Actual emitted ASCII bytes including the
newline must also fit; failure never truncates away changes. Report admission
checks structure, not whether supplied hashes/changes are authentic.
`ExecutableDiff.model_dump()` and `.model_dump_json()` use this same complete
fixed format; partial/custom formatting arguments are refused.

Future CLI wrappers must include wrapper fields and the newline within the same
budget, revalidate the nested report, and use the explicit `DIFF_MODELS` allowlist
from `lotuspond_sdk.executable.diff` with the common `report_bytes` boundary.
Appending wrapper text after serializing an already maximal inner report does
not satisfy the complete-output limit. Opt-in detail remains untrusted private
data and must be deliberately stored/displayed by the caller.

## Pure event transition checks

`check_event_transition(state, event, context)` evaluates one fully admitted
`RuntimeEvent` against a bounded supplied snapshot. It does not persist events,
read a clock, authenticate records, execute steps, resolve evidence or grant
permission. `event_digest(event)` uses full event admission and `wire_digest`:
its SHA-256 covers all normalized fields, including ID, tenant, source, generation,
epoch, sequence and observation time. It is distinct from `payload_sha256`, the
intent binding digest and a decision's full-record digest.

The public models and admission helpers are `ContractState` /
`validate_contract_state`, `EventContext` / `validate_event_context`, `KnownEvent` /
`validate_known_event`, `CompletionSummary` / `validate_completion_summary`, and
`TransitionReport` / `validate_transition_report`. Constructors alone are not
complete admission. Every helper reprojects only explicitly supported classes,
revalidates mutable/constructed objects and checks normalized bounds. Local
snapshots and reports are limited to 65,536 JCS bytes, depth 32 and 10,000 items.
`transition_report_json(report)` emits revalidated, deterministic ASCII JSON plus
newline under the common 8 MiB report cap; it never invokes supplied serializers.

| Model | Exact fields |
| --- | --- |
| `ContractState` | Required `phase`: `new`, `running`, `waiting`, `reconciliation_required`, `cancelled`, `failed`, `completed`; required `action_state`: `not_dispatched`, `dispatching`, `accepted`, `observed`, `unknown`; required `outcome`: `unknown`, `not_verified`, `verified`. |
| `KnownEvent` | Required `event_id` (OpaqueId), `sha256` (full normalized event digest). Only one matching known event may be supplied. |
| `CompletionSummary` | Required exact integer counts `required_effects`, `verified_effects`, `not_verified_effects`, `unknown_effects`, `unfinished_steps`, each 0..10,000. The three outcome counts must sum exactly to `required_effects`. |
| `EventContext` required | `installation_id`, `workspace_id`, `project_id`, `environment_id`, `execution_id`, `source_id` (OpaqueId); `recovery_epoch`, `source_generation`, `now_ms`, `prior_source_sequence` (nonnegative safe integers); `package_sha256`, `configuration_sha256`, `plan_sha256` (Digest); `active_step` (exact bool). |
| `EventContext` optional, default null | `step_id` (Identifier), `action_id` (OpaqueId), `attempt` (nonnegative safe integer), `known_event` (KnownEvent), `binding_context` (BindingContext), `intent` (ActionIntent), `decision` (HumanDecision), `receipt` (ActionReceipt), `wait` (WaitStartedPayload), `retry_policy` (RetryPolicy), `completion` (CompletionSummary), `target_operation_ref` (ReferenceRevision), `reconciliation_rule_id` and `verification_rule_id` (Identifier), `reconciliation_rule_version` and `verification_rule_version` (ReferenceRevision). Each rule ID/version pair is both supplied or both null. |
| `TransitionReport` | Required `disposition`: `applied`, `duplicate`, `refused`, `reconciliation_required`; required fixed `code`, `state` (ContractState), `accepted_source_sequence` (nonnegative safe integer), `structurally_retryable` (exact bool). Optional `binding_code` defaults null and may carry only a refusing BindingReport code when code is `binding_refused`. Fixed defaults: `assurance="synthetic-contract-evaluation"`, `authority_granted=false`. |

OpaqueId, Identifier, ReferenceRevision and Digest retain the wire definitions
above. There are no implicit current-time or sequence defaults: initialize the
prior source sequence to 0, making the first new event sequence 1. Attempts are
zero-based: `max_attempts=1` permits index 0 only. Supplied retry codes must be
unique. Nested wire records undergo their full public admission, including
lifetimes and hashes, even when unused by the selected event. If a BindingContext
is supplied, its scope, epoch, three admission hashes, step/action and `now_ms`
must equal the outer EventContext. Its worker/generation remain command-recipient
facts; they are not inferred from the transport source identity. Contradictory
nested contexts or malformed local models raise sanitized `invalid_context`;
malformed nested wire records retain `invalid_wire` and bounded-input errors
retain their specific codes.

`new` requires `not_dispatched` and `unknown`. A completed state cannot contain
`dispatching`, `unknown` action state, or a `not_verified` outcome. Completed
`accepted` or `observed` actions require a `verified` outcome, including at public
state/report admission and serialization after mutation or construction. A
completed `not_dispatched` state may retain `unknown` for zero-effect computation
or `verified` for aggregate completion. A verified outcome cannot coexist with
`dispatching` or `unknown` action state. Other
combinations remain supplied facts; conservative checks prevent contradictory
facts from resetting known outcomes or enabling dispatch. The state summarizes
the relevant action and, at completion, the aggregate execution outcome; it is
not a ledger of all effects. The caller must retain every action and its evidence.

### Evaluation order and context requirements

All three arguments are admitted first. Then the evaluator checks:

1. Outer installation/tenant/execution, source identity/generation and recovery
   epoch. A mismatch returns `context_mismatch` before replay handling.
2. A supplied KnownEvent must name the incoming ID. Matching full digest returns
   `duplicate`; changed normalized record returns `replay_conflict`; a different
   known ID returns `context_mismatch`. Historical identical events can be
   duplicates despite current phase, attempt, wait or lifetime changes.
3. The exact next source sequence. A gap returns `sequence_gap` with reconciliation
   required; an unseen sequence at or below the prior sequence refuses with
   `stale_sequence`. Gap handling preserves effects and terminal phases.
4. Current step/action/attempt bindings and `active_step`, terminal phase and
   cancellation/failure stops; then supplied receipt consistency before
   event-specific phase, binding, time, evidence and completion checks.

Every supplied receipt describes the relevant current action/attempt. Fresh
progression requires its full expected intent/BindingContext and exact
intent/action/attempt/opaque target-operation match, even for a step failure or
completion event. Missing or incompatible facts return `binding_refused`;
incompatible receipt identity returns binding code `intent_mismatch`. A matching
`observed_effect` receipt contradicting `not_dispatched` returns
`illegal_transition`. Both refuse without sequence advancement or retry, leaving
the supplied state and receipt evidence intact. The guard cannot be bypassed by
a no-dispatch timeout declaration, ordinary failure code, another dispatch or a
zero-effect completion summary. A retained `accepted` receipt is acknowledgement,
so it does not trigger the positive-effect contradiction after qualified absence.
Historical duplicates and sequence fences retain their earlier precedence;
terminal-state refusal and cancellation/failure stops remain unchanged.

A future store must retain historical receipts separately and supply the receipt
relevant to the current attempt in this bounded singleton, together with the
logical action state that preserves every known effect. Omitting a historical
receipt from a new attempt's snapshot must never erase an observed logical effect.

Only `applied` advances the accepted sequence to the event sequence. Every other
disposition retains the prior sequence. Every refusal and duplicate preserves
the supplied state; reconciliation can change a nonterminal phase and represent
uncertainty while preserving known effects. Terminal states refuse all fresh
in-order events, including late evidence; a future store may retain late evidence
separately but must not resurrect authority. Identical known replay still returns
a duplicate after the scope fences.

All events require the outer context fields. Optional facts become required as
shown below; unrelated optional facts may be null. An absent required fact never
implies success. E/S/A event references retain the wire matrix above. Every S/A
event additionally needs matching `step_id`/`attempt` and `active_step=true`; A
also needs matching `action_id`.

| Event | Additional supplied facts and legal behavior |
| --- | --- |
| `admission` | Independent context package/configuration/plan hashes must match. Only `new -> running`. No fabricated action/BindingContext is needed. |
| `step_started` | Running phase, effective retry policy and attempt below its ceiling. `active_step` identifies the currently expected step, not an authenticated identity or persisted start record. |
| `step_result`, `step_failed` | Running phase and matching active step/attempt. Results never complete an execution or verify an effect. Failure retry suggestions require policy/code/attempt eligibility and no possible action dispatch. |
| `wait_started` | Running phase and complete `wait` matching every payload field; unexpired wait. Moves to waiting. A future `not_before_ms` is allowed at creation. |
| `signal_received`, `timer_elapsed` | Waiting phase and matching `wait_id`; signals also require matching correlation digest and human/external wait; timers require timer kind. Resume only when `not_before_ms <= now_ms < expires_at_ms`. No assignment or escalation authenticates a signal sender. |
| `intent_created` | Running, not_dispatched, unknown outcome; expected full intent and BindingContext. Incoming intent binding digest must match exactly and lifetime must include now. |
| `decision_recorded` | Same state and facts, plus incoming decision checked by `check_decision`. If context decision is nonnull its full digest is immutable; if null this is the first decision declaration. Denied/expired/mismatched decisions refuse. |
| `action_dispatched` | Same state and facts, effective retry policy, and context decision for human approval (null for none mode). Uses `check_command_binding` for exact approval, command recipient/fencing, hashes and lifetimes. Moves to dispatching, never verified. |
| `action_receipt` | Running/reconciliation phase and an already possibly dispatched action; full expected intent/BindingContext and exact `target_operation_ref`. Receipt action/attempt/intent/operation must match. If a context receipt is supplied, its full digest must match too. Accepted remains unverified; observed_effect needs evidence and marks observed only. Unknown requires reconciliation. |
| `dispatch_timeout` | Running/reconciliation phase, expected intent/BindingContext and exact payload intent digest. Any possible dispatch requires reconciliation; dispatching cannot be cleared by a payload merely claiming no dispatch. Proven not_dispatched can suggest retry only in running phase. |
| `reconciliation_observed` | Running/reconciliation phase, expected intent/BindingContext, exact target-operation reference and reconciliation rule ID/version. Any supplied receipt must match intent/action/attempt/operation; accepted state requires that receipt. Effect presence requires evidence and marks observed, not verified. Unknown/unqualified absence retains uncertainty. Qualified absence may clear dispatching/unknown/acknowledged state; it cannot clear observed/verified state or a receipt claiming observed_effect. |
| `outcome_recorded` | Running/reconciliation phase and possibly dispatched action, expected intent/BindingContext, exact receipt/target-operation and verification rule ID/version. Outcome receipt/intent/rule must match, observation cannot exceed now. Verified requires the admitted nonempty deterministic-rule evidence declaration and sets observed/verified; receipt acceptance alone never does. Unknown requires reconciliation. Contradictory outcomes cannot overwrite verified effects. |
| `execution_completed` | Running phase, CompletionSummary, no unfinished steps, all required effects verified, no unknown/dispatching action or known not_verified outcome. A current accepted/observed action also needs its own verified outcome and a nonzero effect count. Zero-effect completion retains unknown outcome; it cannot erase prior verification. |
| `cancellation_requested`, `execution_failed` | Any nonterminal phase; change only phase to cancelled/failed. All known/unknown action and outcome facts remain. Compensation is a separately bound action; its failure cannot erase an original effect. |

Evidence events may report facts after an intent's lifetime has expired; they do
not renew permission. New intent/decision/dispatch checks and retry suggestions
require the current intent lifetime. Any suggestion remains false after
cancellation, without a current epoch, or without remaining attempts.
For ordinary read/computation `step_failed` events, a suggestion requires running
phase, matching active step/attempt, not_dispatched action, unknown outcome, the
actual failure code in the effective retry policy and `attempt + 1 < max_attempts`.
No intent is required for an ordinary activity; if one is supplied its binding
and current lifetime must match. A proven no-dispatch timeout uses the actual
`timeout_before_dispatch` retry code. Qualified absence is a separate structural
recovery basis with no failure code, so it requires remaining attempts and the
current bound intent but does not invent or match a retry code. It never
schedules a retry or bypasses fresh authority. Expiry or exhaustion suppresses a
retry suggestion without discarding an otherwise valid absence observation.

Acknowledgement is distinct from observed effect: qualified absence can resolve
an accepted request, but its original receipt/evidence must remain in the future
store and supplied context. The evaluator does not delete those facts.

The fixed transition codes are `applied`, `duplicate`, `replay_conflict`,
`sequence_gap`, `stale_sequence`, `context_mismatch`, `illegal_transition`,
`wait_mismatch`, `wait_not_ready`, `wait_expired`, `binding_refused`,
`reconciliation_required`, `evidence_missing`, `verification_missing`,
`completion_incomplete`, `terminal_state`, `retry_exhausted`. Code/disposition and
retry/state consistency are revalidated in report admission. Reports contain no
source records, supplied free text or evidence bodies.

The future runtime must atomically load authenticated tenant-bound state and
source sequences, compare immutable full records, retain every receipt and
contradictory evidence item, persist accepted events, enforce step scheduling,
backoff and budgets, authenticate waits and qualified read-back, and issue fresh
permission for any retry under the same logical intent/idempotency identity.
Neither a synthetic snapshot nor `structurally_retryable=true` establishes those
facts, exactly-once execution, signed trust, provenance or actual target effects.
