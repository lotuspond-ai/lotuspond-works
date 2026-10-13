import { useMemo, useState } from 'react';
import { decide, type Outcome, type PreviewPolicy } from '../../lib/policyPreview.js';
import { DecisionSummary } from '../DecisionSummary.js';
import { StatusBadge } from '../ui.js';
import {
  formatLatency,
  sampleBaseline,
  sampleBaselinePassRate,
  sampleCandidate,
  samplePolicy,
  sampleProject,
  sampleScenarios,
} from './sample.js';

const cycle: Outcome[] = ['passed', 'failed', 'unknown', 'missing'];

// The console's release page, built from the console's own components, with its policy made
// adjustable so a visitor can watch the real rules decide. In a workspace a policy is
// immutable; changing a rule means a new version.
export function ReleaseView({ onInteract }: { onInteract: () => void }) {
  const [scenarios, setScenarios] = useState(sampleScenarios);
  const [policy, setPolicy] = useState<PreviewPolicy>(samplePolicy);
  const [approved, setApproved] = useState(false);
  const decision = useMemo(
    () => decide(policy, scenarios, sampleBaselinePassRate, approved),
    [policy, scenarios, approved],
  );

  function change(next: () => void) {
    onInteract();
    setApproved(false);
    next();
  }
  function advance(index: number) {
    change(() =>
      setScenarios((current) =>
        current.map((scenario, position) =>
          position === index
            ? {
                ...scenario,
                outcome: cycle[(cycle.indexOf(scenario.outcome) + 1) % cycle.length] ?? 'passed',
              }
            : scenario,
        ),
      ),
    );
  }

  return (
    <div className="preview-release">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Release decisions</p>
          <h3 className="preview-title">
            {sampleProject} · candidate {sampleCandidate}
          </h3>
          <p>Baseline {sampleBaseline} passed every scenario in the same frozen snapshot.</p>
        </div>
      </div>

      <div className="preview-release-grid">
        <div className="table-wrap">
          <table>
            <caption className="sr-only">
              Scenario results. Choose an outcome to change it and the decision updates.
            </caption>
            <thead>
              <tr>
                <th scope="col">Scenario</th>
                <th scope="col">Outcome</th>
                <th scope="col">Latency</th>
              </tr>
            </thead>
            <tbody>
              {scenarios.map((scenario, index) => (
                <tr key={scenario.name}>
                  <td className="primary-cell">
                    {scenario.name}
                    {scenario.critical && <span className="preview-critical">Critical</span>}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="preview-outcome"
                      onClick={() => advance(index)}
                      aria-label={`${scenario.name}: ${scenario.outcome}. Change outcome`}
                    >
                      <StatusBadge value={scenario.outcome} />
                    </button>
                  </td>
                  <td className="cell-mono">
                    {scenario.outcome === 'missing'
                      ? '—'
                      : scenario.outcome === 'unknown'
                        ? 'timed out'
                        : formatLatency(scenario.latencyMs)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <DecisionSummary
          as="h4"
          eyebrow="Decision"
          live
          verdict={decision.verdict}
          reasons={decision.reasons}
          metrics={[
            { label: 'Pass rate', value: `${Number((decision.passRate * 100).toFixed(1))}%` },
            { label: 'Evidence received', value: `${decision.received} / ${decision.total}` },
            {
              label: 'P95 latency',
              value: decision.p95Ms === null ? 'Not recorded' : formatLatency(decision.p95Ms),
            },
            { label: 'Evaluation cost', value: 'Not recorded' },
          ]}
        >
          {decision.verdict === 'approval_required' && (
            <button
              type="button"
              className="preview-approve"
              onClick={() => {
                onInteract();
                setApproved(true);
              }}
            >
              Approve as a different administrator
            </button>
          )}
        </DecisionSummary>
      </div>

      <fieldset className="preview-policy">
        <legend>Policy</legend>
        <label>
          Minimum pass rate
          <select
            value={policy.minimumPassRate}
            onChange={(event) =>
              change(() => setPolicy({ ...policy, minimumPassRate: Number(event.target.value) }))
            }
          >
            {[1, 0.95, 0.8, 0.5].map((value) => (
              <option key={value} value={value}>
                {value * 100}%
              </option>
            ))}
          </select>
        </label>
        <label>
          Maximum regression
          <select
            value={policy.maximumRegression}
            onChange={(event) =>
              change(() => setPolicy({ ...policy, maximumRegression: Number(event.target.value) }))
            }
          >
            {[0, 0.2, 0.5].map((value) => (
              <option key={value} value={value}>
                {value * 100}%
              </option>
            ))}
          </select>
        </label>
        <label>
          P95 latency limit
          <select
            value={policy.maximumP95Ms ?? ''}
            onChange={(event) =>
              change(() =>
                setPolicy({
                  ...policy,
                  maximumP95Ms: event.target.value ? Number(event.target.value) : null,
                }),
              )
            }
          >
            <option value="">No limit</option>
            {[2000, 5000].map((value) => (
              <option key={value} value={value}>
                {formatLatency(value)}
              </option>
            ))}
          </select>
        </label>
        <label className="preview-check">
          <input
            type="checkbox"
            checked={policy.requireApproval}
            onChange={(event) =>
              change(() => setPolicy({ ...policy, requireApproval: event.target.checked }))
            }
          />
          Require independent approval
        </label>
      </fieldset>
      <p className="hint">
        Choose an outcome to change it. In a workspace, policies are immutable: a new rule is a new
        policy version.
      </p>
    </div>
  );
}
