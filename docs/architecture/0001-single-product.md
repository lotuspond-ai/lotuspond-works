# ADR-0001: One Lotuspond application and data model

**Status:** Superseded by ADR-0002 on 2026-09-21; retained as the decision record for the initial local rebuild
**Date:** 2026-09-19
**Decider:** Product owner request for a fresh, single-product implementation

## Context

The product needs four connected operating workflows: release decisions, scenarios, production evidence, and governance. Reusing a former implementation or its research is outside scope. Local work must remain recoverable, and existing remote data must not be destroyed as a side effect of changing source.

## Decision

Use one new repository with a React application, a TypeScript/Fastify API, and a fresh PostgreSQL relational schema. One server serves the built application and same-origin API. SQL defines the new database directly, with explicit tenant-bound foreign keys and no compatibility table mappings. API routes start at `/api/v1`; sessions and API keys authenticate independently.

The current Mac workspace path remains stable for editor access. Its contents become the new project; recovery material is kept outside that directory. There is no inherited Git history or deployment remote in the new repository.

## Options considered

| Option                                      | Complexity                             | Operating cost                        | Consequence                                                                |
| ------------------------------------------- | -------------------------------------- | ------------------------------------- | -------------------------------------------------------------------------- |
| One application and one relational database | Low initial deployment complexity      | One application host and one database | Shared transactions make evidence and authorization easier to reason about |
| Separate frontend and domain services       | Higher routing and identity complexity | Multiple services                     | Useful when measured scale or independent ownership warrants it            |

## Consequences

- Application code, resource names, assets, documentation, local tooling, and database models use one product identity.
- Runs freeze their scenarios. Decisions retain the policy, results, comparison, approval evidence, and a deterministic hash.
- Worker leases and result submission are transactional; tenant relationships cannot cross workspace boundaries.
- The local schema is independent of all hosted schemas. Moving data or cutting over deployments requires its own inventory, validation, and approval.
- Enterprise adapters and operational hardening must be completed before a hosted production readiness claim. See the explicit capability register.

## Cutover and recovery

Local cutover consists of an external archive, verified preservation of repository states, and installation of the tested new project. Remote consumers and deploy branches remain attached to their existing systems. No remote consumer is silently redirected. Recovery is performed from the external archive after first preserving any new work. Retirement of external resources is a separately authorized operation.
