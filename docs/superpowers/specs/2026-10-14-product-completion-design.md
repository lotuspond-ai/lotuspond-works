# Lotuspond product completion and production qualification

Date: 14 October 2026 (Asia/Kolkata). Status: written design and dependency sequence approved by the owner on 14 October 2026 for detailed implementation planning. The full delivery goal remains active. This document is not a production-readiness claim or an authorization to operate production infrastructure.

## Intent and acceptance standard

Deliver one licensable enterprise agent automation product that customers can build with, deploy, operate, govern and improve without undocumented engineering intervention. Preserve the working assurance product and every requirement in [GOAL](../../GOAL.md), [CAPABILITIES](../../CAPABILITIES.md), the [master plan](../../plans/2026-10-13-platform-master-plan.md), [architecture](../../plans/2026-10-13-platform-architecture.md) and [delivery plan](../../plans/2026-10-13-platform-delivery.md). Transformation, implementation, training and managed operations remain separately offered services. Industry breadth must come from reusable, qualified packs rather than product forks.

The owner's requested quality bar means explicit contracts, controlled failure behavior, maintainable boundaries, measurable acceptance and independently reviewable evidence. Company names are a quality aspiration, not a certification. A completed development slice, synthetic connector or successful CI run cannot close requirements for live delivery, availability, customer outcomes or support.

This design uses the existing roadmap's default first topology: a connected, isolated customer installation with a Linux action-runtime pool. Other topologies remain in scope and receive separate qualification. It assumes no authorized production credentials, customer endpoints, cloud changes, outreach or Microsoft work-context access.

## Current evidence and preserved boundaries

The source audit was pinned to registry commit `9c50d098f6d0ca7f9866861b8114c1caccbec743`. Foundation PR #1 was subsequently merged at `18eb85420d1e90e47fb5fb23a060a125180f08ed`. PR #2 was retargeted to main, repaired at `64d263f`, and merged at `a6025f8c2d7c1372c28876d5700d03df3d9faffd` after all ten final CI jobs passed. Those changes do not supply a production runtime.

The implemented foundation includes human tenancy/identity/MFA, scenarios, supervised evaluation runners, release policies and independent approvals, traces/monitoring/incidents, model-gateway budgets, protocol adapters, voice transcript flows, software evaluation tools, a Go collector, Rust evidence verification/signatures, and the assurance console. The v1 package contract and immutable registry add declaration validation and content identity. Neither grants trust or permission to execute.

Maintain one Python API and relational authority, one React console, the existing language-specific components, the preserved recovery archive and existing data. Never repurpose evaluation `Run` as business `Execution`. Keep customer code and credentials outside the shared API. Keep v1 canonical bytes stable, immutable package records readable, and schema upgrades additive. Production database access is outside repository development work.

## Approach and alternatives

Recommended: complete the package tooling boundary, then implement one complete automation journey through identity/fleet, durable execution, action approval and verified outcomes. Add Studio and licensing against these same contracts. Expand packs and topologies only through explicit acceptance gates. This preserves useful code and produces inspectable increments without substituting screens for working behavior.

An engine-first sequence would expose recovery and operating costs earlier but would build against an incomplete public package/action contract; use a bounded engine spike after the executable contract is designed. Parallel development of every subsystem can reduce calendar time with staffed interface owners, but adds integration and consistency costs. It is appropriate only after the shared contracts and dependency gates are settled. A wholesale rewrite is not justified by the inspected code.

## Complete requirement coverage and closure evidence

All rows remain open until their complete evidence exists. A row may be decomposed into smaller reviewed specifications; decomposition does not remove its requirements.

| Workstream | Required delivery and closure evidence |
| --- | --- |
| E01 ownership and baseline | Preserve source/history/provenance, accepted ADRs and existing work; current exact-commit checks; enforceable repository review/protection or an explicitly documented operational control. |
| E02 package/SDK/trust | Independently installable SDK, strict executable typed contracts, stable canonicalization, immutable registry, semantic version diff, signatures/trusted publishers, capability compatibility and unsupported-feature rejection. The next bounded subsystem is specified below; executable semantics require a subsequent versioned contract. |
| E03 connections and fleet | Environment-scoped connections, allowed destinations/operations, credential references resolved at the execution boundary, one-use worker enrollment, expiring service identity, rotation/revocation, signed commands, replay protection, capability pools and negative tenant tests. |
| Knowledge and artifacts | Authorized source ingestion, source ACLs, immutable versions/freshness, citations, deletion propagation, data classifications, tenant-scoped artifact access, encryption, bounded retention and export. |
| E04 durable execution | Reviewed engine choice, command outbox/event inbox, stable admission IDs, deduplication, pinned package/configuration versions, restart/replay, timers, human/external waits, pause/cancel, retries, bounded agent steps, resource/cost limits, queue fairness and backpressure. |
| Triggers and schedules | Authenticated/replay-protected triggers and webhooks; persisted timezone, DST, misfire and occurrence identity; duplicate trigger yields one business execution. |
| E05 actions and human tasks | Durable action intent before effects; exact tenant/execution/version/connector/target/input/policy/expiry binding; independent reviewer assignment/claim/expiry/escalation; fresh authority before dispatch; idempotency, receipts, ambiguous-effect reconciliation and explicitly authorized compensation. |
| Recovery authority | Disconnection blocks new consequential effects; revoked or stale workers are fenced. Restoring an old backup requires a new recovery epoch, downstream reconciliation and current identity/grant validation before writes resume. |
| E06 cases/outcomes | Separate case/execution/attempt state; input-to-version-to-action-to-approval-to-target-read-back traceability; verified outcome rules, explicit uncertainty, exception ownership, business reporting and permission-correct exports. |
| E07 Studio | Shared SDK/compiler representation, constrained supported-node editor, import/export, drafts, typed mappings, errors, version diff, debug/test/publish and reviewable AI authoring. AI drafts cannot expand authority or silently publish. |
| E07 Control Room | Role/environment-aware deployments, version/configuration pinning, production pools, queues, schedules, executions, incidents, human inbox and case timelines; usable loading/error/empty/recovery states. |
| E08 licensing/usage | Signed scoped entitlement, installation/organization/modules/environments/capacity, issuance/renewal/correction/key rotation, idempotent usage ledger, admission enforcement, disclosed grace/expiry and audit. Human waits do not consume active compute; evidence/export/admin/bounded recovery survive expiry. Manual invoicing is sufficient initially. |
| E10a reference packs | Working document/order-intake or equivalent buyer-selected workflow, synthetic second-domain fixtures, supported-use statement, versioned connectors/templates/evaluations/policies/classifications/runbooks and independent outcome acceptance. |
| Broader workflow families | Retain document, knowledge, business transactions, long-running cases, voice/conversation and browser/desktop/legacy work in the qualification register. Each needs supported systems/versions, limits and evidence. Healthcare administrative and clinical intended uses remain separate qualification programs. |
| E10b improvement/reuse | Authorized incident-to-redacted/synthetic-regression conversion with provenance/rights, reviewed fix, independent release approval, deployment and outcome evidence; repeat deployment using the same pack with bounded configuration. |
| E11 enterprise/delegation | Supported OIDC/SAML and provisioning/deprovisioning, session/service revocation, passkeys where specified, time-limited customer-granted partner/service access, distinct platform/customer authority, audited emergency access and independent partner handover. |
| Existing identity assurance | Real verification/recovery/invitation/security-notice delivery, abuse controls, reviewed lost-factor/key-rotation recovery, offline proofing and operator onboarding. Preserve existing membership, session and MFA guarantees. |
| Existing evaluation/model/protocols | Complete selected recovery inventory/parity; real Jev/model/customer HTTP/MCP/A2A evidence, calibrated quality/cost/latency, streaming/task-resumption behavior and external release-consumer receipts. No unconfigured provider yields a pass. |
| Voice and software automation | Real audio/channel/provider tests, diarization/interruption/tool/recording quality and human handoff; representative model/customer sandbox tasks, wider application support, image provenance and separate device/session isolation qualification. |
| Telemetry/evidence | Preserve bounded authenticated ingestion, spool/replay and integrity; address required logs/metrics/gRPC and span events/links explicitly before claiming support; test disk/load/failure behavior and key custody/rotation. |
| Product experience/accessibility | Existing design tokens, responsive desktop/tablet/375px journeys, keyboard and error focus, accessible validation; Chromium checks plus Safari/Firefox, screen readers and Windows forced colours; real customer usability. |
| E09 supply chain | Reproducible approved-source builds, pinned digests/locks, SBOM, dependency/license/container/secret checks, provenance, signed distribution with independently trusted keys, and promotion of identical artifacts. |
| E09 deployment/data | Qualified IaC/configuration, ingress/internal TLS, least-privilege database/Redis/network/secret access, persistent storage, bounded resources; current-version fresh install and additive upgrade/restore tests. Never run the empty-database initializer as an upgrade. |
| E09 operations | Authenticated alert receipt/deduplication/routing, API/dependency/queue/worker/outcome/cost/backup signals, incident response, realistic load/soak/failure tests, measured SLO/RPO/RTO, backup retention/key custody and staffed escalation. |
| Deployment matrix | Separately qualify managed connected, hosted control-plane/customer-runtime, customer-managed/BYOC and fully offline operation; signed clean-host transfer, local identity/models/clock/TLS, no-egress, licensing, upgrades and recovery. Shared hostile multi-tenant execution needs its own isolation program. |
| Documentation/services/commercial | Versioned API/SDK/connector/operator/customer guides, hosted docs under its explicit publication scope, independent onboarding/export/handover/training, supported-version matrix, readiness/acceptance/service templates, support responsibility and actual buyer outcomes. No outreach or commercial commitment is implied by this design. |

## Runtime architecture and end-to-end acceptance

The control plane owns tenant authority, package/deployment admission, product records and business outcomes. A narrow orchestration adapter owns start/signal/cancel/history interactions with the selected durable engine. The engine owns progression, waits and retries. A separately isolated worker supervises bounded agent activities and policy-enforcing connector execution. Customer credentials stay at that boundary. Only registered, permitted connectors may perform effects.

The first complete journey is create/import a package → validate and test → publish → approve and deploy an immutable package/configuration → admit an idempotent trigger → read permitted data → run a bounded agent proposal → independently approve the exact action → reauthorize and dispatch → read back the target result → record/export the verified outcome. Both SDK and Studio must produce and operate the same representation.

Durably record intent and authority before an external action. Distinguish target acknowledgement, verified effect and business outcome. A timeout after dispatch enters reconciliation, never blind retry. Model/tool/document content cannot authorize an action. Customer-visible failures use bounded structured errors, preserve evidence, and disclose uncertainty without exposing secrets or private raw payloads.

Required failure demonstrations include duplicate admission, worker crash during wait and around effect dispatch, changed/expired approvals, cross-tenant references, provider unavailability, disconnected worker plus revocation, pause/cancel, package upgrade with old work pinned, license expiry, evidence destination outage, and backup restoration from before a successful write or later access revocation. Use independently verified localhost databases and synthetic destinations during development.

## Next subsystem: standalone package SDK, diff and detached trust tooling

### Scope and layout

Add an installable Python SDK under `sdk/python/`, compatible with the project's supported Python versions. The SDK owns the pure package contract and bounded decoder; `backend/lotuspond/package_contract.py` and `package_input.py` retain compatibility imports. API/database/web/runtime dependencies must not be needed to install and use local package tooling. Use the existing Pydantic and reviewed cryptography libraries rather than custom cryptographic primitives.

Public functions cover load, validate, canonicalize, digest, diff, sign, verify and check declared compatibility. CLI commands expose the same functions with machine-readable bounded errors and documented exit statuses. Preserve `npm run package:validate`, schema export/parity, existing Python imports, registry canonical bytes and existing validator/canonicalizer identifiers when semantics do not change. The initial SDK is source/wheel tested; public package publication is a distinct release action.

### Content and differences

Freeze v1 sorted-key UTF-8 canonicalization, explicit normalized defaults and significant array order. Validate both inputs before diffing. Return both digests and deterministic changes by stable declaration IDs/JSON paths, classifying graph, model, instructions, limits, connector and secret-reference changes. Explicitly report list reordering. Do not label arbitrary instruction or graph changes behaviorally compatible. Equal normalized content produces equal digests and an empty diff. Avoid returning private free-text values by default; provide an explicit local detail mode.

### Detached signatures and trust

Use Ed25519 with a distinct Lotuspond workflow-package context, versioned framing, exact canonical bytes and bound schema/canonicalization metadata. Never reuse evidence-manifest signature context. Envelopes identify the algorithm, format version and public-key fingerprint. Verification requires an independently supplied trusted-key set with allowed/revoked state; an envelope's embedded identifier grants no trust. A valid signature does not grant tenant authority, deployment admission or execution permission.

Local key generation writes owner-only private material and refuses existing output paths. Signing refuses unsafe private-key permissions and avoids printing private keys. Validation, errors, diagnostics and tests must never expose secret bytes. Publish key lifecycle and rotation requirements; hosted issuer custody and registry trust-policy admission remain a separate reviewed integration.

### Declared compatibility

Define a strict versioned target capability profile with supported schema/canonicalization identifiers, node kinds, connector operations/versions and platform requirements. Unknown or missing requirements return incompatible/unsupported with bounded reasons. Separate contract validity, trusted signature, declared compatibility and authority in the report. With no production runtime yet, profiles are explicitly synthetic declarations and cannot produce a production-supported claim. Do not silently add execution semantics to v1.

### Verification and exit criteria

Require unchanged golden canonical bytes/digests/default/Unicode behavior; bounded decoder and CLI parity; deterministic semantic diff/reorder fixtures; independently sourced Ed25519 vectors and cross-implementation signatures; changed-bytes/wrong-context/wrong-key/revoked-key/unknown-algorithm rejection; safe key/output permissions; compatibility refusal cases; a built wheel installed in a fresh environment without API/database packages; schema parity; full existing application checks and independent code review. Document actual commands, commits and limitations.

This subsystem closes the SDK/diff/local signature tooling portion of E02. It does not close executable semantics, trusted deployment admission or real runtime compatibility. The subsequent specification introduces executable package v2 with typed input/output mappings, branch/join semantics, node/action/event contracts, retries/compensation, package limits, approval bindings, dependencies and outcome verification. V1 remains readable and valid as a declaration-only format.

## Dependency sequence and engineering controls

1. Complete PR integration and repair verified defects in the existing product/deployment paths.
2. Implement and review the SDK/diff/trust subsystem above; then specify executable v2 and runtime action/event contracts.
3. Run the durable-engine spike with explicit restart/wait/deduplication/unknown-write/version-upgrade acceptance. Record adoption, operational and redistribution tradeoffs before installing the chosen engine into the product.
4. Implement E03, then E04/E05/E06 as the complete journey; implement licensing semantics early enough to constrain admission. Develop a synthetic reference connector/pack alongside it.
5. Deliver Studio/Control Room against those contracts, then independent customer workflow acceptance and E09 artifact/topology/operations qualification.
6. Complete E10b/E11 and each remaining industry/topology/assurance gap without relabeling unqualified work as supported.

Each implementation plan lists acceptance tests, files, migration/rollback compatibility, documentation and independent review. Immutable artifacts and data-preserving migration tests are required for schema changes. Design review, code review, local verification, remote CI, artifact qualification, live acceptance and operational observation remain distinct evidence states. Track regressions and unsuccessful runs, not only successful reruns.

## Decisions and evidence required outside local development

Before external qualification, record the first customer's bounded workflow, connector/provider versions, permitted actions/data, independent acceptance owner, installation placement/jurisdiction/model egress, authorized infrastructure/domain scope, license/capacity/grace terms, support coverage, incident owner and key/backup custodians. Local implementation can continue independently of these decisions where contracts and synthetic tests suffice.

Use the roadmap's workload targets as hypotheses: 5 admissions/second, 25 active steps, 10,000 waiting cases, 60-minute steady load and a 2× five-minute burst; p95 admission below one second and runnable queue age below 30 seconds with bounded backlog recovery. Validate the proposed 15-minute RPO/four-hour RTO through actual restore drills. A proposed 99.9% availability objective requires service observation, including the planned 30-day window; it cannot be certified during this coding session.

## Review handoff

The owner approved this written design and dependency sequence on 14 October 2026: “Approve the written design and sequence.” The next review artifact is the [SDK implementation plan](../plans/2026-10-14-package-sdk.md); the owner subsequently approved it and selected subagent-driven execution with review after each task. Existing authorized PR integration, verification and concrete defect repair remain independently actionable. No unimplemented row is considered complete by approval of this document.
