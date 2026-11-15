# Retire workflow-package v1 authoring and publishing

Date: 15 November 2026 (Asia/Kolkata). Status: **plan, decided by Claude under the owner's delegation ("Go with your recommendations"); this change carries it out.** It is step 3 of [ADR-0013](../../architecture/0013-trim-to-the-automation-core.md): "Once that ships, retire the Studio's v1 publishing, `POST /projects/{id}/packages`, the backend v1 compatibility modules (`package_contract.py`, `package_input.py`, `package_cli.py`) and `npm run package:validate`. This record is the decision for that step. It needs an implementation plan, not another ADR." Step 2, the Studio authoring executable-v2, shipped on 15 November (PR #45).

## What retires, and what stays

| Surface | Today | After |
| --- | --- | --- |
| The five Studio patterns | v1 graphs in `contracts/examples/*.v1.json` | **v2 versions** in `contracts/examples/executable-v2/patterns/`: reads, agent steps on a declared model, and each write as a reviewed action with read-back verification. Each ships with its synthetic catalog documents. Their generator (`generate.py --check`) and a backend test that publishes each catalog and passes the server's own check (`check_v2`, E05 rules included) pin them |
| Studio | Deployable workflow (v2) beside a Legacy graph (v1) | v2 only. It offers the five domain patterns, the SDK's structural examples (decision, parallel, iteration) and a blank package |
| Console v1 code | `pages/Studio.tsx`, `components/studio/*`, `lib/studio*.ts`, `lib/packageContract.ts` and their tests | Removed |
| `POST /projects/{id}/packages` | Publishes v1 | **Retired**: 405, like every retired write route |
| `GET /packages`, `/packages/{id}`, `/packages/{id}/content` | Read stored v1 publications | **Kept**: stored v1 packages stay readable byte for byte (ADR-0013) |
| `GET /package-contracts` | Describes v1 publication admission | **Retired** (404): there is no v1 publication left to describe |
| `package_contract.py`, `package_input.py`, `package_cli.py` | Backend re-exports of the SDK's v1 contract | Removed. `export_package_schema.py` reads the SDK directly |
| `npm run package:validate`, `scripts/python.mjs package` | Validate a v1 file | Removed; the SDK's own `lotuspond-package` CLI keeps validating v1 for anyone holding a v1 file |
| `workflow_package_versions`, `package_publication_requests` | Tables | **Kept unchanged**: no migration, and `schema.sql` is unchanged |
| SDK v1 contract (`lotuspond_sdk.contract`), `contracts/workflow-package.v1.schema.json`, the v1 examples | The SDK's published contract | **Kept**: the SDK is a separate distribution and ADR-0013 does not retire it. The v1 examples stay as the SDK's fixtures and as history |

## Steps

1. Generate the five v2 patterns with their catalogs, pinned by `generate.py --check` and `tests/execution/test_pattern_examples.py`.
2. Point the Studio's patterns at them, and remove the v1 Studio, its libraries, tests and the mode switch.
3. Remove the v1 publication route and `/package-contracts`, the three compatibility modules, the request-size special case for v1 publication in `app.py`, and the v1 npm script. `export_package_schema.py` imports the SDK. Delete the tests of the removed modules; `test_packages.py` keeps the read routes over stored rows; `test_automation_core_trim.py` lists the two retired routes.
4. Update API.md, CAPABILITIES, GOAL, the contracts README, the repository guides that name `package:validate`, and the changelog. The dev console harness drops its v1 publishing fixture.
5. Verify:
   - backend: the full execution and top-level suites;
   - web: lint, tests and build;
   - a browser check of the Studio.

   The engine is untouched, so the Linux conformance batch is not needed for this change.

## Not in this plan

Dropping the v1 tables or their rows (that needs its own reviewed, data-preserving plan, as ADR-0012 and ADR-0013 require), retiring the SDK's v1 contract, and a visual graph for v2.
