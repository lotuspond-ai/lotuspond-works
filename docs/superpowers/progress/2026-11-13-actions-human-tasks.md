# Reviewed actions and human tasks implementation evidence (E05)

The approved [E05 plan](../plans/2026-10-26-actions-human-tasks.md) (owner decisions of 29 October and 2 November 2026) is implemented on `claude/e05-actions`. That branch is stacked on the reference workflow's milestone 3 (`055f495`, PR #28) and is milestone 4 of the [reference workflow](../specs/2026-11-11-reference-workflow-design.md). Each task had an independent review before its commit.

Completion of this foundation does not establish production writes or production qualification: every connector, provider and target that ran it is synthetic, and all evidence is local. Each task's evidence and limits are in [VALIDATION.md](../../VALIDATION.md). Task 12's qualification record is [the validation document](../../validation/2026-11-13-actions-human-tasks.md); the operator guide is [actions and human tasks](../../operations/actions-human-tasks.md).

| Task | Commit | Delivers |
| --- | --- | --- |
| 1 | `9dea74d`, `3e76589` | Frozen action binding derivation and closed E05 vocabularies |
| 2 | `244db4e` | Scoped action, task and outcome records; additive schema v16 (backend 0.4.0) |
| 3 | `3f519b1` | Admission of reviewed effect packages under a pinned interpreter v2 |
| 4 | `dea1ca1` | Immutable action intents as interpreter-v2 transitions |
| E04 finding 2 | `5683b3a` | A wait is listed and signalled only once it is retained |
| 5 | `5149393` | Human tasks: assignment, claim, decision and escalation under current authority |
| 6 | `9266e92` | Reviewed actions dispatched as signed commands under fresh authority |
| 7 | `57c568c` | Journaled writes against a synthetic target in the runtime (runtime 0.3.0) |
| 8 | `c10a233` | Uncertain writes settled by read-back before any resend |
| 9 | `9c1f658` | Outcome verification, and execution success gated on it |
| 10 | `6650c4a` | Controls, revocation, restore and action projections |
| 11 | `05e05ef` | Human task, action and runtime action APIs |
| 12 | `805dddc` | Conformance tiers 1–3, installed acceptance and the qualification record |
| 13 | this commit | Packaging checks, the operator guide, API and capability documentation; three regressions the full `validate` job found: Task 2's schema test no longer strips foreign keys from later SQLite schemas, the deploy smoke fixture carries Task 12's write count, and the fleet harness reads the current schema version |

## Open for the owner

Recorded with their evidence in the validation document:

- **ACT-12** (a reviewer without source access) has no real-process form: proposal values are stored under the workspace response policy, never a source pin. Propagate source pins into intent values, or restate the case.
- **RESTORE-2 (present).** A restored intent that the restored rows never dispatched fails `recovery_epoch_changed` even when its effect reads back present. The plan expected `observed`. Its write is never repeated.
- **The lapsed-write slot.** A lapsed write keeps its deployment's compute slot until its stop is confirmed, by the runtime's report or an owner's `compute_stopped` resolution.
- **Earlier deviations awaiting acknowledgement:** intent state columns named `*_state` (`AUTHORIZATION` is reserved); the new code `unsupported_operation`; an intent's approval must equal its policy's.
- **Task 10's gaps, fixed on 2026-11-14** (`claude/e05-edge-cases`), each with a test in `test_action_recovery.py` or `test_action_races.py`; an independent review of the first draft found more, fixed here too:
  - A cancellation requested after the owner's release reads back every held intent (`_contain_restored`, which searches every region of the execution that exists), and ends `cancelled` once the read-backs settle. An undecided held intent is read back too: the cancellation withdraws it, and its read-back still settles whether a write from the lost timeline exists.
  - A cancellation while an owner already decides waits for them instead of asking again (a round that changed nothing fenced the execution as `integrity`); this also covered `_contain`'s uncertain writes.
  - Every held intent of a region is read back at once, and none settles the region before all are recorded and each qualified absence has closed its intent; an owner's question outranks another step's failure.
  - `intent_held` names the intent by its intent step's occurrence path.
  - An intent step of an enclosing region can be dispatched from a nested one (a parallel branch): `_body_refs` loads the `intent`, `decision` and `receipt` handles across regions, `_with_actions` finds the intent through the outer handle, and recording a read-back finds the same step (`_intent_step_path`). Before, such a dispatch failed `unavailable_value`.
  - Race tests cover the restore hold against a task decision, the release against a cancellation and against a runtime poll, and cancel containment against a read-back report.
- **Restore gaps still open** (with RESTORE-2):
  - Read-backs are read back together only within one region. A step that fails `recovery_epoch_changed` in one parallel branch cancels the reserved read-back of a held intent in a sibling branch or iteration item, which is then never read back.
  - A restored intent whose last write failed before the restore (for example unclaimed and expired) gets a read-back whose result `restored()` ignores; the dispatch step's retry may create a new command, which the dispatcher refuses for an older-epoch intent, so nothing is re-sent.
  - `restored()` does not consult an owner's resolutions, so after an owner resolves a restored intent's unknown or unqualified read-back, it asks again.
  - A held intent whose dispatch region never started before the restore is not read back under a cancellation, and an intent reaching its dispatch other than as a whole step handle (a field of a value, a subworkflow input, an iteration item) is found only through its commands, so a restored one that was never dispatched is not read back.

## Not done

- Real connectors and providers: the `https-json` write side, a production command key provider and its trust. Production composition stays interpreter v1 only, and the action routes answer 503.
- Notification delivery, compensation (decision 14), E06 outcomes, the E07 task inbox UI, E08 signed entitlements and E09 operations.
- Hosted CI (billing-blocked since 5 November), and the planning-target load: tier 3 failed its throughput criteria on a 4-vCPU VM, while at most one target write per intent held.
- The branch has not been merged or opened as a pull request.
