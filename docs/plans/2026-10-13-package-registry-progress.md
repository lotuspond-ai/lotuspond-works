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

The initial review deferred a metadata-query memory optimization. The authorized 14 October review follow-up reproduced canonical-content fetching in both list and single-metadata SQL queries, then deferred the BYTEA column with raiseload protection. SQL-level regressions verify one content-free metadata query and unchanged exact-byte content retrieval. The focused PostgreSQL registry suite passed 48 tests. No manifest content is returned in list/audit output.

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

## GitHub publication

Implementation commit: `91bf714c7409f347ab157e3806ee2640c00daffe`. The branch was pushed normally; remote verification retained foundation `c1da02edcb3fb4db57f63a8e403db8e379cf5a2a` unchanged. [Draft PR #2](https://github.com/lotuspond-ai/lotuspond-works/pull/2) targets `codex/platform-foundation` and explicitly depends on open PR #1.

The implementation commit started [GitHub validation run 38433275134](https://github.com/lotuspond-ai/lotuspond-works/actions/runs/38433275134). At the publication snapshot, execution, monitoring rules, docs, protocols, voice, evidence and restore jobs passed; validation, collector and offline-install were still running. This is a snapshot, not an all-green CI claim. A documentation-only follow-up records publication and completes the plan ledger; the final PR/head status must be read from GitHub separately. No workflow was manually dispatched and nothing was deployed.

## Coordinated review fixes: 14 October 2026

The complete independent review list comprised two foundation fixes and three registry fixes. Foundation commit `784a2e0f81bb73c643b67cd04ade5cd01b6ec54c` fixes valid-model revalidation and normalized-size admission consistency under the unchanged 1 MiB bound. The independent published-commit re-review passed 32 focused tests plus 80 subtests and schema parity, verified both original repros and retained rejection of invalid limits, references, cycles, extras, tuples, nonfinite values and deep mutations; no new findings. Foundation was pushed first. Registry incorporates it with normal merge `adf8b8f`, preserving both ADR appendices. No history rewrite or PR base change was made.

Metadata list/single reads now omit canonical BYTEA at SQL level with deferred-column raiseload protection. Two regression cases failed before the fix and passed afterward, also confirming unchanged exact content downloads. A named stash preserved this work while foundation was fixed; the stash remains retained after successful application.

Direct raw-ASGI requests reproduced a non-ASCII header byte returning 500 and duplicate gzip/identity or identity/identity encodings reaching publication with 201. HTTP Latin-1 decoding now lets strict key validation return controlled 422, while the identity-only package route rejects duplicate Content-Encoding with 415. Body limits, tenant authentication, key scopes and Origin rules remain intact. Three raw-wire cases failed before the fix; all four encoding/key cases now pass. A near-boundary API regression rejects overlarge normalized content at admission, then publishes, downloads and replays the exactly bounded omitted/null equivalent content with identical digest and one version.

Final integrated cloud npm run check (saved serial Node wrapper/reaper): 105 Node tests passed, zero skips; 293 Python tests plus 89 subtests passed, 39 PostgreSQL-only skips; lint/contracts/build passed. Required independently verified loopback PostgreSQL npm run test:database passed 332 tests plus 89 subtests, zero failures/skips. Focused registry/boundary check before the final size case passed 54 tests. Contract/schema parity, strict documentation and git diff --check passed. The existing Starlette/AnyIO deprecation warning remains. Test output and manual progress ledger are preserved in ignored work/package-registry/.

The coordinated reviews are complete, but PR #2 remains a draft for the coordinator's re-review of the published fixes. Merge order remains foundation PR #1 first, registry PR #2 second; PR #2 continues to target codex/platform-foundation until explicitly coordinated otherwise. Cloud validation and GitHub CI are separate evidence. No PR was merged, no deployment occurred and no production database, customer infrastructure or Microsoft context was accessed.

Final review smoke: all seven real API/Node runner cases passed on schema 12, cleanup confirmed, no external network. Foundation GitHub validation run 38466076619 completed successfully for exact commit 784a2e0. Registry GitHub CI must be checked separately after its final additive fix push.

## Main integration and discovery-limit correction: 14 October 2026

Foundation PR #1 was merged with a merge commit at `18eb85420d1e90e47fb5fb23a060a125180f08ed` after exact-head hosted CI, independent review, a fresh full check and 267 PostgreSQL tests plus 89 subtests. Registry PR #2 now targets `main`; the main merge was incorporated normally without changing registry source bytes or rewriting history.

The subsequent GitHub review identified a discovery mismatch when `LOTUSPOND_MAX_REQUEST_BYTES` is below the package contract's 1 MiB ceiling. A new API regression failed with 1,048,576 advertised instead of the configured 1,024 bytes. Discovery now reads the same effective minimum enforced by request middleware. Three cases cover smaller, equal and larger global limits, accepting a valid package at the exact advertised wire limit and rejecting one additional byte. The registry contract ceiling and normalized-content limits are unchanged.

Fresh verification of the integrated source and repair: all three discovery regressions pass; the saved serial Node wrapper/reaper `npm run check` passes 105 Node tests, 296 Python tests plus 89 subtests (39 database-only skips), lint, contract parity and build. Independently verified loopback PostgreSQL `npm run test:database` passes 335 tests plus 89 subtests with no skips. SQL snapshot parity, strict MkDocs and whitespace checks pass. Independent review found no material blockers. One existing Starlette/AnyIO deprecation warning remains. These local results do not replace the new hosted CI run required before merging PR #2.
