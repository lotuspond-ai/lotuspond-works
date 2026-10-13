export type Billing = {
  plan: string;
  entitlements: { monthly_trace_quota: number; trace_retention_days: number };
  usage: { traces_this_month: number; traces_remaining: number };
  payment: { status: string; message: string };
};

export type BillingView = {
  plan: string;
  used: number;
  quota: number;
  remaining: number;
  percentUsed: number;
  state: 'within' | 'nearly' | 'exhausted';
  allowance: string;
  retention: string;
};

// Reported as a share of the allowance the operator provisioned, never as a forecast
// or a charge: Lotuspond collects no money and cannot know what a customer will be billed.
export function billingView(billing: Billing): BillingView {
  const quota = Math.max(0, billing.entitlements.monthly_trace_quota);
  const used = Math.max(0, billing.usage.traces_this_month);
  const remaining = Math.max(0, billing.usage.traces_remaining);
  const percentUsed = quota === 0 ? 100 : Math.min(100, Math.round((used / quota) * 100));
  const state = remaining === 0 ? 'exhausted' : percentUsed >= 80 ? 'nearly' : 'within';
  const days = billing.entitlements.trace_retention_days;
  return {
    plan: billing.plan,
    used,
    quota,
    remaining,
    percentUsed,
    state,
    allowance:
      state === 'exhausted'
        ? `This workspace has used all ${quota.toLocaleString()} traces in its monthly allowance. Further traces are refused until the allowance resets or an operator provisions a larger plan.`
        : `${used.toLocaleString()} of ${quota.toLocaleString()} traces used this month, ${remaining.toLocaleString()} remaining.`,
    retention: `Traces are kept for ${days} ${days === 1 ? 'day' : 'days'} on this plan. They stay readable and exportable until the retention job removes them.`,
  };
}
