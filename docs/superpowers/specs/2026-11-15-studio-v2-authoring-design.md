# Studio authoring of executable-v2 packages

Date: 15 November 2026 (Asia/Kolkata). Status: **design, decided by Claude under the owner's delegation ("proceed to build in the order", item 9); this change builds it.** It is step 2 of [ADR-0013](../../architecture/0013-trim-to-the-automation-core.md)'s order ("The next Studio increment authors executable-v2 and publishes through `/executable-packages`"), and the Build row of [GOAL.md](../../GOAL.md).

## What exists

- **The Studio authors only v1.** It is a graph of nodes and edges, with a browser mirror of the v1 validator, and it publishes v1 to `/projects/{id}/packages`. The runtime admits only executable-v2, so the console cannot produce a deployable package.
- **v2 differs from v1.** A package is a sequence of typed steps with references: agent, connector read, decision, parallel, iteration, subworkflow, action intent, human task, dispatch and verification. It declares pinned catalog documents (connectors, models, policies) and limits.
- **The SDK has the v2 tools.** It validates, compiles, signs and diffs v2, and its errors are a closed list of codes with JSON pointers and fixed messages.
- **Publishing is gated.** `POST /executable-packages` takes `{package, dependency_ids, signature}`. The workflow must be signed by a publisher the operator trusts, and every pinned catalog document must already be published in the environment and named in `dependency_ids`.

## Decisions

1. **The server checks; the browser does not reimplement v2.**
   - `POST …/executable-packages/check` takes `{package, dependency_ids}` and runs the publication path without trust or storage. It validates and canonicalizes, then resolves and compiles the closure against the named publications.
   - It answers `{valid, package_sha256, plan_sha256, canonical, problems}`. Each problem is the SDK's own `{path, code, message}`: closed codes, never a submitted value.
   - It needs `publish` and writes nothing. One validator stays the single source of truth; v1's browser mirror, 650 lines for a far smaller format, is not repeated.
2. **The publication list says what each publication is.** `PublicationView` gains `kind`, `name` and `version`, so the Studio can match a package's pinned references to publications in the environment.
3. **The Studio gets a second editor, *Executable package (v2)*, beside the v1 graph.** v1 stays unchanged until it is retired (ADR-0013 step 3, a separate plan).
   - The editor works in one environment of the project, because executable packages are environment-scoped.
   - It starts from the synthetic v2 examples in `contracts/examples/executable-v2/`: minimal, decision, parallel, iteration, reviewed action. It also opens a stored version, or a file.
   - It is a **constrained editor:**
     - package identity, limits and assurance classification in forms;
     - an outline of steps by kind and ID, where each step is edited as JSON, with add, remove and move;
     - declared connectors, models and policies listed with their pins;
     - the whole package as JSON for anything else.
   - Every change is checked by the server after a short pause. Problems show with their paths, and a problem in a step is shown on that step.
4. **Dependencies.**
   - The editor lists each pinned reference beside the publication in this environment that matches its kind, name, version and digest, and says which are missing. The matched publications become `dependency_ids`. Nothing is guessed: a reference without an exact match blocks publishing.
   - Catalog documents are published by an operator through the API. The editor does not author them in this change.
5. **Signing stays outside the browser.**
   - The editor downloads the canonical package and shows the exact SDK command: `lotuspond-package v2 sign package.json --key <publisher key> --output signature.json`.
   - The author then attaches `signature.json`, and the editor publishes `{package, dependency_ids, signature}` with an idempotency key.
   - The publisher's private key never enters the console. Trust stays the operator's (`authority trust-publisher`).
6. **Drafts** stay in the browser, as v1's do, under their own key.

## Out of scope

- v2 equivalents of the five v1 domain patterns, and retiring v1 publishing and its compatibility modules. Both are ADR-0013 step 3, which needs its own plan.
- A visual graph for v2, typed mapping editors per step kind, and AI-assisted authoring.
- Authoring catalog documents, and version diff in the console.
