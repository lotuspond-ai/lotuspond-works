import type { ReactNode } from 'react';

export type Verdict = 'pass' | 'block' | 'approval_required';

const headings: Record<Verdict, string> = {
  pass: 'Passed this release policy',
  block: 'Release blocked',
  approval_required: 'Independent approval required',
};
const stamps: Record<Verdict, string> = {
  pass: 'Passed',
  block: 'Blocked',
  approval_required: 'Approval required',
};

export function VerdictStamp({ verdict }: { verdict: Verdict }) {
  return <span className={`verdict-stamp verdict-${verdict}`}>{stamps[verdict]}</span>;
}

export type DecisionMetric = { label: string; value: string };

// A release decision as the console records it. The public preview renders the same component,
// so the two cannot drift apart.
export function DecisionSummary({
  verdict,
  reasons,
  metrics,
  meta,
  eyebrow = 'Recorded release decision',
  live = false,
  as: Heading = 'h2',
  children,
}: {
  verdict: Verdict;
  reasons: readonly string[];
  metrics: readonly DecisionMetric[];
  meta?: string;
  eyebrow?: string;
  live?: boolean;
  // The public preview sits inside a page section, so it renders a lower heading level.
  as?: 'h2' | 'h3' | 'h4';
  children?: ReactNode;
}) {
  return (
    <div className="decision-summary" aria-live={live ? 'polite' : undefined}>
      <div className="decision-head">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <Heading className="decision-title">{headings[verdict]}</Heading>
        </div>
        <VerdictStamp key={verdict} verdict={verdict} />
      </div>
      {meta && <p className="decision-meta">{meta}</p>}
      <ul className="decision-reasons">
        {reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
      {metrics.length > 0 && (
        <dl className="decision-metrics">
          {metrics.map((metric) => (
            <div key={metric.label}>
              <dt>{metric.label}</dt>
              <dd>{metric.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {children}
    </div>
  );
}
