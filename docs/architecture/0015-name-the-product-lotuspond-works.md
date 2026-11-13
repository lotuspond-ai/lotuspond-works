# ADR-0015: Name the product LotusPond Works

Date: 2026-11-12. Status: **accepted. On 2026-11-12 the owner asked for a product name based on the problem the product solves, chose LotusPond Works from a shortlist, and asked for the documentation and the landing page to use it.** It names the product that [GOAL.md](../GOAL.md) and [ADR-0014](0014-focus-on-the-core-loop.md) define; it changes no scope.

## Context

LotusPond is the company name and has more than one product: LotusPond Health and this platform. The console, the signed-out page and the documentation called this platform plain "Lotuspond", the same word as the company, so the platform could not be told apart from the company or from LotusPond Health.

The product runs AI-agent workflows inside a company's real processes: each version is proven before release, each consequential action waits for a person's decision, and every outcome is recorded.

## Decision

The product is **LotusPond Works**. The company name is written **LotusPond**, as in LotusPond Health.

- It follows the pattern of LotusPond Health: the company name and one plain word for the area the product serves. Health serves healthcare; Works serves the work of running a business.
- It answers the first problem in GOAL.md: today an agent stops halfway and leaves half-done work behind; this product's workflows finish.
- It matches the signed-out page's headline, "Agents at work, people in charge".

## Options considered

| Name                  | For                                                         | Against                                                                                                                                                           |
| --------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LotusPond Works       | Same pattern as LotusPond Health; short; matches the hero   | Says nothing about human approval, so the tagline carries it                                                                                                      |
| LotusPond Countersign | Names the differentiator: an agent proposes, a person signs | Covers only the Control step; US marks exist for antivirus, password-management and document-transmission software, and countersign.com is an e-signature product |
| LotusPond Waterline   | Fits the pond scene                                         | Needs explaining; Waterline Data holds a US software mark, and Waterline AI sells AI agents for water-risk compliance                                             |
| LotusPond Agents      | The most literal                                            | Names the technology, not the promise                                                                                                                             |
| Plain Lotuspond       | No change                                                   | Cannot be told apart from the company or from LotusPond Health                                                                                                    |

A keyword search of US federal marks on 2026-11-12 found no live LOTUSPOND or LOTUSPOND WORKS mark; the only LOTUSPOND filing (2008, interior design services) was abandoned in 2009. It was not a clearance search and did not cover other registers.

## What changes and what does not

Changed: the console and the signed-out page (the wordmark lockup in `web/src/components/Wordmark.tsx`, browser titles and copy), the current documentation (README, GOAL, CAPABILITIES, API, DATABASE, the design documents, the documentation site and the agent guides) and the changelog.

Unchanged, on purpose:

- Identifiers: the `lotuspond`, `lotuspond_sdk` and `lotuspond_runtime` packages, the `lotuspond-*` commands, `LOTUSPOND_*` environment variables, `X-Lotuspond-*` headers, database and schema names, the repository, and the `"product": "Lotuspond"` value in API responses and evidence exports. Renaming them would break installed runtimes, keys, scripts and stored evidence for no customer benefit.
- Dated records: plans, earlier ADRs, validation and research records keep the name they were written with.
- The early-access page in `deploy/interest/`, which speaks for the company and names no product.

Not yet renamed: text the server and command-line clients produce, such as mail subjects and bodies, the authenticator account label, error messages, the OpenAPI title and the CI workflow names. The console and the documentation quote the authenticator label ("Lotuspond") exactly until it changes. Changing that label affects new enrollments only; existing authenticators keep their label and their codes still verify.

## Consequences

New public copy, pages and documentation use "LotusPond Works" for the product and "LotusPond" for the company. Code keeps the `lotuspond` identifier.
