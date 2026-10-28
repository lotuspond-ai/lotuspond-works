import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { BillingCard } from '../components/BillingCard.js';
import { Alert, Readiness } from '../components/ui.js';
import type { Billing } from '../lib/billing.js';

// Set at build time, as on the public introduction; without it there is no contact action
// rather than an invented address.
const contactEmail = String(import.meta.env.VITE_LOTUSPOND_CONTACT_EMAIL ?? '').trim();

// Proposed editions and services from docs/plans/2026-10-13-platform-master-plan.md (sections 4
// and 5). They are labelled as proposals: names, contents and prices await buyer validation.
const editions = [
  {
    name: 'Developer',
    use: 'Local development and evaluation',
    detail:
      'Studio and the SDK, package validation, test environments and agent evaluation for the people who build workflows.',
  },
  {
    name: 'Business',
    use: 'Connected production',
    detail:
      'Production environments, workers you control, connections, human tasks and outcome evidence, with standard support.',
  },
  {
    name: 'Enterprise',
    use: 'Dedicated or customer-managed deployment',
    detail:
      'Advanced identity and provisioning, delegated partner access and stronger support. Air-gapped operation is qualified separately.',
  },
] as const;
const services = [
  [
    'Readiness assessment',
    'Process inventory, data and system readiness, value and risk ranking, a target architecture and an adoption plan, useful whether or not an implementation follows.',
  ],
  [
    'Implementation',
    'Process design, connections, the workflow package, test evidence, deployment and handover, delivered against milestones you accept.',
  ],
  [
    'Managed operations',
    'Monitoring, incident response, capacity management, approved changes and improvement reviews under an agreed retainer.',
  ],
  [
    'Training and partner enablement',
    'Training for administrators, developers and reviewers, with guides and implementation methods for partners.',
  ],
] as const;

export function License({ workspace }: { workspace: string }) {
  const [billing, setBilling] = useState<Billing | null | undefined>(undefined);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setBilling(undefined);
    setError('');
    api<{ billing?: Billing }>('/settings', workspace, undefined, 'GET', controller.signal)
      .then((data) => setBilling(data.billing ?? null))
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [workspace]);

  return (
    <div className="license">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Administer</p>
          <h1>License and plan</h1>
          <p>
            What this workspace is entitled to today, and how the platform and services are proposed
            to be licensed. Lotuspond collects no payment in the console.
          </p>
        </div>
      </div>

      <h2 className="license-heading">Current plan</h2>
      {error ? (
        <Alert>The plan could not be loaded: {error}</Alert>
      ) : billing === undefined ? (
        <p className="hint" role="status">
          Loading the plan…
        </p>
      ) : billing === null ? (
        <p className="panel">This installation does not report a plan for this workspace.</p>
      ) : (
        <BillingCard billing={billing} />
      )}

      <section aria-labelledby="editions-title">
        <div className="license-section-head">
          <h2 id="editions-title" className="license-heading">
            Platform editions
          </h2>
          <Readiness state="planned" />
        </div>
        <p className="hint license-lede">
          Proposed. Edition names, contents and prices need buyer validation and are not offered
          yet. The intended model is a base platform license, production capacity, transparent
          variable costs and optional services. In that model, waiting for a person would not
          consume active execution capacity, and ordinary reviewers would not need a builder
          license.
        </p>
        <ul className="license-editions">
          {editions.map((edition) => (
            <li key={edition.name} className="panel">
              <h3>{edition.name}</h3>
              <p className="license-use">{edition.use}</p>
              <p>{edition.detail}</p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="services-title">
        <h2 id="services-title" className="license-heading">
          Services
        </h2>
        <p className="hint license-lede">
          Purchased separately from the platform. Lotuspond works without them: build on your own,
          work with our team, or appoint an implementation partner.
        </p>
        <dl className="license-services">
          {services.map(([name, detail]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{detail}</dd>
            </div>
          ))}
        </dl>
        {contactEmail && (
          <a className="button-link" href={`mailto:${contactEmail}`}>
            Talk to our team
          </a>
        )}
      </section>

      <section className="panel" aria-labelledby="delegation-title">
        <div className="license-section-head">
          <h2 id="delegation-title">Delegated service access</h2>
          <Readiness state="planned" />
        </div>
        <p>
          You will be able to grant Lotuspond&rsquo;s team or a partner scoped, time-limited access
          to this workspace, approved by you and recorded in the audit log. Until then, add people
          as{' '}
          <a className="inline-link" href="#members">
            members
          </a>{' '}
          with the least privilege they need.
        </p>
      </section>
    </div>
  );
}
