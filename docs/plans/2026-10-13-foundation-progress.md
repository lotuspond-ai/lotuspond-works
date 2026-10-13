# Platform foundation: first implementation increment

Date: 13 October 2026. This is implementation evidence for the [platform delivery plan](2026-10-13-platform-delivery.md), not completion of the full platform.

## Preserved baseline and repository

The original checkout had no commits or remote, 329 staged files, 107 further working-copy modifications, and four untracked source/documents. Before editing, a private recovery snapshot retained the original Git objects/index, current source, status, file hashes, and archive checksums under `.local/foundation-backup-20261013T120303Z/`. All 333 source entries were verified against the archive. Actual environment files, dependencies, databases, and build output were excluded.

Initial commit `3f91bf0` contains the current bytes of 332 curated source files, each verified against that snapshot. The remaining `HANDOFF.md` is unchanged and stays local. The former index is recoverable from the private snapshot rather than silently discarded.

The canonical private repository [lotuspond-ai/lotuspond](https://github.com/lotuspond-ai/lotuspond) is created, its baseline `main` is published and verified, and this increment uses `codex/platform-foundation`. Repository-local HTTPS authentication uses the already signed-in GitHub CLI account. The nine older repositories and their consumers remain unchanged.

CODEOWNERS, contribution guidance, issue/PR templates and the intended main-branch policy are included. GitHub rejected protection with HTTP 403 because the current plan does not support it for this private repository. The policy is **not enforced**; see [repository governance](../repository-governance.md). No visibility, billing, hosting, deployment secrets, or documentation-publishing settings were changed.

## Implemented foundation

- ADR-0009 records the single licensable platform plus independently purchasable services. Current assurance workflows and onboarding remain in place.
- The authoritative Python contract defines bounded workflow-package v1 declarations: references, agent limits, supported nodes and graph structure.
- The local CLI rejects malformed/oversized/deep JSON, duplicate keys, invalid Unicode and nonfinite values, undeclared references, unsupported fields/types, and invalid graphs. Error output avoids rejected values and private paths.
- Canonical validated content has a deterministic SHA-256 digest. This is not package signing, publisher trust, approval or runtime enforcement.
- A generated Draft 2020-12 JSON schema, synthetic reviewed-action example and contract tests are checked in. Schema drift fails `npm run check`. The generated snapshot is excluded from generic formatting.
- The API changelog records this developer capability. The relational schema and existing application execution behavior are unchanged.

Try the current slice from the root:

```sh
npm run package:validate -- contracts/examples/reviewed-action.v1.json
npm run check:contracts
```

The example digest is `305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3`. Full contract semantics and limitations are in [contracts/README.md](../../contracts/README.md).

## Local validation

| Check                              | Observed result                                                                                                                                                                                                                                                                                                |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unchanged-source `npm run check`   | 105 JavaScript tests; 207 Python tests plus 9 subtests; 28 PostgreSQL-dependent skips; lint and build passed                                                                                                                                                                                                   |
| Foundation `npm run check`         | 105 JavaScript tests; 235 Python tests plus 89 subtests; 28 PostgreSQL-dependent skips; Python/TypeScript lint, schema parity and build passed                                                                                                                                                                 |
| Focused contract/CLI suite         | 28 tests passed, including mutated-model revalidation, serialization coercion, bounded collections, Unicode and FIFO rejection                                                                                                                                                                                 |
| Independent PostgreSQL integration | Fresh PostgreSQL 14.20 loopback cluster: 262 tests plus 89 subtests, no failures/skips; identity verified, disposable schemas absent and cluster stopped. Collection preceded the last added contract regression, which passed in the final ordinary/focused suites; hosted PostgreSQL 18 coverage is separate |
| Existing-component baseline        | Protocol 42 tests, voice 13 tests, Rust 10 tests, Go race 37 top-level tests / 65 including subtests, SQL schema snapshot parity passed                                                                                                                                                                        |
| PostgreSQL restore drill           | PostgreSQL 18.6 Alpine image, 38 synthetic tables restored with matching fingerprints; network disabled; temporary containers cleaned up                                                                                                                                                                       |
| Preservation and review            | Initial commit matches every curated pre-edit source byte; independent foundation review completed; original handoff remains unchanged                                                                                                                                                                         |

One existing Starlette/AnyIO deprecation warning remains. The first sandbox-only baseline had four loopback-listen permission failures; the unchanged rerun with local-test permissions passed. An integration run collected foundation tests while their modules were still being written and failed; that result is retained separately and superseded by the completed-code evidence above. These runs are not counted as passes.

Logs are retained privately under `.local/foundation-test-*/`, `/tmp/lotuspond-foundation-*.log` and the restore drill's temporary report directory. No existing or production database was read, reset or migrated.

## First hosted CI findings

The initial baseline [validation run](https://github.com/lotuspond-ai/lotuspond/actions/runs/38394753383) passed eight jobs and failed two. Both failures were in existing harnesses and were investigated rather than excluded:

1. The restore fixture omitted the required `workspaces.require_mfa` column. Explicit personal/organization values repair the fixture and exercise policy preservation; the real isolated restore drill now passes locally.
2. The runner smoke requires a verified loopback server address. A bridged CI PostgreSQL service has a container address, so the smoke guard rejects it before executing the runner. CI now gives this drill its own disposable host-network PostgreSQL instance listening only on loopback, with explicit identity and cleanup. The guard remains unchanged.

The foundation [PR validation run](https://github.com/lotuspond-ai/lotuspond/actions/runs/38396169650) passed all ten jobs for `f79f1e5`: PostgreSQL 18 ran 263 tests plus 89 subtests with no skips; sandbox checks passed 22 tests plus 14 subtests; deployment checks passed 31 tests plus 13 subtests. The corrected runner smoke, restore and offline installation drills all passed. The runner confirmed cancellation/revocation, timeout/crash handling, persisted results, control-key isolation and cleanup.

The duplicate push-triggered run was cancelled in favor of the PR run, but its cancelled checks still made GitHub label the PR unstable. The follow-up CI configuration runs feature validation through pull requests, with pushes to `main` and explicit manual dispatch still supported. This avoids duplicate runs and gives the final PR source one authoritative check set. CI is not deployment. Documentation publication remains disabled by its existing opt-in gate.

## Remaining platform work

E01 has a preserved/published engineering baseline and accepted product ADR; branch protection remains unavailable and operational staffing/first-customer decisions remain open. E02 is partial. This slice does not implement a persistent package registry, SDK distribution, executable input/output semantics, signatures, version compatibility tooling, a production runtime, Studio, commercial licensing, or a qualified industry pack.

The next bounded increment should define package-version compatibility and the immutable registry's tenancy/authorization contract, with an additive data-model review and failure tests before any migration. The durable-engine spike and synthetic action/approval contracts can then proceed under the established plan. Keep evaluation runs separate from production business executions and qualify each new connector/topology with its own evidence.

## Coordinated foundation review follow-up: 14 October 2026

Two independent review findings were reproduced at c1da02e. Valid typed package instances failed revalidation with invalid_json_value, and the exact 1 MiB/32-connector fixture validated before failing digest when optional defaults expanded its canonical content by 576 bytes. New regressions failed for both defects before changes.

A wrap validator now projects only known model fields, checks bounds and revalidates all semantics. Unknown model types, extra state/fields, cycles and arbitrary values are rejected without invoking their serializers. The existing mutated-model regression now asserts the actual limit violation rather than accepting any rejection. Admission checks the complete normalized document under the unchanged 1 MiB and structural bounds, so oversized default expansion is rejected early. Omitted/null defaults at exactly the normalized bound produce identical digests; bound-plus-one rejects. No schema, canonical format or execution authority was changed.

Fresh cloud verification using the saved serial Node wrapper/reaper: npm run check passed 105 Node tests (zero skips) and 239 Python tests plus 89 subtests (28 PostgreSQL-only skips), lint/contracts/build. Independently verified loopback PostgreSQL 17.11 npm run test:database passed 267 tests plus 89 subtests with zero skips. Focused contract/CLI tests passed 32 plus 80 subtests. Contract/schema snapshot parity, strict MkDocs and git diff --check passed. Existing Starlette/AnyIO deprecation warning remains. Serial cloud checks do not substitute for default parallel GitHub CI. No deployment or production database access occurred.

Registry metadata changes were preserved in a named stash while this additive foundation fix was made first. Registry must incorporate the foundation fix through a normal merge, preserving both histories; neither PR is merged by this work.
