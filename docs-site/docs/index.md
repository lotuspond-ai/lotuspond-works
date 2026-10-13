# Operate agents with evidence

Lotuspond connects four workflows: define agent scenarios, run evaluations, retain production evidence, and make governed release decisions. This documentation describes the current source tree and its portable deployment artifacts. It does not imply a hosted service or a production validation.

## Choose a path

- [Install](install.md) for a local source checkout.
- [Projects and scenarios](projects.md) to define what should be tested.
- [Evaluations](evaluations.md) to understand evidence and release gates.
- [Production monitoring](monitoring.md) for bounded OTLP trace ingestion.
- [Deployment](deployment.md) for connected Docker, on-premises, and air-gapped operation.
- [Pilot program](pilot.md) for a measured first engagement.

## Evidence boundary

A passing local build proves that the checked-out source builds in that environment. A successful deployment proves that one configured deployment started. Neither proves customer-agent compatibility, production resilience, backup recovery, a provider's claims, or a release outcome. Lotuspond retains the evidence that operators submit; operators remain responsible for source quality, permissions, and confidential-data controls.
