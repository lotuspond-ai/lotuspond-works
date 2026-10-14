# Executable Package V2 and Runtime Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver bounded executable definitions, deterministic compilation and runtime-facing contracts in the standalone SDK, while preserving every v1 interface and byte contract.

**Architecture:** Keep the implementation in a separate lotuspond_sdk.executable namespace. Typed authoring, static compilation, signatures, declaration checks and pure transition checks share explicit contracts; none executes a workflow or grants authority. The compiler produces source-bound metadata for the later engine adapter, which must reload and validate the pinned definitions.

**Tech Stack:** Python >=3.13; existing Pydantic >=2.12,<3, cryptography >=50,<51, Hatchling and uv; reviewed and exactly pinned rfc8785==0.1.4; Python 3.13/3.14 CI, independent Node JSON/JCS and OpenSSL test oracles. No engine, HTTP client or database dependency in the SDK.

**Spec:** [Owner-approved executable-v2 design](../specs/2026-10-14-executable-package-v2-design.md), approved 14 October 2026. The owner has already selected subagents with independent review after each task. The owner approved this written implementation plan on 14 October 2026; execute using the selected subagent method with independent task reviews.

## Global Constraints

- Preserve original source, archives, test data and all existing branches/worktrees. SDK PR #4 and design PR #5 remain separately reviewable; implementation begins on an isolated feature branch after this plan is reviewed.
- V1 remains declaration-only with its existing schema, validator/canonicalizer identifiers, root SDK APIs, legacy CLI, 1,166 canonical bytes and digest 305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3. The registry stays v1-only; no database migration, record rewrite or automatic conversion.
- New schema identifier: lotuspond.workflow-package.v2. New canonicalizer: lotuspond.workflow-package.jcs.v2. New namespace: lotuspond_sdk.executable. Ordinary v1 command forms remain unchanged; v2 uses an explicit CLI group.
- Package raw input and normalized canonical output: 1,048,576 bytes; JSON depth 32 and items 10,000; authored steps 128 across the entire package; each declaration family 64; control nesting 8; package dependency closure 64 including root and depth 8 including root; expression nodes 64 and depth 8.
- Per-execution conservative expansion: at most 10,000 step attempts, including control overhead, retries and nested calls; each iteration at most 1,000 items. Parallel branches all count. No implicit network, environment lookup, secret resolution, provider calls, engine client or execution.
- V2 uses normalized RFC 8785 JCS, explicit defaults and significant array order. Preserve Unicode code points without normalization; reject invalid Unicode, duplicate keys, nonfinite numbers, unknown fields, cycles and coercions. Integer fields reject bool and float, including 1.0; integer values stay within ±9,007,199,254,740,991.
- Decimal values are strings with explicit scale, no exponent/leading plus/redundant leading zeros/negative zero, and exactly the declared fractional digits. Number is a separate declared type; JCS spelling must not make a valid numeric literal invalid when read back.
- Reports are ASCII-safe and bounded to 8,388,608 bytes including CLI wrapper/newline; errors/reasons are at most 20, sanitized and explicitly truncated. Truncation cannot turn failure into success. All typed inputs are revalidated after mutation and construction without invoking arbitrary serializers.
- Guard the actual Python type of every fixed integer/boolean field before Pydantic literal validation: inherited tests demonstrated that strict Literal[False] alone can accept 0. Test constructed/mutated report flags as well as normal parsed inputs.
- Only action-intent, human-review, dispatch and verification operations can originate their corresponding opaque handles. Package input, literals, models and connector payloads cannot manufacture them. Wire handles remain untrusted until the later authority reloads authenticated tenant-bound records.
- A signature, compiled plan, transition result or synthetic capability match grants no authority. V2 package context is lotuspond-workflow-package-signature-v2; command context is lotuspond-runtime-command-v1. Neither accepts v1/evidence/other-context signatures.
- Reuse reviewed explicit allowed/revoked TrustSet and safe file/key functions. Existing outputs are refused; partial output is retained on failure. Keep the known small-order/noncanonical key guard and its documented limits; do not introduce curve arithmetic.
- Every effect-producing normal completion path requires verification for each action, including subworkflows. Acknowledgement, missing evidence or model assertion cannot become verified business success. Unknown dispatch results require reconciliation, never blind replacement.
- Read AGENTS.md and SECURITY.md. Use only independently identity-verified localhost test databases for inherited regression checks. No production/customer/cloud access, public package-index release, merge or deployment is authorized by this plan.
- Run meaningful red/green tests per task, a fresh task review, then separate whole-branch review and complete application/database/artifact/hosted gates. Preserve the distinction between local synthetic contract checks and real production qualification.

## Review Focus

1. JCS writes number 1.0 as 1 and negative zero as 0: canonical output must remain valid and hash identically on reread without accepting float values in integer fields. Task 1/Task 3 numeric round-trip tests pin this.
2. A value available in only one decision/parallel branch, or a missing optional property, must not become available after a join by accident. Task 2/Task 4 availability and handle-origin tests pin this.
3. Shared dependencies and nested iteration/retries must not hide conflicts, cycles or work exceeding 10,000 attempts. Task 4 diamond-closure and expansion-bound tests pin this.
4. An approval or signed command must fail structural binding when target/input/policy/configuration/package/epoch/recipient changes; a generic form result is not an approval. Task 5/Task 8 one-field mutation and cross-context tests pin this.
5. Duplicate IDs with different content, sequence gaps, cancelled state and unknown writes must not produce completion or a blind retry. Task 6 literal transition-table tests pin this.

---

## File ownership and common contracts

All new runtime paths below are under sdk/python/src/lotuspond_sdk/executable/. All new tests are under sdk/python/tests/executable/. Each task updates its public exports and its portion of docs-site/docs/executable-packages.md and sdk/python/README.md. Do not reorganize v1 modules merely to share an internal helper.

| Files | Responsibility / task |
| --- | --- |
| __init__.py, metadata.py, errors.py, input.py, _validation.py, _canonical.py; test_canonical.py, test_input.py | Version constants, fixed errors, bounded decoder/known-model projection and reviewed JCS; Task 1. |
| types.py, expressions.py; test_types.py, test_expressions.py | Closed data schemas, opaque flow types, lexical reference/type checking; Task 2. |
| package.py, catalog.py, validation.py; test_package.py, test_catalog.py, conftest.py | Authoring/control/dependency models, static structural validation and synthetic cases; Task 3. |
| dependencies.py, compiler.py, plan.py; test_dependencies.py, test_compiler.py | Exact local closure, typed control analysis, deterministic plans and conservative work/outcome coverage; Task 4. |
| actions.py, wire.py; test_actions.py, test_wire.py | Intent/decision/command/event/receipt models and exact bindings; Task 5. |
| transitions.py; test_transitions.py | Pure context-supplied replay/state/reconciliation checks; Task 6. |
| diff.py; test_diff.py | Private deterministic v2 differences including movement and content; Task 7. |
| framing.py, signing.py; test_signing.py | Distinct v2 package and runtime-command signatures; Task 8. |
| compatibility.py; test_compatibility.py | Complete synthetic v2 profiles and explicit requirements; Task 9. |
| cli.py, schema.py; test_cli.py, test_schema.py | Explicit v2 command group, schema export and installed journeys; Task 10. |
| sdk/python/pyproject.toml, sdk/python/uv.lock, backend/uv.lock | Pin reviewed JCS dependency and SDK 0.2.0 in Task 1; verify final standalone/runtime installs in Task 10. |
| contracts/examples/executable-v2/, contracts/workflow-package.v2.schema.json | Synthetic ordinary/decision/parallel/iteration/reviewed-action/subworkflow cases in Tasks 3–6; committed exported schema in Task 10. |
| sdk/python/tests/fixtures/executable-v2/, scripts/checks/jcs-oracle.mjs | Attributed independent RFC/JCS/signature fixtures and independent ECMAScript serialization oracle; Tasks 1/8. Test-only oracle is not an SDK runtime dependency. |
| sdk/python/src/lotuspond_sdk/cli.py, sdk/python/tests/conftest.py, sdk/python/tests/test_distribution.py; backend/tests/test_package_sdk_compatibility.py, backend/tests/test_package_cli.py | Minimal v2 dispatch hook and both installed artifacts/legacy parity; Task 10. |
| deploy/checks/airgap-smoke.py, deploy/checks/test_airgap_smoke.py, deploy/checks/test_content_proof.py | Actual image v2 import/compiler checks and nested source-content proof regression; Task 10. Existing recursive SDK proof must include every new module. |
| .github/workflows/ci.yml, package.json, docs-site/mkdocs.yml, README.md, docs/CAPABILITIES.md, backend/lotuspond/app.py, AGENTS.md, CLAUDE.md, docs/architecture/0009-agent-automation-platform.md | Verification wiring, truthful guides/capabilities/changelog/ownership and dated architectural addendum; Task 10. |
| docs/plans/2026-10-14-executable-package-v2-progress.md | Scoped red/green, review, artifact and exact-head CI evidence; all tasks/controller. |

### Representation decisions

- Metadata adds VALIDATOR_REVISION=lotuspond.workflow-package.validator.v2.1, COMPILER_REVISION=lotuspond.workflow-compiler.v2.1 and PLAN_SCHEMA=lotuspond.compiled-workflow.v2. Package signature format_version is exact integer 2; runtime wire format_version is exact integer 1.
- Required model fields have no inferred defaults. Kind-dependent optional fields normalize to null, optional knowledge/tool lists to empty lists, and report truth/assurance flags to their fixed values. Canonical bytes include these explicit defaults; versions never depend on host environment.
- Reuse v1 Identifier, Version and ReferenceRevision bounds where applicable. Stable step/declaration IDs use Identifier; package/connector versions use Version; other exact revisions use ReferenceRevision. Digest is 64 lowercase hex; no wildcard/range/default resolution. Labels max 160 characters, instructions max 16,384, references/JSON pointers max 512. These strings remain untrusted and private in ordinary output.
- DataSchema is discriminated by kind: null, boolean, integer, number, string, decimal, array, object, ref. Integer min/max are safe integers; number min/max are finite JSON numbers. String max_length is required 1..65,536 code points. Decimal precision 1..38 and scale 0..18 with scale<=precision; array max_items 0..1,000 and items schema; object fields is an ordered unique list of FieldSpec(name 1..128 characters, schema, required exact bool), at most 64. Ref names an acyclic local NamedSchema; no external ref, regex or custom validator. Schema definitions max 64 and resolution depth 8.
- Number literals use the declared IEEE 754 domain: JSON int/float spellings normalize to finite float, including IEEE rounding, so integral JCS spellings above the safe-integer range remain readable as number. Integer-schema values and every integer metadata field still require exact safe int; type assignment never silently promotes an integer reference into number. Exact quantities use decimal strings. The raw v2 decoder bounds integer tokens to 309 digits and other numeric tokens to 1024 characters; prevalidation allows at most 1024-bit raw ints until the declared field selects its domain. Normalized JCS input contains only safe ints or finite floats. Thus a bare unsafe int sent directly to jcs_bytes refuses, while a validated number literal can use the full finite IEEE domain. Public data schemas cannot contain handle types.
- FlowType is compiler metadata: DataType(schema, may_be_missing), HandleType(handle_kind, immutable source symbol), FlowObjectType(fields) and FlowArrayType(items,max_items). ExportSchema explicitly declares data, handle, object-of-exports or array-of-exports for region results; handles are permitted only on internal typed flows. Public package input/output, model/tool/connector payloads and literals use DataSchema. Only direct reference flow can satisfy a handle argument; object/array/region exports preserve its origin. A reviewer/dispatch pair must consume the same immutable intent symbol, and runtime binding checks still compare exact digests. Indexed/iterated handle symbols include the stable occurrence path; a possibly absent array element needs the same presence guard as an optional property.
- Mapping is a closed discriminated expression: literal(schema:DataSchema,value), ref(source,pointer), object(fields), array(items,item_schema:ExportSchema), or call(fn,args,scale?,rounding?). Source is package_input, step(id), iteration_item or iteration_index. Resolve pointers with RFC 6901 escaping; no wildcard/index coercion/negative indices. Missing optional values remain unavailable unless coalesced or refined by the direct exists(ref) decision guard. exists distinguishes absence from a present null. A declared handle export constrains a reference; it never casts literal/data content into a handle.
- Fixed call vocabulary: concat; add/subtract/multiply/divide for matching integer or decimal domains; eq/ne/lt/le/gt/ge for matching comparable types; and/or/not; exists; coalesce. No implicit casts. Decimal arithmetic requires result scale and rounding in reject, half_even, floor, ceiling; integer division requires exact divisibility. Divide-by-zero, overflow and nonrepresentable results are specified runtime errors. This subsystem infers types and validates semantics; it does not add an expression interpreter or constant-fold package programs.
- All effectful/retryable steps use RetryPolicy(max_attempts 1..10, timeout_seconds 1..86,400, backoff_seconds 0..3,600, retry_codes unique bounded fixed codes). Codes: unavailable, rate_limited, timeout_before_dispatch. A possibly dispatched write never qualifies from this list. Every package declares max_steps 1..10,000, active_seconds 1..86,400, elapsed_seconds 1..31,536,000, retained_waits 0..10,000, max_input_tokens/max_output_tokens 0..10,000,000 and max_cost_microusd 0..1,000,000,000,000. Limits are finite declarations, not spend/availability promises.
- AgentLimits has max_steps 1..1,000, timeout_seconds 1..3,600 and the same bounded token/cost fields. Its max_steps counts all model/tool attempts, including tool retries; outer agent retries multiply that total. Read-tool declarations include connector_ref, operation and RetryPolicy. Waiting does not consume active compute; active/elapsed/retained-wait and spend enforcement remain distinct runtime duties.
- ExecutableError has a closed fixed code/message map, sanitized issues and truncated flag. Errors use fixed field names/numeric offsets in pointers, replacing unknown names by a literal * token; no supplied value, path, instruction or exception string. Codes are invalid_json_value, invalid_unicode, nonfinite_number, safe_integer, input_too_large, input_too_deep, input_too_complex, cyclic_input, invalid_package, invalid_catalog, invalid_schema, invalid_mapping, duplicate_identifier, unknown_reference, unavailable_value, type_mismatch, dependency_missing, dependency_conflict, dependency_cycle, dependency_digest_mismatch, unsupported_operation, expansion_exceeded, intent_mismatch, verification_missing, invalid_wire, invalid_context, invalid_profile, invalid_requirements, invalid_envelope and report_too_large. Each has a fixed English message; tests assert the code property, not the prose. Low-level decode/file failures retain existing PackageReadError/SdkError codes. Successful reports have exact authority_granted=false and synthetic assurance where applicable.

### Verification command conventions

Repeat each task's exact red pytest command for green, then its named regression files and uv run --frozen --project sdk/python ruff check sdk/python. When a task requires the full matrix, use the current 3.14 SDK environment and a fresh separate 3.13 environment; do not replace a shared environment while another check uses it:

```bash
uv run --frozen --project sdk/python --python 3.14 pytest sdk/python/tests -q
v2_python_environment=$(mktemp -d /tmp/lotuspond-v2-python313.XXXXXX)
UV_PROJECT_ENVIRONMENT="$v2_python_environment" uv sync --frozen --project sdk/python --python 3.13
UV_PROJECT_ENVIRONMENT="$v2_python_environment" uv run --frozen --project sdk/python --python 3.13 pytest sdk/python/tests -q
```

Passing means exit 0 and every required assertion/oracle/artifact journey passing. Record actual counts and any skips rather than predicting them. Use the saved environment activation script only on this cloud host; installed-product tooling has no dependency on it.

## Task 1: Establish bounded JCS and independent numeric oracles

**Files:** Task 1 rows above; add docs-site/docs/executable-packages.md and progress record. Modify SDK metadata/locks only to version 0.2.0 and add rfc8785==0.1.4; refresh backend path-dependency lock without changing other requirements.

**Interfaces:** Produces decode_executable_bytes(content: bytes) -> object and internal JsonLimits, project_json(raw: object, *, model_types: tuple[type[BaseModel], ...]=(), limits: JsonLimits=PACKAGE_LIMITS) -> JsonValue, jcs_bytes(raw: object, *, max_bytes: int=1048576) -> bytes and sha256_hex(content: bytes) -> str, plus ExecutableError/ExecutableIssue and metadata constants. JsonValue is the recursive union of exact JSON scalar/list/string-keyed-dict types. Each public module supplies a fixed tuple of accepted model classes, without dynamic registration. Projection checks unknown/mutated fields before recursive serialization, refuses arbitrary objects/serializers and enforces the raw integer bound; JCS then refuses every remaining unsafe int. PACKAGE_LIMITS=(1048576,32,10000), WIRE_LIMITS=(65536,32,10000), REPORT_LIMITS=(8388608,32,262144) specify bytes/depth/items; package guards are never raised to report limits. JCS accepts already projected normalized JSON with a supported exact-integer byte cap 1..8388608, choosing package or report traversal limits accordingly. It is not a v1 replacement.

- [x] **Step 1: Freeze independent tests and dependency provenance.** Review package source/license and verify inspected sdist SHA-256 e545841329fe0eee4f6a3b44e7034343100c12b4ec566dc06ca9735681deb4da before adding the exact dependency. Add attributed RFC 8785 vectors and an independent Node oracle that recursively assembles arrays and UTF-16-sorted object members, using native JSON.stringify for scalar values and property names. Do not stringify a reconstructed object: JavaScript reorders integer-like property names numerically. The oracle never calls Python/JCS code. Pin Unicode non-normalization, surrogate refusal, numeric-string keys such as "10" before "2", negative zero, exponent boundaries, safe integers, cyclic/mutated/programmatic input and every byte/depth/item boundary. Use literal expected bytes, not solely round trips.

```python
def test_jcs_numeric_and_utf16_order():
    assert jcs_bytes({"z": -0.0, "a": 1.0}) == b'{"a":1,"z":0}'
    assert jcs_bytes({"\U00010000": 1, "\ue000": 2}).decode() == '{"\U00010000":1,"\ue000":2}'
    with pytest.raises(ExecutableError) as error:
        jcs_bytes(9007199254740992)
    assert error.value.code == "safe_integer"
```

- [x] **Step 2: Run the focused new tests before implementation.** Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_canonical.py sdk/python/tests/executable/test_input.py -q; expect missing executable interfaces, while existing v1 canonical tests remain green.
- [x] **Step 3: Implement the bounded decoder, adapter and fixed errors.** Reuse existing duplicate-key/nonfinite/preparse-depth helpers without changing the v1 decoder; the v2 numeric parser uses the bounds above so JCS integer spellings such as 1e20 remain parseable before type selection. Call the reviewed library for actual JCS; do not implement float formatting or Unicode sorting in Python. Count raw and normalized limits; reject bool-as-int in fixed metadata; sanitize exceptions. Report serialization uses existing ASCII/final-size invariants with known-model revalidation. Expose only the executable namespace, leaving root v1 exports untouched.
- [x] **Step 4: Verify and record.** Focused canonical tests plus existing test_contract.py/test_input.py/test_signing.py, frozen Ruff, independent Node oracle and SDK wheel/sdist installation pass on 3.13/3.14. Confirm historical 1166 bytes/digest/schema unchanged; record dependency license/hash and exact lock delta.
- [x] **Step 5: Commit scoped files.** Commit with message feat: add bounded v2 JCS canonicalization; task review checks dependency provenance and numeric/domain boundaries.

## Task 2: Define typed values, mappings and opaque flow provenance

**Files:** types.py, expressions.py, test_types.py, test_expressions.py and their guide/export sections.

**Interfaces:** Produces DataSchema, NamedSchema, ExportSchema, FlowType, TypeEnvironment(input_schema, step_outputs, iteration_item, schema_definitions, present_refs); validate_value(raw: object, schema: DataSchema, definitions: list[NamedSchema]) -> JsonValue; infer_mapping(raw: object, environment: TypeEnvironment) -> FlowType; assert_assignable(actual: FlowType, expected: ExportSchema) -> None. Each consumes Task 1 validation/JCS/errors. Helpers are pure and never evaluate a package program.

- [x] **Step 1: Write type/availability tests.** Define local schema/environment fixtures in these tests. Assert exact decimal spelling/scale/precision, optional-versus-null, closed object fields, RFC 6901 / and ~ escaping, local schema cycles, known operation signatures, missing/future/private branch references, schema/mapping bounds and prohibition on literal/input/model handle construction. Include arrays/objects of legitimate handle references, guarded indexing and rejection of a lookalike data object inside those containers.

```python
def test_optional_reference_requires_default(optional_env, optional_ref, defaulted_ref, required_string_export):
    assert infer_mapping(optional_ref, optional_env).may_be_missing is True
    with pytest.raises(ExecutableError) as error:
        assert_assignable(infer_mapping(optional_ref, optional_env), required_string_export)
    assert error.value.code == "unavailable_value"
    assert infer_mapping(defaulted_ref, optional_env).may_be_missing is False

def test_integer_is_not_bool_or_float(integer_schema):
    for value in (True, 1.0, "1"):
        with pytest.raises(ExecutableError) as error:
            validate_value(value, integer_schema, [])
        assert error.value.code == "type_mismatch"
```

- [x] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_types.py sdk/python/tests/executable/test_expressions.py -q; expect missing models/functions.** Record actual red failures, then implement only this layer.
- [x] **Step 3: Implement closed schema and mapping semantics.** Resolve local schemas with cycle/depth guards; preserve exact types and optionality. Infer operation signatures and result bounds; document arithmetic runtime failures. Handle references carry immutable source symbols; literals and ordinary objects cannot cast into handles. Export constructors may preserve existing handle references but cannot create an origin. Unknown fn, extra fields and constructed/mutated models fail closed.
- [x] **Step 4: Verify focused tests, Task 1 tests and Ruff.** Include literal number 1.0 -> JCS 1 -> validate under number schema, negative zero, 1e20, a neighboring representable large float and the largest finite IEEE value through decode/typed normalization/JCS; bytes and digest must be stable on reread. Integer 1.0 and unsafe integer-schema values still refuse. Test exact 64/65 expression nodes and 8/9 depth without violating another guard first.
- [x] **Step 5: Commit feat: define v2 typed mappings and opaque flow types.** Update user-facing schema/expression syntax and error behavior; independent task review must trace optionality and handle origins.

## Task 3: Define structured packages and exact dependency catalogs

**Files:** package.py, catalog.py, validation.py, test_package.py, test_catalog.py, executable test conftest, contracts/examples/executable-v2/ and guide/exports.

**Interfaces:** Produces ExecutablePackage, Body, Step, ArtifactRef, CatalogDocument, CatalogBundle; validate_executable_package(raw: object) -> ExecutablePackage; canonical_executable_json(raw: object) -> bytes; executable_digest(raw: object) -> str; validate_catalog(raw: object) -> CatalogBundle. Validation checks structure/local references; compilation in Task 4 checks supplied artifact closure and complete typed flow.

ArtifactRef has kind, name, version, sha256; kind is workflow, connector, model, agent, evaluation, policy, knowledge or artifact. CatalogDocument has exact format_version 1, kind/name/version, local schemas and one kind-specific definition; its digest is over its normalized complete document, excluding the separate ArtifactRef wrapper. CatalogBundle holds at most 64 documents per non-workflow family and fits 1 MiB/depth 32/items 10,000. Workflow dependency sources are complete v2 packages supplied separately. Identity/version with different content is always a conflict.

ExecutablePackage fields: schema_version, package_id, version, name, input_schema, output_schema, schemas, body, models, connectors, agents, subworkflows, evaluations, policies, knowledge, artifacts, secret_refs, environment, limits, assurance. Each declaration family is required (empty valid), unique and bounded 64; content includes normalized defaults. Environment fields are runtime_contract (literal lotuspond.runtime-contract.v1), platforms (unique OS/architecture pairs), required_capabilities, destination_classes and operation_classes; lists max 64. secret_refs is the single list of credential reference names and connector credential_ref must name it. Assurance fields are evaluation_refs, outcome_rule_refs and required_classification, all declarations rather than observed qualification. Knowledge declares pinned source, freshness_seconds, classification and citation schema. Policies/evaluation references are pinned; they do not prove policy or evaluation success.

Declarations carry id and ref:ArtifactRef with the matching family kind. Connector declarations additionally carry credential_ref or null; knowledge declarations add required_freshness_seconds 1..31536000, allowed_classifications and citation_schema. Catalog-internal model/tool references use ArtifactRef rather than caller aliases; resolve them within the explicitly supplied catalog. Agent steps name knowledge_refs from the package. Reference slots reject credential objects/URL substitutions; do not claim heuristic discovery of secrets inside arbitrary author-supplied text. Guides require secrets and customer documents to stay outside packages.

Body has ordered steps, result mapping and result_schema:ExportSchema. Public package output must be data. Every step has unique id and a closed kind-specific payload:

| kind | Required payload and typed result |
| --- | --- |
| transform | mapping and data output_schema. |
| decision | boolean condition, then_body, else_body; matching declared exports. |
| parallel | unique named branches and join literal all; result object keyed by branch identity. |
| iteration | input array mapping, max_items 1..1000, concurrency 1..64 no greater than max_items, body; ordered results. |
| connector_read | exact connector_ref/operation, target and input mappings, retry; ordinary output from catalog. |
| agent | pinned model_ref or reusable agent_ref (exactly one), required input mapping, instructions, structured output_schema, read-only tool references max 32, finite per-agent limits/retry; ordinary untrusted output. |
| action_intent | exact connector_ref/write operation, target mapping, input mapping, policy_ref, approval mode none/human, independent_review bool and expiry_seconds 1..2592000; intent handle. |
| human_task | assignment_policy_ref, timeout_seconds 1..31536000, optional escalation_policy_ref, mode form/review; form has data schema, review has exact intent reference and returns decision handle. Policy references name pinned package policies. |
| action_dispatch | intent reference, decision reference when human-required, retry, optional compensation declaration; receipt handle. Compensation is a separately bound write operation/input/policy/approval/verification declaration, not an automatic inverse. |
| timer | delay_seconds 1..31536000; ordinary completion marker. |
| external_wait | signal_schema, correlation mapping, expiry_seconds 1..31536000, assignment_policy_ref and optional escalation_policy_ref; typed signal output. Policy references name pinned package policies. |
| subworkflow | exact declared workflow_ref and input mapping; declared output from pinned package. |
| outcome_verification | exact receipt reference, connector read-back operation, target/input mappings, fixed deterministic rules and rule/version identity; verification handle. |

Input and output are explicit package boundaries rather than redundant start/end steps. Connector catalog operations declare target/input/output schemas (null target schema for a global operation), effect read/write, idempotency mode none/target_key and reconciliation mode none/read_back with exact read operation and deterministic absence/effect rules. Write compilation requires read_back. VerificationSpec names connector_ref, read_operation, target/input mappings, rule_id/version and typed rules; its target must preserve the originating intent's target symbol. CompensationSpec contains connector_ref, write operation, target/input mappings, policy_ref, approval mode/independence, expiry_seconds, retry and its own VerificationSpec.

Catalog definitions are closed: connector has artifact_sha256 and operations; model has profile, configuration_revision/configuration_sha256, input/output schemas, read-tool contracts and limits; agent has pinned model_ref, instructions, input/output schemas, read tools and limits; evaluation has input schema, rule IDs/versions and fixed acceptance rules; policy has approval mode/independence, assignment/escalation identifiers and allowed operation/capability classes; knowledge has source revision, content_sha256, freshness_seconds, classification and citation schema; artifact has media_type, content_sha256, size_bytes and classification. Rule has op eq/ne/lt/le/gt/ge/present/and/or: leaf comparisons require a readback_pointer and typed expected Mapping; present requires a readback_pointer; and/or require child rules. Apply the 64-node/8-depth bound and reject literal-only/model-assertion acceptance. External/custom code is unsupported. Binary/content checksums in these declarations remain unverified availability claims; ArtifactRef.sha256 binds the actual supplied catalog definition, not an absent external binary. Compilation verifies complete definition/catalog closure and records artifact_availability=not_checked. Real artifact custody/digests/provider support are later admission duties.

- [x] **Step 1: Write structural and golden tests.** Add named synthetic fixtures minimal, decision, parallel, iteration, reviewed_action, subworkflow and catalog. conftest exposes case(name: str) -> dict (fresh deep copy), catalog_case() -> dict and v1_example as an alias of the existing historical fixture; Task 4 adds literal expected plan fixtures. Assert each family, unique IDs across nested bodies, unknown fields, required declarations, pinned refs, unsafe URL/credential fields, unsupported joins/back edges and all stated counts.

```python
def test_v2_is_explicit_and_v1_stays_fixed(case, v1_example):
    package = validate_executable_package(case("minimal"))
    assert package.schema_version == "lotuspond.workflow-package.v2"
    with pytest.raises(ExecutableError):
        validate_executable_package(v1_example)
    with pytest.raises(ValidationError):
        validate_package(case("minimal"))
    assert package_digest(v1_example) == "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"
```

- [x] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_package.py sdk/python/tests/executable/test_catalog.py -q; expect absent models/functions.** Keep the historical v1 oracle independent of new fixtures.
- [x] **Step 3: Implement strict discriminated models and local validation.** Check step/control/schema limits before recursive model validation; nested models and extra fields cannot bypass projection. Validate per-kind required fields and local declaration references without pretending catalog/provider support was checked. Canonicalization always validates first, emits explicit defaults and rejects normalized output>1 MiB.
- [x] **Step 4: Verify package/catalog plus Tasks 1–2 and v1 parity tests.** Include canonicalize/redecode/revalidate/hash equality for every synthetic package and numeric literal; JSON member order has no effect, array order does. No provider/DB/engine import is added.
- [x] **Step 5: Commit feat: define structured executable v2 packages.** Task review checks every approved step family, catalog pin and authority boundary; fixture documentation labels all examples synthetic.

## Task 4: Resolve local closure and compile deterministic bounded plans

**Files:** dependencies.py, compiler.py, plan.py, their tests and literal expected fixtures; extend Task 3 synthetic cases/guide/exports.

**Interfaces:** resolve_dependencies(raw: object, dependencies: list[object], catalog: object) -> DependencyClosure; compile_package(raw: object, *, dependencies: list[object], catalog: object) -> CompiledPlan; canonical_plan_json(plan: object) -> bytes; compiled_plan_digest(plan: object) -> str. Inputs are explicit, including empty dependency/catalog sets. Closure stores validated root/packages/catalog entries keyed by identity and digest, with no I/O.

CompiledPlan fields: schema_version, compiler_revision, package_sha256, dependency_digests, catalog_digests, steps, regions, edges, requirements, limits, maximum_step_attempts, outcome_obligations, artifact_availability=not_checked, authority_granted=false. PlanStep identifies package digest, stable authored step/region identity, source pointer, kind, input-mapping digest, output-type/schema digest and operation/dependency pins. Plans reference authored instructions/literals by source path/digest; they do not copy those values or resolved customer/credential payloads. Sort sets/maps by specified stable identity; preserve authored sequence/branch/iteration order. Plan JCS artifact is bounded 8 MiB, separately from 1 MiB package canonical bytes.

- [x] **Step 1: Write closure, literal-plan and coverage tests.** Golden expected plans are hand-authored from the small cases and independently checked, never generated by the function under test. Include shared diamond dependencies, identity/content conflict, missing/unused declared dependencies, cycles, digest mismatch, depth 8/9, closure 64/65, deterministic source order, forbidden branch/private values, handle alias flow, guarded optional values, joins and nested subworkflows.

```python
def test_expansion_includes_nested_iterations_and_retries(case, catalog_case):
    plan = compile_package(case("attempt_budget_10000"), dependencies=[], catalog=catalog_case())
    assert plan.maximum_step_attempts == 10000
    with pytest.raises(ExecutableError) as error:
        compile_package(case("attempt_budget_10001"), dependencies=[], catalog=catalog_case())
    assert error.value.code == "expansion_exceeded"

def test_branch_private_output_is_unavailable(case, catalog_case):
    with pytest.raises(ExecutableError) as error:
        compile_package(case("unjoined_branch_reference"), dependencies=[], catalog=catalog_case())
    assert error.value.code == "unavailable_value"
```

- [x] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_dependencies.py sdk/python/tests/executable/test_compiler.py -q; expect missing compiler/closure interfaces.** Add named red fixtures in this task; do not weaken limits to make examples compile.
- [x] **Step 3: Implement closure and typed control analysis.** Validate every supplied document, reject unexpected/conflicting inputs, deduplicate identical supplied/referenced content, require all declared and catalog-internal pins and verify actual definition JCS digests. Bound raw dependency sources 63 before parsing and aggregate package-source bytes 64 MiB including root; root depth 1 and closure count include root. Catalog family bounds apply before deduplication; identical duplicates share one resolved definition, while identity/version with different content refuses. Traverse bodies lexically; only branch exports join, iteration item/index are scoped, no step jumps regions. Enforce exact read/write/tool/model schemas and immutable handle-symbol binding. Child capability/destination/operation classes and credential names must be subsets of the parent's declared environment envelope, and child limits cannot exceed inherited effective limits. Real authority is never inferred from these declarations.
- [x] **Step 4: Implement deterministic plan emission and conservative obligations.** Count an ordinary step as one, ordinary activity retries by max_attempts, an agent by AgentLimits.max_steps*max_attempts, sequential/parallel regions as one plus sum, decision as one plus maximum branch, iteration as one plus max_items*body, subworkflow as one plus callee count; include compensation's bounded attempts in the conservative maximum. Reject count>declared max_steps or 10,000. Track dispatched actions through structured paths: each path must verify its receipts before normal completion; branch-private obligations cannot disappear at joins, and called packages must satisfy the same rule. An explicit cancellation/exception is not normal success. Verification rules are closed typed comparisons/presence checks over declared read-back schemas, not model assertions.
- [x] **Step 5: Verify compiler/closure/earlier suites, literal plan hashes and Ruff.** Recompile reordered input catalogs to identical bytes; mutate each pin/mapping/control/budget to the expected refusal or changed digest. Ensure source dictionaries remain unchanged and oversized plan serialization fails before output. Record dynamic spend, actual provider and runtime enforcement as unchecked.
- [x] **Step 6: Commit feat: compile pinned typed workflows without execution.** Independent review traces expansion arithmetic, lexical availability, handle origins, compensation and per-action outcome coverage across every normal path.

## Task 5: Bind intents, decisions and runtime wire records

**Files:** actions.py, wire.py, their tests, synthetic wire fixtures and guide/exports. No routes, tables, credentials or transport client.

**Interfaces:** ActionBinding, ActionIntent, HumanDecision, RuntimeCommand, RuntimeEvent, ActionReceipt, OutcomeRecord, BindingContext, BindingReport; build_action_intent(binding: object) -> ActionIntent; intent_digest(raw: object) -> str; decision_digest(raw: object) -> str; check_decision(intent: object, decision: object, context: object) -> BindingReport; check_command_binding(command: object, intent: object, decision: object | None, context: object) -> BindingReport. All inputs use strict wire models and bounded JCS; malformed values raise invalid_wire/invalid_context, well-formed binding refusals return fixed reasons with authority=false.

Every wire record has fixed record_type and format_version 1. IDs are bounded opaque identifiers, hashes 64 lowercase hex, sequences/attempts/epochs exact nonnegative safe integers, timestamps UTC epoch milliseconds. Each record fits 65,536 bytes/depth 32/items 10,000. Explicit current time comes only from BindingContext.now_ms; never read wall clock or environment. Maximum intent lifetime 30 days, decision expiry no later than intent expiry, command lifetime 300,000 ms; validity interval is issued_at_ms<=now_ms<expires_at_ms.

| Record | Required binding fields |
| --- | --- |
| ActionBinding | installation_id, recovery_epoch, workspace_id, project_id, environment_id, execution_id, step_id, action_id; package_sha256, configuration_sha256, plan_sha256; connector name/version/artifact_sha256 and operation; target_sha256, input_sha256, policy_sha256; approval mode/independence, requester_id, issued_at_ms, expires_at_ms. No raw target/input/secret payload. |
| ActionIntent | binding and sha256 computed over exactly the normalized ActionBinding. Its logical action/idempotency identity survives safe retries; attempts do not alter it. |
| HumanDecision | decision_id, intent_sha256, decision allow/deny, actor_id, actor_kind human/service/worker/model, policy_sha256, issued_at_ms, expires_at_ms. Human-required approval accepts only human; independent approval excludes requester. |
| RuntimeCommand | command_id, replay_id, exact intent_sha256 and optional decision_sha256, complete workspace/project/environment/execution/action identity, worker_id, fencing_generation, recovery_epoch, installation_id, issued_at_ms, expires_at_ms. Full record is signed in Task 8. |
| RuntimeEvent | event_id, installation_id, recovery_epoch, workspace/project/environment/execution/step/action references as required by kind, attempt, source_id, source_generation, source_sequence, event_kind, observed_at_ms, payload and payload_sha256. Payload is a strict kind-specific union, not arbitrary JSON. |
| ActionReceipt | receipt_id, action_id, intent_sha256, attempt, target_operation_ref and status accepted/observed_effect/unknown; artifact-reference declarations requiring future authenticated resolution. Transport acceptance does not imply an effect. |
| OutcomeRecord | outcome_id, action_id, intent_sha256, receipt_id, state verified/not_verified/unknown, rule_id/version, evidence_refs and observation time. Verified requires nonempty evidence and deterministic-rule identity; model claims are not an evidence kind. |

BindingContext supplies the expected installation/tenant/execution, package/configuration/plan/policy hashes, worker/generation/epoch, requester and current time. Every expected field is required; comparison is exact. BindingReport has valid, code, signature_status=not_checked, assurance=synthetic-contract-evaluation and authority_granted=false. Codes are binding_valid, context_mismatch, stale_epoch, stale_generation, wrong_recipient, not_yet_valid, expired, intent_mismatch, approval_required, approval_unexpected, approval_denied, invalid_approver and independence_required. Check scope/hashes first, then epoch/generation/recipient, time, intent and decision/actor consistency in that order. It describes only consistency of supplied declarations.

EvidenceRef fields are artifact_id, sha256, media_type (max 128), classification public/internal/confidential/restricted and source_kind target_readback/signed_receipt/deterministic_check; lists max 64. No URL or raw evidence is embedded. RuntimeEvent payload variants are fixed in this task: admission(package/configuration/plan hashes, recovery_epoch); step_started(input_sha256); step_result(output_sha256); step_failed(code); wait_started(wait_id, kind timer/external/human, correlation_sha256, not_before_ms, expires_at_ms); signal_received(wait_id, correlation_sha256, signal_sha256); timer_elapsed(wait_id); intent_created(intent); decision_recorded(decision); action_dispatched(command); action_receipt(receipt); dispatch_timeout(intent_sha256, may_have_dispatched); reconciliation_observed(intent_sha256, observation effect_present/effect_absent/unknown, rule_id/version, evidence_refs, qualified_absence); outcome_recorded(outcome); execution_completed(output_sha256); cancellation_requested(reason_code); execution_failed(code). Failure codes are unavailable/rate_limited/timeout_before_dispatch/invalid_output/cancelled/unknown; cancellation reasons owner_requested/policy_revoked/recovery_fence. Step/action/attempt references are required for corresponding variants and null otherwise. Every payload digest is recomputed. Actor identity, artifact provenance and qualified_absence are declarations requiring later authentication, not SDK-certified facts.

- [x] **Step 1: Write binding and wire tests.** Add fresh intent/decision/command/context fixtures in executable conftest, with literal expected intent bytes/digest independently calculated from the frozen binding. Mutate each binding field individually and assert changed intent digest; change each command/context field and assert refusal. Pin expiry equality, bool/float timestamps, wrong actor/requester, generic form data, extra fields, mutable instances and wire byte/collection caps.

```python
def test_changed_intent_cannot_reuse_decision(intent_input, decision_input, binding_context):
    old = build_action_intent(intent_input)
    changed = build_action_intent({**intent_input, "target_sha256": "f" * 64})
    assert changed.sha256 != old.sha256
    result = check_decision(changed, decision_input, binding_context)
    assert result.valid is False
    assert result.code == "intent_mismatch"
    assert result.authority_granted is False
```

- [x] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_actions.py sdk/python/tests/executable/test_wire.py -q; expect missing wire/binding interfaces.** Fixture inputs contain synthetic IDs/hashes only.
- [x] **Step 3: Implement strict models, digest builders and consistency checks.** Revalidate both supplied digests and their normalized payloads; never trust an incoming intent_sha256 without the corresponding binding. Require deny/expired/mismatched decisions to refuse; approval mode none requires decision=None and null decision digest, while human requires the exact allowed human decision. Command checks require matching current supplied recipient, fencing generation and recovery epoch, without claiming those values were authenticated.
- [x] **Step 4: Verify actions/wire plus compiler/type suites and Ruff.** Check package/model/literal handle spoofing still fails, every binding field contributes to the hash, and no raw customer/private material appears in errors. A valid structural result always leaves signature status unchecked and authority false.
- [x] **Step 5: Commit feat: bind v2 action approvals and runtime records.** Review assesses exact context binding, immutable identities, time boundaries and absence of hidden authentication assumptions.

## Task 6: Specify pure event transitions, uncertainty and completion

**Files:** transitions.py, test_transitions.py, wire event-payload union and literal transition fixtures; guide/exports.

**Interfaces:** ContractState, EventContext, KnownEvent, CompletionSummary, TransitionReport; event_digest(raw: object) -> str; check_event_transition(state: object, event: object, context: object) -> TransitionReport. EventContext supplies expected installation/tenant/execution/source/source-generation/recovery-epoch, independent package/configuration/plan hashes, now_ms, prior source sequence (initially 0), exact active_step and expected step/action/attempt, an optional matching known event ID/full-record digest, relevant BindingContext/intent/decision/receipt/wait facts, effective RetryPolicy and a CompletionSummary when completion is requested. Expected opaque target_operation_ref and paired reconciliation/verification rule IDs/versions support exact receipt/evidence correlation. It is a bounded snapshot of relevant facts, not a persistent inbox or unbounded history. Attempts are zero-based: dispatch requires attempt < max_attempts, and a further retry requires attempt+1 < max_attempts. Qualified absence is a separate retry basis without an invented failure code; it can resolve accepted acknowledgement but never observed/verified effects or contradictory positive evidence. Ordinary failures and no-dispatch timeouts require their actual allowed retry code.

ContractState has phase new/running/waiting/reconciliation_required/cancelled/failed/completed, relevant logical action state not_dispatched/dispatching/accepted/observed/unknown, and outcome unknown/not_verified/verified. CompletionSummary contains required_effects, verified_effects, not_verified_effects, unknown_effects, unfinished_steps (all 0..10000); outcome counts partition required_effects. These caller-supplied facts remain synthetic; the future authority must load authenticated durable state.

TransitionReport contains disposition applied/duplicate/refused/reconciliation_required, fixed code, resulting state, accepted_source_sequence, structurally_retryable exact bool, assurance=synthetic-contract-evaluation and authority_granted=false. Applied advances accepted_source_sequence to the event sequence; every other disposition retains the supplied prior sequence. Refusal never changes state. Same-ID replay compares the digest of the entire normalized event, including identity/sequence/time, not only payload_sha256. The supplied known-event record must refer to the incoming ID.

Transition codes are applied, duplicate, replay_conflict, sequence_gap, stale_sequence, context_mismatch, illegal_transition, wait_mismatch, wait_not_ready, wait_expired, binding_refused, reconciliation_required, evidence_missing, verification_missing, completion_incomplete, terminal_state and retry_exhausted. A binding_refused report may include the fixed BindingReport code, never free text. A reconciliation result retains the last accepted sequence and known effects; it cannot mark completion or authorize replay.

| Event group | Required result |
| --- | --- |
| admission, step_started/result/failed | Validate expected identity and legal current phase; admission is new->running. Step IDs/attempts must match supplied bindings; no unsolicited result completes an execution. |
| wait_started, signal_received, timer_elapsed | Only matching correlation/wait identity and unexpired signal or reached timer deadline resume waiting. Earlier timers, unsolicited or mismatched signals refuse. Assignment/escalation metadata grants no authenticated identity. |
| intent_created, decision_recorded, action_dispatched | Reuse Task 5 consistency rules; decision immutable, deny/expired/mismatch/cancelled state refuses dispatch. Supplied fencing/epoch/context must match. |
| action_receipt, dispatch_timeout, reconciliation_observed | Accepted stays unverified. Possible-dispatch timeout becomes unknown/reconciliation_required. Declared read-back can report observed effect, qualified absence or unknown. Retry can be structurally possible only when supplied state proves no dispatch, or explicit qualified absence under the same intent/idempotency identity has evidence and matching reconciliation rule. Attempt limit, expiry, cancellation and epoch checks still apply; fresh authority remains required elsewhere. |
| outcome_recorded, execution_completed | Verified requires deterministic rule/evidence/receipt binding. Completion requires no unfinished steps, no unknown/not-verified effects and verified_effects==required_effects. Zero-effect computations may complete without claiming a verified customer-side effect. |
| cancellation_requested, execution_failed | Cancellation prevents future dispatch; late evidence may be retained by a future store but cannot resurrect authority or turn cancelled state into completed. Failure/cancellation never erase known or unknown effects. Compensation is a separate action with its own binding and outcome. |

- [x] **Step 1: Write literal transition-table tests and invariants.** Build table fixtures independently of implementation for allowed/refused transitions, including duplicate admission, changed payload/header with reused ID, prior sequence+2, out-of-order events, stale epochs/generations, wait deadlines, cancelled state, changed decisions, ambiguous receipts, absent evidence and compensation failure.

```python
def test_replay_gap_and_unknown_write_do_not_complete(states, events, contexts):
    conflict = check_event_transition(states.running, events.reused_id_changed, contexts.known)
    assert (conflict.disposition, conflict.code) == ("refused", "replay_conflict")
    gap = check_event_transition(states.running, events.sequence_gap, contexts.fresh)
    assert gap.disposition == "reconciliation_required"
    timeout = check_event_transition(states.dispatching, events.dispatch_timeout, contexts.dispatched)
    assert timeout.state.action_state == "unknown"
    assert timeout.structurally_retryable is False
    assert timeout.authority_granted is False
```

- [x] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_transitions.py -q; expect missing reducer interfaces.** Fixture factories states/events/contexts are introduced in this task and yield immutable fresh typed inputs.
- [x] **Step 3: Implement bounded deterministic transition checks.** First validate record/payload digests and scope/source/generation/recovery-epoch context, then known-ID replay/conflict, then exact next sequence, then current attempt/wait/phase-specific guards. This permits known identical historical events to be harmless duplicates while still fencing old epochs. A gap requires reconciliation without accepting completion; an unseen older sequence refuses. Emit fixed codes without dropping contradictory evidence or reading clocks/files/network.
- [x] **Step 4: Verify transition/action/compiler tests and Ruff.** Mutate every state/context model to prove revalidation. Repeated input gives identical report bytes and leaves input unchanged. Assert every report has authority=false, and no pure check is described as real persistence, permission, provider read-back or exactly-once execution.
- [x] **Step 5: Commit feat: define deterministic runtime event contract checks.** Task review traces cancellation, unknown-write handling, sequence/replay precedence and completion evidence.

## Task 7: Add deterministic private executable differences

**Files:** executable/diff.py, test_diff.py and guide/exports. Do not modify v1 diff semantics.

**Interfaces:** ExecutableChange, ExecutableDiff; diff_executable_packages(before: object, after: object, *, include_values: bool=False) -> ExecutableDiff; validate_executable_diff(raw: object) -> ExecutableDiff; executable_diff_json(raw: object) -> bytes. Reports include before_sha256/after_sha256, exact include_values, behavioral_compatibility=not_assessed and authority_granted=false. Inputs use structural v2 validation/canonical identity; the report does not require local dependencies or claim behavioral compatibility.

Changes match globally unique authored step IDs, region/branch IDs and exact declaration identities. Categories are metadata, data_mapping, control_flow, model_tools, connector_action, approval_policy, outcome and limits. Operations are added, removed, changed, moved and reordered. Record before/after source pointers and IDs; logical region movement can retain identical positional pointers. Default private detail normalizes to values=null, with no authored payload; only exact include_values=true admits the before/after detail object; sort by category/identity/before/after/operation. Moving a step to another region and editing it produces both move and change; common-item relative-order changes are independent. Parent region comparison excludes child step contents already independently compared, while region exports/guards/join settings remain significant. Public schemas, package result mappings, all declarations and dependency pins are covered.

- [x] **Step 1: Write literal change-list tests.** Test no-op normalized input; add/remove; reorder+edit; move+edit across branches; changed mapping/approval/reconciliation/outcome/budget; kind replacement; Unicode pointers and private instruction/literal sentinels. Freeze category/order/path expectations independently of the algorithm.

```python
def test_move_and_edit_remain_private(case, expected_move_and_edit):
    report = diff_executable_packages(case("before_move"), case("after_move_and_edit"))
    assert [(c.operation, c.identity) for c in report.changes] == expected_move_and_edit
    assert "PRIVATE_SENTINEL" not in report.model_dump_json()
    assert report.authority_granted is False
```

- [x] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_diff.py -q; expect absent v2 diff interface.** This task introduces the two named fixtures and literal expected_move_and_edit fixture.
- [x] **Step 3: Implement identity-aware structural comparison and bounded reports.** Preserve all meaningful changes; default output omits values. A real bool opt-in includes local details. Revalidate report mutations and actual formatted ASCII output; over 8 MiB refuses without truncating changes into an incomplete success.
- [x] **Step 4: Verify v2/v1 diff suites, package/canonical tests and Ruff.** Assert deterministic reports under raw object-member reordering and unchanged input objects; significant arrays still differ.
- [x] **Step 5: Commit feat: compare executable workflows with private deterministic diffs.** Independent review checks missing/duplicated nested changes and separation from authorization/behavioral compatibility.

## Task 8: Sign v2 packages and runtime commands in distinct contexts

**Files:** framing.py, signing.py, test_signing.py, attributed signing fixtures and guide/exports. Consume existing TrustSet, Ed25519/file helpers and Task 5 wire validation; do not modify v1 framing.

**Interfaces:** ExecutableSignatureEnvelope, CommandSignatureEnvelope, ExecutableVerificationReport; sign_executable_package(raw: object, private_key: Ed25519PrivateKey) -> ExecutableSignatureEnvelope; verify_executable_package(raw: object, envelope: object, trust_set: object) -> ExecutableVerificationReport; sign_runtime_command(raw: object, private_key: Ed25519PrivateKey) -> CommandSignatureEnvelope; verify_runtime_command(raw: object, envelope: object, trust_set: object) -> ExecutableVerificationReport.

V2 envelope fields are exact format_version 2, algorithm ed25519, context lotuspond-workflow-package-signature-v2, schema_version lotuspond.workflow-package.v2, canonicalization lotuspond.workflow-package.jcs.v2, key_id, sha256 and signature. Command envelope uses format_version 1, context lotuspond-runtime-command-v1, schema_version lotuspond.runtime-command.v1 and canonicalization lotuspond.runtime-wire.jcs.v1. Public keys remain outside envelopes. Signature is 128 lowercase hex. H is sorted compact ASCII JSON of every envelope field except signature; C is exact JCS package/command bytes. Frame is context ASCII + NUL + uint64be(len(H)) + H + uint64be(len(C)) + C. The internal frame helper is called only with validated context-specific envelopes.

Verification report fields: valid, code verified/untrusted_key/revoked_key/digest_mismatch/invalid_signature, content_kind package_v2/runtime_command_v1, sha256, key_id, contract_valid=true, compatibility_status=not_checked, binding_status=not_checked and authority_granted=false. Malformed context/version/header is an input error, never a fallback to another verifier. Allowed/revoked precedence and duplicate/degenerate trust refusal remain the reviewed v1 behavior.

- [ ] **Step 1: Write independent framing/signature tests.** Create literal synthetic frames/signatures using RFC 8032 key material and independent OpenSSL signing, retaining provenance. Assert both directions, every signed header field, wrong key/content, v1/evidence/command/package cross-context refusal, malformed formats, duplicate/revoked trust and mutated models. An internally valid signature over a wrong-context command must still fail Task 5 binding.

```python
def test_signed_wrong_recipient_is_not_authority(command_input, key, trust, intent, decision, context):
    command_input["worker_id"] = "another-worker"
    envelope = sign_runtime_command(command_input, key)
    assert verify_runtime_command(command_input, envelope, trust).valid is True
    result = check_command_binding(command_input, intent, decision, context)
    assert result.valid is False
    assert result.authority_granted is False
```

- [ ] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_signing.py -q; expect missing signing APIs.** Existing v1 signature fixtures/tests must stay green unchanged. Missing OpenSSL on required Linux CI is a failed prerequisite, not a silent skip.
- [ ] **Step 3: Implement context-specific signing/verification.** Canonicalize once after full validation, bind all header bytes, revalidate external trust and envelope, then verify with cryptography. Command cryptographic verification deliberately leaves actual identity/current authority/binding unchecked; compose the separate Task 5 check explicitly. No signer key discovery or credentials from environment.
- [ ] **Step 4: Verify both signature suites, exact frame literals, OpenSSL/RFC/JCS oracles and Ruff on 3.13/3.14.** Exercise wrong-algorithm keys, report/output caps and known key-encoding guards through the new APIs; do not broaden subgroup claims.
- [ ] **Step 5: Commit feat: sign v2 packages and runtime commands with domain separation.** A crypto-capable independent reviewer checks both framing contexts and trust/authority separation.

## Task 9: Assess explicit synthetic v2 capability declarations

**Files:** compatibility.py, test_compatibility.py, synthetic capability/requirements examples and guide/exports.

**Interfaces:** ExecutableCapabilityProfile, ExecutableRequirements, ExecutableCompatibilityReport; check_declared_compatibility(raw: object, profile: object, requirements: object, *, dependencies: list[object], catalog: object) -> ExecutableCompatibilityReport. The function lives only in the executable namespace; the root v1 name is unchanged.

Profile fields are required: format literal lotuspond.workflow-capabilities.v2, assurance synthetic-declaration, schema_versions, compiler_revisions, canonicalizations, step_kinds, expression_functions, value_kinds, exact artifact/operation/model-configuration pins, platforms and supported_limits. Lists max 64 (operations 32), empty means no support, unknown literals/conflicting exact records are malformed. Platform natural key is OS/architecture; artifact identity includes kind/name/version/digest; model configuration includes exact revision/digest. Requirements contain exact format_version 1, runtime_contract, OS, architecture and required_capabilities max 64; no platform/environment defaults.

Report contains contract_valid=true for returned structurally valid packages, closure_complete, compilation_status valid/unresolved, compatible, package/profile/requirements digests, at most 20 sorted fixed reasons, reasons_truncated, signature_status=not_checked, assurance synthetic-declaration and authority_granted=false. Missing well-formed dependency definitions yield unresolved/false without a plan; conflicts/cycles/bad digests/type errors are invalid input. Actual provider/binary availability, policy, credentials and live support remain not_checked.

Reason codes are dependency_missing, unsupported_schema, unsupported_compiler, unsupported_canonicalization, unsupported_step, unsupported_expression, unsupported_value, unsupported_artifact, unsupported_operation, unsupported_model_configuration, unsupported_platform, missing_capability and insufficient_limit; sort by code then sanitized path.

- [ ] **Step 1: Write complete/partial/refusal tests.** Match every declared feature/pin, including unused declarations, all step/expression/value kinds, platform capabilities and package budget limits. Remove one requirement at a time. Test empty support, duplicate pairs, unknown formats, omitted fields, max lists, modified typed inputs, missing closure and truncation that remains incompatible.

```python
def test_missing_expression_or_model_pin_refuses(case, catalog_case, profile, requirements):
    profile["expression_functions"].remove("coalesce")
    result = check_declared_compatibility(case("optional_default"), profile, requirements,
                                         dependencies=[], catalog=catalog_case())
    assert result.compatible is False
    assert any(r.code == "unsupported_expression" for r in result.reasons)
    assert result.signature_status == "not_checked"
    assert result.authority_granted is False
```

- [ ] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_compatibility.py -q; expect missing interfaces.** Existing v1 profiles must still be accepted only by v1 APIs.
- [ ] **Step 3: Implement strict projection and exact assessment.** Use Tasks 3–4 validation/closure/compiler, compare every declaration and each package limit against declared support, and never merge conflicting entries. Hash normalized profile/requirements separately from package content. Derive incompatible from the complete refusal set before truncating display; include no raw values in reasons.
- [ ] **Step 4: Verify v1/v2 compatibility, compiler and serialization tests plus Ruff.** Independent expected hashes, sorted reasons and actual formatted-output cap must pass on both Python versions. Document structurally valid versus compiled versus declared support explicitly.
- [ ] **Step 5: Commit feat: assess synthetic executable capability declarations.** Independent review verifies complete feature accounting, closure/report truth and exact-version behavior.

## Task 10: Deliver explicit v2 CLI, schemas and verified artifacts

**Files:** Task 10 rows in file map, guides/progress/exports, committed schemas and existing installed-artifact fixtures. Controller owns whole-branch review, final integration/PR/CI; the implementer owns CLI/schema/docs/tests and scoped commit.

**Interfaces:** executable.cli.main(argv: list[str] | None=None) -> int; executable.schema.export_schema(kind: Literal["package", "plan", "runtime"]) -> dict. Root CLI delegates only an explicit leading v2 token; existing forms/JSON/statuses are byte-compatible. Runtime schema is a discriminated union of all versioned Task 5 records; export also commits contracts/compiled-workflow.v2.schema.json and contracts/runtime-contracts.v1.schema.json. Existing schema export/parity remains unchanged.

| CLI form after lotuspond-package v2 | Behavior |
| --- | --- |
| validate PATH; digest PATH | Structural v2 validation/digest, explicitly compilation_status=not_checked and authority=false. |
| canonicalize PATH --output PATH | Exclusive 1 MiB JCS canonical file; digest-only result. |
| compile PATH --catalog PATH [--dependency PATH ...] --output PATH | Caller explicitly supplies bounded local files, max 63 dependencies plus root; no paths/URLs are resolved from package content. Exclusive complete compiled-plan JCS file; result carries plan/package digests, compiler revision and false authority. |
| diff BEFORE AFTER [--include-values] | Deterministic redacted-by-default v2 report; changes still exit 0. |
| sign PATH --key PATH --output PATH; verify PATH --signature PATH --trust PATH | V2 package context only; explicit trust and reviewed key/file rules. Shared global keygen remains available without a second implementation. Runtime-command signing/binding APIs are documented and tested as Python APIs. |
| compatibility PATH --profile PATH --requirements PATH --catalog PATH [--dependency PATH ...] | Exact synthetic assessment including closure status, no inferred target. |
| schema --kind package/plan/runtime --output PATH | Exclusive deterministic exported schema; digest/format result. Developer schema parity command compares committed bytes without overwriting. |

Reads: package/catalog/dependency 1 MiB each; aggregate package sources 64 MiB; key/package envelope 4096 bytes; trust/profile 65,536; requirements 16,384. Package/catalog/dependency files use Task 1's v2 decoder and typed normalization; v1 entry points retain their decoder unchanged. Signature payload/command APIs separately retain 65,536-byte wire cap. Schemas/plans/reports max 8 MiB. All reads require regular files and bounded parsing; no supplied argument/file path is echoed on failure. Exit 0 success, 2 malformed/argument/file/compiler failure, 3 well-formed signature or declaration refusal. JSON stdout includes wrapper/newline in its bound; content files are never implicitly overwritten.

- [ ] **Step 1: Write actual installed journeys and schema/image regressions.** Extend existing shared wheel/sdist-wheel fixture to exercise v1 plus every v2 CLI command from temporary directories under sanitized environments with timeouts. Build an sdist-derived wheel independently; assert no checkout/API/DB/HTTP/engine dependency is imported. Use real keys/files/canonical bytes, explicit catalog/dependency inputs, reference plan literals, malformed/FIFO/oversized/duplicate inputs, unknown sentinel-bearing arguments, no-clobber and partial-write failures. Add schema parity and a nested executable-module-only content-proof mutation regression; extend the actual image SDK probe with a small literal v2 compile case.

```python
def test_installed_v2_and_v1_remain_separate(invoke, v2_path, v1_path):
    assert invoke("v2", "validate", str(v2_path)).returncode == 0
    assert invoke("validate", str(v2_path)).returncode == 2
    assert invoke("v2", "validate", str(v1_path)).returncode == 2
    old = invoke("validate", str(v1_path))
    assert json.loads(old.stdout)["sha256"] == "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"
```

- [ ] **Step 2: Run uv run --frozen --project sdk/python pytest sdk/python/tests/executable/test_cli.py sdk/python/tests/executable/test_schema.py -q before dispatch changes; expect unsupported v2 commands.** Confirm existing legacy CLI baseline passes. Use tests that exercise meaningful behavior, not schema snapshots alone.
- [ ] **Step 3: Implement thin dispatch/schema integration and accurate guides.** Reuse each task's public APIs and reviewed file helpers. Add npm schema:executable/check:executable-contracts and include parity in npm run check/CI. SDK CI explicitly sets up repository Node version for the independent test oracle; Node/OpenSSL remain test-only dependencies. Document all model/API/CLI syntax, limits, pin/closure rules, canonical/signature IDs, numeric semantics, statuses, opaque-handle and synthetic authority boundaries. Update changelog/capabilities/ownership and dated ADR, preserving every open runtime/Studio/commercial/production gate.
- [ ] **Step 4: Verify scoped deliverables and commit.** Python 3.13/3.14 SDK suites, both installed artifact journeys, independent JCS/OpenSSL, v1 schema/import/CLI/registry parity, Ruff and strict MkDocs pass. Commit feat: expose executable v2 package tooling; report exact red/green commands, test counts, skips and limitations. A fresh task reviewer checks implementer scope before controller completion.
- [ ] **Step 5: Controller verifies full application and actual artifacts.** Run npm run check, identity-verified localhost npm run test:database, deployment checks, strict docs and whitespace. Build actual noneditable API SDK image; run immutable airgap installation/failure-recovery and require backend/nested SDK/static UI source-content matches. Keep cloud serial Node/subreaper or build-only CA adaptations separate from ordinary hosted CI; do not weaken TLS or replace artifact checks with mocks. Content matches are not signed Git provenance.
- [ ] **Step 6: Controller obtains independent whole-branch review and resolves material findings.** Supply immutable base/head/diff and all task evidence, using the strongest review model. Review v1 stability, model/canonical boundaries, typed/handle flow, closure/expansion/outcome coverage, both signature contexts, state/refusal logic and actual artifacts. Use subagent-driven fix/re-review rules; no controller product patches and no skipped shipping gates.
- [ ] **Step 7: Publish and verify the concrete draft.** Stage intended files only, push the isolated branch normally, create/attach a draft PR against its actual reviewed base and inspect complete diff plus all exact-head jobs. Record delivered contracts/tooling separately from engine/runtime/provider qualification. Preserve branches/worktrees/data and report each implementation ruling with its cost. Do not merge, release to a package index or deploy under this plan.

## Coverage and execution handoff

| Approved specification | Owning task/evidence |
| --- | --- |
| V1 preservation, JCS, limits, independent dependency/oracle evidence | Tasks 1/3/10; unchanged v1 bytes/schema/API/registry plus independent numeric vectors. |
| Typed schemas/mappings, optionality, opaque handles | Tasks 2/4/5; lexical/type/origin regressions and binding mutations. |
| All structured node families, pinned catalogs/knowledge/policies/environment/assurance | Tasks 3/4; explicit models, complete closure and literal compiled plans. |
| Decisions/parallel/iteration, retries/budgets, compensation/outcome coverage | Tasks 3/4/6; conservative counts and every normal-path obligation. |
| Action/approval/command/receipt/event contracts and safe state transitions | Tasks 5/6/8; literal refusal tables and independent signatures. |
| Private diff, v2 signatures and exact synthetic declarations | Tasks 7/8/9; identity/change/privacy, oracle and partial-support tests. |
| Installable artifacts, explicit CLI, docs, full app/DB/offline/hosted qualification | Task 10/controller; exact-source evidence and independent whole-branch review. |
| Durable-engine selection, real persistence/authority/providers, Studio/licensing/fleet/production qualification | Deliberately outside this contract subsystem; retained in the approved master requirement register and subsequent design cycles. |

This is one coherent static-contract SDK subsystem: each task produces an independently testable layer used by the compiler or its tooling; no independently deployable runtime service is hidden in the plan. Complete each task and its review sequentially, with one implementer active at a time. After the owner's written-plan review, use the already-selected subagent method without another execution-method question.

Inline plan self-review completed: the coverage table accounts for every specification section; produced/consumed names and task-owned fixtures agree; all ten Python examples parse; local links and whitespace checks pass; no placeholders or copied implementation bodies remain. Corrections made during review explicitly cover full finite-number JCS round trips, JavaScript numeric-string key ordering, agent-internal attempt accounting, identical dependency deduplication, opaque array/region provenance, current-epoch replay precedence and code-property error assertions. Each Review Focus case has concrete owning tests. The plan is longer than the design because it fixes interfaces, wire variants and exact commands; implementation bodies remain the implementers' work.

The owner approved this written plan on 14 October 2026. The selected subagent/task-review method now governs implementation; each task still requires its tests and independent review before completion.
