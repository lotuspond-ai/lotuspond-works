import type { IncidentDetail } from '../lib/incidents.js';
import { alertDelivery, incidentCategory, incidentSource } from '../lib/incidents.js';
import { IncidentSourceEvidence } from './IncidentSourceEvidence.js';
import { ObjectView, StatusBadge, displayValue, humanize } from './ui.js';

export function IncidentReview({
  detail,
  workspace,
  onOpenProject,
}: {
  detail: IncidentDetail;
  workspace: string;
  onOpenProject: (id: string) => void;
}) {
  const {
    incident,
    project,
    trace,
    voice_session: voice,
    assessment,
    monitor,
    history,
    alerts,
  } = detail;
  const category = incidentCategory(incident.category);
  return (
    <>
      <div className="incident-summary">
        <span className={`badge badge-${category.tone}`}>{category.label}</span>
        <p>{category.explanation}</p>
        <dl className="compact-facts">
          <div>
            <dt>Incident status</dt>
            <dd>
              <StatusBadge value={incident.status} />
            </dd>
          </div>
          <div>
            <dt>Severity</dt>
            <dd>
              <StatusBadge value={incident.severity} />
            </dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{displayValue(incident.created_at)}</dd>
          </div>
          <div>
            <dt>Reported by</dt>
            <dd>{incidentSource(incident.source)}</dd>
          </div>
          <div>
            <dt>Resolved</dt>
            <dd>{displayValue(incident.resolved_at)}</dd>
          </div>
          <div>
            <dt>Last reopened</dt>
            <dd>{displayValue(incident.reopened_at)}</dd>
          </div>
        </dl>
        {incident.resolution_reason && (
          <div className="incident-resolution">
            <h3>Recorded resolution</h3>
            <p>{incident.resolution_reason}</p>
          </div>
        )}
        <p className="hint">
          Incident status records the review workflow. Resolving an incident does not change its
          original assessment or release verdict.
        </p>
      </div>
      <section className="evidence-section" aria-labelledby="incident-context-heading">
        <h3 id="incident-context-heading">Linked evidence</h3>
        <dl className="record-facts incident-links">
          <div>
            <dt>Project</dt>
            <dd>
              {incident.project_id ? (
                <>
                  <button
                    className="text-button"
                    onClick={() => onOpenProject(incident.project_id!)}
                  >
                    {String(project?.name || 'Open linked project')}
                  </button>
                  <code>{incident.project_id}</code>
                </>
              ) : (
                'No project linked'
              )}
            </dd>
          </div>
          <div>
            <dt>Trace</dt>
            <dd>
              {incident.trace_id ? (
                <>
                  {trace?.name ? <strong>{String(trace.name)}</strong> : null}
                  <code>{incident.trace_id}</code>
                </>
              ) : (
                'No trace linked'
              )}
            </dd>
          </div>
          <div>
            <dt>Voice session</dt>
            <dd>
              {incident.voice_session_id ? (
                <>
                  {voice?.name ? <strong>{String(voice.name)}</strong> : null}
                  <code>{incident.voice_session_id}</code>
                </>
              ) : (
                'No voice session linked'
              )}
            </dd>
          </div>
          <div>
            <dt>Assessment</dt>
            <dd>
              {incident.assessment_id ? (
                <code>{incident.assessment_id}</code>
              ) : (
                'No assessment linked'
              )}
            </dd>
          </div>
          <div>
            <dt>Monitor</dt>
            <dd>
              {incident.monitor_id ? (
                <>
                  {monitor?.name ? <strong>{String(monitor.name)}</strong> : null}
                  <code>{incident.monitor_id}</code>
                </>
              ) : (
                'No monitor linked'
              )}
            </dd>
          </div>
          <div>
            <dt>Incident ID</dt>
            <dd>
              <code>{incident.id}</code>
            </dd>
          </div>
        </dl>
        {monitor?.criteria ? (
          <div className="incident-resolution">
            <h4>Monitor criteria</h4>
            <p>{String(monitor.criteria)}</p>
          </div>
        ) : null}
      </section>
      <section className="evidence-section" aria-labelledby="incident-assessment-heading">
        <h3 id="incident-assessment-heading">Triggering assessment</h3>
        {assessment ? (
          <article className="assessment">
            <div className="section-row">
              <h4>{String(assessment.model || 'Model not recorded')}</h4>
              <StatusBadge value={assessment.verdict || assessment.status || 'unknown'} />
            </div>
            <p className="assessment-reason">
              {String(assessment.reason || 'No explanation recorded.')}
            </p>
            <dl className="compact-facts">
              <div>
                <dt>Provider</dt>
                <dd>{displayValue(assessment.provider)}</dd>
              </div>
              <div>
                <dt>Recorded</dt>
                <dd>{displayValue(assessment.created_at)}</dd>
              </div>
            </dl>
            <p className="hint">
              This is the retained assessment that triggered the incident. It is advisory evidence.
            </p>
            {assessment.provenance !== undefined && (
              <details className="evidence-disclosure">
                <summary>Assessment provenance</summary>
                <ObjectView value={assessment.provenance} />
              </details>
            )}
          </article>
        ) : (
          <p className="hint">
            {incident.assessment_id
              ? 'The linked assessment could not be returned. Its outcome is unavailable here.'
              : 'No assessment is linked to this incident.'}
          </p>
        )}
      </section>
      {incident.trace_id && (
        <IncidentSourceEvidence workspace={workspace} kind="trace" id={incident.trace_id} />
      )}
      {incident.voice_session_id && (
        <IncidentSourceEvidence workspace={workspace} kind="voice" id={incident.voice_session_id} />
      )}
      <section className="evidence-section" aria-labelledby="incident-history-heading">
        <h3 id="incident-history-heading">Review history</h3>
        {history.length ? (
          <ol className="incident-history">
            {history.map((item) => (
              <li key={item.id}>
                <div className="section-row">
                  <strong>{humanize(item.action)}</strong>
                  <time dateTime={item.created_at}>{displayValue(item.created_at)}</time>
                </div>
                <p>{item.reason || 'No reason recorded.'}</p>
                <span className="hint">
                  {item.actor_id === 'evaluation-worker' ? (
                    'System · evaluation worker'
                  ) : (
                    <>
                      Actor <code>{item.actor_id}</code>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="hint">No review history is available for this incident.</p>
        )}
      </section>
      <section className="evidence-section" aria-labelledby="incident-alerts-heading">
        <h3 id="incident-alerts-heading">Alert delivery</h3>
        <p>
          Webhook accepted means the destination returned HTTP 2xx. It does not confirm that a
          downstream notification reached a person.
        </p>
        {alerts.length ? (
          <div className="incident-alerts">
            {alerts.map((alert) => (
              <article className="incident-alert" key={alert.id}>
                <div className="section-row">
                  <h4>{humanize(alert.event.replaceAll('.', ' '))}</h4>
                  <span
                    className={`badge badge-${alert.status === 'failed' ? 'danger' : alert.status === 'delivered' ? 'success' : 'neutral'}`}
                  >
                    {alertDelivery(alert.status)}
                  </span>
                </div>
                <dl className="compact-facts">
                  <div>
                    <dt>Destination</dt>
                    <dd>{alert.destination}</dd>
                  </div>
                  <div>
                    <dt>Attempts</dt>
                    <dd>{alert.attempts}</dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd>{displayValue(alert.created_at)}</dd>
                  </div>
                  {alert.status === 'queued' && (
                    <div>
                      <dt>Available for delivery</dt>
                      <dd>{displayValue(alert.available_at)}</dd>
                    </div>
                  )}
                  {alert.status === 'delivered' && (
                    <div>
                      <dt>Webhook accepted at</dt>
                      <dd>{displayValue(alert.delivered_at)}</dd>
                    </div>
                  )}
                  {alert.last_error_code && (
                    <div>
                      <dt>Last recorded error</dt>
                      <dd>{humanize(alert.last_error_code)}</dd>
                    </div>
                  )}
                </dl>
              </article>
            ))}
          </div>
        ) : (
          <p className="hint">
            No alert delivery record is available. This does not confirm that an alert was sent.
          </p>
        )}
      </section>
    </>
  );
}
