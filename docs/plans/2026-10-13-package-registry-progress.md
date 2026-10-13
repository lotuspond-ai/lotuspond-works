# Immutable workflow package registry: cloud evidence

Date: 13 October 2026 (Asia/Kolkata). Work is limited to `lotuspond-ai/lotuspond`; no deployment, production database, Microsoft work context or additional cloud infrastructure was accessed.

## Source and preservation

The fresh checkout was clean on `codex/platform-foundation` at `c1da02edcb3fb4db57f63a8e403db8e379cf5a2a`. Remote foundation and pull/1/head matched exactly; main was `3f91bf0f091a4a9cee719ce67b5b3ed3f74ef52c`. GitHub confirmed PR #1 remains open. `codex/package-registry` was created from that foundation, without modifying its branch, merging, resetting, force-pushing or restoring unrelated history. Source archives and existing features remain preserved. Environment files, dependencies, database files and test output remain excluded from publication.

Read AGENTS.md, CLAUDE.md, SECURITY.md, CONTRIBUTING.md, capability register, ADR-0009 and all three October platform plans before model/API implementation. Superpowers was applied through the available skill provider: context/brainstorming, writing-plans, executing-plans, TDD, systematic debugging, independent review, verification and branch finishing. The reviewed task brief already authorized architecture and autonomous routine implementation; the [bounded implementation and additive migration plan](../superpowers/plans/2026-10-13-package-registry.md) received independent review before model/API work.

## Restored environment

Observed Node 24.21.0, Python 3.14.5, uv 0.11.16 and PostgreSQL 17.11. The saved `/workspace/.lotuspond-tools/start-test-db.sh` verified the saved cluster system identifier against pg_controldata, then verified authenticated user, database, server loopback address, port 55439, listen address and exact data directory `/workspace/lotuspond/.local/cloud-test/data`. Repeated startup reverified the same identity. Only this localhost test database and disposable random test schemas were used. No empty initializer was applied to an existing installation.

The restored serial Node wrapper exactly matches the saved install script's heredoc. The required `mkdir -p /workspace/.lotuspond-tools/test-bin` precedes wrapper creation; reconstruction in a newly created scratch directory produced identical bytes. The startup runner baseline passed 21 tests with zero skips. The saved child-process reaper and serial test-file profile were used without modifying assertions or hiding skips.

The shell sandbox initially prevented loopback sockets (`Operation not permitted`) and access to the configured network proxy. Authorized sandbox escalation allowed the saved local database helper and Git operations; no identity/loopback guard was weakened, credentials printed, network domains added or proxy bypassed. Sandboxed TestClient runs stalled and were stopped; successful API validation used the same authorized execution mode. `gh` API access was forbidden by the cloud HTTP policy; the installed GitHub connector supplied the PR-state read instead.

## Implemented boundary

The Python-owned registry admits complete v1 semantic validation, shares the CLI's bounded byte parser and safe errors, and stores exact canonical JSON bytes with digest and admission revisions. Workspace/project composite foreign keys, immutable logical version uniqueness, workspace request-key uniqueness and a composite tenant result pointer reinforce scoped read/write dependencies. Version/key/audit share one outer transaction; savepoint recovery preserves identical replays and rolls back conflicting candidate versions. Lists and audit responses omit manifest free text; content reads do not revalidate or rewrite stored bytes. No mutation routes, signatures, secret resolution, Studio, production execution or runtime compatibility claim were added.

Schema 12 adds only workflow_package_versions and package_publication_requests (40 total tables). Advisory transaction lock 736183 and local-only guards remain; the v10 step advances its local current to 11, then v12 refuses either or both premature registry tables. Existing rows and columns are preserved. Tests prove fresh empty initialization, v11 preservation, older chains, partial-state/earlier-DDL rollback, tenant pointer rejection and second migration refusal. See [DATABASE.md](../DATABASE.md) for local sequencing and forward-fix recovery; schema.sql remains an empty-database snapshot, never an upgrade script.

## Independent review

A separate plan reviewer found no architectural blocker and tightened hashing, transaction/audit placement, streaming budgets, authority coverage and partial-state tests. A fresh final reviewer found no critical or concrete authorization/transaction/migration defect. Two important evidence gaps were closed: keyed/unkeyed replay after changed server revisions plus a forced equal-digest/different-bytes conflict test; and SQL-insertion barriers proving actual competing version/key inserts, including rollback of the losing cross-project key race. Final PostgreSQL verification includes these tests.

One minor optimization is deferred: metadata list queries select full canonical-content rows internally before excluding content from responses. A page is bounded to 101 fetched rows, but can load roughly 101 MiB of manifest bytes; a future deferred-column optimization can reduce that memory. No manifest content is returned in list/audit output.

## Exact cloud commands and outcomes

From `/workspace/lotuspond`, activate the saved tools and select the saved test wrapper:

```bash
source /workspace/.lotuspond-tools/activate.sh
export PATH=/workspace/.lotuspond-tools/test-bin:$PATH
backend/.venv/bin/python /workspace/.lotuspond-tools/reap.py npm run check
LOTUSPOND_PLATFORM_TEST_DATABASE_URL=postgresql+psycopg://lotuspond@127.0.0.1:55439/lotuspond_platform_test backend/.venv/bin/python /workspace/.lotuspond-tools/reap.py npm run test:database
npm run check:contracts
backend/.venv/bin/python backend/export_schema.py --check
uvx --from mkdocs==1.6.1 mkdocs build --strict --config-file docs-site/mkdocs.yml
git diff --check
LOTUSPOND_PLATFORM_TEST_DATABASE_URL=postgresql+psycopg://lotuspond@127.0.0.1:55439/lotuspond_platform_test backend/.venv/bin/python /workspace/.lotuspond-tools/reap.py backend/.venv/bin/python scripts/checks/runner-smoke.py --expected-data-directory /workspace/lotuspond/.local/cloud-test/data --report work/package-registry/runner-smoke-final.json
```

| Check | Observed result |
| --- | --- |
| Foundation npm run check, saved cloud profile | Passed: 105 Node tests, zero skips; 235 Python tests + 89 subtests, 28 database skips; lint/contracts/build passed. |
| Shared decoder/CLI RED→GREEN | Six expected missing-module failures, then 18 tests + 12 subtests passed. |
| Registry API RED | 35 expected missing-route failures, four PostgreSQL-only skips. |
| Registry migration RED→GREEN | Six expected missing-model/v12 failures; final focused migration suite 26 passed on PostgreSQL. |
| Intermediate registry/migration PostgreSQL | 65 passed, zero skips; expanded registry suite 44 passed before final review additions. |
| Final npm run check, isolated saved cloud profile | Passed: 105 Node tests, zero skips; 282 Python tests + 89 subtests, 39 database-dependent skips; TypeScript/Python lint, contract parity and production build passed. |
| Final npm run test:database | Passed: 321 tests + 89 subtests, zero failures/skips; includes final review additions and deterministic concurrency. |
| Real API/Node runner smoke | Seven cases passed on schema 12; cleanup confirmed; no external network used. |
| Explicit contract parity | Passed. |
| SQL snapshot parity | Passed; generated without database connection. |
| Strict MkDocs 1.6.1 build | Passed. |
| git diff --check | Passed. |

One existing Starlette/AnyIO BlockingPortal deprecation warning occurs in Python tests. Skipped ordinary-suite integration tests are not counted as passes; the separate PostgreSQL run executes all 321 without skips.

A first final full check run concurrently with the PostgreSQL suite failed the existing `cooperative abort lets the adapter finish its cleanup before group termination` Node test: its cleanup file was absent when the 80 ms abort arrived. The identical test passed in the foundation baseline and final isolated run. An intermediate isolated run passed 105 Node tests then stopped on an app.py import-order lint error, which was fixed before the final full green run. Product timing assertions and tests were not weakened. Serial cloud verification does not establish default parallel GitHub CI behavior.

## Decisions and remaining limits

Use the supplied fresh mounted checkout on the requested separate branch, without an additional worktree: restored tools target that checkout and it contained no unrelated edits. Relocating tools would be required if that choice proved unsuitable. Maintain scratch evidence in ignored work/ because remote skill resources supply instructions without local helper executables. The DELETE refusal test supplies the JSON header required by existing mutation middleware; no security exception was introduced. Production readiness/deployment and redesign of unchanged authentication remain outside the approved registry scope; these tests cannot establish runtime safety.

Cloud validation, GitHub publication/CI and deployment are separate. The dependent PR must remain a draft targeting codex/platform-foundation while PR #1 is open. GitHub CI has not been represented as passing by these cloud results. Deployment/customer compatibility and production PostgreSQL upgrades require their own authorization and evidence.
