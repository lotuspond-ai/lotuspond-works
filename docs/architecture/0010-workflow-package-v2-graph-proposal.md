# ADR-0010: Graph-based workflow-package v2 proposal (withdrawn)

Date: 2026-10-22. Status: **withdrawn before review. The executable-v2 contract merged in PR #6 supersedes it.** Nothing from the proposal is adopted: no document, schema, example or code. This record accounts for the number and keeps the proposal's one unaddressed requirement visible.

## Context

On 15 October 2026 a local session drafted "ADR-0010: Workflow-package v2 executable contract". It worked from a checkout 120 commits behind `origin`. The draft came with a proposal (`docs/plans/2026-10-15-workflow-package-v2.md`) and a prototype under `spike/workflow-package-v2/`, and none of it was published. By then the owner had already approved and merged a different executable contract: the [executable-v2 specification](../superpowers/specs/2026-10-14-executable-package-v2-design.md) and its SDK in `sdk/python/src/lotuspond_sdk/executable/` (PR #6). Both use the literal `lotuspond.workflow-package.v2`, but their structures are incompatible: the draft is a node graph with `branch` nodes, while executable-v2 uses nested structured regions.

## Decision

Withdraw the draft. Executable-v2 is the only v2 contract. The draft's decisions map onto it as follows:

| Draft ADR-0010 decision                                                   | Executable-v2 as merged                                                                                                                                                     |
| ------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Typed package inputs, outputs, configuration and connector signatures     | Bounded typed values and schemas. The dependency catalog declares operation schemas, effect class and idempotency/reconciliation requirements                               |
| Checked data mapping from dominating steps                                | Typed mappings with lexical availability, branch-export and join-ownership checks                                                                                           |
| Bounded branching: `branch` nodes, JSON condition trees, no other fan-out | Decision regions select one of two bodies. Executable-v2 also has parallel all-join and bounded iteration, which the draft excluded                                         |
| Manual, schedule and event triggers with derived idempotency keys         | **Not covered.** The requirement stays in the completion register's "Triggers and schedules" row                                                                            |
| A human review binds exactly one write step and its resolved parameters   | An action intent, a human task in review mode and an action dispatch, linked by intent and decision handles bound to the canonical intent digest. Recompute before dispatch |
| v1 frozen; readers dispatch on the exact version literal                  | Same rule: v1 bytes and APIs unchanged, a separate `lotuspond_sdk.executable` namespace and an explicit `v2` CLI token                                                      |

The draft's prototype and examples stay on the owner's local branch `claude/local-work-2026-10-15` (commit `5b5cd5b`) and are not copied into this repository. The [durable-engine spike](../plans/2026-10-15-durable-engine-spike.md) ran that prototype, so the spike's evidence covers the draft's sequential vocabulary only. [ADR-0011](0011-postgresql-durable-execution.md) states what that means for the engine decision.

## Consequences

- No second v2 contract, schema snapshot or validator is introduced.
- There is still no contract for trigger declarations: schedule occurrence keys, event deduplication keys, timezone, DST and misfire policy. E04 admits work only through explicit caller idempotency keys. Schedules and webhooks need their own specification under the register row above.
- The draft's analysis of alternatives (deferring CEL, rejecting full JSON Schema for types) is not carried forward as a decision.
