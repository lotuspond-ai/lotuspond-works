import { type Row } from '../api.js';
import { traceCost } from '../lib/traceCost.js';
import { Assessments } from './Assessments.js';
import { ObjectView, RawRecord, StatusBadge, displayValue } from './ui.js';

export type TraceDetail = { trace: Row; assessments: Row[] };
export function TraceDetails({
  value,
  workspace,
  role,
  onRecorded,
}: {
  value: TraceDetail;
  workspace: string;
  role: string;
  onRecorded: () => Promise<void>;
}) {
  const { trace, assessments } = value;
  const cost = traceCost(trace);
  return (
    <div className="decision-summary">
      <div className="section-row">
        <div>
          <p className="eyebrow">Production trace</p>
          <h2>{String(trace.name || 'Trace evidence')}</h2>
        </div>
        <StatusBadge value={trace.status} />
      </div>
      <dl className="compact-facts">
        <div>
          <dt>Occurred</dt>
          <dd>{displayValue(trace.occurred_at || trace.created_at)}</dd>
        </div>
        <div>
          <dt>Measured latency</dt>
          <dd>
            {trace.latency_ms === undefined || trace.latency_ms === null
              ? 'Not recorded'
              : `${displayValue(trace.latency_ms)} ms`}
          </dd>
        </div>
        <div>
          <dt>{cost.label}</dt>
          <dd>{cost.value}</dd>
        </div>
      </dl>
      <p className="hint">{cost.description}</p>
      <section className="evidence-section">
        <h3>Retained trace evidence</h3>
        <ObjectView value={trace.attributes || {}} />
      </section>
      <Assessments
        items={assessments || []}
        workspace={workspace}
        resourcePath={`/traces/${encodeURIComponent(trace.id)}`}
        canWrite={role !== 'viewer'}
        subject="trace"
        onRecorded={onRecorded}
      />
      <RawRecord value={value} />
    </div>
  );
}
