import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api.js';
import { EnvironmentScope } from '../components/operate/EnvironmentScope.js';
import {
  Alert,
  EmptyState,
  RelativeTime,
  SectionHeader,
  StatusBadge,
  humanize,
} from '../components/ui.js';
import {
  allowedControls,
  fromMs,
  opaqueId,
  operateMessage,
  scopedPath,
  type Scope,
} from '../lib/operate.js';

type Deployment = {
  id: string;
  revision_id: string | null;
  status: string;
  unreviewed_operations: string[];
};
type Execution = {
  id: string;
  state: string;
  requested_state: string;
  reason: string | null;
  admitted_at_ms: number;
  deadline_ms: number;
};
type Page<T> = { items: T[]; next_after_id: string | null };
type Wait = { id: string; occurrence_path: string | null; state: string; deadline_ms: number };
type Event = {
  sequence: number;
  family: string;
  code: string;
  occurred_at_ms: number;
  application_state: string | null;
};
type Action = {
  action_id: string;
  step_id: string;
  authorization: string;
  effect: string;
  outcome: string;
  receipt_status: string | null;
};

function problem(reason: unknown) {
  return reason instanceof ApiError
    ? operateMessage(reason.code, reason.message)
    : 'The request could not be completed. Please try again.';
}

// IDs are UUIDv7, time-ordered: records made close together share their first characters,
// so the random tail tells them apart.
const short = (id: string) => `…${id.slice(-8)}`;

// Control Room (docs/superpowers/specs/2026-11-15-review-inbox-control-room-design.md): the
// deployments and executions of one environment, with the controls an operator may use.
export function ControlRoom({
  workspace,
  projectId,
  role,
}: {
  workspace: string;
  projectId: string;
  role: string;
}) {
  return (
    <div className="operate">
      <EnvironmentScope workspace={workspace} projectId={projectId}>
        {(scope, picker) => (
          <Room
            key={scope.environmentId}
            workspace={workspace}
            scope={scope}
            picker={picker}
            canControl={role !== 'viewer'}
          />
        )}
      </EnvironmentScope>
    </div>
  );
}

function usePaged<T>(workspace: string, scope: Scope, path: string) {
  const [items, setItems] = useState<T[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(
    async (after?: string) => {
      setError('');
      try {
        const query = after ? `?${new URLSearchParams({ after_id: after })}` : '';
        const data = await api<Page<T>>(scopedPath(scope, `${path}${query}`), workspace);
        setItems((current) => (after && current ? [...current, ...data.items] : data.items));
        setNext(data.next_after_id);
      } catch (reason) {
        setError(problem(reason));
        if (!after) setItems([]);
      }
    },
    [workspace, scope, path],
  );
  useEffect(() => {
    void load();
  }, [load]);
  return { items, next, error, load };
}

function Room({
  workspace,
  scope,
  picker,
  canControl,
}: {
  workspace: string;
  scope: Scope;
  picker: React.ReactNode;
  canControl: boolean;
}) {
  const deployments = usePaged<Deployment>(workspace, scope, 'deployments');
  const executions = usePaged<Execution>(workspace, scope, 'executions');
  const [openId, setOpenId] = useState('');
  // Both lists fail the same way when the server has no execution services: say it once.
  const sameError = deployments.error && deployments.error === executions.error;
  const opened = executions.items?.find((item) => item.id === openId);
  const retry = () => {
    void deployments.load();
    void executions.load();
  };
  return (
    <>
      <SectionHeader
        eyebrow="Operate"
        title="Control Room"
        description="What is deployed in this environment and what is running, with the controls to pause, resume or cancel it."
        actions={
          <>
            {picker}
            <button className="secondary" onClick={retry}>
              Refresh
            </button>
          </>
        }
      />
      {sameError ? (
        <div className="notice operate-unavailable" role="status">
          <p>{executions.error}</p>
          <button className="secondary" onClick={retry}>
            Try again
          </button>
        </div>
      ) : (
        <>
          <section className="panel" aria-labelledby="deployments-heading">
            <h2 id="deployments-heading">Deployments</h2>
            {!sameError && deployments.error && (
              <Alert onRetry={() => void deployments.load()}>{deployments.error}</Alert>
            )}
            {deployments.items === null ? (
              <p className="loading-state" role="status">
                Loading deployments…
              </p>
            ) : !deployments.items.length ? (
              !deployments.error && (
                <p className="hint">
                  Nothing is deployed here yet. A deployment admits one published package version to
                  this environment.
                </p>
              )
            ) : (
              <div className="table-wrap">
                <table>
                  <caption className="sr-only">Deployments</caption>
                  <thead>
                    <tr>
                      <th scope="col">Deployment</th>
                      <th scope="col">Status</th>
                      <th scope="col">Active revision</th>
                      <th scope="col">Writes sent without review</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deployments.items.map((item) => (
                      <tr key={item.id}>
                        <td className="primary-cell cell-mono" title={item.id}>
                          {short(item.id)}
                        </td>
                        <td>
                          <StatusBadge value={item.status} />
                        </td>
                        <td className="cell-mono" title={item.revision_id ?? undefined}>
                          {item.revision_id ? short(item.revision_id) : 'None'}
                        </td>
                        <td>
                          {item.unreviewed_operations?.length
                            ? item.unreviewed_operations.join(', ')
                            : 'None'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {deployments.next && (
              <button
                className="secondary"
                onClick={() => void deployments.load(deployments.next!)}
              >
                Load more
              </button>
            )}
          </section>

          <section className="panel" aria-labelledby="executions-heading">
            <h2 id="executions-heading">Executions</h2>
            {!sameError && executions.error && (
              <Alert onRetry={() => void executions.load()}>{executions.error}</Alert>
            )}
            {executions.items === null ? (
              <p className="loading-state" role="status">
                Loading executions…
              </p>
            ) : !executions.items.length ? (
              !executions.error && (
                <EmptyState
                  glyph="enclosure"
                  title="No executions yet"
                  description="Each run of a deployed workflow appears here with its observed state and any control requested."
                />
              )
            ) : (
              <div className="operate-split operate-split-even">
                <div className="table-wrap">
                  <table>
                    <caption className="sr-only">Executions</caption>
                    <thead>
                      <tr>
                        <th scope="col">Execution</th>
                        <th scope="col">State</th>
                        <th scope="col">Control requested</th>
                      </tr>
                    </thead>
                    <tbody>
                      {executions.items.map((item) => (
                        <tr key={item.id} className={item.id === openId ? 'selected' : undefined}>
                          <td className="primary-cell">
                            <button
                              className="text-button cell-mono"
                              aria-current={item.id === openId ? 'true' : undefined}
                              onClick={() => setOpenId(item.id)}
                              title={item.id}
                            >
                              {short(item.id)}
                            </button>
                          </td>
                          <td>
                            <StatusBadge value={item.state} />
                          </td>
                          <td>
                            {item.requested_state !== 'running' &&
                            item.requested_state !== item.state
                              ? humanize(item.requested_state)
                              : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {executions.next && (
                    <button
                      className="secondary"
                      onClick={() => void executions.load(executions.next!)}
                    >
                      Load more
                    </button>
                  )}
                </div>
                {opened ? (
                  <ExecutionDetail
                    key={opened.id}
                    workspace={workspace}
                    scope={scope}
                    execution={opened}
                    canControl={canControl}
                    onChanged={() => void executions.load()}
                  />
                ) : (
                  <div className="operate-detail operate-placeholder">
                    <p>Choose an execution to see its waits, actions and events.</p>
                  </div>
                )}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

function ExecutionDetail({
  workspace,
  scope,
  execution: listed,
  canControl,
  onChanged,
}: {
  workspace: string;
  scope: Scope;
  execution: Execution;
  canControl: boolean;
  onChanged: () => void;
}) {
  const [execution, setExecution] = useState(listed);
  const [waits, setWaits] = useState<Wait[] | null>(null);
  const [events, setEvents] = useState<Event[] | null>(null);
  const [actions, setActions] = useState<Action[] | null | 'unavailable'>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const pending = useRef<{ key: string; id: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const base = `executions/${execution.id}`;
  const load = useCallback(async () => {
    setError('');
    const get = <T,>(path: string) => api<T>(scopedPath(scope, path), workspace);
    try {
      const [current, waitPage, eventPage] = await Promise.all([
        get<Execution>(base),
        get<Page<Wait>>(`${base}/waits`),
        get<{ items: Event[] }>(`${base}/events?limit=100`),
      ]);
      setExecution(current);
      setWaits(waitPage.items);
      setEvents(eventPage.items);
    } catch (reason) {
      setError(problem(reason));
    }
    try {
      setActions((await get<{ items: Action[] }>(`${base}/actions`)).items);
    } catch {
      // Action services are optional: without them the route answers 503 by design.
      setActions('unavailable');
    }
  }, [base, scope, workspace]);
  useEffect(() => {
    heading.current?.focus();
    void load();
  }, [load]);
  async function control(action: 'pause' | 'resume' | 'cancel') {
    setConfirming(false);
    setBusy(true);
    setError('');
    setNotice('');
    if (pending.current?.key !== action)
      pending.current = { key: action, id: opaqueId(`control-${action}`) };
    try {
      const view = await api<Execution>(scopedPath(scope, `${base}/control`), workspace, {
        request_id: pending.current.id,
        action,
      });
      pending.current = null;
      setExecution(view);
      setNotice(
        `${humanize(action)} requested. The engine applies it at the next safe point; the observed state changes when it has.`,
      );
      onChanged();
    } catch (reason) {
      setError(problem(reason));
      if (reason instanceof ApiError && reason.code === 'conflict') {
        pending.current = null;
        void load();
      }
    } finally {
      setBusy(false);
    }
  }
  const controls = allowedControls(execution.state, execution.requested_state);
  return (
    <section className="operate-detail" aria-labelledby="execution-heading">
      <h2 id="execution-heading" ref={heading} tabIndex={-1}>
        Execution <span className="cell-mono">{short(execution.id)}</span>
      </h2>
      <dl className="record-facts">
        <div>
          <dt>Observed state</dt>
          <dd>
            <StatusBadge value={execution.state} />
          </dd>
        </div>
        <div>
          <dt>Requested state</dt>
          <dd>{humanize(execution.requested_state)}</dd>
        </div>
        <div>
          <dt>Admitted</dt>
          <dd>
            <RelativeTime value={fromMs(execution.admitted_at_ms)} />
          </dd>
        </div>
        <div>
          <dt>Deadline</dt>
          <dd>
            <RelativeTime value={fromMs(execution.deadline_ms)} />
          </dd>
        </div>
        {execution.reason && (
          <div className="wide">
            <dt>Reason</dt>
            <dd>{humanize(execution.reason)}</dd>
          </div>
        )}
      </dl>

      {error && <Alert onRetry={() => void load()}>{error}</Alert>}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}

      {canControl && controls.length > 0 && (
        <div className="operate-actions">
          {controls.includes('pause') && (
            <button className="secondary" disabled={busy} onClick={() => void control('pause')}>
              Pause
            </button>
          )}
          {controls.includes('resume') && (
            <button className="secondary" disabled={busy} onClick={() => void control('resume')}>
              Resume
            </button>
          )}
          {controls.includes('cancel') &&
            (confirming ? (
              <span className="operate-confirm" role="group" aria-label="Confirm cancel">
                <span>Cancel this execution? Work already sent is not undone.</span>
                <button
                  className="secondary danger"
                  disabled={busy}
                  onClick={() => void control('cancel')}
                >
                  Yes, cancel
                </button>
                <button className="text-button" onClick={() => setConfirming(false)}>
                  Keep running
                </button>
              </span>
            ) : (
              <button
                className="text-button danger"
                disabled={busy}
                onClick={() => setConfirming(true)}
              >
                Cancel…
              </button>
            ))}
        </div>
      )}

      <h3>Waiting on</h3>
      {waits === null ? (
        <p className="hint">Loading…</p>
      ) : !waits.length ? (
        <p className="hint">Nothing.</p>
      ) : (
        <ul className="operate-facts">
          {waits.map((wait) => (
            <li key={wait.id}>
              <span className="cell-mono">{wait.occurrence_path ?? short(wait.id)}</span>
              <StatusBadge value={wait.state} />
              <span>
                until <RelativeTime value={fromMs(wait.deadline_ms)} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {waits?.some((wait) => wait.state === 'open') && (
        <p className="hint">
          A review or form waiting here is answered in{' '}
          <a className="inline-link" href="#human-tasks">
            Human tasks
          </a>
          .
        </p>
      )}

      <h3>Actions</h3>
      {actions === null ? (
        <p className="hint">Loading…</p>
      ) : actions === 'unavailable' ? (
        <p className="hint">Action records are not available on this server.</p>
      ) : !actions.length ? (
        <p className="hint">This execution has proposed no writes.</p>
      ) : (
        <ul className="operate-facts">
          {actions.map((action) => (
            <li key={action.action_id}>
              <span className="cell-mono">{action.step_id}</span>
              <span>Authorization: {humanize(action.authorization)}</span>
              <span>Effect: {humanize(action.effect)}</span>
              <StatusBadge value={action.outcome} />
            </li>
          ))}
        </ul>
      )}

      <h3>Events</h3>
      {events === null ? (
        <p className="hint">Loading…</p>
      ) : !events.length ? (
        <p className="hint">No events recorded.</p>
      ) : (
        <ol className="operate-events">
          {events.map((event) => (
            <li key={event.sequence}>
              <span className="cell-mono">{event.sequence}</span>
              <span>{humanize(event.code)}</span>
              <RelativeTime value={fromMs(event.occurred_at_ms)} />
            </li>
          ))}
        </ol>
      )}
      <p className="hint">Event contents stay encrypted; only what happened and when is shown.</p>
    </section>
  );
}
