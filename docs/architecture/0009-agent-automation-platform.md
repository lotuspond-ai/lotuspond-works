# ADR-0009: Expand Lotuspond into an enterprise agent automation platform

Date: 2026-10-13. Status: product scope accepted; foundation implementation in progress. Supersedes ADR-0002's assurance-only product scope, while retaining its applicable implementation, ownership, authorization, and data-safety decisions.

## Context and decision

The owner approved licensing Lotuspond's own platform and offering transformation, implementation, training, and managed automation services across industries. Lotuspond remains one product. Studio, Control Room, execution, and assurance are coordinated capabilities, not separate brands or competing APIs. Customers may use the product independently, engage Lotuspond, or appoint an implementation partner.

The target platform combines agent reasoning, deterministic workflows, business-system actions, and human tasks. Cross-industry reach comes from shared capabilities and qualified workflow/industry packs. A reference invoice, order, or healthcare-administration workflow does not define the entire market or establish support for every workflow in that industry. Clinical functionality requires its own qualification and domain expertise.

The accepted direction is described by the [master plan](../plans/2026-10-13-platform-master-plan.md), [target architecture](../plans/2026-10-13-platform-architecture.md), and [delivery plan](../plans/2026-10-13-platform-delivery.md). Their proposed components, performance targets, staffing ranges, commercial terms, and topology choices remain subject to implementation and evidence.

## Preserve the existing core

- Keep the Python API and relational model as the product authority, with one React console. Preserve current tenancy, identity, scenarios, evaluation runs, release policies, approvals, production evidence, and governance.
- Keep evaluation `Run` and future production execution separate. The supervised evaluation runner and durable assessment worker do not provide a production business runtime.
- Continue using the Go collector, Rust evidence tools, Node clients/adapters, voice bridge, and isolated software evaluation tools for their documented responsibilities. Source availability and local tests do not establish customer compatibility or hosted readiness.
- Preserve the recovery archive and provenance. Import no credentials, obsolete affiliations, unrelated products, existing customer data, or previous Git history wholesale.

## First implementation boundary: workflow-package v1

The current slice defines a strict declarative JSON package contract, local validation, a canonical content digest, schema export, synthetic examples, and contract tests. It establishes a shared input for future SDK and Studio authoring without executing the definition.

Validation must reject malformed or unsupported definitions rather than silently accepting fields or capabilities that an eventual runtime cannot interpret. The canonical digest identifies validated content consistently; it is not a signature, authorization decision, registry record, or proof of a business outcome. Schema structure and semantic validation remain distinct: consumers must use the complete validator rather than treating schema conformance alone as execution approval.

This slice does not execute code or actions, contact business systems, install packages, resolve credentials, select a durable engine, create registry/API/database records, or change existing evaluation and release behavior. Contract tests and the validation report establish only the checks they exercise.

The contract model is `backend/lotuspond/package_contract.py`; `package_cli.py` supplies the local command. The exported schema is `contracts/workflow-package.v1.schema.json`, with `contracts/examples/reviewed-action.v1.json` as a synthetic example. Run `npm run package:validate -- contracts/examples/reviewed-action.v1.json` to validate it, `npm run check:contracts` for contract checks, and `npm run schema:contracts` to regenerate the schema after a reviewed model change. No database is needed.

E01 (baseline and product ADR) is in progress. E02 (package contract and SDK) is partial: the contract foundation does not complete its planned SDK, version-diff, registry, signing, or runtime-compatibility work. Exact check results belong in the current validation report; the broader delivery goal remains open.

## Target boundaries for later slices

1. A separate production runtime executes reviewed, versioned packages in an explicit customer isolation boundary. Customer code and credentials do not run inside the shared API process.
2. A durable orchestration adapter owns progression, waits, retries, and reconciliation through a narrow internal interface. Temporal is a candidate for an architecture spike, not an adopted dependency in this slice. Resolved by [ADR-0011](0011-postgresql-durable-execution.md) on 22 October 2026: the adapter is backed by a Lotuspond-owned PostgreSQL state machine, and Temporal is not adopted.
3. Future Studio and SDK authoring produce the same versioned representation. Published definitions and deployment configuration have separate immutable identities; existing executions remain pinned unless explicitly migrated under tested rules.
4. Production actions require current tenant/environment authorization and exact reviewed inputs. Packages, model outputs, semantic assessments, and cached approvals cannot independently grant permission. Ambiguous external effects require reconciliation before retry.
5. Business outcomes link the deployed version, allowed action, human decision, and downstream evidence. A model response or successful HTTP status alone does not establish completion.
6. Licensing, idempotent usage accounting, customer-authorized service access, and independently purchasable services require their own contracts. Current trace entitlements are not full platform licensing. Expiry must preserve evidence access and bounded recovery under a disclosed continuity policy.
7. Qualify deployment topologies, connectors, and industry packs individually. Offline operation needs local dependencies, explicit data boundaries, supported identity and licensing behavior, and installation/recovery evidence.

## Consequences and acceptance

This sequence retains working assurance functionality while establishing a common package boundary before adding orchestration and authoring. It adds a new contract to maintain; schema snapshots, semantic validation, content identity, and future consumers must remain compatible. It does not justify a parallel product fork for each industry or an immediate microservice rewrite.

Follow the dependency-ordered epics and acceptance gates in the delivery plan. Preserve existing checks and run the additional checks required by each changed boundary. Local validation, Git publication, deployment, real customer execution, commercial delivery, and operational readiness remain separate evidence states in [CAPABILITIES](../CAPABILITIES.md), [GOAL](../GOAL.md), and [VALIDATION](../VALIDATION.md). No production database or infrastructure change follows from accepting this ADR.

## Registry implementation boundary (2026-10-13)

The next increment implements workspace/project-scoped immutable package versions in the Python API and relational model. Publication runs the complete v1 validator and stores canonical JSON bytes, SHA-256, publishing actor, schema, validator revision and canonicalizer identifier. Optional workspace-scoped request keys bind project plus canonical content. Existing read/write roles, key scopes, suspension, identity and MFA gates apply; operators gain no tenant bypass. Exact content is retrieved without revalidation or rewriting, and list/audit records carry fixed metadata only.

The additive v11-to-v12 migration creates two registry tables under the existing local-only advisory transaction lock. See the [implementation and reviewed migration plan](../superpowers/plans/2026-10-13-package-registry.md) and [database contract](../DATABASE.md). Registry persistence is neither installation, signature verification, release approval, runtime compatibility nor production execution. Evaluation Run remains separate from future Execution. SDK, Studio and runtime work remain open.

## Foundation review corrections (14 October 2026)

Valid typed package instances are revalidated through a bounded projection of known model fields before Pydantic can turn them into dictionaries containing nested models. Mutated or validation-bypassed models, extras and arbitrary values cannot acquire trust through instance reuse. Admission checks both submitted JSON and the complete normalized document against the unchanged 1 MiB bound; optional defaults can cause early rejection near that bound. A successfully validated, unchanged package can always canonicalize and digest. Omitted and explicit null secret references produce identical canonical content when that content fits the bound. The schema, canonicalization format and execution boundary are unchanged.

## Standalone SDK and local trust boundary (14 October 2026)

The independently installable Python SDK now owns the shared workflow-package v1
contract, decoder and canonical content implementation at
`sdk/python/src/lotuspond_sdk/`; backend contract/input/CLI modules retain import
and command compatibility. Sorted-key UTF-8 canonical bytes, normalized defaults,
array order and historical validator/canonicalizer identifiers remain unchanged.
The local CLI validates, digests, writes canonical content exclusively, compares
content, generates owner-only Ed25519 keys, signs detached envelopes, verifies
against an explicit independent trust set, and compares exact synthetic capability
declarations and explicit platform requirements. It needs no API, database, HTTP
client, provider, ambient credentials or production runtime.

Independent publisher key authentication and explicit allow/revoke state are
operator inputs; an envelope key ID never makes a key trusted. Private output is
owner-only and exclusive, and incomplete publication is retained for operator
inspection. Verification establishes integrity under the supplied allowed key,
not tenant authority, deployment admission, release approval or permission to
execute. Compatibility establishes only agreement with synthetic declarations;
signatures, real providers and runtime support are not checked by that assessment.
E02 remains partial: executable-v2/authoring, trusted admission, runtime
qualification, Studio and production execution retain their own open gates.
Installed wheel and independently rebuilt sdist-wheel checks are local artifact
evidence; they do not establish hosted or customer readiness.

## Executable-v2 local tooling (14 October 2026)

SDK 0.2.0 adds a separate executable namespace with bounded typed definitions,
explicit pinned dependency/catalog compilation, normalized RFC 8785 JCS, private
diffs, distinct package-v2/runtime-command-v1 signature contexts, synthetic
capabilities and pure runtime-wire/transition contracts. Explicit `v2` CLI forms
and structural package/plan/runtime exports expose those reviewed producers.
Root v1 APIs, identifiers, canonical bytes and v1-only registry remain stable.

These local contracts support future agent authoring and oversight around
customer goals, instructions, models, tools, context, evaluation and debugging;
see the [customer rationale](../AGENT_PRODUCT_RATIONALE.md). They add no UI or
engine. Every runtime must authenticate records and reload/revalidate pinned
sources, reauthorize actions and persist replay/reconciliation state. A signature,
compiled plan, transition result or synthetic match grants no authority. Real
provider/runtime/Studio/licensing and production qualification remain open;
installed artifacts and source-content matching are local evidence, not signed
Git provenance or customer readiness.

## Console Studio for v1 packages (15 October 2026)

The console gains the first Studio increment: a graph canvas with palette
drag-and-drop, pointer and keyboard node movement, port and keyboard edge
creation, an inspector for nodes, package fields and declarations, and a browser
admission check ported from the shared v1 validator with tested message, bound,
canonical-JSON and digest parity. Publishing calls the existing registry API with
an idempotency key; stored versions reopen as drafts. The Studio adds no routes,
schema, server authority or execution: a published version is admitted content,
not a deployment, an approval or permission to run. It composes v1 packages only;
executable-v2 authoring, Control Room and production execution keep their own
gates. Browser drafts are local state, not published or trusted content.
