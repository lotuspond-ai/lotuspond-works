# Control Room: workers, queue and deployment changes

Date: 16 November 2026 (Asia/Kolkata). Status: **design, decided by Claude under the owner's delegation ("Go with your recommendations"); this change builds it.** It closes the Run row's "Control Room pages for workers, queues and deployment changes" in [GOAL.md](../../GOAL.md) and extends the [first Control Room](2026-11-15-review-inbox-control-room-design.md).

## What exists

- **Workers** are runtime instances that enroll into a runtime pool with a one-time token. Owner and admin APIs exist under `/projects/{p}/environments/{e}`:
  - pools: create, enable or disable, and versioned pool policies;
  - enrollment tokens: issued once, listed, revoked;
  - runtime instances: listed with their last-seen time, and revoked.

  Every mutation takes an idempotency key. The console uses none of them.
- **The queue.** The engine keeps one `execution_capacities` row per environment: active, pending and retained-wait counts against their limits. No route exposes it.
- **Deployments.** `POST …/deployments` deploys a published package to a pool with a configuration and an optional release; `POST …/deployments/{id}/status` enables, disables or revokes. Both are API-only. Control Room only lists deployments.
- **Triggers and schedules** do not exist in the engine (a separate plan item), so there is nothing to show for them.

## Decisions

1. **Control Room gets four tabs over one environment:**
   - **Runs:** today's deployments and executions, unchanged.
   - **Deployments:** status changes and a new deployment.
   - **Workers.**
   - **Queue.**
2. **Workers** (owners and admins; others see why they cannot):
   - **Pools:** status and current policy version. Create a pool, and enable or disable it.
   - **Enrollment tokens:** issue one into a pool, shown **once** with the runtime's enroll command and never again; list their state; revoke one not yet used.
   - **Instances:** pool, state, generation, policy version and last seen, with a "not seen recently" mark after 2 minutes. Revoke an instance after confirming. Revoking stops its next session and never undoes work already sent.
   - Pool policies stay in the API: they name connectors and connections, which need the operator's catalog. The tab shows each pool's current policy version.
3. **Queue** (`GET …/execution-queue`, `read`). A new read model, read without locks. It returns:
   - the environment's capacity row: active, pending and waits, with their limits, or `null` before any admission;
   - the count of executions in each state;
   - the oldest still-pending admission time.

   The console shows each count against its limit, plus how long the oldest pending run has waited.
4. **Deployment changes** (`manage`: owners and admins):
   - Enable, disable or revoke a deployment from its row. Revoke asks first and cannot be undone; a revoked deployment admits nothing new.
   - **Deploy a package:** pick a published workflow in this environment, a pool, and optionally a release. Limits are prefilled within the package's own limits. Connection bindings are edited as JSON, prefilled with one entry per pinned dependency to complete.
   - The server's answer is shown as it is. In particular, `assurance_unavailable` means the installation requires a release and none proves this version.
   - Every mutation carries a fresh idempotency key, reused on a retry of the same intent.
5. **Errors** use the closed codes and the shared sentences. Fleet routes answer `authority_unavailable` where fleet management is not composed, and the tab says so once.

## Out of scope

Pool policy authoring, connection and secret-reference management (the planned Connections page), triggers and schedules, and live updates; the tabs refresh on request.
