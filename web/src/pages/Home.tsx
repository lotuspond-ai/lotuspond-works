import { useEffect, useState } from 'react';
import { api, type Account, type Row } from '../api.js';
import { Icon } from '../components/console/icons.js';
import { pageLabel } from '../components/console/navigation.js';
import { exactTime, relativeTime } from '../lib/format.js';
import { requestCreate, setupSteps, type SetupProgress } from '../lib/setup.js';

const hiddenKey = (workspace: string) => `lotuspond.setup-hidden.${workspace}`;
function readHidden(workspace: string) {
  try {
    return localStorage.getItem(hiddenKey(workspace)) === 'true';
  } catch {
    return false;
  }
}
function writeHidden(workspace: string, value: boolean) {
  try {
    if (value) localStorage.setItem(hiddenKey(workspace), 'true');
    else localStorage.removeItem(hiddenKey(workspace));
  } catch {
    /* Storage can be disabled; the checklist simply stays visible. */
  }
}

// The first page after sign-in: the path to a first recorded release decision, then what
// changed recently. Progress comes from the workspace's own records.
export function Home({
  account,
  workspace,
  role,
  setup,
  onOpenSearch,
}: {
  account: Account;
  workspace: string;
  role: string;
  setup: SetupProgress;
  onOpenSearch: () => void;
}) {
  const [hidden, setHidden] = useState(() => readHidden(workspace));
  const [activity, setActivity] = useState<Row[] | null>(null);
  const [activityError, setActivityError] = useState('');
  useEffect(() => setHidden(readHidden(workspace)), [workspace]);
  useEffect(() => {
    const controller = new AbortController();
    setActivity(null);
    setActivityError('');
    api<{ items: Row[] }>('/audit-events?limit=6', workspace, undefined, 'GET', controller.signal)
      .then((data) => setActivity(data.items))
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setActivityError(reason.message);
      });
    return () => controller.abort();
  }, [workspace]);

  const first = account.user.name.split(/\s+/)[0] || account.user.name;
  const complete = !setup.loading && setup.done === setup.total;
  const next = setupSteps.find((step) => setup.states[step.id] !== 'done');
  const canCreate = role !== 'viewer';
  const isAdmin = ['owner', 'admin'].includes(role);

  return (
    <div className="home">
      <div className="page-heading">
        <div>
          <h1>Welcome, {first}</h1>
          <p>
            {complete
              ? 'Your workspace can reach a recorded release decision. Evaluate a new revision whenever your agent changes.'
              : 'Six steps take this workspace from empty to its first recorded release decision.'}
          </p>
        </div>
        <button
          type="button"
          className="secondary"
          aria-keyshortcuts="Meta+K Control+K"
          onClick={onOpenSearch}
        >
          <Icon name="search" />
          Search or jump to
          <kbd aria-hidden="true">⌘K</kbd>
        </button>
      </div>

      {!hidden && !complete && (
        <section className="setup" aria-labelledby="setup-title">
          <div className="setup-head">
            <div>
              <h2 id="setup-title">Set up your workspace</h2>
              <p>
                {setup.loading
                  ? 'Checking what this workspace already has…'
                  : `${setup.done} of ${setup.total} complete`}
              </p>
            </div>
            <button
              type="button"
              className="text-button"
              onClick={() => {
                writeHidden(workspace, true);
                setHidden(true);
              }}
            >
              Hide checklist
            </button>
          </div>
          <div
            className="setup-meter"
            role="progressbar"
            aria-label="Setup progress"
            aria-valuemin={0}
            aria-valuemax={setup.total}
            aria-valuenow={setup.done}
          >
            <span style={{ width: `${(setup.done / setup.total) * 100}%` }} />
          </div>
          <ol className="setup-steps">
            {setupSteps.map((step, index) => {
              const state = setup.states[step.id] ?? 'unknown';
              const isNext = step.id === next?.id && !setup.loading;
              const allowed = canCreate && (!step.admin || isAdmin);
              return (
                <li key={step.id} data-state={state} data-next={isNext || undefined}>
                  <span className="setup-mark" aria-hidden="true">
                    {state === 'done' ? <Icon name="check" /> : index + 1}
                  </span>
                  <div>
                    {/* The mark and colour show progress; this says it in words. */}
                    <h3>
                      {step.title}
                      <span className="sr-only">
                        {state === 'done'
                          ? ' (complete)'
                          : isNext
                            ? ' (next step)'
                            : state === 'unknown' && !setup.loading
                              ? ' (not checked)'
                              : ''}
                      </span>
                    </h3>
                    <p>
                      {state === 'unknown' && !setup.loading
                        ? 'Could not check this step with your access.'
                        : step.detail}
                    </p>
                  </div>
                  {state === 'done' ? (
                    <a className="setup-view" href={`#${step.id}`}>
                      View<span className="sr-only"> {pageLabel(step.id)?.toLowerCase()}</span>
                    </a>
                  ) : allowed ? (
                    <button
                      type="button"
                      className={isNext ? undefined : 'secondary'}
                      onClick={() => requestCreate(step.id)}
                    >
                      {step.action}
                    </button>
                  ) : (
                    <span className="setup-note">Needs an administrator</span>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      )}

      <div className="home-grid">
        <section className="panel" aria-labelledby="activity-title">
          <h2 id="activity-title">Recent activity</h2>
          {activityError ? (
            <p className="hint">Activity is unavailable: {activityError}</p>
          ) : activity === null ? (
            <p className="hint" role="status">
              Loading activity…
            </p>
          ) : activity.length === 0 ? (
            <p className="hint">Changes to this workspace will appear here as they are recorded.</p>
          ) : (
            <ul className="activity">
              {activity.map((event) => (
                <li key={event.id}>
                  <span className="activity-action">
                    {String(event.action).replaceAll('.', ' ').replaceAll('_', ' ')}
                  </span>
                  <time
                    dateTime={String(event.created_at)}
                    title={exactTime(String(event.created_at))}
                  >
                    {relativeTime(String(event.created_at))}
                  </time>
                </li>
              ))}
            </ul>
          )}
          <a className="text-button" href="#audit-events">
            Open the audit log
          </a>
        </section>

        <section className="panel" aria-labelledby="gate-title">
          <h2 id="gate-title">Gate a deployment</h2>
          <p className="hint">
            Once a release has a decision, your pipeline can obey it. Anything other than a pass
            exits with status 2.
          </p>
          {/* Scrolls sideways on a narrow screen, so it is a named stop for keyboard scrolling. */}
          <pre className="home-code" role="region" aria-label="Release gate command" tabIndex={0}>
            <code>npm run cli -- gate "$RELEASE_ID"</code>
          </pre>
          <a className="text-button" href="#api-keys">
            Create a key for your pipeline
          </a>
        </section>

        {hidden && !complete && (
          <section className="panel" aria-labelledby="restore-title">
            <h2 id="restore-title">Setup checklist hidden</h2>
            <p className="hint">
              {setup.done} of {setup.total} steps are complete.
            </p>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                writeHidden(workspace, false);
                setHidden(false);
              }}
            >
              Show checklist
            </button>
          </section>
        )}
      </div>
    </div>
  );
}
