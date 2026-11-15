# What LotusPond Works is for

Status: the product's direction, set on 2026-11-11 by [ADR-0014](architecture/0014-focus-on-the-core-loop.md); the product's name, LotusPond Works, was set on 2026-11-12 by [ADR-0015](architecture/0015-name-the-product-lotuspond-works.md); who the product is for, starting with supply chain operations, was set on 2026-11-13 and 2026-11-14 by [ADR-0017](architecture/0017-position-works-for-supply-chain-operations.md). Every feature must serve one step of the core loop below, or the foundation beneath it. Anything else is removed or not built. [CAPABILITIES.md](CAPABILITIES.md) records what each step can do today, and [VALIDATION.md](VALIDATION.md) the evidence for it.

## Who it is for

LotusPond Works automates the operational work of a business. Its first market is supply chain operations: the day-to-day work of buying, storing, moving and delivering goods. Supply chain is the first market, not the only one. Healthcare administration, such as referral intake and appointment operations, is also work LotusPond Works automates.

It is built to each customer's requirement, in two ways. LotusPond configures a customer once and builds the systems that customer needs. The customer's own builders can also build the systems they need themselves. Both happen inside the product, through the same Build step: the customer's systems are connected, agents are built, and the processes that customer chooses are automated. The systems are whatever the customer's operations already run on. These documents name them by kind and never by vendor: for example an ERP (the system that holds a company's orders, stock and accounts), a warehouse system, or a carrier portal (a transport company's site for booking and tracking shipments).

It is not for a single process or a single function, such as invoices or accounts payable (the team that pays suppliers). One customer may automate many processes, and two customers may automate different ones.

This says who the core loop below is for. It does not change the loop, and it adds no capability: what is built is still what [CAPABILITIES.md](CAPABILITIES.md) records. [ADR-0017](architecture/0017-position-works-for-supply-chain-operations.md) keeps the owner's own words, how they settle earlier decisions, and the two points still open.

LotusPond, the company, has two products. LotusPond Works is the one in this repository. LotusPond Health is the other and is not built here. It is a standard product, the same for every customer, and is not built to a customer's requirement. Its direction is one API and MCP server for health records, so that a company or an AI agent does not have to connect to each records system itself. (MCP, the Model Context Protocol, is a standard way for an AI agent to reach outside data and tools.)

## The problem

Companies can build an AI agent in an afternoon. They cannot yet trust one inside a real business process, such as matching a supplier's invoice, changing an order or updating a stock record, for four reasons:

1. **It does not run reliably.** An agent stops halfway, retries blindly, and leaves half-done work in the systems it touched.
2. **Nobody can prove a change is safe.** A new prompt, model or tool silently breaks a process that worked yesterday.
3. **Nobody is in charge of what it does.** A payment, refund or record change happens without a named person deciding.
4. **Nobody can say what happened.** When something goes wrong, there is no record of which version acted, on what input, with whose approval.

## What LotusPond Works does

**LotusPond Works runs AI-agent workflows inside a company's real processes: each version is proven before it is released, each consequential action waits for a person's decision, and every outcome is recorded.**

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

| Step       | Built                                                                                                                                                                                                                                                            | Still to build                                                                                                                                                                                                                                                    |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Build      | Executable-v2 package format and SDK (validate, compile, sign, diff); publication and admission of executable packages; a Studio that composes the older v1 format                                                                                               | Studio authoring of executable-v2, then retiring v1 (ADR-0013); a first reference workflow built end to end                                                                                                                                                       |
| Prove | Scenarios, environments, runners with the HTTP adapter, evaluation runs, policies, releases, release approvals, the release gate command and evidence export; package evaluation runs that run a package version on its scenarios (reviews recorded, never approved) and the release binding provider, so a release admits exactly the package version it proved | A separate evaluation environment (today a package is evaluated through an evaluation deployment in the production environment; a separate one needs a reviewed migration); console pages for package evaluations |
| Run        | Durable execution engine: deployments, executions, waits, signals, pause, resume, cancel, reconciliation and restore holds; runtime enrollment and activities; connection and secret-reference APIs                                                              | Control Room pages for workers, queues and deployment changes (deployments and executions are shown, with pause, resume and cancel); providers proven against live systems (the runtime ships `https-json` and `anthropic-messages`, tested only against local stand-ins); engine throughput beyond a few admissions a second (finding 11) |
| Control    | Release approvals by a different administrator; reviewed actions and human tasks (E05): immutable action intents, review tasks for people, signed write commands, read-back of uncertain writes and verified outcomes, tested locally against a synthetic target; the operator command key and installed connector catalog that switch them on in production (`authority command-key`, `install-connector`), and the runtime's operator write providers; verified outcomes (E06, first part): each execution's trace from input to version, action, decision, read-back and outcome, its business outcome with explicit uncertainty, a digest-carrying export, outcome counts, shown in Control Room; cases (E06, second part, schema v17): business items named by an encrypted key, their executions, and a person who owns and resolves each one that needs it | A write to a real customer system; command key rotation without a restart; notifications for tasks and cases                                                                                                  |
| Foundation | All of the foundation above                                                                                                                                                                                                                                      | A first hosted installation; working CI (GitHub Actions refuses jobs over billing)                                                                                                                                                                                |

## The reference workflow

A reference workflow is one worked example, built end to end, that shows the core loop on a real kind of process. The first is invoice exception triage. A supplier's invoice is compared with the purchase order (what the company ordered) and the goods receipt (what arrived). When they do not match, an agent investigates and proposes what to do, and a person approves that exact action before it is sent. Its package, runtime providers, server seams and reviewed actions were merged on 2026-11-13 (PR #28 and PR #30), against a synthetic stand-in system and not a customer's. Its package proof and its review and Control Room pages followed on 2026-11-15.

A second, late purchase order recovery, is specified ([specification](superpowers/specs/2026-11-14-late-purchase-order-design.md)): it spans an ERP, a carrier and a customer inbox, and asks two people to approve two actions. The owner chose on 2026-11-14 to build it before a customer brings a process ([ADR-0017](architecture/0017-position-works-for-supply-chain-operations.md)).

Each is an example, not the product. LotusPond Works is not an invoice tool or an accounts-payable tool. The example was chosen because it tests every promise this document makes, and its specification says the first customer's own process replaces it or joins it.

## Not part of the product

These were removed, or are not planned, because they serve none of the four steps:

- Monitoring agents that run outside LotusPond Works: trace ingestion, monitors, incidents and their alerts ([ADR-0013](architecture/0013-trim-to-the-automation-core.md)).
- Voice, software sandboxes, the model gateway, trace-plan billing, the Go collector and the Rust verifier ([ADR-0012](architecture/0012-retire-assurance-extras.md)).
- Air-gapped operation, offline identity enrollment, organization-wide and installation-wide authenticator rules, the tool-authorization endpoint, MCP and A2A evaluation runners, the Operations page and the on-premises Compose variant ([ADR-0014](architecture/0014-focus-on-the-core-loop.md)).
- Commercial editions and usage metering, delegated partner access, enterprise federation, multi-topology qualification, industry pack libraries, and the services operating model. These are business decisions for later, not product features; the delivery plans keep them as history.

## How to keep it simple

- One package format. Executable-v2 is the only format that gains features.
- A feature that serves no step of the loop is not built. A planned step gets one honest placeholder page and nothing more.
- Evidence states stay separate: a local test, a hosted run and a customer deployment are different claims.
- Removing a feature never removes stored data. Its tables stay until a reviewed, data-preserving plan drops them.
