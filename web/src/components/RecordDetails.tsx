import { formatLatency } from '../lib/format.js';
import { DecisionSummary } from './DecisionSummary.js';
import { ObjectView, RawRecord, StatusBadge, displayValue } from './ui.js';

type Data = Record<string, unknown>;
function object(value: unknown): Data {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Data) : {};
}
function number(value: unknown, suffix = ''): string {
  return value !== null && value !== undefined && Number.isFinite(Number(value))
    ? `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 })}${suffix}`
    : 'Not recorded';
}

export function RecordDetails({ value }: { value: unknown }) {
  const record = object(value);
  if (Array.isArray(record.decisions))
    return (
      <div className="decision-history">
        {record.decisions.map((decision, index) => (
          <RecordDetails key={String(object(decision).id || index)} value={decision} />
        ))}
        <RawRecord value={value} />
      </div>
    );
  if (Array.isArray(record.results) && Array.isArray(record.scenario_snapshot)) {
    const results = record.results.map(object);
    const scenarios = record.scenario_snapshot.map(object);
    const completed = results.length;
    return (
      <div className="decision-summary">
        <div className="section-row">
          <div>
            <p className="eyebrow">Evaluation evidence</p>
            <h2>{String(record.name || 'Evaluation run')}</h2>
          </div>
          <StatusBadge value={record.status} />
        </div>
        <dl className="compact-facts">
          <div>
            <dt>Revision</dt>
            <dd>{displayValue(record.revision)}</dd>
          </div>
          <div>
            <dt>Results received</dt>
            <dd>
              {completed} / {scenarios.length}
            </dd>
          </div>
          <div>
            <dt>Passing results</dt>
            <dd>{results.filter((result) => result.passed === true).length}</dd>
          </div>
        </dl>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Scenario</th>
                <th>Result</th>
                <th>Latency</th>
                <th>Cost</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {scenarios.map((scenario, index) => {
                const result = results.find((item) => item.scenario_id === scenario.id);
                return (
                  <tr key={String(scenario.id || index)}>
                    <td className="primary-cell">
                      {String(scenario.name || 'Scenario')}
                      {scenario.critical === true && (
                        <small className="critical-note">Critical</small>
                      )}
                    </td>
                    <td>
                      {result ? (
                        <StatusBadge
                          value={
                            result.passed === true
                              ? 'passed'
                              : result.passed === false
                                ? 'failed'
                                : 'unknown'
                          }
                        />
                      ) : (
                        <span className="hint">Awaiting evidence</span>
                      )}
                    </td>
                    <td>{result ? number(result.latency_ms, ' ms') : '—'}</td>
                    <td>
                      {!result
                        ? '—'
                        : result.cost_usd === null ||
                            result.cost_usd === undefined ||
                            object(result.evidence).cost_measurement === 'unavailable'
                          ? 'Not recorded'
                          : `$${number(result.cost_usd)}`}
                    </td>
                    <td>
                      <details>
                        <summary className="inline-link">Inspect</summary>
                        <ObjectView
                          value={{
                            input: scenario.input,
                            expected: scenario.expected,
                            ...(result ? { output: result.output, evidence: result.evidence } : {}),
                          }}
                        />
                      </details>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <RawRecord value={value} />
      </div>
    );
  }
  if (!record.verdict)
    return (
      <div className="decision-summary">
        <ObjectView value={value} />
        <RawRecord value={value} />
      </div>
    );
  const metrics = object(object(record.evidence).metrics);
  const verdict = String(record.verdict);
  const reasons = Array.isArray(record.reasons) ? record.reasons.map(String) : [];
  return (
    <DecisionSummary
      verdict={verdict === 'pass' || verdict === 'block' ? verdict : 'approval_required'}
      reasons={reasons}
      meta={`${displayValue(record.created_at)}${record.id ? ` · ${String(record.id).slice(0, 12)}` : ''}`}
      metrics={
        Object.keys(metrics).length
          ? [
              {
                label: 'Pass rate',
                value:
                  metrics.passRate === null || metrics.passRate === undefined
                    ? 'Not recorded'
                    : `${Number((Number(metrics.passRate) * 100).toFixed(1))}%`,
              },
              {
                label: 'Evidence received',
                value: `${number(metrics.received)} / ${number(metrics.total)}`,
              },
              {
                label: 'P95 latency',
                value: Number.isFinite(Number(metrics.p95Ms ?? NaN))
                  ? formatLatency(Number(metrics.p95Ms))
                  : 'Not recorded',
              },
              {
                label: 'Evaluation cost',
                value:
                  metrics.costUsd === null || metrics.costUsd === undefined
                    ? 'Not recorded'
                    : `$${number(metrics.costUsd)}`,
              },
            ]
          : []
      }
    >
      <details className="evidence-disclosure">
        <summary>Inspect the decision evidence</summary>
        <ObjectView value={record.evidence} />
      </details>
      <RawRecord value={value} />
    </DecisionSummary>
  );
}
