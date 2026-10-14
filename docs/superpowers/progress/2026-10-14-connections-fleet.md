# Connections and runtime identity implementation evidence

The approved [E03 plan](../plans/2026-10-14-connections-fleet.md) is being implemented on `codex/connections-fleet`, separately from the preserved design branch. Completion of this foundation does not establish production agent execution or production qualification.

## Task 1 — strict protocol and runtime distribution

Implementation `1372dc187047fe2bf4fcc30657f8cdb39e4fcbf0` and correction `96163e01a603fd1db9e60e2fdffcf49c41f839a1` passed independent specification and code-quality reviews after one correction round, with no open findings. The corrections reject native adapter JSON parsing, per-call relaxation of unknown-field checks, and malformed existing secret wrappers. Supported JSON callers use the bounded decoder followed by Python validation or the bounded class entry point.

- Runtime/protocol and external direct-wheel/sdist-wheel tests: **313 passed, zero skips** on each of Python **3.13.13** and **3.14.5**, at the corrected source. Installed consumers use `python -I` outside the checkout and independently built SDK artifacts.
- Both interpreter runs passed runtime/backend Ruff and three backend SDK compatibility tests. Frozen backend installation and dependency compatibility passed. SDK source, public contracts and canonical fixtures remain unchanged.
- The earlier full root check passed with the existing local process-reaping and serial Node-test harness: Node 105, backend 300 plus 97 subtests, SDK 1,472 plus 68 subtests, and runtime 165. Backend had 39 database-related skips. The raw root run had two runner process-cleanup failures. This broader run preceded the correction and is not fresh corrected-head evidence; Task 9 requires full final checks.
- Exact source hashes and retained artifact hashes were independently compared by both closure reviewers. Runtime direct and rebuilt wheel SHA256: `a7b99eb134893d5a7e8c27055e5077f2900593b1640439c103548e9d99662ead`; runtime sdist: `8dc826c0417e57d106b54c8c92504aac205dc517156066bd14be6748ed60eddd`; SDK wheel: `ed5aeaa0fd4cc417eaa4a9ba69627e5dccb7c9e000e4716397a4d2ea5e216118`.

Public Pydantic deferred schema construction preserves inert imports; mapped IPv6 protocol text uses stable hexadecimal rendering across the supported interpreters. These choices have regression coverage. Actual destination enforcement, secure persistence, route privacy controls, database integrity/races, the complete installed journey, API-image verification and hosted final-head CI remain with their subsequent tasks.

Detailed red/green logs, artifact receipts, task reports and independent review reports are preserved in the plan's ignored local SDD workspace. They are not shipped package contents. No production database, deployment or additional merge occurred.
