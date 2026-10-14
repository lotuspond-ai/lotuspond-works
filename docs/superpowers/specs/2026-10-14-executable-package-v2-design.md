# Executable package v2 and runtime contract design

Date: 14 October 2026. Status: written specification approved by the owner on 14 October 2026 for detailed implementation planning; not implemented. The owner retains subagent-driven execution with independent task reviews. The written implementation plan must be reviewed before implementation.

## Intent and place in the approved sequence

The owner wants one complete, production-qualified enterprise automation product, preserving the existing assurance system and all roadmap features. The [approved completion design](2026-10-14-product-completion-design.md) sequences local package tooling, executable contracts, a durable-engine spike, then identity/fleet and the complete execution/action/outcome journey. This specification addresses the executable-contract boundary. The production requirement register remains authoritative and open; contract delivery is not execution, deployment, customer acceptance or production qualification.

Keep the current Python API and relational authority, React console, language-specific components and recovery archive. V1 remains a declaration-only format with exactly its existing bytes, validator/canonicalizer IDs, SDK entry points and immutable registry records. An evaluation Run never becomes a production Execution. This subsystem changes no database and contacts no provider, customer system or secret store.

Success means SDK users and future Studio can author the same bounded typed representation, compile supported definitions deterministically, and exchange unambiguous action/event records. Invalid mappings, ambiguous control flow, unpinned dependencies and altered action bindings must be rejected before they can be confused with execution authority.

## Recommended approach and alternatives

Use a versioned, structured workflow representation with typed data and a deterministic compiler. Sequential bodies, decisions, parallel regions and bounded iteration have explicit nesting and outputs. Studio can render these as a graph; the compiler emits a normalized graph/step plan for the engine adapter. This gives branches and joins precise semantics and allows static data-availability checks without inventing an unrestricted programming language.

A general graph with arbitrary cross-region edges offers more authoring freedom but requires complex join, cancellation and data-availability rules. Reject those edges in the initial executable format; do not silently assign semantics. An engine-native format or executable workflow code would reduce compiler work but couple public packages to engine versions and expose a much larger execution boundary. Keep engine adoption separate behind the previously approved spike.

This is a new architectural subsystem. Approval of this written specification permits a detailed implementation plan; implementation follows that plan's review. The existing selected subagent execution method is preserved.

## Versions and public boundaries

Introduce `lotuspond.workflow-package.v2` and a separate `lotuspond_sdk.executable` namespace. Existing root-level v1 APIs keep their types and v1-only acceptance. Backend registry admission continues using the v1 validator until a separate additive registry/deployment design explicitly integrates v2. No automatic v1-to-v2 conversion or v1 execution is offered: missing data/control/approval semantics require an author to supply them.

The new namespace owns executable models, validation, compilation, canonicalization, digest, diff, detached signatures and synthetic declared-capability assessment. Add an explicit CLI command group for v2, with the existing v1 command forms unchanged. A local compiled plan is an inspectable artifact, not an installation or authorization token. The compiler accepts supplied local dependency definitions and catalogs; it never fetches references from URLs or environment credentials.

Separate these module responsibilities: bounded typed values/schemas; expressions/mappings; package/control-flow models; static validation; dependency closure; compiler/plan; action bindings; command/event schemas; signatures/diff/CLI adapters. Reuse safe input/file/error/trust helpers without weakening their v1 contracts.

## Canonical content and limits

V2 uses RFC 8785 JSON Canonicalization Scheme under the distinct identifier `lotuspond.workflow-package.jcs.v2`. Canonicalize the complete normalized validated document, including explicit defaults. Object key order is irrelevant; array order remains significant. Preserve Unicode code points without normalization and reject invalid Unicode. Reject duplicate JSON keys, nonfinite numbers, cyclic/programmatic objects, unknown fields and coercions.

Use an existing JCS library rather than implement floating-point serialization or UTF-16 key sorting. The implementation plan must review and pin `rfc8785` 0.1.4 and verify published RFC vectors plus independent JavaScript results. Read-only package inspection found no runtime dependencies and an Apache-2.0 license; this is dependency research, not an installed or approved product dependency. The reviewed project is [rfc8785.py](https://github.com/trailofbits/rfc8785.py), also distributed through [PyPI](https://pypi.org/project/rfc8785/). The inspected sdist SHA-256 is `e545841329fe0eee4f6a3b44e7034343100c12b4ec566dc06ca9735681deb4da`.

Use JSON integers only within the interoperable safe-integer range; finite IEEE-754 numbers are a separate declared type. Exact financial decimals are canonical decimal strings with an explicit scale, never binary floating point. They have no exponent, leading plus or redundant leading zeros, exactly the declared fractional digits, and no negative-zero spelling. Type checking does not coerce between numbers, strings and booleans. Typed integer fields reject boolean values and floats, including `1.0`.

Keep package input and normalized output at 1,048,576 bytes, JSON nesting at 32 and items at 10,000. Bound package definitions to 128 authored steps and 64 declarations of each dependency/capability family. Control nesting is at most 8; complete dependency closure is at most 64 packages and depth 8. Bound expressions to 64 nodes and depth 8. Per-execution expansion is bounded by a declared total step budget, at most 10,000; bounded iteration has at most 1,000 items. Limits are admission/compiler requirements and later runtime enforcement duties, not evidence of execution isolation.

Reports remain ASCII-safe, at most 8 MiB, with at most 20 sanitized errors/reasons and an explicit truncation marker. A truncated invalid/incompatible result never becomes successful. Failed explicit file writes preserve incomplete outputs for inspection and never overwrite or unlink a concurrent replacement.

## Typed data and mappings

Define a closed schema vocabulary for null, boolean, integer, finite number, bounded string, fixed-scale decimal string, bounded array and closed object. Objects declare required fields and explicit optional fields; extra fields are rejected. Arrays declare their item schema and maximum length. Local named schema references are allowed only within an acyclic bounded schema-definition set. External JSON Schema references, regex execution supplied by packages and arbitrary custom validators are unsupported.

Mappings contain typed references, literals, object/array construction and a small versioned deterministic expression vocabulary. References use JSON Pointer selection from package input, lexically available prior-step outputs or the current bounded-iteration item/index. A reference cannot read a future step, another branch's private intermediate value, an unjoined parallel output, credentials, runtime internals or undeclared environment state. Required mappings cannot rely on a missing optional source without an explicit default or branch guard. Literal values are validated against their declared type and bounds.

The initial transform vocabulary covers projection/construction, string concatenation, exact integer/decimal arithmetic, comparison and boolean conjunction/disjunction/negation. Decimal arithmetic declares scale and rounding; division by zero, overflow and unavailable fields are explicit errors. No eval, imports, shell, filesystem/network access, arbitrary callable names, wall clock or randomness is part of expressions. Each operation has a fixed input/output signature and resource bounds; unknown operations fail validation. Expression evaluation belongs to the later runtime and must use the same versioned semantics.

Only trusted runtime operations can produce opaque system handles such as action intent, human decision, action receipt and verification evidence. Package input, literals, model output and ordinary objects cannot construct or masquerade as those types. Their eventual wire representation remains untrusted until the receiving authority authenticates and reloads the corresponding tenant-bound record.

## Package content and dependencies

A package contains identity/version/name, typed input/output definitions, a structured body, declared schemas, model profiles/configuration revisions, connector operations, knowledge/artifact references, policy references, environment requirements, budgets and assurance/outcome declarations. IDs are stable and unique; labels/instructions remain untrusted private free text and are omitted from ordinary errors and diffs.

Every connector, model configuration, reusable agent, subworkflow, evaluation suite and other executable dependency is pinned to an exact version/revision and content digest where an artifact exists. There are no ranges, wildcard/default versions or implicit latest lookups. A supplied dependency catalog declares operation input/output schemas, effect class, idempotency/reconciliation requirements and capabilities. Its declarations are not evidence of real provider support or trust.

Knowledge references identify source/version, required freshness, allowed classification and expected citation shape. Package declarations cannot grant source ACLs or override deletion. Environment requirements name supported runtime contract, OS/architecture, capabilities, destination/operation classes and credential reference names. Concrete credentials, URLs with embedded credentials and customer documents do not belong in packages. Separately versioned deployment configuration and its digest remain required future admission inputs.

Standalone validation checks local structure and declarations. Compilation additionally requires the complete supplied dependency closure and exact schema/capability matches; unresolved, conflicting, cyclic or digest-mismatched dependencies produce no compiled plan. Repeated identical dependencies are deduplicated by digest. Different content under the same identity/version is a conflict. The compiler neither installs nor trusts dependencies.

## Structured control flow and node semantics

Every body is an ordered sequence with one well-defined output. Step IDs are unique across the package; nested region and iteration identities are explicit in compiled paths. Outcomes and output schemas must agree on every normal completion path. Exceptions, cancellation and ambiguous effects remain distinct from normal completion.

| Step family | Contract and completion rule |
| --- | --- |
| Input/output | Package input is validated before admission; final output is explicitly mapped and type-checked. These boundaries never confer authority. |
| Transform | Evaluates the fixed pure mapping/expression vocabulary; produces only its declared output. |
| Decision | A typed boolean condition selects exactly one of two declared bodies. Both bodies export the same declared result schema; only the selected branch runs. |
| Parallel | Declares named branches and an explicit all-branches join. Each branch has isolated lexical state; joined output is keyed by branch identity. Race/first-success/quorum joins are unsupported in this format. |
| Bounded iteration | Evaluates a bounded input array once and pins its order/content identity. Each iteration uses a stable index and isolated local state; declared concurrency and total expansion remain bounded. Results retain input order. |
| Connector read | Calls one exact declared read operation with typed input/output and bounded timeout/retry. A declared read cannot invoke a write adapter; actual adapter classification is checked at later admission/dispatch. |
| Bounded agent | Uses one pinned model profile, structured output schema, instructions, read-only declared tools and explicit step/time/token/cost limits. Model/tool output is untrusted data. An agent may propose an action but cannot authorize or directly dispatch a consequential effect. |
| Action intent | Proposes exact connector/operation, target and typed input plus policy/approval requirements. Activation must durably bind the resolved data and current execution context before any effect; the output is an opaque intent handle. |
| Human task | Names an assignment policy, bounded wait/escalation and either typed form output or an exact intent to review. Action review yields a decision handle bound to that intent; a generic form result cannot serve as approval. |
| Action dispatch | Consumes the exact intent and required decision handles. Later runtime dispatch reloads them and obtains fresh authority; approval alone is insufficient. No changed target/input/policy/version may reuse an old decision. |
| Timer/external wait | A timer has a bounded explicit delay; an external wait declares typed signal, correlation and expiry. Unauthenticated data cannot resume it. Runtime persists its occurrence/wait identity and deadlines. |
| Subworkflow | Invokes an exact digest in the validated acyclic dependency closure with checked input/output mappings and inherited stricter budgets/authority. It cannot enlarge capabilities or credentials. |
| Outcome verification | Applies declared deterministic acceptance rules to authorized target read-back/evidence and links the exact action/version. A model assertion, transport acknowledgement or missing evidence cannot produce verified success. |

Structured regions cannot jump into or out of another region. There are no arbitrary backward edges or unbounded loops. Explicit iteration and bounded agent loops carry their own limits. The compiler emits deterministic step/region/edge identities and verifies lexical availability, typing, branch exports, join ownership and conservative maximum step expansion before producing a plan. It checks every declared budget is finite and within supported limits; dynamic token/spend quota consumption remains a later runtime check, and a valid plan does not promise enough funds for every possible retry.

Human/external waits and timers do not consume active compute allowance. Separate limits govern active work, attempts, elapsed business time and retained waiting state. Cancellation stops future work at the defined dispatch boundary; it cannot undo completed or in-flight effects. Parallel failure cancels undispatched siblings, preserves known/unknown effects and does not pretend compensation completed.

## Retries, uncertainty and compensation

Each activity declares maximum attempts, per-attempt timeout and bounded backoff. Budget accounting includes every attempt and nested call. Retryable error classes are fixed codes; a model-provided string cannot select retry or recovery policy.

A consequential effect has a stable logical action ID and target-supported idempotency/reconciliation strategy. An attempt timeout after possible dispatch becomes `reconciliation_required`; it never schedules a blind replacement action. Read-back proves an effect, proves its absence under a qualified operation contract, or remains unknown. Only a proved safe retry may reuse the same intent/idempotency identity under fresh authority.

Compensation is an explicitly declared, independently policy-checked action with its own input binding, authorization, attempts, receipts and outcome. It is not a database rollback or an automatic inverse. Compensation failure and unresolved original effects remain visible. Definitions with a write operation lacking a declared reconciliation strategy are unsupported for executable compilation.

## Runtime action and event contracts

Add versioned pure wire models and deterministic state-transition checks; no API routes, database schema or engine client in this subsystem. Transition checks consume explicit prior state, event, expected identities/epochs and observation time, and return the next state or a fixed refusal/reconciliation reason without side effects. Their caller-supplied context is synthetic and unauthenticated in local tooling; it cannot authorize an action. All transport values are bounded, extra fields refused and immutable identity/hash fields validated. Wire timestamps are explicit integer UTC epoch milliseconds; issue/expiry ordering and declared maximum lifetimes are checked, while the later authority supplies the current trusted time. Records distinguish logical execution/case/step/action IDs from delivery IDs and attempt numbers.

An action intent's canonical binding includes installation/recovery epoch, workspace/project/environment, execution and logical step/action, package/configuration/compiled-plan digests, connector artifact/version/operation, target identity/digest, input digest, policy digest, approval requirement and expiry. The authority owning these records supplies tenant/context fields; package/model inputs cannot choose them. A change to any bound field changes the intent digest. Raw secrets and unnecessary customer payloads remain outside event/approval records; authenticated artifact references carry retained data.

Human decisions identify the exact intent digest, decision, authenticated actor and applicable policy/expiry. They are immutable once accepted; independent-review policy excludes the requester and model/worker service actors where human approval is required. Structural SDK validation never certifies a person's role or the independence of a decision.

Dispatch commands additionally bind worker/service identity, fencing generation, recovery epoch, command ID, issue/expiry time and replay identity. Sign transport commands under `lotuspond-runtime-command-v1`, distinct from package/evidence signatures; include the wire format identifier and every binding field in the signed canonical header/payload frame. A command requires independently trusted issuer keys, matching recipient/epoch and current authority at the execution boundary; signature validity alone is insufficient. Revoked/disconnected workers receive no new consequential authority. Previously sent requests can remain uncertain.

Events carry a distinct event ID, tenant/execution/attempt references, source identity, monotonic source sequence, event kind, canonical payload digest and observation time. Duplicate ID/same digest is a replay; duplicate ID/different digest is a conflict. Sequence gaps require reconciliation and cannot be projected as completion. Define kinds for admission, step start/result/failure, human/external wait, action intent/decision/dispatch/receipt, reconciliation, outcome and cancellation. Only valid state-specific events advance projections; late/duplicate evidence cannot resurrect cancelled authority or overwrite immutable decisions.

Receipts distinguish accepted request, observed target effect and verified business outcome. Outcome state is `verified`, `not_verified` or `unknown`, with required evidence references and rule/version identity. Every effect-producing normal completion path must declare verification covering each action, including effects in pinned subworkflows. A completed effect-producing execution requires those outcomes to be verified; absence or uncertainty cannot count as success. A purely computational workflow may complete with valid typed output, but reports no verified customer-side effect. Partial business completion and exception ownership remain separate case state.

These are contracts for the subsequent durable-engine spike and E03/E04/E05/E06 implementation. Outbox/inbox persistence, authentication, current permission checks, revocation, tenant foreign keys and recovery fencing must be implemented and failure-tested before any effect-capable runtime exists.

## SDK compiler, diff, signatures and declarations

Compile normalized package/dependency input to a deterministic intermediate plan with schema/compiler revision, source package and dependency digests, typed step/region metadata, required capabilities and bounded execution limits. Recompiling identical input yields identical canonical plan bytes/digest. Compiled output never contains private credentials, grants or resolved customer data. The later runtime must validate/recompile trusted source material and enforce its supported compiler revision rather than accept an arbitrary producer's plan claims.

V2 diff matches stable declaration/step/region IDs, reports movement/order and content changes independently, and classifies data mapping, control flow, model/tools, connector/action, approval/policy, outcome, limits and metadata. Default output excludes free-text and literal values. Explicit detail mode remains local and bounded. A content diff does not claim behavioral or authorization compatibility.

V2 detached signatures use format version 2 and the fixed context `lotuspond-workflow-package-signature-v2`, with the existing context/NUL and uint64-length-prefixed header/content framing over exact JCS canonical bytes plus bound version/algorithm/schema/canonicalization/digest/key ID. The SDK reuses Ed25519 and independently supplied allowed/revoked trust with the reviewed encoding guards; it never accepts a v1 or evidence signature under the v2 context. Any envelope version/header change is signed. Key files retain the reviewed owner-only/no-follow/no-clobber behavior. Hosted key custody and registry trust admission remain separate.

V2 capability declarations include schema/compiler/canonicalizer IDs, every step/expression kind, typed value features, exact dependency operation versions and platform/resource capabilities. Empty support means no support; unknown/conflicting declarations are invalid. Reports separately state contract validity, closure completeness, declared compatibility, signature status and authority=false. All fixtures remain synthetic until a real runtime/topology is qualified.

## Verification and acceptance

Require independent review and exact-source local/hosted verification before delivery. Tests must prove behavior, not merely schema snapshots or round trips:

- V1 canonical bytes/digest/schema, imports, CLI and immutable-registry behavior remain unchanged; no existing record is rewritten.
- Published JCS vectors, UTF-16 key ordering, numeric boundaries, negative zero, finite-number/decimal distinctions and an independent JavaScript oracle agree. Unsupported values fail safely before hashing/signing.
- Typed mappings reject unavailable branch/parallel values, optional-field mistakes, future/self references, type/coercion errors, constructed system handles, unknown expressions and all stated complexity overflows.
- Structured decisions, parallel joins, ordered bounded iteration and pinned subworkflows have literal expected compiled plans. Reorder/edit/move diffs preserve every meaningful change without default private values.
- Missing/conflicting/cyclic/digest-mismatched dependency closure produces no plan. Static compilation cannot label provider availability, credentials, tenancy or live runtime capability verified.
- Mutating each action-bound field changes its digest; changed input/target/policy/configuration/package/recovery epoch cannot reuse an approval. Structural records explicitly leave real authentication/authority unchecked.
- Duplicate/conflicting events, sequence gaps, stale worker/recovery epochs, expired decisions, cancellation, ambiguous receipts and absent evidence have specified refusal/reconciliation states; no unit test is presented as proof of real transport persistence or external exactly-once effects.
- Independent RFC/OpenSSL tests cover v2 signatures and cross-context/version replay refusal; default errors/diffs omit private sentinel values; file failures preserve existing/replaced outputs.
- Clean wheel and rebuilt-sdist installs support v1 and v2 journeys without API/database/engine dependencies. Run SDK Python matrix, full application checks, independently verified localhost DB regressions, strict docs, actual API-image/offline recovery/content proof and exact-head hosted CI.

The subsequent engine spike must demonstrate persisted restart during waits, duplicate admission, ambiguous external writes, changed/expired approvals, pause/cancel, pinned upgrades and restore from before a write or revocation. Selecting or adopting an engine is not part of this specification's implementation.

## Scope closure and retained work

This work supplies executable definition/compiler and runtime-facing contract foundations for E02 and the later runtime journey. It does not implement a production engine, connections/fleet, actual actions or human-task service, schedules, knowledge ingestion, artifact custody, Studio, licensing, federation/delegation, industry packs or deployment/operational qualification. Every one remains in the approved requirement register with its own acceptance evidence. The restricted expression/control vocabulary rejects unsupported forms explicitly; expanding it requires versioned semantics and tests.

No production database, customer integration, cloud resource, public package index, domain or deployment is authorized by this document. Local synthetic development continues within the approved repository scope. Buyer acceptance, real provider/connector qualification, supported topology, key/recovery custody and the measured service-observation gates remain required before production claims.

## Review handoff

The owner approved this written specification on 14 October 2026. The [detailed implementation plan](../plans/2026-10-14-executable-package-v2.md) now defines exact model/API/CLI names, file ownership, compatibility tests, dependency review, meaningful red/green cases and independent review gates. That plan awaits owner review before implementation. Preserve the owner's selected subagent-driven execution method; do not ask them to select it again.
