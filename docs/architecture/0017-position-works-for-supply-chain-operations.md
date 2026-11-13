# ADR-0017: Position LotusPond Works for supply chain operations

Date: 2026-11-13. Status: **accepted. On 2026-11-13 the owner restated who LotusPond Works is for while reviewing a new company landing page, and asked for it to be recorded in the product documents.** It says who the core loop of [GOAL.md](../GOAL.md) and [ADR-0014](0014-focus-on-the-core-loop.md) is for. It does not change the loop, and it adds no capability. The points where it differs from earlier decisions are listed below and are not settled here.

Number 0016 is held by the design-system record on the unmerged branch `claude/design-system-sage`.

## Context

Until this record, GOAL.md said what the product does and named its four steps (Build, Prove, Run, Control), but not who it is for. It said only "a company's real processes", with invoices, refunds and customer records as examples.

The one worked example is the first reference workflow, invoice exception triage (draft PR #28). A reference workflow is a single process built end to end to show the loop working. With only that example in view, the product can be read as an invoice or accounts-payable tool. Accounts payable is the team that pays suppliers.

LotusPond, the company, has two products ([ADR-0015](0015-name-the-product-lotuspond-works.md)): LotusPond Works, in this repository, and LotusPond Health, which is not built here.

The owner's words on LotusPond Works, 2026-11-13:

> LotusPond works should not focus on a single thing. LotusPond works should be for the supply chain. We are going to build systems based on the customer's requirement, like AI systems. It should not be for a single thing, like invoice or accounts payable. It should be for multiple things that can be automated in enterprises by using our software, like building agents in the software, connecting their systems within our product, and automating them.

## Decision

1. **LotusPond Works is for supply chain operations**: the day-to-day work of buying, storing, moving and delivering goods.
2. **It is built to each customer's requirement.** Inside the product, a customer's own systems are connected, agents are built, and the processes that customer chooses are automated.
3. **It is not for a single process or function**, such as invoices or accounts payable. One customer may automate many processes.
4. **Invoice exception triage stays the reference workflow.** It is one worked example and not the product. Its specification already says the first customer's own process replaces it or joins it.
5. **The core loop is unchanged.** Every feature still serves Build, Prove, Run or Control, or the foundation beneath them. This record says who the loop is for.

At company level: LotusPond Health is the sibling product. Its direction is one API and MCP server for health records, so that a company or an AI agent does not have to connect to each records system itself. MCP, the Model Context Protocol, is a standard way for an AI agent to reach outside data and tools. Nothing of LotusPond Health is built or specified in this repository.

## What changes and what does not

Changed:

- [GOAL.md](../GOAL.md) gains "Who it is for" and "The reference workflow". Its problem statement now gives supply chain examples.
- The README says who the product is for in one sentence.
- [CAPABILITIES.md](../CAPABILITIES.md) notes the positioning as direction, above its table. No row changes.

Unchanged, on purpose:

- The core loop, the scope ADR-0014 set, and every item under "Not part of the product" in GOAL.md.
- Every row of CAPABILITIES.md. Positioning is direction. No supply chain workflow, connection to a customer's system or customer deployment is built or qualified because of this record.
- Dated records: earlier ADRs, plans, research and validation records keep their text.
- `CLAUDE.md`, `DESIGN.md`, `docs/design-system.md` and the console and landing page under `web/`. Another unmerged branch is editing them, so their wording waits for the owner.

## Open questions for the owner

The positioning differs from these earlier decisions. Each earlier text stays as written until the owner chooses.

| Earlier decision                                                                                                                                                                                                                                                                                                                                               | How the positioning differs                                                                                                                                                       | What the owner has to choose                                                                                                                             |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [ADR-0009](0009-agent-automation-platform.md), the [master plan](../plans/2026-10-13-platform-master-plan.md) (sections 1 and 6) and CAPABILITIES.md describe one platform "across industries", with six workflow families that include support, onboarding and voice. The landing page's Workflows section and `docs/design-system.md` repeat those families. | The owner now says the product "should be for the supply chain".                                                                                                                  | Whether supply chain is the whole market for LotusPond Works or the first one, and whether the six families stay in public copy.                         |
| ADR-0014 and GOAL.md put "the services operating model" outside the product's scope, as a business decision for later. ADR-0009 lets a customer use the product alone, engage LotusPond, or appoint a partner.                                                                                                                                                 | "We are going to build systems based on the customer's requirement" reads as LotusPond doing the building. "By using our software" reads as the customer's own builders doing it. | Who builds a customer's system (LotusPond's team, the customer's builders, or both), and whether that delivery work comes back into the product's scope. |
| ADR-0015 explains the name with "Works serves the work of running a business".                                                                                                                                                                                                                                                                                 | Supply chain operations is narrower than running a business.                                                                                                                      | Nothing about the name. Only whether that sentence should be read as superseded by this record.                                                          |
| ADR-0009 names a healthcare-administration workflow as a possible reference workflow for this product, and the landing page says "Healthcare starts with administrative work such as referral intake and appointment operations".                                                                                                                              | LotusPond Health is now the company's healthcare product, and LotusPond Works is for the supply chain.                                                                            | Whether LotusPond Works still offers healthcare administration workflows, or leaves healthcare to LotusPond Health.                                      |
| The reference workflow is an accounts-payable process, and its specification scopes itself to one workflow.                                                                                                                                                                                                                                                    | The owner named "invoice or accounts payable" as the single thing the product should not be.                                                                                      | Whether invoice exception triage stays the only reference workflow until a customer brings a process, or a second supply chain example is built first.   |
| The landing page and its design rules lead with "enterprise agent automation" in "real business processes", offered "as a platform and as services" (`web/src/pages/Landing.tsx`, `web/index.html`, `DESIGN.md`).                                                                                                                                              | They do not say supply chain, and they speak for the company and the product at once.                                                                                             | The wording of the product page, once the new company landing page is settled.                                                                           |

[AGENT_PRODUCT_RATIONALE.md](../AGENT_PRODUCT_RATIONALE.md) lists the first task and its buyer as an assumption the owner has to clarify, and says its order example is not a decision about the first market. This record names the market. The first buyer, their systems, and the outcome they will accept are still open.

## Consequences

- New documents and public copy may say who LotusPond Works is for: supply chain operations, built to each customer's requirement.
- Public copy must still trace to CAPABILITIES.md. It may not present a supply chain workflow, a connection to a kind of system, or a customer as available until the register records it.
- Documents name a customer's systems by kind, not by vendor, and invent no customer, number or logo. Examples of kinds: an ERP (the system that holds a company's orders, stock and accounts), a warehouse system, or a carrier portal (a transport company's site for booking and tracking shipments).
- LotusPond Health is mentioned only as the company's other product. Its features are not described here.
