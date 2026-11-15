# Review inbox and Control Room pages

Date: 15 November 2026 (Asia/Kolkata). Status: **design, decided by Claude under the owner's delegation ("proceed to build in the order", item 6); this change builds it.** It serves the Control row of [GOAL.md](../../GOAL.md) ("a review inbox and Control Room pages") and E07's task inbox and review screen ([actions and human tasks](2026-10-26-actions-human-tasks-design.md): "A task inbox, a review screen and Studio authoring of effect steps belong to E07. E05 supplies their state and APIs").

## What exists

- **Task APIs** under `/projects/{project}/environments/{environment}`:
  - `GET human-tasks` (keyset pages) and `GET human-tasks/{id}`.
  - `GET human-tasks/{id}/proposal`: never cached, and every read is audited.
  - `POST claim`, `release`, `decision` (`decision_id`, `intent_sha256`, `allow` or `deny`) and `submission` (`submission_id`, `payload`).
  - Deciding needs the caller's live claim. A key is refused, and so is the requester when independent approval applies.
- **Execution APIs**:
  - `GET deployments` and `GET executions` (keyset pages).
  - `GET executions/{id}`, `/events` (metadata only), `/waits` and `/actions`.
  - `POST executions/{id}/control` (`request_id`, `pause` | `resume` | `cancel`).
- **Errors** are closed codes in `detail.error`. Without a production command key provider the task and action routes answer 503 `authority_unavailable` (item 7). On a server with no execution services, every execution route answers the same.

## Decisions

1. **Two pages replace the two Planned pages.** *Human tasks* is the review inbox. *Control Room* shows deployments and executions and lets an operator pause, resume or cancel. Both are scoped to one project (the top bar's project filter) and one of its environments (a picker on the page).
2. **The inbox lists open tasks**, each with its kind, deadline, escalation and claim. Opening a task shows its facts, then, for a review, the exact proposal: connector, operation, policy, target, input and when the approval expires. The proposal is fetched only when the task is opened, because each read is audited.
3. **Deciding is explicit.**
   - A person claims the task first. Allow and Deny appear only while the caller holds the claim.
   - The decision carries the proposal's `intent_sha256`, so a changed proposal cannot be approved by a stale screen. The server refuses it with `conflict`, and the page says the proposal changed and reloads it.
   - Allow is the one tomato control in the view (the design system's "a person decides"). Deny is a secondary control in danger text.
   - A form task takes a JSON payload.
   - Each decision and submission gets a fresh opaque id. A retry of the same click reuses it, so a double submit is answered by the server's replay.
4. **Control Room** lists:
   - deployments (status, active revision, writes that dispatch without review);
   - executions (observed state, requested state, reason, admitted, deadline).

   Opening an execution shows its waits, actions (when action services exist) and event metadata, plus the controls its state allows:
   - pause when it is pending, running or waiting;
   - resume when it is paused, or pause is requested;
   - cancel unless it has finished.

   Cancel asks for confirmation. A requested state is always shown apart from the observed one, as the API does.
5. **Unavailable is stated, not hidden.** A 503 `authority_unavailable` says that this server has not switched on production execution, or the action services, and that nothing on the page can act until it does. Other codes map to plain sentences, and a value is never echoed.
6. **Not in this change:**
   - creating deployments and changing their status (Studio and the API do this);
   - workers and queues;
   - reconciliation and owner resolutions;
   - signals;
   - package evaluation pages;
   - notifications.

   The dev console harness (`web/dev/console.tsx`) gains fixtures for both pages, so their layout can be reviewed without a server.
