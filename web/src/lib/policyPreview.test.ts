import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
  decide,
  REASONS,
  TIMEOUT_MS,
  type PreviewPolicy,
  type PreviewScenario,
} from './policyPreview.js';

const policy: PreviewPolicy = {
  minimumPassRate: 1,
  maximumRegression: 0,
  maximumP95Ms: null,
  requireApproval: false,
};
const scenario = (outcome: PreviewScenario['outcome'], critical = false): PreviewScenario => ({
  name: outcome,
  critical,
  outcome,
  latencyMs: 800,
});

test('every preview reason is quoted exactly from the server policy', async () => {
  const source = await readFile(
    new URL('../../../backend/lotuspond/policy.py', import.meta.url),
    'utf8',
  );
  for (const reason of Object.values(REASONS)) assert.ok(source.includes(reason), reason);
});

test('a complete passing run passes, or waits for independent approval', () => {
  const run = [scenario('passed', true), scenario('passed')];
  assert.deepEqual(decide(policy, run, null).reasons, [REASONS.pass]);
  const held = decide({ ...policy, requireApproval: true }, run, null);
  assert.equal(held.verdict, 'approval_required');
  assert.deepEqual(held.reasons, [REASONS.approval]);
  assert.equal(decide({ ...policy, requireApproval: true }, run, null, true).verdict, 'pass');
});

test('unknown and missing outcomes block even when the pass-rate floor allows them', () => {
  const lenient = { ...policy, minimumPassRate: 0.5 };
  for (const outcome of ['unknown', 'missing'] as const) {
    const decision = decide(lenient, [scenario('passed'), scenario(outcome)], null);
    assert.equal(decision.verdict, 'block');
    assert.deepEqual(decision.reasons, [REASONS.coverage]);
  }
});

test('a failed critical scenario blocks with the server’s ordering of reasons', () => {
  const decision = decide(policy, [scenario('failed', true), scenario('passed')], 1);
  assert.deepEqual(decision.reasons, [REASONS.passRate, REASONS.critical, REASONS.regression]);
});

test('a timed-out result counts toward P95 latency like the server’s stored wait', () => {
  const decision = decide(
    { ...policy, minimumPassRate: 0, maximumP95Ms: 5000 },
    [scenario('passed'), scenario('unknown')],
    null,
  );
  assert.equal(decision.p95Ms, TIMEOUT_MS);
  assert.ok(decision.reasons.includes(REASONS.latency));
});
