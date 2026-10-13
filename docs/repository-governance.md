# Repository governance

Lotuspond uses one private product repository, [lotuspond-ai/lotuspond](https://github.com/lotuspond-ai/lotuspond). Existing source paths remain in place. Create new components only when they contain reviewed implementation; empty runtime, SDK, or industry repositories would imply ownership and readiness that do not yet exist.

The initial accountable maintainer is `@srujanpapaiah`, recorded in `.github/CODEOWNERS`. Add backend, runtime, integration, and release owners when those roles are staffed. A sole maintainer cannot independently approve their own PR: the initial protection policy requires pull requests and checks, with zero required approvals. Raise that to at least one independent approval and enable code-owner enforcement when another authorized reviewer is available. Customer acceptance remains a separate responsibility.

The intended `main` policy is versioned in `.github/main-protection.json`: current required validation jobs, an up-to-date branch, resolved review conversations, linear history, and no force pushes or deletion, including for admins. GitHub must accept this configuration before it can be called enforced. Private-repository protection availability depends on the organization's plan. If unavailable, retain the policy, use PRs, and report the limitation; do not change repository visibility to work around it.

On 2026-10-13 GitHub rejected the policy application with HTTP 403 because branch protection is unavailable for this private repository on the current plan. The repository remains private. The policy is prepared but **not enforced**; PR discipline alone cannot prevent an administrator from pushing directly. Reapply and verify it after the repository becomes eligible.

An authorized maintainer can apply the reviewed policy with:

```sh
gh api --method PUT repos/lotuspond-ai/lotuspond/branches/main/protection \
  --input .github/main-protection.json
```

Keep source releases and deployments distinct. CI validates the repository; documentation publication is separately gated by `LOTUSPOND_DOCS_PUBLISH`. Do not enable that variable or configure deployment secrets as part of ordinary repository setup.

Preserve the nine legacy repositories and their deployment consumers until an explicit migration/retirement scope has been accepted. The [delivery plan](plans/2026-10-13-platform-delivery.md) records their disposition. Do not copy their history wholesale into this repository. Recovered code needs source, commit/path, license, destination, and regression evidence.

Create `lotuspond-operations` only for the first managed environment, with restricted infrastructure authority. Create a private `delivery-<customer-code>` repository only when a signed engagement needs customer-specific source and access controls. Neither private customer data nor secret values belong in these repositories. Product extensions should depend on pinned artifacts rather than long-lived platform forks.

Product licensing is commercial work still to be finalized. Existing Rust package metadata and vendored MIT notices remain unchanged; no new root license is inferred from repository setup. Dependency/IP review, distribution licensing, signed releases, and a qualified support matrix remain release requirements.
