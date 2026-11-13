# Contributing to LotusPond Works

The canonical private product repository is [lotuspond-ai/lotuspond](https://github.com/lotuspond-ai/lotuspond). Begin with [README.md](README.md), [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), and the [capability register](docs/CAPABILITIES.md). The [platform plan](docs/plans/2026-10-13-platform-master-plan.md) describes the approved direction; planned features are not shipped functionality.

Use short-lived branches (`codex/` for agent-created branches) and pull requests. Inspect the branch, index, working tree, and remote before editing. Preserve unrelated work and stage intended paths explicitly. Never reset someone else's changes or force-push shared history. Keep baseline failures separate from regressions introduced by your change.

Install the locked Node and Python dependencies using the README instructions. Before proposing a change, run `npm run check`. This includes workflow-package schema parity. Contract changes must update the authoritative Python contract, generated schema, examples, and meaningful acceptance/rejection tests together. Use `npm run schema:contracts` to regenerate and review the diff. A matching schema alone does not validate graph semantics; use the package validator.

Run `npm run test:database` against an independently verified, isolated localhost test database for API behavior, authorization, policy, or relational model changes. Read [SECURITY.md](SECURITY.md) before those changes. Do not inspect or operate a production database. Database evolution requires a reviewed, data-preserving plan; the empty-database initializer is not an upgrade tool.

Run component-specific checks for changes to the protocol adapters, deployment artifacts, or documentation. Preserve existing CI checks. Follow [DESIGN.md](DESIGN.md) for interface work and verify desktop, tablet, 375px, and keyboard behavior. Update the API changelog for user-visible changes and keep capability claims tied to evidence.

Do not commit actual environment files, credentials, customer data, local database contents, dependencies, build output, or private handoff notes. Preserve third-party notices and recovery provenance. This private repository does not establish a new open-source license: retain existing component notices and resolve distribution terms before distributing product artifacts.

Repository publication does not authorize hosting, cloud resource changes, customer migrations, or deployment. Record local tests, GitHub CI, published artifacts, and observed hosted behavior separately. See [repository governance](docs/repository-governance.md) for ownership and repository boundaries.
