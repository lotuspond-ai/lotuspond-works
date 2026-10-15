# Lotuspond engineering guide

Lotuspond is one licensable enterprise agent automation product with separately offered transformation, implementation, training, and managed services across industries. Follow `AGENTS.md` for ownership, authorization, and database safety. `docs/architecture/0009-agent-automation-platform.md` records the approved expansion and first implementation boundary; ADR-0002 and subsequent assurance decisions retain their applicable contracts. The complete goal remains in `docs/GOAL.md`, with implemented and planned capabilities distinguished in `docs/CAPABILITIES.md`.

## Structure

`backend/lotuspond/` owns the Python API, relational models, identity, release workflow, evaluation providers, telemetry ingestion, and durable assessment worker. `web/` owns the React console. `collector/` contains the Go OTLP JSON relay; `evidence-core/` contains the Rust artifact verifier. `scripts/` contains Node execution and delivery clients and local development commands. `docs-site/` is the Git-backed documentation site, and `deploy/` is delivery packaging, including the separate customer-side execution image.

The initial TypeScript API and schema were preserved in the separate recovery archive after Python workflow validation. There is one active API and one authoritative relational model. The current product goal permits selective recovery of useful archived functionality with provenance and Lotuspond naming; never restore credentials, unrelated products, or inherited history. `voice-bridge/` handles live transcription, `protocol-adapters/` contains the HTTP agent and explicit MCP tool runners, and `sandbox/` runs allowlisted software tasks in independently configured containers.

The platform expansion preserves this assurance core. Production business execution will have a separate runtime and contracts; an evaluation `Run` is not a production execution. The first slice is a strict workflow-package v1 JSON contract, local validator, canonical content digest, schema export, and contract tests. The registry increment adds immutable tenant-scoped publication and exact canonical-content retrieval with admission-validator metadata. The standalone SDK adds local content differences, detached Ed25519 signatures against independent trust and exact synthetic capability checks. Studio, hosted publisher trust/admission, production orchestration, commercial licensing, and industry packs are later work. The master, architecture, and delivery plans under `docs/plans/2026-10-13-platform-*.md` describe that target, not available runtime features.

## Development and validation

Root npm scripts are the entry points. Run `uv sync --frozen --project backend`, `npm run db:start`, and one-time `npm run db:init` for a new empty database. Run `npm run dev` for the Python API, `npm run dev:web` for the console, and `npm run worker` for semantic jobs. Match `LOTUSPOND_ORIGIN` to the browser origin. Never use old environment files or old database names with the Python service.

Review signed-in console layouts without an account at `/dev/console.html` on the dev server, a fixture harness that is never built. Run `npm run check` and the isolated PostgreSQL tests for authorization, policy, data access, or schema changes. Go and Rust checks run independently. Record local, remote, deployment, and live-provider evidence separately in `docs/VALIDATION.md`. A configured integration is not a verified connection.

For the local package contract, run `npm run package:validate -- contracts/examples/reviewed-action.v1.json` and `npm run check:contracts`. `sdk/python/src/lotuspond_sdk/` owns the shared validation, bounded local file/CLI, diff, signing/trust and compatibility implementations. The backend `package_contract.py`, `package_input.py` and `package_cli.py` preserve legacy imports and command behavior. The independently installable `lotuspond-package` and `python -m lotuspond_sdk` expose the complete local command set; see `sdk/python/README.md`. `npm run schema:contracts` regenerates `contracts/workflow-package.v1.schema.json`; review and check schema parity after changing the model. These commands do not require a database or execute the declared workflow.

## Contracts

API paths start with `/api/v1`. Every workspace query must enforce membership or a scoped key. Body fields do not assign tenancy. Database composite foreign keys reinforce authorization. Individual signup creates a personal workspace; an organization is a deliberate additional choice.

Runs freeze scenarios. Policies and release decisions are immutable through the API. Missing or unknown outcomes block releases. Human approval requires a different administrator and the `release` action. Model judgments are advisory and never confer authority.

Local workflow-package validation checks the declared contract only. A content digest identifies the package content; it does not prove publisher identity, approval, connector compatibility, or safe execution. Future deployment and action authorization must enforce tenant/environment scope and current permissions independently of the package. Do not route package validation through an existing evaluation runner as an implicit production runtime.

Voice transcript partials retain revisions; final segments cannot change. Background assessment jobs use leased claims and retain provider, model, input digest, and source provenance. No credentials or raw provider failures may appear in logs, evidence, or public errors.

## Design

Use the documented tokens and reusable components: a daylight console with an inset workspace, icon-and-label navigation, a Home setup checklist and a ⌘K palette; muted green actions, legible records, clear states, and restrained typography. Avoid simulated activity and fabricated metrics; the only exception is the public platform preview's labelled sample workspace, whose decisions come from the tested policy port in `web/src/lib/policyPreview.ts`. Keep individual and organization flows distinct and preserve accessible mobile forms.

Public pages follow the rules in `DESIGN.md` and the structure in `docs/design-system.md`. Show the product through its real rules and quoted product strings, never invented logos, testimonials, or numbers. Build on the brand theme in `DESIGN.md` — the pond as signature scene, the field record (ink glyphs, traces, stamps, threads) everywhere else — and the serif, sans, and mono type roles rather than new motifs, never let motion cost usability, and check visible changes in a browser at 1440px, 768px, and 375px widths with keyboard focus before calling them done.

## Delivery

Do not infer hosted readiness from a build. Remote repository creation, cloud resources, hosted databases, DNS, billing, and production cutovers need explicit scope. The local initializer is not a production migration tool. Preserve existing data and prove any additive local migration independently before changing a database.

## Executable-v2 ownership (14 October 2026)

`sdk/python/src/lotuspond_sdk/executable/` owns typed authoring, deterministic
explicit-source compilation, JCS, private diffs, detached package/command
signatures, synthetic capability and pure wire/transition contracts. Only a
leading `v2` token selects its local CLI; root v1 APIs/bytes and registry admission
remain unchanged. Schemas are structural, and every authenticated runtime must
reload pinned sources/records and enforce current authority independently.
Engine, Studio, providers, licensing and production qualification remain open.
Agent goals/instructions/models/tools/context/evaluation/debugging/oversight
follow customer needs in `docs/AGENT_PRODUCT_RATIONALE.md`; Automation Anywhere
is a reference only. This SDK increment adds no UI or runtime behavior.
