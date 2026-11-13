# LotusPond Works engineering guide

LotusPond Works runs AI-agent workflows inside a company's real processes: each version is proven before release, each consequential action waits for a person's decision, and every outcome is recorded. `docs/GOAL.md` is the single statement of the problem, the core loop (Build, Prove, Run, Control, on a shared foundation) and what is out of scope; build nothing that serves no step of that loop ([ADR-0014](docs/architecture/0014-focus-on-the-core-loop.md)). Follow `AGENTS.md` for ownership, authorization, and database safety. `docs/CAPABILITIES.md` distinguishes implemented from planned capability. The product's name is LotusPond Works; code, packages, commands, headers, environment variables and databases keep the `lotuspond` identifier, and dated plans, ADRs and validation records keep the name they were written with ([ADR-0015](docs/architecture/0015-name-the-product-lotuspond-works.md)).

## Structure

`backend/lotuspond/` owns the Python API, relational models, identity, release workflow, read-only stored traces and incidents, and the durable execution engine. `web/` owns the React console. `scripts/` contains Node execution and delivery clients and local development commands. `docs-site/` is the Git-backed documentation site, and `deploy/` is delivery packaging, including the separate customer-side execution image. `reference/invoice-triage/` holds the first, synthetic reference workflow: its generator, packages, catalog documents, scenarios and stand-in AP service.

The initial TypeScript API and schema were preserved in the separate recovery archive after Python workflow validation. There is one active API and one authoritative relational model. Do not restore archived agent-testing features; never restore credentials, unrelated products, or inherited history. `protocol-adapters/` contains the HTTP evaluation runner. ADR-0012, ADR-0013 and ADR-0014 retired the assurance-era extras, outside-agent monitoring, air-gapped operation, offline identity, the authenticator requirements, tool authorization, the MCP and A2A runners and the Operations page; their tables stay, unwritten, and the schema is unchanged.

The platform expansion preserves this assurance core. Production business execution will have a separate runtime and contracts; an evaluation `Run` is not a production execution. The first slice is a strict workflow-package v1 JSON contract, local validator, canonical content digest, schema export, and contract tests. The registry increment adds immutable tenant-scoped publication and exact canonical-content retrieval with admission-validator metadata. The standalone SDK adds local content differences, detached Ed25519 signatures against independent trust and exact synthetic capability checks. The console Studio (`web/src/pages/Studio.tsx`, `web/src/components/studio/`, `web/src/lib/studio.ts`) composes a v1 package as a graph, checks it in the browser with `web/src/lib/packageContract.ts` — a tested port of the SDK validator — and publishes through the registry; it runs nothing and adds no server authority. Studio authoring of executable-v2 definitions, Control Room, hosted publisher trust/admission and production orchestration are later work; commercial licensing and industry pack libraries are out of the product's scope. The master, architecture, and delivery plans under `docs/plans/2026-10-13-platform-*.md` describe that target, not available runtime features.

## Development and validation

Root npm scripts are the entry points. Run `uv sync --frozen --project backend`, `npm run db:start`, and one-time `npm run db:init` for a new empty database. Run `npm run dev` for the Python API and `npm run dev:web` for the console; trace ingestion, monitors, incidents and their workers were retired by ADR-0013. Match `LOTUSPOND_ORIGIN` to the browser origin. Never use old environment files or old database names with the Python service.

Review signed-in console layouts without an account at `/dev/console.html` on the dev server, a fixture harness that is never built. Run `npm run check` and the isolated PostgreSQL tests for authorization, policy, data access, or schema changes. Record local, remote, deployment, and live-provider evidence separately in `docs/VALIDATION.md`. A configured integration is not a verified connection.

For the local package contract, run `npm run package:validate -- contracts/examples/reviewed-action.v1.json` and `npm run check:contracts`. `sdk/python/src/lotuspond_sdk/` owns the shared validation, bounded local file/CLI, diff, signing/trust and compatibility implementations. The backend `package_contract.py`, `package_input.py` and `package_cli.py` preserve legacy imports and command behavior. The independently installable `lotuspond-package` and `python -m lotuspond_sdk` expose the complete local command set; see `sdk/python/README.md`. `npm run schema:contracts` regenerates `contracts/workflow-package.v1.schema.json`; review and check schema parity after changing the model. These commands do not require a database or execute the declared workflow.

## Contracts

API paths start with `/api/v1`. Every workspace query must enforce membership or a scoped key. Body fields do not assign tenancy. Database composite foreign keys reinforce authorization. Individual signup creates a personal workspace; an organization is a deliberate additional choice.

Runs freeze scenarios. Policies and release decisions are immutable through the API. Missing or unknown outcomes block releases. Human approval requires a different administrator and the `release` action. Model judgments are advisory and never confer authority.

Local workflow-package validation checks the declared contract only. A content digest identifies the package content; it does not prove publisher identity, approval, connector compatibility, or safe execution. Future deployment and action authorization must enforce tenant/environment scope and current permissions independently of the package. Do not route package validation through an existing evaluation runner as an implicit production runtime.

Background assessment jobs use leased claims and retain provider, model, input digest, and source provenance. No credentials or raw provider failures may appear in logs, evidence, or public errors.

## Design

Use the LotusPond design system ([ADR-0016](docs/architecture/0016-adopt-the-lotuspond-design-system.md); source in `design-system/project/`, published to Claude Design) through the documented tokens and reusable components: a light sage frame with an inset white workspace, icon-and-label navigation, a Home setup checklist and a ⌘K palette; sage green actions, tomato only where a person has to decide, legible records, clear states, and restrained typography. Change a colour in `design-system/project/tokens.json` first, then in `web/src/styles/console.css`; `web/src/lib/designTokens.test.ts` holds them together. Avoid simulated activity and fabricated metrics; the only exception is the public platform preview's labelled sample workspace, whose decisions come from the tested policy port in `web/src/lib/policyPreview.ts`. Keep individual and organization flows distinct and preserve accessible mobile forms. Navigation groups pages as Build, Assure, Operate and Administer; Control Room, Connections and Human tasks are tagged Planned and render honest pages from `web/src/lib/roadmap.ts`, which change only when `docs/CAPABILITIES.md` does.

Public pages follow the rules in `DESIGN.md` and the structure in `docs/design-system.md`. Show the product through its real rules and quoted product strings, never invented logos, testimonials, or numbers. Build public pages in the design system (a sage field, off-white sheets, one tomato mark per view and never on sage, the one-thread illustrations), as the signed-out introduction is. Never let motion cost usability, and check visible changes in a browser at 1440px, 768px, and 375px widths with keyboard focus before calling them done.

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

E03 adds the separately installable Linux runtime 0.1.0 and additive schema 13
connection/runtime identity APIs. SDK stays 0.2.0. CLI observations are empty;
production recovery, record, dispatch and trust providers deny/unavailable by
default. Signed command claims never execute business effects. See
`docs/operations/runtime-recovery.md` and
`docs/validation/2026-10-14-connections-fleet.md` for installed-artifact qualification
and its limits. E04/E05 execution/actions/approvals/reconciliation, E08 licensing,
E09 production/customer qualification, Studio/UI and enterprise goals stay open.

## Console Studio for v1 packages (15 October 2026)

The console Studio is the first Studio increment: a drag-and-drop canvas, an
inspector and a browser admission check that mirrors the shared v1 validator,
publishing to the existing v1 registry. It reads and writes only the public
registry API, adds no routes, schema or authority, and composes v1 packages only;
executable-v2 authoring, Control Room and execution remain open. Keep
`web/src/lib/packageContract.ts` in step with `sdk/python/src/lotuspond_sdk/contract.py`;
`web/src/lib/packageContract.test.ts` quotes the SDK source and pins the example digest.
Studio patterns are the synthetic v1 files in `contracts/examples/`; add a pattern there, never
as a browser-only definition (`web/src/lib/studioPatterns.test.ts` pins each CLI digest). Drafts
stay in browser storage; the Studio's version-conflict help only foresees the registry's answer.
