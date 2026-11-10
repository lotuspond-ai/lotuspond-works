# ADR-0012: Retire six assurance-era components

Date: 2026-11-05. Status: **accepted. The owner asked on 2026-11-05 to drop the Gym sandbox, voice, the Jev evaluator, the model gateway, trace-plan billing, and the Go collector and Rust evidence-core.**

This record:

- supersedes the [ADR-0009](0009-agent-automation-platform.md) instruction to keep using the Go collector, Rust evidence tools, voice bridge and isolated software evaluation tools;
- retires the voice, Gym-Anything, TypeSafe Jev and model-gateway parts of [ADR-0002](0002-agent-assurance-platform.md), the collector metrics of [ADR-0005](0005-operational-observations.md), and the Gym kit and voice command of [ADR-0007](0007-customer-execution-distribution.md);
- leaves every other assurance contract in force: tenancy, identity, scenarios, runs, release policies, approvals, receipts, traces, monitors, incidents and alerts.

## Context

These components arrived with the agent-testing product that preceded the automation platform (AG2 in the recovery archive), and were rebuilt in the September 2026 baseline (`3f91bf0`). On 2026-11-05 none of them had changed since that baseline. The automation code (`execution/`, `fleet/`, packages) used none of them; it depends on the baseline only for `models`, `security`, `deps` and `policy`. Each one tested only against mocks or one synthetic task, and the collector, verifier, voice bridge and Gym kit each needed their own toolchain and CI job.

## Decision

Remove these components:

| Component | Removed |
| --- | --- |
| Voice | `voice-bridge/`, the `/voice/sessions` routes, voice assessment in the worker, the Voice sessions page, the `voice` execution command |
| Gym-Anything sandbox | `sandbox/`, `evaluation/gym_anything.py`, the Gym image and host kit in the execution archive, the runner's Gym variables |
| TypeSafe Jev | `evaluation/jev.py`, the `typesafe-sdk` dependency, `LOTUSPOND_TYPESAFE_*` settings |
| Model gateway | `gateway.py`, `/gateway/*`, the `gateway` key scope for new keys, `LOTUSPOND_PROVIDER_*` settings, the workspace monthly model budget |
| Trace-plan billing | `entitlements.py`, the monthly trace allowance, the `retention` job, `PATCH /admin/workspaces/:id/plan`, `billing` in `/settings`, the plan card on Settings and License |
| Go collector and Rust evidence-core | `collector/`, `evidence-core/`, `deploy/monitoring/`, the collector service, image and bundle entry, the `collector`, `monitoring-rules`, `voice` and `evidence` CI jobs |

Keep the stored data and the schema:

- `voice_sessions`, `voice_segments`, `gateway_usage`, `workspaces.plan` and `workspaces.monthly_budget_usd` keep their rows, columns and constraints. The ORM models stay, so `backend/schema.sql` is byte-identical and no migration runs. Nothing writes these records any more. Dropping them needs its own reviewed, data-preserving plan.
- Workspace responses omit `plan` and `monthly_budget_usd`, so a retired value is never reported as if it applied.
- An evaluation job queued for a voice session before this change completes as **unknown** with the reason "Voice evidence is no longer assessed." Incidents keep their `voice_session_id`, and the incident detail still returns the linked session.
- Existing API keys that carry the `gateway` scope keep it; it grants nothing.
- Scenario modality labels, including `voice`, are unchanged: they describe what a scenario tests, and the database constraint allows them.

## Consequences

- Traces arrive only through `POST /api/v1/traces` and the API's `/api/v1/telemetry/otlp`, in JSON or protobuf, with or without gzip. Producers keep their own retry queue; a repeated span replays idempotently.
- With no semantic evaluator, monitors and manual assessments decide only what the deterministic assertions can; every other criterion is **unknown**, never passed.
- There is no trace allowance and Lotuspond deletes no trace on its own. Commercial licensing remains E08's planned work.
- The service bundle has three images (API, PostgreSQL, Redis) and its drill four containers; the execution archive has one image with `runner-http`, `runner-mcp`, `runner-a2a` and `gate`.
- The public page no longer mentions voice or software-task agents, the collector image or airgapped cloud model calls. The planned "Voice and conversation" workflow family from the master plan is a separate, future automation scope and stays.
- History in `docs/VALIDATION.md`, the plans and earlier ADRs keeps describing these components as they were tested; it is not current scope.
