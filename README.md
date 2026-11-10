# Lotuspond

Lotuspond runs AI-agent workflows inside a company's real processes: each version is proven before it is released, each consequential action waits for a person's decision, and every outcome is recorded. [What Lotuspond is for](docs/GOAL.md) states the problem, the core loop (Build, Prove, Run, Control, on a shared foundation) and what is out of scope; [capabilities](docs/CAPABILITIES.md) records what each step can do today.

Today the product proves agents before release (scenarios, evaluation runs, policies, releases and independent approval) and runs admitted executable packages on a durable PostgreSQL engine with customer-enrolled runtimes. The Studio composes workflow-package v1 drafts, and the standalone SDK authors, signs and compiles executable-v2 packages. The primary API is Python/FastAPI with a fresh PostgreSQL model; the console is React. Node tooling runs customer-controlled evaluation runners and release gates.

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

## Work through a release

1. Create a project, scenarios, and an immutable release policy.
2. Register a runner and issue an API key with `read` and `run` scopes.
3. Queue a run. Execute real evaluations through `npm run runner -- /absolute/path/to/adapter.mjs`.
4. Create a release with the candidate revision, policy, and optional comparable baseline.
5. Evaluate it. Missing or unknown evidence, critical failures, regressions, and latency/cost breaches block release.
6. If required, request the `release` approval action and have a different administrator approve it. Re-evaluate and export the evidence.
7. Enforce the result in your delivery process with `npm run cli -- gate RELEASE_ID`.

The [HTTP runner adapter](protocol-adapters/README.md) evaluates an operator-selected agent behind HTTP. The package has its own lockfile; run `npm ci --ignore-scripts` in it before using it. Its local tests use synthetic endpoints.

Voice sessions, Gym-Anything sandboxes, the TypeSafe Jev evaluator, the model gateway, trace-plan billing, the Go collector and the Rust verifier were retired on 2026-11-05; see [ADR-0012](docs/architecture/0012-retire-assurance-extras.md). Trace ingestion, monitors and assessments, incidents and their alerts, receipt submission, endpoint registration and the License page followed on 2026-11-11; stored traces, incidents and receipts stay readable through the API. See [ADR-0013](docs/architecture/0013-trim-to-the-automation-core.md). Air-gapped operation, offline identity enrollment, the organization and installation authenticator requirements, tool authorization, the MCP and A2A runners, the Operations page and the on-premises Compose variant were retired the same day ([ADR-0014](docs/architecture/0014-focus-on-the-core-loop.md)).

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

Read [capabilities](docs/CAPABILITIES.md), [API](docs/API.md), [database](docs/DATABASE.md), [design system](docs/design-system.md), and [what Lotuspond is for](docs/GOAL.md). The Git-backed documentation site is in `docs-site/`; packaging and operator instructions are in `deploy/`.

The canonical private repository is [lotuspond-ai/lotuspond](https://github.com/lotuspond-ai/lotuspond). Repository publication and hosted deployment are separate milestones. Production readiness requires the remaining identity, provider, deployment, and operational evidence listed in the capability register, and the runtime capabilities still to build. The separate local recovery archive is outside this project.

[Evaluation runners](docs-site/docs/runners.md) execute reviewed adapters in supervised processes. Use the HTTP adapter for an operator-configured text agent; frozen scenarios, complete evidence and a current lease remain required.

Executable-v2 authoring is available through the standalone SDK's explicit
`lotuspond-package v2` commands: bounded validation/JCS, explicit-source
compilation, private diffs, signatures, synthetic compatibility and structural
schemas. See the [executable guide](docs-site/docs/executable-packages.md). The
registry remains v1-only, and the console Studio composes v1 packages only.
Studio authoring of v2 definitions, engine execution, authenticated runtime
admission, providers, commercial licensing and production qualification retain
their separate gates. `npm run check:executable-contracts` verifies all three
committed schema exports; Node/OpenSSL are SDK test tools only.
