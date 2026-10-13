import assert from 'node:assert/strict';
import test from 'node:test';
import { billingView, type Billing } from './billing.js';

const billing: Billing = {
  plan: 'developer',
  entitlements: { monthly_trace_quota: 10000, trace_retention_days: 7 },
  usage: { traces_this_month: 100, traces_remaining: 9900 },
  payment: { status: 'not_collected', message: 'Lotuspond stores no payment instrument.' },
};

test('the allowance is reported as usage against what an operator provisioned', () => {
  const view = billingView(billing);
  assert.equal(view.state, 'within');
  assert.equal(view.percentUsed, 1);
  assert.match(view.allowance, /100 of 10,000 traces used this month, 9,900 remaining/);
  assert.match(view.retention, /kept for 7 days/);
  assert.match(view.retention, /readable and exportable until the retention job removes them/);
});

test('an allowance close to its limit is distinguished from one already spent', () => {
  const nearly = billingView({
    ...billing,
    usage: { traces_this_month: 8500, traces_remaining: 1500 },
  });
  assert.equal(nearly.state, 'nearly');
  assert.equal(nearly.percentUsed, 85);

  const spent = billingView({
    ...billing,
    usage: { traces_this_month: 10000, traces_remaining: 0 },
  });
  assert.equal(spent.state, 'exhausted');
  assert.match(spent.allowance, /Further traces are refused/);
  assert.match(spent.allowance, /an operator provisions a larger plan/);
});

test('a single retention day reads correctly and a zero quota never divides by zero', () => {
  assert.match(
    billingView({ ...billing, entitlements: { monthly_trace_quota: 10, trace_retention_days: 1 } })
      .retention,
    /kept for 1 day\b/,
  );
  const zero = billingView({
    ...billing,
    entitlements: { monthly_trace_quota: 0, trace_retention_days: 7 },
    usage: { traces_this_month: 0, traces_remaining: 0 },
  });
  assert.equal(zero.percentUsed, 100);
  assert.equal(zero.state, 'exhausted');
});

test('negative or inconsistent counts are clamped rather than rendered', () => {
  const view = billingView({
    ...billing,
    usage: { traces_this_month: -5, traces_remaining: -5 },
  });
  assert.equal(view.used, 0);
  assert.equal(view.remaining, 0);
  assert.equal(view.percentUsed, 0);
});
