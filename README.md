# Lotuspond

Lotuspond is being developed as a licensable enterprise agent automation platform, with transformation, implementation, training, and managed services across industries. Customers will be able to build, deploy, operate, and govern workflows combining agents, deterministic actions, and human tasks, using their own team, Lotuspond, or an implementation partner.

The implemented core tests AI agents and governs releases through Scenarios, Releases and Governance, and deployment admission requires a current release decision. The primary API is Python/FastAPI with a fresh PostgreSQL model; the console is React. Node tooling runs customer-controlled evaluation adapters and release gates.

The first expansion slice is a strict workflow-package v1 JSON contract with local validation, canonical content digests, schema export, and tests. The registry adds immutable, tenant-scoped package storage with admission-validator metadata. A standalone Python SDK shares the v1 validator and canonical content behavior, with local differences, detached signatures against independent trust, and exact synthetic capability checks. The console Studio composes a v1 package as a graph, checks it against the same contract in the browser and publishes it to that registry. Production runtime, executable-v2 authoring in the Studio, hosted publisher trust, commercial licensing, and qualified industry packs remain planned. See the [platform decision](docs/architecture/0009-agent-automation-platform.md), [product and services plan](docs/plans/2026-10-13-platform-master-plan.md), and [capability register](docs/CAPABILITIES.md) for the boundary between current code and the target product.

## Run locally

Install Node.js 24 LTS (the reviewed version is in `.node-version`), Python 3.13+, uv, and PostgreSQL tools (`initdb`, `pg_ctl`, `psql`, `createdb`). Node 22.12+ remains compatible with the execution clients.

```sh
npm ci
uv sync --frozen --project backend
npm run db:start
npm run db:init
npm run build
LOTUSPOND_REGISTRATION_ENABLED=true npm start
```

Open http://127.0.0.1:8000. Choose an individual account or deliberately create an organization. Every account has a personal workspace; organizations contain projects and role-based memberships. Public registration never grants platform operator access.

`db:start` creates only the independent `lotuspond_platform_dev` database in the localhost cluster on port 55439. `db:init` refuses nonempty databases. Existing data is not imported or reset. Reviewed additive upgrades from local schema versions 2–11 to version 12 use `npm run db:migrate`; read [the migration plan](docs/DATABASE.md) first.

For development, run `LOTUSPOND_REGISTRATION_ENABLED=true LOTUSPOND_ORIGIN=http://127.0.0.1:3001 npm run dev` and `npm run dev:web` in separate terminals. The UI runs on port 3001 and proxies the API on port 8000. Optional settings belong in the untracked project `.env`; use `.env.example` as the template.

Configure [identity mail](docs-site/docs/identity-mail.md) and run `npm run worker:mail` separately for verification, recovery, and invitations; mail is disabled by default.

Air-gapped installations use [offline identity enrollment](docs-site/docs/offline-identity.md) to provision new individuals, deliberate organizations and explicit operators. A one-use link lets each person set their own password. Operator attestation stays separate from mailbox verification; pending-link reissue and account revocation are audited CLI operations.

## Work through a release

1. Create a project, scenarios, and an immutable release policy.
2. Register a runner and issue an API key with `read` and `run` scopes.
3. Queue a run. Execute real evaluations through `npm run runner -- /absolute/path/to/adapter.mjs`.
4. Create a release with the candidate revision, policy, and optional comparable baseline.
5. Evaluate it. Missing or unknown evidence, critical failures, regressions, and latency/cost breaches block release.
6. If required, request the `release` approval action and have a different administrator approve it. Re-evaluate and export the evidence.
7. Enforce the result in your delivery process with `npm run cli -- gate RELEASE_ID`.

The [protocol adapters](protocol-adapters/README.md) evaluate an operator-selected HTTP or A2A agent or execute an explicit MCP tool, with read-only tool discovery kept separate. The package has its own lockfile; run `npm ci --ignore-scripts` in it before using it. Its local tests use synthetic endpoints.

Voice sessions, Gym-Anything sandboxes, the TypeSafe Jev evaluator, the model gateway, trace-plan billing, the Go collector and the Rust verifier were retired on 2026-11-05; see [ADR-0012](docs/architecture/0012-retire-assurance-extras.md). Trace ingestion, monitors and assessments, incidents and their alerts, receipt submission, endpoint registration and the License page followed on 2026-11-11; stored traces, incidents and receipts stay readable through the API. See [ADR-0013](docs/architecture/0013-trim-to-the-automation-core.md).

## Validate

```sh
npm run check
createdb -h 127.0.0.1 -p 55439 -U lotuspond lotuspond_platform_test
LOTUSPOND_PLATFORM_TEST_DATABASE_URL=postgresql+psycopg://lotuspond@127.0.0.1:55439/lotuspond_platform_test npm run test:python
```

Create the test database only if it does not exist. Integration tests create and remove only their own random schemas. Protocol-adapter, restore, execution-image, offline-install and documentation checks have separate CI jobs. [Current validation](docs/VALIDATION.md) distinguishes observed results from remaining work.

## Validate a workflow package locally

The independently installable [Python SDK](sdk/python/README.md) provides
`lotuspond-package` commands for validation, digest, canonical file output, diff,
key generation, detached signing/verification and synthetic compatibility, plus
their Python interfaces, without the API or database. File outputs are exclusive;
trust and capability inputs are explicit, and local reports grant no authority.
Build its wheel and sdist with
`uv build --project sdk/python`. The [SDK guide](docs-site/docs/package-sdk.md)
describes installation, bounds, independent trust and the v1-only declaration
boundary. The [synthetic profile](contracts/examples/synthetic-capabilities.v1.json)
and [explicit requirements](contracts/examples/synthetic-requirements.v1.json)
illustrate declared compatibility; a match never establishes runtime support or
grants execution authority.

The current platform expansion starts with the [workflow-package v1 schema](contracts/workflow-package.v1.schema.json) and a synthetic reviewed-action example:

```sh
npm run package:validate -- contracts/examples/reviewed-action.v1.json
npm run check:contracts
```

The validator checks the local declaration and emits a canonical content digest. It does not execute the workflow, contact a connector, grant approval, or verify a publisher signature. `npm run schema:contracts` regenerates the checked-in schema from the contract model; review the generated diff when changing the contract. A valid synthetic example is not a customer integration test.

## Product and delivery

Read [capabilities](docs/CAPABILITIES.md), [API](docs/API.md), [database](docs/DATABASE.md), [design system](docs/design-system.md), and the [complete delivery goal](docs/GOAL.md). The Git-backed documentation site is in `docs-site/`; packaging and operator instructions are in `deploy/`.

The canonical private repository is [lotuspond-ai/lotuspond](https://github.com/lotuspond-ai/lotuspond). Repository publication and hosted deployment are separate milestones. Production readiness requires the remaining identity, provider, deployment, and operational evidence listed in the capability register, as well as the new runtime and commercial capabilities required for the expanded platform. The separate local recovery archive is outside this project.

Use [Operations](docs-site/docs/operations.md) to inspect queue eligibility, expired leases and worker poll observations.

[Evaluation runners](docs-site/docs/runners.md) execute reviewed adapters in supervised processes. Use the HTTP adapter for an operator-configured text agent or the MCP adapter for a specific tool; frozen scenarios, complete evidence and a current lease remain required.

Executable-v2 authoring is available through the standalone SDK's explicit
`lotuspond-package v2` commands: bounded validation/JCS, explicit-source
compilation, private diffs, signatures, synthetic compatibility and structural
schemas. See the [executable guide](docs-site/docs/executable-packages.md). The
registry remains v1-only, and the console Studio composes v1 packages only.
Studio authoring of v2 definitions, engine execution, authenticated runtime
admission, providers, commercial licensing and production qualification retain
their separate gates. `npm run check:executable-contracts` verifies all three
committed schema exports; Node/OpenSSL are SDK test tools only.
