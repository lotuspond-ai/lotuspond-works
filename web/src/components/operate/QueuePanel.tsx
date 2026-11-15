import { useCallback, useEffect, useState } from 'react';
import { api, ApiError } from '../../api.js';
import { fill, fromMs, operateMessage, scopedPath, type Scope } from '../../lib/operate.js';
import { Alert, RelativeTime, humanize } from '../ui.js';

type Queue = {
  capacity: {
    active: number;
    active_limit: number;
    pending: number;
    pending_limit: number;
    retained_waits: number;
    wait_limit: number;
  } | null;
  states: Record<string, number>;
  oldest_pending_admitted_at_ms: number | null;
};

function Meter({ label, count, limit }: { label: string; count: number; limit: number }) {
  const share = fill(count, limit);
  return (
    <div className="queue-meter">
      <div className="queue-meter-head">
        <span>{label}</span>
        <strong>
          {count.toLocaleString()} of {limit.toLocaleString()}
        </strong>
      </div>
      <div
        className="queue-meter-bar"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={count}
      >
        <span
          className={share >= 1 ? 'full' : share >= 0.8 ? 'high' : undefined}
          style={{ width: `${share * 100}%` }}
        />
      </div>
    </div>
  );
}

// The environment's queue (docs/superpowers/specs/2026-11-16-control-room-operations-design.md).
export function QueuePanel({ workspace, scope }: { workspace: string; scope: Scope }) {
  const [queue, setQueue] = useState<Queue | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setError('');
    try {
      setQueue(await api<Queue>(scopedPath(scope, 'execution-queue'), workspace));
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? operateMessage(reason.code, reason.message)
          : 'The request could not be completed. Please try again.',
      );
    }
  }, [scope, workspace]);
  useEffect(() => {
    void load();
  }, [load]);
  if (error) return <Alert onRetry={() => void load()}>{error}</Alert>;
  if (!queue)
    return (
      <p className="loading-state" role="status">
        Loading the queue…
      </p>
    );
  const states = Object.entries(queue.states);
  return (
    <section className="panel" aria-labelledby="queue-heading">
      <div className="panel-head">
        <h2 id="queue-heading">Queue</h2>
        <button className="secondary" onClick={() => void load()}>
          Refresh
        </button>
      </div>
      {queue.capacity ? (
        <div className="queue-meters">
          <Meter
            label="Running now"
            count={queue.capacity.active}
            limit={queue.capacity.active_limit}
          />
          <Meter
            label="Waiting to start"
            count={queue.capacity.pending}
            limit={queue.capacity.pending_limit}
          />
          <Meter
            label="Waiting on people or signals"
            count={queue.capacity.retained_waits}
            limit={queue.capacity.wait_limit}
          />
        </div>
      ) : (
        <p className="hint">Nothing has been admitted in this environment yet.</p>
      )}
      {queue.oldest_pending_admitted_at_ms !== null && (
        <p>
          The oldest run waiting to start was admitted{' '}
          <RelativeTime value={fromMs(queue.oldest_pending_admitted_at_ms)} />.
        </p>
      )}
      <h3>Runs by state</h3>
      <ul className="queue-states">
        {states.map(([state, count]) => (
          <li key={state} className={count ? undefined : 'zero'}>
            <span>{humanize(state)}</span>
            <strong>{count.toLocaleString()}</strong>
          </li>
        ))}
      </ul>
      <p className="hint">
        When a limit is full, new runs are refused at admission rather than queued without bound.
        The limits come from the installation's entitlement.
      </p>
    </section>
  );
}
