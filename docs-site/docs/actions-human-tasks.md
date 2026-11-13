# Reviewed actions and human tasks

A durable execution can write to a business system only through a **reviewed action**. This page explains what happens to a write, from the moment a workflow proposes it to the moment it is verified, and what a reviewer sees.

!!! warning "Synthetic evidence only"
    Reviewed actions have run only against a synthetic target, with synthetic connectors and providers, in local Linux containers. No real connector writes yet, no notification is sent, and the console has no task inbox. An installation without a command key provider refuses every action and task route (503).

## The life of a write

1. **Intent.** When a workflow reaches an action step, the engine records an immutable *intent*: the exact connector version, operation, target and input, bound together by one digest (`intent_sha256`). Nothing can change those values afterwards.
2. **Review.** If the package's policy requires approval, a *human task* opens. An eligible person claims it, reads the proposal, and allows or denies that exact digest. If the values were different, the digest would differ and the decision would be refused.
3. **Dispatch.** An approved intent becomes a signed command for one runtime of the deployment's pool. Authority is checked again first: the reviewer's role, the approval's expiry, the connection, the pool and the recovery epoch.
4. **One write.** The runtime claims the command under a lease, journals that it is about to write, and writes once. It reports a receipt.
5. **Read-back.** If the result is uncertain (a lost response, a lapsed lease), the write is not repeated. The engine reads the target back. Only a qualified absence allows one resend, under the same idempotency key, and only for an operation that declares that key (`target_key` idempotency).
6. **Verification.** A verification read checks the business outcome. The execution succeeds only when every write it made is verified.

## Reviewing a task

Tasks are listed per environment at `GET /api/v1/projects/{project}/environments/{environment}/human-tasks`. A task appears once its execution is waiting on it.

- **Only people review.** API keys, runtimes, viewers and platform administrators cannot claim or decide. With independent approval, the person who started the execution cannot approve it.
- **Read the proposal** (`GET .../human-tasks/{id}/proposal`): the exact values you are asked to approve. Proposal responses are never cached, and reading one is audited without its values.
- **Claim** a task to hold it for 15 minutes (never past its deadline). The holder, or an owner or admin, can release it.
- **Decide** with `POST .../human-tasks/{id}/decision`: a `decision_id` you choose, the `intent_sha256` you reviewed, and `allow` or `deny`.
- **Escalation.** At half the task's timeout the escalation group may also act, and any claim ends. Nobody is notified: check the list.
- **Expiry.** An unanswered task expires at its deadline and the write is never made. An approval also expires; a write not sent by then is not sent.

## Following an action

`GET .../executions/{execution}/actions` lists an execution's actions as metadata only: authorization (`awaiting_review`, `approved`, `denied`, …), effect (`not_dispatched`, `accepted`, `observed`, `unknown`, `absent`) and outcome (`unknown`, `verified`, `not_verified`). Values never appear there.

When an effect cannot be settled automatically, the execution waits in `reconciliation_required`. An owner or admin then resolves the action (`POST .../actions/{action}/resolution`): read back again, confirm the write's compute stopped, record the effect as present from recorded evidence, or close it as unknown. A resolution never resends.

## For operators

Command key custody, leases, restore and the runbooks are in the repository's `docs/operations/actions-human-tasks.md`. The qualification record is `docs/validation/2026-11-13-actions-human-tasks.md`.
