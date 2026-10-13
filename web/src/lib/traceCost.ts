import type { Row } from '../api.js';

export function traceCost(trace: Row) {
  const amount = trace.cost_usd;
  const hasAmount = typeof amount === 'number' && Number.isFinite(amount) && amount >= 0;
  const value = hasAmount
    ? `$${amount.toLocaleString('en-US', { maximumFractionDigits: 6 })}`
    : 'Not recorded';
  if (trace.cost_measurement === 'reported' && hasAmount)
    return {
      label: 'Reported cost',
      value,
      state: 'Reported',
      description: 'Reported by the trace producer. This is not independently verified billing.',
    };
  if (trace.cost_measurement === 'unavailable')
    return {
      label: 'Cost',
      value: 'Not recorded',
      state: 'Unreported',
      description: 'The trace producer did not report a cost. This is not a zero-cost measurement.',
    };
  return {
    label: hasAmount ? 'Unverified legacy cost' : 'Cost',
    value,
    state: 'Unverified',
    description: hasAmount
      ? 'Retained amount with unknown measurement provenance. Excluded from reported-cost totals.'
      : 'Cost measurement provenance is unknown. Excluded from reported-cost totals.',
  };
}
