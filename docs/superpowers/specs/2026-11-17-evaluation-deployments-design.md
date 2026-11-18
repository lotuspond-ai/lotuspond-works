# Evaluation deployments

Date: 17 November 2026 (Asia/Kolkata). Status: **design, decided by the owner on 2026-11-17 ("Evaluation-only deployments"); this change builds it.** It repairs the Prove step of [GOAL.md](../../GOAL.md) on a server configured as production is.

## The defect

[Package proof](2026-11-15-package-proof-design.md) runs a package version on its scenarios "through an evaluation deployment", and its decision 7 says release decisions "can stay on". They cannot. With `release_required` on, the operator directory's default, which [the runbook](../../operations/operator-authority.md) says to keep, deployment admission refuses any deployment without a release (`503 assurance_unavailable`). The evaluation deployment is one of them. A release needs a completed evaluation run, and the run needs that deployment, so nothing can be proven and released unless the operator switches the gate off. The package-proof conformance case passed only because test worlds switch it off. Running late purchase order recovery on an operator directory (milestone 5) found it.

## Decisions

1. **A deployment may say it exists to prove its package.** Its configuration carries `"purpose": "evaluation"`. The configuration is the revision's immutable canonical bytes, bound by `configuration_sha256`, so the purpose cannot change after admission and needs no new column. A configuration without the field keeps its exact bytes and digest: every stored revision is unchanged, and no migration is needed.
2. **Admitted without a release.** Release decisions, and a package's own evaluation or outcome-rule references, do not require a release for an evaluation deployment. It may not name one (`assurance_unavailable`).
3. **Every write behind a review.** An evaluation deployment of a package with a write that dispatches without review is refused (`invalid_request`). A package evaluation stops each execution at its review and cancels it there; a write without one would reach the evaluation connection's system.
4. **Only a package evaluation starts it.** `POST …/executions` on an evaluation deployment is refused (`deployment_unavailable`). A package evaluation run admits its executions with the evaluation flag, on an evaluation deployment or any other.
5. **It never writes.** No review on an evaluation deployment can be allowed (`forbidden`); a person may still deny one. Command records for its executions are refused (`forbidden`), which stops both command issuance and any claim, whatever the decision rows say.
6. **Everything else is unchanged.** Production deployments keep the full release pipeline, re-verified at admission and at every final guard. The release binding provider still requires the release's candidate run to be a completed package evaluation of exactly that publication.

## Out of scope

Showing the purpose in Control Room's deployment list and the deployment view (the API's views are unchanged), a separate evaluation environment (the package-proof design's later work), and creating evaluation deployments from the console.
