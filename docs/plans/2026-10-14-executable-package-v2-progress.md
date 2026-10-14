# Executable package v2 implementation progress

The owner approved the specification and ten-task plan on 14 October 2026 and selected subagent implementation with independent review after every task. Implementation starts from approved design commit `468550703a45c702995391c5d93ac201283d4a50` in `codex/executable-v2`, preserving the original checkout and the completed SDK/design branches.

The new worktree passed the inherited SDK contract/input baseline: 38 tests and 68 subtests on Python 3.14.5. The prior SDK has its own complete review, application/database, installed-artifact, offline-recovery and hosted-CI evidence. These baseline results do not establish any v2 implementation or runtime qualification.

All ten v2 tasks are pending. The complete approved product requirement register remains open; this subsystem delivers executable definitions/compiler and runtime-facing contracts, with later engine/runtime/Studio/commercial/operational goals retained.

## Task 1 implementation evidence (independent review pending)

Bounded executable JSON/JCS, fixed diagnostics, exact known-model projection,
strict fixed-field guards and ASCII report helpers are implemented in the new
namespace. SDK metadata is 0.2.0; both locks add only exactly pinned
`rfc8785==0.1.4` and the SDK metadata/dependency changes. The reviewed Apache-2.0
sdist hash matches
`e545841329fe0eee4f6a3b44e7034343100c12b4ec566dc06ca9735681deb4da`;
installed canonicalizer source also matches that reviewed source.

The specified focused command first failed with two missing-namespace collection
errors, then passed 105 tests. Self-review reproduced and fixed unsafe integer
field admission (including optional integers) and constructor-bypassed limits;
normalized defaults, report wrappers/newlines and ASCII expansion have bounds
coverage. An executable test-package marker resolves the same-name inherited
`test_input.py` collection collision. The controller authorized the existing
installed distribution assertion to track SDK 0.2.0.

Final required scoped checks pass on Python 3.14.5 and separate Python 3.13.13:
181 tests and 68 subtests on each, with no skips. This includes focused v2 tests,
inherited contract/input/signing and installed wheel/rebuilt-sdist checks.
A separate one-off installed probe imports the new namespace, verifies the exact
JCS dependency and tests decoder/JCS numeric/order behavior from both artifact
origins on both interpreters. The independent Node oracle passes all 26 RFC
numeric vectors plus UTF-16/numeric-key/Unicode checks. Frozen SDK Ruff passes.
Historical v1 schema parity, 1,166 bytes and digest
`305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3` remain pinned.

This is local Task 1 evidence; no full application/database/offline matrix,
provider execution, authority or production qualification is claimed. Later
v2 authoring/compiler/wire/CLI APIs remain separate tasks. The controller owns
review acceptance and task/plan completion state.
