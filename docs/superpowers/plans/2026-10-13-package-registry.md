# Immutable workflow package registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Authenticated developers publish fully validated v1 packages into workspace projects and retrieve immutable canonical bytes and admission metadata.

**Architecture:** A dedicated Python router shares bounded decoding and safe errors with the CLI. Scoped relational version and idempotency records use composite tenant foreign keys and transactional uniqueness; manifests bypass generic evidence redaction. Reads use stored bytes without validation or canonicalization.

**Tech Stack:** Existing FastAPI, SQLAlchemy, PostgreSQL, Pydantic; no dependencies or runtime infrastructure added.

**Spec:** The reviewed cloud implementation brief (2026-10-13), ADR-0009 and the approved platform plans. The brief explicitly authorizes autonomous implementation and independent review; native execution with independent plan and final code review is used.

## Global Constraints

- Only lotuspond-ai/lotuspond on codex/package-registry, based on c1da02edcb3fb4db57f63a8e403db8e379cf5a2a; foundation PR #1 remains separate.
- Registry admission is neither trust, release approval, runtime compatibility nor execution authority.
- No deployment, production database access, Microsoft context, connectors, signatures, UI or production Execution.
- Reuse require("read")/require("write") unchanged; tenancy always comes from authentication.
- Never redact package content, revalidate old content on read, or expose content in audit/list/error output.
- Only independently verified localhost PostgreSQL development/test databases; never initialize existing data.

## Review Focus

1. Competing requests with the same key but different projects must conflict even if content matches.
2. Same-version replays must compare canonical bytes as well as digest and preserve the original admission revision.
3. Malformed/chunked requests must be bounded before JSON parsing, including absent or false Content-Length.
4. Cursor timestamp ties must neither duplicate nor omit items; cursors must be bounded and project/workspace bound.
5. Older migrations with newest-model fixture metadata must remove registry tables in rewind helpers; partial registry state must roll back the entire chain.

## Reviewed v11-to-v12 additive migration design

Add workflow_package_versions and package_publication_requests only. Versions contain server UUID/time, workspace/project, package_id/version/schema_version, validator_revision, canonicalization, SHA-256, canonical bytes/byte length and publishing actor identifier/type. Use unique (workspace_id, project_id, package_id, version), scoped(workspace_id,id), within("projects","project_id"). Idempotency keys are optional, printable ASCII 1–128 characters, unique within workspace; store project+canonical-content request hash and within("workflow_package_versions","package_version_id") result pointer. No global content deduplication and no update/delete API.

Migration retains advisory transaction lock 736183 and existing localhost/name guards. Accept version 11; explicitly set current=11 after the v10 step. Reject either registry table already present before creating both in dependency order, then update schema_version to 12 in the same transaction. Any error rolls back all DDL and version updates. Existing rows/columns are untouched. Fresh empty initialization includes both new tables. Second migration refuses. Existing v2–v10 chains pass through v11 to v12. PostgreSQL tests use isolated random schemas only. The generated schema.sql remains an empty-database snapshot, never an upgrade script.

## Task 1: Shared admission decoder

**Files:** Create backend/lotuspond/package_input.py; modify package_cli.py; test test_package_cli.py and new test_packages.py.
**Interfaces:** decode_package_bytes(data: bytes) -> object; safe_validation_errors(error: ValidationError) -> list[dict]; PackageReadError and READ_ERRORS shared by CLI/HTTP.

- [x] Add failing byte decoder tests for duplicate keys, nonfinite, depth, size and invalid Unicode; run focused pytest and observe failure.
- [x] Extract existing decoder/error sanitizer without weakening CLI behavior; CLI read_package delegates after bounded file read.
- [x] Run focused decoder and CLI tests; preserve all prior assertions.

## Task 2: Registry persistence and migration

**Files:** models.py, initialize.py, schema_version.py, schema.sql, test_migrations.py, new test_packages.py.
**Interfaces:** WorkflowPackageVersion(Scoped), PackagePublicationRequest(Scoped); canonical content uses LargeBinary to retain exact UTF-8 bytes.

- [x] Add failing model/migration tests for composite foreign keys, v11 preservation, fresh init, partial state rollback and second refusal; run focused tests.
- [x] Add models, byte/digest checks and pagination index; implement the reviewed v12 migration; adjust rewind helpers, including historical tests that directly change version without rewinding other tables.
- [x] Generate schema.sql with backend/export_schema.py; verify schema parity and focused PostgreSQL migration tests.

## Task 3: Tenant-scoped publication and reads

**Files:** Create packages.py; modify app.py; tests test_packages.py.
**Interfaces:** POST /api/v1/projects/{project_id}/packages (raw JSON, optional Idempotency-Key), GET /api/v1/packages?project_id=...&limit=...&cursor=..., GET /api/v1/packages/{id}, GET /api/v1/packages/{id}/content, GET /api/v1/package-contracts.

- [x] Add failing API tests for auth/scope/tenant boundaries, parser failures, immutable replay/conflict, exact content/secret_refs and metadata-only output; run focused pytest.
- [x] Publish only fully validated canonical JSON with server revisions. Require exact JSON media type and identity encoding. Bound package bytes in middleware while reading as well as at decoder. Store only fixed identifiers/digest/version/actor metadata in publication audit.
- [x] Use nested transaction uniqueness handling: insert version, recover identical existing version after conflict; insert workspace request key, recover same hash/result after conflict. An outer request-key conflict rolls back any new version/audit. Emit one audit only for a new version. Return 201 new or 200 identical replay; 409 changes. No update/delete routes.
- [x] Return only explicit metadata fields. Content response returns stored bytes with ETag, X-Package-SHA256 and X-Package-Canonicalization. List requires scoped project, limit 1–100, descending (created_at,id) and bounded opaque workspace/project cursor.
- [x] Run focused tests; add actual PostgreSQL concurrent identical/conflicting publication/key tests with barriers and assert one version/audit.

## Task 4: Documentation, independent review and verification

**Files:** API/database/capability docs, ADR-0009, AGENTS.md/CLAUDE.md boundary wording, app changelog, validation evidence.

- [x] Document endpoints, replay/conflict semantics, admission revisions, raw-byte integrity and migration sequencing; distinguish admission from runtime compatibility.
- [x] Independent reviewer checks final diff for authorization, transactions, migrations, immutability and acceptance coverage; fix findings and rerun affected checks.
- [x] Run npm run check through saved cloud serial Node wrapper/reaper; npm run check:contracts; npm run test:database against verified localhost PostgreSQL; schema parity, docs checks and git diff --check. Record exact counts/skips/failures and cloud profile separately from default CI behavior.
- [x] Commit intended files only, publish codex/package-registry and create draft PR targeting codex/platform-foundation if GitHub publication is available. Never merge, force-push or deploy.

## Independent plan review incorporated

Review found no architectural blocker. Version, request-key and publication audit share one outer transaction. Add audit only after recovered uniqueness savepoints; begin_nested flushes pending changes. Hash fixed context + ASCII project UUID + NUL + canonical bytes; keys are preserved exactly. Add same-key/different-project race rollback and different-key identical races. Explicitly cover current authority, production verified identity/MFA, operator tenancy, both partial-table states, complete premature tables and older-chain rollback. Middleware enforces package budget while reading; content decoder is defense in depth. Direct result-pointer tenant FK is tested independently.
