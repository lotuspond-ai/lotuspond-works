import { billingView, type Billing } from '../lib/billing.js';
import { StatusBadge } from './ui.js';

// The workspace's provisioned trace plan. Shared by Settings and License.
export function BillingCard({ billing }: { billing?: Billing }) {
  if (!billing) return null;
  const view = billingView(billing);
  return (
    <section className="panel">
      <h2>Plan and allowance</h2>
      <p>
        <StatusBadge value={view.plan} />{' '}
        <StatusBadge
          value={
            view.state === 'exhausted'
              ? 'exhausted'
              : view.state === 'nearly'
                ? 'nearly used'
                : 'within allowance'
          }
        />
      </p>
      <p>{view.allowance}</p>
      <p className="hint">{view.retention}</p>
      <p className="hint">{billing.payment.message}</p>
      <p className="hint">
        A workspace administrator cannot change this plan. Ask your Lotuspond operator to provision
        a different one.
      </p>
    </section>
  );
}
