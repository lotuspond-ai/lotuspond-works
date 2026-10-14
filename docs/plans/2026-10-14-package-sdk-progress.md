# Package SDK implementation progress — 14 October 2026

The owner approved the [implementation plan](../superpowers/plans/2026-10-14-package-sdk.md) and chose subagents with independent review after each task. Work proceeds in an isolated `codex/package-sdk` worktree from `c2d3e3685db79f37758095403b3bab4a94212e32`; the original checkout, draft readiness PR #3, archive and local test data are preserved.

The scope is the approved standalone SDK/diff/detached trust/declared compatibility subsystem. Executable package semantics, runtime authority, live compatibility and the broader production qualification register remain open. No package-index publication, production deployment or customer-system access is implied.

## Task evidence

Implementation has started with isolated dependency installation and baseline checks. Each completed task will record commits, meaningful red/green tests and independent specification/quality review. The controller coordinates whole-branch review and draft PR publication after the final task; implementers do not spawn additional agents or publish.

## Task 1 — standalone extraction

The historical example's exact canonical bytes were frozen before extraction from
baseline `753b0658730a5ac1f5ffd47afab89f30b0d7374e`. SHA-256 remains
`305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3`.
The new focused tests first failed on the missing `lotuspond_sdk` module. A separate
content-proof regression failed because SDK source was not included in the proof.

The extracted pure validator/decoder share types and behavior with backend
compatibility modules. Focused SDK, distribution and backend parity tests,
frozen sync, schema byte parity and deployment proof unit tests are recorded in
the Task 1 implementation report. Local SDK suites passed 40 tests and 68 subtests
on Python 3.13.5 and 3.14.5; focused backend checks passed 40 tests and 80 subtests
(with one existing Starlette deprecation warning), and deployment unit checks
passed 36 tests and 13 subtests. Ruff and schema parity checks passed.
Wheel and sdist-derived wheel tests run from
fresh environments outside the checkout and reject API dependencies.

The brief's sample Unicode test named `Reviewed action example`; the existing
example actually names `Synthetic reviewed action example`. Its replacement
needle was corrected to match the independent fixture without changing bytes.
An initial copied SDK schema test also needed its repository-root path adjusted.

Controller-owned independent review, full application/database tests, actual API
image build/content proof and airgap smoke remain pending here. CI includes the
3.13/3.14 SDK matrix; no hosted CI, publication, production deployment or customer
qualification is claimed by local tests.
