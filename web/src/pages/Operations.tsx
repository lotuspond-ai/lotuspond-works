import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { displayValue } from '../components/ui.js';
import {
  elapsedSeconds,
  pollLabel,
  type OperationsSnapshot,
  type WorkerRole,
} from '../lib/operations.js';

const roles: WorkerRole[] = ['evaluation', 'mail', 'alert'];
const titles: Record<WorkerRole, string> = {
  evaluation: 'Evaluations',
  mail: 'Identity mail',
  alert: 'Incident alerts',
};
const statuses: Record<WorkerRole, [string, string][]> = {
  evaluation: [
    ['queued', 'Queued'],
    ['running', 'Running'],
    ['completed', 'Completed'],
    ['failed', 'Failed jobs'],
    ['cancelled', 'Cancelled'],
  ],
  mail: [
    ['queued', 'Queued'],
    ['sending', 'Sending'],
    ['sent', 'SMTP accepted'],
    ['failed', 'Failed messages'],
    ['cancelled', 'Cancelled'],
  ],
  alert: [
    ['queued', 'Queued'],
    ['sending', 'Sending'],
    ['delivered', 'Webhook accepted'],
    ['failed', 'Failed deliveries'],
    ['cancelled', 'Cancelled'],
  ],
};

function ObservedTime({ value }: { value: string | null }) {
  return value ? <time dateTime={value}>{displayValue(value)}</time> : <>Not recorded</>;
}

export function OperationsSnapshotView({ snapshot }: { snapshot: OperationsSnapshot }) {
  const redis = snapshot.dependencies.redis.state;
  return (
    <>
      <p className="operations-timestamp">
        Snapshot observed <ObservedTime value={snapshot.observed_at} />. Ages and freshness are
        measured at this time; refresh for a new observation.
      </p>
      <section
        className="operations-dependencies"
        aria-labelledby="operations-dependencies-heading"
      >
        <h3 id="operations-dependencies-heading">Dependencies</h3>
        <dl className="record-facts">
          <div>
            <dt>Database</dt>
            <dd>Query succeeded</dd>
          </div>
          <div>
            <dt>Worker observation store</dt>
            <dd>
              {redis === 'ok'
                ? 'Available'
                : redis === 'not_configured'
                  ? 'Not configured'
                  : 'Unavailable'}
            </dd>
          </div>
        </dl>
        {redis !== 'ok' && (
          <p className="notice">
            {redis === 'not_configured'
              ? 'Redis is not configured. Configure the observation store to record worker polls.'
              : 'The observation store could not be reached. Check Redis connectivity and refresh.'}{' '}
            Queue counts below come from the database; worker activity remains unknown.
          </p>
        )}
      </section>
      <div className="operations-queues">
        {roles.map((role) => {
          const queue = snapshot.queues[role];
          const worker = snapshot.workers[role];
          const observation = worker.observation;
          return (
            <section className="operations-queue" key={role} aria-labelledby={`queue-${role}`}>
              <div className="section-row">
                <h3 id={`queue-${role}`}>{titles[role]}</h3>
                <span className={`badge badge-${queue.enabled ? 'neutral' : 'warning'}`}>
                  {queue.enabled ? 'Queue enabled' : 'Queue disabled'}
                </span>
              </div>
              {!queue.enabled && (
                <p className="hint">
                  Processing is disabled by operator configuration. Existing records remain visible.
                </p>
              )}
              <dl className="operations-counts" aria-label={`${titles[role]} recorded job states`}>
                {statuses[role].map(([key, title]) => (
                  <div key={key}>
                    <dt>{title}</dt>
                    <dd>{queue.status_counts[key]?.toLocaleString() ?? 'Unknown'}</dd>
                  </div>
                ))}
              </dl>
              <dl className="operations-eligibility">
                <div>
                  <dt>Eligible now</dt>
                  <dd>{queue.eligible_now.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Oldest eligible age</dt>
                  <dd>
                    {queue.eligible_now === 0 && queue.oldest_eligible_age_seconds === null
                      ? 'No eligible work'
                      : elapsedSeconds(queue.oldest_eligible_age_seconds)}
                  </dd>
                </div>
                <div>
                  <dt>Delayed retries</dt>
                  <dd>{queue.delayed.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Blocked by suspended workspace</dt>
                  <dd>{queue.suspended.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Expired leases</dt>
                  <dd>{queue.expired_leases.toLocaleString()}</dd>
                </div>
              </dl>
              <div className="operations-worker">
                <h4>Worker poll observation</h4>
                <p
                  className={`badge badge-${observation.state === 'stale' || observation.state === 'unavailable' ? 'warning' : 'neutral'}`}
                >
                  {pollLabel(observation.state)}
                </p>
                <dl className="operations-eligibility">
                  <div>
                    <dt>Worker configuration</dt>
                    <dd>{worker.enabled ? 'Enabled' : 'Disabled'}</dd>
                  </div>
                  <div>
                    <dt>Last poll</dt>
                    <dd>
                      <ObservedTime value={observation.last_poll_at} />
                    </dd>
                  </div>
                  <div>
                    <dt>Poll age at snapshot</dt>
                    <dd>{elapsedSeconds(observation.age_seconds)}</dd>
                  </div>
                  <div>
                    <dt>Freshness threshold</dt>
                    <dd>{elapsedSeconds(observation.fresh_for_seconds)}</dd>
                  </div>
                </dl>
                {observation.error_code && (
                  <p className="hint">
                    Observation error: <code>{observation.error_code}</code>
                  </p>
                )}
                {observation.state === 'stale' || observation.state === 'missing' ? (
                  <p className="hint">
                    Check the worker process and observation-store connection. This observation does
                    not establish that the worker has stopped.
                  </p>
                ) : observation.state === 'fresh' ? (
                  <p className="hint">
                    A poll was observed within the threshold. It does not confirm task progress or
                    completion.
                  </p>
                ) : null}
              </div>
            </section>
          );
        })}
      </div>
      <p className="hint">
        Eligible, delayed, and suspended counts classify pending work and overlap the job-state
        totals. Expired leases can overlap eligible or suspended work. These values should not be
        added together. A disabled queue does not process eligible work.
      </p>
      <section className="operations-outcomes" aria-labelledby="operations-outcomes-heading">
        <h3 id="operations-outcomes-heading">Assessment outcomes</h3>
        <p>
          From <ObservedTime value={snapshot.window.start_at} /> up to (excluding){' '}
          <ObservedTime value={snapshot.window.end_at} /> — a{' '}
          {elapsedSeconds(snapshot.window.duration_seconds)} window.
        </p>
        <dl className="record-facts">
          <div>
            <dt>Failed assessments</dt>
            <dd>{snapshot.assessments_last_24h.failed.toLocaleString()}</dd>
          </div>
          <div>
            <dt>Unknown assessments</dt>
            <dd>{snapshot.assessments_last_24h.unknown.toLocaleString()}</dd>
          </div>
        </dl>
        <p className="hint">
          Failed is an assessment verdict; unknown means no conclusive verdict is available. These
          outcomes are separate from queue job failures.
        </p>
      </section>
      <p className="hint">
        SMTP acceptance does not confirm inbox delivery. Webhook acceptance does not confirm that a
        downstream notification reached a person.
      </p>
    </>
  );
}

export function Operations() {
  const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const errorMessage = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setSnapshot(null);
    void api<OperationsSnapshot>(
      '/admin/operations',
      undefined,
      undefined,
      'GET',
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) setSnapshot(data);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof TypeError
              ? 'Could not reach Operations. Check your connection.'
              : reason.message,
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    if (error) errorMessage.current?.focus();
  }, [error]);
  return (
    <section aria-labelledby="operations-heading" aria-busy={loading}>
      <div className="section-row">
        <div>
          <h2 id="operations-heading">Operations</h2>
          <p>Read-only queue state and worker poll observations across the installation.</p>
        </div>
        <button
          className="secondary"
          disabled={loading}
          onClick={() => {
            setLoading(true);
            setSnapshot(null);
            setError('');
            setRevision((value) => value + 1);
          }}
        >
          {loading ? 'Refreshing…' : 'Refresh operations'}
        </button>
      </div>
      {loading ? (
        <p className="loading-state" role="status">
          Loading operations snapshot…
        </p>
      ) : error ? (
        <p className="error" role="alert" ref={errorMessage} tabIndex={-1}>
          {error} Refresh to try again.
        </p>
      ) : snapshot ? (
        <OperationsSnapshotView snapshot={snapshot} />
      ) : (
        <p role="status">No operations snapshot is available. Refresh to try again.</p>
      )}
    </section>
  );
}
