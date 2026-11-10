# Operate agents with evidence

Lotuspond connects three workflows: define agent scenarios, run evaluations, and make governed release decisions that deployment admission requires. This documentation describes the current source tree and its portable deployment artifacts. It does not imply a hosted service or a production validation.

## Choose a path

- [Install](install.md) for a local source checkout.
- [Projects and scenarios](projects.md) to define what should be tested.
- [Evaluations](evaluations.md) to understand evidence and release gates.
- [Deployment](deployment.md) for the single-host Compose installation and the customer-side execution image.

## Evidence boundary

A passing local build proves that the checked-out source builds in that environment. A successful deployment proves that one configured deployment started. Neither proves customer-agent compatibility, production resilience, backup recovery, a provider's claims, or a release outcome. Lotuspond retains the evidence that operators submit; operators remain responsible for source quality, permissions, and confidential-data controls.
