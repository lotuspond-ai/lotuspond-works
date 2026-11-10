# Evaluations and release evidence

An evaluation should produce evidence a reviewer can trace to a scenario, agent revision, environment, evaluator version, and time window. A result without those anchors is incomplete.

Use an [evaluation runner](runners.md) to execute a reviewed custom adapter, an HTTP agent endpoint, or an explicit MCP tool. HTTP completion and adapter process exit are distinct from an independently checked task verdict.

## Recommended sequence

1. Select a frozen scenario set and candidate revision.
2. Run deterministic checks before model-graded or human checks.
3. Retain raw artifacts according to the project's data policy.
4. Record evaluator versions, thresholds, failures, and missing evidence.
5. Compare the candidate with a compatible baseline.
6. Require an independent human decision when policy calls for approval.

Lotuspond must fail closed when required evidence is missing, a critical scenario fails, a comparison is incompatible, or the approver is not independent. A retry is a new attempt with its own identifier and evidence.

