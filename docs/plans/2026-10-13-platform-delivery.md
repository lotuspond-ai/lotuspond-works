# Lotuspond repositories, implementation and delivery plan

Date: 13 October 2026. Status: proposed execution plan; no GitHub mutations, deployment cutovers or production changes have been performed. Read the [master plan](2026-10-13-platform-master-plan.md) and [architecture](2026-10-13-platform-architecture.md) first.

## 1. Verified starting point

- Current owning checkout: `/Users/srujanpapaiahgari/Downloads/LotusPond`.
- Git is initialized on `main`, with no commits and no remote. At inspection: 329 staged paths, 107 paths with unstaged changes and one untracked file. Some staged files therefore differ from their current working copies.
- GitHub account `srujanpapaiah` has organization `lotuspond-ai`. A fresh read-only inventory returned the nine private, unarchived repositories listed below. Their deployment consumers have not been revalidated.
- The current implementation has a Python-owned API/model, React console, Go collector, Rust evidence tools and Node execution clients. Evaluation/release/monitoring foundations are substantial. Production business orchestration and full commercial licensing are proposed additions.
- Existing CI covers the application, isolated PostgreSQL, runner smoke, Go race checks, Rust, protocol/voice tests, restore, packaging and an offline installation drill. These checks were inspected, not rerun for this planning change.
- Rust evidence-manifest signing exists. The current service-bundle installation document explicitly says its distribution has no signature, SBOM or vulnerability attestation. Those are different capabilities.

Current source and `docs/CAPABILITIES.md` take precedence over stale gap lists in `docs/research/deployment-and-pilots.md`. For example, that older research still describes protobuf, spool and signing gaps that subsequent implementation addressed. Update historical references explicitly rather than discarding their useful pilot methodology.

## 2. Repository topology

Keep source together where changes share contracts, tests and ownership. Separate repositories where access, customer confidentiality or deployment authority differ.

```text
lotuspond-ai/
├── lotuspond                         proposed canonical private product repo
│   ├── web/                          existing console; add Studio and operations views
│   ├── backend/                      existing Python control plane and schema authority
│   ├── contracts/                    new versioned package/API/event schemas
│   ├── sdk/                          new authoring, validation and client SDK sources
│   ├── runtime/                      new production execution components
│   ├── connectors/                   new reusable qualified business integrations
│   ├── solution-packs/               new reusable workflows and synthetic tests
│   ├── collector/                    existing Go telemetry edge
│   ├── evidence-core/                existing Rust integrity tools
│   ├── scripts/                      existing evaluation/CLI/local commands
│   ├── protocol-adapters/            existing HTTP/MCP/A2A foundations
│   ├── voice-bridge/                 existing transcription integration
│   ├── sandbox/                      existing isolated software evaluation tools
│   ├── deploy/                       portable product packaging, not customer secrets
│   └── docs/ + docs-site/            engineering and customer documentation
├── lotuspond-operations              create for first managed environment
│   └── infrastructure code, environment references and operational runbooks
└── delivery-<customer-code>           only when an engagement needs private source
    └── customer-specific adapters, configuration schemas, tests and overlays
```

These are proposed names and additions; the repositories/directories have not been created by this plan except the planning documents themselves. Avoid reorganizing existing working files merely to match an ideal tree.

The product monorepo emits separately identified control-plane, runtime, collector, SDK and solution-pack artifacts. Independent deployment does not require separate source repositories. Publish generated SDK packages from this source before considering dedicated public repositories.

A future `lotuspond-solutions` repository is justified only when pack contributors, IP/access boundaries or release ownership differ enough to outweigh cross-repository coordination. Do not create a repository for every language, screen or industry. Customer implementations depend on pinned platform artifacts rather than permanent platform forks.

Infrastructure state, credentials, private customer records, production database snapshots and private trace exports never belong in these Git repositories. Use governed state/secrets/data stores. Customer-owned hosting of delivery source is an option under the engagement agreement.

### Existing repository disposition

| Repository                                                                                   | Planned treatment                                                                         |
| -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| [lotuspond-main-client](https://github.com/lotuspond-ai/lotuspond-main-client)               | Preserve legacy source/history; review useful behavior and deployment consumers           |
| [lotuspond-main-server](https://github.com/lotuspond-ai/lotuspond-main-server)               | Preserve legacy source/history; avoid reviving a competing primary API                    |
| [lotuspond-ag2-client](https://github.com/lotuspond-ai/lotuspond-ag2-client)                 | Preserve as recovery/reference material pending dependency review                         |
| [lotuspond-agent-client](https://github.com/lotuspond-ai/lotuspond-agent-client)             | Review reusable behavior and consumers before retirement                                  |
| [lotuspond-admin-panel](https://github.com/lotuspond-ai/lotuspond-admin-panel)               | Preserve; new administration stays in the unified product                                 |
| [lotuspond-schemas-repository](https://github.com/lotuspond-ai/lotuspond-schemas-repository) | Preserve historical schemas; current Python model remains authoritative                   |
| [lotuspond-gym-runtime](https://github.com/lotuspond-ai/lotuspond-gym-runtime)               | Audit runtime/package consumers before deciding on consolidation or independent retention |
| [aarogyaai-main-client](https://github.com/lotuspond-ai/aarogyaai-main-client)               | Keep separate; potential healthcare reuse requires explicit product/IP/data review        |
| [aarogyaai-main-server](https://github.com/lotuspond-ai/aarogyaai-main-server)               | Keep separate; do not infer that it is a qualified Lotuspond healthcare pack              |

No deletion is needed. Archive a legacy repository only after consumers, recovery requirements and useful source have been addressed under a separately authorized retirement scope.

### Safe transition sequence

1. Inventory branches, ownership, source licenses, dependencies, CI, published packages/images and actual deployment source links.
2. Create a recovery map: source repository/commit/path/license → destination module → justification → regression evidence. Preserve the existing archive; import no secrets, environment files, customer data, obsolete affiliations or previous history wholesale.
3. Review the current index and working tree independently. Establish a baseline test report, known failures and secret/generated-file exclusions. Do not assume everything currently staged is publishable.
4. In an explicitly authorized publication step, create the canonical private repository, curated initial commit, protected `main`, ownership rules and required checks. Use short-lived `codex/` branches by default for agent-created branches. Never reset unrelated work or force-push old history into a different repository.
5. Migrate consumers one at a time to pinned new artifacts with an owner, acceptance evidence and rollback plan. Remote code publication, documentation publication and live deployment are separate milestones.
6. Verify no required consumer remains before proposing archive status. Preserve AarogyaAI's independent boundary.

## 3. Ownership and versioning

| Contract/artifact       | Accountable role                      | Required rule                                                                  |
| ----------------------- | ------------------------------------- | ------------------------------------------------------------------------------ |
| Public API and events   | Platform lead                         | Versioned schemas, compatibility checks and generated clients                  |
| Relational model        | Backend lead                          | One schema authority; reviewed, data-preserving migration plan                 |
| Workflow/package format | Runtime lead                          | Immutable released definitions; explicit runtime compatibility                 |
| Connector               | Integration owner                     | Supported operations/auth/API version, idempotency/reconciliation and tests    |
| Industry/workflow pack  | Domain and delivery owner             | Supported-use statement, acceptance suite and compatibility constraints        |
| Product release         | Release owner                         | Signed manifest pins source, images, packages, contracts and migration version |
| Customer deployment     | Customer approver plus delivery owner | Tested configuration, deployment receipt and operating handover                |
| Commercial license      | Product/commercial owner              | Clear units, terms, entitlement behavior and supported configurations          |
| Security/support        | Named responsible owner               | Access review, incident process, key custody and staffed coverage              |

Small teams may combine roles; release acceptance and consequential customer approval still need appropriate separation. Use CODEOWNERS and review paths reflecting these responsibilities. Documentation ownership is part of every module, not a final handoff task.

## 4. Phased program and dependencies

Planning ranges assume 5–8 core contributors, stable scope, one initial runtime topology, accessible design partners and reuse of the existing core. These are estimates, not deadlines. A founder-only effort must sequence fewer capabilities and expect longer delivery. Additional clinical/domain experts and customer integration effort are outside core engineering capacity.

| Phase                               | Scope and dependencies                                                                                                                                     | Exit evidence                                                                                                  | Indicative planning range                         |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| A: Product and engineering baseline | This plan; buyer/process interviews; package/action contracts; current-code baseline; engine spike; repository publication preparation                     | Named first design partners, architecture decisions, supported scope, test baseline and acceptance plans       | 4–8 weeks                                         |
| B: Complete platform slice          | Package registry/SDK; minimal Studio; production execution; connections; human tasks; action/outcome records; server entitlements; identity/fleet boundary | End-to-end demonstrator with failure tests; independently repeatable installation; explicit remaining blockers | Next 8–12 weeks                                   |
| C: Paid bounded production          | One qualified deployment mode; signed artifacts; real connector/provider validation; recovery/upgrade; customer acceptance and support                     | Paying licensed pilot, verified outcomes, staffable support, actual deployment evidence                        | Next 4–12 weeks, partly overlapping late B        |
| D: Repeatable enterprise product    | SSO/provisioning; delegated partners; additional deployment qualification; reusable packs in a second industry                                             | Repeated deployments without product forks; measured delivery efficiency; compatibility and SLO evidence       | Additional 6–12+ months as demand/staffing permit |
| E: Ecosystem and wider execution    | Broader Studio, third-party packs, voice/desktop workers, shared hosted isolation where justified                                                          | Qualification for each added trust boundary and supported workflow family                                      | Demand- and evidence-driven                       |

Broad commercial GA follows operational evidence and a supportable matrix; completion of B does not authorize an “any workflow” production claim. Progress toward multiple industries during C/D by testing shared primitives with representative workflows. Do not maintain separate product forks as a shortcut.

### Dependency-ordered epics

| ID   | Epic                                  | Depends on                           | Testable acceptance                                                                                                            |
| ---- | ------------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| E01  | Baseline and product ADR              | None                                 | New scope, source inventory, exact test baseline and architecture decisions recorded                                           |
| E02  | Package contract and SDK              | E01                                  | Typed graph validation, immutable digest, version diff, signatures and unsupported-capability rejection                        |
| E03  | Identity, connections and fleet       | E01/E02                              | Tenant/environment isolation, worker enrollment/revocation and secret-reference resolution proven                              |
| E04  | Durable execution                     | E02/E03                              | Restart, timers, duplicate admission, cancellation and pinned-version behavior demonstrated                                    |
| E05  | Actions and human tasks               | E03/E04                              | Parameter changes/expiry invalidate approval; denied action cannot execute; uncertain effects reconcile                        |
| E06  | Cases and verified outcomes           | E04/E05                              | Input → deployed version → action/approval → target read-back → outcome traceability                                           |
| E07  | Minimal Studio and Control Room       | E02; integrates E03–E06              | Customer builds/configures, tests, deploys and operates a supported workflow via documented UI/SDK                             |
| E08  | Licensing and usage                   | E01; enforced in E03/E04             | Idempotent metering, admission enforcement, renewal/grace and evidence-preserving expiry tested                                |
| E10a | Reference pack and acceptance suite   | E02/E03; developed alongside E04–E06 | One working pack, synthetic connector fixtures and buyer-reviewed outcomes are available as inputs to production qualification |
| E09  | Release, installation and operations  | E03–E08/E10a                         | Exact-artifact install/upgrade/restore, authenticated incident delivery, observed SLOs and support handover                    |
| E10b | Repeatable pack delivery and feedback | E06/E09                              | Incident becomes reviewed test; new version fixes it; repeat customer uses same pack with bounded configuration                |
| E11  | Enterprise/partner expansion          | E09/E10b                             | Federation lifecycle, delegated access, customer-managed qualification and independent partner install                         |

E08 initially uses manually issued commercial entitlements and invoices; payment collection automation is not required for the first licensed customer. E07's first visual editor is constrained to supported nodes; a broad no-code designer is a later investment.

## 5. First two implementation sprints

Assume two-week sprints after staffing and the initial working agreement are established. External customer access is a dependency, not guaranteed available time.

| Work item                        | Owner role         | Deliverable and completion test                                                                                                               |
| -------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Establish current baseline       | Engineering/QA     | Curated source inventory and test report; no existing dirty work lost; named failures separated from new failures                             |
| Draft replacement platform ADR   | Architect/product  | Updated product boundaries, ownership and execution/isolation decisions linked to existing ADRs; planned versus implemented remains explicit  |
| Define package/action/event v1   | Runtime/platform   | JSON schemas, synthetic examples and acceptance tests; a changed approved action has a different digest                                       |
| Durable-engine spike             | Runtime/SRE        | Demonstrate restart during human wait, duplicate command, unknown write and version upgrade; record operational/license/dependency trade-offs |
| Design core console journeys     | Product/frontend   | Reviewed create → test → publish → run → approve → inspect flow, accessible states and role permissions                                       |
| Design commercial contract       | Product/commercial | License units, grace behavior, support boundary and pilot acceptance template                                                                 |
| Select reference implementations | Solutions/domain   | At least one real buyer workflow and one synthetic second-domain case exercise shared capabilities without industry-specific backend branches |

Sprint 2 begins the immutable package registry, SDK validator, additive data-model design, outbox/engine boundary and one synthetic connector. Do not simultaneously rewrite the console, split all repositories, migrate databases and launch all deployment modes.

## 6. Release engineering

Extend current checks rather than replacing them:

1. PR validation: `npm run check`; required isolated database integration; existing Go/Rust/protocol/voice/packaging checks; schema snapshot parity; contract compatibility; dependency/license/secret checks; meaningful failure tests.
2. UI validation: required desktop/tablet/375px and keyboard-focus checks for visible changes, plus accessible error/empty/wait/approval states. Update the API changelog for user-visible changes.
3. Build once from approved source with locked dependencies and pinned image digests. Produce images, SDKs, packs, migration bundle, SBOM and a release manifest.
4. Sign product distribution using controlled publisher keys/identity. Include build provenance and scan results. Existing evidence signatures are reusable primitives, not a completed supply-chain system.
5. Test those exact artifacts on fresh install and realistic synthetic upgrade fixtures, including supported control-plane/runtime versions. Reject unsupported packages before execution.
6. Promote the same artifact digests through staging and production. Shadow/canary representative customer work under explicit acceptance and rollback criteria.
7. Record actual installed digests/configuration. Application rollback is allowed only across compatible schemas; otherwise use a reviewed forward fix or restore plan. No destructive automatic schema downgrade.
8. Offline delivery verifies signed manifests, trusted keys, architecture, dependencies, license behavior, no-egress operation, upgrade and customer acceptance. Signature verification requires independently trusted keys and documented rotation/revocation.

Test-only databases remain independently verified localhost instances. No production database may be read, introspected, reset or migrated under this repository's development instructions. Any later production deployment/migration needs its separately reviewed operational scope; this plan is not that authorization.

## 7. First production acceptance matrix

| Scenario                                                            | Required result                                                                                                                   |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Same trigger delivered twice                                        | One admitted business execution; original identity is returned                                                                    |
| Worker crashes before/after external effect                         | Resume safely; ambiguous effect reconciles; no blind duplicate                                                                    |
| Approval refers to old parameters                                   | Execution refused until the exact new action is approved                                                                          |
| Tenant A references tenant B's connection/artifact                  | Server and runtime deny access and avoid exposing private existence/details                                                       |
| Model/provider unavailable                                          | Declared fallback/escalation and bounded cost; no fabricated success                                                              |
| Credential/runtime grant revoked                                    | No new work or effects authorized under revoked authority                                                                         |
| Customer runtime disconnects while access is revoked                | New consequential actions remain blocked until reconnection and fresh authorization; in-flight uncertainty reconciles             |
| Execution paused/cancelled                                          | Defined future work stops; completed effects and compensation limits visible                                                      |
| Package/configuration upgraded                                      | Existing work remains pinned; new work uses approved compatible versions                                                          |
| License expires or meter temporarily fails                          | Documented grace/continuity behavior; evidence and recovery remain accessible                                                     |
| Data restoration or regional dependency outage                      | Measured recovery within the qualified topology's stated target; no contradictory business state                                  |
| Restore a backup from before an external write or access revocation | Workers remain fenced until current target state and authorization are reconciled; no repeated write or restored stale permission |
| Evidence/alert destination unavailable                              | Bounded retention/backpressure/retry; visible uncertainty and incident routing                                                    |
| Customer export/handover                                            | Versions, allowed data and outcomes can be retrieved under agreed permissions                                                     |

A zero count of unauthorized or duplicate actions in a finite test set is an acceptance observation, not proof that such failures are impossible. Report sample size, workload, missing evidence and uncertainty.

## 8. Services delivery system

Use a standard engagement sequence: discovery → readiness → design → build → evaluate → shadow → approved rollout → operate → improve. Every stage produces an artifact and named acceptance owner.

- Discovery/readiness: process map, baseline, data/API inventory, AI suitability and economic hypothesis.
- Design: allowed actions, information boundaries, roles, integration contracts, exception model and acceptance suite.
- Build: versioned customer package/configuration, test fixtures, operator procedures and provenance.
- Evaluate/shadow: failure set, outcome evidence, reviewer disagreement and actual cost/time measurements.
- Rollout: approved deployment, recovery exercise, training, responsibility matrix and support activation.
- Operate/improve: incident ownership, change budget, tested releases, customer reports and renewal evidence.

Commercial templates need statement of work, acceptance criteria, change process, support scope, confidentiality/data terms and IP allocation reviewed for the relevant customer/jurisdiction. Customer-specific material stays in its authorized boundary. Frameworks and generic code may be reusable; private business data never becomes a shared test corpus by default.

Use ordinary project/ticket tooling for internal delivery. The product carries the customer-visible technical truth: package/version, deployment, action, approval, outcome and incident. Do not duplicate an entire services-management system in Lotuspond.

## 9. Risks, decisions and stop/revisit triggers

| Risk or choice                                    | Mitigation or decision trigger                                                                                                        |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Cross-industry scope overwhelms a small team      | Qualify horizontal capabilities and packs sequentially; keep the broad product vision while limiting concurrent launches              |
| Services become bespoke development               | Track reuse and delivery hours; after three comparable deployments, require evidence that core pack/configuration reuse is increasing |
| Orchestrator adds operational burden              | Time-box the spike; adopt only with recoverable install/upgrade and clear state ownership                                             |
| Studio diverges from SDK/runtime                  | One contract/compiler; round-trip/diff tests; no separate execution semantics                                                         |
| Claimed support outruns actual qualification      | Public capability register and supported integration/version matrix tied to evidence                                                  |
| Cloud/on-prem/offline matrix grows too quickly    | One initial topology; qualify each additional mode with its own install, upgrade, recovery and support evidence                       |
| Billing interrupts important work                 | Explicit admission/grace policy; preserve evidence and bounded recovery; critical workflows need agreed continuity terms              |
| Product and delivery roles approve their own work | Independent customer/domain acceptance and scoped delegation                                                                          |
| Healthcare pack implies clinical readiness        | Separate administrative and clinical intended uses; require appropriate experts and validation before claims                          |
| Platform fragmentation across old repos           | One authoritative new core; preserve old consumers until individually migrated                                                        |

Review at each phase gate: paying demand, supported scope, technical evidence, customer outcomes, onboarding effort, gross-margin drivers, incident burden and staffed support. If the first workflows cannot produce trustworthy outcome evidence, narrow the implementation and fix that foundation before adding a marketplace or new industries.

## 10. Documentation and authorization boundaries

At implementation kickoff, add a platform ADR describing the expanded scope and update `CLAUDE.md`, `AGENTS.md`, `docs/GOAL.md`, `docs/CAPABILITIES.md`, relevant security/design docs and public copy consistently. Preserve useful onboarding and recovery instructions. Keep historical decisions with clear supersession notes. Planned capability must not become an implemented claim through a documentation edit.

This planning work creates only the three files under `docs/plans/`. It does not create or archive repositories, stage/commit/push code, configure hosting, change cloud resources or operate databases. Those execution steps must follow their applicable scope and authorization. No test suite pass or deployment outcome is claimed by the existence of this plan.
