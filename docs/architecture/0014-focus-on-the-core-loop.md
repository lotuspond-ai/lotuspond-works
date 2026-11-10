# ADR-0014: Focus Lotuspond on its core loop

Date: 2026-11-11. Status: **accepted. On 2026-11-11 the owner asked for a clean slate: one clear direction for what Lotuspond builds, and only the features that make it a complete product. The owner delegated the decisions to Claude.** It builds on [ADR-0012](0012-retire-assurance-extras.md) and [ADR-0013](0013-trim-to-the-automation-core.md).

This record:

- makes [GOAL.md](../GOAL.md) the single statement of the problem, the product, its core loop (Build, Prove, Run, Control, on a shared foundation) and what is out of scope;
- retires the air-gapped operation and offline identity parts of [ADR-0003](0003-offline-identity-and-cost-provenance.md), [ADR-0007](0007-customer-execution-distribution.md) and [ADR-0009](0009-agent-automation-platform.md), and ADR-0013's instruction to keep offline enrollment and the authenticator requirements;
- removes commercial editions, delegated partner access, enterprise federation, multi-topology qualification, industry pack libraries and the services operating model from the product's scope. They stay in the delivery plans as history, not as planned features.

## Context

Over two months the repository accumulated features for an enterprise rollout that has not started: air-gapped bundles, operator-attested offline identity, organization-wide security rules, several agent protocols and an operations dashboard. Each was built and tested to a high bar, and each made the product harder to understand. None of them is a step a first customer needs in order to build a workflow, prove it, run it and control its actions.

## Decision

Retire these features:

| Feature                            | Retired                                                                                                                                                                                                                                                                                             |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Air-gapped operation               | `LOTUSPOND_NETWORK_MODE`; the offline image bundle and its drill (`deploy/airgap/`, `deploy/checks/airgap-smoke.py`, `verify_airgap_bundle.py`, CI `offline-install`); the customer execution archive (`deploy/execution/build-bundle.sh`, `verify-bundle.sh`). The execution image itself stays.   |
| Offline identity enrollment        | `offline_identity.py`, `offline_provision.py`, `npm run offline-identity`, `/auth/offline-enrollment/*`, the Enrollment page; an operator attestation no longer verifies an identity.                                                                                                               |
| Authenticator requirements         | The installation-wide `LOTUSPOND_REQUIRE_MFA` and the organization-wide requirement with its Settings card. Personal authenticators, recovery codes, the administrator-authorized reset for a member who lost every factor, and the mandatory authenticator for production platform operators stay. |
| Tool authorization                 | `POST /authorize` and a policy's blocked and approval tool lists. Approving exact actions is E05's human tasks.                                                                                                                                                                                     |
| MCP and A2A runners                | `runner-mcp`, `runner-a2a` and their protocol clients. The HTTP runner and the release gate stay.                                                                                                                                                                                                   |
| Operations page                    | `/admin/operations`, the Operations tab and Redis worker-poll observations. Redis stays for rate limiting.                                                                                                                                                                                          |
| Deployment variants and pilot docs | `deploy/compose.onprem.yaml`, the pilot programme and offline identity pages of the docs site.                                                                                                                                                                                                      |

Keep the stored data and the schema, as ADR-0012 and ADR-0013 do:

- `offline_identity_attestations`, `offline_enrollments` and `workspaces.require_mfa` keep their rows, columns and constraints, and `backend/schema.sql` stays byte-identical.
- An account with a stored, unfinished offline enrollment stays blocked from signing in and from key authority, as before. With the completion route retired, such an account can no longer finish enrollment and stays blocked. This fails closed.
- Policies keep their `blocked_tools` and `approval_tools` columns; new policies store empty lists.

## Consequences

- Identity is verified only by mailbox. An account whose only verification was an operator attestation loses organization and operator authority until it verifies its mailbox. The capability register records no customer use.
- An organization that turned on the authenticator requirement no longer has its members held to it, and an installation's `LOTUSPOND_REQUIRE_MFA` is ignored. This is a deliberate reduction in scope, not a weakening of any remaining check: personal authenticators, the lost-factor reset and the production-operator requirement work as before.
- Mail is the only way to verify a mailbox, recover a password or accept an invitation, so a hosted installation needs an authorized SMTP service.
- The service bundle is one Compose file. The execution image runs `runner-http` and `gate`.
- Unmerged work is handled outside this record. `claude/control-transport-reuse`, whose measurement was inconclusive, is not merged. `claude/e04-cursor-redesign` (finding 11) is held: it raises engine throughput for scale, its own finding is still open, and it needs rebasing onto ADR-0012 and ADR-0013 before it can be validated.
- History in `docs/VALIDATION.md`, the plans and earlier records keeps describing these features as they were tested; it is not current scope.
