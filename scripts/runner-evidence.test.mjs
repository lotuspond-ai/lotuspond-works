import assert from 'node:assert/strict';
import { test } from 'node:test';
import { adapterResult, executionFailure } from './runner-evidence.mjs';

test('infrastructure failure is unknown and does not invent a free execution', () => {
  const result = executionFailure('scenario-one', 60000);
  assert.equal(result.passed, null);
  assert.equal(result.evidence.execution, 'failed');
  assert.equal(result.evidence.cost_measurement, 'unavailable');
  assert.equal(Object.hasOwn(result, 'cost_usd'), false);
});

test('omitted cost cannot be upgraded by an adapter evidence claim', () => {
  const result = adapterResult(
    { passed: true, output: {}, evidence: { cost_measurement: 'reported' } },
    'scenario-one',
    20,
  );
  assert.equal(result.evidence.cost_measurement, 'unavailable');
  assert.equal(Object.hasOwn(result, 'cost_usd'), false);
});

test('explicit zero is reported unless the adapter marks its cost unavailable', () => {
  const measured = adapterResult({ passed: true, output: {}, costUsd: 0 }, 'a', 20);
  assert.equal(measured.cost_usd, 0);
  assert.equal(measured.evidence.cost_measurement, 'reported');
  const unmeasured = adapterResult(
    { passed: false, output: {}, costUsd: 0, evidence: { cost_measurement: 'unavailable' } },
    'b',
    20,
  );
  assert.equal(unmeasured.passed, false);
  assert.equal(unmeasured.evidence.cost_measurement, 'unavailable');
});

test('adapter cost must be a finite nonnegative number', () => {
  for (const costUsd of [NaN, Infinity, -Infinity, -1, '0', null, false]) {
    assert.throws(() => adapterResult({ passed: true, output: {}, costUsd }, 'a', 20));
  }
  assert.equal(adapterResult({ passed: null, output: {}, costUsd: 0.05 }, 'a', 20).cost_usd, 0.05);
});
