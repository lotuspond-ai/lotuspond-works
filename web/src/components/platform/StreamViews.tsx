import { StatusBadge } from '../ui.js';
import {
  formatLatency,
  sampleCandidate,
  sampleDecision,
  sampleProject,
  sampleScenarios,
} from './sample.js';
import { useStream } from './useStream.js';

// An evaluation run filling in as a customer-side runner submits each measured result.
export function RunView({ running }: { running: boolean }) {
  const shown = useStream(sampleScenarios.length + 1, 650, running);
  const received = Math.min(shown, sampleScenarios.length);
  const done = shown > sampleScenarios.length;
  return (
    <div className="preview-run">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Evaluation runs</p>
          <h3 className="preview-title">
            {sampleProject} · {sampleCandidate}
          </h3>
          <p className="cell-mono">
            snapshot frozen · {sampleScenarios.length} scenarios · runner-1 holds a 15-minute lease
          </p>
        </div>
        <StatusBadge value={done ? 'completed' : 'running'} />
      </div>
      <div
        className="preview-progress"
        role="progressbar"
        aria-label="Results received"
        aria-valuemin={0}
        aria-valuemax={sampleScenarios.length}
        aria-valuenow={received}
      >
        <span style={{ width: `${(received / sampleScenarios.length) * 100}%` }} />
      </div>
      <p className="hint">
        {received} of {sampleScenarios.length} results received
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Scenario</th>
              <th scope="col">Result</th>
              <th scope="col">Latency</th>
              <th scope="col">Cost</th>
            </tr>
          </thead>
          <tbody>
            {sampleScenarios.map((scenario, index) => (
              <tr key={scenario.name} className={index < received ? 'preview-arrive' : undefined}>
                <td className="primary-cell">{scenario.name}</td>
                <td>
                  <StatusBadge value={index < received ? scenario.outcome : 'pending'} />
                </td>
                <td className="cell-mono">
                  {index >= received
                    ? '—'
                    : scenario.outcome === 'unknown'
                      ? 'timed out'
                      : formatLatency(scenario.latencyMs)}
                </td>
                <td className="cell-mono">{index < received ? 'Not reported' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {done && (
        <p className="preview-callout">
          The run is complete, but one result is unknown. A release built on it will be blocked
          until that scenario has a conclusive result.
        </p>
      )}
    </div>
  );
}

const traces = [
  ['refund.lookup', 'ok', '412 ms', '$0.0011', '5 minutes ago'],
  ['refund.create', 'ok', '1.18 s', null, '4 minutes ago'],
  ['order.cancel', 'error', '2.84 s', '$0.0019', '3 minutes ago'],
  ['refund.create', 'ok', '980 ms', '$0.0014', '2 minutes ago'],
  ['voice.greeting', 'ok', '640 ms', null, '1 minute ago'],
] as const;

// Production traces arriving, a monitor that cannot assess one, and the incident it opens.
export function EvidenceView({ running }: { running: boolean }) {
  const shown = useStream(traces.length + 2, 700, running);
  return (
    <div className="preview-evidence">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Production evidence</p>
          <h3 className="preview-title">Traces from {sampleProject}</h3>
          <p>A cost that was never reported stays unreported, not zero.</p>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Status</th>
              <th scope="col">Latency</th>
              <th scope="col">Cost (USD)</th>
              <th scope="col">Occurred</th>
            </tr>
          </thead>
          <tbody>
            {traces.slice(0, shown).map(([operation, outcome, latency, cost, occurred]) => (
              <tr key={`${operation}-${occurred}`} className="preview-arrive">
                <td className="primary-cell">{operation}</td>
                <td>
                  <StatusBadge value={outcome} />
                </td>
                <td>{latency}</td>
                <td>
                  {cost ?? 'Not recorded'}
                  <span className="incident-source-label">{cost ? 'Reported' : 'Unreported'}</span>
                </td>
                <td className="cell-time">{occurred}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="preview-monitor">
        {shown > traces.length && (
          <div className="preview-card preview-arrive">
            <p className="eyebrow">Monitor</p>
            <p className="hint">Refunds cite the policy · 1 in 10 traces sampled</p>
            <p>
              Assessment of refund.create from 4 minutes ago <StatusBadge value="unavailable" />
            </p>
            <p className="hint">No evaluator is configured, so the result is unknown.</p>
          </div>
        )}
        {shown > traces.length + 1 && (
          <div className="preview-card preview-card-incident preview-arrive">
            <p className="eyebrow">Incident</p>
            <p>
              Assessment unavailable, linked to its trace <StatusBadge value="open" />
            </p>
            <p className="hint">Resolving it requires a note, and reopening is audited.</p>
          </div>
        )}
      </div>
    </div>
  );
}

const pipeline = [
  ['Build', 'passed'],
  ['Unit tests', 'passed'],
  ['Lotuspond release gate', 'failed'],
  ['Deploy to production', 'skipped'],
] as const;

// The delivery pipeline obeying the decision through the release gate command.
export function GateView({ running }: { running: boolean }) {
  const output = [
    '$ npm run cli -- gate "$RELEASE_ID"',
    '{',
    `  "verdict": "${sampleDecision.verdict}",`,
    '  "reasons": [',
    ...sampleDecision.reasons.map(
      (reason, index) => `    "${reason}"${index < sampleDecision.reasons.length - 1 ? ',' : ''}`,
    ),
    '  ],',
    '  "evidenceSha256": "…"',
    '}',
    '$ echo $?',
    '2',
  ];
  // Build and tests have already passed; the gate runs, then deployment is skipped.
  const shown = useStream(output.length + 2, 260, running);
  const lines = Math.min(shown, output.length);
  const steps = 2 + Math.max(0, shown - output.length);
  return (
    <div className="preview-gate">
      <ol className="preview-pipeline" aria-label="Delivery pipeline">
        {pipeline.map(([name, status], index) => (
          <li key={name}>
            <span>{name}</span>
            <StatusBadge
              value={index < steps ? status : index === steps && running ? 'running' : 'pending'}
            />
          </li>
        ))}
      </ol>
      <pre className="preview-terminal" aria-label="Release gate output">
        <code>
          {output.slice(0, lines).map((line, index) => (
            <span key={index} className={line.startsWith('$') ? 'code-command' : 'code-output'}>
              {line}
              {'\n'}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
