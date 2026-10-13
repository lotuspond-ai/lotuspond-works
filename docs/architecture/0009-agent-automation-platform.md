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
2. A durable orchestration adapter owns progression, waits, retries, and reconciliation through a narrow internal interface. Temporal is a candidate for an architecture spike, not an adopted dependency in this slice.
3. Future Studio and SDK authoring produce the same versioned representation. Published definitions and deployment configuration have separate immutable identities; existing executions remain pinned unless explicitly migrated under tested rules.
4. Production actions require current tenant/environment authorization and exact reviewed inputs. Packages, model outputs, semantic assessments, and cached approvals cannot independently grant permission. Ambiguous external effects require reconciliation before retry.
5. Business outcomes link the deployed version, allowed action, human decision, and downstream evidence. A model response or successful HTTP status alone does not establish completion.
6. Licensing, idempotent usage accounting, customer-authorized service access, and independently purchasable services require their own contracts. Current trace entitlements are not full platform licensing. Expiry must preserve evidence access and bounded recovery under a disclosed continuity policy.
7. Qualify deployment topologies, connectors, and industry packs individually. Offline operation needs local dependencies, explicit data boundaries, supported identity and licensing behavior, and installation/recovery evidence.

## Consequences and acceptance

This sequence retains working assurance functionality while establishing a common package boundary before adding orchestration and authoring. It adds a new contract to maintain; schema snapshots, semantic validation, content identity, and future consumers must remain compatible. It does not justify a parallel product fork for each industry or an immediate microservice rewrite.

Follow the dependency-ordered epics and acceptance gates in the delivery plan. Preserve existing checks and run the additional checks required by each changed boundary. Local validation, Git publication, deployment, real customer execution, commercial delivery, and operational readiness remain separate evidence states in [CAPABILITIES](../CAPABILITIES.md), [GOAL](../GOAL.md), and [VALIDATION](../VALIDATION.md). No production database or infrastructure change follows from accepting this ADR.

## Foundation review corrections (14 October 2026)

Valid typed package instances are revalidated through a bounded projection of known model fields before Pydantic can turn them into dictionaries containing nested models. Mutated or validation-bypassed models, extras and arbitrary values cannot acquire trust through instance reuse. Admission checks both submitted JSON and the complete normalized document against the unchanged 1 MiB bound; optional defaults can cause early rejection near that bound. A successfully validated, unchanged package can always canonicalize and digest. Omitted and explicit null secret references produce identical canonical content when that content fits the bound. The schema, canonicalization format and execution boundary are unchanged.
