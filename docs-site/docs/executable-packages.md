# Executable package v2 foundations

The standalone Python SDK has a separate `lotuspond_sdk.executable` namespace
for executable definitions. This increment provides bounded JSON decoding,
reviewed RFC 8785 canonicalization, strict validation/report helpers, closed data schemas, static mapping inference
and fixed value-free errors. Package/control authoring, compilation, runtime-facing
records and explicit v2 CLI commands are subsequent implementation tasks. No workflow is
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

Compiler-only `SourceSymbol(operation, step_id, occurrence_path=())` identifies
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
summary never stands in for one chosen concrete origin. An empty array has
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
selection_path)`, preserving order, multiplicity, nested choices and missing
optional-field slots (`None`). The compiler must traverse those choices, use the
same selector path for correlated fields, distinguish different selection paths
or reordered slots, and never choose the first option or treat an unbound
`items` summary as a concrete handle.

For repeated operation outputs, `FlowArrayType.iteration_id` identifies the
producing iteration. Only source occurrence slots spelled `$item:<iteration_id>`
are bound when that array is selected. The compiler creates such slots only for
operations actually authored inside that iteration. Aliases of outside handles
retain their origin, including an outer iteration's still-symbolic slot when an
inner result is selected. Nested choices remain conservative metadata; the
compiler must carry their complete structure through region exports and joins.
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
