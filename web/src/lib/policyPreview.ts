// A browser port of `evaluate` in backend/lotuspond/policy.py for the public platform
// preview. It decides only sample data and never records anything; the server remains the
// only authority. The reason strings must stay identical to the Python ones, which
// policyPreview.test.ts checks against the source file.

export const REASONS = {
  coverage: 'Candidate run lacks a conclusive result for every scenario.',
  passRate: 'Pass rate is below the policy minimum.',
  critical: 'A critical scenario failed or has no conclusive result.',
  latency: 'P95 latency exceeds the policy limit or is unavailable.',
  cost: 'Evaluation cost exceeds the policy limit or is unavailable.',
  regression: 'Candidate regresses beyond the permitted baseline threshold.',
  approval: 'An independent administrator must approve this release.',
  pass: 'All configured release checks passed.',
} as const;

export type Outcome = 'passed' | 'failed' | 'unknown' | 'missing';
export type PreviewScenario = {
  name: string;
  critical: boolean;
  outcome: Outcome;
  latencyMs: number;
};
export type PreviewPolicy = {
  minimumPassRate: number;
  maximumRegression: number;
  maximumP95Ms: number | null;
  requireApproval: boolean;
};
export type Verdict = 'pass' | 'block' | 'approval_required';
export type PreviewDecision = {
  verdict: Verdict;
  reasons: string[];
  total: number;
  received: number;
  unknown: number;
  passRate: number;
  p95Ms: number | null;
};

// A result that timed out is still received, with the time the runner waited.
export const TIMEOUT_MS = 30000;

export function decide(
  policy: PreviewPolicy,
  scenarios: readonly PreviewScenario[],
  baselinePassRate: number | null,
  approved = false,
): PreviewDecision {
  const results = scenarios.filter((scenario) => scenario.outcome !== 'missing');
  const total = scenarios.length;
  const passed = results.filter((scenario) => scenario.outcome === 'passed').length;
  const unknown = results.filter((scenario) => scenario.outcome === 'unknown').length;
  const passRate = total ? passed / total : 0;
  const latencies = results
    .map((scenario) => (scenario.outcome === 'unknown' ? TIMEOUT_MS : scenario.latencyMs))
    .sort((a, b) => a - b);
  const p95Ms = latencies.length
    ? (latencies[Math.ceil(latencies.length * 0.95) - 1] ?? null)
    : null;
  const complete = total > 0 && results.length === total && unknown === 0;

  const reasons: string[] = [];
  if (!complete) reasons.push(REASONS.coverage);
  if (passRate < policy.minimumPassRate) reasons.push(REASONS.passRate);
  if (scenarios.some((scenario) => scenario.critical && scenario.outcome !== 'passed'))
    reasons.push(REASONS.critical);
  if (policy.maximumP95Ms !== null && (p95Ms === null || p95Ms > policy.maximumP95Ms))
    reasons.push(REASONS.latency);
  // The preview's baseline evaluated the same frozen snapshot and was complete.
  if (baselinePassRate !== null && baselinePassRate - passRate > policy.maximumRegression + 1e-12)
    reasons.push(REASONS.regression);

  const verdict: Verdict = reasons.length
    ? 'block'
    : policy.requireApproval && !approved
      ? 'approval_required'
      : 'pass';
  if (verdict === 'approval_required') reasons.push(REASONS.approval);
  if (verdict === 'pass') reasons.push(REASONS.pass);
  return { verdict, reasons, total, received: results.length, unknown, passRate, p95Ms };
}
