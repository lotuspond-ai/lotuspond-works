# ADR-0013: Trim the console to the automation core

Date: 2026-11-10. Status: **proposed; awaiting the owner's decision.** It builds on [ADR-0012](0012-retire-assurance-extras.md), which is still under review in PR #20, and applies only after that PR merges.

If accepted, this record:

- narrows the [ADR-0009](0009-agent-automation-platform.md) instruction to keep "approvals, production evidence, and governance": release approvals and stored production evidence stay, but monitoring of agents running outside Lotuspond goes;
- narrows ADR-0012's list of assurance contracts that stay in force: receipts, monitors, incidents and alerts leave it, approvals are limited to release approvals, and traces stay;
- retires the monitoring, incident-alert and endpoint-registration parts of [ADR-0002](0002-agent-assurance-platform.md), and the evaluation and alert worker roles of [ADR-0005](0005-operational-observations.md);
- makes executable-v2 the only package format the product develops further.

## Context

ADR-0009 defines the product as one licensable platform to build, deploy, run and govern workflows that combine agents, deterministic actions and human tasks. Its capabilities are the Studio, Control Room, execution, connections, assurance, business operations and administration. On 2026-11-10 a signed-in walk-through of every console page at `origin/main` (`22708f1`) covered a sidebar that defines 25 entries. Three of them, Connections, Control Room and Human tasks, are the core and are still Planned placeholders. Thirteen came with the agent-testing product that preceded the platform: the eight Assure entries, Traces, Monitors, Incidents, Voice sessions and Integrations. ADR-0012 retires the six components that nothing used. Checked against PR #20's branch (`1e87593`), the features below are also unused by the automation code, or duplicate something the platform plan already assigns elsewhere:

| Feature                                         | What the code shows                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Integrations                                    | `POST /integrations` stores a name, a kind (`mcp`, `a2a`, `telemetry`, `webhook`, `ci`) and an HTTPS endpoint with status `unverified` (`workflows.py:184`). No module outside `models.py` and `workflows.py` reads the table, and nothing changes the status. The Planned Connections page describes the real version of this feature.                                                                                                                                                                                                                                                                                                           |
| Receipts                                        | `POST /receipts` and `npm run cli -- receipt` store a CI outcome against a release (`workflows.py:657`, `scripts/cli.mjs:25`). The only reader copies them into the release evidence export (`workflows.py:636`). The release gate never reads them. Verified business outcomes are E06's planned work.                                                                                                                                                                                                                                                                                                                                           |
| Approvals for other actions                     | `POST /approvals` accepts any `action` name (`workflows.py:672`). Only `action == "release"` is ever read (`workflows.py:586`, `execution/assurance.py:148`), so an approval for any other action authorizes nothing. Approving exact actions is E05's Human tasks.                                                                                                                                                                                                                                                                                                                                                                               |
| Monitors, trace assessments and their incidents | Monitors sample customer traces into evaluation jobs, and `POST /traces/{id}/assess` queues one by hand (`monitoring.py:538`, `:231`). With no semantic evaluator after ADR-0012, the worker decides only producer-supplied assertions and returns **unknown** for everything else (`worker.py:234`). Every assessment that does not pass opens an incident (`incident_service.py:102`), so a monitor with ordinary criteria opens an "assessment unavailable" incident for each sampled trace. The incidents then feed the signed alert outbox (`alerts.py`). None of `execution/` or `fleet/` reads monitors, assessments, incidents or alerts. |
| License page                                    | After ADR-0012 removes the plan card, the page shows only proposed editions, services and planned delegated access (`web/src/pages/License.tsx`). It describes no entitlement the workspace has, and the public page already presents services.                                                                                                                                                                                                                                                                                                                                                                                                   |
| Workflow-package v1                             | The Studio publishes v1 packages to `POST /projects/{id}/packages` (`packages.py:188`). The runtime admits only executable-v2 packages from `/executable-packages` (`execution/routes.py:141`, `execution/admission.py:151`). The only authoring path in the console therefore produces packages that cannot be deployed.                                                                                                                                                                                                                                                                                                                         |

The same check showed which assurance features the automation core depends on. Deployment admission and activity budgets call `check_assurance_current` (`execution/admission.py:90`, `execution/budgets.py:239`). It reloads the bound release, its policy, candidate and baseline runs, decision, and approved `release` approvals by a different administrator (`execution/assurance.py:144`). Execution and fleet scopes are bound to environment records (`execution/authorization.py:96`, `fleet/management.py:65`). Those features stay.

## Decision

Retire these features:

| Feature                        | Retired                                                                                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Integrations                   | The Integrations page and nav entry, `GET/POST /integrations`, the endpoint-registration rows on the API reference. The Planned Connections page stops pointing to Integrations as the thing to use today.                                                                                                                                                                                                             |
| Receipts                       | The Receipts page and nav entry, `POST /receipts`, the `receipt` CLI command.                                                                                                                                                                                                                                                                                                                                          |
| Approvals for other actions    | `POST /approvals` requires a `release_id` and the `release` action; any other action is refused with 400. The Approvals page describes release approvals only.                                                                                                                                                                                                                                                         |
| Trace monitoring and incidents | The Monitors and Incidents pages and nav entries; `/monitors`, `/traces/{id}/assess`, `/evaluation-jobs/{id}`, and the incident create, resolve and reopen routes; the assessment worker (`npm run worker`) and the alert worker (`npm run worker:alerts`); `LOTUSPOND_ALERT_*` settings; the evaluation and alert queues and worker roles on the operator Operations page; their Compose services and bundle entries. |
| License page                   | The License page and nav entry. The page comes back when E08 issues a real license. Proposed editions and services stay in the master plan and on the public page.                                                                                                                                                                                                                                                     |

Make executable-v2 the only package format, in this order:

1. No new v1 features. v1 validation, the v1 registry, the Studio and their tests keep working unchanged.
2. The next Studio increment authors executable-v2 and publishes through `/executable-packages`. The five Studio patterns get v2 equivalents in `contracts/examples/`, pinned by the same digest tests.
3. Once that ships, retire the Studio's v1 publishing, `POST /projects/{id}/packages`, the backend v1 compatibility modules (`package_contract.py`, `package_input.py`, `package_cli.py`) and `npm run package:validate`. That retirement gets its own record and plan.

Keep the stored data and the schema, as ADR-0012 does:

- The `integrations`, `receipts`, `monitors`, `evaluation_jobs`, `assessments`, `incidents`, `incident_events` and `alert_deliveries` tables keep their rows, columns and constraints. The ORM models stay, `backend/schema.sql` stays byte-identical and no migration runs. Dropping them needs its own reviewed, data-preserving plan.
- `GET /incidents` and `GET /incidents/{id}` keep returning existing incidents with their evidence links, so history stays readable through the API.
- The release evidence export keeps its `receipts` array, filled from stored rows, so its schema version 2 shape does not change.
- Existing approvals for other actions stay readable. They still authorize nothing.
- Queued evaluation jobs and alert deliveries are left as they are. Nothing claims them.
- v1 packages already published stay retrievable byte for byte through `GET /packages/{id}/content`.

Keep, and stop expanding without a qualified customer:

- Offline enrollment and operator attestation ([ADR-0003](0003-offline-identity-and-cost-provenance.md)), the organization-wide authenticator policy and administrator-authorized factor reset. They stay maintained and tested. New work on them waits for E11 or a qualified air-gapped Enterprise engagement. Personal authenticator MFA ([ADR-0004](0004-authenticator-mfa.md)) is the security baseline and is not affected.

Unchanged:

- Scenarios, Environments, Runners, Evaluation runs, Releases, Policies and release Approvals, because deployment admission depends on them. The Environments page copy should say that its records also scope deployments.
- Traces and OTLP ingestion. See the first open question.
- The three Planned pages, which describe the core.
- The changelog. Its wording is a content fix, not a scope decision.

## Consequences

- The sidebar drops from 25 entries (24 after ADR-0012) to 19: Home; Projects, Studio and Connections under Build; seven Assure entries; Control Room, Human tasks and Traces under Operate; and Members, Invitations, API keys, Audit log and Settings under Administer.
- No background worker is needed for assurance. The mail and execution workers remain, and the operator Operations page keeps only its mail queue and worker role.
- A workspace that relied on monitor incidents or alert webhooks loses them without a replacement until E05/E06 add incidents for Lotuspond's own executions. The capability register records no customer use, so no known installation loses behavior it depends on, and no stored data is removed.
- Until the Studio authors v2, the console has no way to build a deployable package. Executable packages are authored with the SDK's `lotuspond-package v2` commands. That gap exists today; this record names it rather than creating it.
- History in `docs/VALIDATION.md`, the plans and earlier records keeps describing these features as they were tested; it is not current scope.

## Open questions for the owner

1. **Traces.** Once monitors go, the only consumers of stored traces are the trace views, the trace export and the workspace `GET /overview` cost summary, which the console does not display. Should they stay as evidence for agents Lotuspond does not run, or retire too, with production evidence rebuilt from execution events under E06? This record keeps them, because it is the smaller and reversible step.
2. **Incident history in the console.** Is read-only API access to existing incidents enough, or should the console keep a read-only incident history view until E05/E06?
3. **One record or two.** Should the v1 retirement in step 3 stay in this record as a commitment, or move entirely to the record that accompanies the v2 Studio?
