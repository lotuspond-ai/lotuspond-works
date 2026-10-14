# Lotuspond Python SDK

Independent local tooling for workflow-package v1 and executable-v2 definitions;
Python 3.13 or later. Runtime
dependencies are Pydantic, cryptography and exactly pinned rfc8785 0.1.4. No API server, database, Redis,
HTTP client, model provider or runtime execution is needed.

Build and install from this repository:

```sh
uv build --project sdk/python
python -m pip install sdk/python/dist/lotuspond_sdk-0.2.0-py3-none-any.whl
lotuspond-package validate contracts/examples/reviewed-action.v1.json
python -m lotuspond_sdk validate contracts/examples/reviewed-action.v1.json
```

The CLI keeps historical validation JSON and supplies local digest, diff,
detached trust and synthetic compatibility commands.

## CLI commands and local limits

`lotuspond-package`, `python -m lotuspond_sdk` and the legacy
`python -m lotuspond.package_cli` dispatch the same commands. The legacy module
requires the backend installation; the standalone entry points need only the SDK.
Use `--help` or `COMMAND --help` for usage. All paths are explicit and relative to
the caller; options cannot be abbreviated.

| Command | Result |
| --- | --- |
| `validate PATH` | Historical validation JSON with `ok`, `schema_version` and `sha256`. |
| `digest PATH` | Validated canonical `sha256`. |
| `canonicalize PATH --output PATH` | Exclusive canonical UTF-8 output file; digest on stdout. |
| `diff BEFORE AFTER [--include-values]` | Deterministic content changes; values omitted unless requested. Changes still exit 0. |
| `keygen --private PATH --public PATH` | Exclusive PKCS8 private/SPKI public PEM files; public-key fingerprint on stdout. |
| `sign PATH --key PATH --output PATH` | Exclusive detached signature JSON file; digest and key ID on stdout. |
| `verify PATH --signature PATH --trust PATH` | Verification report using an independent explicit trust set. |
| `compatibility PATH --profile PATH --requirements PATH` | Exact comparison with explicit synthetic declarations. |

Except `validate`, successful commands and well-formed refusals print one
ASCII-safe JSON object with `ok` and `result`. Validation output keeps its original
shape. Argument, input, file and format failures print `ok: false` with at most 20
sanitized `errors` (`path`, fixed `code`, fixed `message`), never raw values,
unknown field names, supplied paths, private keys or tracebacks. Exit **0** means
success (including a diff containing changes); **2** means argument/input/file/
format failure; **3** means a well-formed signature/trust refusal or incompatible
declaration. `--help` prints usage and exits 0. No network, ambient credential,
key discovery or overwrite behavior is added.

| CLI input/report | Byte limit |
| --- | ---: |
| Package, including normalized canonical content | 1,048,576 |
| Private PEM or signature envelope | 4,096 |
| Trust set or capability profile | 65,536 |
| Compatibility requirements | 16,384 |
| Complete JSON stdout report, including its newline | 8,388,608 |

All inputs must be bounded regular files; named pipes are refused without waiting
for a writer. Structured auxiliary JSON reuses UTF-8, duplicate-key, nonfinite
number and depth-32 checks, enforces 10,000 JSON items, then uses its own strict
model limits. Oversized reports fail with `report_too_large` instead of dropping
changes. Canonical bytes and signature envelopes go only to explicit exclusive
files. Every second write refuses and preserves existing bytes. A write failure
after creation retains the destination (`output_incomplete` or
`keypair_incomplete`); inspect it before retrying. Private keys never go to stdout.

The tested private-key file boundary is Linux: owner-only mode 0600, ownership by
the current user and refusal of final-component symlinks. Platforms missing the
required POSIX file primitives fail closed; other platforms are not qualified.
Python 3.13 and 3.14 are tested; the distribution requires Python >=3.13,
Pydantic >=2.12,<3 and cryptography >=50,<51. OpenSSL is a test oracle, not a
runtime dependency.

For a local synthetic journey, supply a validated `workflow.json` and use fresh
output paths:

```sh
lotuspond-package validate workflow.json
lotuspond-package digest workflow.json
lotuspond-package canonicalize workflow.json --output workflow.canonical.json
lotuspond-package diff workflow.json workflow.canonical.json
lotuspond-package keygen --private publisher.private.pem --public publisher.public.pem
lotuspond-package sign workflow.json --key publisher.private.pem --output workflow.signature.json
# Independently authenticate the public key and provision trust.json first.
lotuspond-package verify workflow.json --signature workflow.signature.json --trust trust.json
lotuspond-package compatibility workflow.json --profile synthetic-capabilities.v1.json --requirements synthetic-requirements.v1.json
```

Generate keys only for a publisher you control. Verification uses a public key
obtained and authenticated out of band, not one inferred from the envelope. The
explicit trust-set format, rotation/revocation procedure and Python interfaces
are described below. An empty trust set refuses with `untrusted_key`; revoked
entries refuse with `revoked_key`. A valid signature grants no tenant authority,
deployment admission, release approval or execution permission. Compatibility is
always a synthetic declaration with unchecked signatures and false authority;
real provider and runtime qualification remains open.


```python
from pathlib import Path
from lotuspond_sdk import load_package, canonical_package_json, package_digest

package = load_package(Path("workflow.json"))
canonical_bytes = canonical_package_json(package)
digest = package_digest(package)
```

`validate_package(raw)` revalidates typed instances, including nested mutations.
`decode_package_bytes(data)` rejects duplicate keys and nonfinite numbers.
`PackageReadError.code` and sanitized Pydantic `ValidationError` retain existing
backend behavior; operational file failures use fixed-code `SdkError`.
`read_regular(path, max_bytes=..., private=False)` reads bounded regular files;
private reads additionally require current-user ownership, owner-only permissions
and a platform that can refuse symlinks atomically.

Input is limited to 1,048,576 bytes, depth 32 and 10,000 JSON items. Canonical
content uses sorted keys, UTF-8, explicit normalized defaults and significant
array order. Canonicalization and validator revision identifiers are exported
from `lotuspond_sdk`; the backend uses this same implementation.

Compare two validated packages locally using the repository’s reviewed-action example:

```python
from copy import deepcopy
from lotuspond_sdk import diff_packages

package = load_package(Path("contracts/examples/reviewed-action.v1.json"))
after = deepcopy(package)
after.nodes[2].instructions = "Propose a different update."
after.nodes.reverse()
report = diff_packages(package, after)
[(change.category, change.before_path, change.after_path) for change in report.changes]
# [('graph', '/nodes', '/nodes'),
#  ('instructions', '/nodes/2/instructions', '/nodes/3/instructions')]
report.model_dump_json()
```

`diff_packages(before, after, include_values=False)` revalidates both inputs and
returns `PackageDiff` with content digests, a tuple of `PackageChange` records,
and `authority_granted=False`. Each change contains a category, operation,
declaration ID and RFC 6901 paths into the corresponding normalized document;
absent locations are `None`. Default output omits value payloads, including
names, instructions and revisions. Declaration IDs remain visible.

Declarations match by ID, edges by source/target and connector operations by
value. A changed relative order of common elements emits `reordered`, alongside
edits or additions/removals. Insertion alone does not count as a reorder. Output
sorts by category, declaration ID, before path, after path and operation, treating
missing IDs/paths as empty strings. Normalized defaults and object key order do
not create changes; array order remains significant for both diff and digest.

`include_values=True` explicitly enables potentially sensitive local detail.
Only actual booleans are accepted; a string such as `"false"` raises `TypeError`.
Detail records contain JSON `values.before` and `values.after`, with `None` for
absent values. Reorder payloads show declaration IDs, edge pairs or operation
values in sequence order. Report models forbid extra fields and revalidate typed
instances and serialization. JSON is ASCII-safe and capped at 8 MiB; oversized
reports raise `SdkError` with `code="report_too_large"` without dropping changes.

Validation and content digests check declarations and integrity only. They do
not install, execute, authorize, resolve secrets or establish publisher
trust or runtime compatibility. A diff provides no behavioral compatibility,
safety, trust or authority assessment. Only workflow-package v1 is supported.

## Detached signatures and independent trust

```python
from lotuspond_sdk import generate_keypair, load_signing_key, sign_package, write_new

# Use new paths in an operator-controlled directory, with synthetic local data.
key_id = generate_keypair(Path("publisher.private.pem"), Path("publisher.public.pem"))
envelope = sign_package(package, load_signing_key(Path("publisher.private.pem")))
write_new(Path("workflow.signature.json"), (envelope.model_dump_json() + "\n").encode("ascii"))
```

Private PEM is unencrypted PKCS8; public PEM is SubjectPublicKeyInfo (SPKI).
Private files are created with mode 0600, require current-user ownership and no
group/other access, and are read without following the final symlink. Key reads
are nonblocking, require a regular file and are limited to 4096 bytes. Encrypted,
malformed and non-Ed25519 private keys are rejected. Unsupported private-key
platforms fail closed with `unsupported_key_platform`; permissions are not
silently weakened. Use protected custody and access-controlled offline backups:
the SDK supplies neither a keystore nor secret recovery, and Python does not
promise zeroization of in-memory private-key objects.

All new outputs use exclusive creation and refuse existing files, directories,
symlinks and named pipes. `write_new(path, data, private=False)` requires bytes and
an actual boolean, completes short writes and fsyncs the file. Public output mode
is 0644 masked by the caller’s umask. Once a destination has been created, a
publication failure preserves it, including any concurrent replacement. Failed
standalone publication returns `output_incomplete`; failed keypair publication
returns `keypair_incomplete`. Both require operator inspection: a complete private
key, a partial key file or partial signature output can remain. Do not blindly
retry into the same paths. Pre-existing outputs are refused without mutation.
No failure path unlinks a destination; POSIX cannot atomically compare an inode
and unlink against hostile concurrent directory writers. Keep parent directories
operator-controlled. Two-file publication is **not crash-atomic** and does not
promise durable parent directory entries after power loss. No key bytes or
supplied paths appear in SDK error messages.

The verifier obtains and authenticates the publisher’s public key **out of band**.
A key ID is SHA-256 of raw 32-byte public key bytes; an envelope never embeds a
public key. Provision a separate JSON trust set with exactly these fields:

```json
{"format_version":1,"keys":[{"key_id":"<lowercase hex64 fingerprint>","public_key":"<lowercase hex64 raw Ed25519 public key>","state":"allowed"}]}
```

The placeholders must be replaced with independently authenticated real values.
The trust set allows at most 64 keys, forbids extras and duplicates, and checks
every fingerprint. It rejects the established small-order compressed public-key
encodings (both sign bits) and noncanonical y encodings (y >= 2^255 - 19), so
an allowed identity-point key cannot authorize a forged signature. These are
encoding checks based on libsodium’s published constants, with cryptography
performing signature verification; they are not a full subgroup validation or a
claim of equivalence to Rust’s `verify_strict`. An empty set grants no trust. To rotate, independently
provision the new public key, allow both keys during a deliberate overlap, and
change the old entry to `revoked` when old signatures must no longer be accepted.
Revocation rejects even a mathematically valid signature immediately when that
updated trust set is supplied; the SDK does not retrieve remote revocation state.

```python
import hashlib
from cryptography.hazmat.primitives import serialization
from lotuspond_sdk import read_regular, verify_package

# This PEM must already be authenticated through an independent trusted channel.
public = serialization.load_pem_public_key(
    read_regular(Path("authenticated.publisher.public.pem"), max_bytes=4096)
).public_bytes_raw()
trust_set = {"format_version": 1, "keys": [{
    "key_id": hashlib.sha256(public).hexdigest(),
    "public_key": public.hex(), "state": "allowed",
}]}
report = verify_package(package, envelope, trust_set)
print(report.model_dump_json())
```

`sign_package(raw, private_key)` and `verify_package(raw, envelope, trust_set)`
revalidate canonical package content and all mutable typed records. Envelope and
trust format versions require integer 1; booleans and floats are rejected.
`SignatureEnvelope` fixes Ed25519, context
`lotuspond-workflow-package-signature-v1`, current schema/canonicalization IDs,
lowercase hex64 digest/key ID and lowercase hex128 signature. Signing uses
`context || NUL || uint64be(len(H)) || H || uint64be(len(C)) || C`, where `H` is
sorted-key compact ASCII JSON of all envelope fields except `signature`, and `C`
is exact canonical UTF-8 package content. This frame is distinct from evidence
manifest signatures and binds both metadata and normalized content.

Malformed envelopes/trust sets raise fixed `invalid_signature_envelope` or
`invalid_trust_set` errors. Well-formed outcomes return `VerificationReport`:
`verified`, `untrusted_key`, `revoked_key`, `digest_mismatch` or `invalid_signature`.
Unknown/revoked trust takes precedence over digest/signature checks. The report
contains the recomputed content digest, named key ID, `contract_valid=True`,
`compatibility_status="not_checked"` and `authority_granted=False`; only
`verified` has `valid=True`. All report/envelope/trust serialization revalidates
models, is ASCII-safe and is bounded at 8 MiB.

A valid detached signature establishes integrity under the supplied allowed key.
It grants no tenant authority, registry admission, release approval, deployment
admission, runtime compatibility or permission to execute. No hosted issuer or
trust registry is introduced. The CLI exposes the same local signing and
verification boundaries.

## Synthetic declared compatibility

Two separate synthetic JSON inputs accompany the reviewed-action example:
`contracts/examples/synthetic-capabilities.v1.json` declares support, and
`contracts/examples/synthetic-requirements.v1.json` supplies explicit platform
requirements. They are demonstration declarations, not a production qualification.

```python
from pathlib import Path
from lotuspond_sdk import (
    check_declared_compatibility, decode_package_bytes,
    load_package, read_regular,
)

package = load_package(Path("contracts/examples/reviewed-action.v1.json"))
profile = decode_package_bytes(read_regular(
    Path("contracts/examples/synthetic-capabilities.v1.json"), max_bytes=65_536,
))
requirements = decode_package_bytes(read_regular(
    Path("contracts/examples/synthetic-requirements.v1.json"), max_bytes=16_384,
))
report = check_declared_compatibility(package, profile, requirements)
print(report.model_dump_json())
# compatible=True; assurance="synthetic-declaration";
# contract_valid=True; signature_status="not_checked"; authority_granted=False
```

`CapabilityProfile` requires format `lotuspond.workflow-capabilities.v1`, assurance
`synthetic-declaration`, and explicit `schema_versions`, `canonicalizations`,
`node_kinds`, `connectors`, `models` and `platforms` lists. Only the current package
schema and canonicalization identifiers are accepted. Connector entries contain
`connector`, exact `version` and `operations`; model entries contain `profile`
and exact `revision`; platform entries contain `os`, `architecture` and
`capabilities`. All lists allow zero support, have at most 64 entries and require
unique natural keys; connector operations are limited to 32. Connector keys are
(connector, version), model keys are (profile, revision), and platform keys are
(os, architecture). Multiple explicitly listed versions/revisions are allowed;
duplicate pairs and conflicting platform entries are rejected, without merging.

`CompatibilityRequirements` requires **integer** `format_version: 1`, explicit
`os`, `architecture` and `required_capabilities` (up to 64 unique identifiers).
No platform default, wildcard, version range or inferred support is provided.
Requirements are assessment context because workflow-package v1 has no platform
field; they never change its canonical bytes or digest. Names, connector versions
and model revisions use the package contract's existing constraints.

`check_declared_compatibility(raw, profile, requirements)` revalidates the package
and both inputs, including mutated nested typed models. It checks every node kind,
declared connector version/operation and model revision, including unused
references, then the exact platform and every required platform capability.
Invalid profile/requirements shapes, missing fields, unknown fields or identifiers,
and duplicate/conflicting keys raise fixed `SdkError.code` values
`invalid_capability_profile` or `invalid_compatibility_requirements`. Package errors
retain their existing contract. Well-formed declarations lacking exact support
return `compatible=False`; empty support lists do not imply support.

`CompatibilityReport` includes the unchanged `package_sha256` and separate
`profile_sha256` and `requirements_sha256` digests. Assessment digests use sorted-key,
compact UTF-8 JSON of validated normalized content, retaining list order. At most
20 unique reasons sort by `(code, path)`. Codes distinguish `unsupported_schema`,
`unsupported_canonicalization`, `unsupported_node`, `unsupported_connector`,
`unsupported_operation`, `unsupported_model`, `unsupported_platform` and
`missing_capability`. Paths use fixed fields and numeric offsets; no supplied
identifiers, instructions, credentials, revisions or private values are returned.
Truncation sets `reasons_truncated=True` and preserves `compatible=False`.
Strict report serialization revalidates mutations, forces ASCII-safe JSON and
checks both the complete record and actual caller formatting against the 8 MiB
cap, raising `report_too_large` when exceeded.

A compatible result establishes only agreement with the supplied synthetic
declarations. It does not check signatures, credential availability, tenant policy,
real providers or runtime support, and grants no tenant authority, approval,
deployment admission or execution permission. Detached verification remains a
separate assessment. The CLI exposes this same declaration-only comparison.

For development, run `uv sync --frozen --project sdk/python`, then `npm run
check:sdk`. Distribution tests build a wheel and independently rebuild a second
wheel from the sdist, install both into fresh environments outside the checkout
and run complete local CLI journeys and refusal cases against each installed
artifact. Public package-index publication is a separate action.

## Executable v2 foundations

SDK 0.2.0 adds `lotuspond_sdk.executable.decode_executable_bytes`, fixed
`ExecutableError`/`ExecutableIssue` diagnostics and v2 metadata constants.
The v2 schema/canonicalizer identifiers are `lotuspond.workflow-package.v2`
and `lotuspond.workflow-package.jcs.v2`. The current increment supplies bounded
JSON/JCS, closed data schemas, static typed mappings and immutable handle-origin
metadata; package/control authoring, compilation,
runtime-facing records and CLI commands are subsequent implementation tasks.

Raw package and normalized canonical JSON stay within 1,048,576 bytes, depth 32
and 10,000 items (including object keys). Internal wire/report boundaries use
65,536/8,388,608 bytes respectively; report limits never raise package limits.
The decoder checks supplied raw bytes and preserves bounded raw integers until
a declared field chooses its domain. Internal projection bounds structure and
a byte lower bound without choosing numeric spellings; complete normalized JCS
bytes are checked after field validation and default insertion. Integer fields require exact safe integers; numbers may normalize to
finite IEEE-754 floats. JCS may spell a float such as `1.0` as `1`, or `1e20` as
`100000000000000000000`; those spellings remain readable before field selection.

The internal adapter calls reviewed Apache-2.0 `rfc8785==0.1.4`. Attributed RFC
fixtures and an independent Node scalar/UTF-16 ordering oracle pin its bytes.
Strings preserve code points without normalization; invalid Unicode, duplicate
keys, nonfinite numbers, cycles, unknown model fields and arbitrary serializers
are refused. Diagnostics contain fixed codes/messages, sanitized pointers and
at most 20 issues with explicit truncation. Reports revalidate known model
fields and count complete ASCII output, including a wrapper and final newline.
These local foundations confer no execution authority.


Typed helpers are available in that separate namespace:

```python
from lotuspond_sdk.executable import TypeEnvironment, infer_mapping, validate_value

value = validate_value("12.50", {"kind": "decimal", "precision": 4, "scale": 2}, [])
flow = infer_mapping(
    {"kind": "ref", "source": {"kind": "package_input"}, "pointer": ""},
    TypeEnvironment(input_schema={"kind": "integer", "min": 0, "max": 10}),
)
```

`DataSchema` is closed and bounded; local named schemas are acyclic. `Mapping`
permits literals, lexical references, objects, arrays and the fixed arithmetic,
comparison, boolean, `exists` and `coalesce` calls. It performs static inference
only. Required consumers reject missing optional fields unless defaulted or
refined by a direct `exists(ref)` true guard. Number literals normalize before
JCS so integral canonical spellings remain readable; integers remain exact,
and decimals retain canonical fixed-scale strings. Arithmetic runtime failures
are specified, never evaluated by this layer.

`ExportSchema`, `DataType`, `HandleType`, `FlowObjectType` and `FlowArrayType`
describe internal region results. Data cannot manufacture a handle. Compiler
metadata carries `SourceSymbol` and ordered `OriginChoice` provenance.
`select_array_item` preserves alias origins and requires presence checking;
`iteration_item_flow` produces a present but possibly symbolic current element.
An array's `iteration_id` binds only its own `$item:<iteration_id>` occurrence
slots, preserving outer-loop aliases. Nested array alternatives retain each
producer owner and cardinality; empty alternatives contribute no current item.
Already-bound choices retain their selector identity through repeated aliases.
Explicit ordered occurrence/selection binding records preserve owner identity
after slots are bound and remain part of exact provenance equality. Empty handle
arrays create no origin.
The later compiler must enforce lexical scopes, guarded branches, originating
operation kinds and correlated intent/decision provenance; wire records still
require independent authenticated reload at runtime. See
[the typed syntax and compiler handoff](../../docs-site/docs/executable-packages.md)
for exact schemas, operation signatures, selection helpers and limits.

Structured package authoring is also available in `lotuspond_sdk.executable`:

```python
from lotuspond_sdk.executable import (
    canonical_executable_json,
    executable_digest,
    validate_catalog,
    validate_executable_package,
)

package = validate_executable_package(raw_package)
canonical_bytes = canonical_executable_json(package)
sha256 = executable_digest(package)
catalog = validate_catalog({"documents": []})
```

`ExecutablePackage`, `Body`, `Step`, `ArtifactRef`, `CatalogDocument`, and
`CatalogBundle` describe closed structures. Required declaration families may
be empty. All 13 step families, exact reference pins, local schema scopes,
control bounds, strict limits and deterministic readback rules are validated.
Every literal is normalized in its declared domain before complete default,
item/depth and 1 MiB canonical measurement. Known models are revalidated after
mutation or construction; use these complete boundary functions rather than
model constructors alone. V1 root APIs, registry format and bytes stay fixed.

`canonical_catalog_document` and `catalog_document_digest` bind the complete
normalized catalog definition document, excluding the separate `ArtifactRef`
wrapper. Identical normalized catalog documents are retained and every occurrence
counts toward the family and complete bundle limits; the compiler
deduplicates resolved definitions. Conflicting normalized content for one
identity/version is refused. External binary/content checksums remain unverified claims. All
examples in `contracts/examples/executable-v2/` are synthetic and include actual
local definition pins; they do not establish provider support or authority.
Secrets and customer documents belong outside packages. Runtime-facing records
and explicit v2 CLI tooling are local contracts; engine execution remains later work. See [the complete structural contracts and compiler obligations](../../docs-site/docs/executable-packages.md#structured-packages-and-exact-catalogs).


### Compile a pinned v2 package locally

```python
from lotuspond_sdk.executable import (
    resolve_dependencies, compile_package, canonical_plan_json, compiled_plan_digest,
)

closure = resolve_dependencies(raw_package, [], {"documents": []})
plan = compile_package(raw_package, dependencies=[], catalog={"documents": []})
plan_bytes = canonical_plan_json(plan)
plan_digest = compiled_plan_digest(plan)
```

Supply the complete exact reachable local closure, including every declared pin
and catalog-internal pin; unrelated extra documents refuse. Complete admission
precedes deduplication. Root counts in the 64-package/eight-level closure limits;
raw additional package sources are limited to 63. The compiler preserves lexical
availability, complete handle/array provenance and target aliases, checks pinned
operation/model/tool schemas and inherited envelopes, and requires verification
of every dispatched action on every normal completion path. Agent steps require
an explicit typed `input` mapping. Conservative attempts include region overhead,
retries, agents, nested calls/iterations and compensation, and cannot exceed the
package limit or 10,000. Canonical plans are independently bounded to 8 MiB.
Decision selectors retain the package/source path, enclosing iteration slots and
ordered owner bindings. Consumers must preserve that complete correlation in
origin choices and array alternatives. Shared-choice projection and item
selection use context-specific DAG memoization with one bounded work budget;
selection of a different item's target cannot prove receipt verification.

Plans contain source pointers/digests and typed metadata, without copying authored
instructions or literal payloads. `authority_granted` is always false and
`artifact_availability` is `not_checked`. Runtime consumers must reload pinned
source definitions, bind authenticated records and call instances, and enforce
dynamic quotas and real outcome evidence. These APIs perform no I/O or execution.
See [the compiler contract](../../docs-site/docs/executable-packages.md#pinned-local-compilation)
for plan fields, catalog rule contexts, provenance identities and proof limits.

Action and runtime record contracts are also available in
`lotuspond_sdk.executable`: `build_action_intent`, `intent_digest`,
`decision_digest`, `check_decision`, `check_command_binding`, `validate_wire`,
`canonical_wire_json`, `wire_digest` and kind-specific validators.
`event_payload_digest(event_kind, payload)` validates the selected closed payload
and nested records before computing its normalized hash. Complete event admission
also checks outer references. All records fit 65,536 bytes, depth 32 and 10,000
items. Exact current time comes only from the required `BindingContext`.

`intent_digest` accepts a complete `ActionIntent` and rechecks the embedded
binding; it returns the binding hash. `wire_digest` hashes the entire record,
including command delivery/replay fields. Mutated models are always revalidated.
Use public validators rather than constructors as admission boundaries. Human
approval, tenant/configuration/policy/epoch/recipient bindings and expiry are
structural comparisons of supplied declarations. Valid reports retain
`signature_status="not_checked"` and `authority_granted=false`. Receipts distinguish
acceptance, observed effect and unknown; verified outcome declarations require
rule identity and evidence references. These checks authenticate neither people
nor artifacts and perform no dispatch or transition execution. See the
[complete wire contract](../../docs-site/docs/executable-packages.md#action-bindings-and-runtime-wire-declarations)
for exact fields, error precedence, event variants and downstream obligations.

Pure event checks are available through `ContractState`, `EventContext`,
`KnownEvent`, `CompletionSummary`, `TransitionReport`, `check_event_transition`
and their `validate_*` helpers. `event_digest` hashes the complete admitted event,
including source/sequence/time, rather than its payload alone.
`transition_report_json` emits a bounded ASCII report. Scope/epoch fences precede
known replay, then sequence and current-state guards. Only applied events advance
the supplied sequence. Gaps and possible writes require reconciliation;
acknowledgements do not verify effects, cancellation preserves effects, and
completion requires the supplied complete verification summary. Attempts are
zero-based. All context, time and durable-state claims are supplied and remain
unauthenticated; reports grant no authority, persistence, dispatch or retry
permission. See the [exact transition contract](../../docs-site/docs/executable-packages.md#pure-event-transition-checks)
for required context facts, phases, retry qualifications and consumer obligations.

Private v2 authored-source differences are available through `ExecutableChange`,
`ExecutableDiff` and `diff_executable_packages(before, after, include_values=False)`.
Both sources undergo structural admission; no catalog, compilation or execution
is required. Steps match global authored IDs, regions match stable owner-scoped
identities and declarations match exact family-local aliases. Movement, content
edits and common-item relative order remain separate. Every public schema,
declaration pin, result, control setting and budget participates.

Ordinary reports expose stable IDs and fixed/numeric source paths, with
`values:null` and no instruction/literal/private data details. Only an actual
boolean `True` enables local `before`/`after` detail. All reports retain
`authority_granted=false` and `behavioral_compatibility="not_assessed"`.
`validate_executable_diff` revalidates report structure and privacy;
`executable_diff_json` emits complete compact ASCII JSON plus newline within
8,388,608 bytes. Mutated/constructed reports cannot bypass exact flags or bounds.
CLI consumers must count their complete wrapper in that same budget. See the
[complete authored-source diff contract](../../docs-site/docs/executable-packages.md#private-authored-source-differences)
for categories, identity/path/order rules and serialization obligations.

### Sign v2 packages and complete runtime commands locally

`lotuspond_sdk.executable` exports `ExecutableSignatureEnvelope`,
`CommandSignatureEnvelope`, `ExecutableVerificationReport`,
`sign_executable_package`, `verify_executable_package`, `sign_runtime_command`
and `verify_runtime_command`. Supply an Ed25519 private key and an independent
root `TrustSet` explicitly. Package-v2 and runtime-command-v1 signatures use
distinct contexts and bind every required header plus the complete admitted JCS
content; v1 and evidence signatures are refused.

Use `validate_executable_signature_envelope`,
`validate_command_signature_envelope` and
`validate_executable_verification_report` for public admission of raw,
constructed or mutated records. The corresponding complete ASCII-plus-newline
serializers are `executable_signature_envelope_json`,
`command_signature_envelope_json` and `executable_verification_report_json`.
They revalidate without calling arbitrary serializers and enforce the 8 MiB
complete output cap. Malformed envelopes raise fixed `invalid_envelope`;
invalid trust retains the root SDK's `invalid_trust_set` behavior.

Verification reports leave both compatibility and binding `not_checked` and
always have `authority_granted=false`. Compose `check_command_binding` separately
with the full intent, decision and supplied current context: a correctly signed
wrong-recipient or expired command still fails binding. These APIs are pure local
checks; file/key output uses the existing safe helpers, with exclusive writes
and retained partial failures. The [executable guide](../../docs-site/docs/executable-packages.md#detached-package-and-command-signatures)
contains exact fields, framing, refusal precedence, bounds and caller obligations.

### Assess explicit synthetic v2 support

```python
from lotuspond_sdk.executable import (
    check_declared_compatibility, executable_compatibility_report_json,
)

report = check_declared_compatibility(
    package, profile, requirements, dependencies=definitions, catalog=catalog,
)
output = executable_compatibility_report_json(report)
```

`ExecutableCapabilityProfile` requires every feature list, exact artifact pins,
connector operations, model configuration pins, platforms and package capacity
limits. `ExecutableRequirements` explicitly selects runtime-contract v1,
OS/architecture and capabilities; no host defaults apply. The checker accounts
for unused declarations and the entire dependency closure, including catalog
references and implicit compiled output types. Empty lists or zero capacity mean
no support. Distinct versions are allowed; duplicate/conflicting immutable pins
are refused.

Missing well-formed definitions yield `closure_complete=false`,
`compilation_status="unresolved"` and `compatible=false` after complete supplied
input and known-edge checks. Structural validity does not assert lexical/type
compilation. Complete sources compile before support assessment; malformed or
type-invalid complete inputs raise fixed errors. Reports expose separate package,
profile and requirements digests, at most 20 sorted private reasons and explicit
truncation. All signatures, provider/binary availability, policy, credentials and
live support remain `not_checked`; authority remains false.

Public validators are `validate_executable_capability_profile`,
`validate_executable_requirements` and `validate_executable_compatibility_report`.
Canonical declaration serializers are `canonical_capability_profile_json` and
`canonical_requirements_json`; report output is revalidated ASCII JSON plus
newline under the complete 8 MiB cap. See the [full synthetic assessment contract](../../docs-site/docs/executable-packages.md#explicit-synthetic-capability-assessment)
for record fields, exact bounds, refusal vocabulary and caller obligations.
Root v1 compatibility APIs and profiles remain unchanged.

## Explicit executable-v2 commands and schemas

Use a leading `v2` with either installed entry point. Ordinary commands and root
imports preserve v1. `v2 validate PATH` and `v2 digest PATH` structurally validate
and identify content with `compilation_status=not_checked` and false authority.
`v2 canonicalize PATH --output PATH` creates complete JCS bytes exclusively.
`v2 compile PATH --catalog PATH [--dependency PATH ...] --output PATH` creates
complete compiled-plan JCS and reports both digests. Supply exact local closure
files explicitly, including unused pins; no authored path or URL is resolved.
`v2 diff BEFORE AFTER [--include-values]`, `v2 sign PATH --key PATH --output PATH`,
`v2 verify PATH --signature PATH --trust PATH`, and
`v2 compatibility PATH --profile PATH --requirements PATH --catalog PATH
[--dependency PATH ...]` use the producer APIs described above. Global `keygen`
is shared. Runtime-command signing/binding remain separate Python APIs.

`v2 schema --kind package|plan|runtime --output PATH` exclusively writes a
Draft 2020-12 schema. `export_schema(kind)` is available from the executable
namespace. Repository `npm run schema:executable` regenerates the three committed
exports for review; `npm run check:executable-contracts` compares bytes without
writing. Runtime schema checks the seven complete versioned records, outer
kind/payload correlation and the required/null step/action/attempt matrix.
Schemas do not recompute hashes, prove compilation, authenticate records or
replace public semantic validators. JSON Schema mathematical integers do not
replace Python exact int/bool guards.

Package/catalog/dependency files cap at 1 MiB each; at most 63 dependency files
plus root cap aggregate sources at 64 MiB before deduplication. Profile/trust
cap at 65,536 bytes, requirements at 16,384, private key/package envelope at
4,096. Plans/schemas and ASCII stdout including wrapper/newline cap at 8 MiB.
Existing depth/item and canonical bounds still apply. Regular-file reads refuse
pipes; outputs refuse existing files and retain incomplete writes for inspection.
Exit 0 means local success (also a nonempty diff), 2 malformed/argument/file/
compiler failure, 3 a well-formed signature or declaration refusal. Sanitized
errors cap at 20 with an explicit truncation flag, never input paths or values.

Both real wheel and independently rebuilt-sdist-wheel journeys run every command
outside the checkout with sanitized environments and timeouts. Independent
Node/OpenSSL oracles remain required test tooling. Exactly pinned dev-only
jsonschema 4.26.0 independently checks structural schema behavior; its MIT
source/license/hash provenance is in
`tests/fixtures/executable-v2/schema-validator-provenance.md` and `uv.lock`.
The installed SDK runtime needs none of these test tools, an API, database or
provider. The registry remains v1-only; engine, Studio, authenticated runtime
admission, commercial licensing and production qualification remain separate.
