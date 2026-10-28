# Lotuspond workspace instructions

## Product and ownership

- Lotuspond is the single product: a licensable enterprise agent automation platform with separately offered transformation, implementation, training, and managed services across industries. Studio, Control Room, and execution are capabilities of this product, not separate brands. Releases, Scenarios, Production Evidence, and Governance remain its implemented assurance workflows.
- This root is the owning repository. Resolve its branch and dirty state before editing or publishing.
- Use `CLAUDE.md`, `docs/architecture/0009-agent-automation-platform.md`, and `docs/CAPABILITIES.md` for the current scope and implementation boundaries. ADR-0002 and later assurance decisions retain their applicable technical contracts; the October platform plans describe the expansion, not completed functionality.
- Keep workflow-package v1 tooling separate from execution: the standalone SDK owns strict validation, canonical content, local differences, detached signatures against independent trust, and exact synthetic capability checks. Local validation and a digest do not establish publisher trust; signatures and synthetic declarations do not authorize, admit, or run a package. The registry adds tenant-scoped immutable storage and admission-validator metadata; it grants no execution authority. The console Studio composes, checks and publishes v1 packages through that registry and nothing more; its browser validator is a port that must stay in step with the SDK, and its starting patterns are the synthetic files in `contracts/examples/`, never browser-only definitions. Production runtime, Studio authoring of executable-v2 definitions, Control Room, hosted publisher trust/admission, commercial licensing, and qualified industry packs remain planned until their own evidence is recorded.
- The current product goal authorizes selective recovery of archived agent-testing implementation. Restore useful behavior under Lotuspond naming, retain provenance, and preserve the archive. Do not restore unrelated products, obsolete affiliations, environment files, or previous Git history.
- Preserve unrelated user changes. Never commit environment files, credentials, dependencies, local database files, or generated build output.

## Personal workflow

- Teams, Outlook, SharePoint, OneDrive, and other Microsoft work-context sources are excluded. Do not connect them or retrieve their context indirectly.
- Use Asia/Kolkata for scheduled briefs. Keep updates concise and distinguish local tests, Git publication, deployment, and observed hosted behavior.
- Honor the task's configured reasoning. Do not claim to change it without verifying the setting.

## Implementation and validation

- TypeScript uses strict checking. Keep imports first and use async/await.
- UI: `web/`. Primary API and relational model: `backend/`. Go telemetry collector: `collector/`. Rust artifact verification: `evidence-core/`. Node execution clients: `scripts/`. Live transcription: `voice-bridge/`. Isolated software tasks: `sandbox/`.
- Read `SECURITY.md` before authentication, authorization, data access, or outbound HTTP changes.
- Before shipping, run `npm run check`. Run the isolated database integration test for API, policy, authorization, or schema changes.
- Update the API changelog for user-visible changes. The client reads it from `/api/v1/changelog`.
- Do not describe registered integrations as verified or locally tested functionality as production ready.
- Interface work follows `DESIGN.md` and `docs/design-system.md`. Public copy must trace to `docs/CAPABILITIES.md`; verify visible changes in a browser at desktop, tablet, and 375px widths, including keyboard focus.

## Database safety

- Never read, modify, reset, migrate, or introspect a production database.
- Use only an independently verified localhost development or test database. Do not reuse environment configuration from outside this project.
- `npm run db:init` is a one-time, empty-database initializer. It must refuse existing tables.
- Do not drop, rename, reset, or replace existing user data. Schema evolution needs a reviewed, data-preserving plan.
- Changes to cloud resources, hosted domains, databases, deployment consumers, or remote repository history require a separate explicit scope and authorization.

## AWS work

- Prefer the AWS MCP server and a relevant AWS skill. Use profile `lotuspond-aws`, workload region `ap-south-2`, and toolkit endpoint region `us-east-1` unless instructed otherwise.
- Prefer infrastructure-as-code. Inventory live resources before changing them.
- Load the AWS secrets-manager skill before AWS secret tasks; resolve secrets at runtime with `asm-exec`. Never return secret values into conversation context.

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
