import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { TraceDetails } from '../components/TraceDetails.js';
import { traceCost } from './traceCost.js';

test('legacy stored zero remains visible with unverified provenance', () => {
  const html = renderToStaticMarkup(
    createElement(TraceDetails, {
      value: { trace: { id: 'synthetic-trace', cost_usd: 0 }, assessments: [] },
      workspace: 'synthetic-workspace',
      role: 'viewer',
      onRecorded: async () => {},
    }),
  );
  assert.match(html, /Unverified legacy cost/);
  assert.match(html, /unknown measurement provenance/);
  assert.match(html, /Excluded from reported-cost totals/);
  assert.match(html, /\$0/);
  assert.doesNotMatch(html, /Measured cost/);
});

test('explicit reported zero differs from unreported and unknown trace cost', () => {
  const reported = traceCost({ id: 'trace', cost_usd: 0, cost_measurement: 'reported' });
  assert.equal(reported.value, '$0');
  assert.equal(reported.label, 'Reported cost');
  assert.equal(
    traceCost({ id: 'trace', cost_usd: 0.000001, cost_measurement: 'reported' }).value,
    '$0.000001',
  );
  assert.equal(
    traceCost({ id: 'trace', cost_usd: null, cost_measurement: 'unavailable' }).value,
    'Not recorded',
  );
  assert.equal(
    traceCost({ id: 'trace', cost_usd: 0, cost_measurement: 'unavailable' }).value,
    'Not recorded',
  );
  assert.equal(
    traceCost({ id: 'trace', cost_usd: 12.3456, cost_measurement: 'unknown' }).state,
    'Unverified',
  );
});

test('missing provenance and invalid amounts cannot imply reported evidence', () => {
  assert.equal(traceCost({ id: 'trace', cost_usd: 2 }).state, 'Unverified');
  for (const cost_usd of [null, undefined, NaN, Infinity, -1]) {
    const cost = traceCost({ id: 'trace', cost_usd, cost_measurement: 'reported' });
    assert.equal(cost.value, 'Not recorded');
    assert.notEqual(cost.state, 'Reported');
  }
});
