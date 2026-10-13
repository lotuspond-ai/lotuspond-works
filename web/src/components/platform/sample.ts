import { decide, type PreviewPolicy, type PreviewScenario } from '../../lib/policyPreview.js';

// One illustrative workspace shared by every view of the platform preview, so the run, the
// decision and the gate tell the same story. None of it is customer data or a result.
export const sampleProject = 'Refund assistant';
export const sampleCandidate = 'r42';
export const sampleBaseline = 'r41';
export const sampleBaselinePassRate = 1;

export const sampleScenarios: PreviewScenario[] = [
  { name: 'Refund within policy', critical: true, outcome: 'passed', latencyMs: 820 },
  { name: 'Order lookup by email', critical: false, outcome: 'passed', latencyMs: 640 },
  { name: 'Cancel a shipped order', critical: false, outcome: 'passed', latencyMs: 910 },
  { name: 'Refund above the approval limit', critical: true, outcome: 'passed', latencyMs: 1140 },
  { name: 'Escalate an upset customer', critical: false, outcome: 'passed', latencyMs: 1320 },
  { name: 'Tool call: issue store credit', critical: true, outcome: 'unknown', latencyMs: 1060 },
];

export const samplePolicy: PreviewPolicy = {
  minimumPassRate: 0.95,
  maximumRegression: 0,
  maximumP95Ms: 5000,
  requireApproval: true,
};

export const sampleDecision = decide(samplePolicy, sampleScenarios, sampleBaselinePassRate);

export { formatLatency } from '../../lib/format.js';
