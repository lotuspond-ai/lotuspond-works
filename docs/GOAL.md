# What Lotuspond is for

Status: the product's direction, set on 2026-11-11 by [ADR-0014](architecture/0014-focus-on-the-core-loop.md). Every feature must serve one step of the core loop below, or the foundation beneath it. Anything else is removed or not built. [CAPABILITIES.md](CAPABILITIES.md) records what each step can do today, and [VALIDATION.md](VALIDATION.md) the evidence for it.

## The problem

Companies can build an AI agent in an afternoon. They cannot yet trust one inside a real business process, such as matching invoices, approving refunds or updating customer records, for four reasons:

1. **It does not run reliably.** An agent stops halfway, retries blindly, and leaves half-done work in the systems it touched.
2. **Nobody can prove a change is safe.** A new prompt, model or tool silently breaks a process that worked yesterday.
3. **Nobody is in charge of what it does.** A payment, refund or record change happens without a named person deciding.
4. **Nobody can say what happened.** When something goes wrong, there is no record of which version acted, on what input, with whose approval.

## What Lotuspond does

**Lotuspond runs AI-agent workflows inside a company's real processes: each version is proven before it is released, each consequential action waits for a person's decision, and every outcome is recorded.**

It is used by three kinds of people:

- **Builders** define and change workflows.
- **Reviewers** decide the actions a workflow is not allowed to take on its own.
- **Administrators** manage people, keys and environments.

## The core loop

Everything in the product belongs to one of four steps, in this order, or to the foundation.

| Step           | The question it answers                    | What it contains                                                                                                                                                                                                                             |
| -------------- | ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Build**   | What exactly will this workflow do?        | A workflow package: the systems it reads and writes through named connections, agent steps with hard limits, and the points where a person decides. Authored in the Studio or the SDK, published as an immutable, digest-identified version. |
| **2. Prove**   | Is this version safe to release?           | Scenarios with expected outcomes, evaluation runs on the customer's runners, a release policy, and a release decision with independent approval when the policy requires it. Deployment admission requires that decision.                    |
| **3. Run**     | Will it finish, even when something fails? | Deployments of admitted versions to environments, and a durable execution engine on PostgreSQL. Workers the customer enrolls run each step inside their own boundary, and connections resolve credentials at run time.                       |
| **4. Control** | Who allowed this, and did it happen?       | Human tasks that hold the exact action and inputs until a person decides; a changed or expired approval authorizes nothing. Each outcome is verified and linked to the version, the action and the decision.                                 |
| **Foundation** | Who may do what?                           | Accounts, personal and organization workspaces, members and roles, invitations, scoped API keys, sign-in with an authenticator, the audit log, and platform-operator administration.                                                         |

## Where each step stands

| Step       | Built                                                                                                                                                                                               | Still to build                                                                                                                                                                                                                                                    |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Build      | Executable-v2 package format and SDK (validate, compile, sign, diff); publication and admission of executable packages; a Studio that composes the older v1 format                                  | Studio authoring of executable-v2, then retiring v1 (ADR-0013); a first reference workflow built end to end                                                                                                                                                       |
| Prove      | Scenarios, environments, runners with the HTTP adapter, evaluation runs, policies, releases, release approvals, the release gate command and evidence export                                        | Evaluating a workflow package version itself, not only an agent behind HTTP; the provider that binds a package version to its release decision at deployment                                                                                                      |
| Run        | Durable execution engine: deployments, executions, waits, signals, pause, resume, cancel, reconciliation and restore holds; runtime enrollment and activities; connection and secret-reference APIs | Control Room pages for deployments, executions and workers; providers proven against live systems (the runtime ships `https-json` and `anthropic-messages`, tested only against local stand-ins); engine throughput beyond a few admissions a second (finding 11) |
| Control    | Release approvals by a different administrator                                                                                                                                                      | Human tasks and exact-action approvals (E05, plan in PR #16); cases and verified outcomes (E06)                                                                                                                                                                   |
| Foundation | All of the foundation above                                                                                                                                                                         | A first hosted installation; working CI (GitHub Actions refuses jobs over billing)                                                                                                                                                                                |

## Not part of the product

These were removed, or are not planned, because they serve none of the four steps:

- Monitoring agents that run outside Lotuspond: trace ingestion, monitors, incidents and their alerts ([ADR-0013](architecture/0013-trim-to-the-automation-core.md)).
- Voice, software sandboxes, the model gateway, trace-plan billing, the Go collector and the Rust verifier ([ADR-0012](architecture/0012-retire-assurance-extras.md)).
- Air-gapped operation, offline identity enrollment, organization-wide and installation-wide authenticator rules, the tool-authorization endpoint, MCP and A2A evaluation runners, the Operations page and the on-premises Compose variant ([ADR-0014](architecture/0014-focus-on-the-core-loop.md)).
- Commercial editions and usage metering, delegated partner access, enterprise federation, multi-topology qualification, industry pack libraries, and the services operating model. These are business decisions for later, not product features; the delivery plans keep them as history.

## How to keep it simple

- One package format. Executable-v2 is the only format that gains features.
- A feature that serves no step of the loop is not built. A planned step gets one honest placeholder page and nothing more.
- Evidence states stay separate: a local test, a hosted run and a customer deployment are different claims.
- Removing a feature never removes stored data. Its tables stay until a reviewed, data-preserving plan drops them.
