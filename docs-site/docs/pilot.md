# Pilot program

Run a four-week, bounded pilot around one release decision and one production signal. A pilot validates a specific workflow with a specific customer; it does not establish broad market or production readiness.

## Entry criteria

- Named executive owner, operational owner, and independent release reviewer.
- One agent workflow with a stable revision identifier and 10–30 representative scenarios.
- Written data classification, retention, deletion, and access rules.
- Baseline outcomes from the customer's current process.
- Agreed stop conditions for safety, confidentiality, excessive false blocks, or missing evidence.

## Weekly sequence

1. **Scope and connect:** inventory the workflow, define metrics, and test only synthetic data.
2. **Evaluate:** freeze scenarios, run candidate and baseline, and review every critical failure.
3. **Shadow production:** ingest minimized traces without controlling releases; measure coverage and operator workload.
4. **Decision:** compare against the baseline, exercise evidence export and recovery, and document proceed, revise, or stop.

## Exit measures

Report scenario coverage, critical failures, false-allow and false-block review, evaluator disagreement, evidence completeness, telemetry loss, decision time, and operator hours. Commercial continuation should require a named production use case, acceptable risk findings, evidence completeness at the agreed threshold, and a buyer willing to fund the next bounded phase.

Do not promise universal agent compatibility, autonomous governance, compliance certification, or production savings from a pilot. Convert discovered integration gaps into explicit backlog items with owners and evidence needed for closure.
